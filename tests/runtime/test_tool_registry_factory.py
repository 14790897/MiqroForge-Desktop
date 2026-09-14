"""Tests for runtime tool registry factory (Phase 22)."""

import os
from pathlib import Path

import pytest


def test_runtime_tool_registry_factory_registers_core_tools(fake_config, tmp_path):
    """The factory registers exec, filesystem, spawn, plan, and office tools."""
    from unittest.mock import MagicMock

    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    # Create minimal PlanTracker mock so plan tools register
    plan_tracker = MagicMock()

    registry = create_runtime_tool_registry(
        config=fake_config,
        workspace=tmp_path,
        approval_callback=None,
        sandbox_manager=None,
        plan_tracker=plan_tracker,
    )

    names = set(registry.tool_names)

    # Core tools always registered
    assert "exec" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "list_dir" in names
    assert "edit_file" in names

    # Web tools
    assert "web_search" in names
    assert "web_fetch" in names

    # Paper tools
    assert "paper_search" in names
    assert "paper_get" in names
    assert "paper_download" in names

    # Skill manage
    assert "skill_manage" in names

    # Office document tools
    assert "create_docx" in names
    assert "create_pptx" in names
    assert "create_xlsx" in names
    assert "edit_docx" in names
    assert "append_xlsx" in names
    assert "docx_read" in names
    assert "docx_write" in names
    assert "pptx_read" in names
    assert "pptx_write" in names
    assert "xlsx_read" in names
    assert "xlsx_write" in names

    # Plan tools (require plan_tracker)
    assert "plan_create" in names
    assert "plan_update" in names


def test_tool_registry_factory_spawn_always_registered(fake_config, tmp_path):
    """Spawn tool is always registered — Phase 13 removed the legacy
    SubagentManager dependency; the tool executes via AgentControl, which
    RuntimeServices wires in after the registry is built.  Gating it on
    subagent_manager previously advertised "spawn" to the main agent while
    leaving no implementation in the registry (issue #246)."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config,
        workspace=tmp_path,
    )

    names = set(registry.tool_names)
    assert "spawn" in names


def test_tool_registry_factory_optional_tools(fake_config, tmp_path):
    """Optional tools are registered when their dependencies are provided."""
    from unittest.mock import MagicMock

    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config,
        workspace=tmp_path,
        memory_store=MagicMock(),
        trace_store=MagicMock(),
        session_manager=MagicMock(),
        bus=MagicMock(),
        subagent_manager=MagicMock(),
        cron_service=MagicMock(),
        plan_tracker=MagicMock(),
    )

    names = set(registry.tool_names)

    assert "memory" in names
    assert "task_begin" in names
    assert "task_end" in names
    assert "trace_search" in names
    assert "session_search" in names
    assert "message" in names
    assert "spawn" in names
    assert "cron" in names
    assert "plan_create" in names
    assert "plan_update" in names


def test_tool_registry_factory_registration_order_is_stable(fake_config, tmp_path):
    """Tool registration order must be deterministic for model tool specs."""
    from unittest.mock import MagicMock

    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    plan_tracker = MagicMock()
    registry1 = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path, plan_tracker=plan_tracker,
    )
    registry2 = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path, plan_tracker=plan_tracker,
    )

    assert registry1.tool_names == registry2.tool_names


# ── Phase 32: Office doc write tools enforce workspace boundary ──────────────
#   even with the default restrict_to_workspace=False.


@pytest.mark.asyncio
async def test_factory_docx_write_rejects_outside_workspace_default_config(
    fake_config, tmp_path,
):
    """With default config (restrict_to_workspace=False), docx_write
    must still reject an absolute path outside workspace."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    tool = registry.get("docx_write")
    assert tool is not None

    outside = tmp_path.parent / "outside_d.docx"
    result = await tool.execute(file_path=str(outside), content="test")
    assert "权限被拒绝" in result
    assert not outside.exists()


@pytest.mark.asyncio
async def test_factory_pptx_write_rejects_outside_workspace_default_config(
    fake_config, tmp_path,
):
    """Default config: pptx_write rejects absolute path outside workspace."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    tool = registry.get("pptx_write")
    assert tool is not None

    outside = tmp_path.parent / "outside_p.pptx"
    result = await tool.execute(file_path=str(outside), slides=[])
    assert "权限被拒绝" in result
    assert not outside.exists()


@pytest.mark.asyncio
async def test_factory_xlsx_write_rejects_outside_workspace_default_config(
    fake_config, tmp_path,
):
    """Default config: xlsx_write rejects absolute path outside workspace."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    tool = registry.get("xlsx_write")
    assert tool is not None

    outside = tmp_path.parent / "outside_x.xlsx"
    result = await tool.execute(file_path=str(outside), sheets={})
    assert "权限被拒绝" in result
    assert not outside.exists()


