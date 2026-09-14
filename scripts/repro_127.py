"""127 复现：确认后真实工具执行 → 回合是否继续（后端级，非 E2E）。

场景：R1 ask_user_confirm_card（自动确认）→ R2 read_file（真实）→
R3 write_file（真实）→ R4 最终回答。看 turn 是否完整跑完。
"""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool
from miqi.agent.tools.ask_user_plan_confirm import AskUserPlanConfirmTool
from miqi.agent.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool
from miqi.agent.tools.registry import ToolRegistry
from miqi.execution.factory import create_default_orchestrator
from miqi.runtime.context_runtime import ContextRuntime
from miqi.runtime.tool_runtime import ToolRuntime
from miqi.runtime.turn_context import TurnContext
from miqi.runtime.turn_runner import TurnRunner


class Emitter:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)
        name = type(event).__name__
        if "ToolCallBegin" in name or "ToolCallEnd" in name:
            print(f"  [event] {name}: {getattr(event, 'tool_name', '')} -> {getattr(event, 'success', '')}")

    def has(self, cls_name):
        return any(type(e).__name__ == cls_name for e in self.events)


async def auto_confirm(payload: dict):
    print(f"  [resolver] 自动确认: {payload.get('title', '')[:40]}")
    return {"status": "confirmed", "choice_id": "confirm", "label": "确认执行"}


class SeqProvider:
    """按轮次返回预设响应：R1 确认卡 → R2 read_file → R3 write_file → R4 完成。"""

    def __init__(self):
        self.calls = 0
        self.workspace = None

    def _tc(self, name, args, cid):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=cid,
            name=name,
            arguments=args,
            arguments_json=json.dumps(args, ensure_ascii=False),
        )

    def get_default_model(self):
        return "mock"

    async def stream_chat(self, messages=None, tools=None, model=None, temperature=None, max_tokens=None, **kw):
        self.calls += 1
        n = self.calls
        print(f"\n=== provider round R{n} === (messages={len(messages or [])})")
        roles = [m.get("role") for m in (messages or [])]
        print(f"  roles: {roles[-6:]}")
        if n == 1:
            resp = (self._tc("ask_user_confirm_card", {
                "title": "确认执行方案？",
                "message": "将搜索并整理数据。",
                "choices": [{"id": "confirm", "label": "确认执行"}, {"id": "cancel", "label": "取消", "role": "cancel"}],
                "timeout_seconds": 60,
            }, "call_c1"), "tool_calls")
        elif n == 2:
            resp = (self._tc("read_file", {"path": "README.md"}, "call_r2"), "tool_calls")
        elif n == 3:
            resp = (self._tc("write_file", {"path": "out/127-repro.json", "content": '{"ok": true}'}, "call_w3"), "tool_calls")
        else:
            resp = ("✅ 已完成全部步骤。", "stop")
        from miqi.providers.base import LLMResponse, LLMStreamEvent
        if resp[1] == "stop":
            response = LLMResponse(content=resp[0], finish_reason="stop", usage={})
        else:
            response = LLMResponse(content="", tool_calls=[resp[0]], finish_reason="tool_calls", usage={})
        yield LLMStreamEvent(kind="completed", response=response)


async def main():
    workspace = Path(tempfile.mkdtemp(prefix="miqi-127-repro-"))
    (workspace / "README.md").write_text("# repro\n", encoding="utf-8")
    (workspace / "out").mkdir(exist_ok=True)
    print(f"workspace: {workspace}")

    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace=workspace, allowed_dir=workspace))
    registry.register(WriteFileTool(workspace=workspace))
    registry.register(ListDirTool(workspace=workspace, allowed_dir=workspace))
    registry.register(AskUserConfirmCardTool(resolver=auto_confirm))
    registry.register(AskUserPlanConfirmTool(resolver=auto_confirm))

    from types import SimpleNamespace

    orch = create_default_orchestrator(
        registry,
        approval_bypass=SimpleNamespace(bypass_all=True),
    )
    tool_runtime = ToolRuntime(orchestrator=orch)
    provider = SeqProvider()
    provider.workspace = workspace
    emitter = Emitter()
    context = ContextRuntime()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=tool_runtime,
        context_runtime=context,
        event_emitter=emitter,
        max_iterations=10,
    )

    turn = TurnContext(
        turn_id="turn-127-repro",
        agent_metadata=SimpleNamespace(name="main", system_prompt=""),
        thread_id="thread-127-repro",
        workspace=workspace,
        model="mock",
        provider=provider,
        execution_policy="edit",
        temperature=0.1,
        max_tokens=2048,
    )
    turn.permission_profile = __import__(
        "miqi.runtime.permission_profile", fromlist=["PermissionProfile"]
    ).PermissionProfile(workspace=workspace)

    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            runner.run(turn=turn, user_content="帮我整理季度销售数据报告并确认执行", system_prompt="", tools=[]),
            timeout=60,
        )
        elapsed = time.perf_counter() - t0
        print(f"\n=== RESULT ({(elapsed):.1f}s) ===")
        final_content = result.final_content or ""
        print("final_content:", final_content[:120])
        print("tools_used:", result.tools_used)
        print("provider rounds:", provider.calls)
        ok = provider.calls == 4 and final_content.startswith("✅")
        print("\n>>> 127 复现：", "PASS（回合完整推进）" if ok else "FAIL（回合未推进到 R4）")
    except asyncio.TimeoutError:
        print(f"\n=== TIMEOUT 60s（provider rounds={provider.calls}）——回合卡住 ===")
        print(">>> 127 复现：FAIL（卡死）")
    except Exception as exc:
        print(f"\n=== EXCEPTION: {type(exc).__name__}: {exc}")
        print(">>> 127 复现：FAIL（异常中断）")


if __name__ == "__main__":
    asyncio.run(main())
