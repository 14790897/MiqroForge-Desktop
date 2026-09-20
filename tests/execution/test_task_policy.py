"""Task and action policy tests for #646."""

from miqi.execution.task_policy import (
    ACTION_CONFIRM_THRESHOLD,
    ACTION_FAMILY,
    EXEC_FAMILY_TOOLS,
    TOOL_RISK,
    action_family,
    complexity_score,
    exec_command,
    external_effect_of_command,
    is_mutation_tool,
    phase_for_tool,
    should_confirm_action,
    should_plan_confirm,
    tool_risk,
)


def test_phase_classification():
    assert phase_for_tool("web_search") == "READ"
    assert phase_for_tool("read_file") == "READ"
    assert phase_for_tool("write_file") == "WRITE"
    assert phase_for_tool("exec") == "EXEC"
    assert phase_for_tool("upload") == "EXTERNAL"
    assert phase_for_tool("payment") == "EXTERNAL"
    assert phase_for_tool("remove_file") == "EXTERNAL"
    assert phase_for_tool("rm") == "EXTERNAL"
    assert phase_for_tool("unknown_tool") is None


def test_mutation_gate():
    assert is_mutation_tool("web_search") is False
    assert is_mutation_tool("read_file") is False
    assert is_mutation_tool("write_file") is True
    assert is_mutation_tool("exec") is True
    assert is_mutation_tool("upload") is True
    assert is_mutation_tool("delete_dir") is True
    assert is_mutation_tool("remove_file") is True
    assert is_mutation_tool("rm") is True


def test_complexity_score_steps():
    assert complexity_score(n_tool_calls=1) == 0
    assert complexity_score(n_tool_calls=2) == 0
    assert complexity_score(n_tool_calls=3) == 1
    assert complexity_score(n_tool_calls=5) == 1
    assert complexity_score(n_tool_calls=6) == 2
    assert complexity_score(n_tool_calls=3, phase_history=["READ", "READ", "WRITE"]) == 3
    assert complexity_score(n_tool_calls=3, phase_history=["READ", "READ", "READ"]) == 1
    assert complexity_score(n_tool_calls=1, produces_artifact=True) == 2
    assert complexity_score(n_tool_calls=1, uses_skill=True) == 3
    assert complexity_score(n_tool_calls=3, uses_skill=True, produces_artifact=True) == 6


def test_plan_confirm_rules():
    # Plan 是任务级协作节点：单个明确写入不应把用户打断。
    assert should_plan_confirm(["write_file"]) is False
    # 纯读查询即使调用很多工具，也不应凭数量制造确认卡。
    assert should_plan_confirm(["web_search"] * 10) is False
    # 搜索→写入跨阶段，且产生本地产物，属于真正的多步骤任务。
    assert (
        should_plan_confirm(["web_search", "write_file"], phase_history=["READ", "WRITE"])
        is True
    )
    # 多工具 + 跨阶段 + artifact：明确复杂任务。
    assert (
        should_plan_confirm(
            ["web_search"] * 5 + ["write_file"],
            phase_history=["READ"] * 5 + ["WRITE"],
        )
        is True
    )
    # Skill 任务复杂度足够高。
    assert should_plan_confirm(["web_search"] * 3, uses_skill=True) is True
    assert should_plan_confirm(["web_search", "write_file"], mode="auto") is False
    assert should_plan_confirm(["web_search", "write_file"], mode="plan") is False


def test_action_confirm_rules():
    assert should_confirm_action("write_file") is False
    assert should_confirm_action("upload") is True
    assert should_confirm_action("payment") is True
    assert should_confirm_action("delete_file", {"path": "tmp.txt"}) is False
    assert should_confirm_action("delete_dir") is True
    assert should_confirm_action("delete_file", {"path": "logs/*.tmp"}) is True
    assert should_confirm_action("delete_file", {"path": "data", "recursive": True}) is True
    assert should_confirm_action("remove_file", {"path": "logs/*.tmp"}) is True
    assert should_confirm_action("rm", {"path": "data", "recursive": True}) is True


def test_tool_risk_table():
    assert tool_risk("web_search") == 0
    assert tool_risk("write_file") == 2
    assert tool_risk("exec") == 5
    assert tool_risk("upload") == 10
    # Unknown tools remain conservatively classified for Plan detection.
    assert tool_risk("future_tool") == 2


