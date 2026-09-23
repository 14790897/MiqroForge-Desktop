from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.skills_app_handlers import register_skills_app_handlers


def write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n# {name}\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_skills_list_reads_cwd_and_extra_roots(tmp_path):
    cwd = tmp_path / "workspace"
    extra = tmp_path / "extra"
    (cwd / "skills").mkdir(parents=True)
    write_skill(cwd / "skills", "workspace-skill")
    write_skill(extra, "extra-skill")

    registry = ClientSessionRegistry()
    state = MagicMock()
    cfg = MagicMock()
    cfg.workspace_path = cwd
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    registry.bridge_context["skills_extra_roots"] = [extra]
    server = AppServer(registry)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "skills/list", {"cwds": [str(cwd)], "forceReload": True}, "client-1", None
    )
    names = {skill["name"] for skill in response["result"]["skills"]}
    assert "workspace-skill" in names
    assert "extra-skill" in names


@pytest.mark.asyncio
async def test_skills_extra_roots_set_is_client_scoped_and_emits_event(tmp_path):
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    events = []

    async def sink(envelope):
        events.append(envelope)

    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()

    server.set_event_sink("client-1", sink)
    register_skills_app_handlers(server)
    response = await server.dispatch(
        "1", "skills/extraRoots/set", {"roots": [str(extra_dir)]}, "client-1", None
    )
    assert response["result"] == {}
    # Client-scoped key is set for the calling client only.
    by_client = registry.bridge_context.setdefault("skills_extra_roots_by_client", {})
    assert "client-1" in by_client
    assert by_client["client-1"] == [extra_dir]
    # Legacy global key is NOT mutated.
    assert "skills_extra_roots" not in registry.bridge_context
    assert any(event["event"] == "skills/changed" for event in events)


@pytest.mark.asyncio
async def test_hooks_list_reads_workspace_hooks(tmp_path):
    cwd = tmp_path / "workspace"
    hooks_dir = cwd / ".forge" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "hooks.json").write_text(
        '{"hooks":[{"name":"pre-tool","event":"pre_tool"}]}',
        encoding="utf-8",
    )
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    register_skills_app_handlers(server)
    response = await server.dispatch(
        "1", "hooks/list", {"cwds": [str(cwd)]}, "client-1", None
    )
    assert response["result"]["hooks"][0]["name"] == "pre-tool"


# ---------------------------------------------------------------------------
# Phase 37 Hardening: client-scoped extra roots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extra_roots_are_client_scoped(tmp_path):
    """Each client has independent extra roots — setting for client-A
    does not affect client-B."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    server.set_event_sink("client-A", None)
    server.set_event_sink("client-B", None)
    register_skills_app_handlers(server)

    extra_a = tmp_path / "extra-a"
    extra_b = tmp_path / "extra-b"
    extra_a.mkdir()
    extra_b.mkdir()

    # Set roots for client-A
    await server.dispatch(
        "1", "skills/extraRoots/set", {"roots": [str(extra_a)]}, "client-A", None
    )
    # Set roots for client-B
    await server.dispatch(
        "2", "skills/extraRoots/set", {"roots": [str(extra_b)]}, "client-B", None
    )

    by_client = registry.bridge_context.get("skills_extra_roots_by_client", {})
    assert by_client.get("client-A") == [extra_a]
    assert by_client.get("client-B") == [extra_b]
    # Client-B's roots are isolated from client-A's.
    assert by_client["client-A"] != by_client["client-B"]


@pytest.mark.asyncio
async def test_extra_roots_legacy_global_is_read_only_fallback(tmp_path):
    """When a legacy 'skills_extra_roots' global value exists and no
    client-scoped value exists, the legacy value is used as a fallback."""
    from miqi.runtime.skills_app_handlers import _get_client_extra_roots

    registry = ClientSessionRegistry()
    registry.bridge_context["skills_extra_roots"] = [tmp_path / "legacy-root"]
    # Client has no scoped key yet — falls back to legacy.
    roots = _get_client_extra_roots(registry, "client-1")
    assert roots == [tmp_path / "legacy-root"]


@pytest.mark.asyncio
async def test_extra_roots_set_does_not_mutate_legacy_global_key(tmp_path):
    """Setting extra roots for a client must NOT write to the legacy
    'skills_extra_roots' key."""
    registry = ClientSessionRegistry()
    server = AppServer(registry)
    server.set_event_sink("client-1", None)
    register_skills_app_handlers(server)

    # Pre-populate legacy global (path is a value object — doesn't need to exist)
    old_legacy = tmp_path / "old-legacy"
    old_legacy.mkdir()
    new_root = tmp_path / "new-root"
    new_root.mkdir()
    registry.bridge_context["skills_extra_roots"] = [old_legacy]

    await server.dispatch(
        "1", "skills/extraRoots/set",
        {"roots": [str(new_root)]}, "client-1", None,
    )

    # Legacy key must remain unchanged.
    assert registry.bridge_context["skills_extra_roots"] == [old_legacy]
    # New writes only in client-scoped storage.
    by_client = registry.bridge_context.get("skills_extra_roots_by_client", {})
    assert "client-1" in by_client


# ---------------------------------------------------------------------------
# Phase 37 Hardening: path validation for cwds and extra roots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_list_rejects_cwd_outside_workspace(tmp_path):
    """A cwd outside the configured workspace must be rejected."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    registry = ClientSessionRegistry()
    state = MagicMock()
    cfg = MagicMock()
    cfg.workspace_path = workspace
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    server = AppServer(registry)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "skills/list",
        {"cwds": [str(outside)]}, "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    assert "outside workspace" in response.get("error", "")


