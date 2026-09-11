"""Session workspace isolation for file tools (#221 / #613 follow-up).

Regression coverage for the bug where ``write_file`` with an absolute path
under the default workspace root (the directory the system prompt advertises
as the working directory) landed the file in the SHARED root instead of the
per-session files dir ``sessions/<key>/files/``.

Behaviour under test:
- NEW files written under the default workspace root are redirected into the
  session files dir (native and WSL sandbox paths).
- Existing root files (bootstrap AGENTS.md etc.) are edited in place.
- Shared sub-roots (memory/ skills/ .skills/) are never redirected.
- read_file falls back to the session files dir when the root misses.
- The registry factory wires the per-session dir from ``session_id``.
"""

import os
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import (
    EditFileTool,
    ReadFileTool,
    WriteFileTool,
    _redirect_new_file_write,
    _redirect_path_to_session,
    _session_files_dir_key,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _mk_env(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    key = "miqi-desktop:desktop:1786807046853"
    session_dir = root / "sessions" / _session_files_dir_key(key) / "files"
    session_dir.mkdir(parents=True)
    write = WriteFileTool(
        workspace=session_dir,
        base_workspace=root,
        session_files_dir=session_dir,
    )
    read = ReadFileTool(workspace=root, session_files_dir=session_dir)
    return root, session_dir, write, read


# ── Key derivation ─────────────────────────────────────────────────────────


def test_session_files_dir_key_derivation():
    # Namespaced session_id (client_id:session_key, ≥3 segments) → client dropped.
    assert _session_files_dir_key("miqi-desktop:desktop:1786807046853") == "desktop_1786807046853"
    # Two-segment channel keys keep the channel — matches the disk convention
    # used by files.read / attachment saving.
    assert _session_files_dir_key("desktop:1786807046853") == "desktop_1786807046853"
    assert _session_files_dir_key("cli:direct") == "cli_direct"
    assert _session_files_dir_key("gateway:default") == "gateway_default"
    assert _session_files_dir_key("thread_nomap") == "thread_nomap"


# ── Path redirection helpers ───────────────────────────────────────────────


def test_redirect_path_absolute_root_new_file(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    target = str(root / "welcome.md")
    out = _redirect_path_to_session(target, root, session_dir)
    assert out == str(session_dir / "welcome.md")


@pytest.mark.skipif(os.name != "nt", reason="WSL /mnt/ form is Windows-specific")
def test_redirect_path_preserves_mnt_style(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    drive = str(root)[0].lower()  # CI runners may use D: — never hardcode c:
    target = f"/mnt/{drive}" + str(root).replace("\\", "/")[2:] + "/report.md"
    out = _redirect_path_to_session(target, root, session_dir)
    assert out is not None
    assert out.startswith(f"/mnt/{drive}/")
    assert out.endswith("/sessions/desktop_1786807046853/files/report.md")


@pytest.mark.skipif(os.name != "nt", reason="8.3 short names are Windows-only")
def test_redirect_path_handles_windows_83_short_names(tmp_path):
    """A temp dir handed out as ``C:\\Users\\INTERS~1\\...`` must still be
    recognized as being under the workspace (whose canonical form uses the
    long name ``C:\\Users\\Intership003\\...``).  This exact mismatch made
    the E2E temp-home scenario skip the redirect and write to the root.

    On CI runners the short form may equal the long form (short paths with
    no spaces need no 8.3 munging) — the redirect assertion still holds."""
    import ctypes

    GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW  # noqa: N806

    def short_name(p: Path) -> str:
        buf = ctypes.create_unicode_buffer(512)
        GetShortPathNameW(str(p), buf, 512)
        return buf.value

    root, session_dir, _, _ = _mk_env(tmp_path)
    short_root = short_name(root)
    assert short_root  # API must return a usable path
    target = short_root + "\\welcome.md"
    out = _redirect_path_to_session(target, root, session_dir)
    assert out == str(session_dir / "welcome.md")


def test_redirect_path_relative_maps_to_session_dir(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    out = _redirect_path_to_session("notes.txt", root, session_dir)
    assert out == str(session_dir / "notes.txt")


def test_redirect_path_skips_shared_subdirs(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    for sub in ("memory", "skills", ".skills"):
        assert _redirect_path_to_session(str(root / sub / "x.md"), root, session_dir) is None
        assert _redirect_path_to_session(str(root / sub), root, session_dir) is None


def test_redirect_path_skips_outside_root_and_session_dir(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    outside = tmp_path / "elsewhere" / "x.md"
    assert _redirect_path_to_session(str(outside), root, session_dir) is None
    already = session_dir / "inner" / "x.md"
    assert _redirect_path_to_session(str(already), root, session_dir) is None
    assert _redirect_path_to_session(str(root / "sessions"), root, session_dir) is None


async def _exists_true(_p):
    return True


async def _exists_false(_p):
    return False


@pytest.mark.asyncio
async def test_redirect_new_file_write_keeps_existing_root_file(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    existing = root / "AGENTS.md"
    existing.write_text("old", encoding="utf-8")
    out = await _redirect_new_file_write(str(existing), root, session_dir, _exists_true)
    assert out == str(existing)


@pytest.mark.asyncio
async def test_redirect_new_file_write_redirects_when_missing(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    out = await _redirect_new_file_write(str(root / "new.md"), root, session_dir, _exists_false)
    assert out == str(session_dir / "new.md")


@pytest.mark.asyncio
async def test_redirect_new_file_write_noop_without_dirs(tmp_path):
    out = await _redirect_new_file_write("C:/Users/x/.miqi/workspace/a.md", None, None, _exists_false)
    assert out == "C:/Users/x/.miqi/workspace/a.md"


# ── write_file (native, no sandbox) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_file_absolute_root_path_lands_in_session_dir(tmp_path):
    root, session_dir, write, _ = _mk_env(tmp_path)
    target = str(root / "welcome.md")
    result = await write.execute(path=target, content="hi")
    assert "Successfully wrote" in result
    assert str(session_dir) in result
    # Delivery truthfulness (miqibug 路径归一化): the message states BOTH the
    # real session-dir path and the requested path it was normalized from.
    assert str(target) in result
    assert "已按会话隔离归一化到会话 files 目录" in result
    assert (session_dir / "welcome.md").read_text(encoding="utf-8") == "hi"
    assert not (root / "welcome.md").exists()


@pytest.mark.asyncio
async def test_write_file_relative_path_lands_in_session_dir(tmp_path):
    root, session_dir, write, _ = _mk_env(tmp_path)
    result = await write.execute(path="rel.md", content="hi")
    assert "Successfully wrote" in result
    # Relative paths anchor to the session dir by convention — no redirect
    # note needed (the legacy prompt documents relative = session dir).
    assert "已按会话隔离归一化" not in result
    assert (session_dir / "rel.md").exists()
    assert not (root / "rel.md").exists()


@pytest.mark.asyncio
async def test_write_file_existing_root_file_edited_in_place(tmp_path):
    root, session_dir, write, _ = _mk_env(tmp_path)
    bootstrap = root / "AGENTS.md"
    bootstrap.write_text("old", encoding="utf-8")
    result = await write.execute(path=str(bootstrap), content="new")
    assert "Successfully wrote" in result
    assert bootstrap.read_text(encoding="utf-8") == "new"
    assert not (session_dir / "AGENTS.md").exists()


@pytest.mark.asyncio
async def test_write_file_shared_subdir_not_redirected(tmp_path):
    root, session_dir, write, _ = _mk_env(tmp_path)
    (root / "memory").mkdir()
    target = root / "memory" / "MEMORY.md"
    result = await write.execute(path=str(target), content="mem")
    assert "Successfully wrote" in result
    assert target.read_text(encoding="utf-8") == "mem"
    assert not (session_dir / "memory" / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_write_file_outside_workspace_untouched(tmp_path):
    _root, _, write, _ = _mk_env(tmp_path)
    outside = tmp_path / "out.md"
    await write.execute(path=str(outside), content="x")
    assert outside.read_text(encoding="utf-8") == "x"  # no restriction configured


# ── edit_file (native) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_file_by_original_root_path_edits_session_copy(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    (session_dir / "note.md").write_text("hello world", encoding="utf-8")
    edit = EditFileTool(
        workspace=session_dir, base_workspace=root, session_files_dir=session_dir,
    )
    result = await edit.execute(
        path=str(root / "note.md"), old_text="hello", new_text="bye",
    )
    assert "Successfully edited" in result
    # Delivery truthfulness: the message states the requested path too.
    assert str(root / "note.md") in result
    assert "已按会话隔离归一化到会话 files 目录" in result
    assert (session_dir / "note.md").read_text(encoding="utf-8") == "bye world"
    assert not (root / "note.md").exists()


@pytest.mark.asyncio
async def test_edit_file_existing_root_file_edited_in_place(tmp_path):
    root, session_dir, _, _ = _mk_env(tmp_path)
    bootstrap = root / "AGENTS.md"
    bootstrap.write_text("alpha beta", encoding="utf-8")
    edit = EditFileTool(
        workspace=session_dir, base_workspace=root, session_files_dir=session_dir,
    )
    result = await edit.execute(
        path=str(bootstrap), old_text="alpha", new_text="gamma",
    )
    assert "Successfully edited" in result
    assert bootstrap.read_text(encoding="utf-8") == "gamma beta"


# ── read_file fallback ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_file_falls_back_to_session_dir_absolute(tmp_path):
    root, session_dir, _, read = _mk_env(tmp_path)
    (session_dir / "welcome.md").write_text("session content", encoding="utf-8")
    result = await read.execute(path=str(root / "welcome.md"))
    assert result == "session content"


@pytest.mark.asyncio
async def test_read_file_falls_back_to_session_dir_relative(tmp_path):
    _root, session_dir, _, read = _mk_env(tmp_path)
    (session_dir / "rel.md").write_text("rel content", encoding="utf-8")
    result = await read.execute(path="rel.md")
    assert result == "rel content"


@pytest.mark.asyncio
async def test_read_file_root_file_still_readable(tmp_path):
    root, _session_dir, _, read = _mk_env(tmp_path)
    (root / "AGENTS.md").write_text("bootstrap", encoding="utf-8")
    result = await read.execute(path="AGENTS.md")
    assert result == "bootstrap"


# ── Factory wiring ─────────────────────────────────────────────────────────


def test_factory_wires_session_files_dir_for_default_workspace(fake_config, tmp_path):
    from miqi.paths import get_miqi_home
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    ws = Path(get_miqi_home()) / "workspace"
    ws.mkdir(parents=True)
    fake_config.agents.defaults.workspace = str(ws)

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=ws, session_id="miqi-desktop:desktop:1786807046853",
    )
    session_dir = ws / "sessions" / "desktop_1786807046853" / "files"

    write = registry.get("write_file")
    assert write._workspace == session_dir
    assert write._session_files_dir == session_dir
    assert write._base_workspace == ws

    edit = registry.get("edit_file")
    assert edit._workspace == session_dir

    patch = registry.get("apply_patch")
    assert patch._workspace == session_dir

    read = registry.get("read_file")
    assert read._workspace == ws  # reads stay rooted at the workspace
    assert read._session_files_dir == session_dir


def test_factory_no_isolation_for_custom_workspace(fake_config, tmp_path):
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    custom = tmp_path / "project"
    custom.mkdir()
    fake_config.agents.defaults.workspace = str(custom)

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=custom, session_id="miqi-desktop:desktop:123",
    )
    write = registry.get("write_file")
    assert write._workspace == custom
    assert write._session_files_dir is None
    assert write._base_workspace == custom


def test_factory_no_session_id_keeps_legacy_behavior(fake_config, tmp_path):
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=tmp_path,
    )
    write = registry.get("write_file")
    assert write._workspace == tmp_path
    assert write._session_files_dir is None


# ── Cross-session containment (CodeRabbit #731) ────────────────────────────


@pytest.mark.asyncio
async def test_write_file_rejects_foreign_session_dir(tmp_path):
    """A write targeting another session's files dir must be denied (native)."""
    root, session_dir, _, _ = _mk_env(tmp_path)
    other = root / "sessions" / "other_session" / "files"
    other.mkdir(parents=True)
    write = WriteFileTool(
        workspace=session_dir, base_workspace=root, session_files_dir=session_dir,
    )
    result = await write.execute(path=str(other / "x.md"), content="sneak")
    assert "权限被拒绝" in result
    assert not (other / "x.md").exists()


@pytest.mark.asyncio
async def test_write_file_rejects_relative_escape_into_sessions(tmp_path):
    """../ traversal cannot climb out of the session dir into sessions/."""
    root, session_dir, write, _ = _mk_env(tmp_path)
    other = root / "sessions" / "other_session" / "files"
    other.mkdir(parents=True)
    result = await write.execute(path="../other_session/files/x.md", content="sneak")
    assert "权限被拒绝" in result or "Error" in result
    assert not (other / "x.md").exists()


@pytest.mark.asyncio
async def test_read_file_rejects_foreign_session_dir(tmp_path):
    # Read containment derives its base from the DEFAULT workspace (read
    # tools resolve against the root); a tmp root is treated as custom.
    ws = _default_ws()
    session_dir = ws / "sessions" / _session_files_dir_key("desktop:456") / "files"
    session_dir.mkdir(parents=True)
    other = ws / "sessions" / "other_session" / "files"
    other.mkdir(parents=True)
    (other / "secret.md").write_text("secret", encoding="utf-8")
    read = ReadFileTool(workspace=ws, session_files_dir=session_dir)
    result = await read.execute(path=str(other / "secret.md"))
    assert "权限被拒绝" in result or "文件不存在" in result


@pytest.mark.asyncio
async def test_read_fallback_uses_session_workspace_with_native_sandbox(tmp_path):
    """Non-WSL (native) sandbox: the read fallback must resolve against the
    session workspace so it matches where writes landed (CodeRabbit #731)."""
    from unittest.mock import MagicMock

    root, session_dir, _, _ = _mk_env(tmp_path)
    sandbox_ws = tmp_path / "sandbox-ws"
    sandbox_ws.mkdir()

    sandbox = MagicMock()
    sandbox.is_running = True
    sandbox.workspace_path = str(sandbox_ws)
    manager = MagicMock()
    manager.active_sandbox = sandbox

    write = WriteFileTool(
        workspace=session_dir, base_workspace=root, session_files_dir=session_dir,
        sandbox_manager=manager,
    )
    result = await write.execute(path="note.md", content="native sb")
    assert "Successfully wrote" in result
    assert (sandbox_ws / "note.md").read_text(encoding="utf-8") == "native sb"

    read = ReadFileTool(
        workspace=root, session_files_dir=session_dir, sandbox_manager=manager,
    )
    result = await read.execute(path="note.md")
    assert result == "native sb"


# ── KUN runtime path ───────────────────────────────────────────────────────


def _default_ws() -> Path:
    from miqi.paths import get_miqi_home

    ws = Path(get_miqi_home()) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.mark.asyncio
async def test_kun_tool_host_injects_session_key(fake_config):
    """KUN MiQiToolHost must inject _session_key (mirroring the legacy
    orchestrator) or per-session isolation never engages on the KUN runtime."""
    from miqi.kun_runtime.migration_adapter import clear_mapping, register_mapping
    from miqi.kun_runtime.tool_host import MiQiToolHost, ToolCallLike, ToolHostContext
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    ws = _default_ws()
    fake_config.agents.defaults.workspace = str(ws)
    registry = create_runtime_tool_registry(config=fake_config, workspace=ws)
    host = MiQiToolHost(registry)

    try:
        register_mapping("desktop:456", "thread_abc")
        ctx = ToolHostContext(thread_id="thread_abc", turn_id="t1", workspace=str(ws))
        result = await host.execute(
            ToolCallLike(call_id="c1", tool_name="write_file",
                         arguments={"path": str(ws / "k.md"), "content": "kun"}),
            ctx,
        )
        assert "Successfully wrote" in result.item["output"]
        # Delivery truthfulness: the absolute requested path is restated
        # alongside the real session-dir path.
        assert "已按会话隔离归一化到会话 files 目录" in result.item["output"]
        assert (ws / "sessions" / "desktop_456" / "files" / "k.md").read_text(encoding="utf-8") == "kun"
        assert not (ws / "k.md").exists()
    finally:
        clear_mapping("desktop:456")


@pytest.mark.asyncio
async def test_kun_tool_host_uses_thread_id_without_mapping(fake_config):
    """Without a registered mapping the thread id itself is the session key."""
    from miqi.kun_runtime.tool_host import MiQiToolHost, ToolCallLike, ToolHostContext
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

    ws = _default_ws()
    fake_config.agents.defaults.workspace = str(ws)
    registry = create_runtime_tool_registry(config=fake_config, workspace=ws)
    host = MiQiToolHost(registry)

    ctx = ToolHostContext(thread_id="thread_nomap", turn_id="t1", workspace=str(ws))
    result = await host.execute(
        ToolCallLike(call_id="c1", tool_name="write_file",
                     arguments={"path": str(ws / "n.md"), "content": "kun"}),
        ctx,
    )
    assert "Successfully wrote" in result.item["output"]
    assert "已按会话隔离归一化到会话 files 目录" in result.item["output"]
    assert (ws / "sessions" / "thread_nomap" / "files" / "n.md").exists()
    assert not (ws / "n.md").exists()


@pytest.mark.asyncio
async def test_native_write_with_session_key_derives_session_dir(tmp_path):
    """Even a directly-constructed tool (no factory) isolates native writes
    when _session_key is injected — the per-call fallback."""
    from miqi.paths import get_miqi_home

    ws = Path(get_miqi_home()) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    write = WriteFileTool(workspace=ws)  # no factory plumbing at all
    result = await write.execute(
        path=str(ws / "native.md"), content="hi", _session_key="desktop:789",
    )
    assert "Successfully wrote" in result
    assert "已按会话隔离归一化到会话 files 目录" in result
    assert (ws / "sessions" / "desktop_789" / "files" / "native.md").exists()
    assert not (ws / "native.md").exists()


# ── WSL sandbox branch (Windows-only) ─────────────────────────────────────


@pytest.mark.skipif(os.name != "nt", reason="WSL /mnt/ mapping is Windows-specific")
@pytest.mark.asyncio
async def test_write_file_wsl_sandbox_redirects_absolute_root_path(tmp_path):
    """The sandbox write command must target sessions/<key>/files, not the root."""
    from unittest.mock import MagicMock

    root, session_dir, _, _ = _mk_env(tmp_path)

    class FakeSandbox:
        _use_wsl = True
        is_running = True
        session_key = "miqi-desktop:desktop:1786807046853"
        workspace = str(root)
        workspace_path = str(root)

        def __init__(self):
            self.calls = []

        async def run_command(self, cmd, timeout=30):
            self.calls.append(cmd)
            if cmd.startswith("test "):
                return (1, "", "")  # target does not exist yet
            return (0, "", "")

    sandbox = FakeSandbox()
    manager = MagicMock()
    manager.get_or_create = MagicMock()

    async def _get_or_create(key):
        return sandbox

    manager.get_or_create.side_effect = _get_or_create

    write = WriteFileTool(
        workspace=session_dir,
        base_workspace=root,
        session_files_dir=session_dir,
        sandbox_manager=manager,
        shared_roots=[root],  # production factory whitelists the workspace root (#689)
    )
    result = await write.execute(
        path=str(root / "welcome.md"),
        content="hi",
        _session_key="miqi-desktop:desktop:1786807046853",
    )
    assert "Successfully wrote" in result
    write_cmds = [c for c in sandbox.calls if "base64" in c]
    assert write_cmds, "expected a sandbox write command"
    assert "/sessions/desktop_1786807046853/files/welcome.md" in write_cmds[0]
    assert not any("workspace/welcome.md'" in c for c in write_cmds)
