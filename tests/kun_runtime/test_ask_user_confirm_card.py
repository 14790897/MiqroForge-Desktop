"""Issue #646 — ask_user_confirm_card: tool contract, tool-host interception,
storm-breaker exemption, never-parallel classification, contracts, and the
user-input gate timeout."""

from __future__ import annotations

import asyncio
import json

import pytest

from miqi.agent.tools.ask_user_confirm import (
    DEFAULT_CHOICES,
    AskUserConfirmCardTool,
)
from miqi.agent.tools.registry import _NEVER_PARALLEL_TOOLS, ToolRegistry
from miqi.agent.user_input_resolver import make_resolver
from miqi.kun_runtime.contracts import TurnStatus, UserInputItem, UserInputRequestedEvent
from miqi.kun_runtime.tool_host import (
    ASK_USER_CONFIRM_TOOL,
    MiQiToolHost,
    ToolCallLike,
    ToolHostContext,
)
from miqi.kun_runtime.tool_storm_breaker import STORM_EXEMPT_TOOLS, ToolStormBreaker
from miqi.kun_runtime.user_input_gate import UserInputGate


async def _announce(target: list, value: str) -> None:
    """Async on_pending helper for gate queue tests."""
    target.append(value)


# ═══════════════════════════════════════════════════════════════════════════════
# Tool contract
# ═══════════════════════════════════════════════════════════════════════════════


class TestToolContract:
    def test_name_and_schema(self):
        tool = AskUserConfirmCardTool()
        assert tool.name == "ask_user_confirm_card"
        schema = tool.to_schema()["function"]
        params = schema["parameters"]["properties"]
        assert set(schema["parameters"]["required"]) == {"title", "message"}
        for key in ("title", "message", "steps", "choices", "allow_remember_choice", "timeout_seconds"):
            assert key in params
        # 默认超时 120s
        assert params["timeout_seconds"]["default"] == 120
        # 中文描述：模型必须能理解这是 AI 主动发起的人机握手
        assert "确认" in schema["description"]

    def test_fallback_execute_returns_error(self):
        """No user-input channel wired → must NOT fabricate a decision."""
        tool = AskUserConfirmCardTool()
        result = asyncio.run(tool.execute(title="确认", message="可以吗"))
        assert result.startswith("Error")
        assert "不要假设用户已同意" in result


class TestNormalizeArgs:
    def test_defaults(self):
        payload = AskUserConfirmCardTool.normalize_args({"title": "确认执行方案？", "message": "4 个步骤"})
        assert payload["title"] == "确认执行方案？"
        assert payload["message"] == "4 个步骤"
        assert payload["choices"] == DEFAULT_CHOICES
        assert payload["timeout_seconds"] == 120
        assert payload["allow_remember_choice"] is False
        assert payload["steps"] == []

    def test_choices_structured(self):
        payload = AskUserConfirmCardTool.normalize_args({
            "title": "上传？",
            "message": "确认上传到 MiQroForge",
            "choices": [{"id": "confirm", "label": "确认上传"}, {"id": "cancel", "label": "取消"}],
            "timeout_seconds": 30,
            "allow_remember_choice": True,
        })
        assert payload["choices"] == [{"id": "confirm", "label": "确认上传"}, {"id": "cancel", "label": "取消"}]
        assert payload["timeout_seconds"] == 30
        assert payload["allow_remember_choice"] is True

    def test_bad_choices_fallback_to_default(self):
        payload = AskUserConfirmCardTool.normalize_args({"title": "t", "message": "m", "choices": []})
        assert payload["choices"] == DEFAULT_CHOICES

    def test_steps_get_ids(self):
        payload = AskUserConfirmCardTool.normalize_args({
            "title": "t", "message": "m",
            "steps": [{"title": "搜索论文"}, {"id": "query_price", "title": "查价格"}],
        })
        assert payload["steps"] == [
            {"id": "step_0", "title": "搜索论文"},
            {"id": "query_price", "title": "查价格"},
        ]


class TestBuildResult:
    def test_submitted_confirm(self):
        out = AskUserConfirmCardTool.build_result({
            "status": "submitted",
            "answers": {"choice_id": "confirm", "choice_label": "确认执行"},
        })
        data = json.loads(out)
        assert data["status"] == "confirmed"
        assert data["choice_id"] == "confirm"
        assert data["choice_label"] == "确认执行"

    def test_cancelled(self):
        out = AskUserConfirmCardTool.build_result({"status": "cancelled"})
        data = json.loads(out)
        assert data["status"] == "cancelled"
        assert data["choice_id"] == ""


