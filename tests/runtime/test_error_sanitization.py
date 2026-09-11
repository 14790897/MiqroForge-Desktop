"""Error sanitization tests — Phase 35 hardening.

Validates that AppServerError messages from control-plane handlers
do NOT leak raw str(exc), paths, tokens, or internal details to the frontend.
All error messages should be fixed, safe strings.
"""

from __future__ import annotations

import re

import pytest

from miqi.runtime.app_server import AppServerError, ClientSessionRegistry

# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_error_message(exc: AppServerError) -> str:
    """Extract the sanitized error message attribute."""
    return exc.message


async def _invoke_and_catch(handler, params: dict) -> AppServerError:
    """Invoke a handler and return the AppServerError if raised."""
    registry = ClientSessionRegistry()
    try:
        await handler("req-1", params, "client-1", None, registry)
        pytest.fail("Expected AppServerError but no exception was raised")
    except AppServerError as exc:
        return exc


# ── Sanitization assertion helpers ───────────────────────────────────────────

# Patterns that should NEVER appear in error messages returned to frontend
_FORBIDDEN_PATTERNS = [
    (r"sk-[a-zA-Z0-9]{20,}", "API key (sk-...)"),
    (r"AIza[a-zA-Z0-9_-]{20,}", "Google API key"),
    (r"[a-zA-Z]:[\\/]", "Windows path"),
    (r"/[a-z]+/[a-z]+/[a-z]+", "Unix path (3+ levels)"),
    (r"Traceback\s", "Python traceback"),
    (r"File\s+\".*\.py\"", "Python file reference"),
    (r"ConnectionError|ConnectTimeout|HTTPError", "Network error type leak"),
    (r"401\s|403\s|500\s", "HTTP status code"),
    (r"0x[0-9a-fA-F]{4,}", "Memory address"),
    (r"\.git", ".git directory leak"),
    (r"\\\\", "Escaped backslash path"),
]

# Tokens / secrets that should never appear
_FORBIDDEN_TOKENS = [
    "sk-ant-api03-",
    "sk-proj-",
    "AIzaSy",
    "hf_",
]


