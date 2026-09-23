"""Source audit + behavior tests for MiQi-owned path consumers.

Each consumer must resolve paths through the canonical path Interface and
respect ``MIQI_HOME``.  The source audit guards against regressions, but
these behavior tests are the actual acceptance gate.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ALLOWED_DIRECT_HOME_FILES = {
    Path("miqi/paths.py"),
    # External tool discovery in config_cmd intentionally searches the user's
    # normal home and is not MiQi-owned storage.
    Path("miqi/cli/config_cmd.py"),
}


def test_production_code_does_not_construct_dot_miqi_from_path_home():
    """Source audit: no direct Path.home() / '.forge' in production code."""
    violations = []
    for path in Path("miqi").rglob("*.py"):
        if path in ALLOWED_DIRECT_HOME_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if "Path.home() / \".forge\"" in text or "_Path.home() / \".forge\"" in text:
            violations.append(str(path))

    assert violations == []


# ── Behavior tests: each consumer respects MIQI_HOME ──────────────────────────


def test_snapshot_root_respects_miqi_home(monkeypatch, tmp_path):
    """The filesystem snapshot helper writes under MIQI_HOME."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    from miqi.agent.tools.filesystem import _snapshots_dir, _write_snapshot_to

    snap_dir = _snapshots_dir()
    _write_snapshot_to(snap_dir, "dummy-key", "content")

    assert snap_dir.is_relative_to(miqi_home)
    assert snap_dir.name == "snapshots"
    assert any(snap_dir.iterdir())


def test_cli_history_uses_miqi_home(monkeypatch, tmp_path):
    """CLI prompt session creates history under MIQI_HOME."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    from prompt_toolkit.history import FileHistory

    from miqi.cli import commands as cli_commands

    # Reset module globals so _init_prompt_session runs the constructor path.
    original_prompt_session = cli_commands._PROMPT_SESSION
    original_term_attrs = cli_commands._SAVED_TERM_ATTRS
    try:
        cli_commands._PROMPT_SESSION = None
        cli_commands._SAVED_TERM_ATTRS = None

        with patch("miqi.cli.commands.PromptSession") as mock_session:
            cli_commands._init_prompt_session()

        mock_session.assert_called_once()
        call_kwargs = mock_session.call_args.kwargs
        history = call_kwargs["history"]
        assert isinstance(history, FileHistory)
        history_file = Path(str(history.filename))
        assert history_file.is_relative_to(miqi_home.resolve() / "history")
        assert history_file.name == "cli_history"
    finally:
        cli_commands._PROMPT_SESSION = original_prompt_session
        cli_commands._SAVED_TERM_ATTRS = original_term_attrs


def test_initialize_protocol_uses_miqi_home(monkeypatch, tmp_path):
    """Initialize protocol reports MIQI_HOME as data home."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    from miqi.runtime.initialize_protocol import build_initialize_result

    result = build_initialize_result("client-1")

    assert result["miqiHome"] == str(miqi_home.resolve())
    assert result["codexHome"] == str(miqi_home.resolve())


def test_user_plugins_dir_uses_miqi_home(monkeypatch, tmp_path):
    """RuntimeServices passes MIQI_HOME/plugins to PluginManager."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    from miqi.config.schema import Config
    from miqi.paths import get_miqi_home
    from miqi.runtime.services import RuntimeServices

    with patch("miqi.skills.plugin_manager.PluginManager") as mock_pm:
        mock_instance = MagicMock()
        mock_pm.return_value = mock_instance
        RuntimeServices.from_config(
            config=Config(),
            provider=MagicMock(),
            session_id="test-session",
            workspace=tmp_path / "workspace",
        )

    mock_pm.assert_called_once()
    call_kwargs = mock_pm.call_args.kwargs
    assert call_kwargs["user_plugins_dir"] == get_miqi_home() / "plugins"


def test_marketplaces_dir_uses_miqi_home(monkeypatch, tmp_path):
    """Plugin catalog handler defaults marketplaces dir to MIQI_HOME/marketplaces."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    from miqi.runtime.plugin_app_handlers import _catalog

    registry = MagicMock()
    registry.bridge_context = {"plugin_manager": MagicMock()}
    catalog = _catalog(registry)

    assert catalog.marketplaces_dir.is_relative_to(miqi_home)
    assert catalog.marketplaces_dir.name == "marketplaces"


@pytest.mark.asyncio
async def test_diagnostic_config_exists_handler_uses_miqi_home(monkeypatch, tmp_path):
    """Diagnostic handler resolves config_exists through MIQI_HOME."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))
    miqi_home.mkdir(parents=True)
    (miqi_home / "config.json").write_text("{}", encoding="utf-8")

    from miqi.runtime.diagnostic_handlers import python_check_handler

    response = await python_check_handler(
        request_id="req-1",
        params={},
        client_id="client-1",
        session_id=None,
        registry=None,
    )

    assert response["result"]["config_exists"] is True


def test_sandbox_state_uses_miqi_home(monkeypatch, tmp_path):
    """SandboxManager persists state under MIQI_HOME."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    from miqi.sandbox.manager import SandboxManager

    manager = SandboxManager(workspace=tmp_path / "workspace")

    assert manager._state_file.is_relative_to(miqi_home)
    assert manager._state_file.name == "sandbox_state.json"


@pytest.mark.asyncio
async def test_bridge_status_handler_uses_miqi_home(monkeypatch, tmp_path):
    """Bridge status handler reports config existence under MIQI_HOME."""
    miqi_home = tmp_path / "miqi"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))
    miqi_home.mkdir(parents=True)
    (miqi_home / "config.json").write_text("{}", encoding="utf-8")

    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop()
    result = await loop._status_handler("req", {}, "client", None, None)

    assert result["result"]["configured"] is True
    assert result["result"]["status"] == "ok"
