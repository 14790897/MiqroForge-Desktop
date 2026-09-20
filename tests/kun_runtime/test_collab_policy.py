"""Collaboration policy tests (issue #646 design v2).

Two AI reviews converged: the harness — not the model — decides when to ask.
This policy is the "collaboration gate": risk-classified tool intents mapped
against the user's autonomy mode. External transfers / payments ALWAYS
confirm, independent of approval bypass.
"""

import asyncio

from miqi.execution.collab_policy import (
    AutonomyMode,
    CollabVerdict,
    RiskLevel,
    evaluate,
    risk_of,
)


class TestRiskClassification:
    def test_read_tools_are_read(self):
        assert risk_of("web_search") == RiskLevel.READ
        assert risk_of("read_file") == RiskLevel.READ
        assert risk_of("paper_search") == RiskLevel.READ

    def test_unknown_tool_fails_closed(self):
        # Unrecognised tools are UNKNOWN: DENY in plan, CONFIRM in manual
        # (CodeRabbit #711) — never silently classified as safe read.
        assert risk_of("unknown_tool") == RiskLevel.UNKNOWN
        assert evaluate("unknown_tool", AutonomyMode.PLAN) == CollabVerdict.DENY
        assert evaluate("unknown_tool", AutonomyMode.MANUAL) == CollabVerdict.CONFIRM
        assert evaluate("unknown_tool", AutonomyMode.SUPERVISED) == CollabVerdict.ALLOW

    def test_write_exec_external_payment(self):
        assert risk_of("write_file") == RiskLevel.WRITE
        assert risk_of("create_pdf") == RiskLevel.WRITE
        assert risk_of("exec") == RiskLevel.EXEC
        assert risk_of("bash") == RiskLevel.EXEC
        assert risk_of("upload_workflow") == RiskLevel.EXTERNAL
        assert risk_of("data_upload") == RiskLevel.EXTERNAL
        assert risk_of("purchase") == RiskLevel.PAYMENT


class TestModeMatrix:
    def test_read_always_allowed(self):
        for mode in AutonomyMode:
            assert evaluate("web_search", mode) == CollabVerdict.ALLOW
            assert evaluate("read_file", mode) == CollabVerdict.ALLOW

    def test_autonomous_writes_auto_but_external_always_confirms(self):
        # 自动模式：写文件自动（低风险自主），外发/付费必须确认
        assert evaluate("write_file", AutonomyMode.AUTONOMOUS) == CollabVerdict.ALLOW
        assert evaluate("exec", AutonomyMode.AUTONOMOUS) == CollabVerdict.ALLOW
        assert evaluate("upload_workflow", AutonomyMode.AUTONOMOUS) == CollabVerdict.CONFIRM
        assert evaluate("data_upload", AutonomyMode.AUTONOMOUS) == CollabVerdict.CONFIRM
        assert evaluate("purchase", AutonomyMode.AUTONOMOUS) == CollabVerdict.CONFIRM

    def test_supervised_confirms_exec(self):
        # 允许编辑模式：写文件自动，执行/外发确认
        assert evaluate("write_file", AutonomyMode.SUPERVISED) == CollabVerdict.ALLOW
        assert evaluate("exec", AutonomyMode.SUPERVISED) == CollabVerdict.CONFIRM
        assert evaluate("upload_workflow", AutonomyMode.SUPERVISED) == CollabVerdict.CONFIRM

    def test_manual_confirms_writes(self):
        assert evaluate("write_file", AutonomyMode.MANUAL) == CollabVerdict.CONFIRM
        assert evaluate("exec", AutonomyMode.MANUAL) == CollabVerdict.CONFIRM

    def test_plan_denies_writes_and_exec(self):
        assert evaluate("write_file", AutonomyMode.PLAN) == CollabVerdict.DENY
        assert evaluate("exec", AutonomyMode.PLAN) == CollabVerdict.DENY
        assert evaluate("upload_workflow", AutonomyMode.PLAN) == CollabVerdict.DENY

    def test_external_never_bypassable(self):
        """外发/付费在任何执行模式下都确认（PLAN 例外：只分析 → DENY）。
        协作门不受审批 bypass 影响。"""
        for mode in AutonomyMode:
            if mode == AutonomyMode.PLAN:
                assert evaluate("upload_workflow", mode) == CollabVerdict.DENY
            else:
                assert evaluate("upload_workflow", mode) == CollabVerdict.CONFIRM
                assert evaluate("purchase", mode) == CollabVerdict.CONFIRM