# ═══════════════════════════════════════════════════════════════════════════════
# Tool-host interception
# ═══════════════════════════════════════════════════════════════════════════════


def _ctx(await_user_input=None) -> ToolHostContext:
    return ToolHostContext(
        thread_id="thr_1",
        turn_id="turn_1",
        workspace="/tmp/ws",
        await_user_input=await_user_input,
    )


class TestToolHostInterception:
    def test_routes_through_await_user_input(self):
        registry = ToolRegistry()
        registry.register(AskUserConfirmCardTool())

        async def fake_gate(payload):
            assert payload["title"] == "确认执行方案？"
            assert payload["steps"][0]["id"] == "search_papers"
            return {"status": "submitted", "answers": {"choice_id": "confirm", "choice_label": "确认执行"}}

        host = MiQiToolHost(registry)
        call = ToolCallLike(call_id="call_1", tool_name=ASK_USER_CONFIRM_TOOL, arguments={
            "title": "确认执行方案？",
            "message": "4 个步骤",
            "steps": [{"id": "search_papers", "title": "搜索论文"}],
        })
        result = asyncio.run(host.execute(call, _ctx(await_user_input=fake_gate)))
        item = result.item
        assert item["status"] == "completed"
        assert item["isError"] is False
        data = json.loads(item["output"])
        assert data["status"] == "confirmed"
        assert data["choice_id"] == "confirm"

    def test_cancelled_choice(self):
        registry = ToolRegistry()
        registry.register(AskUserConfirmCardTool())

        async def fake_gate(payload):
            return {"status": "cancelled", "reason": "timeout"}

        host = MiQiToolHost(registry)
        call = ToolCallLike(call_id="call_2", tool_name=ASK_USER_CONFIRM_TOOL, arguments={
            "title": "上传？", "message": "确认上传",
        })
        result = asyncio.run(host.execute(call, _ctx(await_user_input=fake_gate)))
        data = json.loads(result.item["output"])
        assert data["status"] == "cancelled"
        assert data["reason"] == "timeout"

    def test_no_channel_falls_back_to_tool_error(self):
        """No await_user_input wired → tool's own execute() returns error."""
        registry = ToolRegistry()
        registry.register(AskUserConfirmCardTool())
        host = MiQiToolHost(registry)
        call = ToolCallLike(call_id="call_3", tool_name=ASK_USER_CONFIRM_TOOL, arguments={
            "title": "t", "message": "m",
        })
        result = asyncio.run(host.execute(call, _ctx(await_user_input=None)))
        assert result.item["isError"] is True
        assert "Error" in result.item["output"]


# ═══════════════════════════════════════════════════════════════════════════════
# Storm breaker / parallel classification / contracts
# ═══════════════════════════════════════════════════════════════════════════════


class TestGuards:
    def test_storm_breaker_exempt(self):
        assert ASK_USER_CONFIRM_TOOL in STORM_EXEMPT_TOOLS
        breaker = ToolStormBreaker()
        for _ in range(5):
            verdict = breaker.inspect(ASK_USER_CONFIRM_TOOL, {"title": "t"})
            assert verdict["suppress"] is False

    def test_never_parallel(self):
        assert ASK_USER_CONFIRM_TOOL in _NEVER_PARALLEL_TOOLS

    def test_contracts_extensions(self):
        assert TurnStatus.waiting_for_user == "waiting_for_user"
        item = UserInputItem(
            kind="user_input", role="system", inputId="ui_1", prompt="p", id="item_1",
            status="pending", threadId="thr", turnId="turn", createdAt="2026-08-11T00:00:00Z",
            title="确认执行方案？", steps=[{"id": "s1", "title": "搜索论文"}],
            choices=[{"id": "confirm", "label": "确认执行"}], timeout_seconds=120,
            allow_remember_choice=True,
        )
        assert item.title == "确认执行方案？"
        assert item.steps[0]["id"] == "s1"
        assert item.timeout_seconds == 120
        assert item.allow_remember_choice is True
        ev = UserInputRequestedEvent(
            kind="user_input_requested", inputId="ui_1", title="t", timeoutSeconds=120,
            seq=1, timestamp="2026-08-11T00:00:00Z", threadId="thr",
        )
        assert ev.timeoutSeconds == 120


