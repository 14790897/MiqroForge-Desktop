"""Capability-based command guard for the exec tool (issue #811).

The old guard denied DANGEROUS COMMAND STRINGS (``rm -rf`` blocked
everywhere, compound commands blocked as a whole, refusals carried no
guidance) — agents worked around it by switching spellings
(``shutil.rmtree``) or splitting commands.  This module replaces that
pattern-stacking with a path-aware capability check:

  * The command is split into subcommands on top-level ``&&`` / ``||`` /
    ``;`` / ``&`` (quote-, backtick- and paren-aware).
  * Every subcommand is inspected for destructive file operations —
    shell forms (``rm``/``rmdir``/``del``/``unlink``/``shred``,
    ``cp``/``mv``/``install``, ``find -delete``/``-exec``, ``tee``/
    ``mkdir``/``touch``/``ln``, redirect targets) AND inline-script
    forms (``python -c``/``perl -e``/``node -e``/``php -r``/``bash -c``
    payloads containing ``shutil.rmtree``, ``os.remove``, ``fs.rmSync``,
    nested ``rm -rf``, ...) — so re-spelling the operation does not
    bypass the check (capability parity).
  * Every affected path is resolved (canonical ``.``/``..`` handling,
    symlink resolution on the host) and classified against the session
    scope.  Permission levels:

      Level 0  system paths (/etc, /usr, C:\\Windows, drive roots,
               ~/.ssh, the MiQroForge config home)        → deny
      Level 1  global workspace (outside the session tree) → read-only
               (writes/deletes denied with guidance)
      Level 2  current session tree / exec cwd subtree      → read-write
      other sessions / outside workspace / statically
      unresolvable targets ($VAR, globs, command
      substitution) — UNCERTAIN → DENY (fail closed)

    Sandbox semantics: when the exec runs inside the bwrap sandbox,
    ``/home/miqi/**`` and ``/tmp`` are sandbox-internal overlays
    (per-sandbox home/workspace or the session's own bind-mounted
    workspace) and are writable; ``/mnt/c/...`` maps to host paths and
    is classified like a host path.  Deleting the workspace/session
    ROOT itself is always refused.
  * Refusals are structured: reason code + policy + resolved target +
    a safe alternative the agent can act on, so a blocked command
    teaches the agent the legal spelling instead of leaving it to
    guess.  ``sudo`` gets a dedicated
    ``PRIVILEGE_ESCALATION_UNAVAILABLE`` answer.

Pure static analysis — no subprocess, no network.  Conservative by
design: anything that cannot be statically resolved is denied.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── Structured refusal templates ─────────────────────────────────────────

_HEADER = "Error: 命令被沙箱护栏拦截。"

_REASON_TEXTS: dict[str, str] = {
    "privilege": "检测到提权操作（sudo）——沙箱内不提供 root 权限。",
    "system_path": "目标解析后位于系统路径（受保护）。",
    "other_session": "目标位于其他 session 的目录。",
    "outside_workspace": "目标解析后位于当前 session 可操作范围之外。",
    "workspace_level1": (
        "目标位于全局工作区内、但不在当前 session 可操作范围内"
        "（全局工作区只读）。"
    ),
    "workspace_root": "目标是工作区或 session 根目录本身。",
    "uncertain_path": "目标含变量/通配符/命令替换等无法静态解析的成分。",
    "script_uncertain": "内联脚本中的破坏性操作无法静态确定目标路径。",
    "find_exec": "find 的 -exec/-execdir 会执行任意程序，无法静态验证删除范围。",
}

_POLICY_TEXTS: dict[str, str] = {
    "delete": "只允许删除当前 session 工作区内的文件。",
    "write": "只允许写入当前 session 工作区内的文件。",
    "move": "只允许在当前 session 工作区内移动/重命名文件。",
    "uncertain": "无法确认作用范围的破坏性操作一律拒绝（宁可误拒）。",
    "workspace_root": "禁止删除工作区或 session 根目录。",
    "find_exec": "无法确认删除范围的 find 操作一律拒绝。",
    "privilege": "沙箱为无特权环境，系统目录只读，提权命令不可用。",
}

_SAFE_TEXTS: dict[str, str] = {
    "delete": (
        "请将目标限定在当前 session 目录内，例如：rm -rf ./run-output"
    ),
    "write": "请将写入目标限定在当前 session 目录内。",
    "move": "请将源与目标都限定在当前 session 目录内。",
    "uncertain": (
        "请使用明确的目录名（如 rm -rf ./run-output），"
        "不要使用 $变量、通配符或命令替换。"
    ),
    "workspace_root": (
        "请指定根目录下的具体子目录，例如：rm -rf ./run-output"
    ),
    "find_exec": (
        "请用 rm 加明确路径逐个删除，或对 session 目录内的明确子目录"
        "执行 find。"
    ),
    "privilege": (
        "用户级安装：python3 -m pip install --user <包名>；"
        "系统包安装：请在 设置 > 沙箱隔离 中开启「允许系统包安装」后，"
        "sudo apt-get install ... 会自动路由到 WSL 发行版以 root 执行"
        "（仅 Windows + WSL；沙箱环境授权时会出现确认卡，也可选「允许本次安装」）。"
    ),
}

# ── Operation vocabulary ──────────────────────────────────────────────────

_DELETE_WORDS = frozenset({"rm", "rmdir", "del", "unlink", "shred"})
_COPY_MOVE_WORDS = frozenset({"cp", "mv"})
_WRITE_WORDS = frozenset({"tee", "mkdir", "touch", "ln"})

_SCRIPT_LAUNCHERS = frozenset({
    "python", "python2", "python3", "py", "perl", "ruby",
    "node", "nodejs", "php",
})
_SHELL_LAUNCHERS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash"})
_SCRIPT_FLAGS = frozenset({"-c", "-e", "-r"})
#: stdin 启动器（#875 外部评估 B7 实测缺口）：`python3 - <<EOF ... EOF` /
#: `bash -s <<EOF ... EOF` 的 payload 是 heredoc 正文——之前只有 -c/-e/-r
#: 被识别，heredoc 携带的破坏性调用（shutil.rmtree 等）穿透两层护栏。
_STDIN_LAUNCHER_FLAGS = frozenset({"-", "-s"})

#: Words that make a following ``install`` NOT a POSIX file-copy install.
_INSTALL_EXCLUDE_PREV = frozenset({
    "pip", "pip2", "pip3", "npm", "yarn", "pnpm", "make", "run",
    "apt", "apt-get", "dnf", "yum", "zypper", "apk", "pacman",
    "brew", "cargo", "go", "gem", "bundle", "poetry", "uv",
    "composer", "conda", "mamba", "pdm", "hatch", "rye", "meson",
    "ninja", "cmake", "xcodebuild", "dotnet", "nuget", "choco",
    "scoop", "flatpak", "snap", "emerge",
})

#: Destructive call names inside inline-script payloads (issue #811:
#: ``shutil.rmtree`` must be caught the same way ``rm -rf`` is).
_DESTRUCTIVE_CALL_RE = re.compile(
    r"\bshutil\.rmtree\b"
    r"|\bshutil\.move\b"
    r"|\bos\.(remove|unlink|rmdir|removedirs)\b"
    r"|\bos\.system\b"
    r"|\bpathlib\.[^;)]*\.unlink\("
    r"|\.unlink\(\s*\)"
    r"|\bfs\.(rmSync|rmdirSync|unlinkSync|rm|rmdir|unlink)\s*\("
    r"|\bFile\.(delete|unlink)\b"
    r"|\bFileUtils\.(rm|rm_r|rm_rf|rmdir|remove_dir)\b"
    r"|\bunlink\b"
    r"|\brmtree\b"
    r"|\brm\s+-[a-zA-Z]*r"
)

#: String literals inside a script payload are extracted with a LINEAR
#: scanner (_extract_string_literals) — a backreference regex here would
#: be ReDoS-prone (CodeQL high-severity on the original pattern).

#: Redirect tokens (">", "2>", "&>", "2>>", ...), with an optional
#: attached target ("2>/dev/null", ">>file") or fd form ("2>&1").
_REDIRECT_RE = re.compile(r"^(?:[12]?&?)?>{1,2}(.*)$")

#: Harmless device targets.
_DEV_NULL_TARGETS = frozenset({
    "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty",
    "/dev/zero", "/dev/random", "/dev/urandom",
})

#: deny-pattern sources from ExecTool's defaults that the capability
#: engine subsumes for file-op subcommands (kept for the system-install
#: routed path and for non-file-op subcommands).
FILE_OP_PATTERN_EXCLUSIONS = frozenset({
    r"\brm\s+-[rf]{1,2}\b",
    r"\bdel\s+/[fq]\b",
    r"\brmdir\s+/s\b",
})

_SYSTEM_POSIX_ROOTS = frozenset({
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt",
    "/var", "/root", "/boot", "/dev", "/proc", "/sys", "/run",
    "/mnt", "/tmp", "/var/tmp",
})


# ── Tokenizing / splitting ────────────────────────────────────────────────


@dataclass
class _Token:
    """One shell word with quote information.

    ``quoted`` is True when ANY part of the word was quoted — the shell
    strips quotes before executing, so ``'rm'`` and ``s"udo"`` both
    execute as ``rm``/``sudo`` (issue #811 review: critical bypass).
    ``fully_quoted`` is True only when EVERY character was inside quotes.
    ``first_quoted`` records whether the FIRST character was quoted — a
    redirect whose operator itself was quoted (``'>' out``) is a literal
    word, while a quoted TARGET (``2>"/etc/x"``) is still a redirect.
    ``first_escaped`` records whether the first character came from a
    backslash escape (``\\>`` is the literal word ``>``, not a redirect).
    """

    text: str
    quoted: bool = False
    fully_quoted: bool = False
    first_quoted: bool = False
    first_escaped: bool = False


#: In double quotes the shell treats ``\`` as an escape only before
#: these characters; elsewhere it stays literal (``"C:\temp"`` keeps
#: its backslash).
_DQ_ESCAPED_CHARS = frozenset({'$', '`', '"', '\\', '\n'})


def _tokenize(text: str) -> list[_Token]:
    """Split *text* into shell words, tracking quotes and backslash
    escapes with shell semantics.

    Quote characters are stripped from ``text``; outside quotes a
    backslash escapes the NEXT character (``\\rm`` executes as ``rm`` —
    issue #811 review), inside single quotes everything is literal, and
    inside double quotes ``\\`` escapes only ``$ ` " \\`` and newline.
    Flags record quote/escape usage per token (see :class:`_Token`).
    """
    tokens: list[_Token] = []
    buf: list[str] = []
    quote: str | None = None
    n_quoted = 0
    saw_quote = False
    first_quoted = False
    first_escaped = False
    first_seen = False

    def _append(ch: str, *, quoted: bool = False, escaped: bool = False) -> None:
        nonlocal first_quoted, first_escaped, first_seen, n_quoted
        if not first_seen:
            first_quoted = quoted
            first_escaped = escaped
            first_seen = True
        if quoted:
            n_quoted += 1
        buf.append(ch)

    def _flush() -> None:
        nonlocal buf, n_quoted, saw_quote, first_quoted, first_escaped, first_seen
        if buf or saw_quote:
            tokens.append(_Token(
                "".join(buf),
                quoted=n_quoted > 0 or saw_quote,
                fully_quoted=n_quoted > 0 and n_quoted == len(buf),
                first_quoted=first_quoted,
                first_escaped=first_escaped,
            ))
            buf = []
            n_quoted = 0
            saw_quote = False
            first_quoted = False
            first_escaped = False
            first_seen = False

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if quote == "'":
                if ch == "'":
                    quote = None
                else:
                    _append(ch, quoted=True)
                i += 1
                continue
            # double quotes
            if ch == '"':
                quote = None
                i += 1
                continue
            if ch == "\\" and i + 1 < n and text[i + 1] in _DQ_ESCAPED_CHARS:
                _append(text[i + 1], quoted=True, escaped=True)
                i += 2
                continue
            _append(ch, quoted=True)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            saw_quote = True
            i += 1
            continue
        if ch == "\\":
            if i + 1 < n:
                _append(text[i + 1], escaped=True)
                i += 2
                continue
            i += 1  # dangling escape at end of input — drop it
            continue
        if ch.isspace():
            _flush()
            i += 1
            continue
        _append(ch)
        i += 1
    _flush()
    return tokens


def _extract_string_literals(payload: str) -> list[str]:
    """Extract quoted string literals from a script payload.

    Linear scan (no backreference regex — avoids catastrophic
    backtracking on malformed quotes, CodeQL high-severity fix).
    Backslash escapes are kept verbatim inside the literal.
    """
    literals: list[str] = []
    i, n = 0, len(payload)
    while i < n:
        if payload[i] not in ("'", '"'):
            i += 1
            continue
        quote = payload[i]
        i += 1
        buf: list[str] = []
        closed = False
        while i < n:
            c = payload[i]
            if c == "\\" and i + 1 < n:
                buf.append(payload[i:i + 2])
                i += 2
                continue
            if c == quote:
                closed = True
                i += 1
                break
            buf.append(c)
            i += 1
        if closed:
            literals.append("".join(buf))
    return literals


_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _command_positions(tokens: list[_Token]) -> set[int]:
    """Indices of COMMAND WORDS in a subcommand's token list.

    A simple command starts at the beginning of the subcommand or after
    an unquoted pipe (``ls | rm -rf /x``); env-assignment prefixes
    (``FOO=1``) are skipped.  Only these positions are classified as
    operators when the word was quoted — the shell executes the dequoted
    word there regardless of quoting.
    """
    positions: set[int] = set()
    expect = True
    for i, tok in enumerate(tokens):
        if expect:
            if _ASSIGNMENT_RE.match(tok.text):
                continue
            positions.add(i)
            expect = False
        elif not tok.quoted and tok.text == "|":
            expect = True
    return positions


def split_subcommands(command: str) -> list[str]:
    """Split *command* on top-level ``&&``/``||``/``;``/``&`` separators.

    Separators inside quotes, backticks or parentheses (``$(...)``,
    command groups) are NOT split points — the whole substitution stays
    in one subcommand so the deny-pattern / capability checks see it as
    a unit.  Unbalanced quotes/backticks/parens degrade to "one
    subcommand" (conservative, never more permissive).
    """
    parts: list[str] = []
    start = 0
    i = 0
    n = len(command)
    quote: str | None = None
    paren = 0
    backtick = False
    while i < n:
        ch = command[i]
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "`":
            backtick = not backtick
            i += 1
            continue
        if backtick:
            i += 1
            continue
        if ch == "<" and command[i:i + 2] == "<<":
            # heredoc（#875 B7）：正文内的 ; && | 不是命令分隔符——跳到
            # 分隔符行，使整个 heredoc 保持为一个子命令（保守：整体暴露
            # 给 deny-pattern 与能力扫描）。支持 <<EOF / <<-EOF / <<'EOF'。
            j = i + 2
            if j < n and command[j] == "-":
                j += 1
            while j < n and command[j] in " \t":
                j += 1
            delim_start = j
            while j < n and (command[j].isalnum() or command[j] in "_'"):
                j += 1
            delim = command[delim_start:j].strip("'\"")
            if not delim:
                i += 1
                continue
            # 分隔符行：行首 delimiter（可能带 \r）
            idx = command.find("\n" + delim, j)
            if idx == -1:
                idx = command.find(delim, j)
            if idx != -1:
                i = idx + len(delim)
            else:
                i = n  # 未闭合 heredoc——保守：整段视为一个子命令
            continue
        if ch == "(":
            paren += 1
            i += 1
            continue
        if ch == ")" and paren > 0:
            paren -= 1
            i += 1
            continue
        if paren == 0:
            two = command[i:i + 2]
            if two in ("&&", "||"):
                parts.append(command[start:i].strip())
                start = i + 2
                i += 2
                continue
            if ch == ";":
                parts.append(command[start:i].strip())
                start = i + 1
                i += 1
                continue
            if ch == "&":
                # "&>" / "&>>" is a redirect; "2>&1" is an fd redirect
                # (& follows ">").  Neither is a background separator.
                if command[i + 1:i + 2] == ">" or command[i - 1:i] == ">":
                    i += 1
                    continue
                parts.append(command[start:i].strip())
                start = i + 1
                i += 1
                continue
        i += 1
    parts.append(command[start:].strip())
    return [p for p in parts if p]


# ── Path machinery ────────────────────────────────────────────────────────


def _lex_norm(path: str) -> str:
    """Lexically normalize a POSIX-style path (resolve ``.``/``..``).

    Pure string operation — no filesystem access, safe for sandbox
    paths that do not exist on the host.
    """
    parts: list[str] = []
    for seg in path.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not parts:
                continue  # above root: clamp
            else:
                parts.append(seg)
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


def _norm_str(path: str) -> str:
    """Normalize a host path to a comparable string (fw slashes, lowercase
    on Windows)."""
    s = path.replace("\\", "/")
    return s.lower() if os.name == "nt" else s


def _under(path: str, parent: str) -> bool:
    if not parent:
        return False
    p, q = _norm_str(path), _norm_str(parent).rstrip("/")
    return p.startswith(q + "/") or p == q


def _is_system_path(pstr: str, rt: "RuntimePaths") -> bool:
    """True when *pstr* (normalized host path) is a protected system path."""
    n = _norm_str(pstr).rstrip("/")
    if os.name == "nt":
        # drive roots and protected drive trees (any drive letter)
        if re.match(r"^[a-z]:/?$", n):
            return True
        if re.match(
            r"^[a-z]:/(windows|program files|program files \(x86\)|"
            r"programdata|system32)(/|$)",
            n,
        ):
            return True
    else:
        for root in _SYSTEM_POSIX_ROOTS:
            if n == root or n.startswith(root + "/"):
                return True
    if rt.host_home:
        home = _norm_str(rt.host_home).rstrip("/")
        if n == home or n.startswith(home + "/.ssh"):
            return True
    if rt.miqi_home:
        mh = _norm_str(rt.miqi_home).rstrip("/")
        if n == mh or n.startswith(mh + "/"):
            return True
    return False


def _msys_to_windows(path: str) -> str:
    """``/c/Users/x`` → ``C:/Users/x`` (Git Bash msys form)."""
    m = re.match(r"^/([a-zA-Z])/(.+)$", path)
    if not m:
        return path
    return f"{m.group(1).upper()}:/{m.group(2)}"


# ── Runtime context / verdict ─────────────────────────────────────────────


@dataclass
class RuntimePaths:
    """Host/sandbox path context for one guard evaluation."""

    host_cwd: str
    host_workspace: str | None = None
    session_files_dir: str | None = None
    sandbox_active: bool = False
    sandbox_cwd: str = "/home/miqi/workspace"
    miqi_home: str | None = None
    host_home: str | None = None

    @property
    def current_session_key(self) -> str | None:
        """The on-disk session dir key (``sessions/<key>/files`` parent)."""
        if not self.session_files_dir:
            return None
        return Path(self.session_files_dir).parent.name


@dataclass
class GuardVerdict:
    """Result of evaluating one exec command."""

    allowed: bool = True
    message: str = ""
    reason_code: str = ""
    subcommands: list[str] = field(default_factory=list)
    #: indices of subcommands the capability engine checked (file ops) —
    #: callers skip legacy string checks for these.
    handled: list[int] = field(default_factory=list)


# ── Operation detection ───────────────────────────────────────────────────


@dataclass
class _FileOp:
    kind: str  # delete | copy | move | write | find_delete | inline_script
    display: str
    operands: list[_Token] = field(default_factory=list)
    #: for find: starting paths; for scripts: the payload string
    extra: str = ""


def _op_descriptions() -> dict[str, str]:
    return {
        "delete": "删除操作（{verb}）",
        "copy": "复制操作（{verb}）",
        "move": "移动操作（{verb}）",
        "write": "写入操作（{verb}）",
        "find_delete": "find 批量删除",
        "inline_script": "内联脚本破坏性调用（{verb}）",
    }


def _is_flag(tok: _Token, windows_flags: bool = False) -> bool:
    if tok.text.startswith("-"):
        return True
    if windows_flags and re.match(r"^/[A-Za-z]+$", tok.text):
        return True
    return False


def _extract_heredoc_payload(tokens: list[_Token]) -> str:
    """Extract a heredoc body after a stdin launcher flag.

    ``python3 - <<PY\\nimport shutil; shutil.rmtree('/etc/x')\\nPY`` →
    the tokens after ``<<`` until the delimiter token are joined into a
    payload string for the inline-script scanner (#875 B7 实测：stdin
    heredoc 是唯一穿透 launcher 扫描的入口).  Handles both ``<< PY``
    and ``<<PY`` token forms.  Returns "" when no heredoc is present.
    """
    for idx, tok in enumerate(tokens):
        if tok.text == "<<" or tok.text.startswith("<<"):
            prefix = tok.text[2:]
            if prefix.startswith("-"):
                prefix = prefix[1:]
            if idx + 1 < len(tokens) and not prefix:
                prefix = tokens[idx + 1].text.strip("'\"")
            delim = prefix.strip("'\"")
            if not delim:
                return ""
            body_start = idx + 1 if not tok.text[2:] else idx + 1
            body: list[str] = []
            for body_tok in tokens[body_start:]:
                if body_tok.text == delim:
                    break
                body.append(body_tok.text)
            return " ".join(body)
    return ""


def _detect_ops(tokens: list[_Token]) -> list[_FileOp]:
    """Find destructive file operations in one subcommand's token list.

    Operator classification is position-aware (issue #811 review,
    critical bypass): in COMMAND position the dequoted text always
    counts (``'rm' -rf /x`` runs rm), elsewhere a quoted word never
    does (``echo "rm -rf x"`` is not a delete) while an UNQUOTED word
    still does — so ``xargs rm -rf /x`` keeps its rm detection.
    Quoted tokens also act as operands (paths with spaces).
    """
    ops: list[_FileOp] = []
    n = len(tokens)
    cmd_positions = _command_positions(tokens)
    for i, tok in enumerate(tokens):
        if tok.quoted and i not in cmd_positions:
            continue  # quoted argument — never an operator
        text = tok.text
        if text in _DELETE_WORDS:
            ops.append(_FileOp(
                kind="delete", display=text,
                operands=[t for t in tokens[i + 1:]
                          if not _is_flag(t, windows_flags=(text in ("del", "rmdir")))
                          and not re.search(r"[<>]", t.text)],
            ))
            continue
        if text in _COPY_MOVE_WORDS:
            kind = "move" if text == "mv" else "copy"
            ops.append(_FileOp(
                kind=kind, display=text,
                operands=[t for t in tokens[i + 1:]
                          if not _is_flag(t)
                          and not re.search(r"[<>]", t.text)],
            ))
            continue
        if text == "install":
            prev = tokens[i - 1].text if i > 0 else ""
            if prev not in _INSTALL_EXCLUDE_PREV:
                ops.append(_FileOp(
                    kind="copy", display=text,
                    operands=[t for t in tokens[i + 1:]
                              if not _is_flag(t)
                              and not re.search(r"[<>]", t.text)],
                ))
            continue
        if text == "find":
            rest = tokens[i + 1:]
            # Flags are matched on the dequoted/de-escaped text — the
            # shell strips quotes/escapes, so find /etc '-delete' still
            # deletes (issue #811 review).
            has_delete = any(t.text == "-delete" for t in rest)
            has_exec = any(
                t.text in ("-exec", "-execdir") for t in rest
            )
            if has_delete or has_exec:
                paths: list[_Token] = []
                for t in rest:
                    if not t.quoted and (
                        t.text.startswith("-") or t.text in ("!", "(", ")")
                    ):
                        break
                    paths.append(t)
                ops.append(_FileOp(
                    kind="find_delete", display="find",
                    operands=paths, extra="exec" if has_exec else "delete",
                ))
            continue
        if text in _SCRIPT_LAUNCHERS | _SHELL_LAUNCHERS:
            for j in range(i + 1, n):
                ftok = tokens[j]
                # Flag matching on dequoted/de-escaped text: python '-c'
                # still runs the -c code path (issue #811 review).
                if ftok.text not in _SCRIPT_FLAGS:
                    # stdin 启动器（- / -s）：payload 是 <<EOF heredoc 正文
                    # （#875 B7 实测：python3 - <<EOF 可携带 shutil.rmtree 穿透）
                    if ftok.text in _STDIN_LAUNCHER_FLAGS:
                        payload = _extract_heredoc_payload(tokens[j + 1:])
                        if payload:
                            ops.append(_FileOp(
                                kind="inline_script", display=text,
                                operands=[], extra=payload,
                            ))
                    continue
                payload = tokens[j + 1].text if j + 1 < n else ""
                if payload:
                    ops.append(_FileOp(
                        kind="inline_script", display=text,
                        operands=[], extra=payload,
                    ))
                break
            continue
        if text in _WRITE_WORDS:
            operands = [t for t in tokens[i + 1:]
                        if not _is_flag(t)
                        and not re.search(r"[<>]", t.text)]
            if text == "ln":
                # ln: only the link NAME is a write target (the -s target
                # is just a string stored in the link — dangling links are
                # legal and resolve() catches actual escapes elsewhere).
                operands = operands[-1:]
            ops.append(_FileOp(
                kind="write", display=text, operands=operands,
            ))
            continue
    return ops


def _check_redirects(
    tokens: list[_Token], rt: RuntimePaths, *, strict: bool,
) -> tuple[bool, str, str]:
    """Classify shell redirect targets (``>``/``>>``) in a subcommand.

    Returns (ok, reason_code, display).  EVERY redirect target is
    classified — returning only on the first failure (issue #811 review:
    ``echo a > ok.txt 2> /etc/evil`` must be refused).  fd redirects
    (``2>&1``) and harmless device targets are allowed.  A redirect
    whose OPERATOR was quoted (``'>' out``) is a literal word; a quoted
    TARGET (``2>"/etc/x"``) is still a redirect.  Process substitution
    (``>(...)``/``<(...)``) next to a destructive operation is
    UNCERTAIN → denied; without a destructive op it is skipped.
    """
    for i, tok in enumerate(tokens):
        if (tok.quoted and tok.first_quoted) or tok.first_escaped:
            continue  # the ">" itself was quoted/escaped — a literal word
        m = _REDIRECT_RE.match(tok.text)
        if not m:
            continue
        rest = m.group(1)
        if rest.startswith("&"):
            continue  # fd redirect (2>&1)
        if rest:
            ttext = rest
        else:
            target = tokens[i + 1] if i + 1 < len(tokens) else None
            if target is None:
                continue
            ttext = target.text
        ttext = ttext.strip('\'"')
        if not ttext or ttext.startswith("&"):
            continue
        if ttext in _DEV_NULL_TARGETS:
            continue
        if ttext.startswith("("):
            if strict:
                return False, "uncertain_path", ttext
            continue
        ok, code, display = _classify_operand(ttext, rt, for_write=True)
        if not ok:
            return ok, code, display
    return True, "", ""


# ── Classification ────────────────────────────────────────────────────────


def _classify_operand(
    raw: str,
    rt: RuntimePaths,
    *,
    for_write: bool,
) -> tuple[bool, str, str]:
    """Classify one path operand.  Returns (ok, reason_code, display)."""
    text = raw.strip().strip('\'"')
    display = text
    if not text:
        return False, "uncertain_path", display

    # Windows absolute (native or UNC) — backslashes are separators here,
    # not escapes, so the escape check below must not see them.
    win_abs = bool(re.match(r"^[A-Za-z]:[\\/]", text)) or text.startswith("\\\\")

    # Shell expansion / globbing / escapes: statically unresolvable.
    if any(c in text for c in ("$", "`", "*", "?", "[")):
        return False, "uncertain_path", display
    if not win_abs and "\\" in text:
        return False, "uncertain_path", display

    if win_abs:
        return _classify_host(text, rt, for_write=for_write)

    # Tilde expansion — runtime HOME depends on the execution context.
    if text.startswith("~"):
        if rt.sandbox_active:
            if text == "~":
                text = "/home/miqi"
            elif text.startswith("~/"):
                text = "/home/miqi" + text[1:]
            else:
                return False, "uncertain_path", display
        else:
            expanded = os.path.expanduser(text)
            if expanded == text:
                return False, "uncertain_path", display
            text = expanded

    # Git Bash /c/... msys form (host execution only).
    if not rt.sandbox_active:
        msys = re.match(r"^/([a-zA-Z])/(.+)$", text)
        if msys:
            return _classify_host(
                f"{msys.group(1).upper()}:/{msys.group(2)}",
                rt, for_write=for_write,
            )

    if text.startswith("/"):
        if rt.sandbox_active:
            return _classify_sandbox_abs(text, rt, for_write=for_write)
        return _classify_host(text, rt, for_write=for_write)

    # Relative — resolve against the RUNTIME cwd.
    if rt.sandbox_active:
        return _classify_sandbox_abs(
            _lex_norm(rt.sandbox_cwd + "/" + text), rt, for_write=for_write,
        )
    return _classify_host(text, rt, for_write=for_write, relative=True)


def _classify_sandbox_abs(
    path: str, rt: RuntimePaths, *, for_write: bool,
) -> tuple[bool, str, str]:
    """Classify a POSIX path inside the bwrap sandbox."""
    norm = _lex_norm(path)
    if norm == "/home/miqi" or norm == "/home/miqi/workspace":
        if for_write:
            return False, "workspace_root", norm
        return True, "", norm
    # Sandbox-internal home/workspace overlay — per-sandbox files (default
    # workspace) or the session's own bind-mounted workspace (custom).
    if norm.startswith("/home/miqi/"):
        return True, "", norm
    # Sandbox tmpfs overlays
    if norm in ("/tmp", "/var/tmp") or norm.startswith("/tmp/") or norm.startswith("/var/tmp/"):
        return True, "", norm
    # WSL interop bridge — real host files, classify like host paths.
    if norm.startswith("/mnt/"):
        m = re.match(r"^/mnt/([a-zA-Z])/(.+)$", norm)
        if m:
            return _classify_host(
                f"{m.group(1).upper()}:/{m.group(2)}", rt, for_write=for_write,
            )
        return False, "outside_workspace", norm
    if _norm_str(norm) == "/" or any(
        _norm_str(norm) == r or _norm_str(norm).startswith(r + "/")
        for r in _SYSTEM_POSIX_ROOTS
    ):
        if for_write:
            return False, "system_path", norm
        return True, "", norm
    if for_write:
        return False, "outside_workspace", norm
    return True, "", norm


def _classify_host(
    path: str, rt: RuntimePaths, *, for_write: bool, relative: bool = False,
) -> tuple[bool, str, str]:
    """Classify a host path (symlink-resolving) against the session scope."""
    try:
        base = Path(rt.host_cwd)
        p = (base / path if relative else Path(path)).resolve()
    except (ValueError, OSError):
        return False, "uncertain_path", path
    pstr = _norm_str(str(p)).rstrip("/") or "/"
    ws = _norm_str(rt.host_workspace).rstrip("/") if rt.host_workspace else None
    sess = _norm_str(rt.session_files_dir).rstrip("/") if rt.session_files_dir else None
    cwd = _norm_str(rt.host_cwd).rstrip("/")

    # 1. Workspace root itself — never a write/delete target.
    if ws and pstr == ws:
        if for_write:
            return False, "workspace_root", pstr
        return True, "", pstr

    # 2. Session files dir root itself — never a delete target.
    if sess and pstr == sess:
        if for_write:
            return False, "workspace_root", pstr
        return True, "", pstr

    # 3. Session trees: current session → Level 2 (its root dir excluded);
    #    others → deny for reads AND writes (cross-session out of scope).
    if ws and (pstr == ws + "/sessions" or pstr.startswith(ws + "/sessions/")):
        key = pstr[len(ws + "/sessions/"):].split("/")[0]
        if key != _norm_str(rt.current_session_key or ""):
            return False, "other_session", pstr
        if for_write and pstr == f"{ws}/sessions/{key}":
            return False, "workspace_root", pstr
        return True, "", pstr

    # 4. Inside the exec cwd subtree → the session's active working area.
    if _under(pstr, cwd):
        return True, "", pstr

    # 5. Reads anywhere else are unrestricted (cp sources etc.).
    if not for_write:
        return True, "", pstr

    # 6. System paths → deny.
    if _is_system_path(pstr, rt):
        return False, "system_path", pstr

    # 7. Global workspace but outside the session tree → Level 1 read-only.
    if ws and _under(pstr, ws):
        return False, "workspace_level1", pstr

    return False, "outside_workspace", pstr


# ── Refusals ──────────────────────────────────────────────────────────────


def _refusal(op_desc: str, code: str, target: str, policy_kind: str) -> str:
    reason = _REASON_TEXTS.get(code, "目标不在允许范围内。")
    policy = _POLICY_TEXTS.get(policy_kind, _POLICY_TEXTS["uncertain"])
    safe = _SAFE_TEXTS.get(policy_kind, _SAFE_TEXTS["uncertain"])
    return (
        f"{_HEADER}\n"
        f"原因：检测到{op_desc}，{reason}\n"
        f"当前策略：{policy}\n"
        f"目标：{target}\n"
        f"安全替代：{safe}"
    )


_PRIVILEGE_MSG = (
    f"{_HEADER}\n"
    f"原因：{_REASON_TEXTS['privilege']}\n"
    f"当前策略：{_POLICY_TEXTS['privilege']}\n"
    f"安全替代：{_SAFE_TEXTS['privilege']}"
)


# ── Entry point ───────────────────────────────────────────────────────────


def _check_find_op(
    op: _FileOp, rt: RuntimePaths, sub: str,
) -> tuple[bool, str, str, str]:
    """Check a find_delete op (-exec denied; -delete paths classified).

    find never deletes its starting point itself — only the CONTENTS
    matter, so session-tree roots are allowed while workspace / sessions
    roots (whose contents span other sessions) stay denied.
    """
    desc = _op_descriptions()["find_delete"].format(verb=op.display)
    if op.extra == "exec":
        return (
            False, "find_exec", sub[:200],
            _refusal(desc, "find_exec", sub[:200], "find_exec"),
        )
    targets = op.operands or [_Token(".")]
    for t in targets:
        ok, code, display = _classify_operand(t.text, rt, for_write=True)
        if not ok and code == "workspace_root":
            d = _norm_str(display).rstrip("/") or "/"
            ws = (
                _norm_str(rt.host_workspace).rstrip("/")
                if rt.host_workspace else ""
            )
            sess = (
                _norm_str(rt.session_files_dir).rstrip("/")
                if rt.session_files_dir else ""
            )
            key = _norm_str(rt.current_session_key or "")
            if d in ("/home/miqi", "/home/miqi/workspace"):
                ok = True  # sandbox-internal contents
            elif ws and (d == ws or d == ws + "/sessions"):
                ok = False  # spans other sessions
            elif sess and d == sess:
                ok = True  # session files dir contents
            elif ws and key and d == f"{ws}/sessions/{key}":
                ok = True  # session tree contents
            else:
                ok = False
        if not ok:
            return (
                False, code, display,
                _refusal(desc, code, display, "delete"),
            )
    return True, "", "", ""


def _check_ops_operands(
    op: _FileOp, rt: RuntimePaths,
) -> tuple[bool, str, str, str]:
    """Classify a delete/copy/move/write op's operands.

    Returns (ok, reason_code, display, refusal_message); on pass the
    message fields are empty.  COPY: source readable + target writable;
    MOVE: source AND target writable (move removes the source);
    DELETE/WRITE: every operand writable (i.e. inside session scope).
    """
    desc = _op_descriptions()[op.kind].format(verb=op.display)
    operands = op.operands
    policy = {"delete": "delete", "copy": "write",
              "move": "move", "write": "write"}.get(op.kind)
    if policy is None:
        return True, "", "", ""  # unknown kind — not a file op we check
    if op.kind in ("copy", "move") and len(operands) < 2:
        return True, "", "", ""  # shell will error; nothing is copied/moved
    if op.kind in ("delete", "write") and not operands:
        return True, "", "", ""
    if op.kind in ("copy", "move"):
        sources = operands[:-1]
        target = operands[-1]
        # Sources: readable unless in ANOTHER session's tree.
        for src in sources:
            ok, code, display = _classify_operand(
                src.text, rt, for_write=False,
            )
            if not ok:
                return False, code, display, _refusal(desc, code, display, policy)
        # mv removes the source: source must be in-scope too.
        if op.kind == "move":
            for src in sources:
                ok, code, display = _classify_operand(
                    src.text, rt, for_write=True,
                )
                if not ok:
                    return False, code, display, _refusal(desc, code, display, policy)
        ok, code, display = _classify_operand(target.text, rt, for_write=True)
        if not ok:
            return False, code, display, _refusal(desc, code, display, policy)
        return True, "", "", ""
    # delete / write (rm, tee, mkdir, touch, ln targets)
    for t in operands:
        ok, code, display = _classify_operand(t.text, rt, for_write=True)
        if not ok:
            return False, code, display, _refusal(desc, code, display, policy)
    return True, "", "", ""


def evaluate_command(command: str, rt: RuntimePaths) -> GuardVerdict:
    """Evaluate *command* with the capability engine.

    Splits into subcommands, checks each for destructive file
    operations and classifies every affected path.  Returns a verdict
    with a structured refusal message when denied.
    """
    subcommands = split_subcommands(command)
    verdict = GuardVerdict(subcommands=subcommands)
    desc_map = _op_descriptions()

    for idx, sub in enumerate(subcommands):
        tokens = _tokenize(sub)

        # 1. Privilege escalation — always refused, with alternatives.
        #    Command-position aware: `'sudo'` / `s"udo"` execute as sudo
        #    (quote removal) and must be refused (issue #811 review).
        if any(
            i in _command_positions(tokens) and tok.text == "sudo"
            for i, tok in enumerate(tokens)
        ):
            verdict.allowed = False
            verdict.reason_code = "privilege"
            verdict.message = _PRIVILEGE_MSG
            return verdict

        # 2. Destructive file operations — path-aware capability check.
        ops = _detect_ops(tokens)

        # 3. Redirect targets — every subcommand's write targets must be
        #    in scope.  Process substitution is UNCERTAIN only next to a
        #    destructive operation (see _check_redirects).
        ok, code, display = _check_redirects(tokens, rt, strict=bool(ops))
        if not ok:
            verdict.allowed = False
            verdict.reason_code = code
            verdict.message = _refusal(
                "写入操作（重定向）", code, display, "write",
            )
            return verdict

        if not ops:
            continue
        verdict.handled.append(idx)
        for op in ops:
            op_desc = desc_map[op.kind].format(verb=op.display)

            if op.kind == "find_delete":
                ok, code, display, msg = _check_find_op(op, rt, sub)
                if not ok:
                    verdict.allowed = False
                    verdict.reason_code = code
                    verdict.message = msg
                    return verdict
                continue

            if op.kind == "inline_script":
                payload = op.extra
                # Nested shell (bash -c "rm -rf /x"): parse the payload as
                # a mini subcommand so its file ops AND redirect targets
                # get precise path classification instead of a blanket
                # UNCERTAIN (issue #811 review: bash -c "echo x >
                # /etc/evil" must be refused).
                if op.display in _SHELL_LAUNCHERS:
                    inner_tokens = _tokenize(payload)
                    inner_ops = _detect_ops(inner_tokens)
                    ok, code, display = _check_redirects(
                        inner_tokens, rt, strict=bool(inner_ops),
                    )
                    if not ok:
                        verdict.allowed = False
                        verdict.reason_code = code
                        verdict.message = _refusal(
                            f"{op.display} -c 中的写入操作（重定向）",
                            code, display, "write",
                        )
                        return verdict
                    for inner in inner_ops:
                        if inner.kind == "inline_script":
                            continue  # deeper nesting — not parsed
                        inner.display = f"{op.display} -c"
                        if inner.kind == "find_delete":
                            ok, code, display, msg = _check_find_op(
                                inner, rt, payload[:200],
                            )
                        else:
                            ok, code, display, msg = _check_ops_operands(
                                inner, rt,
                            )
                        if not ok:
                            verdict.allowed = False
                            verdict.reason_code = code
                            verdict.message = msg
                            return verdict
                    continue
                if not _DESTRUCTIVE_CALL_RE.search(payload):
                    continue  # plain python -c "print(1)" etc.
                literals = _extract_string_literals(payload)
                path_literals = [
                    lit for lit in literals
                    if "/" in lit or "\\" in lit
                    or lit.startswith(".") or lit.startswith("~")
                ]
                if not path_literals:
                    verdict.allowed = False
                    verdict.reason_code = "script_uncertain"
                    verdict.message = _refusal(
                        op_desc, "script_uncertain", payload[:200], "uncertain",
                    )
                    return verdict
                for lit in path_literals:
                    ok, code, display = _classify_operand(
                        lit, rt, for_write=True,
                    )
                    if not ok:
                        verdict.allowed = False
                        verdict.reason_code = code
                        verdict.message = _refusal(op_desc, code, display, "delete")
                        return verdict
                continue

            # delete / copy / move / write
            ok, code, display, msg = _check_ops_operands(op, rt)
            if not ok:
                verdict.allowed = False
                verdict.reason_code = code
                verdict.message = msg
                return verdict

    return verdict