class TestExecCommandExternalEffect:
    """#1101：exec 族工具的命令串命中上传签名 → 抬升为 EXTERNAL。"""

    def test_risk_of_raises_exec_upload_to_external(self):
        assert (
            risk_of("exec", "python scripts/upload_run.py x.json --json")
            == RiskLevel.EXTERNAL
        )
        assert (
            risk_of("exec", "curl -X POST https://api/dataUpload")
            == RiskLevel.EXTERNAL
        )
        assert risk_of("bash", "python upload_run.py x.json") == RiskLevel.EXTERNAL
        # 普通 exec / 无命令仍按 EXEC 分类
        assert risk_of("exec", "python train.py") == RiskLevel.EXEC
        assert risk_of("exec", None) == RiskLevel.EXEC

    def test_evaluate_confirms_exec_upload_in_autonomous(self):
        # 自治模式 exec 本身不确认，但 exec 上传必须确认（EXTERNAL 在 AUTONOMOUS 恒 CONFIRM）
        assert (
            evaluate("exec", AutonomyMode.AUTONOMOUS, "python upload_run.py x.json")
            == CollabVerdict.CONFIRM
        )
        assert (
            evaluate("exec", AutonomyMode.AUTONOMOUS, "python train.py")
            == CollabVerdict.ALLOW
        )
        assert (
            evaluate("exec", AutonomyMode.SUPERVISED, "python upload_run.py x.json")
            == CollabVerdict.CONFIRM
        )
        assert (
            evaluate("exec", AutonomyMode.PLAN, "python upload_run.py x.json")
            == CollabVerdict.DENY
        )


