"""#680 desktop FAST budget 单测（desktop-fast-budget-design.md 方案 1+2+4）。

用假时钟注入测边界（无需 monkeypatch/sleep）：
- 方案 2: fast 裁剪迭代上限为 3（think 保持 500）
- 方案 1: 25s 进入收尾（拒绝新工具 + 注入收尾提示）；30s 硬停
- 方案 4: fast web_search 每轮 ≤1 次（第 2 次被拒，模型可见跳过原因）
- think 不受影响（无熔断/无 phase 限制）
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from miqi.runtime.turn_context import TurnContext
from miqi.runtime.turn_runner import TurnRunner


class FakeClock:
    """可控假时钟：run() 内每次调用返回当前设定值。

    auto_advance: 每次调用后自动推进 N 秒——用于模拟真实时间流逝
    （run 内 t0 之后每次 clock() 都更晚）。
    """

    def __init__(self, start: float = 0.0, auto_advance: float = 0.0):
        """Test double for the reasoning-elapsed scenarios."""
        self._now = start
        self._auto = auto_advance

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        now = self._now
        if self._auto:
            self._now += self._auto
        return now


class FakeProvider:
    """返回预编排的响应序列：先 N 轮 tool_calls，最后收尾回答。"""

    def __init__(self, tool_rounds: int = 0, all_search: bool = False):
        """Test double for the reasoning-elapsed scenarios."""
        self._rounds = tool_rounds
        self._all_search = all_search
        self.calls = 0

    async def stream_chat(self, **kwargs):
        """Test double for the reasoning-elapsed scenarios."""
        self.calls += 1
        if self.calls <= self._rounds:
            yield SimpleNamespace(kind="content_delta", delta="")
            yield SimpleNamespace(
                kind="completed",
                response=SimpleNamespace(
                    has_tool_calls=True,
                    tool_calls=[SimpleNamespace(
                        id=f"tc{self.calls}",
                        # all_search=True 时每轮都 web_search（phase 预算测试）；
                        # 否则首轮 web_search、其余 read_file（轮数测试）
                        name="web_search" if (self._all_search or self.calls == 1) else "read_file",
                        arguments={},
                        arguments_json="{}",
                    )],
                    content="",
                    reasoning_content="",
                    usage={},
                    finish_reason="tool_calls",
                ),
            )
        else:
            yield SimpleNamespace(kind="content_delta", delta="最终回答")
            yield SimpleNamespace(
                kind="completed",
                response=SimpleNamespace(
                    has_tool_calls=False,
                    tool_calls=[],
                    content="最终回答",
                    reasoning_content="思考过程",
                    usage={},
                    finish_reason="stop",
                ),
            )


class FakeTools:
    def __init__(self):
        """Test double for the reasoning-elapsed scenarios."""
        self.executed: list[str] = []

    async def execute_many(self, turn, tool_calls):
        out = []
        for tc in tool_calls:
            self.executed.append(tc.name)
            out.append(SimpleNamespace(
                tool_call_id=tc.id,
                result=f"{tc.name} 结果",
                status="success",
                duration_ms=10,
                permission_decision=None,
                sandbox_selection=None,
            ))
        return out


class FakeContext:
    def __init__(self):
        """Test double for the reasoning-elapsed scenarios."""
        self.messages: list[dict] = []

    def build_initial_messages(self, **kwargs):
        self.messages = [{"role": "user", "content": kwargs.get("user_content", "")}]
        return self.messages

    def trim_for_model(self, messages, model):
        return messages

    def add_assistant_message(
        self, *,
        messages, content, tool_calls=None, reasoning_content=None,
    ):
        return messages + [{"role": "assistant", "content": content, "tool_calls": tool_calls or []}]

    def add_tool_result(
        self, *,
        messages, tool_call_id, name, content, arguments=None,
    ):
        return messages + [{"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": content}]


class FakeEmitter:
    def __init__(self):
        """Test double for the reasoning-elapsed scenarios."""
        self.events: list[str] = []

    async def emit(self, event):
        self.events.append(type(event).__name__)


def _mk_turn(reasoning_mode: str | None) -> TurnContext:
    return TurnContext(
        turn_id="t1",
        agent_metadata=SimpleNamespace(system_prompt=""),
        thread_id="th1",
        workspace="",
        model="fake",
        provider=SimpleNamespace(),
        execution_policy="edit",
        reasoning_mode=reasoning_mode,
    )


def _mk_runner(clock: FakeClock, rounds: int = 0, all_search: bool = False) -> tuple[TurnRunner, FakeProvider, FakeTools, FakeEmitter]:
    provider = FakeProvider(tool_rounds=rounds, all_search=all_search)
    tools = FakeTools()
    emitter = FakeEmitter()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=tools,
        context_runtime=FakeContext(),
        event_emitter=emitter,
        max_iterations=500,
        clock=clock,
    )
    return runner, provider, tools, emitter


@pytest.mark.asyncio
async def test_fast_caps_iterations_to_3():
    """fast: 模型连续请求 4 轮工具 → 只执行 3 轮（第 4 次模型调用不产生工具）。

    search_every=2：第 1 轮 web_search（phase 放行），第 2-4 轮 read_file
    （不触发 phase 预算）——纯验证轮数裁剪。"""
    clock = FakeClock()
    runner, provider, tools, emitter = _mk_runner(clock, rounds=5)
    await runner.run(
        turn=_mk_turn("fast"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert len(tools.executed) == 3, f"fast 应裁剪到 3 轮工具，实际 {len(tools.executed)}"
    assert provider.calls <= 4


@pytest.mark.asyncio
async def test_think_keeps_full_iterations():
    """think: 不裁剪——5 轮工具全部执行。"""
    clock = FakeClock()
    runner, provider, tools, emitter = _mk_runner(clock, rounds=5)
    await runner.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert len(tools.executed) == 5


@pytest.mark.asyncio
async def test_fast_time_fuse_finalizes_at_25s():
    """fast: 时间流逝到 25s+ → 进入收尾（不再执行新工具，注入收尾提示）。

    auto_advance=9：t0=0，迭代检查 now=9/18/27…——第 3 次迭代 27s > 25s
    finalize_at → 收尾（拒绝新工具）。"""
    clock = FakeClock(auto_advance=9)
    runner, provider, tools, emitter = _mk_runner(clock, rounds=5)
    result = await runner.run(
        turn=_mk_turn("fast"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    # 27s 时已过 finalize（25s）——收尾后不再执行新工具
    assert len(tools.executed) <= 2, f"25s+ 后不应再执行新工具，实际 {len(tools.executed)}"
    # 收尾提示应注入消息流
    finalize_msgs = [m for m in result.messages if isinstance(m, dict) and "极速模式收尾" in str(m.get("content", ""))]
    assert len(finalize_msgs) >= 1, "收尾提示应注入"


@pytest.mark.asyncio
async def test_fast_hard_stop_at_30s():
    """fast: 时间流逝到 30s+ → 硬停（循环 break，不再调用模型）。

    auto_advance=10：迭代检查 now=10/20/30——第 3 次迭代 30s ≥ hard_stop_at
    → break（模型调用 ≤2 次）。"""
    clock = FakeClock(auto_advance=10)
    runner, provider, tools, emitter = _mk_runner(clock, rounds=5)
    await runner.run(
        turn=_mk_turn("fast"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert provider.calls <= 2, f"30s 硬停后不应再调用模型，实际 {provider.calls} 次"


@pytest.mark.asyncio
async def test_fast_search_phase_budget():
    """fast: 每轮 web_search 只放行 1 次——第 2 次被拒（跳过结果进消息流）。"""
    clock = FakeClock()
    runner, provider, tools, emitter = _mk_runner(clock, rounds=2, all_search=True)
    result = await runner.run(
        turn=_mk_turn("fast"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    # 两轮都请求 web_search：第 1 轮放行，第 2 轮被拒
    assert tools.executed.count("web_search") == 1
    # 跳过结果应出现在消息流（模型可见"搜索预算已用尽"）
    skip_msgs = [m for m in result.messages if isinstance(m, dict) and m.get("content", "").startswith("[跳过]")]
    assert len(skip_msgs) == 1, f"跳过结果应进消息流，实际 {len(skip_msgs)}"


@pytest.mark.asyncio
async def test_think_no_search_phase_limit():
    """think: web_search 无 phase 限制——两轮都执行。"""
    clock = FakeClock()
    runner, provider, tools, emitter = _mk_runner(clock, rounds=2, all_search=True)
    await runner.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert tools.executed.count("web_search") == 2


@pytest.mark.asyncio
async def test_fast_budget_end_returns_mild_notice_not_diagnosis():
    """缺陷 A/B（外部审阅 2026-08-24）：fast 预算终止（3 轮耗尽）返回温和
    说明 + 已有内容，而不是"已达到最大迭代次数"失败诊断。"""
    clock = FakeClock()
    runner, provider, tools, emitter = _mk_runner(clock, rounds=5)
    result = await runner.run(
        turn=_mk_turn("fast"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert "已达到最大迭代次数" not in result.final_content, (
        f"fast 预算终止不应返回失败诊断：{result.final_content[:80]}"
    )
    assert "极速模式" in result.final_content
    # 已有内容（工具轮里的 assistant 消息）应附带
    assert "已使用工具" in result.final_content


@pytest.mark.asyncio
async def test_fast_hard_stop_returns_mild_notice():
    """缺陷 A：30s 硬停 → final_content 是温和说明，不是诊断。"""
    clock = FakeClock(auto_advance=10)
    runner, provider, tools, emitter = _mk_runner(clock, rounds=5)
    result = await runner.run(
        turn=_mk_turn("fast"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert provider.calls <= 2
    assert "已达到最大迭代次数" not in result.final_content
    assert "极速模式" in result.final_content


@pytest.mark.asyncio
async def test_think_exhaustion_keeps_diagnosis():
    """think（非 fast）：迭代耗尽仍返回诊断（#491 行为不变）。"""
    clock = FakeClock()
    runner, provider, tools, emitter = _mk_runner(clock, rounds=0)
    # think + rounds=0 → provider 第一次就返回纯回答（无工具）→ 正常完成，
    # 不会走到耗尽。构造"永远工具轮"来触发耗尽：
    provider2 = FakeProvider(tool_rounds=500)
    runner2 = TurnRunner(
        provider=provider2,
        tool_runtime=FakeTools(),
        context_runtime=FakeContext(),
        event_emitter=FakeEmitter(),
        max_iterations=3,  # 小上限快速耗尽
        clock=clock,
    )
    result = await runner2.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert "已达到最大迭代次数" in result.final_content


