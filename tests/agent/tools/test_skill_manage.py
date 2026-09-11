"""Tests for the skill_manage tool's skill-location disclosure.

skill_manage(view) must append the runtime-resolved scripts directory to
the SKILL.md content so the agent can run the skill's scripts without
relying on hard-coded machine paths in the system prompt.
"""


import pytest

from miqi.agent.tools.skill_manage import SkillManageTool


@pytest.mark.asyncio
async def test_view_appends_scripts_dir(tmp_path):
    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Demo\n\nDo the thing.\n", encoding="utf-8")

    tool = SkillManageTool(workspace=tmp_path)
    result = await tool.execute(action="view", name="demo-skill")

    assert "# Demo" in result
    assert "本技能的脚本目录" in result
    assert str(skill_dir).replace("\\", "/") in result.replace("\\", "/")


@pytest.mark.asyncio
async def test_view_missing_skill_errors(tmp_path):
    tool = SkillManageTool(workspace=tmp_path)
    result = await tool.execute(action="view", name="nope")
    assert "未找到技能" in result
