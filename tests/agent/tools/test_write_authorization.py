"""Unit tests for the write authorization card (issue #864).

Read/write asymmetry + on-demand write authorization: file reads are widened
to home + whole disk, writes stay on the narrow whitelist, and a write target
outside every legal write root pops [允许本次 / 本目录不再询问 / 拒绝] via the
shared user-input resolver instead of a hard PermissionError.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import (
    WriteFileTool,
    _resolve_write_shared_roots,
)

_IS_WINDOWS = sys.platform == "win32"


def _resolver(choice: str):
    async def r(payload: dict):
        return {"status": "submitted", "answers": {"choice_id": choice}}

    return r


def test_resolve_write_shared_roots_in_roots_no_card(tmp_path):
    """A target already inside the write roots returns the roots unchanged."""
    target = tmp_path / "out.txt"
    shared = [tmp_path]
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(target),
            base_dir=tmp_path,
            workspace_root=tmp_path,
            shared=shared,
            granted=set(),
            write_resolver=_resolver("once"),
        )
    )
    assert result == shared


def test_resolve_write_shared_roots_once_grants(tmp_path):
    """Out-of-roots + [允许本次] → augmented roots (no session memory)."""
    outside = tmp_path / "outside" / "x.txt"
    shared = [tmp_path / "ws"]
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(outside),
            base_dir=tmp_path / "ws",
            workspace_root=tmp_path / "ws",
            shared=shared,
            granted=set(),
            write_resolver=_resolver("once"),
        )
    )
    assert result is not None
    assert outside.parent.resolve() in [Path(r).resolve() for r in result]


def test_resolve_write_shared_roots_always_dir_grants_and_persists(tmp_path):
    """Out-of-roots + [本目录不再询问] → persists the parent dir."""
    outside = tmp_path / "outside" / "x.txt"
    shared = [tmp_path / "ws"]
    persisted = []

    async def persist(root):
        persisted.append(root)

    result = asyncio.run(
        _resolve_write_shared_roots(
            str(outside),
            base_dir=tmp_path / "ws",
            workspace_root=tmp_path / "ws",
            shared=shared,
            granted=set(),
            write_resolver=_resolver("always_dir"),
            persist_extra_root=persist,
        )
    )
    assert result is not None
    assert persisted == [outside.parent.resolve()]


def test_resolve_write_shared_roots_bypass_grants_without_persist(tmp_path):
    """Out-of-roots + bypass=True → granted session-scoped, no persist, no card."""
    outside = tmp_path / "outside" / "x.txt"
    shared = [tmp_path / "ws"]
    persisted = []
    calls = []

    async def persist(root):
        persisted.append(root)

    async def resolver(payload):
        calls.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "once"}}

    result = asyncio.run(
        _resolve_write_shared_roots(
            str(outside),
            base_dir=tmp_path / "ws",
            workspace_root=tmp_path / "ws",
            shared=shared,
            granted=set(),
            write_resolver=resolver,
            persist_extra_root=persist,
            bypass=True,
        )
    )
    assert result is not None
    assert outside.parent.resolve() in [Path(r).resolve() for r in result]
    assert persisted == []  # bypass never widens tools.extra_roots
    assert calls == []  # bypass skips the card


def test_resolve_write_shared_roots_bypass_protected_still_denies(tmp_path):
    """bypass does NOT grant protected targets (home root)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    home = Path.home()
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(home / "x.txt"),
            base_dir=ws,
            workspace_root=ws,
            shared=[],
            granted=set(),
            write_resolver=_resolver("once"),
            bypass=True,
        )
    )
    assert result is None


def test_resolve_write_shared_roots_deny(tmp_path):
    """Out-of-roots + [拒绝] → None (denied)."""
    outside = tmp_path / "outside" / "x.txt"
    shared = [tmp_path / "ws"]
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(outside),
            base_dir=tmp_path / "ws",
            workspace_root=tmp_path / "ws",
            shared=shared,
            granted=set(),
            write_resolver=_resolver("deny"),
        )
    )
    assert result is None


def test_resolve_write_shared_roots_no_resolver_denies(tmp_path):
    """Headless (no resolver) → None (deny-first)."""
    outside = tmp_path / "outside" / "x.txt"
    shared = [tmp_path / "ws"]
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(outside),
            base_dir=tmp_path / "ws",
            workspace_root=tmp_path / "ws",
            shared=shared,
            granted=set(),
            write_resolver=None,
        )
    )
    assert result is None


def test_resolve_write_shared_roots_drive_root_never_grants(tmp_path):
    """A drive/filesystem root is never grantable even with a resolver."""
    root = Path(tmp_path.anchor)  # drive root on Windows, '/' on POSIX
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(root / "whatever.txt"),
            base_dir=tmp_path,
            workspace_root=tmp_path,
            shared=[],
            granted=set(),
            write_resolver=_resolver("once"),
        )
    )
    assert result is None


def test_resolve_write_shared_roots_home_never_grants(tmp_path):
    """The user profile root is never grantable, even when outside the workspace."""
    ws = tmp_path / "ws"
    ws.mkdir()
    home = Path.home()  # isolated by the autouse fixture, but NOT under ws
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(home / "x.txt"),
            base_dir=ws,
            workspace_root=ws,
            shared=[],
            granted=set(),
            write_resolver=_resolver("once"),
        )
    )
    # The grantable dir would be the profile root; must be rejected.
    assert result is None


