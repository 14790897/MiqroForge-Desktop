import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from miqi.cli.commands import app
from miqi.config.schema import Config

runner = CliRunner()


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with patch("miqi.config.loader.get_config_path") as mock_cp, \
         patch("miqi.config.loader.save_config") as mock_sc, \
         patch("miqi.config.loader.load_config"), \
         patch("miqi.utils.helpers.get_workspace_path") as mock_ws:

        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_sc.side_effect = lambda config: config_file.write_text("{}")

        yield config_file, workspace_dir

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "miqi is ready" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir = mock_paths
    workspace_dir.mkdir(parents=True)
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


def test_status_with_existing_config_no_crash(monkeypatch, tmp_path):
    """Status should not crash when config file exists."""
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    config = Config()
    config.providers.openrouter.api_key = "sk-or-v1-test"
    config.agents.defaults.model = "anthropic/claude-opus-4-5"

    monkeypatch.setattr("miqi.config.loader.get_config_path", lambda: config_path)
    monkeypatch.setattr("miqi.config.loader.load_config", lambda: config)

    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "MiQroForge Status" in result.stdout
    assert "OpenRouter" in result.stdout


def test_config_heartbeat_defaults_and_alias_parsing():
    default_config = Config()
    assert default_config.heartbeat.enabled is True
    assert default_config.heartbeat.interval_seconds == 1800

    parsed = Config.model_validate(
        {"heartbeat": {"enabled": False, "intervalSeconds": 90}}
    )
    assert parsed.heartbeat.enabled is False
    assert parsed.heartbeat.interval_seconds == 90


def test_agent_command_passes_runtime_configs(monkeypatch, tmp_path):
    config = Config()
    config.providers.openrouter.api_key = "sk-or-v1-test"
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)

    class FakeCronService:
        def __init__(self, _store_path, **kwargs):
            self.on_job = None

    # One-shot now goes through RuntimeSession; mock it to verify config flow
    runtime_captured: dict = {}

    async def _fake_runtime(config, provider, message, session_id):
        runtime_captured["config"] = config
        runtime_captured["provider"] = provider
        runtime_captured["message"] = message
        runtime_captured["session_id"] = session_id
        return "ok"

    monkeypatch.setattr("miqi.config.loader.load_config", lambda: config)
    monkeypatch.setattr("miqi.config.loader.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("miqi.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("miqi.cron.service.CronService", FakeCronService)
    monkeypatch.setattr(
        "miqi.cli.agent_cmd._run_agent_once_via_runtime",
        _fake_runtime,
    )

    result = runner.invoke(app, ["agent", "-m", "hello"])

    assert result.exit_code == 0
    # Runtime received the correct config
    assert runtime_captured["config"] is config
    assert runtime_captured["message"] == "hello"
    assert runtime_captured["session_id"] == "cli:default"


def test_cron_run_passes_runtime_configs(monkeypatch, tmp_path):
    """Phase 14: cron_run uses RuntimeSession + RuntimeClient, not AgentLoop."""
    config = Config()
    config.providers.openrouter.api_key = "sk-or-v1-test"
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)

    runtime_calls: list = []

    class FakeRuntime:
        async def start(self):
            pass
        async def stop(self):
            pass
        async def submit(self, _submission):
            pass
        async def next_event(self, timeout=None):
            from miqi.protocol.events import AgentMessageEvent
            return AgentMessageEvent(turn_id="t1", content="ok", finish_reason="stop")

    class FakeCronService:
        def __init__(self, _store_path):
            self.on_job = None

        async def run_job(self, _job_id, force=False):
            if self.on_job is not None:
                # Simulate a cron job callback
                await self.on_job(_FakeCronJob())
            return bool(force)

    class _FakeCronJob:
        id = "demo"
        payload = type("Payload", (), {"message": "test", "channel": "cli", "to": "direct", "deliver": False})()

    def _fake_create(*, config, provider, session_id, workspace, **kwargs):
        runtime_calls.append({
            "config": config,
            "session_id": session_id,
            "workspace": workspace,
        })
        return FakeRuntime()

    monkeypatch.setattr("miqi.config.loader.load_config", lambda: config)
    monkeypatch.setattr("miqi.config.loader.get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("miqi.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("miqi.cron.service.CronService", FakeCronService)
    monkeypatch.setattr("miqi.runtime.session.RuntimeSession.create", _fake_create)

    result = runner.invoke(app, ["cron", "run", "demo", "--force"])

    assert result.exit_code == 0
    assert "Job executed" in result.stdout
    assert len(runtime_calls) >= 1
    assert runtime_calls[0]["config"] is config
    assert runtime_calls[0]["session_id"] == "cron:demo"


def test_interactive_onboard_configures_papers_and_skips_feishu(monkeypatch):
    from miqi.cli.commands import _interactive_onboard_setup

    config = Config()

    prompt_values = iter(
        [
            1,  # provider number: OpenRouter
            "sk-or-v1-test",  # OpenRouter key
            "",  # model name => default
            1,  # search mode: DuckDuckGo/ddgs
            1,  # fetch mode: built-in
            1,  # papers provider: hybrid
            "",  # semantic scholar key optional
            20,  # papers timeout
            8,  # papers default limit
            20,  # papers max limit
            "",  # assistant name => default
            1,  # soul preset
        ]
    )
    confirm_values = iter(
        [
            False,  # custom API base URL
        ]
    )

    monkeypatch.setattr("miqi.cli.commands.typer.prompt", lambda *a, **k: next(prompt_values))
    monkeypatch.setattr("miqi.cli.commands.typer.confirm", lambda *a, **k: next(confirm_values))

    agent_name, soul = _interactive_onboard_setup(config)

    assert agent_name == "miqi"
    assert soul == "balanced"
    assert config.tools.web.search.provider == "auto"  # #561: mode 1 = auto 回落链
    assert config.tools.web.search.api_key == ""
    assert config.tools.papers.provider == "hybrid"
    assert config.tools.papers.timeout_seconds == 20
    assert config.tools.papers.default_limit == 8
    assert config.tools.papers.max_limit == 20
