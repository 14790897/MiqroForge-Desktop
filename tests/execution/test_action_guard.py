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


def _ctx(tool, args=None, thread="t1", turn="n1", bypass=False):
    return SimpleNamespace(
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
    """用户确认 → guard 放行；同 thread 同类动作会话内不再重复弹卡。"""
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