def test_resolve_write_shared_roots_granted_memory(tmp_path):
    """A previously granted dir short-circuits without another card."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    calls = []

    async def resolver(payload):
        calls.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "once"}}

    granted = {str(outside.resolve())}
    result = asyncio.run(
        _resolve_write_shared_roots(
            str(outside / "y.txt"),
            base_dir=ws,
            workspace_root=ws,
            shared=[],
            granted=granted,
            write_resolver=resolver,
        )
    )
    # Already authorized → the granted dir is returned (so the caller's
    # whitelist check accepts it), but no card is popped.
    assert outside.resolve() in [Path(r).resolve() for r in result]
    assert calls == []


@pytest.mark.asyncio
async def test_write_file_authorize_paths_deny_aborts(tmp_path):
    """write_file with a declined authorize_paths returns an error, no write."""
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = WriteFileTool(
        workspace=ws,
        allowed_dir=ws,
        shared_roots=[],
        write_resolver=_resolver("deny"),
    )
    out = tmp_path / "outside" / "x.txt"
    result = await tool.execute(path=str(out), content="hi", authorize_paths=[str(out)])
    assert result.startswith("Error: 权限被拒绝")
    assert not out.exists()


@pytest.mark.asyncio
async def test_write_file_authorize_paths_once_writes(tmp_path):
    """write_file with authorize_paths granted once writes the file."""
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = WriteFileTool(
        workspace=ws,
        allowed_dir=ws,
        shared_roots=[],
        write_resolver=_resolver("once"),
    )
    out = tmp_path / "outside" / "x.txt"
    result = await tool.execute(path=str(out), content="hi", authorize_paths=[str(out)])
    assert result.startswith("Successfully wrote")
    assert out.read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_file_authorize_paths_unrestricted_native_not_blocked(tmp_path):
    """On the unrestricted native path (no boundary), a declared out-of-roots
    authorize_paths must NOT block the write — the boundary does not exist."""
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = WriteFileTool(
        workspace=ws,
        shared_roots=[],
        write_resolver=_resolver("deny"),
    )
    out = tmp_path / "outside" / "x.txt"
    result = await tool.execute(path=str(out), content="hi", authorize_paths=[str(out)])
    assert result.startswith("Successfully wrote")
    assert out.read_text(encoding="utf-8") == "hi"


@pytest.mark.asyncio
async def test_write_file_in_workspace_no_card(tmp_path):
    """In-workspace writes never pop a card even with a resolver wired."""
    ws = tmp_path / "ws"
    ws.mkdir()
    calls = []

    async def resolver(payload):
        calls.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "deny"}}

    tool = WriteFileTool(
        workspace=ws,
        shared_roots=[ws],
        write_resolver=resolver,
    )
    out = ws / "in.txt"
    result = await tool.execute(path=str(out), content="ok")
    assert result.startswith("Successfully wrote")
    assert calls == []


@pytest.mark.asyncio
async def test_write_file_grants_are_session_scoped(tmp_path):
    """A grant in one session must not leak into another (CodeRabbit #866)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = WriteFileTool(
        workspace=ws,
        allowed_dir=ws,
        shared_roots=[],
        write_resolver=_resolver("once"),
    )
    out = tmp_path / "outside" / "x.txt"
    # Session A grants (once) → writes successfully.
    res_a = await tool.execute(
        path=str(out), content="a", _session_key="sessionA",
    )
    assert res_a.startswith("Successfully wrote")

    # Same tool instance, session B, same target — but "once" is
    # invocation-scoped (never stored in the session set), so a fresh call
    # must re-authorize and be denied by the (now deny) resolver.  This proves
    # grants are keyed by session AND "once" does not persist.
    tool._write_resolver = _resolver("deny")
    res_b = await tool.execute(
        path=str(out), content="b", _session_key="sessionB",
    )
    assert res_b.startswith("Error: 权限被拒绝")


@pytest.mark.asyncio
async def test_write_file_once_not_persisted_within_session(tmp_path):
    """「允许本次」只对当次调用生效，同会话后续写需重新授权 (CWE-863)。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = WriteFileTool(
        workspace=ws,
        allowed_dir=ws,
        shared_roots=[],
        write_resolver=_resolver("once"),
    )
    out = tmp_path / "outside" / "x.txt"
    # First call: "once" grants → writes.
    res_a = await tool.execute(
        path=str(out), content="a", _session_key="sessionA",
    )
    assert res_a.startswith("Successfully wrote")

    # Same session, second call to a DIFFERENT file in the same dir: "once"
    # must NOT have persisted, so a deny resolver blocks it.
    tool._write_resolver = _resolver("deny")
    res_b = await tool.execute(
        path=str(out), content="b", _session_key="sessionA",
    )
    assert res_b.startswith("Error: 权限被拒绝")


@pytest.mark.asyncio
async def test_write_file_always_dir_persists_within_session(tmp_path):
    """「本目录不再询问」在同会话内持久，后续写不再弹卡。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    tool = WriteFileTool(
        workspace=ws,
        allowed_dir=ws,
        shared_roots=[],
        write_resolver=_resolver("always_dir"),
    )
    out = tmp_path / "outside" / "x.txt"
    res_a = await tool.execute(
        path=str(out), content="a", _session_key="sessionA",
    )
    assert res_a.startswith("Successfully wrote")

    # Same session, second call: "always_dir" persisted in the session set, so
    # even a deny resolver never fires (no card, direct write).
    tool._write_resolver = _resolver("deny")
    res_b = await tool.execute(
        path=str(out), content="b", _session_key="sessionA",
    )
    assert res_b.startswith("Successfully wrote")
    assert out.read_text(encoding="utf-8") == "b"
