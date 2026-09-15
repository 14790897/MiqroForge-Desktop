"""todo_write 工具契约测试（#646-v2 v3.3）。"""

import json

from miqi.agent.tools.todo_write import TodoWriteTool
from miqi.runtime.task_objects import TodoState


async def _run(tool: TodoWriteTool, **kwargs):
    return json.loads(await tool.execute(**kwargs))


async def test_status_flip():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("step-1", "搜索论文"), ("step-2", "生成报告")])
    tool = TodoWriteTool(ts)
    r = await _run(tool, todos=[{"id": "step-1", "status": "in_progress"}])
    assert r["status"] == "ok"
    assert r["summary"]["in_progress"] == ["搜索论文"]
    assert ts.revision == 2


async def test_plan_mutation_rejected_error_as_output():
    """plan item 改 content → error-as-output（不抛异常，含 suggestion）。"""
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("step-1", "搜索论文")])
    tool = TodoWriteTool(ts)
    r = await _run(tool, todos=[{"id": "step-1", "content": "训练分类器", "kind": "plan"}])
    assert r["status"] == "partial"
    assert r["rejected"][0]["reason"] == "PLAN_MUTATION_REQUIRES_CONFIRMATION"
    assert r["rejected"][0]["suggestion"] == "ask_user_plan_confirm"
    assert ts.item("step-1").content == "搜索论文"


async def test_auxiliary_add():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("step-1", "搜索论文")])
    tool = TodoWriteTool(ts)
    r = await _run(tool, todos=[{"id": "dl-pdf", "content": "下载补充PDF", "kind": "auxiliary", "status": "in_progress"}])
    assert r["status"] == "ok"
    assert ts.item("dl-pdf").kind == "auxiliary"


async def test_full_replace_rejected():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索")])
    tool = TodoWriteTool(ts)
    r = await _run(tool, merge=False, todos=[{"id": "x", "content": "y"}])
    assert r["status"] == "rejected"
    assert r["reason"] == "FULL_REPLACE_NOT_ALLOWED"


async def test_invalid_transition_rejected():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索")])
    tool = TodoWriteTool(ts)
    await _run(tool, todos=[{"id": "a", "status": "in_progress"}])
    await _run(tool, todos=[{"id": "a", "status": "completed"}])
    r = await _run(tool, todos=[{"id": "a", "status": "in_progress"}])  # 回滚
    assert r["status"] == "partial"
    assert "INVALID_TRANSITION" in r["rejected"][0]["reason"]


async def test_blocked_with_reason():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "生成报告")])
    tool = TodoWriteTool(ts)
    await _run(tool, todos=[{"id": "a", "status": "in_progress"}])
    r = await _run(tool, todos=[{"id": "a", "status": "blocked", "blocked_reason": "waiting_user"}])
    assert r["status"] == "ok"
    assert ts.item("a").blocked_reason == "waiting_user"


async def test_summary_prompt_shape():
    ts = TodoState(run_id="r1")
    ts.initialize_from_plan([("a", "搜索"), ("b", "分析"), ("c", "报告")])
    tool = TodoWriteTool(ts)
    r = await _run(tool, todos=[{"id": "a", "status": "in_progress"}])
    s = r["summary"]
    # 结构化短摘要：total/completed/in_progress/pending/blocked——无 source/kind
    assert set(s) == {"total", "completed", "in_progress", "pending", "blocked"}
    # pending=queued 语义（8b51ae25）：a=in_progress、b/c=queued → pending == 2
    assert s["total"] == 3 and s["pending"] == 2
