"""Reproduction for #886: after manual stop + fresh retry succeeds, does the
interrupted turn's snapshot survive in the backend history?

Scenario (desktop chat.send flow, driven at the RuntimeSession level):
1. submit a UserMessage (turn T1) — user message persisted, turn streams
2. submit AbortTurn (user clicks 停止生成) — turn cancelled, execution snapshot saved
3. submit the SAME content again as a fresh turn T2 (用户点击重新生成),
   with NO resume_turn_id
4. T2 completes successfully
5. Read history_runtime snapshots + history items + SessionManager messages
   (the same stores sessions.get reads) — check the interrupted round survived

Expected per issue: the interrupted round's node must REMAIN in history
(snapshot kept, user message kept).
"""

import asyncio

import pytest


class StreamProvider:
    """Streams deltas then completes after a short delay.

    T1 is aborted while streaming (before completion); the retry T2 runs the
    same provider to completion.
    """

    def __init__(self, delay_s: float = 0.5):
        self.delay_s = delay_s
        self.calls = 0

    def get_default_model(self):
        return "test-model"

    async def stream_chat(self, **kwargs):
        from miqi.providers.base import LLMResponse, LLMStreamEvent

        self.calls += 1
        yield LLMStreamEvent(kind="content_delta", delta="部分回答内容")
        await asyncio.sleep(self.delay_s)
        yield LLMStreamEvent(kind="content_delta", delta="，继续输出")
        await asyncio.sleep(self.delay_s)
        yield LLMStreamEvent(kind="completed", response=LLMResponse(
            content="部分回答内容，继续输出重试成功", finish_reason="stop",
        ))


async def _drain_until(runtime, ev_name: str, *, timeout_s: float = 8.0) -> list:
    """Drain runtime events until one of class *ev_name* is seen, or timeout."""
    seen: list = []
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        ev = await runtime.next_event(timeout=0.5)
        if ev is None:
            continue
        seen.append(ev)
        if ev.__class__.__name__ == ev_name:
            return seen
    return seen


@pytest.mark.asyncio
async def test_interrupted_turn_survives_fresh_retry(tmp_path, fake_config):
    from miqi.protocol.commands import AbortTurn, UserMessage
    from miqi.runtime.session import RuntimeSession

    provider = StreamProvider(delay_s=0.5)
    runtime = RuntimeSession.create(
        config=fake_config,
        provider=provider,
        session_id="c1:s1",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()
    hr = runtime.services.history_runtime
    assert hr is not None

    thread_id = "thread-886"
    content = "请生成长时间运行的任务"

    try:
        # ── Turn T1: long task ──────────────────────────────────────────
        await runtime.submit(UserMessage(content=content, thread_id=thread_id, mode="edit"))

        # Wait until the model stream actually starts (turn_runner.run is
        # inside the stream loop) — mirrors a user letting the task run.
        t1_events = await _drain_until(runtime, "AgentMessageDeltaEvent")
        assert t1_events and t1_events[-1].__class__.__name__ == "AgentMessageDeltaEvent", (
            "T1 never started streaming"
        )

        # ── Stop: user clicks 停止生成 ──────────────────────────────────
        await runtime.submit(AbortTurn(thread_id=thread_id))

        t1_settle = await _drain_until(runtime, "TurnAbortedEvent")
        assert t1_settle and t1_settle[-1].__class__.__name__ == "TurnAbortedEvent", (
            f"T1 never emitted TurnAbortedEvent: {[e.__class__.__name__ for e in t1_settle]}"
        )

        # The interrupted snapshot MUST exist now.
        interrupted_after_abort = await hr.get_interrupted_snapshots()
        assert len(interrupted_after_abort) == 1, (
            f"T1 snapshot missing after abort: {interrupted_after_abort}"
        )

        # ── Retry: user clicks 重新生成 (fresh turn, no resume_turn_id) ─
        await runtime.submit(UserMessage(content=content, thread_id=thread_id, mode="edit"))

        # Wait for the retry turn to complete (TurnCompleteEvent).
        t2_events = await _drain_until(runtime, "TurnCompleteEvent")
        assert t2_events and t2_events[-1].__class__.__name__ == "TurnCompleteEvent", (
            f"retry turn never completed: {[e.__class__.__name__ for e in t2_events]}"
        )

        # ── Verify history state (what sessions.get would return) ───────
        snapshots = await hr.get_interrupted_snapshots()
        assert len(snapshots) == 1, (
            "#886: interrupted snapshot vanished after fresh retry succeeded. "
            f"interrupted_turns={snapshots}"
        )

        items = await hr.load_items(thread_id)
        roles = [i.role for i in items]
        assert roles.count("user") == 2, (
            f"expected user msg from BOTH the interrupted round and the retry, got {roles}"
        )
        assert roles.count("assistant") == 1, (
            f"retry's assistant answer missing: {roles}"
        )

        # sessions.get reads messages from SessionManager JSONL — verify the
        # interrupted round's user message survived there too.
        from miqi.session.manager import SessionManager

        sm = SessionManager(fake_config.workspace_path)
        sess = sm.get_or_create("s1")
        jsonl_roles = [m.get("role") for m in sess.messages]
        assert jsonl_roles.count("user") == 2, (
            f"JSONL user msgs missing (interrupted round lost): {jsonl_roles}"
        )
        assert jsonl_roles.count("assistant") == 1, (
            f"JSONL assistant msg missing: {jsonl_roles}"
        )
    finally:
        await runtime.stop()


@pytest.mark.asyncio
async def test_resume_completing_deletes_only_that_snapshot(tmp_path, fake_config):
    """Regression guard: deleting a snapshot on resume-completion must not
    wipe unrelated interrupted snapshots in the same session (#886 class)."""
    from miqi.runtime.session import RuntimeSession

    provider = StreamProvider(delay_s=0.1)
    runtime = RuntimeSession.create(
        config=fake_config,
        provider=provider,
        session_id="c1:s1",
        workspace=fake_config.workspace_path,
    )
    await runtime.start()
    hr = runtime.services.history_runtime

    try:
        await hr.upsert_snapshot("turn-other-1", "thread-886", status="interrupted",
                                 assistant_content="半截A")
        await hr.upsert_snapshot("turn-other-2", "thread-886", status="interrupted",
                                 assistant_content="半截B")
        await hr.upsert_snapshot("turn-resume", "thread-886", status="interrupted",
                                 assistant_content="半截C")
        await hr.delete_snapshot("turn-resume")

        snapshots = await hr.get_interrupted_snapshots()
        ids = sorted(s["turn_id"] for s in snapshots)
        assert ids == ["turn-other-1", "turn-other-2"], ids
    finally:
        await runtime.stop()
