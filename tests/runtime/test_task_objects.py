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
    # CodeRabbit（9-11）：实现语义 pending=queued（8b51ae25）——a/b 已 in_progress、
    # a 已 completed，queued 只剩 c → pending == 1
    assert s["pending"] == 1


def test_observed_source_item():
    """ToolEvent fallback：kind=observed, source=harness。"""
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    ts.merge([{"id": "obs-1", "content": "搜索论文", "kind": "observed", "status": "completed"}])
    it = ts.item("obs-1")
    assert it is not None and it.source == "harness" and it.kind == "observed"


# --- #1071 R1：merge 的 rejected 路径必须零副作用 -------------------------
#
# 旧实现先把 content 写进 existing、再校验 status；status 非法走 rejected 时
# content 已被改掉、revision 却没计——部分提交让 UI 拿到一个"没发生过"的
# 变更，且 revision 与内容不一致（前端无法据此判断该帧是否可信）。


def _aux_state() -> TodoState:
    """一个含 auxiliary 条目的状态（revision 已推进到 2）。"""
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    rejected = ts.merge([{"id": "aux-1", "content": "下载补充论文", "kind": "auxiliary"}])
    assert rejected == []
    assert ts.item("aux-1").status == "queued"
    assert ts.revision == 2
    return ts


def test_merge_invalid_transition_leaves_content_untouched():
    """核心回归：非法 transition + content → rejected 且 content/revision 不变。

    修前：content 被写成"偷偷改完的版本"，revision 原地踏步。
    """
    ts = _aux_state()
    before_revision = ts.revision
    before_content = ts.item("aux-1").content

    # queued→completed 不是合法迁移（queued 只能 → in_progress/cancelled）
    rejected = ts.merge([{"id": "aux-1", "content": "被偷偷改掉的内容", "status": "completed"}])

    assert rejected and "INVALID_TRANSITION" in rejected[0]["reason"]
    assert rejected[0]["id"] == "aux-1"
    assert ts.item("aux-1").content == before_content, "rejected 路径不得修改 content"
    assert ts.item("aux-1").status == "queued"
    assert ts.revision == before_revision, "rejected 路径不得推进 revision"


def test_merge_invalid_status_string_zero_side_effects():
    """非法状态字面量（不在状态机内）同样零副作用。"""
    ts = _aux_state()
    before = (ts.item("aux-1").content, ts.item("aux-1").status, ts.revision)

    rejected = ts.merge([{"id": "aux-1", "content": "新内容", "status": "bogus_status"}])

    assert rejected and "INVALID_TRANSITION" in rejected[0]["reason"]
    assert (ts.item("aux-1").content, ts.item("aux-1").status, ts.revision) == before


def test_merge_invalid_transition_leaves_blocked_reason_untouched():
    """blocked_reason 也在提交点内：rejected 时不得被清/被改。"""
    ts = _aux_state()
    ts.merge([{"id": "aux-1", "status": "in_progress"}])
    ts.merge([{"id": "aux-1", "status": "blocked", "blocked_reason": "network"}])
    assert ts.item("aux-1").blocked_reason == "network"
    before = (ts.item("aux-1").content, ts.item("aux-1").status, ts.item("aux-1").blocked_reason, ts.revision)

    # blocked→completed 非法
    rejected = ts.merge([{"id": "aux-1", "content": "偷改", "status": "completed"}])

    assert rejected and "INVALID_TRANSITION" in rejected[0]["reason"]
    assert (ts.item("aux-1").content, ts.item("aux-1").status,
            ts.item("aux-1").blocked_reason, ts.revision) == before


def test_merge_auxiliary_content_plus_valid_status_commits_atomically():
    """合法路径与现状一致：content 与 status 一起生效，revision 只 +1。"""
    ts = _aux_state()
    rejected = ts.merge([{"id": "aux-1", "content": "下载补充论文v2", "status": "in_progress"}])

    assert rejected == []
    assert ts.item("aux-1").content == "下载补充论文v2"
    assert ts.item("aux-1").status == "in_progress"
    assert ts.revision == 3


def test_merge_auxiliary_content_only_path_preserved():
    """「仅改内容不改状态」路径保持：content 生效 + revision +1（bulk replace）。"""
    ts = _aux_state()
    rejected = ts.merge([{"id": "aux-1", "content": "扩充后的描述"}])

    assert rejected == []
    assert ts.item("aux-1").content == "扩充后的描述"
    assert ts.item("aux-1").status == "queued"
    assert ts.revision == 3


def test_merge_non_auxiliary_content_only_is_still_noop():
    """非 auxiliary、非 plan 的纯 content patch 仍是 no-op（不写、不计 revision）。

    plan 走的是更早的 PLAN_MUTATION_REQUIRES_CONFIRMATION 分支，见
    test_merge_plan_content_change_rejected；这里覆盖 observed 这类"够得着
    新代码"的条目。
    """
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索论文")])
    ts.merge([{"id": "obs-1", "content": "读文件", "kind": "observed", "status": "completed"}])
    rev_after_add = ts.revision

    rejected = ts.merge([{"id": "obs-1", "content": "偷改 observed"}])

    assert rejected == []  # 与现状一致：静默忽略，不报 rejected
    assert ts.item("obs-1").content == "读文件"
    assert ts.item("obs-1").status == "completed"
    assert ts.revision == rev_after_add


def test_merge_mixed_batch_revision_counts_only_commits():
    """同一批 patch 里 rejected 与合法项并存：只有真正提交的才推进 revision。"""
    ts = _aux_state()
    rejected = ts.merge([
        {"id": "aux-1", "content": "偷改", "status": "completed"},  # 非法 → 零副作用
        {"id": "a", "status": "in_progress"},                        # 合法
    ])

    assert len(rejected) == 1 and rejected[0]["id"] == "aux-1"
    assert ts.item("aux-1").content == "下载补充论文"
    assert ts.item("a").status == "in_progress"
    assert ts.revision == 3, "只应为合法的 1 次提交 +1（2 → 3）"


def test_merge_rollback_with_content_zero_side_effects():
    """第二种形态：completed→in_progress 回滚 + content，同样零副作用。"""
    ts = _aux_state()
    ts.merge([{"id": "aux-1", "status": "in_progress"}])
    ts.merge([{"id": "aux-1", "status": "completed"}])
    before = (ts.item("aux-1").content, ts.item("aux-1").status, ts.revision)

    rejected = ts.merge([{"id": "aux-1", "content": "回滚时偷改", "status": "in_progress"}])

    assert rejected and "INVALID_TRANSITION" in rejected[0]["reason"]
    assert (ts.item("aux-1").content, ts.item("aux-1").status, ts.revision) == before


def test_approved_scope_structured():
    scope = ApprovedScope(
        sources=["academic papers"],
        artifacts=[{"type": "document", "name": "report.docx"}],  # type: ignore[arg-type]
        external_actions=[ExternalAction(provider="qraft", operation="upload")],
    )
    plan = PlanSnapshot(plan_id="p1", goal="g", steps=[("a", "s")], approved_scope=scope)
    assert plan.approved_scope.external_actions[0].provider == "qraft"
    # CodeRabbit（9-11）：normalize 后 artifacts 是 ArtifactRef 值对象（97a1bf20）——
    # 属性访问，非 dict 下标
    assert plan.approved_scope.artifacts[0].name == "report.docx"