@pytest.mark.asyncio
async def test_reasoning_elapsed_s_measured_from_round_start():
    """#834: 首个 reasoning delta 到达时记录思考耗时（本轮模型调用开始→首
    delta，粗钟兜底路径），随 TurnResult 返回——前端 final 事件优先使用
    该服务端值。"""
    import asyncio

    class _ReasoningProvider(FakeProvider):
        def __init__(self):
            """Reasoning-first provider for the round-start timing test."""
            super().__init__(tool_rounds=0)
            self.reasoning_yielded = False

        async def stream_chat(self, **kwargs):
            """Yield reasoning deltas after a simulated thinking delay."""
            self.calls += 1
            # 模拟服务端先思考再下发：首 delta 前 sleep 200ms（0.2s 留足
            # Windows 计时器精度余量，见 test_openai_streaming 同模式修复）
            await asyncio.sleep(0.2)
            yield SimpleNamespace(kind="reasoning_delta", delta="先思考")
            yield SimpleNamespace(kind="reasoning_delta", delta="再思考")
            yield SimpleNamespace(kind="content_delta", delta="最终回答")
            yield SimpleNamespace(
                kind="completed",
                response=SimpleNamespace(
                    has_tool_calls=False,
                    tool_calls=[],
                    content="最终回答",
                    reasoning_content="先思考再思考",
                    usage={},
                    finish_reason="stop",
                ),
            )

    clock = FakeClock()
    provider = _ReasoningProvider()
    tools = FakeTools()
    emitter = FakeEmitter()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=tools,
        context_runtime=FakeContext(),
        event_emitter=emitter,
        max_iterations=10,
        clock=clock,
    )
    result = await runner.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert result.reasoning_elapsed_s is not None
    assert result.reasoning_elapsed_s >= 0.15


