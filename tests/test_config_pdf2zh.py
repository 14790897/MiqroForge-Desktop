"""Tests for ``miqi config pdf2zh`` and ``miqi config sync-llm``.

These commands are the only path that wires LLM-backed MCP servers (currently
just pdf2zh) to the miqi default model. They must:

1. Never silently drift the model away from the miqi default.
2. Refuse to run interactively under ``--no-prompt``.
3. Re-sync existing LLM-backed MCP entries when the default model changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from miqi.cli.commands import app
from miqi.cli.config_cmd import _looks_like_small_model
from miqi.config.schema import Config, MCPServerConfig

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Yield a temp config.json wired into miqi.config.loader.

    Patches both ``miqi.config.loader.get_config_path`` and the re-exports used
    by ``config_cmd`` (which imports them lazily inside each command).
    """
    cfg_path = tmp_path / "config.json"

    # Seed a config with an OpenRouter provider + default model
    cfg = Config()
    cfg.providers.openrouter.api_key = "sk-or-test"
    cfg.providers.openrouter.api_base = "https://openrouter.ai/api/v1"
    cfg.agents.defaults.model = "openrouter/anthropic/claude-opus-4-5"
    cfg_path.write_text(json.dumps(cfg.model_dump(by_alias=True)))

    with patch("miqi.config.loader.get_config_path", return_value=cfg_path):
        yield cfg_path


def _reload(path: Path) -> Config:
    # Windows 默认编码（cp1252）读 UTF-8 配置会崩（默认 MCP 条目含中文描述）
    return Config.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# _looks_like_small_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        "Qwen/Qwen2.5-7B-Instruct",
        "qwen2.5-7b",
        "llama-3.1-8b",
        "mistral-7B",
        "gemma-2b",
        "phi-3.8b",
    ],
)
def test_small_model_detected(model: str) -> None:
    assert _looks_like_small_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-5",
        "gpt-4o",
        "deepseek-v3",
        "Qwen2.5-72B-Instruct",
        "llama-3.1-70b",
        None,
        "",
    ],
)
def test_large_or_unknown_model_not_flagged(model: str | None) -> None:
    assert _looks_like_small_model(model) is False


# ---------------------------------------------------------------------------
# miqi config pdf2zh --no-prompt
# ---------------------------------------------------------------------------


def test_pdf2zh_no_prompt_uses_defaults(config_file: Path) -> None:
    """With --no-prompt, the command should fully populate from miqi defaults."""
    result = runner.invoke(
        app,
        [
            "config", "pdf2zh",
            "--mcp-python", "/usr/bin/python3",
            "--no-prompt",
        ],
    )
    assert result.exit_code == 0, result.output

    cfg = _reload(config_file)
    pdf = cfg.tools.mcp_servers["pdf2zh"]
    # Default model has provider prefix stripped
    assert pdf.env["OPENAI_MODEL"] == "anthropic/claude-opus-4-5"
    assert pdf.env["OPENAI_API_KEY"] == "sk-or-test"
    assert pdf.env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert pdf.command == "/usr/bin/python3"
    assert pdf.tool_timeout == 3600


def test_pdf2zh_no_prompt_fails_without_provider_key(tmp_path: Path) -> None:
    """If the matched provider has no api_key, --no-prompt must exit non-zero."""
    cfg_path = tmp_path / "config.json"
    cfg = Config()
    # Default model with no configured provider → no api_key
    cfg.agents.defaults.model = "anthropic/claude-opus-4-5"
    cfg_path.write_text(json.dumps(cfg.model_dump(by_alias=True)))

    with patch("miqi.config.loader.get_config_path", return_value=cfg_path):
        result = runner.invoke(
            app,
            [
                "config", "pdf2zh",
                "--mcp-python", "/usr/bin/python3",
                "--no-prompt",
            ],
        )
    assert result.exit_code != 0
    assert "API key" in result.output or "api key" in result.output.lower()


def test_pdf2zh_explicit_model_overrides_default(config_file: Path) -> None:
    """Passing -m still works (e.g. for ad-hoc testing)."""
    result = runner.invoke(
        app,
        [
            "config", "pdf2zh",
            "--mcp-python", "/usr/bin/python3",
            "--model", "gpt-4o-mini",
            "--no-prompt",
        ],
    )
    assert result.exit_code == 0, result.output
    cfg = _reload(config_file)
    assert cfg.tools.mcp_servers["pdf2zh"].env["OPENAI_MODEL"] == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# miqi config sync-llm
# ---------------------------------------------------------------------------


def test_sync_llm_realigns_existing_pdf2zh(config_file: Path) -> None:
    """sync-llm should rewrite an out-of-date pdf2zh entry to the current default."""
    # Pre-seed pdf2zh pointing at a stale model + provider
    cfg = _reload(config_file)
    cfg.tools.mcp_servers["pdf2zh"] = MCPServerConfig(
        command="/usr/bin/python3",
        args=["-m", "pdf2zh.mcp_server"],
        env={
            "OPENAI_API_KEY": "old-key",
            "OPENAI_BASE_URL": "https://stale.example.com/v1",
            "OPENAI_MODEL": "qwen2.5-7b-instruct",
        },
        tool_timeout=3600,
    )
    config_file.write_text(json.dumps(cfg.model_dump(by_alias=True)))

    result = runner.invoke(app, ["config", "sync-llm"])
    assert result.exit_code == 0, result.output

    cfg = _reload(config_file)
    pdf = cfg.tools.mcp_servers["pdf2zh"]
    assert pdf.env["OPENAI_MODEL"] == "anthropic/claude-opus-4-5"
    assert pdf.env["OPENAI_API_KEY"] == "sk-or-test"
    assert pdf.env["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"


def test_sync_llm_skips_non_llm_servers(config_file: Path) -> None:
    """An MCP server without OPENAI_* env vars must be left untouched."""
    cfg = _reload(config_file)
    cfg.tools.mcp_servers["zeopp"] = MCPServerConfig(
        command="/usr/bin/python3",
        args=["-m", "zeopp.mcp_server"],
        env={"ZEOPP_DATA_DIR": "/tmp/data"},
    )
    config_file.write_text(json.dumps(cfg.model_dump(by_alias=True)))

    result = runner.invoke(app, ["config", "sync-llm"])
    assert result.exit_code == 0, result.output

    cfg = _reload(config_file)
    # zeopp untouched
    assert cfg.tools.mcp_servers["zeopp"].env == {"ZEOPP_DATA_DIR": "/tmp/data"}


def test_sync_llm_fails_without_default_provider(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg = Config()
    cfg.agents.defaults.model = "anthropic/claude-opus-4-5"
    cfg.tools.mcp_servers["pdf2zh"] = MCPServerConfig(
        command="/usr/bin/python3",
        env={
            "OPENAI_API_KEY": "old",
            "OPENAI_BASE_URL": "https://x/v1",
            "OPENAI_MODEL": "old-model",
        },
    )
    cfg_path.write_text(json.dumps(cfg.model_dump(by_alias=True)))

    with patch("miqi.config.loader.get_config_path", return_value=cfg_path):
        result = runner.invoke(app, ["config", "sync-llm"])
    assert result.exit_code != 0
    assert "API key" in result.output or "api key" in result.output.lower()


def test_sync_llm_no_op_when_no_llm_servers(config_file: Path) -> None:
    """If no MCP server has OPENAI_* env vars, sync-llm exits 0 with a hint."""
    result = runner.invoke(app, ["config", "sync-llm"])
    assert result.exit_code == 0, result.output
    assert "No LLM-backed MCP servers" in result.output or "nothing to sync" in result.output
