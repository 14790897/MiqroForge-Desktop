import json

from miqi.agent.tools.ask_user_plan_confirm import AskUserPlanConfirmTool


def test_build_result_preserves_adjustment_feedback():
    result = AskUserPlanConfirmTool.build_result({
        "status": "submitted",
        "answers": {
            "choice_id": "modify",
            "choice_label": "不要上传，先本地完成报告并运行测试。",
        },
    })
    payload = json.loads(result)

    assert payload["status"] == "modify_requested"
    assert payload["choice_id"] == "modify"
    assert payload["adjustment"] == "不要上传，先本地完成报告并运行测试。"
    assert payload["plan_confirmed"] is False


def test_build_result_treats_adjust_as_modify():
    result = AskUserPlanConfirmTool.build_result({
        "status": "submitted",
        "answers": {
            "choice_id": "adjust",
            "choice_label": "删掉上传步骤",
        },
    })
    payload = json.loads(result)

    assert payload["choice_id"] == "modify"
    assert payload["adjustment"] == "删掉上传步骤"
