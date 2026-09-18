"""Action Guard（外部复核 9-11）：高危外部副作用（risk>=10）fail-closed 派发前强制确认。

模型不先调 request_action_confirmation、直接发起 upload/破坏性 delete 等时，
runtime 必须在真实执行边界拦截——无确认通道 → APPROVAL_REQUIRED（不静默放行）；
有通道 → 弹卡；拒绝 → DENY；确认 → 放行且会话级缓存。

注：should_confirm_action 对 delete_file 仅在破坏性场景（delete_dir / recursive /
通配 / 敏感路径）触发——普通单文件删除走既有 file_write 审批链，不进 guard。
"""

import asyncio
from types import SimpleNamespace

from miqi.execution.permission_engine import PermissionEngine, PermissionVerdict


def _ctx(tool, args=None, thread="t1", turn="n1", bypass=False, families=None):
    """families=None → 不设该字段（等价于现状，供既有用例当回归基线）。"""
    ctx = SimpleNamespace(
        tool_name=tool,
        arguments=args or {},
        thread_id=thread,
        turn_id=turn,
        bypass_approval=bypass,
        force_approval=False,
        permission_profile=None,
        client_id="",
        session_id="",
    )
    if families is not None:
        ctx.action_confirmed_families = frozenset(families)
    return ctx


def test_guard_headless_requires_approval():
    """无 resolver（headless/CLI）→ APPROVAL_REQUIRED（fail-closed，不静默放行）。"""
    engine = PermissionEngine()
    decision = asyncio.run(engine.check(_ctx("delete_dir", {"path": "build/"})))
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert "Action Guard" in (decision.reason or "")