class TestUserInputGateTimeout:
    def test_timeout_resolves_cancelled(self):
        async def scenario():
            gate = UserInputGate()
            task = asyncio.create_task(
                gate.request("thr", "turn", "item", "prompt", timeout=0.05)
            )
            result = await task
            return result

        result = asyncio.run(scenario())
        assert result["status"] == "cancelled"

    def test_resolve_submitted(self):
        async def scenario():
            gate = UserInputGate()
            task = asyncio.create_task(
                gate.request("thr", "turn", "item", "prompt", timeout=5)
            )
            await asyncio.sleep(0.01)
            gate.resolve("user_input_" + list(gate._pending)[0].split("_", 2)[-1], {"choice_id": "confirm"})
            return await task

        result = asyncio.run(scenario())
        assert result["status"] == "submitted"
        assert result["answers"]["choice_id"] == "confirm"


class TestGateTurnQueue:
    """Issue #714 follow-up: at most one pending confirm card per turn — a
    concurrent second request for the same turn QUEUES behind the active one
    instead of stacking a second live card or being rejected. Every queued
    card eventually becomes pending and gets its own answer."""

    def test_second_request_same_turn_queues_until_first_resolves(self):
        async def scenario():
            gate = UserInputGate()
            first = asyncio.create_task(
                gate.request("thr", "turn_1", "item_1", "p1", timeout=5)
            )
            for _ in range(50):
                if gate.pending_count == 1:
                    break
                await asyncio.sleep(0.01)
            assert gate.pending_count == 1
            second = asyncio.create_task(
                gate.request("thr", "turn_1", "item_2", "p2", timeout=5)
            )
            # Queued: not pending yet, no answer possible.
            for _ in range(20):
                await asyncio.sleep(0.01)
            assert gate.pending_count == 1, "queued request must not stack a second live card"
            assert second.done() is False, "queued request must wait, not return"
            # Resolve the first → the queued one becomes pending.
            first_id = list(gate._pending)[0]
            assert gate.resolve(first_id, {"choice_id": "cancel", "choice_label": "取消"})
            await first
            for _ in range(50):
                if gate.pending_count == 1 and not second.done():
                    break
                await asyncio.sleep(0.01)
            second_id = list(gate._pending)[0]
            assert second_id != first_id, "second request must acquire its own slot"
            assert gate.resolve(second_id, {"choice_id": "cancel", "choice_label": "取消"})
            second_result = await second
            return second_result

        second_result = asyncio.run(scenario())
        # Resolved via the cancel choice — submitted with choice_id=cancel
        # (the upper layer classifies it as cancelled, see build_result).
        assert second_result["status"] == "submitted"
        assert second_result["answers"]["choice_id"] == "cancel"

    def test_on_pending_fires_only_when_actually_pending(self):
        async def scenario():
            gate = UserInputGate()
            announced: list[str] = []

            async def announce(input_id: str):
                announced.append(input_id)

            first = asyncio.create_task(
                gate.request("thr", "turn_1", "item_1", "p1", timeout=5,
                             input_id="ui_1", on_pending=lambda: announce("ui_1"))
            )
            for _ in range(50):
                if gate.pending_count == 1:
                    break
                await asyncio.sleep(0.01)
            second = asyncio.create_task(
                gate.request("thr", "turn_1", "item_2", "p2", timeout=5,
                             input_id="ui_2", on_pending=lambda: announce("ui_2"))
            )
            # ui_2 is queued — its announce must NOT have fired yet.
            for _ in range(20):
                await asyncio.sleep(0.01)
            assert announced == ["ui_1"], announced
            gate.resolve("ui_1", {"choice_id": "cancel", "choice_label": "取消"})
            await first
            for _ in range(50):
                if "ui_2" in announced:
                    break
                await asyncio.sleep(0.01)
            assert announced == ["ui_1", "ui_2"], "queued card announces when it becomes pending"
            gate.resolve("ui_2", {"choice_id": "cancel", "choice_label": "取消"})
            await second
            return announced

        assert asyncio.run(scenario()) == ["ui_1", "ui_2"]

    def test_cancel_all_cancels_queued_requests(self):
        """CodeRabbit #718: turn cancellation must cancel BOTH the active
        request and requests still waiting in the queue — a queued card
        must never surface (announce) after the turn ended."""
        async def scenario():
            gate = UserInputGate()
            announced: list[str] = []

            async def noop():
                return None

            first = asyncio.create_task(
                gate.request("thr", "turn_1", "item_1", "p1", timeout=5,
                             input_id="ui_1", on_pending=noop)
            )
            for _ in range(50):
                if gate.pending_count == 1:
                    break
                await asyncio.sleep(0.01)
            second = asyncio.create_task(
                gate.request("thr", "turn_1", "item_2", "p2", timeout=5,
                             input_id="ui_2", on_pending=lambda: _announce(announced, "ui_2"))
            )
            for _ in range(20):
                await asyncio.sleep(0.01)
            gate.cancel_all("turn_1")
            first_result, second_result = await asyncio.gather(first, second)
            return first_result, second_result, announced

        first_result, second_result, announced = asyncio.run(scenario())
        assert first_result["status"] == "cancelled"
        assert second_result["status"] == "cancelled"
        assert "queued" in second_result.get("reason", "")
        assert announced == [], "queued card must not announce after turn cancellation"

    def test_slot_entry_dropped_when_idle(self):
        """CodeRabbit #718: per-turn slot entries must not accumulate for
        finished turns."""
        async def scenario():
            gate = UserInputGate()
            await gate.request("thr", "turn_1", "item_1", "p1", timeout=0.05)
            return len(gate._turn_slots)

        assert asyncio.run(scenario()) == 0

    def test_different_turns_can_be_pending_concurrently(self):
        async def scenario():
            gate = UserInputGate()
            t1 = asyncio.create_task(
                gate.request("thr", "turn_1", "item_1", "p1", timeout=0.3)
            )
            t2 = asyncio.create_task(
                gate.request("thr", "turn_2", "item_2", "p2", timeout=0.3)
            )
            for _ in range(50):
                if gate.pending_count == 2:
                    break
                await asyncio.sleep(0.01)
            count = gate.pending_count
            await asyncio.gather(t1, t2)  # both time out
            return count

        assert asyncio.run(scenario()) == 2

    def test_new_request_accepted_after_previous_resolves(self):
        async def scenario():
            gate = UserInputGate()
            first = asyncio.create_task(
                gate.request("thr", "turn_1", "item_1", "p1", timeout=0.3)
            )
            for _ in range(50):
                if gate.pending_count == 1:
                    break
                await asyncio.sleep(0.01)
            first_id = list(gate._pending)[0]
            assert gate.resolve(first_id, {"choice_id": "cancel", "choice_label": "取消"})
            await first
            assert gate.pending_count == 0
            # Same turn again AFTER the first resolved → accepted.
            second = asyncio.create_task(
                gate.request("thr", "turn_1", "item_2", "p2", timeout=0.3)
            )
            for _ in range(50):
                if gate.pending_count == 1:
                    break
                await asyncio.sleep(0.01)
            second_id = list(gate._pending)[0]
            count = gate.pending_count
            await second  # times out
            return count, first_id, second_id

        pending, first_id, second_id = asyncio.run(scenario())
        assert pending == 1
        assert second_id != first_id