@pytest.mark.asyncio
async def test_factory_docx_write_relative_path_inside_workspace_default_config(
    fake_config, tmp_path,
):
    """Default config: docx_write relative path succeeds inside workspace."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    tool = registry.get("docx_write")
    assert tool is not None

    result = await tool.execute(file_path="report.docx", content="# Hi\nTest")
    assert "Created:" in result
    assert (tmp_path / "report.docx").exists()


@pytest.mark.asyncio
async def test_factory_docx_write_path_traversal_rejected_default_config(
    fake_config, tmp_path,
):
    """Default config: docx_write rejects ../ path traversal."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    tool = registry.get("docx_write")
    assert tool is not None

    result = await tool.execute(file_path="../escape.docx", content="test")
    assert "权限被拒绝" in result
    assert not (tmp_path.parent / "escape.docx").exists()


def test_write_file_semantics_unchanged(fake_config, tmp_path):
    """write_file must NOT gain the office-write default-boundary behavior.

    write_file's path enforcement is controlled by restrict_to_workspace
    config, NOT hardcoded to workspace.  Phase 32 only changes office tools.
    """
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    tool = registry.get("write_file")
    assert tool is not None

    # write_file's allowed_dir is None by default (restrict_to_workspace=False)
    assert tool._allowed_dir is None, (
        "write_file._allowed_dir must be None by default — "
        "restrict_to_workspace controls it, not Phase 32"
    )


# ── Sandbox manager wiring ────────────────────────────────────────────────


def test_sandbox_manager_wired_to_file_tools(fake_config, tmp_path):
    """When sandbox_manager is provided, file tools receive it."""
    from unittest.mock import MagicMock

    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    sandbox_manager = MagicMock()
    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path, sandbox_manager=sandbox_manager,
    )
    for tool_name in ("read_file", "write_file", "edit_file", "list_dir"):
        tool = registry.get(tool_name)
        assert tool is not None
        assert tool._sandbox_manager is sandbox_manager


def test_sandbox_manager_none_does_not_break_tools(fake_config, tmp_path):
    """None sandbox_manager should not break tool registration."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path, sandbox_manager=None,
    )
    for tool_name in ("exec", "read_file", "write_file", "edit_file", "list_dir"):
        assert registry.get(tool_name) is not None


def test_sandbox_manager_wired_through_services_from_config(fake_config):
    """RuntimeServices.from_config passes sandbox_manager to tool registry."""
    from unittest.mock import MagicMock

    from miqi.runtime.services import RuntimeServices

    sandbox_manager = MagicMock()
    services = RuntimeServices.from_config(
        config=fake_config,
        provider=MagicMock(),
        session_id="test:sandbox",
        workspace=fake_config.workspace_path,
        sandbox_manager=sandbox_manager,
    )
    for tool_name in ("read_file", "write_file", "edit_file"):
        tool = services.tool_registry.get(tool_name)
        assert tool is not None
        assert tool._sandbox_manager is sandbox_manager


def test_file_tools_receive_dot_skills_and_configured_extra_roots(fake_config, tmp_path):
    """tools.extra_roots and workspace/.skills flow into file tool shared roots."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    extra_root = tmp_path.parent / f"authorized-{tmp_path.name}"
    extra_root.mkdir()
    fake_config.tools.extra_roots = [str(extra_root)]

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    expected = {
        (tmp_path / "memory").resolve(),
        (tmp_path / "skills").resolve(),
        (tmp_path / ".skills").resolve(),
        extra_root.resolve(),
    }
    for tool_name in ("read_file", "write_file", "edit_file", "list_dir", "apply_patch"):
        tool = registry.get(tool_name)
        assert tool is not None
        roots = {Path(p).resolve() for p in tool._shared_roots}
        assert expected.issubset(roots), f"{tool_name} missing shared roots: {expected - roots}"