def test_guard_denied_without_runtime_confirm():
    """模型直接破坏性删除、用户拒绝 → DENY（真实动作绝不执行）。"""

    async def resolver(payload):
        assert payload["title"] == "危险动作确认"
        assert payload["allow_remember_choice"] is False
        return {"status": "submitted", "answers": {"choice_id": "cancel"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    decision = asyncio.run(engine.check(_ctx("delete_dir", {"path": "build/"})))
    assert decision.verdict == PermissionVerdict.DENY
    assert "Action Guard" in (decision.reason or "")


def test_guard_confirmed_then_session_cached():
    """用户确认 → guard 放行；同 thread 同工具会话内不再重复弹卡（同 thread+tool → 1 张卡）。

    授权模型：确认范围 = 同一 thread 内同一工具（thread + tool_name），键不看参数——
    见 docs/dev-notes/action-guard-confirmation-scope.md（三条不变式：判定看参数 /
    缓存按 thread+tool / 卡面明示）。
    """
    calls = []

    async def resolver(payload):
        calls.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "confirm"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    d1 = asyncio.run(engine.check(_ctx("upload", {"path": "x"})))
    assert "Action Guard" not in (d1.reason or "")
    d2 = asyncio.run(engine.check(_ctx("upload", {"path": "y"})))
    assert "Action Guard" not in (d2.reason or "")
    assert len(calls) == 1


def test_guard_does_not_touch_low_risk_tools():
    """write_file（2）/ exec（5）不进 guard——各自审批链负责。"""
    engine = PermissionEngine()
    for tool, args in (("write_file", {"path": "a.txt"}), ("exec", {"command": "echo hi"})):
        decision = asyncio.run(engine.check(_ctx(tool, args)))
        assert "Action Guard" not in (decision.reason or "")


def test_guard_not_bypassed_by_execution_policy():
    """#1102：bypass_approval 只跳过后面的分类审批流，**不跳过** Action Guard 兜底。

    auto 与 plan 都由执行策略自动置位 bypass_approval（用户在模式选择器上授权的是
    「普通动作免确认」，不是「高危动作免兜底」）。bypass 若短路在 guard 之前，
    高危动作在 auto 下就没有任何确认环节。
    """
    engine = PermissionEngine()
    decision = asyncio.run(engine.check(_ctx("spawn", {}, bypass=True)))
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert "Action Guard" in (decision.reason or "")


def test_guard_not_bypassed_still_prompts_with_resolver():
    """#1102：auto 下高危动作照常弹卡——确认则放行、拒绝则 DENY。"""
    calls = []

    async def confirm(payload):
        calls.append(payload["tool_name"])
        return {"status": "submitted", "answers": {"choice_id": "confirm"}}

    engine = PermissionEngine(action_guard_resolver=confirm)
    decision = asyncio.run(engine.check(_ctx("spawn", {}, bypass=True)))
    assert calls == ["spawn"]
    assert decision.verdict == PermissionVerdict.ALLOW

    async def cancel(payload):
        calls.append(payload["tool_name"])
        return {"status": "submitted", "answers": {"choice_id": "cancel"}}

    denying = PermissionEngine(action_guard_resolver=cancel)
    decision = asyncio.run(denying.check(_ctx("spawn", {}, bypass=True)))
    assert decision.verdict == PermissionVerdict.DENY
    assert "Action Guard" in (decision.reason or "")


def test_guard_fails_closed_on_malformed_arguments():
    """畸形参数（非 dict）不得被吞成「非高危」而静默放行。

    旧写法会让 should_confirm_action 内部的 `args.get(...)` 抛 AttributeError，
    冒泡进 except 后返回 None（让位给后续门）——auto 下就是直接 ALLOW。参数级
    判定做不了时按高危处理。主路径上畸形参数会先被 orchestrator 的 schema 校验
    挡掉，这里锁的是那条路径被绕过时的兜底。
    """
    engine = PermissionEngine()
    # bypass=True 即 auto：guard 必须仍排在 bypass 之前。只测 bypass=False 的话，
    # 「把 bypass 短路挪回 guard 之前」这类回归不会让本用例变红。
    for bypass in (False, True):
        for bad in ("rm -rf /", ["a"], 42, None):
            ctx = _ctx("spawn", {}, bypass=bypass)
            ctx.arguments = bad
            decision = asyncio.run(engine.check(ctx))
            assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED, (bypass, bad)
            assert "fail-closed" in (decision.reason or ""), (bypass, bad)


def test_guard_fails_closed_when_decision_table_unavailable(monkeypatch):
    """判定表不可用 ≠ 判定为非高危——前者不得放行（与 docstring 的 fail-closed 一致）。

    `from ... import should_confirm_action` 每次调用都会重读模块属性，
    因此 monkeypatch 模块属性即可模拟判定表本身出故障。
    """
    import miqi.execution.task_policy as task_policy

    def boom(*_args, **_kwargs):
        raise RuntimeError("decision table unavailable")

    monkeypatch.setattr(task_policy, "should_confirm_action", boom)
    engine = PermissionEngine()
    # bypass=True 即 auto：同上，两种取值都测，锁住「guard 早于 bypass」。
    for bypass in (False, True):
        decision = asyncio.run(engine.check(_ctx("spawn", {}, bypass=bypass)))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED, bypass
        assert "fail-closed" in (decision.reason or ""), bypass


def test_guard_defers_to_manual_mode():
    """manual（force_approval）下 guard 让位：走「手动模式」确认，不叠加专用卡。

    两张卡对同一个动作没有增量安全性；而 guard 卡面「同类动作不再逐一询问」的
    会话缓存语义在 manual 下并不成立（guard 弃权后 force 会再次拦下），叠加反而
    让卡面文案失真。guard 在代码里排在 force 之前，只为满足「早于 bypass」的顺序约束。
    """
    engine = PermissionEngine()
    ctx = _ctx("spawn", {})
    ctx.force_approval = True
    decision = asyncio.run(engine.check(ctx))
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert "手动模式" in (decision.description or "")
    assert "Action Guard" not in (decision.reason or "")


def test_guard_survives_both_policy_flags():
    """bypass + force 同时置位：普通动作照旧 bypass 放行，高危动作仍被 guard 拦下。

    让位条件写的是「force 且非 bypass」——若写成「force」，bypass 会经由 force 这个
    入口重新绕开 guard（#1102 的语义被换个入口绕过）。生产上没有任何模式同时置位，
    本用例锁的是这个合成组合。
    """
    engine = PermissionEngine()

    normal = _ctx("exec", {"command": "ls"}, bypass=True)
    normal.force_approval = True
    decision = asyncio.run(engine.check(normal))
    assert decision.verdict == PermissionVerdict.ALLOW
    assert "Bypassed by execution policy" in (decision.reason or "")

    risky = _ctx("spawn", {}, bypass=True)
    risky.force_approval = True
    decision = asyncio.run(engine.check(risky))
    assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
    assert "Action Guard" in (decision.reason or "")


def test_bypass_still_skips_normal_approval_flow():
    """#1102 回归：bypass 对**非高危**工具仍完全免确认——别把 auto 修成 manual。

    同时断言 reason 而不只是 verdict：read_file 虽然也在 READ_ONLY_TOOLS 里，但
    bypass 分支排在只读放行**之前**，所以这些工具在 bypass 下走的就是 bypass 那条
    通路。只断言 ALLOW 区分不出「bypass 放行」与「只读/分类放行」，删掉 bypass
    短路也照样绿。
    """
    engine = PermissionEngine()
    for tool, args in (
        ("read_file", {"path": "a.txt"}),
        ("write_file", {"path": "a.txt"}),
        ("edit_file", {"path": "a.txt"}),
        ("exec", {"command": "rm -rf build"}),
        ("web_search", {"query": "x"}),
        ("memory", {"content": "x"}),
    ):
        decision = asyncio.run(engine.check(_ctx(tool, args, bypass=True)))
        assert decision.verdict == PermissionVerdict.ALLOW, tool
        assert "Bypassed by execution policy" in (decision.reason or ""), tool


def test_guard_different_tools_each_prompt():
    """不同工具各自弹卡：确认 upload 不放行 delete_file（键含 tool_name）。"""
    calls = []

    async def resolver(payload):
        calls.append(payload["tool_name"])
        return {"status": "submitted", "answers": {"choice_id": "confirm"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    asyncio.run(engine.check(_ctx("upload", {"path": "x"})))
    asyncio.run(engine.check(_ctx("delete_file", {"path": ".ssh/id_rsa"})))
    assert calls == ["upload", "delete_file"]


def test_guard_different_threads_each_prompt():
    """不同 thread 各自弹卡：t1 的确认不继承到 t2（键含 thread_id）。"""
    calls = []

    async def resolver(payload):
        calls.append(payload["thread_id"])
        return {"status": "submitted", "answers": {"choice_id": "confirm"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    asyncio.run(engine.check(_ctx("upload", {"path": "x"}, thread="t1")))
    asyncio.run(engine.check(_ctx("upload", {"path": "x"}, thread="t2")))
    assert calls == ["t1", "t2"]


def test_guard_payload_contract_declares_scope():
    """卡面明示义务：message 写明同类动作不再逐一询问；记忆选择仍为 False。

    #1071 评审 P2-b：卡面还必须写明「对什么执行」——只给 tool_name 等于让用户
    闭眼确认。具体目标从 ctx.arguments 通用提取（路径/目的地/文件名/大小）。
    """
    seen = []

    async def resolver(payload):
        seen.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "cancel"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    asyncio.run(
        engine.check(
            _ctx(
                "upload",
                {
                    "path": "outputs/mof-report.json",
                    "destination": "MiqroForge",
                    "size_bytes": 2048,
                },
            )
        )
    )
    assert len(seen) == 1
    message = seen[0]["message"]
    assert "不再逐一询问" in message
    # 安全相关参数出现在卡面（而非只有 tool_name）
    assert "outputs/mof-report.json" in message
    assert "MiqroForge" in message
    assert "2048" in message
    assert seen[0]["allow_remember_choice"] is False


def test_guard_payload_message_falls_back_without_arguments():
    """无可提取参数时退回原文案（不留空括号，不改既有语义）。"""
    seen = []

    async def resolver(payload):
        seen.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "cancel"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    asyncio.run(engine.check(_ctx("delete_dir", {})))
    assert seen[0]["message"] == (
        "模型请求执行高危动作：delete_dir。确认后才真正执行。"
        "（确认后本对话内同类动作将不再逐一询问）"
    )


def test_guard_confirmed_cache_capped_at_512():
    """缓存上界：预填 512 项 → 再确认一次即清空重建（最坏退化为多弹卡，方向安全）。"""
    calls = []

    async def resolver(payload):
        calls.append(payload)
        return {"status": "submitted", "answers": {"choice_id": "confirm"}}

    engine = PermissionEngine(action_guard_resolver=resolver)
    engine._action_guard_confirmed.update(f"t{i}:upload" for i in range(100, 612))
    assert len(engine._action_guard_confirmed) == 512
    decision = asyncio.run(engine.check(_ctx("upload", {"path": "x"}, thread="t1")))
    assert "Action Guard" not in (decision.reason or "")
    assert len(calls) == 1
    assert engine._action_guard_confirmed == {"t1:upload"}


# ── 模型侧 ActionCard 确认后的同族去重（#646-v2 R2d C7）────────────────────

def _counting_engine(choice_id="cancel"):
    calls = []

    async def resolver(payload):
        calls.append(payload)
        return {"status": "submitted", "answers": {"choice_id": choice_id}}

    return PermissionEngine(action_guard_resolver=resolver), calls


def test_guard_skips_card_for_confirmed_family():
    """同族跳过：ActionCard 已确认 upload → upload_run 真实执行时不再弹卡。"""
    engine, calls = _counting_engine()
    decision = asyncio.run(
        engine.check(_ctx("upload_run", {"path": "x"}, families={"upload"}))
    )
    assert len(calls) == 0
    assert "Action Guard" not in (decision.reason or "")


def test_guard_still_prompts_for_cross_family():
    """跨族不放行：已确认 upload 不能顺带放行 delete 家族。"""
    engine, calls = _counting_engine()
    decision = asyncio.run(
        engine.check(_ctx("delete_dir", {"path": "build/"}, families={"upload"}))
    )
    assert len(calls) == 1
    assert decision.verdict == PermissionVerdict.DENY
    assert "Action Guard" in (decision.reason or "")


def test_guard_without_family_field_behaves_as_before():
    """无该字段（旧 ctx / 非 ActionCard 路径）→ 行为与现状一致：照常弹卡。"""
    engine, calls = _counting_engine()
    decision = asyncio.run(engine.check(_ctx("upload", {"path": "x"})))
    assert len(calls) == 1
    assert decision.verdict == PermissionVerdict.DENY


def test_guard_headless_still_requires_approval_when_family_confirmed():
    """兜底不因新字段失效：headless 无弹卡通道 → 即使同族已确认仍 APPROVAL_REQUIRED。

    注意断言的是 verdict 而非 reason：同族已确认时 guard **主动弃权**（返回 None），
    决策改由后续门给出（本夹具里是未知工具门，reason 因此不含 "Action Guard"）。
    关键是 fail-closed 不变——新字段只免掉重复弹卡，不构成放行通道。
    """
    engine = PermissionEngine()
    for tool, args, family in (
        ("delete_dir", {"path": "build/"}, "delete"),
        ("upload", {"path": "x"}, "upload"),
    ):
        decision = asyncio.run(engine.check(_ctx(tool, args, families={family})))
        assert decision.verdict == PermissionVerdict.APPROVAL_REQUIRED
