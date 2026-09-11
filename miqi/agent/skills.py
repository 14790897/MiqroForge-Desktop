"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


# 进程级技能索引缓存（#859）：让所有 SkillsLoader 实例共享「目录枚举 + frontmatter
# 解析」结果，避免每次请求 / 每回合都重扫磁盘。key 含 workspace 与 builtin 目录，
# 因为不同 workspace 的技能集合与解析结果不同。
@dataclass
class SkillIndex:
    skills: list[dict[str, str]] | None = None  # None=未枚举；[]=已枚举但为空
    meta_cache: dict[str, dict | None] = field(default_factory=dict)
    references_cache: dict[str, list[str]] = field(default_factory=dict)
    # 代际计数：每次 invalidate 递增，让长生命周期 SkillsLoader 能发现磁盘变更，
    # 进而清空各自的实例级正文缓存（#859 评审）。
    generation: int = 0


_INDEX_CACHE: dict[tuple[str, str], SkillIndex] = {}


def _index_key(workspace: Path, builtin_dir: Path | None) -> tuple[str, str]:
    bd = builtin_dir or BUILTIN_SKILLS_DIR
    return (str(Path(workspace).resolve()), str(Path(bd).resolve()))


def get_skill_index(workspace: Path, builtin_dir: Path | None = None) -> SkillIndex:
    """Return the shared SkillIndex for (workspace, builtin_dir), building an empty one lazily."""
    key = _index_key(workspace, builtin_dir)
    if key not in _INDEX_CACHE:
        _INDEX_CACHE[key] = SkillIndex()
    return _INDEX_CACHE[key]


