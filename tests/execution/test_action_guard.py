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


def test_guard_bypass_respected():
    """bypass_approval（用户显式全放行）→ 不弹卡。"""
    engine = PermissionEngine()
    decision = asyncio.run(engine.check(_ctx("delete_dir", {"path": "build/"}, bypass=True)))
    assert decision.verdict == PermissionVerdict.ALLOW


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
