"""Codex-style fuzzy file search runtime (Phase 46).

Provides ``FuzzyFileSearchRuntime`` — a deterministic filename search
engine that crawls workspace directories, scores filenames and relative
paths against a query, and returns up to 50 ranked results.

Session methods (sessionStart/sessionUpdate/sessionStop) are
client-scoped and emit ``fuzzyFileSearch/sessionUpdated`` and
``fuzzyFileSearch/sessionCompleted`` notifications.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger

# ── Constants ────────────────────────────────────────────────────────────────

MAX_RESULTS = 50
MAX_CANDIDATES = 20_000
SKIPPED_DIR_NAMES: set[str] = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}


# ── Scoring ──────────────────────────────────────────────────────────────────


def fuzzy_match_score(query: str, candidate: str) -> tuple[int, list[int]] | None:
    """Score a candidate string against *query*.

    Returns ``(score, indices)`` where *score* is an integer and *indices*
    are the character positions of the match, or ``None`` if there is no
    match.

    Scoring tiers (from the Codex protocol):
    - Exact case-insensitive substring match → base 1000 + position bonus
    - Subsequence match → base 500 + compactness bonus
    """
    query_l = query.casefold()
    candidate_l = candidate.casefold()
    if not query_l:
        return None

    # Tier 1: exact case-insensitive substring
    pos = candidate_l.find(query_l)
    if pos >= 0:
        indices = list(range(pos, pos + len(query_l)))
        # Earlier matches score higher
        return 1000 + max(0, 200 - pos), indices

    # Tier 2: subsequence match
    indices: list[int] = []
    cursor = 0
    for char in query_l:
        found = candidate_l.find(char, cursor)
        if found < 0:
            return None
        indices.append(found)
        cursor = found + 1
    # More compact matches (shorter span between first and last char) score higher
    compactness = max(0, 200 - (indices[-1] - indices[0]))
    return 500 + compactness, indices


# ── Candidate collection ─────────────────────────────────────────────────────


def _collect_candidates(roots: list[Path]) -> list[tuple[Path, str, str]]:
    """Walk *roots* and collect candidate (root, absolute_path, relative_path) tuples.

    Skips directories in ``SKIPPED_DIR_NAMES``.  Caps at ``MAX_CANDIDATES``.
    Returns the relative path with forward slashes.
    """
    candidates: list[tuple[Path, str, str]] = []

    for root in roots:
        if not root.exists():
            continue
        try:
            for dirpath_str, dirnames, filenames in os.walk(str(root), topdown=True):
                dirpath = Path(dirpath_str)
                # Skip noise directories in-place
                dirnames[:] = [
                    d for d in dirnames if d not in SKIPPED_DIR_NAMES
                ]
                rel_base = dirpath.relative_to(root)

                # Yield directory entries
                for d in dirnames:
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    abs_path = dirpath / d
                    rel_path = str(rel_base / d).replace("\\", "/")
                    if rel_path == ".":
                        rel_path = d
                    candidates.append((root, rel_path, str(abs_path)))

                # Yield file entries
                for f in filenames:
                    if len(candidates) >= MAX_CANDIDATES:
                        break
                    abs_path = dirpath / f
                    rel_path = str(rel_base / f).replace("\\", "/")
                    if rel_path == ".":
                        rel_path = f
                    candidates.append((root, rel_path, str(abs_path)))

                if len(candidates) >= MAX_CANDIDATES:
                    break
        except OSError:
            continue

    return candidates


# ── Search ───────────────────────────────────────────────────────────────────


def _search_roots(query: str, roots: list[Path]) -> list[dict[str, Any]]:
    """Run a one-shot search and return scored results.

    Returns up to ``MAX_RESULTS`` results sorted by ``(-score, path)``.
    """
    if not query:
        return []

    candidates = _collect_candidates(roots)
    scored: list[tuple[int, str, dict[str, Any]]] = []

    for root, rel_path, abs_path_str in candidates:
        p = Path(abs_path_str)
        is_dir = p.is_dir()

        # Score against filename
        file_score = fuzzy_match_score(query, p.name)
        # Score against relative path
        path_score = fuzzy_match_score(query, rel_path)

        best_score: int | None = None
        best_indices: list[int] = []

        if file_score is not None:
            best_score, best_indices = file_score
        if path_score is not None:
            if best_score is None or path_score[0] > best_score:
                best_score, best_indices = path_score

        if best_score is None:
            continue

        # Short path bonus (shorter relative paths rank slightly higher)
        path_bonus = max(0, 40 - len(rel_path))
        best_score += path_bonus

        result = {
            "root": str(root),
            "path": rel_path,
            "match_type": "directory" if is_dir else "file",
            "file_name": p.name,
            "score": best_score,
            "indices": best_indices,
        }
        # Stable sort key: (-score, path) for deterministic ordering
        scored.append((-best_score, rel_path, result))

    # Sort by (-score, path) — using negative score so ascending sort gives
    # highest scores first, with path as tiebreaker
    scored.sort(key=lambda x: (x[0], x[1]))

    return [item[2] for item in scored[:MAX_RESULTS]]


# ── Runtime ──────────────────────────────────────────────────────────────────


class FuzzyFileSearchRuntime:
    """Client-scoped fuzzy file search engine.

    Owned through ``registry.bridge_context["fuzzy_file_search_runtime"]``.
    """

    def __init__(self, app_server: Any):
        self._app_server = app_server
        # (client_id, session_id) → {"roots": [...], "query": str}
        self._sessions: dict[tuple[str, str], dict[str, Any]] = {}

    # ── One-shot search ─────────────────────────────────────────────────────

    def search(self, query: str, roots: list[Path]) -> list[dict[str, Any]]:
        """Run a one-shot (non-session) search.

        Returns up to ``MAX_RESULTS`` scored results.  Empty query
        returns an empty list.
        """
        return _search_roots(query, roots)

    # ── Session management ──────────────────────────────────────────────────

    def session_start(
        self, client_id: str, session_id: str, roots: list[Path],
    ) -> None:
        """Create or replace a session for *client_id*."""
        key = (client_id, session_id)
        self._sessions[key] = {"roots": roots, "query": ""}
        logger.debug(
            "FuzzyFileSearchRuntime: session {} started for client {}",
            session_id, client_id,
        )

    async def session_update(
        self, client_id: str, session_id: str, query: str,
    ) -> None:
        """Run a session query and emit notifications.

        Raises:
            KeyError if the session does not exist.
        """
        key = (client_id, session_id)
        session = self._sessions.get(key)
        if session is None:
            raise KeyError(f"Unknown session: {session_id}")

        session["query"] = query

        # Run search
        if query:
            files = _search_roots(query, session["roots"])
        else:
            files = []

        # Emit sessionUpdated
        await self._app_server.emit_client_event(
            client_id,
            "fuzzyFileSearch/sessionUpdated",
            {
                "sessionId": session_id,
                "query": query,
                "files": files,
            },
        )

        # Emit sessionCompleted
        await self._app_server.emit_client_event(
            client_id,
            "fuzzyFileSearch/sessionCompleted",
            {"sessionId": session_id},
        )

    def session_stop(self, client_id: str, session_id: str) -> None:
        """Stop and remove a session.  Missing session succeeds silently."""
        key = (client_id, session_id)
        removed = self._sessions.pop(key, None)
        if removed is not None:
            logger.debug(
                "FuzzyFileSearchRuntime: session {} stopped for client {}",
                session_id, client_id,
            )

    def cleanup_client(self, client_id: str) -> None:
        """Remove all sessions owned by *client_id*."""
        keys = [k for k in self._sessions if k[0] == client_id]
        for key in keys:
            del self._sessions[key]
        if keys:
            logger.debug(
                "FuzzyFileSearchRuntime: cleaned up {} session(s) for client {}",
                len(keys), client_id,
            )
