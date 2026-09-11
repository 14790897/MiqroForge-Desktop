"""Tests for channel handlers — Phase 35.2 / hardening.

Validates channels.list and channels.update migrated from bridge
legacy to AppServer async handlers.
"""

import tempfile

import pytest

from miqi.runtime.app_server import ClientSessionRegistry


def _make_config_with_workspace():
    from miqi.config.schema import Config
    config = Config()
    config.agents.defaults.workspace = tempfile.mkdtemp()
    return config


# ── channels.list ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_channels_list_returns_data(registry_with_state):
    """channels.list should return channels data dict."""
    from miqi.runtime.channel_handlers import channels_list_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config

    result = await channels_list_handler("req-1", {}, "client-1", None, registry)

    assert "channels" in result["result"]
    channels = result["result"]["channels"]
    assert isinstance(channels, dict)


@pytest.mark.asyncio
async def test_channels_list_secrets_redacted(registry_with_state):
    """channels.list should redact secret fields (token, api_key etc.)."""
    from miqi.runtime.channel_handlers import channels_list_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config

    result = await channels_list_handler("req-1", {}, "client-1", None, registry)

    channels = result["result"]["channels"]
    # Flatten all string values and check no secrets leak
    def _check(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _check(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _check(v, f"{path}[{i}]")
        elif isinstance(obj, str) and obj:
            # If the key contains "token", "secret", "password", "api_key", "apikey"
            # the value should be redacted (contain "****" or be short)
            key_lower = path.lower()
            has_secret_key = any(
                s in key_lower for s in ("token", "secret", "password", "api_key", "apikey")
            )
            if has_secret_key and len(obj) > 10:
                # Redacted values are short (e.g., "sk-a…b123" or "****")
                # This assertion means: if a secret field has a long value, it wasn't redacted
                assert "****" in obj or "…" in obj or len(obj) <= 8, (
                    f"Field {path} may leak secret: {obj[:40]}"
                )

    _check(channels)


# ── channels.update ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_channels_update_rejects_non_dict():
    """channels.update should reject non-dict channels param."""
    from miqi.runtime.app_server import AppServerError
    from miqi.runtime.channel_handlers import channels_update_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError, match="channels must be a dict"):
        await channels_update_handler(
            "req-1", {"channels": "not-a-dict"}, "client-1", None, registry,
        )


@pytest.mark.asyncio
async def test_channels_update_saves_and_merges(registry_with_state, monkeypatch):
    """channels.update must save successfully (#789 regression).

    The handler used to import ``_deep_merge`` from ``miqi.bridge.server``,
    which does not define it — every save raised ImportError and the
    frontend showed INTERNAL.  The helper now comes from
    ``miqi.runtime.config_app_handlers``.
    """
    import miqi.config.loader as loader
    from miqi.runtime.channel_handlers import channels_update_handler

    registry, mock_state = registry_with_state
    config = _make_config_with_workspace()
    mock_state.load_config.return_value = config
    saved: list = []
    monkeypatch.setattr(loader, "save_config", lambda cfg, path=None: saved.append(cfg))

    result = await channels_update_handler(
        "req-1", {"channels": {"send_progress": False}}, "client-1", None, registry,
    )

    assert result["result"]["saved"] is True
    assert saved and saved[0].channels.send_progress is False
    assert mock_state.config is config
