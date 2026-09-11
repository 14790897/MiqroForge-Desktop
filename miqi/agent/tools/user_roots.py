"""User-mentioned output directories — dynamic extra roots (issue #821).

When a user asks "结果输出到 C:\\Users\\x\\Desktop\\test_result\\<dir>",
the file tools must be able to read/write there even though the directory
is outside the workspace and not listed in ``tools.extra_roots``.  This
module extracts absolute directory mentions from the user's own messages so
the runtime can authorize them for the session (per tool call, never
persisted).

Security model:

- ONLY the user's message text is scanned — never model output, tool
  results, or documents (prompt injection via file content cannot grant
  roots).
- A mentioned path becomes a root as: itself when it is (or is intended
  as) a directory — including a not-yet-created one, so "输出到 新目录"
  works on the first write; the parent directory when it is an existing
  file.
- Mentions that would cover the host config, per-session files, the user
  profile root, a drive root, or a top-level system directory are dropped.
- Roots are capped (``max_roots``) per extraction; callers re-extract each
  turn so authorization never outlives the user's request.
"""

from __future__ import annotations

import logging
import os as _os
import re as _re
import sys as _sys
from pathlib import Path
from typing import Iterable

from miqi.paths import get_config_path

_log = logging.getLogger(__name__)

# Characters that may precede a path mention (whitespace, quotes, opening
# brackets/parens, CJK punctuation, list separators).  A path glued to CJK
# prose (e.g. "test_result目录") is intentionally NOT extracted — the
# boundary requirement keeps false positives out of ordinary sentences.
_BOUNDARY_CHARS = r"\s\"'`,，;；:：。!！?？、()（[【"

# Path characters: anything but whitespace, quotes, Windows-illegal
# separators, and CJK punctuation (which otherwise glues the surrounding
# prose to the path, e.g. "输出到 C:\x，然后…").  CJK directory names
# (mof_price相关) are matched in full.
_PATH_CHARS = r"[^\s\"'`<>|*?，。；：！？、（）【】《》「」『』…—]+"

_WIN_PATH_RE = _re.compile(
    rf"(?<![^{_BOUNDARY_CHARS}])([A-Za-z]:[\\/]{_PATH_CHARS})"
)
_MNT_PATH_RE = _re.compile(
    rf"(?<![^{_BOUNDARY_CHARS}])(/mnt/[A-Za-z]/{_PATH_CHARS})"
)
# The ``(?<!:)`` keeps URL path segments out ("https://x/a/b" — the "/a"
# follows a colon), while drive-letter mentions still match after a colon
# via the Windows pattern ("目录:C:\x" works; "目录:/tmp" is dropped —
# colon-adjacent POSIX paths are almost always URLs or prose).
_POSIX_PATH_RE = _re.compile(
    rf"(?<![^{_BOUNDARY_CHARS}])(?<!:)(?:~)?/{_PATH_CHARS}"
)

# Trailing punctuation that gets glued to a path by the surrounding prose:
# "输出到 C:\Users\x\Desktop\test_result，", "……test_result。"
_TRAILING_PUNCT = ".,;:!?，。；：！？)）]】\"'"

# Depth-1 directories of a drive (Windows) or of ``/`` (POSIX) that must
# never become roots (CodeRabbit #851).
_TOP_LEVEL_SYSTEM_DIRS = frozenset({
    # Windows
    "users", "windows", "program files", "program files (x86)",
    "programdata", "perflogs", "$recycle.bin", "system volume information",
    # POSIX
    "bin", "boot", "dev", "etc", "home", "lib", "lib32", "lib64", "libx32",
    "opt", "proc", "root", "run", "sbin", "srv", "sys", "usr", "var",
    "mnt", "media",
    # macOS
    "system", "library", "applications",
})

# Default cap on auto-sensed roots per turn.
DEFAULT_MAX_USER_ROOTS = 8


def _is_protected_extra_root(root: Path, workspace: Path) -> bool:
    """Return True when *root* would make protected paths writable.

    Auto-sensed (and user-configured) extra roots must never cover the host
    config file or per-session files, otherwise a broad root (e.g. ``~/.miqi``
    or the workspace itself) could bypass read-only config handling and
    session isolation.
    """
    config = get_config_path().resolve()
    sessions = (workspace / "sessions").resolve()
    if config.is_relative_to(root):
        return True
    if sessions.is_relative_to(root) or root.is_relative_to(sessions):
        return True
    return False


