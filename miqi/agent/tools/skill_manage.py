"""Skill manage tool — create, view, patch, and archive reusable skills."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from miqi.agent.skills import SkillsLoader
from miqi.agent.tools.base import Tool


def _set_frontmatter_key(content: str, key: str, value: str) -> str:
    """Add or replace a key in YAML frontmatter using regex (no yaml library)."""
    fm_match = re.match(r"^(---\n)(.*?)(\n---\n)", content, re.DOTALL)
    if not fm_match:
        return f'---\n{key}: "{value}"\n---\n\n{content}'
    prefix, fm_body, suffix = fm_match.group(1), fm_match.group(2), fm_match.group(3)
    rest = content[fm_match.end() :]
    lines = fm_body.split("\n")
    replaced = False
    new_lines = []
    for line in lines:
        if line.startswith(f"{key}:"):
            new_lines.append(f'{key}: "{value}"')
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f'{key}: "{value}"')
    return prefix + "\n".join(new_lines) + suffix + rest


class SkillManageTool(Tool):
    """Tool for managing reusable skills (procedural workflows)."""

    def __init__(self, workspace: Path, sandbox_manager: Any | None = None):
        self.workspace = workspace
        self._skills = SkillsLoader(workspace)
        self._sandbox_manager = sandbox_manager

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "Manage reusable skills (procedural workflows). "
            "Use action='list' to discover all available skills with their descriptions. "
            "Use action='view' to read a skill's full SKILL.md before applying it. "
            "Use action='provision' to install a skill's missing Python dependencies. "
            "Create a skill after completing any complex task with 5+ tool calls. "
            "Patch a skill immediately if you notice it is outdated or wrong during use."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "view", "create", "patch", "archive", "provision"],
                    "description": "list all skills, view a skill, create a new skill, patch an existing skill, archive a skill, or provision (install) a skill's missing Python dependencies",
                },
                "name": {
                    "type": "string",
                    "description": "Skill name (required for view/create/patch/archive)",
                },
                "content": {
                    "type": "string",
                    "description": "Full SKILL.md content (required for create). Must include YAML frontmatter with description and version.",
                },
                "patch_text": {
                    "type": "string",
                    "description": "Text to append to the skill (required for patch)",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        name: str = "",
        content: str = "",
        patch_text: str = "",
    ) -> str:
        if action == "list":
            return self._do_list()
        elif action == "view":
            return self._do_view(name)
        elif action == "create":
            return self._do_create(name, content)
        elif action == "patch":
            return self._do_patch(name, patch_text)
        elif action == "archive":
            return self._do_archive(name)
        elif action == "provision":
            return await self._do_provision(name)
        else:
            return f"Error: 未知操作 '{action}'"

    def _do_list(self) -> str:
        skills = self._skills.list_skills(filter_unavailable=False)
        results = []
        for s in skills:
            results.append(
                {
                    "name": s["name"],
                    "description": self._skills._get_skill_description(s["name"]),
                    "source": s["source"],
                }
            )
        return json.dumps({"skills": results}, ensure_ascii=False)

    def _do_view(self, name: str) -> str:
        if not name:
            return "Error: view 操作必须提供 'name'"
        content = self._skills.load_skill(name)
        if content is None:
            return f"Error: 未找到技能 '{name}'"
        # Append the runtime-resolved scripts directory so the agent can run
        # the skill's scripts without relying on any hard-coded path — the
        # skill lives in different places per machine (builtin install dir,
        # workspace copy), so only the tool can answer reliably.
        script_dir = ""
        for s in self._skills.list_skills(filter_unavailable=False):
            if s["name"] == name:
                from pathlib import Path

                script_dir = str(Path(s["path"]).parent)
                break
        if script_dir:
            content = (
                content.rstrip()
                + f"\n\n---\n本技能的脚本目录（运行技能脚本时使用）：{script_dir}\n"
            )
        reqs = self._skills._read_requirements(name)
        if reqs:
            missing = self._skills._missing_python_deps(name)
            content = (
                content.rstrip()
                + f"\n\n---\n本技能的 Python 依赖（requirements.txt）：{', '.join(r.name for r in reqs)}\n"
            )
            if missing:
                content += f"缺失依赖（需先安装）：{', '.join(missing)}\n"
        return content

    async def _do_provision(self, name: str) -> str:
        if not name:
            return "Error: provision 操作必须提供 'name'"
        from miqi.skills.provision import SkillProvisioner

        provisioner = SkillProvisioner(self._skills, self._sandbox_manager)
        result = await provisioner.provision(name)
        return json.dumps(result, ensure_ascii=False)

    def _do_create(self, name: str, content: str) -> str:
        if not name:
            return "Error: create 操作必须提供 'name'"
        if not content.strip():
            return "Error: create 操作必须提供 'content'"

        # Only allow creation in workspace skills
        skill_dir = self.workspace / "skills" / name
        if skill_dir.exists():
            return f"Error: 技能 '{name}' 已存在"

        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content.strip() + "\n", encoding="utf-8")
        return f'{{"ok": true, "action": "create", "name": "{name}"}}'

    def _do_patch(self, name: str, patch_text: str) -> str:
        if not name:
            return "Error: patch 操作必须提供 'name'"
        if not patch_text.strip():
            return "Error: patch 操作必须提供 'patch_text'"

        # Only allow patching workspace skills
        workspace_skill = self.workspace / "skills" / name / "SKILL.md"
        if not workspace_skill.exists():
            return f"Error: 工作区中未找到技能 '{name}'（无法修改内置技能）"

        existing = workspace_skill.read_text(encoding="utf-8")
        new_content = existing.rstrip("\n") + "\n\n" + patch_text.strip() + "\n"
        workspace_skill.write_text(new_content, encoding="utf-8")
        return f'{{"ok": true, "action": "patch", "name": "{name}"}}'

    def _do_archive(self, name: str) -> str:
        if not name:
            return "Error: archive 操作必须提供 'name'"

        # Only allow archiving workspace skills
        workspace_skill = self.workspace / "skills" / name / "SKILL.md"
        if not workspace_skill.exists():
            return f"Error: 工作区中未找到技能 '{name}'（无法归档内置技能）"

        content = workspace_skill.read_text(encoding="utf-8")
        new_content = _set_frontmatter_key(content, "archived", "true")
        workspace_skill.write_text(new_content, encoding="utf-8")
        return f'{{"ok": true, "action": "archive", "name": "{name}"}}'