def test_sensitive_path_force_confirm():
    from miqi.execution.task_policy import _is_sensitive_path

    assert _is_sensitive_path({"path": "/repo/.git/config"}) is True
    assert _is_sensitive_path({"path": "~/.ssh/id_rsa"}) is True
    assert _is_sensitive_path({"path": "/repo/src/main.py"}) is False
    assert _is_sensitive_path({}) is False
    assert should_confirm_action("delete_file", {"path": "/repo/.git/config"}) is True


# ── 动作家族（#646-v2 R2d C7：模型侧确认 → guard 同族不重复弹卡）────────────

def test_action_family_mapping():
    """同族聚合：一次确认覆盖族内全部工具别名；未知工具无族（退回逐次弹卡）。"""
    for tool in ("upload", "upload_run", "qraft_upload"):
        assert action_family(tool) == "upload"
    for tool in ("delete_file", "delete_dir", "remove_file", "rm"):
        assert action_family(tool) == "delete"
    for tool in ("send_message", "spawn"):
        assert action_family(tool) == "external"
    assert action_family("payment") == "payment"
    assert action_family("read_file") is None
    assert action_family("future_tool") is None


def test_action_family_values_match_action_card_enum():
    """两侧词表必须逐字一致：guard 用 action_family() 去匹配 ActionCard 记录的 action。

    漂移会让同族去重静默失效——例如卡片记 ``external`` 而 spawn 记为 ``spawn``，
    用户已确认却仍弹兜底卡。
    """
    from miqi.agent.tools.request_action_confirmation import RequestActionConfirmationTool

    enum = set(
        RequestActionConfirmationTool().parameters["properties"]["action"]["enum"]
    )
    assert enum == {"upload", "payment", "delete", "external"}
    assert set(ACTION_FAMILY.values()) <= enum


def test_action_family_covers_exactly_the_guard_high_risk_set():
    """家族表 = guard 高危集合（risk >= 阈值），防两张表漂移。

    漏登记的高危工具会静默失去同族去重（退化为多弹卡，方向安全但破坏承诺），
    此断言把漂移挡在 CI。
    """
    high_risk = {
        name for name, risk in TOOL_RISK.items() if risk >= ACTION_CONFIRM_THRESHOLD
    }
    assert set(ACTION_FAMILY) == high_risk
    for tool in sorted(ACTION_FAMILY):
        assert should_confirm_action(tool, {"path": "/repo/.git/config"}) is True


# ── #1101：exec 族命令级外部副作用识别（上传经 exec 执行也必须进 guard）────────

def test_external_effect_of_command_detects_upload():
    """命中 upload_run.py / dataUpload 签名 → 识别为 upload 家族；本地脚本不误伤。"""
    assert (
        external_effect_of_command("python /skills/qraft/scripts/upload_run.py x.json --json")
        == "upload"
    )
    assert external_effect_of_command("python3 scripts/upload_run.py --json out.json") == "upload"
    assert external_effect_of_command("curl -X POST https://api/dataUpload") == "upload"
    # 同技能目录里的校验脚本是本地动作，不能误判为上传
    assert external_effect_of_command("python scripts/validate_run.py x.json") is None
    assert external_effect_of_command("python train.py") is None
    assert external_effect_of_command(None) is None
    assert external_effect_of_command("") is None


def test_exec_command_extraction():
    """兼容 command / cmd 两个键；非 dict 或空值返回 None。"""
    assert exec_command({"command": "python x.py"}) == "python x.py"
    assert exec_command({"cmd": "ls"}) == "ls"
    assert exec_command({"command": ""}) is None
    assert exec_command({}) is None
    assert exec_command("not-a-dict") is None


def test_should_confirm_action_raises_exec_upload():
    """exec 命中上传签名 → 按 EXTERNAL(10) 强制确认；普通 exec 仍按 EXEC(5) 不进 guard。"""
    assert (
        should_confirm_action("exec", {"command": "python scripts/upload_run.py x.json --json"})
        is True
    )
    assert (
        should_confirm_action("run_script", {"command": "python upload_run.py --json"})
        is True
    )
    assert should_confirm_action("exec", {"command": "python validate_run.py x.json"}) is False
    assert should_confirm_action("exec", {"command": "echo hi"}) is False


def test_action_family_raises_exec_upload():
    """exec 命中上传签名 → family 归 upload，同族去重/模型侧确认都按 upload 生效。"""
    assert action_family("exec", {"command": "python upload_run.py x.json"}) == "upload"
    assert action_family("exec", {"command": "python train.py"}) is None
    # 无参数调用保持原语义（非 exec 族照常查 ACTION_FAMILY）
    assert action_family("upload") == "upload"
    assert EXEC_FAMILY_TOOLS == {
        "exec", "run_script", "python", "bash", "shell", "run_command", "execute",
    }
