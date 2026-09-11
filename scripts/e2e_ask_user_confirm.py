"""E2E: ask_user_confirm_card 全链路（issue #646）.

模拟真实桌面场景：
  模型(第1轮) 调用 ask_user_confirm_card
    → KUN loop 发 user_input_requested 事件 + turn 转 waiting_for_user
    → 脚本扮演"用户"点击卡片（gate.resolve）
    → 工具结果 {"status":"confirmed","choice_id":...} 回传给模型
  模型(第2轮) 收到确认，输出最终文本
    → turn 完成

运行：
  cd D:\\Desktop\\811\\MiQroForge
  PYTHONPATH=. .venv/Scripts/python.exe scripts/e2e_ask_user_confirm.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

from miqi.agent.tools.registry import ToolRegistry
from miqi.kun_runtime.model_client import FakeModelClient
from miqi.kun_runtime.runtime import KunRuntime, RuntimeOptions


class RoundRobinModel(FakeModelClient):
    """Per-round responses: round 0 → confirm-card tool call, round 1 → final text."""

    def __init__(self) -> None:
        super().__init__()
        self.model = "fake-model"
        self.provider_name = "RoundRobinModel"

    async def stream(self, request):
        self._requests.append(request)
        round_no = len(self._requests) - 1
        print(f"\n  ── [模型] 第 {round_no + 1} 轮请求 ──")
        if round_no == 0:
            print("  [模型] 决定调用 ask_user_confirm_card（上传前确认）")
            yield await _chunk("tool_call_complete", {
                "id": "call_confirm_1",
                "name": "ask_user_confirm_card",
                "arguments": {
                    "title": "方案已完成，是否上传到 MiQroForge？",
                    "message": "工作流方案已生成并通过校验，上传后将作为 WorkflowDefinition 发布到 MiQroForge 平台。",
                    "steps": [
                        {"id": "search_papers", "title": "搜索并下载相关论文"},
                        {"id": "extract_info", "title": "提取 MOF-5 合成路线与成本信息"},
                        {"id": "query_price", "title": "查询供应商价格（国内）"},
                        {"id": "generate_report", "title": "生成最终报告"},
                    ],
                    "choices": [
                        {"id": "confirm", "label": "确认上传"},
                        {"id": "cancel", "label": "取消"},
                    ],
                    "timeout_seconds": 30,
                    "allow_remember_choice": True,
                },
            })
            yield await _chunk("completed", {"stopReason": "tool_calls"})
            return

        # Round 1: 工具结果已回传，模型确认并继续
        last_user = request.history[-1] if request.history else None
        print(f"  [模型] 收到工具结果: {_preview(last_user)}")
        for text in ["好的，已确认上传。WorkflowDefinition 已发布到 MiQroForge，项目入口：forge.miqroera.com/projects/mof-price-report。"]:
            yield await _chunk("assistant_text_delta", {"text": text})
        yield await _chunk("completed", {"stopReason": "stop"})


def _chunk(kind: str, data: dict):
    from miqi.kun_runtime.model_client import ModelStreamChunk

    if kind == "tool_call_complete":
        return _a(ModelStreamChunk(
            kind=kind,
            callId=data.get("id", "call_1"),
            toolName=data.get("name", "unknown"),
            arguments=data.get("arguments", {}),
        ))
    return _a(ModelStreamChunk(kind=kind, **data))


async def _a(value):
    return value


def _preview(msg) -> str:
    if not msg:
        return "(无)"
    text = str(msg.get("content", ""))[:160]
    return text if text else "(工具结果，见上)"


async def main() -> int:
    print("=" * 64)
    print("E2E: ask_user_confirm_card — 模型调用 → 卡片 → 用户选择 → 回传")
    print("=" * 64)

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp) / "data"
        ws = Path(tmp) / "ws"
        ws.mkdir(parents=True, exist_ok=True)

        runtime = KunRuntime(RuntimeOptions(
            data_dir=data_dir,
            workspace=str(ws),
            model="fake-model",
        ))

        # 注册真实工具集（含 ask_user_confirm_card）
        # 轻量 registry：只挂确认卡工具
        registry = ToolRegistry()
        from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool

        registry.register(AskUserConfirmCardTool())
        runtime.set_tool_registry(registry)

        model = RoundRobinModel()
        runtime.model = model  # 直接注入假模型（不走 set_provider）

        # 建线程 + 起 turn
        thread = await runtime.threads.create(workspace=str(ws), model="fake-model")
        thread_id = thread["id"]
        turn = await runtime.turns.start_turn(thread_id, "帮我查 MOF-5 市场合成价格并上传方案")
        turn_id = turn["turnId"]
        print(f"\n线程 {thread_id} / Turn {turn_id} 启动\n")

        # ── 模拟用户：等卡片出现后点击「确认上传」 ──
        async def fake_user():
            deadline = time.time() + 15
            while time.time() < deadline:
                pend = runtime.user_input_gate.get_pending()
                if pend:
                    req = pend[0]
                    print(f"\n  👤 [用户] 看到确认卡: 「{req.prompt[:40]}…」")
                    print(f"  👤 [用户] 点击「确认上传」→ gate.resolve({req.id})")
                    runtime.user_input_gate.resolve(
                        req.id, {"choice_id": "confirm", "choice_label": "确认上传"}
                    )
                    return
                await asyncio.sleep(0.1)
            print("  ⚠ [用户] 15s 内未等到卡片")

        # ── 事件监听：打印 user_input 事件流 ──
        async def watcher():
            async for event in runtime.event_bus.subscribe(thread_id):
                kind = event.get("kind", "")
                if kind in ("user_input_requested", "user_input_resolved", "turn_status_changed", "turn_completed"):
                    brief = {k: v for k, v in event.items() if k in ("kind", "inputId", "status", "choice_id", "resolution")}
                    print(f"  📡 [事件] {kind}: {brief}")

        asyncio.get_event_loop().create_task(watcher())
        asyncio.get_event_loop().create_task(fake_user())

        # ── 跑 turn ──
        result = await runtime.loop.run_turn(thread_id, turn_id)
        print(f"\n{'=' * 64}\nTurn 结果: {result}\n{'=' * 64}")

        # ── 验证最终消息 ──
        items = await runtime.session_store.load_items(thread_id)
        texts = [i.get("content", "") for i in items if i.get("role") == "assistant" and i.get("content")]
        print("\n✅ 最终消息:")
        for t in texts:
            print(f"   {t[:100]}")

        has_confirm_tool = any(i.get("kind") == "tool_call" and i.get("toolName") == "ask_user_confirm_card" for i in items)
        has_result = any("confirmed" in str(i.get("output", "")) for i in items if i.get("kind") == "tool_result")
        ok = has_confirm_tool and has_result and result == "completed"
        print(f"\n验证: 确认卡工具调用记录={'✓' if has_confirm_tool else '✗'} | 确认结果回传={'✓' if has_result else '✗'}")
        print("E2E 完成 ✅" if ok else "E2E 异常 ❌")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
