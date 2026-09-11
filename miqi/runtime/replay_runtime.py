"""Replay runtime — reconstruct turn timelines and provider messages from ledger.

Phase 25: Wraps LedgerRuntime to provide reconstruction APIs. Can rebuild
turn timelines (user input, deltas, tool calls, exec commands, errors),
merge streaming deltas into final text, and produce provider-compatible
message lists. All queries are session-scoped via the underlying ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from miqi.runtime.ledger_runtime import LedgerItem, LedgerRuntime

# ── Replay data types ────────────────────────────────────────────────────


@dataclass
class ToolCallReplay:
    """Reconstructed view of a single tool call from ledger items."""

    tool_call_id: str
    name: str
    arguments: dict[str, Any] | None = None
    result: str | None = None
    status: str = "pending"    # "completed" | "error" | "pending"
    duration_ms: int = 0
    retry_count: int = 0
    permission_verdict: str | None = None
    sandbox_type: str | None = None


@dataclass
class ExecCommandReplay:
    """Reconstructed view of a single exec (shell) command from ledger items."""

    tool_call_id: str
    command: str = ""
    cwd: str = ""
    sandbox_type: str = ""
    output: str = ""           # merged from output deltas
    exit_code: int | None = None
    duration_ms: int = 0
    output_size: int = 0
    # Phase 31.8: terminal status flags from ledger
    cancelled: bool = False
    timed_out: bool = False
    status: str = "pending"    # "completed" | "pending" | "error" | "timed_out" | "cancelled"


@dataclass
class ApprovalEventReplay:
    """Reconstructed view of an approval lifecycle event from ledger items.

    Phase 31.8: An approval goes through: requested → resolved (once/session/
    always/deny/timeout/abort).  Each state change is a separate ledger item;
    this dataclass captures the terminal state plus the initial request.
    """

    approval_id: str
    tool_call_id: str = ""
    tool_name: str = ""
    category: str = ""
    description: str = ""
    allow_permanent: bool = False
    request_seq: int = 0                     # seq at request time
    decision: str = "pending"                # "pending" | "once" | "session" | "always" | "deny" | "timeout" | "abort"
    resolved_seq: int | None = None          # seq at resolution time (None if still pending)


@dataclass
class TurnTimeline:
    """Full turn reconstruction from ledger items.

    Covers the complete lifecycle: user input, streaming deltas,
    tool calls, exec commands, approval events, errors, and final status.
    """

    turn_id: str
    thread_id: str
    status: str = "incomplete"  # "completed" | "aborted" | "error" | "incomplete"
    user_input: str | None = None
    assistant_text: str = ""
    assistant_deltas: list[str] = field(default_factory=list)
    reasoning_deltas: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallReplay] = field(default_factory=list)
    exec_commands: list[ExecCommandReplay] = field(default_factory=list)
    # Phase 31.8: approval lifecycle events
    approval_events: list[ApprovalEventReplay] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    started_at: float | None = None
    completed_at: float | None = None


# ── ReplayRuntime ────────────────────────────────────────────────────────


class ReplayRuntime:
    """Ledger-backed turn and message reconstruction.

    Wraps a LedgerRuntime instance and provides:
      - list_turns(thread_id) → list[str]
      - get_turn_timeline(thread_id, turn_id) → TurnTimeline | None
      - get_thread_timeline(thread_id) → list[TurnTimeline]
      - get_provider_messages(thread_id) → list[dict]

    All queries are session-scoped — the underlying ledger already
    filters by session_id.
    """

    def __init__(self, ledger: LedgerRuntime) -> None:
        self._ledger = ledger

    # ── internal: load items respecting rollback markers ──────────────────

    async def _load_items(self, thread_id: str) -> list[LedgerItem]:
        """Load ledger items, using effective-items when available.

        Phase 36: rollback markers are respected via load_effective_items()
        which filters out rolled-back turns.
        """
        if hasattr(self._ledger, "load_effective_items"):
            return await self._ledger.load_effective_items(thread_id)
        return await self._ledger.load_items(thread_id)

    # ── public API ───────────────────────────────────────────────────────

    async def list_turns(self, thread_id: str) -> list[str]:
        """Return turn_ids in ledger sequence order for a thread."""
        return self.list_turn_ids_from_items(await self._load_items(thread_id))

    # ── public static helpers ─────────────────────────────────────────────

    @staticmethod
    def list_turn_ids_from_items(items: list[LedgerItem]) -> list[str]:
        """Return turn ids ordered by first ledger sequence number."""
        seen: dict[str, int] = {}
        for item in items:
            if item.turn_id and item.turn_id not in seen:
                seen[item.turn_id] = item.seq
        return sorted(seen.keys(), key=lambda tid: seen[tid])

    @staticmethod
    def build_timeline_from_items(
        thread_id: str,
        turn_id: str,
        items: list[LedgerItem],
    ) -> TurnTimeline:
        """Build a turn timeline from already-loaded ledger items.

        This is intentionally public so stored projections and debug tools do
        not need to instantiate ReplayRuntime or call private methods.
        """
        runtime = ReplayRuntime.__new__(ReplayRuntime)
        return runtime._build_timeline(thread_id, turn_id, items)

    async def get_turn_timeline(
        self, thread_id: str, turn_id: str,
    ) -> TurnTimeline | None:
        """Reconstruct a single turn's full timeline from ledger items.

        Returns None if no items exist for this turn_id in this thread.
        """
        items = await self._load_items(thread_id)
        turn_items = [it for it in items if it.turn_id == turn_id]
        if not turn_items:
            return None
        return self._build_timeline(thread_id, turn_id, turn_items)

    async def get_thread_timeline(self, thread_id: str) -> list[TurnTimeline]:
        """Reconstruct all turns in a thread, ordered by first occurrence."""
        turn_ids = await self.list_turns(thread_id)
        timelines: list[TurnTimeline] = []
        for tid in turn_ids:
            timeline = await self.get_turn_timeline(thread_id, tid)
            if timeline is not None:
                timelines.append(timeline)
        return timelines

    async def get_provider_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """Return provider-compatible message dicts from ledger items.

        Delegates to LedgerRuntime.load_provider_messages().
        """
        return await self._ledger.load_provider_messages(thread_id)

    # ── internal reconstruction ──────────────────────────────────────────

    def _build_timeline(
        self, thread_id: str, turn_id: str, items: list[LedgerItem],
    ) -> TurnTimeline:
        timeline = TurnTimeline(turn_id=turn_id, thread_id=thread_id)

        # Mutable accumulators keyed by tool_call_id
        pending_tools: dict[str, ToolCallReplay] = {}
        pending_execs: dict[str, ExecCommandReplay] = {}
        # Phase 31.8: approval events keyed by approval_id
        pending_approvals: dict[str, ApprovalEventReplay] = {}

        for item in items:
            payload = self._safe_payload(item)

            if item.item_type == "turn_started":
                timeline.started_at = item.created_at

            elif item.item_type == "message":
                if item.role == "user" and timeline.user_input is None:
                    timeline.user_input = item.content

            elif item.item_type == "assistant_delta":
                timeline.assistant_deltas.append(item.content)

            elif item.item_type == "reasoning_delta":
                timeline.reasoning_deltas.append(item.content)

            elif item.item_type == "tool_call_started":
                tc_id = payload.get("tool_call_id", "")
                if not tc_id:
                    continue  # corrupt or incomplete item
                pending_tools[tc_id] = ToolCallReplay(
                    tool_call_id=tc_id,
                    name=payload.get("name", ""),
                    arguments=payload.get("arguments"),
                )

            elif item.item_type == "tool_call_completed":
                tc_id = payload.get("tool_call_id", "")
                if not tc_id:
                    continue  # corrupt or incomplete item
                tc = pending_tools.pop(tc_id, ToolCallReplay(
                    tool_call_id=tc_id,
                    name="",
                ))
                tc.status = "completed"
                tc.result = payload.get("result") or ""
                tc.duration_ms = payload.get("duration_ms", 0)
                tc.retry_count = payload.get("retry_count", 0)
                tc.permission_verdict = payload.get("permission_verdict")
                tc.sandbox_type = payload.get("sandbox_type")
                timeline.tool_calls.append(tc)

            # ── Phase 31.8: approval lifecycle events ───────────────────

            elif item.item_type == "approval_requested":
                aid = payload.get("approval_id", "")
                if not aid:
                    continue
                pending_approvals[aid] = ApprovalEventReplay(
                    approval_id=aid,
                    tool_call_id=payload.get("tool_call_id", ""),
                    tool_name=payload.get("tool_name", ""),
                    category=payload.get("category", ""),
                    description=payload.get("description", ""),
                    allow_permanent=payload.get("allow_permanent", False),
                    request_seq=item.seq,
                )

            elif item.item_type == "approval_resolved":
                aid = payload.get("approval_id", "")
                decision = payload.get("decision", "deny")
                if not aid:
                    continue
                ap = pending_approvals.pop(aid, ApprovalEventReplay(
                    approval_id=aid,
                    tool_call_id=payload.get("tool_call_id", ""),
                    tool_name=payload.get("tool_name", ""),
                    category=payload.get("category", ""),
                ))
                ap.decision = decision
                ap.resolved_seq = item.seq
                timeline.approval_events.append(ap)

            # ── Exec lifecycle (may now carry terminal flags) ────────────

            elif item.item_type == "exec_started":
                tc_id = payload.get("tool_call_id", "")
                if not tc_id:
                    continue  # corrupt or incomplete item
                pending_execs[tc_id] = ExecCommandReplay(
                    tool_call_id=tc_id,
                    command=payload.get("command", ""),
                    cwd=payload.get("cwd", ""),
                    sandbox_type=payload.get("sandbox_type", ""),
                )

            elif item.item_type == "exec_output_delta":
                tc_id = payload.get("tool_call_id", "")
                ex = pending_execs.get(tc_id)
                if ex is not None:
                    ex.output += item.content

            elif item.item_type == "exec_completed":
                tc_id = payload.get("tool_call_id", "")
                if not tc_id:
                    continue  # corrupt or incomplete item
                ex = pending_execs.pop(tc_id, ExecCommandReplay(
                    tool_call_id=tc_id,
                ))
                ex.exit_code = payload.get("exit_code")
                ex.duration_ms = payload.get("duration_ms", 0)
                ex.output_size = payload.get("output_size", 0)
                # Phase 31.8: terminal status flags
                ex.cancelled = payload.get("cancelled", False)
                ex.timed_out = payload.get("timed_out", False)
                # Derive status from flags
                if ex.timed_out:
                    ex.status = "timed_out"
                elif ex.cancelled:
                    ex.status = "cancelled"
                elif ex.exit_code is not None and ex.exit_code != 0:
                    ex.status = "error"
                else:
                    ex.status = "completed"
                timeline.exec_commands.append(ex)

            elif item.item_type == "error":
                timeline.errors.append(payload)

            elif item.item_type == "turn_completed":
                timeline.status = "completed"
                timeline.completed_at = item.created_at

            elif item.item_type == "turn_aborted":
                timeline.status = "aborted"
                timeline.completed_at = item.created_at

        # ── post-processing ──────────────────────────────────────────────

        # Merge assistant deltas into final text
        if timeline.assistant_deltas:
            timeline.assistant_text = "".join(timeline.assistant_deltas)
        # Fall back to final assistant message if no deltas
        if not timeline.assistant_text:
            for item in items:
                if item.item_type == "message" and item.role == "assistant":
                    timeline.assistant_text = item.content
                    break

        # Dangling tool starts → mark as pending
        for tc in pending_tools.values():
            tc.status = "pending"
            timeline.tool_calls.append(tc)

        # Dangling exec starts → mark as pending
        for ex in pending_execs.values():
            ex.status = "pending"
            timeline.exec_commands.append(ex)

        # Phase 31.8: dangling approval requests → mark as pending
        for ap in pending_approvals.values():
            ap.decision = "pending"
            timeline.approval_events.append(ap)

        return timeline

    @staticmethod
    def _safe_payload(item: LedgerItem) -> dict[str, Any]:
        """Return item.payload, gracefully handling corrupt payload_json."""
        try:
            return item.payload
        except Exception:
            logger.warning(
                "ReplayRuntime: corrupt payload on item {} (type={})",
                item.item_id, item.item_type,
            )
            return {}