def _is_top_level_system_dir(root: Path) -> bool:
    """True for drive-level system dirs, incl. space-truncated matches.

    A mention of ``C:\\Program Files`` is tokenized up to the space
    (``C:\\Program``) because path tokens cannot contain whitespace; the
    prefix check keeps that residue from becoming a root.
    """
    if root.parent != Path(root.anchor):
        return False
    name = root.name.lower()
    for sys_name in _TOP_LEVEL_SYSTEM_DIRS:
        if name == sys_name or sys_name.startswith(name + " "):
            return True
    return False


def _strip_trailing(raw: str) -> str:
    """Strip trailing separators and prose punctuation from a raw mention."""
    s = raw
    for _ in range(2):
        s = s.rstrip("/\\")
        s = s.rstrip(_TRAILING_PUNCT)
    return s


def _raw_mentions(text: str) -> list[str]:
    """Extract raw absolute-path mentions from one message text."""
    found: list[str] = []
    for pattern in (_WIN_PATH_RE, _MNT_PATH_RE, _POSIX_PATH_RE):
        for m in pattern.finditer(text):
            raw = _strip_trailing(m.group(0))
            if len(raw) < 2:
                continue
            if raw.endswith(":"):
                continue  # bare drive root like "C:"
            if raw not in found:
                found.append(raw)
    return found


def _to_host_path(raw: str) -> str | None:
    """Convert a raw mention to a normalized host path string.

    ``/mnt/c/...`` becomes ``C:/...``; backslashes become forward slashes.
    Drive-letter mentions are dropped on non-Windows hosts (they cannot be
    meaningful there, and ``Path.resolve()`` would prepend the CWD).
    """
    s = raw.replace("\\", "/")
    m = _re.match(r"^/mnt/([a-zA-Z])/(.*)$", s)
    if m:
        s = f"{m.group(1).upper()}:/{m.group(2)}"
    m = _re.match(r"^([a-zA-Z]):/(.*)$", s)
    if m:
        s = f"{m.group(1).upper()}:/{m.group(2)}"
        if _sys.platform != "win32":
            return None
    return _os.path.normpath(s).replace("\\", "/")


def _candidate_root(host: str) -> Path | None:
    """Resolve a host path into a root Path, or None when unusable.

    On Windows the path is fully resolved (symlinks, 8.3 short names,
    ``..``).  On POSIX drive-letter paths never get here (see
    ``_to_host_path``), and POSIX absolute paths resolve normally.
    """
    try:
        if _sys.platform == "win32":
            return Path(host).expanduser().resolve()
        return Path(_os.path.normpath(host)).expanduser().resolve()
    except (OSError, ValueError):
        _log.debug("user_roots: cannot resolve mention %r", host)
        return None


def extract_user_mentioned_roots(
    texts: Iterable[str],
    *,
    workspace: Path | None = None,
    max_roots: int = DEFAULT_MAX_USER_ROOTS,
) -> list[Path]:
    """Extract legal roots from directory mentions in user message texts.

    Args:
        texts: The user's message texts for the current turn (only user-role
            content — never model/tool content).
        workspace: Session workspace; used to drop mentions that are already
            legal roots or that would cover protected config/session paths.
        max_roots: Cap on the number of returned roots.

    Returns:
        Canonical host ``Path`` roots that file tools may accept in
        addition to their static shared roots.  May include not-yet-created
        directories (output dirs the user asked for).
    """
    raws: list[str] = []
    for text in texts:
        if not text:
            continue
        for raw in _raw_mentions(str(text)):
            if raw not in raws:
                raws.append(raw)

    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raws:
        host = _to_host_path(raw)
        if not host:
            continue
        cand = _candidate_root(host)
        if cand is None:
            continue

        # An existing file's parent is the writable root; a directory (or a
        # not-yet-existing path, i.e. a future output dir) roots itself.
        try:
            if cand.is_dir():
                root = cand
            elif cand.is_file():
                root = cand.parent
            else:
                root = cand
        except OSError:
            root = cand

        # Drive root (e.g. C:\) — Path.parent of a drive root is itself.
        if root.parent == root:
            continue
        # The user profile root itself is too broad.
        if root == Path.home().resolve():
            continue
        # Top-level system directories of a drive.
        if _is_top_level_system_dir(root):
            continue
        # Protected paths: config file / per-session files.
        if workspace is not None and _is_protected_extra_root(root, workspace):
            continue
        # Already covered by the workspace root — no need to add.
        if workspace is not None:
            try:
                root.relative_to(workspace.resolve())
                continue
            except ValueError:
                pass

        key = _os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
        if len(roots) >= max_roots:
            break

    return roots