class TestUserInputHistory:
    def test_record_and_query(self):
        from miqi.agent.user_input_history import (
            add_user_input_history,
            clear_history,
            get_user_input_history,
        )

        clear_history()
        add_user_input_history(
            title="确认执行方案？", status="submitted",
            choice_id="confirm", choice_label="确认执行",
            thread_id="thr", turn_id="turn",
        )
        add_user_input_history(
            title="是否上传？", status="cancelled", reason="timeout",
        )
        history = get_user_input_history()
        assert len(history) == 2
        assert history[0]["title"] == "是否上传？"  # most recent first
        assert history[0]["reason"] == "timeout"
        assert history[1]["choice_label"] == "确认执行"
        clear_history()


class TestRememberChoice:
    def test_same_card_reuses_choice_without_pending(self):
        from miqi.kun_runtime.loop import _remember_key

        payload = {"title": "确认执行方案？", "choices": [{"id": "confirm", "label": "确认执行"}]}
        key = _remember_key(payload)
        gate = UserInputGate()
        assert gate.remembered_choice("thr", key) is None
        gate.remember("thr", key, {"choice_id": "confirm", "choice_label": "确认执行"})
        assert gate.remembered_choice("thr", key)["choice_id"] == "confirm"
        # 不同 title → 不同 key
        other = _remember_key({"title": "是否上传？", "choices": [{"id": "confirm", "label": "确认上传"}]})
        assert other != key
        assert gate.remembered_choice("thr", other) is None
        # 跨 thread 不共享
        assert gate.remembered_choice("thr2", key) is None


