"""Phase 3: ensure_sessions_gitignored writes sessions/ to .gitignore for git workspaces."""
from pathlib import Path

from miqi.utils.helpers import ensure_sessions_gitignored


def _make_git_repo(path: Path) -> Path:
    """Create a minimal fake git repo (just a .git directory)."""
    (path / ".git").mkdir()
    return path


def test_no_git_no_gitignore_created(tmp_path):
    """Non-git workspace: .gitignore must NOT be created."""
    ensure_sessions_gitignored(tmp_path)
    assert not (tmp_path / ".gitignore").exists()


def test_git_repo_creates_gitignore(tmp_path):
    """Git repo without .gitignore: creates it with sessions/ entry."""
    _make_git_repo(tmp_path)
    ensure_sessions_gitignored(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert "sessions/" in content


def test_git_repo_appends_to_existing_gitignore(tmp_path):
    """Git repo with .gitignore missing sessions/: appends the entry."""
    _make_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
    ensure_sessions_gitignored(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert "sessions/" in content
    assert "*.pyc" in content  # existing content preserved


def test_idempotent_when_entry_already_present(tmp_path):
    """Git repo with sessions/ already in .gitignore: file unchanged."""
    _make_git_repo(tmp_path)
    original = "*.pyc\nsessions/\n"
    (tmp_path / ".gitignore").write_text(original)
    ensure_sessions_gitignored(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == original


def test_no_duplicate_on_repeated_calls(tmp_path):
    """Calling twice must not duplicate the entry."""
    _make_git_repo(tmp_path)
    ensure_sessions_gitignored(tmp_path)
    ensure_sessions_gitignored(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert content.count("sessions/") == 1