class TestToolHostCollabGate:
    """tool_host 集成：collab gate 在工具执行前强制弹确认卡。"""

    def _ctx(self, mode="supervised", await_user_input=None, ctx_mode=None):
        from miqi.kun_runtime.tool_host import ToolHostContext

        return ToolHostContext(
            thread_id="thr",
            turn_id="turn",
            workspace="/tmp/ws",
            autonomy_mode=mode,
            await_user_input=await_user_input,
            mode=ctx_mode,
        )

    def _host(self):
        from pathlib import Path

        from miqi.kun_runtime.tool_host import MiQiToolHost
        from miqi.runtime.tool_registry_factory import create_runtime_tool_registry

        registry = create_runtime_tool_registry(config={}, workspace=Path("/tmp/ws"))
        return MiQiToolHost(registry)

    def test_external_tool_blocks_until_confirm(self):
        from miqi.kun_runtime.tool_host import ToolCallLike

        gate_calls = []

        async def await_user_input(payload):
            gate_calls.append(payload)
            return {"status": "submitted", "answers": {"choice_id": "confirm", "choice_label": "确认执行"}}

        host = self._host()
        # manual 模式下 write_file 需要确认卡（registry 中存在该工具）
        call = ToolCallLike(call_id="c1", tool_name="write_file", arguments={"path": "/tmp/ws/a.txt", "content": "hi"})
        asyncio.run(host.execute(call, self._ctx(mode="manual", await_user_input=await_user_input)))
        assert len(gate_calls) == 1, "写文件在 manual 模式必须先经过确认卡"
        assert gate_calls[0]["title"] == "确认执行：write_file"
        assert "cancel" in [c["id"] for c in gate_calls[0]["choices"]]

    def test_read_tool_skips_gate(self):
        from miqi.kun_runtime.tool_host import ToolCallLike

        gate_calls = []

        async def await_user_input(payload):
            gate_calls.append(payload)
            return {"status": "submitted", "answers": {"choice_id": "confirm"}}

        host = self._host()
        call = ToolCallLike(call_id="c1", tool_name="read_file", arguments={"path": "/tmp/x"})
        asyncio.run(host.execute(call, self._ctx(mode="autonomous", await_user_input=await_user_input)))
        assert gate_calls == [], "读类工具不应触发确认卡"

    def test_user_cancel_returns_cancelled(self):
        from miqi.kun_runtime.tool_host import ToolCallLike

        async def await_user_input(payload):
            return {"status": "submitted", "answers": {"choice_id": "cancel", "choice_label": "取消"}}

        host = self._host()
        call = ToolCallLike(call_id="c1", tool_name="write_file", arguments={"path": "/tmp/ws/a.txt", "content": "x"})
        result = asyncio.run(host.execute(call, self._ctx(mode="manual", await_user_input=await_user_input)))
        assert result.item["status"] == "cancelled"
        assert result.item["isError"] is True

    def test_no_channel_falls_back_to_execute(self, tmp_path):
        """无 await_user_input 通道（headless）时 collab gate 不阻塞，走正常执行。"""
        from miqi.kun_runtime.tool_host import ToolCallLike

        host = self._host()
        missing = tmp_path / "nope.txt"
        call = ToolCallLike(call_id="c1", tool_name="read_file", arguments={"path": str(missing)})
        result = asyncio.run(host.execute(call, self._ctx(mode="supervised")))
        # 无通道 → 不弹卡直接执行（读文件不存在 → 返回错误而非 gate 拦截）。
        # 不断言具体错误文案：平台间 not found 措辞不同（CodeRabbit #711）。
        assert result.item["status"] == "failed"

    def test_exec_upload_confirms_in_autonomous(self):
        """#1101：exec 执行 upload_run.py 在 autonomous 模式也必须先弹确认卡。"""
        from miqi.kun_runtime.tool_host import ToolCallLike

        gate_calls = []

        async def await_user_input(payload):
            gate_calls.append(payload)
            return {"status": "submitted", "answers": {"choice_id": "cancel", "choice_label": "取消"}}

        host = self._host()
        call = ToolCallLike(
            call_id="c1",
            tool_name="exec",
            arguments={"command": "python scripts/upload_run.py x.json --json"},
        )
        result = asyncio.run(
            host.execute(call, self._ctx(mode="autonomous", await_user_input=await_user_input))
        )
        assert len(gate_calls) == 1, "exec 上传在 autonomous 模式必须先经过确认卡"
        assert result.item["status"] == "cancelled"

    def test_exec_upload_confirms_even_in_fast_mode(self):
        """#1101：fast 模式不豁免对外上传——exec 执行 upload_run.py 仍弹卡，取消即停止。"""
        from miqi.kun_runtime.tool_host import ToolCallLike

        gate_calls = []

        async def await_user_input(payload):
            gate_calls.append(payload)
            return {"status": "submitted", "answers": {"choice_id": "cancel", "choice_label": "取消"}}

        host = self._host()
        call = ToolCallLike(
            call_id="c1",
            tool_name="exec",
            arguments={"command": "python scripts/upload_run.py x.json --json"},
        )
        result = asyncio.run(
            host.execute(
                call,
                self._ctx(mode="autonomous", await_user_input=await_user_input, ctx_mode="fast"),
            )
        )
        assert len(gate_calls) == 1, "fast 模式也不能豁免 exec 上传确认"
        assert result.item["status"] == "cancelled"

    def test_fast_mode_still_skips_non_external_confirm(self):
        """#1101 回归：fast 模式对非对外动作（写文件）仍保持免确认。"""
        from miqi.kun_runtime.tool_host import ToolCallLike

        gate_calls = []

        async def await_user_input(payload):
            gate_calls.append(payload)
            return {"status": "submitted", "answers": {"choice_id": "confirm"}}

        host = self._host()
        call = ToolCallLike(
            call_id="c1",
            tool_name="write_file",
            arguments={"path": "/tmp/ws/a.txt", "content": "hi"},
        )
        asyncio.run(
            host.execute(
                call,
                self._ctx(mode="manual", await_user_input=await_user_input, ctx_mode="fast"),
            )
        )
        assert gate_calls == [], "fast 模式对非对外动作应保持免确认"
