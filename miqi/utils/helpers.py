"""Utility functions for MiQi runtime."""

from datetime import datetime
from pathlib import Path

from miqi.paths import (
    _miqi_home_is_configured,
    get_default_workspace_path,
    get_legacy_data_dir,
    get_miqi_home,
)

# Kept as public aliases for callers that still reference these constants.
DEFAULT_DATA_DIR = ".forge"
LEGACY_DATA_DIR = ".assistant"


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """Get runtime data directory.

    Resolution order:
    1. If ``MIQI_HOME`` is explicitly set, use it.
    2. If the legacy ``~/.assistant`` directory exists but ``~/.forge`` does
       not, return the legacy directory so existing data remains accessible.
    3. Otherwise return ``~/.forge`` (the default for new installs).

    This preserves backward compatibility for users who have not migrated
    their data to the new ``~/.forge`` location, while keeping the default
    data root at ``~/.forge`` for fresh installations.
    """
    if _miqi_home_is_configured():
        return ensure_dir(get_miqi_home())

    default_home = get_miqi_home()
    legacy_home = get_legacy_data_dir()
    if legacy_home.exists() and not default_home.exists():
        return ensure_dir(legacy_home)

    return ensure_dir(default_home)


def get_workspace_path(workspace: str | None = None) -> Path:
    """
    Get the workspace path.

    Args:
        workspace: Optional workspace path. Defaults to the account-scoped
            workspace root (see :func:`miqi.paths.get_default_workspace_path`).

    Returns:
        Expanded and ensured workspace path.
    """
    if workspace:
        path = Path(workspace).expanduser()
    else:
        path = get_default_workspace_path()
    return ensure_dir(path)


def get_sessions_path() -> Path:
    """Get the sessions storage directory."""
    return ensure_dir(get_data_path() / "sessions")


def get_skills_path(workspace: Path | None = None) -> Path:
    """Get the skills directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "skills")


def timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now().isoformat()


def today_date() -> str:
    """Get current date in YYYY-MM-DD format."""
    return datetime.now().strftime("%Y-%m-%d")


def truncate_string(s: str, max_len: int = 100, suffix: str = "...") -> str:
    """Truncate a string to max length, adding suffix if truncated."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix


def safe_filename(name: str) -> str:
    """Convert a string to a safe filename."""
    # Replace unsafe characters
    unsafe = '<>:"/\\|?*'
    for char in unsafe:
        name = name.replace(char, "_")
    return name.strip()


def ensure_sessions_gitignored(workspace: Path) -> None:
    """Append 'sessions/' to workspace/.gitignore if workspace is a git repo
    and the entry is not already present. Operation is idempotent."""
    if not (workspace / ".git").exists():
        return
    gitignore = workspace / ".gitignore"
    entry = "sessions/"
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if entry in lines:
            return
        text = gitignore.read_text(encoding="utf-8")
        gitignore.write_text(text.rstrip("\n") + f"\n{entry}\n", encoding="utf-8")
    else:
        gitignore.write_text(f"{entry}\n", encoding="utf-8")


def parse_session_key(key: str) -> tuple[str, str]:
    """
    Parse a session key into channel and chat_id.

    Args:
        key: Session key in format "channel:chat_id"

    Returns:
        Tuple of (channel, chat_id)
    """
    parts = key.split(":", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid session key: {key}")
    return parts[0], parts[1]