@pytest.mark.asyncio
async def test_reasoning_elapsed_s_prefers_provider_value():
    """#834 / review: completed 事件携带的 provider per-attempt 值（重试排除）
    无条件优先于粗钟——粗钟只做 provider 未上报时的兜底。"""
    import asyncio

    class _ProviderWithElapsed(FakeProvider):
        def __init__(self):
            """Test double for the reasoning-elapsed scenarios."""
            super().__init__(tool_rounds=0)

        async def stream_chat(self, **kwargs):
            """Test double for the reasoning-elapsed scenarios."""
            self.calls += 1
            # 粗钟会测到 0.2s+（首 delta 前 sleep）；provider 上报的精确值
            # 应覆盖它（如 0.05s 的小值、或真实服务端思考的大值）。
            await asyncio.sleep(0.2)
            yield SimpleNamespace(kind="reasoning_delta", delta="思考")
            yield SimpleNamespace(kind="content_delta", delta="回答")
            yield SimpleNamespace(
                kind="completed",
                response=SimpleNamespace(
                    has_tool_calls=False,
                    tool_calls=[],
                    content="回答",
                    reasoning_content="思考",
                    reasoning_elapsed_s=0.05,  # provider 精确测量（排除失败尝试）
                    usage={},
                    finish_reason="stop",
                ),
            )

    clock = FakeClock()
    provider = _ProviderWithElapsed()
    tools = FakeTools()
    emitter = FakeEmitter()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=tools,
        context_runtime=FakeContext(),
        event_emitter=emitter,
        max_iterations=10,
        clock=clock,
    )
    result = await runner.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    # provider 值（0.05）优先，而非粗钟测到的 ~0.2s
    assert result.reasoning_elapsed_s == 0.05


