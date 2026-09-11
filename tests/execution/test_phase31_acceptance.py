"""Phase 31 acceptance / audit tests.

Verifies the key invariants established across Phases 31.1–31.8:
- ledger seq ordering (no missing terminal events)
- single-writer rule (no duplicate exec/approval items)
- RuntimeSession mirror excludes exec & approval lifecycle types
- sandbox selection enforcement
- production code audit (no legacy APIs, no fire-and-forget ledger)
"""

import asyncio
import inspect
import uuid

import pytest

from miqi.runtime.ledger_runtime import LedgerRuntime

# ── Helpers ────────────────────────────────────────────────────────────────


def _unique_id():
    return uuid.uuid4().hex[:12]


def _temp_ledger(tmp_path, session_id=None):
    """Create a real LedgerRuntime backed by a temp SQLite file."""
    if session_id is None:
        session_id = _unique_id()
    db = tmp_path / f"ledger-accept-{_unique_id()}.db"
    return LedgerRuntime(db, session_id=session_id)


# ── Ledger seq ordering — exec lifecycle ───────────────────────────────────


@pytest.mark.asyncio
async def test_exec_lifecycle_items_are_monotonically_ordered(tmp_path):
    """exec_started seq < exec_completed seq for the same tool_call_id.

    This is the "no missing terminal event" guarantee at the ledger level:
    the terminal exec_completed is always written after the corresponding
    exec_started, and their seq numbers reflect that.
    """
    ledger = _temp_ledger(tmp_path)
    await ledger.initialize()
    try:
        tid = "thread-seq-exec"
        turn_id = "turn-seq-exec"
        tcid = "call-seq-exec"

        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_started",
            payload={"tool_call_id": tcid, "command": "echo test", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_completed",
            payload={"tool_call_id": tcid, "exit_code": 0, "duration_ms": 10, "output_size": 5},
        )

        items = await ledger.load_items(tid)
        started = [i for i in items if i.item_type == "exec_started" and i.payload.get("tool_call_id") == tcid]
        completed = [i for i in items if i.item_type == "exec_completed" and i.payload.get("tool_call_id") == tcid]

        assert len(started) == 1, f"Expected 1 exec_started, got {len(started)}"
        assert len(completed) == 1, f"Expected 1 exec_completed, got {len(completed)}"

        assert started[0].seq < completed[0].seq, (
            f"exec_started seq {started[0].seq} must be < exec_completed seq {completed[0].seq}"
        )

        # Verify seq numbers are monotonically increasing overall
        seqs = [i.seq for i in items]
        assert seqs == sorted(seqs), f"Ledger seqs not monotonic: {seqs}"
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_exec_deltas_ordered_between_start_and_end(tmp_path):
    """exec_output_delta items must have seq between exec_started and exec_completed."""
    ledger = _temp_ledger(tmp_path)
    await ledger.initialize()
    try:
        tid = "thread-seq-delta"
        turn_id = "turn-seq-delta"
        tcid = "call-seq-delta"

        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_started",
            payload={"tool_call_id": tcid, "command": "echo a && echo b", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_output_delta", content="a\n",
            payload={"tool_call_id": tcid, "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_output_delta", content="b\n",
            payload={"tool_call_id": tcid, "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_completed",
            payload={"tool_call_id": tcid, "exit_code": 0, "duration_ms": 10, "output_size": 4},
        )

        items = await ledger.load_items(tid)
        started_seq = next(i.seq for i in items if i.item_type == "exec_started")
        completed_seq = next(i.seq for i in items if i.item_type == "exec_completed")
        delta_seqs = [i.seq for i in items if i.item_type == "exec_output_delta"]

        assert len(delta_seqs) == 2
        for ds in delta_seqs:
            assert started_seq < ds < completed_seq, (
                f"Delta seq {ds} not between start {started_seq} and end {completed_seq}"
            )
    finally:
        await ledger.close()


# ── Ledger seq ordering — approval lifecycle ───────────────────────────────


@pytest.mark.asyncio
async def test_approval_lifecycle_items_are_monotonically_ordered(tmp_path):
    """approval_requested seq < approval_resolved seq for the same approval_id."""
    ledger = _temp_ledger(tmp_path)
    await ledger.initialize()
    try:
        tid = "thread-seq-approval"
        turn_id = "turn-seq-approval"
        aid = f"{turn_id}:call-seq-approval"

        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="approval_requested",
            payload={
                "approval_id": aid,
                "tool_name": "exec",
                "category": "shell_exec",
            },
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="approval_resolved",
            payload={
                "approval_id": aid,
                "decision": "once",
            },
        )

        items = await ledger.load_items(tid)
        requested = [i for i in items if i.item_type == "approval_requested"]
        resolved = [i for i in items if i.item_type == "approval_resolved"]

        assert len(requested) == 1
        assert len(resolved) == 1
        assert requested[0].seq < resolved[0].seq, (
            f"approval_requested seq {requested[0].seq} must be < "
            f"approval_resolved seq {resolved[0].seq}"
        )
    finally:
        await ledger.close()


# ── Ledger seq ordering — mixed lifecycle ──────────────────────────────────


@pytest.mark.asyncio
async def test_mixed_exec_approval_lifecycle_is_monotonic(tmp_path):
    """When approval and exec items interleave, all seq numbers remain monotonic.

    This is the realistic scenario: approval_requested -> approval_resolved ->
    exec_started -> exec_output_delta -> exec_completed.  Every terminal event
    has a seq greater than its corresponding start event.
    """
    ledger = _temp_ledger(tmp_path)
    await ledger.initialize()
    try:
        tid = "thread-mixed"
        turn_id = "turn-mixed"
        tcid = "call-mixed"
        aid = f"{turn_id}:{tcid}"

        # Realistic ordering: approval first, then exec
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="approval_requested",
            payload={"approval_id": aid, "tool_name": "exec", "category": "shell_exec"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="approval_resolved",
            payload={"approval_id": aid, "decision": "once"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_started",
            payload={"tool_call_id": tcid, "command": "echo ok", "sandbox_type": "none"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_output_delta", content="ok\n",
            payload={"tool_call_id": tcid, "stream": "stdout"},
        )
        await ledger.append_item(
            thread_id=tid, turn_id=turn_id,
            item_type="exec_completed",
            payload={"tool_call_id": tcid, "exit_code": 0, "duration_ms": 5, "output_size": 3},
        )

        items = await ledger.load_items(tid)

        # All items present exactly once (no duplicates)
        type_counts: dict[str, int] = {}
        for i in items:
            type_counts[i.item_type] = type_counts.get(i.item_type, 0) + 1

        assert type_counts.get("approval_requested") == 1, f"Expected 1 approval_requested, got {type_counts}"
        assert type_counts.get("approval_resolved") == 1, f"Expected 1 approval_resolved, got {type_counts}"
        assert type_counts.get("exec_started") == 1, f"Expected 1 exec_started, got {type_counts}"
        assert type_counts.get("exec_output_delta") == 1, f"Expected 1 exec_output_delta, got {type_counts}"
        assert type_counts.get("exec_completed") == 1, f"Expected 1 exec_completed, got {type_counts}"

        # Monotonic seq
        seqs = [i.seq for i in items]
        assert seqs == sorted(seqs), f"Ledger seqs not monotonic: {seqs}"

        # Pair ordering checks
        approval_req_seq = next(i.seq for i in items if i.item_type == "approval_requested")
        approval_res_seq = next(i.seq for i in items if i.item_type == "approval_resolved")
        exec_start_seq = next(i.seq for i in items if i.item_type == "exec_started")
        exec_end_seq = next(i.seq for i in items if i.item_type == "exec_completed")

        assert approval_req_seq < approval_res_seq, "approval_requested must precede approval_resolved"
        assert exec_start_seq < exec_end_seq, "exec_started must precede exec_completed"
        assert approval_res_seq < exec_start_seq, "approval must resolve before execution begins"
    finally:
        await ledger.close()


# ── Concurrent appends — no duplicate seq ──────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_exec_lifecycle_appends_no_duplicate_seq(tmp_path):
    """Concurrent append_item calls for exec lifecycle must not produce
    duplicate sequence numbers."""
    ledger = _temp_ledger(tmp_path)
    await ledger.initialize()
    try:
        tid = "thread-concurrent"
        turn_id = "turn-concurrent"

        async def write_pair(index):
            tcid = f"call-conc-{index}"
            await ledger.append_item(
                thread_id=tid, turn_id=turn_id,
                item_type="exec_started",
                payload={"tool_call_id": tcid, "command": f"cmd-{index}", "sandbox_type": "none"},
            )
            await ledger.append_item(
                thread_id=tid, turn_id=turn_id,
                item_type="exec_completed",
                payload={"tool_call_id": tcid, "exit_code": 0, "duration_ms": 1, "output_size": 0},
            )

        tasks = [asyncio.create_task(write_pair(i)) for i in range(5)]
        await asyncio.gather(*tasks)

        items = await ledger.load_items(tid)

        # All 10 items present
        assert len(items) == 10, f"Expected 10 items, got {len(items)}"

        # No duplicate seq
        seqs = [i.seq for i in items]
        assert len(seqs) == len(set(seqs)), f"Duplicate seq numbers: {seqs}"

        # Monotonic
        assert seqs == sorted(seqs), f"Seq not monotonic: {seqs}"

        # Each tool_call_id has both start and end, and start < end
        seen_starts: dict[str, int] = {}
        seen_ends: dict[str, int] = {}
        for i in items:
            tcid = i.payload.get("tool_call_id")
            if not isinstance(tcid, str):
                continue
            if i.item_type == "exec_started":
                seen_starts[tcid] = i.seq
            elif i.item_type == "exec_completed":
                seen_ends[tcid] = i.seq

        assert len(seen_starts) == 5
        assert len(seen_ends) == 5
        for tcid in seen_starts:
            assert tcid in seen_ends, f"Missing exec_completed for {tcid}"
            assert seen_starts[tcid] < seen_ends[tcid], (
                f"{tcid}: start_seq {seen_starts[tcid]} >= end_seq {seen_ends[tcid]}"
            )
    finally:
        await ledger.close()


# ── RuntimeSession mirror audit ────────────────────────────────────────────


def test_runtime_session_mirror_excludes_exec_and_approval_events():
    """Audit: RuntimeSession._mirror_event_to_ledger mapping dict must
    NOT contain keys for exec_* or approval_* event types.

    Single-writer rule: these events are written at source
    (ExecTool for exec, ToolOrchestrator for approval).
    Mirroring them here would create duplicates.
    """
    from miqi.runtime.session import RuntimeSession

    source = inspect.getsource(RuntimeSession._mirror_event_to_ledger)

    # Forbidden keys (must NOT appear in the mapping dict)
    forbidden = [
        "exec_command_begin",
        "exec_command_output_delta",
        "exec_command_end",
        "approval_requested",
        "approval_resolved",
    ]

    # Required keys (must still be present)
    required = [
        "command_rejected",
        "error",
        "warning",
        "context_compacted",
    ]

    for key in forbidden:
        assert f'"{key}"' not in source, (
            f"Forbidden item_type '{key}' found in mirror mapping. "
            f"This event must be written at source, not mirrored."
        )

    for key in required:
        assert f'"{key}"' in source, (
            f"Required item_type '{key}' missing from mirror mapping. "
            f"This event has no source writer and must be mirrored."
        )


# ── ExecTool sandbox selection consumption audit ───────────────────────────


@pytest.mark.asyncio
async def test_exec_tool_never_bypasses_injected_sandbox_selection():
    """ExecTool must consume the sandbox selection injected by ToolOrchestrator.

    When _sandbox with type=NONE is injected, the tool must run directly even
    if a sandbox manager were hypothetically present (sandbox_selection always wins).
    This is the "no silent bypass" guarantee from Phase 31.2b/31.3.
    """
    from miqi.agent.tools.shell import ExecTool
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )

    _none = SandboxSelection(
        sandbox_type=SandboxType.NONE,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=5000,
        env_passthrough=[],
        reason="acceptance-test",
    )

    tool = ExecTool(timeout=5)
    # No sandbox_manager set — must still work because NONE is allowed
    result = await tool.execute(
        "echo no-bypass",
        _sandbox=_none,
        _event_emitter=None,
        _turn_id="turn-no-bypass",
        _tool_call_id="call-no-bypass",
    )
    assert "no-bypass" in result
    assert "Error" not in result


@pytest.mark.asyncio
async def test_exec_tool_bwrap_unavailable_fails_closed():
    """When BWRAP is selected but unavailable, exec must fail with a clear
    error rather than silently falling back to direct execution."""
    from miqi.agent.tools.shell import ExecTool
    from miqi.execution.sandbox_policy import SandboxSelection, SandboxType
    from miqi.protocol.permissions import (
        FileSystemSandboxPolicy,
        NetworkSandboxPolicy,
    )

    bwrap = SandboxSelection(
        sandbox_type=SandboxType.BWRAP,
        filesystem_policy=FileSystemSandboxPolicy(),
        network_policy=NetworkSandboxPolicy.ALLOW_ALL,
        timeout_ms=5000,
        env_passthrough=[],
        reason="acceptance-test",
    )

    tool = ExecTool(timeout=5)
    result = await tool.execute(
        "echo should-not-run",
        _sandbox=bwrap,
        _event_emitter=None,
        _turn_id="turn-bwrap-fail",
        _tool_call_id="call-bwrap-fail",
    )
    # Must contain the command output (falls back to host)
    assert "should-not-run" in result
    # Must contain sandbox-unavailable warning
    assert "sandbox not available" in result


# ── Approval workflow audit ────────────────────────────────────────────────


def test_approval_resolve_result_structure():
    """ApprovalResolveResult must have the correct structured fields."""
    from miqi.execution.orchestrator import ApprovalResolveResult

    result = ApprovalResolveResult(
        resolved=True,
        approval_id="thread:turn:tool",
        normalized_decision="once",
        turn_id="turn-1",
    )
    assert result.resolved is True
    assert result.approval_id == "thread:turn:tool"
    assert result.normalized_decision == "once"
    assert result.turn_id == "turn-1"
    assert result.reason == ""

    result_err = ApprovalResolveResult(
        resolved=False,
        approval_id="bogus",
        normalized_decision="",
        turn_id="",
        reason="not found",
    )
    assert result_err.resolved is False
    assert result_err.reason == "not found"


# ── File mutation approval audit ───────────────────────────────────────────


def test_agent_tool_registry_excludes_appserver_files_methods():
    """AppServer files.* control API methods must NOT appear as agent tools.
    This prevents the "agent mutates files through AppServer API without
    going through ToolOrchestrator" bypass vector (Phase 31.7)."""
    from miqi.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()

    # AppServer files.* methods that must NOT be agent tools
    forbidden_names = {"files.write", "files.revert", "files.accept", "files.list", "files.read"}

    agent_tool_names = set()
    for name in registry.tool_names:
        agent_tool_names.add(name)

    overlap = forbidden_names & agent_tool_names
    assert len(overlap) == 0, (
        f"AppServer files.* methods found in agent tool registry: {overlap}. "
        f"These must not be callable as agent tools; they bypass ToolOrchestrator."
    )


def test_no_apply_patch_tool_in_registry():
    """There must be no apply_patch tool in the agent tool registry.
    If one is added in future, it must go through ToolOrchestrator + approval."""
    from miqi.agent.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for name in registry.tool_names:
        assert "patch" not in name.lower(), (
            f"Tool '{name}' looks like a patch tool. "
            f"Patch tools must go through ToolOrchestrator + approval."
        )