def test_exec_tool_receives_cross_session_guard_paths(fake_config, tmp_path, monkeypatch):
    """#1007 review: exec is handed the workspace root + its own files dir.

    The workspace root is in the rw bind set, which re-opens
    ``<ws>/sessions/**`` (every other session) writable inside the sandbox.
    ``bwrap._cross_session_guard_args`` can only close that again if it is
    told which path is the workspace root and which is THIS session's files
    dir — both default to ``None`` in bwrap, so a factory that stops passing
    them turns the guard into dead code with no test failing elsewhere.
    """
    from miqi.agent.tools import filesystem as fs
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    # The per-session files layout exists for the DEFAULT workspace only
    # (``_session_files_dir_for_key``); this tmp dir stands in for it.
    monkeypatch.setattr(fs, "_is_default_workspace", lambda path: True)

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path, session_id="desktop:1",
    )
    exec_tool = registry.get("exec")
    assert exec_tool is not None
    assert exec_tool._workspace_root == str(tmp_path.resolve())
    own = tmp_path / "sessions" / fs._session_files_dir_key("desktop:1") / "files"
    assert exec_tool._session_files_dir == str(own)
    # The guard re-opens this very dir after the read-only re-mount; it must
    # be the dir the factory actually created (not a re-derived spelling).
    assert own.is_dir()


def test_exec_tool_guard_paths_absent_without_session(fake_config, tmp_path):
    """No session key → no per-session dir → no guard (old args, exactly)."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(config=fake_config, workspace=tmp_path)
    exec_tool = registry.get("exec")
    assert exec_tool is not None
    assert exec_tool._workspace_root == str(tmp_path.resolve())
    assert exec_tool._session_files_dir is None


@pytest.mark.asyncio
async def test_factory_native_file_tools_respect_extra_roots_when_restricted(fake_config, tmp_path):
    """tools.extra_roots must also work for native paths under workspace restriction."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    extra_root = tmp_path.parent / f"authorized-{tmp_path.name}"
    extra_root.mkdir()
    target = extra_root / "report.txt"
    target.write_text("ok", encoding="utf-8")
    fake_config.tools.extra_roots = [str(extra_root)]
    fake_config.tools.restrict_to_workspace = True

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )

    read = registry.get("read_file")
    result = await read.execute(path=str(target))
    assert result == "ok"

    write = registry.get("write_file")
    out = extra_root / "out.txt"
    result = await write.execute(path=str(out), content="hello")
    assert "Successfully wrote" in result
    assert out.read_text(encoding="utf-8") == "hello"

    list_dir = registry.get("list_dir")
    result = await list_dir.execute(path=str(extra_root))
    assert "report.txt" in result


def test_factory_rejects_extra_roots_covering_protected_paths(fake_config, tmp_path):
    """Config/session ancestors must not be registered as writable extra roots."""
    from miqi.paths import get_config_path
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    (tmp_path / "sessions").mkdir()
    fake_config.tools.extra_roots = [
        str(tmp_path),
        str(get_config_path().parent),
    ]

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    roots = {Path(p).resolve() for p in registry.get("write_file")._shared_roots}
    # The workspace root itself is now a legal root (#689) — the assertion
    # is that it is NOT registered *as an extra root on top of* the
    # built-in workspace root; config ancestors stay rejected.
    assert get_config_path().parent.resolve() not in roots


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires privileges on Windows")
def test_factory_skips_default_shared_symlink_outside_workspace(fake_config, tmp_path):
    """A symlinked default shared dir resolving outside workspace is not auto-registered."""
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    external = tmp_path.parent / f"external-{tmp_path.name}"
    external.mkdir()
    link = tmp_path / ".skills"
    link.symlink_to(external, target_is_directory=True)

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    roots = {Path(p).resolve() for p in registry.get("read_file")._shared_roots}
    assert link.resolve() not in roots


def test_resolve_path_allows_shared_roots_with_native_sandbox(tmp_path):
    """Native sandbox path resolution also honors shared roots when restricted."""
    from unittest.mock import MagicMock

    from miqi.agent.tools.filesystem import _resolve_path

    ws = tmp_path / "ws"
    ws.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
    target = extra / "a.txt"
    target.write_text("x", encoding="utf-8")

    sandbox = MagicMock()
    sandbox.is_running = True
    sandbox.workspace_path = str(tmp_path / "sandbox-ws")
    manager = MagicMock()
    manager.active_sandbox = sandbox

    result = _resolve_path(
        str(target),
        workspace=ws,
        allowed_dir=ws,
        sandbox_manager=manager,
        shared_roots=[extra],
    )
    assert result == target.resolve()