def invalidate_skill_index(workspace: Path | None = None) -> None:
    """Reset cached skill indexes so the next access re-scans the disk.

    Existing ``SkillsLoader`` instances keep their ``SkillIndex`` object but see
    its contents reset, so even long-lived loaders (e.g. the per-session context)
    pick up on-disk changes. With ``workspace=None`` every index is reset;
    otherwise only entries whose workspace matches are reset.
    """
    if workspace is None:
        targets = list(_INDEX_CACHE.values())
    else:
        ws = str(Path(workspace).resolve())
        targets = [idx for key, idx in _INDEX_CACHE.items() if key[0] == ws]
    for idx in targets:
        idx.skills = None
        idx.meta_cache.clear()
        idx.references_cache.clear()
        idx.generation += 1


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        # #729: metadata/content 缓存——一次摘要构建内同一技能被查 450 次，
        # 每次无缓存都重读文件 + 全树 glob，实测单回合 5.7s。
        # #859: meta_cache 提升为进程级共享（所有实例共享 frontmatter 解析结果）；
        # 正文 content_cache 仍保持实例级（渐进式披露，正文按需读取）。
        self._index = get_skill_index(workspace, self.builtin_skills)
        self._meta_cache: dict[str, dict | None] = self._index.meta_cache
        self._content_cache: dict[str, str | None] = {}
        # nested 技能 name→SKILL.md 索引（懒构建，替代 load_skill 里的全树 glob）
        self._nested_index: dict[str, Path] | None = None
        # 进程级索引的代际快照——检测磁盘变更，变了就清本实例的正文缓存
        self._index_generation = self._index.generation

    def _sync_index_generation(self) -> None:
        """Drop instance-level caches when the shared index was invalidated.

        ``invalidate_skill_index`` bumps ``SkillIndex.generation`` (e.g. after a
        skill create/upload/delete). A long-lived loader keeps its own
        ``_content_cache``, so without this it would keep serving deleted skill
        bodies from memory (#859 评审).
        """
        if self._index.generation != self._index_generation:
            self._content_cache.clear()
            self._nested_index = None
            self._index_generation = self._index.generation

    def _get_nested_index(self) -> dict[str, Path]:
        """Build (once) a name → SKILL.md path index for nested builtin skills.

        Replaces the per-lookup ``glob("**/<name>/SKILL.md")`` which walks the
        whole builtin tree on every call (issue #729: 1100 globs / 5.7s per
        turn). Mirrors ``_discover_nested_skills`` depth: flat dirs plus up to
        2 levels of nesting.
        """
        if self._nested_index is None:
            index: dict[str, Path] = {}
            if self.builtin_skills and self.builtin_skills.exists():
                for entry in sorted(self.builtin_skills.rglob("SKILL.md")):
                    rel = entry.relative_to(self.builtin_skills)
                    # _discover_nested_skills 深度上限：最多 4 层目录 + SKILL.md
                    if len(rel.parts) <= 5:
                        index.setdefault(entry.parent.name, entry)
            self._nested_index = index
        return self._nested_index

    def _enumerate_skills(self) -> list[dict[str, str]]:
        """Enumerate skill dirs (name/path/source) once per (workspace, builtin).

        Cached in the process-level SkillIndex so every SkillsLoader instance
        shares the directory scan (#859).
        """
        if self._index.skills is not None:
            return self._index.skills

        skills: list[dict[str, str]] = []

        # Workspace skills (highest priority)
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})

        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})
                    # Recursively discover nested skills (e.g. kwp/<plugin>/<skill>/SKILL.md)
                    self._discover_nested_skills(skill_dir, skills, source="builtin")

        self._index.skills = skills
        return skills

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = list(self._enumerate_skills())

        # Filter out archived skills
        skills = [s for s in skills if not self._is_skill_archived(s["name"])]

        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def _discover_nested_skills(
        self, root: Path, skills: list[dict[str, str]], source: str, depth: int = 0
    ) -> None:
        """Recursively discover SKILL.md files at up to 2 levels of nesting.

        Supports directory layouts like:
          kwp/<plugin>/<skill>/SKILL.md
          kwp/<plugin>/SKILL.md
        """
        if depth >= 3:
            return  # Guard against runaway recursion
        names_seen = {s["name"] for s in skills}
        for item in sorted(root.iterdir()):
            if not item.is_dir() or item.name.startswith(".") or item.name == "__pycache__":
                continue
            skill_file = item / "SKILL.md"
            if skill_file.exists():
                skill_name = item.name
                # Prefer the leaf directory name unless it collides
                if skill_name in names_seen:
                    # Use parent dir as prefix to disambiguate
                    skill_name = f"{root.name}-{item.name}"
                if skill_name not in names_seen:
                    skills.append({
                        "name": skill_name,
                        "path": str(skill_file),
                        "source": source,
                    })
                    names_seen.add(skill_name)
            else:
                # Recurse one level deeper (kwp/<plugin>/<skill>/SKILL.md)
                self._discover_nested_skills(item, skills, source, depth + 1)

    def _is_skill_archived(self, name: str) -> bool:
        """Check whether a skill is archived."""
        meta = self.get_skill_metadata(name)
        if not meta:
            return False
        archived = meta.get("archived")
        if archived is True:
            return True
        if isinstance(archived, str) and archived.lower() == "true":
            return True
        return False

    def get_skill_path(self, name: str) -> Path | None:
        """Return the path to a skill's SKILL.md file."""
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill

        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill

        # Search nested built-in skills (e.g. kwp/<plugin>/<skill>/SKILL.md)
        entry = self._get_nested_index().get(name)
        if entry is not None:
            return entry

        return None

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        self._sync_index_generation()
        if name in self._content_cache:
            return self._content_cache[name]

        content = self._load_skill_uncached(name)
        self._content_cache[name] = content
        return content

    def _load_skill_uncached(self, name: str) -> str | None:
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        # Check built-in (flat)
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        # Check built-in (nested — kwp/<plugin>/<skill>/SKILL.md) via index
        entry = self._get_nested_index().get(name)
        if entry is not None:
            return entry.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self, description_max_chars: int | None = None) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Follows Anthropic's progressive-disclosure design:
        - Layer 1 (this summary): name + description + relative path
        - Layer 2 (on demand): full SKILL.md via read_file
        - Layer 3 (on demand): references/ siblings, scripts/, etc.

        Paths are emitted relative to the workspace when possible, so the
        agent can read them with relative paths. This matches the Claude
        Code / Cowork convention of `pdf/SKILL.md`-style locations.

        Args:
            description_max_chars: When set, truncate each skill description
                to this many characters (cut at the last sentence boundary
                within budget) to keep the injected summary compact.

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        def truncate_description(desc: str) -> str:
            """Trim to description_max_chars at a sentence boundary."""
            if description_max_chars is None or len(desc) <= description_max_chars:
                return desc
            cut = desc[:description_max_chars]
            best = -1
            for sep in (". ", "。", "！", "! ", "？", "? "):
                idx = cut.rfind(sep)
                if idx > best:
                    best = idx
            if best > description_max_chars * 0.5:
                cut = cut[: best + 1].rstrip()
            return cut + "…"

        def relative_path(absolute: str) -> str:
            """Convert absolute SKILL.md path to a short relative path.

            Examples:
              C:/.../miqi/skills/pdf/SKILL.md          -> pdf/SKILL.md
              C:/.../miqi/skills/kwp/sales/call-prep/SKILL.md
                                                    -> kwp/sales/call-prep/SKILL.md
              <workspace>/skills/custom/SKILL.md       -> skills/custom/SKILL.md
            """
            p = Path(absolute)
            try:
                if self.builtin_skills:
                    rel = p.relative_to(self.builtin_skills)
                    if str(rel) != str(p.name):  # not the builtin root itself
                        return str(rel).replace("\\", "/")
                rel = p.relative_to(self.workspace)
                return str(rel).replace("\\", "/")
            except ValueError:
                return p.name

        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            rel_path = relative_path(s["path"])
            desc = escape_xml(truncate_description(self._get_skill_description(s["name"])))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{rel_path}</location>")

            # Surface references/ siblings so the agent knows there's a
            # third layer (Anthropic's progressive-disclosure level 3).
            refs = self._index.references_cache.get(s["name"])
            if refs is None:
                skill_dir = Path(s["path"]).parent
                refs = sorted(p.name for p in skill_dir.glob("*.md")
                              if p.name.lower() != "skill.md")
                self._index.references_cache[s["name"]] = refs
            if refs:
                lines.append("    <references>" +
                             ", ".join(escape_xml(r) for r in refs) +
                             "</references>")

            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content

    def _parse_skill_metadata(self, raw: str) -> dict:
        """Parse metadata JSON from frontmatter (miqi/assistant/openclaw keys)."""
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                return {}
            return data.get("miqi", data.get("assistant", data.get("openclaw", {})))
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict:
        """Get normalized metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_skill_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_skill_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        if name in self._meta_cache:
            return self._meta_cache[name]

        metadata = self._load_skill_metadata_uncached(name)
        self._meta_cache[name] = metadata
        return metadata

    def _load_skill_metadata_uncached(self, name: str) -> dict | None:
        content = self.load_skill(name)
        if not content:
            return None

        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Simple YAML parsing with basic type coercion
                metadata = {}
                for line in match.group(1).split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        raw = value.strip().strip("\"'")
                        # Boolean coercion to Python bool
                        if raw.lower() in ("true", "yes"):
                            metadata[key.strip()] = True
                        elif raw.lower() in ("false", "no"):
                            metadata[key.strip()] = False
                        else:
                            metadata[key.strip()] = raw
                return metadata

        return None
