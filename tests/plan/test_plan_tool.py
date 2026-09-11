"""Tests for miqi.plan.plan_tool."""

import asyncio


def test_plan_create_and_read():
    from miqi.plan.plan_tool import PlanCreateTool
    from miqi.plan.plan_tracker import PlanTracker

    tracker = PlanTracker()
    create_tool = PlanCreateTool(tracker=tracker)

    result = asyncio.run(create_tool.execute(
        title="Test Plan",
        steps=[
            {"id": "1", "description": "Step one"},
            {"id": "2", "description": "Step two", "depends_on": ["1"]},
        ],
    ))
    assert "created" in result.lower() or "plan" in result.lower()

    # Verify tracker has it
    plan_ids = list(tracker._plans.keys())
    assert len(plan_ids) == 1
    plan = tracker.get(plan_ids[0])
    assert plan is not None
    assert plan.title == "Test Plan"
    assert len(plan.steps) == 2
    assert plan.steps[0].status == "pending"


def test_plan_update_step():
    from miqi.plan.plan_tool import PlanCreateTool, PlanUpdateTool
    from miqi.plan.plan_tracker import PlanTracker

    tracker = PlanTracker()
    create_tool = PlanCreateTool(tracker=tracker)

    asyncio.run(create_tool.execute(
        title="Test",
        steps=[{"id": "s1", "description": "Do thing"}],
    ))

    plan = list(tracker._plans.values())[0]

    update_tool = PlanUpdateTool(tracker=tracker)
    result = asyncio.run(update_tool.execute(
        plan_id=plan.plan_id,
        step_id="s1",
        status="in_progress",
    ))
    assert "updated" not in result.lower() or "in_progress" in result.lower()  # plan_update tool output

    # Verify step status changed
    updated = tracker.get(plan.plan_id)
    assert updated is not None
    assert updated.steps[0].status == "in_progress"


def test_plan_update_not_found():
    from miqi.plan.plan_tool import PlanUpdateTool
    from miqi.plan.plan_tracker import PlanTracker

    tracker = PlanTracker()
    update_tool = PlanUpdateTool(tracker=tracker)

    result = asyncio.run(update_tool.execute(
        plan_id="nonexistent",
        step_id="s1",
        status="completed",
    ))
    assert "未找到" in result