@pytest.mark.asyncio
async def test_reasoning_elapsed_first_value_survives_later_suppressed_round():
    """CodeRabbit #856: 第 1 轮 DeepSeek 建立 provider 值（60s）→ 第 2 轮
    交错流式 suppressed → 首值保留（跨轮权威），不被后续抑制清掉。"""
    import asyncio

    class _MixedProvider(FakeProvider):
        def __init__(self):
            """Test double for the reasoning-elapsed scenarios."""
            super().__init__(tool_rounds=0)

        async def stream_chat(self, **kwargs):
            """Test double for the reasoning-elapsed scenarios."""
            self.calls += 1
            if self.calls == 1:
                # 第 1 轮：缓冲思考（DeepSeek），请求工具
                yield SimpleNamespace(kind="reasoning_delta", delta="思考一")
                yield SimpleNamespace(
                    kind="completed",
                    response=SimpleNamespace(
                        has_tool_calls=True,
                        tool_calls=[SimpleNamespace(
                            id="tc1", name="web_search",
                            arguments={}, arguments_json="{}",
                        )],
                        content="",
                        reasoning_content="思考一",
                        reasoning_elapsed_s=60.0,
                        reasoning_elapsed_suppressed=False,
                        usage={},
                        finish_reason="tool_calls",
                    ),
                )
            else:
                # 第 2 轮：交错流式（Kimi）→ 抑制
                await asyncio.sleep(0.05)
                yield SimpleNamespace(kind="reasoning_delta", delta="思考二")
                yield SimpleNamespace(kind="content_delta", delta="最终回答")
                yield SimpleNamespace(
                    kind="completed",
                    response=SimpleNamespace(
                        has_tool_calls=False,
                        tool_calls=[],
                        content="最终回答",
                        reasoning_content="思考二",
                        reasoning_elapsed_s=None,
                        reasoning_elapsed_suppressed=True,
                        usage={},
                        finish_reason="stop",
                    ),
                )

    clock = FakeClock()
    provider = _MixedProvider()
    tools = FakeTools()
    emitter = FakeEmitter()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=tools,
        context_runtime=FakeContext(),
        event_emitter=emitter,
        max_iterations=10,
        clock=clock,
    )
    result = await runner.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    assert len(tools.executed) == 1  # 第 1 轮工具已执行
    # 首轮 provider 值（60s）跨轮保留，不被第 2 轮抑制清掉
    assert result.reasoning_elapsed_s == 60.0


@pytest.mark.asyncio
async def test_reasoning_elapsed_suppressed_clears_coarse_placeholder():
    """CodeRabbit #856: 首轮就是交错流式（suppressed）时，粗钟占位必须被
    清除——TurnResult 返回 None，前端回退本地 span。"""
    import asyncio

    class _InterleavedProvider(FakeProvider):
        def __init__(self):
            """Test double for the reasoning-elapsed scenarios."""
            super().__init__(tool_rounds=0)

        async def stream_chat(self, **kwargs):
            """Test double for the reasoning-elapsed scenarios."""
            self.calls += 1
            await asyncio.sleep(0.05)  # 粗钟会占到 ~0.05s
            yield SimpleNamespace(kind="reasoning_delta", delta="思考")
            yield SimpleNamespace(kind="content_delta", delta="回答")
            yield SimpleNamespace(
                kind="completed",
                response=SimpleNamespace(
                    has_tool_calls=False,
                    tool_calls=[],
                    content="回答",
                    reasoning_content="思考",
                    reasoning_elapsed_s=None,
                    reasoning_elapsed_suppressed=True,
                    usage={},
                    finish_reason="stop",
                ),
            )

    clock = FakeClock()
    provider = _InterleavedProvider()
    tools = FakeTools()
    emitter = FakeEmitter()
    runner = TurnRunner(
        provider=provider,
        tool_runtime=tools,
        context_runtime=FakeContext(),
        event_emitter=emitter,
        max_iterations=10,
        clock=clock,
    )
    result = await runner.run(
        turn=_mk_turn("think"),
        user_content="hi",
        system_prompt="",
        tools=[],
    )
    # 粗钟占位（~0.05s）被抑制信号清除 → None（前端本地 span 兜底）
    assert result.reasoning_elapsed_s is None