def assert_error_is_sanitized(exc: AppServerError, handler_name: str = "") -> None:
    """Assert that an AppServerError message contains no sensitive data."""
    msg = exc.message
    prefix = f"[{handler_name}] " if handler_name else ""

    for pattern, desc in _FORBIDDEN_PATTERNS:
        if re.search(pattern, msg):
            pytest.fail(
                f"{prefix}Error message leaks {desc}: {msg!r}"
            )

    for token in _FORBIDDEN_TOKENS:
        if token.lower() in msg.lower():
            pytest.fail(
                f"{prefix}Error message contains forbidden token prefix: {msg!r}"
            )

    # Assert the message does not contain "from None" or "from exc" patterns
    assert "from None" not in msg, (
        f"{prefix}Error message contains chaining info: {msg!r}"
    )
    assert "from exc" not in msg, (
        f"{prefix}Error message contains chaining info: {msg!r}"
    )

    # Message should not look like raw str(exc) — should be a fixed,
    # human-readable sentence (at least 3 words for most messages)
    words = msg.split()
    assert len(words) >= 2, (
        f"{prefix}Error message too short (likely raw str(exc)): {msg!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Provider test error sanitization
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_providers_test_error_is_sanitized():
    """providers.test error must not leak internal exception details."""
    from miqi.runtime.provider_handlers import providers_test_handler

    registry = ClientSessionRegistry()
    # No API key + no bridge state => will fail somewhere before the API call
    # We test with a known-unknown provider name to trigger the error path
    try:
        await providers_test_handler(
            "req-1",
            {"provider_name": "nonexistent-provider-xyz"},
            "client-1", None, registry,
        )
    except AppServerError as exc:
        # Should be NOT_FOUND for unknown provider
        assert exc.code == "NOT_FOUND"
        assert_error_is_sanitized(exc, "providers_test")
        # Message must NOT contain the fake API key (but provider name is fine)


@pytest.mark.asyncio
async def test_providers_test_rejects_custom_api_key():
    """后端收口（#835）：providers.test 拒绝非空自配 api_key。"""
    from miqi.runtime.provider_handlers import providers_test_handler

    registry = ClientSessionRegistry()
    with pytest.raises(AppServerError) as exc:
        await providers_test_handler(
            "req-1",
            {"provider_name": "deepseek", "api_key": "sk-fake-key-for-test"},
            "client-1", None, registry,
        )
    assert exc.value.code == "NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_providers_test_no_api_key_message_sanitized():
    """providers.test no-API-key message is safe."""
    from miqi.runtime.provider_handlers import providers_test_handler

    registry = ClientSessionRegistry()
    try:
        await providers_test_handler(
            "req-1",
            {"provider_name": "openai", "api_key": ""},
            "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "providers_test")


# ═══════════════════════════════════════════════════════════════════════════════
# Plugin handler error sanitization
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_plugins_install_missing_url_is_sanitized():
    """plugins.install error when missing url is safe."""
    from miqi.runtime.plugin_handlers import plugins_install_handler

    registry = ClientSessionRegistry()
    try:
        await plugins_install_handler(
            "req-1", {"name": "test", "url": ""}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "plugins_install")
        # Should say "url is required" or similar, NOT raw exception text


@pytest.mark.asyncio
async def test_plugins_install_no_manager_is_sanitized():
    """plugins.install when no plugin manager is safe."""
    from miqi.runtime.plugin_handlers import plugins_install_handler

    registry = ClientSessionRegistry()
    try:
        await plugins_install_handler(
            "req-1", {"name": "test", "url": "https://github.com/test/repo"},
            "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "plugins_install")
        # Should NOT contain the URL in the error
        assert "https://" not in exc.message


@pytest.mark.asyncio
async def test_plugins_uninstall_no_manager_is_sanitized():
    """plugins.uninstall when no plugin manager is safe."""
    from miqi.runtime.plugin_handlers import plugins_uninstall_handler

    registry = ClientSessionRegistry()
    try:
        await plugins_uninstall_handler(
            "req-1", {"name": "test"}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "plugins_uninstall")


@pytest.mark.asyncio
async def test_plugins_toggle_no_manager_is_sanitized():
    """plugins.toggle when no plugin manager is safe."""
    from miqi.runtime.plugin_handlers import plugins_toggle_handler

    registry = ClientSessionRegistry()
    try:
        await plugins_toggle_handler(
            "req-1", {"name": "test", "enabled": True}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "plugins_toggle")


# ═══════════════════════════════════════════════════════════════════════════════
# MCP upsert error sanitization
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mcp_upsert_invalid_config_is_sanitized():
    """mcp.upsert error for invalid config is safe."""
    from miqi.runtime.mcp_handlers import mcp_upsert_handler

    registry = ClientSessionRegistry()
    # Missing name triggers early validation, but let's test with bad name
    try:
        await mcp_upsert_handler(
            "req-1", {}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "mcp_upsert")


# ═══════════════════════════════════════════════════════════════════════════════
# Experience search error sanitization
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_experience_search_no_state_is_sanitized():
    """experience.search error when no bridge state is safe."""
    from miqi.runtime.experience_handlers import experience_search_handler

    registry = ClientSessionRegistry()
    try:
        await experience_search_handler(
            "req-1", {"query": "test"}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "experience_search")
        # Must not contain query in error
        assert "test" not in exc.message.lower() or "test" == exc.message


# ═══════════════════════════════════════════════════════════════════════════════
# Cron error sanitization
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cron_create_invalid_schedule_is_sanitized():
    """cron.create error for invalid schedule kind is safe."""
    from miqi.runtime.cron_handlers import cron_create_handler

    registry = ClientSessionRegistry()
    try:
        await cron_create_handler(
            "req-1",
            {"name": "test-job", "scheduleKind": "invalid_kind_xyz"},
            "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "cron_create")


# ═══════════════════════════════════════════════════════════════════════════════
# Sanity check — known-safe handlers pass audit
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_skills_create_invalid_name_is_sanitized():
    """skills.create invalid name error is safe."""
    from miqi.runtime.skill_handlers import skills_create_handler

    registry = ClientSessionRegistry()
    try:
        await skills_create_handler(
            "req-1", {"name": "../etc/passwd"}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "skills_create")


@pytest.mark.asyncio
async def test_skills_upload_invalid_name_is_sanitized():
    """skills.upload invalid name error is safe."""
    from miqi.runtime.skill_handlers import skills_upload_handler

    registry = ClientSessionRegistry()
    try:
        await skills_upload_handler(
            "req-1", {"name": "../etc/passwd", "content": "x"},
            "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "skills_upload")


@pytest.mark.asyncio
async def test_skills_delete_invalid_name_is_sanitized():
    """skills.delete invalid name error is safe."""
    from miqi.runtime.skill_handlers import skills_delete_handler

    registry = ClientSessionRegistry()
    try:
        await skills_delete_handler(
            "req-1", {"name": "../etc/passwd"}, "client-1", None, registry,
        )
    except AppServerError as exc:
        assert_error_is_sanitized(exc, "skills_delete")