class TestApprovalBoundary:
    """确认卡 vs 审批边界（issue #646）：确认卡是决策工具，不触发规则审批。"""

    def test_not_in_dangerous_patterns(self):
        """ask_user_confirm_card 不在危险命令模式里 —— 它只收集决策，不执行操作。
        审批拦的是确认之后真正执行的动作（write_file 等），两者正交。"""
        from miqi.agent.command_approval import DANGEROUS_PATTERNS

        for pattern, _desc in DANGEROUS_PATTERNS:
            assert "ask_user_confirm" not in pattern, f"确认卡不应出现在危险模式: {pattern}"

    def test_tool_name_not_approval_triggered(self):
        """注册层面确认：工具不是 approval-category 命令（无 approval metadata）。"""
        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool

        tool = AskUserConfirmCardTool()
        assert getattr(tool, "approval_category", None) is None
        assert "ask_user_confirm_card" in tool.name


class TestLegacyResolverPath:
    """Legacy desktop path: tool resolver → shared gate → emit → resolve."""

    def test_tool_blocks_then_resolves(self):
        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool
        from miqi.agent.user_input_resolver import (
            resolve_user_input,
            set_user_input_emitter,
        )

        emitted = {}

        async def emitter(payload):
            emitted["payload"] = payload

        set_user_input_emitter("", emitter)  # session key "" matches the tool's empty thread_id
        try:
            tool = AskUserConfirmCardTool(resolver=make_resolver())
            args = {"title": "确认执行方案？", "message": "4 个步骤", "timeout_seconds": 5}

            async def scenario():
                task = asyncio.create_task(tool.execute(**args))
                # 等卡片事件发出
                for _ in range(50):
                    if "payload" in emitted:
                        break
                    await asyncio.sleep(0.02)
                assert "payload" in emitted, "user_input_requested 未发出"
                input_id = emitted["payload"]["input_id"]
                assert emitted["payload"]["title"] == "确认执行方案？"
                ok = resolve_user_input(input_id, {"choice_id": "confirm", "choice_label": "确认执行"})
                assert ok
                result = await task
                return result

            result = asyncio.run(scenario())
        finally:
            set_user_input_emitter("", None)

        import json as _json

        data = _json.loads(result)
        assert data["status"] == "confirmed"
        assert data["choice_id"] == "confirm"

    def test_concurrent_cards_emit_one_at_a_time(self):
        """Issue #714 follow-up: two concurrent confirm cards in the same
        turn queue — the second card's user_input_requested event fires only
        after the first resolved, so the desktop never sees two stacked
        live cards."""
        import json as _json

        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool
        from miqi.agent.user_input_resolver import (
            resolve_user_input,
            set_user_input_emitter,
        )

        emissions: list[dict] = []

        async def emitter(payload):
            emissions.append(payload)

        set_user_input_emitter("", emitter)  # session key "" matches the tool's empty thread_id
        try:
            tool = AskUserConfirmCardTool(resolver=make_resolver())

            async def scenario():
                t1 = asyncio.create_task(tool.execute(title="卡1", message="m", timeout_seconds=5))
                t2 = asyncio.create_task(tool.execute(title="卡2", message="m", timeout_seconds=5))
                # Only card 1 is announced while card 2 waits in the queue.
                for _ in range(50):
                    if emissions:
                        break
                    await asyncio.sleep(0.02)
                assert len(emissions) == 1, emissions
                assert emissions[0]["title"] == "卡1"
                # Card 2 must NOT be announced yet — it is queued.
                for _ in range(20):
                    await asyncio.sleep(0.01)
                assert len(emissions) == 1, "queued card must not announce early"
                ok = resolve_user_input(emissions[0]["input_id"], {"choice_id": "confirm", "choice_label": "确认执行"})
                assert ok
                r1 = await t1
                # Card 1 resolved → card 2 becomes pending and announces.
                for _ in range(50):
                    if len(emissions) == 2:
                        break
                    await asyncio.sleep(0.02)
                assert len(emissions) == 2, "second card announces after the first resolves"
                assert emissions[1]["title"] == "卡2"
                ok = resolve_user_input(emissions[1]["input_id"], {"choice_id": "confirm", "choice_label": "确认执行"})
                assert ok
                r2 = await t2
                return r1, r2

            r1, r2 = asyncio.run(scenario())
        finally:
            set_user_input_emitter("", None)

        d1 = _json.loads(r1)
        d2 = _json.loads(r2)
        assert d1["status"] == "confirmed"
        assert d2["status"] == "confirmed", "both cards get their own user answers"

    def test_tool_cancelled_when_no_channel(self):
        """Resolver exists but no emitter wired → safe cancelled, never a fake confirm."""
        import json as _json

        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool
        from miqi.agent.user_input_resolver import set_user_input_emitter

        set_user_input_emitter("", None)
        tool = AskUserConfirmCardTool(resolver=make_resolver())
        result = asyncio.run(tool.execute(title="t", message="m"))
        data = _json.loads(result)
        assert data["status"] == "cancelled"
        assert "user-input channel" in data["reason"]

    def test_no_resolver_fallback_error(self):
        """No resolver at all → structured error, never fabricate a decision."""
        tool = AskUserConfirmCardTool()
        result = asyncio.run(tool.execute(title="t", message="m"))
        assert result.startswith("Error")
        assert "不要假设用户已同意" in result

    def test_remembered_choice_skips_card_on_legacy_path(self):
        """Legacy resolver reuses the remembered choice without re-emitting."""
        import json as _json

        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool
        from miqi.agent.user_input_resolver import (
            resolve_user_input,
            set_user_input_emitter,
        )

        emissions = []

        async def emitter(payload):
            emissions.append(payload)

        set_user_input_emitter("", emitter)
        try:
            tool = AskUserConfirmCardTool(resolver=make_resolver())
            args = {
                "title": "确认执行方案？",
                "message": "4 个步骤",
                "timeout_seconds": 5,
                "allow_remember_choice": True,
            }

            async def scenario():
                first = asyncio.create_task(tool.execute(**args))
                for _ in range(50):
                    if emissions:
                        break
                    await asyncio.sleep(0.02)
                assert len(emissions) == 1, "首次应弹卡"
                ok = resolve_user_input(
                    emissions[0]["input_id"],
                    {"choice_id": "confirm", "choice_label": "确认执行"},
                    remember=True,
                )
                assert ok
                first_result = await first
                # 第二次：同卡片应命中 remember，不再发射事件
                second = await tool.execute(**args)
                return first_result, second, len(emissions)

            first_result, second, n_emissions = asyncio.run(scenario())
        finally:
            set_user_input_emitter("", None)

        first = _json.loads(first_result)
        assert first["status"] == "confirmed"
        second = _json.loads(second)
        assert second["status"] == "confirmed"
        assert second.get("remembered") is True
        assert second["choice_id"] == "confirm"
        assert n_emissions == 1, "remember 命中后不应再次弹卡"


