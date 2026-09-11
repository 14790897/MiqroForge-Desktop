"""Phase 2: ContextBuilder includes session work dir in the identity prompt."""
from miqi.agent.context import ContextBuilder


def test_identity_without_session_work_dir(tmp_path):
    """Without session_work_dir, identity must NOT mention 'working directory'."""
    cb = ContextBuilder(workspace=tmp_path, agent_name="TestAgent")
    identity = cb._get_identity()
    assert "working directory (for new files)" not in identity


def test_identity_with_session_work_dir(tmp_path):
    """With session_work_dir, identity must mention the work dir path."""
    work_dir = tmp_path / "sessions" / "s1" / "files"
    work_dir.mkdir(parents=True)
    cb = ContextBuilder(workspace=tmp_path, agent_name="TestAgent", session_work_dir=work_dir)
    identity = cb._get_identity()
    assert "working directory (for new files)" in identity
    assert str(work_dir) in identity


def test_identity_mentions_absolute_path_hint(tmp_path):
    """The hint about absolute paths for project files must be present."""
    work_dir = tmp_path / "sessions" / "s2" / "files"
    work_dir.mkdir(parents=True)
    cb = ContextBuilder(workspace=tmp_path, session_work_dir=work_dir)
    identity = cb._get_identity()
    assert "absolute paths to modify project files" in identity
