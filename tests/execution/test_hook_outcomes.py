import pytest

from miqi.execution.hook_runtime import (
    HookOutcome,
    HookPoint,
    HookRegistration,
    HookRuntime,
)


class _Ctx:
    def __init__(self, tool_name="exec", arguments=None):
        self.tool_name = tool_name
        self.arguments = arguments or {}


def test_missing_points_exist():
    names = {p.value for p in HookPoint}
    assert "permission_request" in names
    assert "stop" in names


@pytest.mark.asyncio
async def test_continue_outcome_is_default():
    rt = HookRuntime()
    out = await rt.run_with_outcome(HookPoint.PRE_TOOL_USE, _Ctx())
    assert out.action == "continue"


@pytest.mark.asyncio
async def test_block_outcome_short_circuits():
    rt = HookRuntime()

    async def veto(ctx):
        return HookOutcome.block("not allowed")

    async def never(ctx):
        raise AssertionError("must not run after block")

    rt.register(HookRegistration(HookPoint.PERMISSION_REQUEST, "*", veto, priority=10))
    rt.register(HookRegistration(HookPoint.PERMISSION_REQUEST, "*", never, priority=20))
    out = await rt.run_with_outcome(HookPoint.PERMISSION_REQUEST, _Ctx())
    assert out.action == "block"
    assert out.reason == "not allowed"


@pytest.mark.asyncio
async def test_modify_outcome_returns_patch():
    rt = HookRuntime()

    async def rewrite(ctx):
        return HookOutcome.modify({"arguments": {"command": "ls"}})

    rt.register(HookRegistration(HookPoint.PRE_TOOL_USE, "exec", rewrite))
    out = await rt.run_with_outcome(HookPoint.PRE_TOOL_USE, _Ctx())
    assert out.action == "modify"
    assert out.patch == {"arguments": {"command": "ls"}}


@pytest.mark.asyncio
async def test_legacy_run_still_works():
    rt = HookRuntime()
    calls = []

    async def cb(ctx):
        calls.append(ctx.tool_name)

    rt.register(HookRegistration(HookPoint.POST_TOOL_USE, "*", cb))
    await rt.run(HookPoint.POST_TOOL_USE, _Ctx())  # returns None, no raise
    assert calls == ["exec"]
