"""Task and action policy tests for #646."""

from miqi.execution.task_policy import (
    complexity_score,
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
