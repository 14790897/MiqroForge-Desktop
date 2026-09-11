"""Agent Task Lifecycle 数据模型测试（#646-v2 v3.3 最终拍板）。"""

from miqi.runtime.task_objects import (
    AgentRunContext,
    ApprovedScope,
    ExternalAction,
    PlanSnapshot,
    TodoState,
    validate_transition,
)


def test_transition_validator():
    # 允许：QUEUED→IN_PROGRESS→COMPLETED；IN_PROGRESS⇄BLOCKED；任何→CANCELLED
    assert validate_transition("queued", "in_progress")
    assert validate_transition("in_progress", "completed")
    assert validate_transition("in_progress", "blocked")
    assert validate_transition("blocked", "in_progress")
    assert validate_transition("queued", "cancelled")
    assert validate_transition("in_progress", "cancelled")
    assert validate_transition("completed", "cancelled")
    # 禁止：COMPLETED→IN_PROGRESS（回滚）——除非人工
    assert not validate_transition("completed", "in_progress")
    assert not validate_transition("cancelled", "in_progress")


def test_plan_confirm_initializes_todo():
    ctx = AgentRunContext(session_key="sess-1")
    plan = PlanSnapshot(
        plan_id="plan-1",
        goal="MOF 调研",
        steps=[("research-literature", "搜集相关论文"), ("analyze", "分析实验条件"), ("report", "生成报告")],
    )
    ctx.plan_snapshot = plan
    ctx.todo_state.initialize_from_plan(plan.steps)
    assert len(ctx.todo_state.items) == 3
    assert all(i.status == "queued" for i in ctx.todo_state.items)
    assert all(i.kind == "plan" for i in ctx.todo_state.items)
    assert ctx.todo_state.revision == 1
    assert ctx.todo_state.item("research-literature") is not None


def test_merge_status_flip():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索"), ("b", "分析")])
    rejected = ts.merge([{"id": "a", "status": "in_progress"}])
    assert rejected == []
    assert ts.item("a").status == "in_progress"
    assert ts.revision == 2


def test_merge_plan_content_change_rejected():
    """v3.3：plan item 改 content → 拒绝（PLAN_MUTATION_REQUIRES_CONFIRMATION）。"""
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    rejected = ts.merge([{"id": "a", "content": "训练分类器", "kind": "plan"}])
    assert rejected and rejected[0]["reason"] == "PLAN_MUTATION_REQUIRES_CONFIRMATION"
    assert rejected[0]["suggestion"] == "ask_user_plan_confirm"
    assert ts.item("a").content == "搜索论文"  # 未被修改


def test_merge_auxiliary_add_allowed():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    rejected = ts.merge([{"id": "download-pdf", "content": "下载补充论文", "kind": "auxiliary"}])
    assert rejected == []
    new_item = ts.item("download-pdf")
    assert new_item is not None and new_item.kind == "auxiliary"
    assert ts.revision == 2


def test_merge_unknown_id_without_kind_rejected():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    rejected = ts.merge([{"id": "new-step", "status": "in_progress"}])  # 无 kind → 视为 plan 新增
    assert rejected and rejected[0]["reason"] == "PLAN_MUTATION_REQUIRES_CONFIRMATION"


def test_merge_invalid_transition_rejected():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    ts.merge([{"id": "a", "status": "in_progress"}])
    ts.merge([{"id": "a", "status": "completed"}])
    rejected = ts.merge([{"id": "a", "status": "in_progress"}])  # 回滚禁止
    assert rejected and "INVALID_TRANSITION" in rejected[0]["reason"]
    assert ts.item("a").status == "completed"  # 未被回滚


def test_summary_structure():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文"), ("b", "分析"), ("c", "报告")])
    # 状态机严格：queued→in_progress→completed（不能直接跳）
    ts.merge([{"id": "a", "status": "in_progress"}, {"id": "b", "status": "in_progress"}])
    ts.merge([{"id": "a", "status": "completed"}])
    s = ts.summary()
    assert s["total"] == 3
    assert s["completed"] == 1
    assert s["in_progress"] == ["分析"]
    assert s["pending"] == 2


def test_observed_source_item():
    """ToolEvent fallback：kind=observed, source=harness。"""
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    ts.merge([{"id": "obs-1", "content": "搜索论文", "kind": "observed", "status": "completed"}])
    it = ts.item("obs-1")
    assert it is not None and it.source == "harness" and it.kind == "observed"


def test_approved_scope_structured():
    scope = ApprovedScope(
        sources=["academic papers"],
        artifacts=[{"type": "document", "name": "report.docx"}],  # type: ignore[arg-type]
        external_actions=[ExternalAction(provider="qraft", operation="upload")],
    )
    plan = PlanSnapshot(plan_id="p1", goal="g", steps=[("a", "s")], approved_scope=scope)
    assert plan.approved_scope.external_actions[0].provider == "qraft"
    assert plan.approved_scope.artifacts[0]["name"] == "report.docx"