# ═══════════════════════════════════════════════════════════════════════════════
# Regression: gate.request() raising must not mask the original error, and the
# finally-block cleanup (item resolution + turn status) must still complete
# (CodeRabbit #666: finally dereferenced result=None → AttributeError).
# ═══════════════════════════════════════════════════════════════════════════════


class TestAwaitUserInputGateFailure:
    @pytest.mark.asyncio
    async def test_gate_error_surfaces_original_and_cleans_up(self, tmp_path):
        from pathlib import Path

        from miqi.kun_runtime.cancellation import InflightTracker
        from miqi.kun_runtime.compactor import ContextCompactor
        from miqi.kun_runtime.event_bus import EventBus
        from miqi.kun_runtime.event_recorder import RuntimeEventRecorder
        from miqi.kun_runtime.loop import AgentLoop, AgentLoopOptions
        from miqi.kun_runtime.model_client import FakeModelClient, ModelStreamChunk
        from miqi.kun_runtime.stores import FileSessionStore, FileThreadStore
        from miqi.kun_runtime.tool_host import MiQiToolHost
        from miqi.kun_runtime.turn_service import TurnService
        from miqi.kun_runtime.usage import UsageService
        from miqi.kun_runtime.user_input_gate import UserInputGate

        fixed = "2026-08-15T00:00:00Z"

        class ExplodingGate(UserInputGate):
            async def request(self, *args, **kwargs):
                raise RuntimeError("gate exploded")

        class OneShotToolCallModel(FakeModelClient):
            """Tool call on the first request only, then plain text."""

            def __init__(self):
                super().__init__(text_chunks=["完成"])
                self._n = 0

            async def stream(self, request):
                self._n += 1
                if self._n == 1:
                    yield ModelStreamChunk(
                        kind="tool_call_complete",
                        callId="call_1",
                        toolName="ask_user_confirm_card",
                        arguments={
                            "title": "确认执行方案？",
                            "message": "m",
                            "choices": [{"id": "confirm", "label": "确认执行"}],
                        },
                    )
                    yield ModelStreamChunk(kind="completed", stopReason="tool_calls")
                else:
                    yield ModelStreamChunk(kind="assistant_text_delta", text="完成")
                    yield ModelStreamChunk(kind="completed", stopReason="stop")

        data_dir = Path(tmp_path) / "data"
        thread_store = FileThreadStore(data_dir)
        session_store = FileSessionStore(data_dir)
        bus = EventBus()
        events = RuntimeEventRecorder(bus, now_iso=lambda: fixed)
        turns = TurnService(
            thread_store, session_store, events, InflightTracker(), now_iso=lambda: fixed
        )

        registry = ToolRegistry()
        registry.register(AskUserConfirmCardTool())
        tool_host = MiQiToolHost(registry)

        opts = AgentLoopOptions(
            thread_store=thread_store,
            session_store=session_store,
            model=OneShotToolCallModel(),
            tool_host=tool_host,
            usage=UsageService(),
            events=events,
            turns=turns,
            inflight=InflightTracker(),
            compactor=ContextCompactor(soft_threshold=100, hard_threshold=500),
            now_iso=lambda: fixed,
            user_input_gate=ExplodingGate(),
        )

        th = {
            "id": "th1",
            "title": "Test Thread",
            "workspace": str(Path(tmp_path) / "ws"),
            "model": "fake-model",
            "mode": "agent",
            "status": "idle",
            "approvalPolicy": "auto",
            "sandboxMode": "workspace-write",
            "relation": "primary",
            "costBudgetWarningSent": False,
            "createdAt": fixed,
            "updatedAt": fixed,
            "turns": [],
        }
        await thread_store.upsert(th)
        started = await turns.start_turn("th1", "hello")
        turn_id = started["turnId"]

        loop = AgentLoop(opts)
        status = await loop.run_turn("th1", turn_id)
        assert status == "completed"

        # The ORIGINAL gate error must surface in the tool result — not a
        # masked "'NoneType' object has no attribute 'get'".
        items = await session_store.load_items("th1")
        outputs = [i.get("output", "") for i in items if isinstance(i, dict)]
        assert any("gate exploded" in o for o in outputs), outputs
        assert not any("NoneType" in o for o in outputs)

        # The finally-block cleanup must still have run: user_input_resolved
        # recorded as cancelled and the turn returned to a finished state.
        kinds = [e["kind"] for e in bus.history("th1")]
        assert "user_input_resolved" in kinds
        resolved = [e for e in bus.history("th1") if e["kind"] == "user_input_resolved"]
        assert resolved[0]["status"] == "cancelled"
        turn = await turns.get_turn("th1", turn_id)
        assert turn["status"] == "completed"


