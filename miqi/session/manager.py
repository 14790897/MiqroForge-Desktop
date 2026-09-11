"""Session management for conversation history."""

import json
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.paths import get_legacy_data_dir
from miqi.utils.helpers import ensure_dir, safe_filename


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _parse_iso_datetime(value: str) -> datetime | None:
    try:
        return _normalize_datetime(datetime.fromisoformat(value))
    except ValueError:
        return None


@dataclass
class Session:
    """A conversation session stored as append-only JSONL messages."""

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already archived to memory files
    saved_count: int = 0  # Number of messages already persisted to disk

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        now = datetime.now()
        msg = {
            "role": role,
            "content": content,
            "timestamp": now.isoformat(),
            **kwargs,
        }
        msg_ts = msg.get("timestamp")
        if isinstance(msg_ts, str):
            parsed_ts = _parse_iso_datetime(msg_ts)
            if parsed_ts is None:
                msg["timestamp"] = now.isoformat()
                self.updated_at = now
            else:
                self.updated_at = parsed_ts
        else:
            msg["timestamp"] = now.isoformat()
            self.updated_at = now
        self.messages.append(msg)

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated history, aligned to a user turn."""
        unconsolidated = self.messages[self.last_consolidated :]
        sliced = unconsolidated[-max_messages:]

        # Drop leading non-user messages to avoid orphaned tool_result blocks.
        # A window containing no user turn at all has nothing to align to —
        # return an empty history rather than orphaned assistant/tool rows
        # (LLM providers reject orphaned tool roles).
        user_idx = next(
            (idx for idx, item in enumerate(sliced) if item.get("role") == "user"),
            None,
        )
        if user_idx is None:
            return []
        sliced = sliced[user_idx:]

        out: list[dict[str, Any]] = []
        for item in sliced:
            # Map MiQi-internal pseudo roles to LLM-accepted roles.  Subagent
            # results are rendered into the conversation as `subagent` for UI
            # purposes, but LLM providers only accept
            # system/user/assistant/tool — passing `subagent` raises a 400.
            role = item["role"]
            if role == "subagent":
                role = "assistant"
            entry: dict[str, Any] = {
                "role": role,
                "content": item.get("content", ""),
            }
            for key in ("tool_calls", "tool_call_id", "name"):
                if key in item:
                    entry[key] = item[key]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset archive cursor."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()
        # Keep saved_count so save() can detect history shrink and rewrite safely.


class OwnershipError(Exception):
    """Raised when a client attempts to access a session it does not own.

    Codes:
    - UNAUTHORIZED: session is owned by a different client
    - REQUIRES_CLAIM: session is unowned (legacy) and must be explicitly claimed
    """

    def __init__(self, message: str, *, code: str = "UNAUTHORIZED"):
        super().__init__(message)
        self.code = code


class SessionManager:
    """Manages conversation sessions stored as JSONL files."""

    def __init__(
        self,
        workspace: Path,
        compact_threshold_messages: int = 400,
        compact_threshold_bytes: int = 2_000_000,
        compact_keep_messages: int = 300,
        *,
        legacy_sessions_dir: Path | None = None,
    ):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_sessions_dir = (
            legacy_sessions_dir
            if legacy_sessions_dir is not None
            else get_legacy_data_dir() / "sessions"
        )
        self.compact_threshold_messages = max(1, compact_threshold_messages)
        self.compact_threshold_bytes = max(1, compact_threshold_bytes)
        self.compact_keep_messages = max(1, compact_keep_messages)
        self._cache: dict[str, Session] = {}
        self._session_locks: dict[str, threading.RLock] = {}
        self._session_locks_guard = threading.Lock()

    def get_session_dir(self, key: str) -> Path:
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / safe_key

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session key."""
        return self.get_session_dir(key) / "conversation.jsonl"

    def _get_session_lock(self, key: str) -> threading.RLock:
        with self._session_locks_guard:
            lock = self._session_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[key] = lock
            return lock

    def _migrate_flat_to_dir(self, key: str) -> None:
        """If old flat .jsonl exists and new dir does not, migrate."""
        safe_key = safe_filename(key.replace(":", "_"))
        old_flat = self.sessions_dir / f"{safe_key}.jsonl"
        new_dir  = self.sessions_dir / safe_key
        if old_flat.exists() and not new_dir.exists():
            new_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_flat), str(new_dir / "conversation.jsonl"))

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path for migration only."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str, *, client_id: str | None = None, workspace: Path | None = None) -> Session:
        """Get an existing session from cache/disk or create a new one.

        Ownership semantics (when client_id is provided):
        - NEW session: owner_client_id is set to client_id automatically.
        - EXISTING, owned by client_id: returned normally.
        - EXISTING, owned by DIFFERENT client: raises OwnershipError(UNAUTHORIZED).
        - EXISTING, UNOWNED (legacy): raises OwnershipError(REQUIRES_CLAIM) —
          auto-claim is NOT performed. The caller must use claim_session().

        When client_id is None (Historical: backward compat, CLI/AgentLoop only):
        - No ownership checks are performed.
        - New sessions are created without owner_client_id.

        When workspace is provided for a NEW session, it is stored in metadata.
        The path is validated for safety (no traversal, must be absolute).
        """
        if key in self._cache:
            session = self._cache[key]
            if client_id is not None:
                owner = session.metadata.get("owner_client_id")
                if owner is None:
                    raise OwnershipError(
                        f"Session '{key}' is a legacy session with no owner. "
                        "It must be explicitly claimed before access.",
                        code="REQUIRES_CLAIM",
                    )
                if owner != client_id:
                    raise OwnershipError(
                        f"Session '{key}' is owned by client '{owner}', not '{client_id}'",
                        code="UNAUTHORIZED",
                    )
            # Explicit workspace wins even for an existing (cached) session —
            # the frontend persists the user's pick via sessions.get(workspace=...)
            # which may arrive after a bridge-not-ready retry already created
            # the session without a workspace.
            if workspace is not None:
                session.metadata["workspace"] = str(self._validate_workspace(workspace))
            return session

        session = self._load(key)
        if session is None:
            # New session
            session = Session(key=key)
            if client_id is not None:
                session.metadata["owner_client_id"] = client_id
            if workspace is not None:
                ws = self._validate_workspace(workspace)
                session.metadata["workspace"] = str(ws)
        else:
            # Existing session on disk
            if client_id is not None:
                owner = session.metadata.get("owner_client_id")
                if owner is None:
                    # Unowned legacy session — DO NOT auto-claim
                    raise OwnershipError(
                        f"Session '{key}' is a legacy session with no owner. "
                        "It must be explicitly claimed before access.",
                        code="REQUIRES_CLAIM",
                    )
                if owner != client_id:
                    raise OwnershipError(
                        f"Session '{key}' is owned by client '{owner}', not '{client_id}'",
                        code="UNAUTHORIZED",
                    )
            # Same as cache path: explicit workspace overrides on disk session.
            if workspace is not None:
                session.metadata["workspace"] = str(self._validate_workspace(workspace))

        self._cache[key] = session
        return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        self._migrate_flat_to_dir(key)
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at: datetime | None = None
            updated_at: datetime | None = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue

                    data = json.loads(line)
                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        # Propagate owner_client_id from top-level
                        # (top-level is used by get_owner() for fast queries;
                        #  metadata sub-dict is used by Session.metadata.get())
                        owner_from_top = data.get("owner_client_id")
                        if owner_from_top and "owner_client_id" not in metadata:
                            metadata["owner_client_id"] = owner_from_top
                        if data.get("created_at"):
                            created_at = datetime.fromisoformat(data["created_at"])
                        if data.get("updated_at"):
                            updated_at = datetime.fromisoformat(data["updated_at"])
                        last_consolidated = int(data.get("last_consolidated", 0) or 0)
                    else:
                        messages.append(data)
                        msg_ts = data.get("timestamp")
                        if isinstance(msg_ts, str):
                            try:
                                updated_at = datetime.fromisoformat(msg_ts)
                            except Exception:
                                pass

            session = Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=max(0, min(last_consolidated, len(messages))),
                saved_count=len(messages),
            )
            return session
        except Exception as exc:
            logger.warning("Failed to load session {}: {}", key, exc)
            return None

    def save(self, session: Session) -> None:
        """Persist session changes with append-only writes when possible."""
        with self._get_session_lock(session.key):
            self._migrate_flat_to_dir(session.key)
            path = self._get_session_path(session.key)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._sync_updated_at_from_messages(session)

            should_rewrite = (
                not path.exists() or len(session.messages) < session.saved_count
            )

            # Force rewrite if owner_client_id was set but not yet on disk
            if not should_rewrite and session.metadata.get("owner_client_id"):
                owner_on_disk = self._read_owner(session.key)
                if owner_on_disk is None:
                    should_rewrite = True

            # Force rewrite when the workspace changed on disk: the append-only
            # path rewrites the metadata line only when NEW messages arrive, so
            # a workspace change alone would be lost (chat.send workspace param
            # or the UI picker on an existing session — #607 MOF e2e caught the
            # sandbox staying on the default workspace because of this).
            # Cache the last persisted value on the session object: _read_workspace
            # reads the WHOLE session file (to find the latest message ts), so
            # re-verifying every save would be an O(file) read under the lock.
            if not should_rewrite and session.metadata.get("workspace"):
                workspace_on_disk = getattr(session, "_persisted_workspace", None)
                if workspace_on_disk is None:
                    workspace_on_disk = self._read_workspace(session.key)
                if workspace_on_disk != session.metadata.get("workspace"):
                    should_rewrite = True

            if should_rewrite:
                with open(path, "w", encoding="utf-8") as f:
                    metadata_line = self._metadata_line_for_session(session)
                    f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                    for msg in session.messages:
                        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                path.chmod(0o600)  # Restrict to owner only (SEC-07)
                session.saved_count = len(session.messages)
                session._persisted_workspace = session.metadata.get("workspace")
            else:
                new_messages = session.messages[session.saved_count :]
                if new_messages:
                    with open(path, "a", encoding="utf-8") as f:
                        for msg in new_messages:
                            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    self._rewrite_metadata_line(path, session)
                    path.chmod(0o600)  # Restrict to owner only (SEC-07)
                    session.saved_count = len(session.messages)
                    session._persisted_workspace = session.metadata.get("workspace")

            self._cache[session.key] = session
            self.compact_if_needed(session.key)

    # ── Tracked files (sidebar) ───────────────────────────────────────

    def _get_tracked_files_path(self, key: str) -> Path:
        """Path to the per-session tracked_files.json."""
        self._migrate_flat_to_dir(key)
        return self.get_session_dir(key) / "tracked_files.json"

    def load_tracked_files(
        self, key: str, *, client_id: str | None = None,
    ) -> dict[str, dict]:
        """Load tracked files map {normalized_path: {op, name, lastSeen}}.

        When client_id is provided, ownership is verified first.
        Returns an empty dict if the file doesn't exist or is corrupt.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        path = self._get_tracked_files_path(key)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("files", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_tracked_file(
        self, key: str, file_path: str, op: str = "read",
        name: str = "", *, client_id: str | None = None,
    ) -> None:
        """Upsert a single tracked file entry.

        ``file_path`` is normalised to forward-slash internally.
        ``op`` is one of: read, write, edit, delete.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        files = self.load_tracked_files(key)
        norm = file_path.replace("\\", "/")
        existing = files.get(norm, {})
        # Upgrade: read < edit < write < delete
        rank = {"read": 0, "edit": 1, "write": 2, "delete": 3}
        cur_rank = rank.get(existing.get("op", "read"), 0)
        new_rank = rank.get(op, 0)
        if new_rank >= cur_rank:
            from pathlib import PurePosixPath
            files[norm] = {
                "op": op,
                "name": name or PurePosixPath(norm).name,
                "lastSeen": int(datetime.now().timestamp() * 1000),
            }
        path = self._get_tracked_files_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "files": files}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def save_tracked_files_batch(
        self, key: str, entries: list[tuple[str, str]],
        *, client_id: str | None = None,
    ) -> None:
        """Batch-upsert tracked file entries with ONE read + ONE write.

        ``entries`` is a list of (file_path, op). Same rank semantics as
        ``save_tracked_file`` (read < edit < write < delete). Used by the
        exec artifact tracker (Phase 59 / #607): N files created by one
        command no longer cost N full read+rewrite cycles on the caller's
        thread (CodeRabbit #682 review).
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        if not entries:
            return
        files = self.load_tracked_files(key)
        rank = {"read": 0, "edit": 1, "write": 2, "delete": 3}
        now = int(datetime.now().timestamp() * 1000)
        from pathlib import PurePosixPath

        for file_path, op in entries:
            norm = file_path.replace("\\", "/")
            existing = files.get(norm, {})
            if rank.get(op, 0) >= rank.get(existing.get("op", "read"), 0):
                files[norm] = {
                    "op": op,
                    "name": PurePosixPath(norm).name,
                    "lastSeen": now,
                }
        path = self._get_tracked_files_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "files": files}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def reset_tracked_file_op(
        self, key: str, file_path: str, op: str = "read",
        *, client_id: str | None = None,
    ) -> None:
        """Force-reset the op of a tracked file entry (ignoring rank).

        Unlike ``save_tracked_file`` this bypasses the rank guard so a
        ``write`` entry can be downgraded back to ``read`` after accept.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        files = self.load_tracked_files(key)
        norm = file_path.replace("\\", "/")
        if norm not in files:
            return
        files[norm]["op"] = op
        files[norm]["lastSeen"] = int(datetime.now().timestamp() * 1000)
        path = self._get_tracked_files_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "files": files}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def remove_tracked_file(
        self, key: str, file_path: str, *, client_id: str | None = None,
    ) -> None:
        """Remove a single tracked file entry.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        files = self.load_tracked_files(key)
        norm = file_path.replace("\\", "/")
        files.pop(norm, None)
        path = self._get_tracked_files_path(key)
        if not files:
            path.unlink(missing_ok=True)
            return
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "files": files}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)

    def clear_tracked_files(
        self, key: str, *, client_id: str | None = None,
    ) -> None:
        """Remove the entire tracked_files.json for a session.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        path = self._get_tracked_files_path(key)
        path.unlink(missing_ok=True)

    # ── Archive ───────────────────────────────────────────────────────

    def _get_archived_marker(self, key: str) -> Path:
        """Path to the archive marker file."""
        self._migrate_flat_to_dir(key)
        return self.get_session_dir(key) / ".archived"

    def invalidate(self, key: str) -> None:
        """Remove a session from in-memory cache."""
        self._cache.pop(key, None)

    def archive(self, key: str, *, client_id: str | None = None) -> None:
        """Mark a session as archived.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        path = self._get_archived_marker(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.invalidate(key)

    def unarchive(self, key: str, *, client_id: str | None = None) -> None:
        """Remove archived marker from a session.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        path = self._get_archived_marker(key)
        path.unlink(missing_ok=True)
        self.invalidate(key)

    @staticmethod
    def _validate_workspace(workspace: Path) -> Path:
        """Validate and normalize a workspace path for safe storage.

        Requires an absolute path (expanduser resolves a leading ~), and
        rejects path traversal. ``..`` must be checked BEFORE resolve(),
        because resolve() collapses ``..`` segments — a traversal check
        after resolve can never fire.
        """
        if not workspace.is_absolute():
            raise ValueError(f"Workspace path must be absolute: {workspace}")
        ws_str = str(workspace.expanduser())
        if ".." in ws_str.split(os.sep):
            raise ValueError(f"Workspace path contains traversal: {workspace}")
        return workspace.expanduser().resolve()

    def list_sessions(
        self,
        include_archived: bool = False,
        *,
        client_id: str | None = None,
        exclude_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """List sessions sorted by updated time descending.

        Args:
            include_archived: If False (default), exclude archived sessions.
            client_id: If provided, filter by ownership:
                - Sessions owned by client_id: included with ownership="owned".
                - Unowned legacy sessions: included with ownership="unowned".
                - Sessions owned by other clients: EXCLUDED.
                - If None (backward compat): all sessions included, no ownership field.
            exclude_empty: If True, omit sessions whose conversation file has
                no real message yet (only a metadata line).  Empty sessions are
                ephemeral until the first message lands, so the desktop sidebar
                must not list them (the UI cannot tell empties apart from the
                list payload alone).
        """
        sessions: list[dict[str, Any]] = []

        # Primary: directory-based sessions
        for path in self.sessions_dir.glob("*/conversation.jsonl"):
            try:
                data = self._read_metadata(path)
                if data is None:
                    continue
                if not include_archived and (path.parent / ".archived").exists():
                    continue
                if exclude_empty and not self._conversation_has_messages(path):
                    continue
                key = data.get("key") or path.parent.name.replace("_", ":", 1)

                # Ownership filtering
                if client_id is not None:
                    owner = data.get("owner_client_id")
                    if owner is None:
                        ownership = "unowned"
                    elif owner == client_id:
                        ownership = "owned"
                    else:
                        continue  # Owned by different client — exclude
                else:
                    ownership = None  # Not set for backward compat

                custom_title = (data.get("metadata") or {}).get("title")
                entry = {
                    "key": key,
                    "title": custom_title or self._extract_title(path) or key,
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "path": str(path),
                    "workspace": (data.get("metadata") or {}).get("workspace"),
                }
                if ownership is not None:
                    entry["ownership"] = ownership
                sessions.append(entry)
            except Exception:
                continue

        # Fallback: old flat .jsonl files not yet migrated
        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                data = self._read_metadata(path)
                if data is None:
                    continue
                if exclude_empty and not self._conversation_has_messages(path):
                    continue
                key = data.get("key") or path.stem.replace("_", ":", 1)

                # Ownership filtering
                if client_id is not None:
                    owner = data.get("owner_client_id")
                    if owner is None:
                        ownership = "unowned"
                    elif owner == client_id:
                        ownership = "owned"
                    else:
                        continue
                else:
                    ownership = None

                custom_title = (data.get("metadata") or {}).get("title")
                entry = {
                    "key": key,
                    "title": custom_title or self._extract_title(path) or key,
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "path": str(path),
                    "workspace": (data.get("metadata") or {}).get("workspace"),
                }
                if ownership is not None:
                    entry["ownership"] = ownership
                sessions.append(entry)
            except Exception:
                continue

        return sorted(sessions, key=lambda item: item.get("updated_at", ""), reverse=True)

    def delete(self, key: str, *, client_id: str | None = None) -> bool:
        """Delete a session from cache and disk.

        When client_id is provided, ownership is verified first.
        Unowned sessions raise REQUIRES_CLAIM.
        Sessions owned by other clients raise UNAUTHORIZED.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        self._cache.pop(key, None)
        self._migrate_flat_to_dir(key)
        session_dir = self.get_session_dir(key)
        if session_dir.exists():
            shutil.rmtree(session_dir)
            return True
        # Fallback: old flat file that was never migrated
        safe_key = safe_filename(key.replace(":", "_"))
        old_flat = self.sessions_dir / f"{safe_key}.jsonl"
        if old_flat.exists():
            old_flat.unlink()
            return True
        return False

    def rename(self, key: str, title: str, *, client_id: str | None = None) -> str:
        """Set a custom display title for a session, persisted in metadata.title.

        Returns the effective title. Empty/whitespace titles are a no-op:
        the existing custom title (or the auto-extracted one) is kept.
        Titles are truncated to 100 chars.

        When client_id is provided, ownership is verified first.
        """
        if client_id is not None:
            self._verify_ownership_for_mutation(key, client_id)
        session = self.get_or_create(key, client_id=client_id)
        cleaned = (title or "").strip()
        if not cleaned:
            return session.metadata.get("title") or (
                self._extract_title(self._get_session_path(key)) or key
            )
        session.metadata["title"] = cleaned[:100]
        # save() skips the write when there are no new messages, so persist the
        # metadata-only change by rewriting the metadata line directly.
        with self._get_session_lock(key):
            self._migrate_flat_to_dir(key)
            path = self._get_session_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._rewrite_metadata_line(path, session)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(
                        json.dumps(self._metadata_line_for_session(session), ensure_ascii=False)
                        + "\n"
                    )
                path.chmod(0o600)
        self._cache[key] = session
        return session.metadata["title"]

    @staticmethod
    def _extract_title(path: Path) -> str:
        """Extract the first user message text (≤ 60 chars) from a conversation file."""
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                obj = json.loads(raw)
                if obj.get("role") == "user" and obj.get("content"):
                    return str(obj["content"])[:60]
        except Exception:
            pass
        return ""

    @staticmethod
    def _conversation_has_messages(path: Path) -> bool:
        """True if a conversation file contains any real message (role) line.

        Stops at the first message — a real session costs ~2 lines of reads;
        an empty session file holds only a metadata line, so the full scan is
        just that one line.  Used by list_sessions(exclude_empty=True).
        """
        try:
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if isinstance(obj, dict) and obj.get("role"):
                        return True
        except Exception:
            return False
        return False

    def _read_metadata(self, path: Path) -> dict | None:
        """Read the metadata line from a conversation.jsonl or flat .jsonl file.

        Falls back to the most recent message timestamp when the metadata
        ``updated_at`` is missing or older than the latest message — saves are
        append-only, so the metadata line is only rewritten on compaction, but
        we still want the sidebar to re-sort the session to the top after
        every new message.
        """
        try:
            metadata: dict | None = None
            latest_msg_ts: datetime | None = None
            with open(path, encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if metadata is None and obj.get("_type") == "metadata":
                        metadata = obj
                        continue
                    msg_ts = obj.get("timestamp")
                    if isinstance(msg_ts, str):
                        ts = _parse_iso_datetime(msg_ts)
                        if ts is None:
                            continue
                        if latest_msg_ts is None or ts > latest_msg_ts:
                            latest_msg_ts = ts
            if metadata is None:
                return None
            if latest_msg_ts is not None:
                meta_ts_raw = metadata.get("updated_at")
                meta_ts: datetime | None = None
                if isinstance(meta_ts_raw, str):
                    meta_ts = _parse_iso_datetime(meta_ts_raw)
                if meta_ts is None or latest_msg_ts > meta_ts:
                    metadata["updated_at"] = latest_msg_ts.isoformat()
            return metadata
        except Exception:
            return None

    @staticmethod
    def _latest_message_timestamp(session: Session) -> datetime | None:
        latest: datetime | None = None
        for msg in session.messages:
            msg_ts = msg.get("timestamp")
            if not isinstance(msg_ts, str):
                continue
            parsed = _parse_iso_datetime(msg_ts)
            if parsed is None:
                continue
            if latest is None or parsed > latest:
                latest = parsed
        return latest

    def _sync_updated_at_from_messages(self, session: Session) -> None:
        latest = self._latest_message_timestamp(session)
        if latest is not None:
            session.updated_at = latest

    def _metadata_line_for_session(self, session: Session) -> dict[str, Any]:
        return {
            "_type": "metadata",
            "key": session.key,
            "owner_client_id": session.metadata.get("owner_client_id"),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "metadata": session.metadata,
            "last_consolidated": session.last_consolidated,
        }

    def _rewrite_metadata_line(self, path: Path, session: Session) -> None:
        metadata_line = self._metadata_line_for_session(session)
        original_lines = path.read_text(encoding="utf-8").splitlines()
        rewritten_lines: list[str] = []
        replaced = False

        for line in original_lines:
            if not replaced:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = None
                if isinstance(obj, dict) and obj.get("_type") == "metadata":
                    rewritten_lines.append(json.dumps(metadata_line, ensure_ascii=False))
                    replaced = True
                    continue
            rewritten_lines.append(line)

        if not replaced:
            rewritten_lines.insert(0, json.dumps(metadata_line, ensure_ascii=False))

        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        tmp_path = Path(tmp_name)
        try:
            os.chmod(tmp_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(rewritten_lines) + "\n")
            tmp_path.replace(path)
        except OSError:
            try:
                os.close(fd)
            except OSError:
                pass
            tmp_path.unlink(missing_ok=True)
            raise

    # ── Ownership ──────────────────────────────────────────────────────

    def _read_owner(self, key: str) -> str | None:
        """Read owner_client_id from the metadata line of a session file.

        Returns None if the session doesn't exist or has no owner_client_id.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        data = self._read_metadata(path)
        if data is None:
            return None
        return data.get("owner_client_id")

    def _read_workspace(self, key: str) -> str | None:
        """Read the workspace from the metadata line of a session file.

        Returns None if the session doesn't exist or has no workspace.
        """
        path = self._get_session_path(key)
        if not path.exists():
            return None
        data = self._read_metadata(path)
        if data is None:
            return None
        return (data.get("metadata") or {}).get("workspace")

    def get_owner(self, key: str) -> str | None:
        """Return the owner_client_id for a session, or None if unowned."""
        return self._read_owner(key)

    def claim_session(self, key: str, client_id: str) -> bool:
        """Explicitly claim an unowned legacy session.

        Returns True if the session was successfully claimed.
        Returns False if the session is already claimed by this client
        (idempotent — no error) or if the session does not exist on disk
        (cannot claim nonexistent sessions).
        Raises OwnershipError if the session is owned by a different client.
        """
        owner = self.get_owner(key)
        if owner is not None and owner != client_id:
            raise OwnershipError(
                f"Session '{key}' is owned by client '{owner}', not '{client_id}'",
                code="UNAUTHORIZED",
            )
        if owner == client_id:
            return False  # Already claimed, idempotent

        # Session is unowned — load it from disk (do NOT create new)
        session = self._load(key)
        if session is None:
            return False  # Cannot claim a nonexistent session

        session.metadata["owner_client_id"] = client_id
        self.save(session)
        self._cache[key] = session
        logger.info(
            "Session {} claimed by client {}", key, client_id,
        )
        return True

    def _verify_ownership_for_mutation(
        self, key: str, client_id: str,
    ) -> None:
        """Verify ownership for destructive operations.

        If the session does not exist on disk at all, the check passes
        (there is nothing to protect). This handles sessions that exist
        only in the AppServer registry and have no disk metadata yet.

        For disk-resident sessions:
        - Unowned sessions are REJECTED (REQUIRES_CLAIM).
        - Sessions owned by other clients are REJECTED (UNAUTHORIZED).

        This is the strict check used by delete/archive/unarchive/
        clear_tracked_files — unowned sessions cannot be mutated
        without an explicit claim first.
        """
        path = self._get_session_path(key)
        if not path.exists():
            # No disk session to protect — mutation is a no-op
            return
        owner = self.get_owner(key)
        if owner is None:
            raise OwnershipError(
                f"Session '{key}' is a legacy session with no owner. "
                "It must be explicitly claimed before modification.",
                code="REQUIRES_CLAIM",
            )
        if owner != client_id:
            raise OwnershipError(
                f"Session '{key}' is owned by client '{owner}', not '{client_id}'",
                code="UNAUTHORIZED",
            )

    def compact_if_needed(self, key: str) -> bool:
        """Compact a session file if thresholds are exceeded."""
        session = self.get_or_create(key)
        path = self._get_session_path(key)
        if not path.exists():
            return False

        by_message_count = len(session.messages) >= self.compact_threshold_messages
        by_file_size = path.stat().st_size >= self.compact_threshold_bytes
        if not by_message_count and not by_file_size:
            return False

        return self.compact(key)

    def compact(self, key: str) -> bool:
        """Compact a session by rewriting with only recent messages."""
        with self._get_session_lock(key):
            path = self._get_session_path(key)
            if not path.exists():
                return False

            session = self.get_or_create(key)
            original_len = len(session.messages)
            if original_len > self.compact_keep_messages:
                drop_count = original_len - self.compact_keep_messages
                session.messages = session.messages[-self.compact_keep_messages :]
                session.last_consolidated = max(0, session.last_consolidated - drop_count)
            session.last_consolidated = min(
                session.last_consolidated, len(session.messages),
            )

            session.saved_count = len(session.messages)
            self._sync_updated_at_from_messages(session)

            with open(path, "w", encoding="utf-8") as f:
                metadata_line = self._metadata_line_for_session(session)
                f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                for msg in session.messages:
                    f.write(json.dumps(msg, ensure_ascii=False) + "\n")

            self._cache[key] = session
            return True

    def compact_all(self) -> int:
        """Compact all existing session files and return compacted count."""
        compacted = 0
        for info in self.list_sessions():
            if self.compact(info["key"]):
                compacted += 1
        return compacted

    def list_recent_workspaces(self, limit: int = 5, *, client_id: str | None = None) -> list[str]:
        """Return distinct workspace paths from recent sessions, newest first.

        Filters out the default workspace path. Used by the frontend workspace picker.
        Scoped to client_id when provided.
        """
        if limit <= 0:
            return []
        default_ws = str(self.workspace.expanduser().resolve())
        sessions = self.list_sessions(client_id=client_id)
        seen: set[str] = set()
        recent: list[str] = []
        for s in sessions:
            ws = s.get("workspace")
            if ws and ws != default_ws and ws not in seen:
                seen.add(ws)
                recent.append(ws)
                if len(recent) >= limit:
                    break
        return recent
