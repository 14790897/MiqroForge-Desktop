"""All four sandbox write sites pass their authorized roots as rw binds (#984).

With ``/mnt`` read-only (layer 2), a file-tool write that lands on
``/mnt/c/...`` needs the root re-opened with a per-call ``--bind``.  The
plan pins exactly four writers — ``write_file``, ``edit_file``,
``apply_patch`` and ``graph_render`` — and each must pass its OWN ``shared``
set (workspace ∪ static roots ∪ gated user roots ∪ #864 grants).

The sandbox is mocked and ``_sandbox_write_file`` is replaced with a
recorder, so these tests assert the plumbing, not bwrap itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_wsl_sandbox(workspace: Path) -> MagicMock:
    sb = MagicMock()
    sb._use_wsl = True
    sb.is_running = True
    sb.workspace = workspace
    sb.workspace_path = str(workspace)
    sb.run_command = AsyncMock(return_value=(0, "", ""))
    return sb


def _render_sandbox(workspace: Path) -> MagicMock:
    """WSL-shaped double whose ``test -d`` fails and ``test -f`` succeeds.

    ``GraphRenderTool._collect_targets`` probes the source with both, so the
    double must answer them differently or the JSON file is mistaken for a
    directory and no write happens at all.
    """
    sb = _mock_wsl_sandbox(workspace)

    async def _run(cmd: str, *args, **kwargs):
        if cmd.startswith("test -d "):
            return (1, "", "")
        return (0, "", "")

    sb.run_command = AsyncMock(side_effect=_run)
    return sb


def _mock_manager(sandbox: MagicMock) -> MagicMock:
    mgr = MagicMock()
    mgr.active_sandbox = sandbox
    mgr.get_or_create = AsyncMock(return_value=sandbox)
    return mgr


def _recorder(captured: dict):
    async def _fake(sandbox, sandbox_path, content, **kwargs):
        captured["sandbox_path"] = sandbox_path
        captured["extra_rw_binds"] = [
            str(r) for r in (kwargs.get("extra_rw_binds") or [])
        ]
    return _fake


def _assert_binds(captured: dict, *roots: Path) -> None:
    for root in roots:
        assert str(root) in captured["extra_rw_binds"], (
            f"{root} missing from {captured['extra_rw_binds']}"
        )


# ── write_file ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_write_file_passes_binds(monkeypatch, tmp_path: Path) -> None:
    import miqi.agent.tools.filesystem as fs

    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    captured: dict = {}
    monkeypatch.setattr(fs, "_sandbox_write_file", _recorder(captured))

    tool = fs.WriteFileTool(
        workspace=ws, sandbox_manager=_mock_manager(_mock_wsl_sandbox(ws)),
        shared_roots=[ws],
    )
    result = await tool.execute(
        str(out / "report.md"), "hello", _session_key="s1",
        _user_roots=[str(out)],
    )
    assert result.startswith("Successfully wrote")
    _assert_binds(captured, ws, out)


# ── edit_file ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_file_passes_binds(monkeypatch, tmp_path: Path) -> None:
    import miqi.agent.tools.filesystem as fs

    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    captured: dict = {}
    monkeypatch.setattr(fs, "_sandbox_write_file", _recorder(captured))
    monkeypatch.setattr(fs, "_sandbox_file_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(fs, "_sandbox_read_file", AsyncMock(return_value="old text"))

    tool = fs.EditFileTool(
        workspace=ws, sandbox_manager=_mock_manager(_mock_wsl_sandbox(ws)),
        shared_roots=[ws],
    )
    result = await tool.execute(
        str(out / "report.md"), "old text", "new text",
        _session_key="s1", _user_roots=[str(out)],
    )
    assert result.startswith("Successfully edited")
    _assert_binds(captured, ws, out)


# ── apply_patch ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_patch_passes_binds(monkeypatch, tmp_path: Path) -> None:
    import miqi.agent.tools.apply_patch as ap

    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    captured: dict = {}
    monkeypatch.setattr(ap, "_sandbox_write_file", _recorder(captured))
    monkeypatch.setattr(ap, "_sandbox_file_exists", AsyncMock(return_value=True))
    monkeypatch.setattr(ap, "_sandbox_read_file", AsyncMock(return_value="old\n"))

    tool = ap.ApplyPatchTool(
        workspace=ws, sandbox_manager=_mock_manager(_mock_wsl_sandbox(ws)),
        shared_roots=[ws, out], base_workspace=ws,
    )
    target = ws / "report.md"
    result = await tool.execute(
        patch=(
            f"--- a/{target}\n+++ b/{target}\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        ),
        _session_key="s1",
    )
    assert "Applied patch" in result, result
    _assert_binds(captured, ws, out)


# ── graph_render ─────────────────────────────────────────────────────────

_GRAPH_JSON = {
    "schema_version": "1.0",
    "graph_type": "step-nodes",
    "skill": "test",
    "nodes": [{"id": "S1", "title": "步骤一", "category": "compute"}],
    "edges": [],
}


def _write_recorder(captured: list):
    async def _fake(sandbox, sandbox_path, content, **kwargs):
        captured.append({
            "sandbox_path": sandbox_path,
            "extra_rw_binds": [
                str(r) for r in (kwargs.get("extra_rw_binds") or [])
            ],
        })
    return _fake


@pytest.mark.asyncio
async def test_graph_render_execute_forwards_its_own_shared(
    monkeypatch, tmp_path: Path,
) -> None:
    """Drives ``GraphRenderTool.execute`` — the real write site.

    ``_write_text`` falls back to ``self._shared_roots`` when ``shared`` is
    omitted, so calling it directly (as this test used to) stays green even if
    ``execute`` stops forwarding ``shared=shared`` (graph_render.py:836/841/852).
    Going through ``execute`` and asserting the forwarded set exactly is what
    makes the 4th writer provable.
    """
    import miqi.agent.tools.graph_render as gr

    ws = tmp_path / "ws"
    ws.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    src = ws / "step-graph.json"
    src.write_text(json.dumps(_GRAPH_JSON), encoding="utf-8")

    seen_shared: list = []
    writes: list = []
    real_write_text = gr.GraphRenderTool._write_text

    async def _spy(self, resolved, content, sandbox, **kwargs):
        seen_shared.append(kwargs.get("shared"))
        await real_write_text(self, resolved, content, sandbox, **kwargs)

    monkeypatch.setattr(gr.GraphRenderTool, "_write_text", _spy)
    monkeypatch.setattr(gr, "_sandbox_write_file", _write_recorder(writes))
    monkeypatch.setattr(gr, "_sandbox_read_file", AsyncMock(
        return_value=json.dumps(_GRAPH_JSON),
    ))

    tool = gr.GraphRenderTool(
        workspace=ws, sandbox_manager=_mock_manager(_render_sandbox(ws)),
        shared_roots=[ws],
    )

    # svg → 1 write (graph_render.py:852); html → 2 writes (:836, :841).
    for fmt, expected_writes in (("svg", 1), ("html", 2)):
        seen_shared.clear()
        writes.clear()
        result = await tool.execute(
            str(src), format=fmt, out_dir=str(out),
            _session_key="s1", _user_roots=[str(out)],
        )
        payload = json.loads(result)
        assert payload["ok"] is True, result
        assert len(seen_shared) == expected_writes, seen_shared
        # EXACTLY workspace ∪ static roots ∪ gated user roots — the fallback
        # (`self._shared_roots` = [ws]) would drop `out` and fail here.
        assert all(shared == [ws, out] for shared in seen_shared), seen_shared
        assert len(writes) == expected_writes, writes
        for write in writes:
            _assert_binds(write, ws, out)