class TestMultipleCardsOneTurn:
    """Issue #714: when the model emits several confirm cards in ONE model
    step, the KUN loop dispatches them sequentially — at no point does the
    gate hold more than one pending request for the turn, and each card gets
    its own requested→resolved event pair."""

    @pytest.mark.asyncio
    async def test_cards_serialize_and_never_stack(self, tmp_path):
        from pathlib import Path

        from miqi.kun_runtime.cancellation import InflightTracker
        from miqi.kun_runtime.compactor import ContextCompactor
        from miqi.kun_runtime.event_bus import EventBus
        from miqi.kun_runtime.event_recorder import RuntimeEventRecorder
        from miqi.kun_runtime.loop import AgentLoop, AgentLoopOptions
        from miqi.kun_runtime.model_client import FakeModelClient, ModelStreamChunk
        from miqi.kun_runtime.stores import FileSessionStore, FileThreadStore
        from miqi.kun_runtime.tool_host import MiQiToolHost
        from miqi.kun_runtime.turn_service import TurnService
        from miqi.kun_runtime.usage import UsageService
        from miqi.kun_runtime.user_input_gate import UserInputGate

        fixed = "2026-08-15T00:00:00Z"
        titles = ["确认执行方案？", "是否上传到 MiQroForge？"]

        class TwoCardsOneStepModel(FakeModelClient):
            """One model step carrying TWO confirm cards, then plain text."""

            def __init__(self):
                super().__init__(text_chunks=["完成"])
                self._n = 0

            async def stream(self, request):
                self._n += 1
                if self._n == 1:
                    for i, title in enumerate(titles, 1):
                        yield ModelStreamChunk(
                            kind="tool_call_complete",
                            callId=f"call_{i}",
                            toolName="ask_user_confirm_card",
                            arguments={"title": title, "message": "m"},
                        )
                    yield ModelStreamChunk(kind="completed", stopReason="tool_calls")
                else:
                    yield ModelStreamChunk(kind="assistant_text_delta", text="完成")
                    yield ModelStreamChunk(kind="completed", stopReason="stop")

        data_dir = Path(tmp_path) / "data"
        thread_store = FileThreadStore(data_dir)
        session_store = FileSessionStore(data_dir)
        bus = EventBus()
        events = RuntimeEventRecorder(bus, now_iso=lambda: fixed)
        turns = TurnService(
            thread_store, session_store, events, InflightTracker(), now_iso=lambda: fixed
        )
        gate = UserInputGate()

        registry = ToolRegistry()
        registry.register(AskUserConfirmCardTool())
        tool_host = MiQiToolHost(registry)

        opts = AgentLoopOptions(
            thread_store=thread_store,
            session_store=session_store,
            model=TwoCardsOneStepModel(),
            tool_host=tool_host,
            usage=UsageService(),
            events=events,
            turns=turns,
            inflight=InflightTracker(),
            compactor=ContextCompactor(soft_threshold=100, hard_threshold=500),
            now_iso=lambda: fixed,
            user_input_gate=gate,
        )

        th = {
            "id": "th1",
            "title": "Test Thread",
            "workspace": str(Path(tmp_path) / "ws"),
            "model": "fake-model",
            "mode": "agent",
            "status": "idle",
            "approvalPolicy": "auto",
            "sandboxMode": "workspace-write",
            "relation": "primary",
            "costBudgetWarningSent": False,
            "createdAt": fixed,
            "updatedAt": fixed,
            "turns": [],
        }
        await thread_store.upsert(th)
        started = await turns.start_turn("th1", "hello")
        turn_id = started["turnId"]

        loop = AgentLoop(opts)
        run_task = asyncio.create_task(loop.run_turn("th1", turn_id))

        async def wait_for_pending(exclude: set[str]) -> str | None:
            for _ in range(500):
                pending = [r for r in gate.get_pending(turn_id) if r.id not in exclude]
                if pending:
                    return pending[0].id
                if run_task.done():
                    return None
                await asyncio.sleep(0.01)
            return None

        # Each card becomes pending ONE at a time; resolve it and the next
        # appears. The gate must never hold two pending requests at once.
        # (The previous request lingers in _pending for a few ticks after
        # resolve until the loop's finally pops it — exclude it so the same
        # card is never resolved twice.)
        resolved_ids: set[str] = set()
        for title in titles:
            input_id = await wait_for_pending(resolved_ids)
            assert input_id is not None, f"card {title} never became pending"
            assert gate.pending_request(input_id) is not None
            assert gate.pending_count == 1, "at most one pending card per turn"
            assert gate.resolve(input_id, {"choice_id": "confirm", "choice_label": "确认执行"})
            resolved_ids.add(input_id)

        status = await asyncio.wait_for(run_task, timeout=10)
        assert status == "completed"

        history = bus.history("th1")
        requested = [e for e in history if e["kind"] == "user_input_requested"]
        assert [e["title"] for e in requested] == titles
        kinds = [e["kind"] for e in history]
        reqs = [i for i, k in enumerate(kinds) if k == "user_input_requested"]
        ress = [i for i, k in enumerate(kinds) if k == "user_input_resolved"]
        assert len(reqs) == 2
        assert len(ress) == 2
        # Serialized: requested₁ < resolved₁ < requested₂ < resolved₂.
        assert reqs[0] < ress[0] < reqs[1] < ress[1]
        for e in history:
            if e["kind"] == "user_input_resolved":
                assert e["status"] == "submitted"