@pytest.mark.asyncio
async def test_hooks_list_rejects_cwd_outside_workspace(tmp_path):
    """A hooks/list cwd outside the configured workspace must be rejected."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    registry = ClientSessionRegistry()
    state = MagicMock()
    cfg = MagicMock()
    cfg.workspace_path = workspace
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    server = AppServer(registry)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "hooks/list",
        {"cwds": [str(outside)]}, "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    assert "outside workspace" in response.get("error", "")


@pytest.mark.asyncio
async def test_extra_roots_set_rejects_root_outside_workspace(tmp_path):
    """An extra root outside the configured workspace must be rejected at set time."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    registry = ClientSessionRegistry()
    state = MagicMock()
    cfg = MagicMock()
    cfg.workspace_path = workspace
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    server = AppServer(registry)
    server.set_event_sink("client-1", None)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "skills/extraRoots/set",
        {"roots": [str(outside)]}, "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    assert "outside workspace" in response.get("error", "")


@pytest.mark.asyncio
async def test_extra_roots_set_rejects_missing_root(tmp_path):
    """A non-existent extra root must be rejected at set time."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    server.set_event_sink("client-1", None)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "skills/extraRoots/set",
        {"roots": [str(tmp_path / "does-not-exist")]}, "client-1", None,
    )
    assert response.get("code") == "INVALID_PARAMS"
    assert "does not exist" in response.get("error", "")


@pytest.mark.asyncio
async def test_skills_list_accepts_workspace_extra_root(tmp_path):
    """An extra root within the workspace is accepted and scanned for skills."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    extra = workspace / "extras"
    extra.mkdir()
    write_skill(extra, "my-extra-skill")

    registry = ClientSessionRegistry()
    state = MagicMock()
    cfg = MagicMock()
    cfg.workspace_path = workspace
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    # Set client-scoped extra roots
    registry.bridge_context.setdefault("skills_extra_roots_by_client", {})["client-1"] = [extra]
    server = AppServer(registry)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "skills/list",
        {"cwds": [str(workspace)], "forceReload": True}, "client-1", None,
    )
    names = {skill["name"] for skill in response["result"]["skills"]}
    assert "my-extra-skill" in names


@pytest.mark.asyncio
async def test_hooks_list_invalid_json_is_sanitized(tmp_path):
    """Invalid JSON in a hooks file must be handled gracefully — no raw paths
    in the response, no crash."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    cwd = tmp_path / "workspace"
    hooks_dir = cwd / ".forge" / "hooks"
    hooks_dir.mkdir(parents=True)
    hooks_file = hooks_dir / "hooks.json"
    hooks_file.write_text("not valid json {{", encoding="utf-8")

    registry = ClientSessionRegistry()
    server = AppServer(registry)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "hooks/list",
        {"cwds": [str(cwd)]}, "client-1", None,
    )
    # Must return hooks: [] without leaking file paths in error.
    assert response["result"]["hooks"] == []
    assert hooks_file.name not in response.get("error", "")


@pytest.mark.asyncio
async def test_skills_list_no_cwd_still_scans_client_extra_roots_when_workspace_exists(tmp_path):
    """When no cwds are provided but client-scoped extra roots exist,
    the skills list still includes skills from those extra roots."""
    from miqi.runtime.skills_app_handlers import register_skills_app_handlers

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    extra = workspace / "extras"
    extra.mkdir()
    write_skill(extra, "extra-only-skill")

    registry = ClientSessionRegistry()
    state = MagicMock()
    cfg = MagicMock()
    cfg.workspace_path = workspace
    state.load_config.return_value = cfg
    registry.bridge_context["state"] = state
    registry.bridge_context.setdefault("skills_extra_roots_by_client", {})["client-1"] = [extra]
    server = AppServer(registry)
    register_skills_app_handlers(server)

    response = await server.dispatch(
        "1", "skills/list",
        {}, "client-1", None,
    )
    names = {skill["name"] for skill in response["result"]["skills"]}
    assert "extra-only-skill" in names
