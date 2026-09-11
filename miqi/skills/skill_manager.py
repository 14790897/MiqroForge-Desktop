"""Skill loading, injection, and rendering.

Skills are markdown instruction files that augment the agent's system prompt.
They can be loaded from:
- Built-in skills (shipped with MiQi)
- Workspace skills (.miqi/skills/)
- Plugin skills (plugin_dir/skills/)

Each skill is defined by a SKILL.md file with YAML frontmatter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml
from loguru import logger


class SkillScope(str, Enum):
    USER = "user"
    WORKSPACE = "workspace"
    SYSTEM = "system"
    ADMIN = "admin"


@dataclass
class SkillMetadata:
    """Parsed from SKILL.md frontmatter."""
    name: str
    description: str
    scope: SkillScope = SkillScope.WORKSPACE
    version: str = "1.0.0"
    author: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    tools_required: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class SkillLoadOutcome:
    """Result of loading all skills."""
    skills: list[SkillMetadata]
    errors: list[tuple[str, str]] = field(default_factory=list)
    total_size_chars: int = 0


@dataclass
class SkillInjections:
    """Rendered skill instructions for injection into context."""
    system_skills: str = ""
    workspace_skills: str = ""
    user_skills: str = ""
    available_list: str = ""

    def total_chars(self) -> int:
        return sum(len(s) for s in [
            self.system_skills,
            self.workspace_skills,
            self.user_skills,
            self.available_list,
        ])


class SkillsManager:
    """Manages skill loading, caching, and context injection."""

    MAX_SKILL_CHARS = 20_000
    MAX_SKILL_NAME_LENGTH = 100
    MAX_METADATA_CHARS = 5_000

    def __init__(
        self,
        system_skills_dir: Path,
        user_skills_dir: Path,
        workspace: Path | None = None,
    ):
        self.system_dir = system_skills_dir
        self.user_dir = user_skills_dir
        self.workspace = workspace
        self._cache: dict[str, tuple[float, SkillMetadata, str]] = {}

    def load_all(self) -> SkillLoadOutcome:
        """Load all available skills."""
        skills: list[SkillMetadata] = []
        errors: list[tuple[str, str]] = []
        total_chars = 0

        search_dirs = [
            (self.system_dir, SkillScope.SYSTEM),
            (self.user_dir, SkillScope.USER),
        ]
        if self.workspace:
            ws_skills = self.workspace / ".miqi" / "skills"
            search_dirs.append((ws_skills, SkillScope.WORKSPACE))

        for base_dir, default_scope in search_dirs:
            if not base_dir.exists():
                continue
            for skill_dir in base_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue
                try:
                    meta, body = self._parse_skill_file(
                        skill_md, default_scope
                    )
                    if meta.enabled:
                        skills.append(meta)
                        total_chars += len(body)
                        if total_chars > self.MAX_SKILL_CHARS:
                            logger.warning(
                                "Skill total size {} exceeds limit {}; "
                                "truncating",
                                total_chars, self.MAX_SKILL_CHARS,
                            )
                except Exception as e:
                    errors.append((str(skill_md), str(e)))
                    logger.error(
                        "Failed to load skill {}: {}", skill_md, e
                    )

        return SkillLoadOutcome(
            skills=skills,
            errors=errors,
            total_size_chars=min(total_chars, self.MAX_SKILL_CHARS),
        )

    def build_injections(
        self, outcome: SkillLoadOutcome
    ) -> SkillInjections:
        """Build context injection strings from loaded skills."""
        system_parts = []
        workspace_parts = []
        user_parts = []

        for skill in outcome.skills:
            _, _, body = self._cache.get(
                skill.name, (0, skill, "")
            )
            entry = self._render_skill_entry(skill, body)
            match skill.scope:
                case SkillScope.SYSTEM:
                    system_parts.append(entry)
                case SkillScope.WORKSPACE:
                    workspace_parts.append(entry)
                case SkillScope.USER:
                    user_parts.append(entry)
                case _:
                    workspace_parts.append(entry)

        available = self._render_available_list(outcome.skills)

        return SkillInjections(
            system_skills="\n---\n".join(system_parts),
            workspace_skills="\n---\n".join(workspace_parts),
            user_skills="\n---\n".join(user_parts),
            available_list=available,
        )

    def _parse_skill_file(
        self, path: Path, default_scope: SkillScope
    ) -> tuple[SkillMetadata, str]:
        """Parse a SKILL.md file with YAML frontmatter."""
        content = path.read_text(encoding="utf-8")
        meta_str, body = self._split_frontmatter(content)

        meta_dict: dict = {"scope": default_scope}
        if meta_str:
            parsed = yaml.safe_load(meta_str)
            if isinstance(parsed, dict):
                meta_dict.update(parsed)

        meta_dict.setdefault("name", path.parent.name)
        meta_dict.setdefault(
            "description", meta_dict.get("name", "untitled")
        )

        # Filter to known SkillMetadata fields
        known_fields = {
            "name", "description", "scope", "version", "author",
            "tags", "triggers", "tools_required", "dependencies",
            "enabled",
        }
        filtered = {k: v for k, v in meta_dict.items() if k in known_fields}

        return SkillMetadata(**filtered), body

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[str | None, str]:
        """Split YAML frontmatter from markdown body."""
        if not content.startswith("---"):
            return None, content
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None, content
        return parts[1].strip(), parts[2].strip()

    @staticmethod
    def _render_skill_entry(
        skill: SkillMetadata, body: str
    ) -> str:
        """Render a single skill for context injection."""
        lines = [
            f"## Skill: {skill.name}",
            f"Description: {skill.description}",
        ]
        if skill.triggers:
            lines.append(f"Triggers: {', '.join(skill.triggers)}")
        lines.append("")
        lines.append(body[:SkillsManager.MAX_SKILL_CHARS])
        return "\n".join(lines)

    @staticmethod
    def _render_available_list(
        skills: list[SkillMetadata],
    ) -> str:
        """Render the human-readable list of available skills."""
        lines = ["## Available Skills", ""]
        for s in skills:
            lines.append(f"- **{s.name}**: {s.description}")
            if s.triggers:
                lines.append(
                    f"  Triggers: {', '.join(s.triggers)}"
                )
        return "\n".join(lines)[:SkillsManager.MAX_METADATA_CHARS]
