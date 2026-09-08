"""Agent type registry — defines known agent roles and their capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger

# Structured comparison output convention (issue #878): the frontend renders a
# ```compare JSON code block as a sortable/highlightable comparison table. This
# is appended to the main agent's system prompt so the model emits the block
# when presenting a multi-scheme parameter comparison.
_STRUCTURED_COMPARE_CONVENTION = """

## Structured Comparison Output (```compare)

When presenting a **parameter comparison across multiple schemes / process paths /
experimental conditions** (e.g. 工艺路径、配方、参数范围对比), output the comparison
as a ```compare JSON code block (NOT a plain markdown table). The desktop app renders
it as a sortable, highlightable comparison table.

Schema (valid JSON):

```compare
{
  "title": "optional title",
  "schemes": ["scheme A", "scheme B", "..."],
  "parameters": [
    { "name": "压力", "unit": "MPa", "range": "2–4", "source": "ref-1",
      "values": ["2–4", "5–8", "..."] }
  ],
  "citations": [{ "id": "ref-1", "title": "...", "doi": "...", "url": "..." }]
}
```

Rules:

- `schemes` = the column headers (one scheme / process per column).
- `parameters[].values` = one value per scheme, in the same order as `schemes`.
- `range` (optional) = the parameter's overall range, used for cell highlighting.
- `source` (optional) = a citation id from `citations`; omit it when there is no source.
- Emit only valid JSON inside the fence; if it cannot be parsed, the block is shown as a plain code block.
"""


@dataclass
class AgentMetadata:
    """Metadata describing an agent type."""
    name: str                    # e.g. "code-agent", "doc-agent"
    display_name: str            # e.g. "Code Agent"
    description: str
    system_prompt: str
    available_tools: list[str]   # Tool names this agent can use
    max_iterations: int = 40
    model_override: str | None = None
    is_builtin: bool = True


class AgentRegistry:
    """Registry of all available agent types.

    Usage:
        registry = AgentRegistry()
        registry.register(AgentMetadata(name='code-agent', ...))
        metadata = registry.resolve('code-agent')

    Plugins can contribute agent definitions via ``agents/*.md`` files
    in their directory (KWP/Claude Code format). These are registered
    at discovery time via ``discover_plugin_agents()``.
    """

    def __init__(self):
        self._agents: dict[str, AgentMetadata] = {}
        self._register_builtins()

    def register(self, metadata: AgentMetadata) -> None:
        if metadata.name in self._agents:
            raise ValueError(f"Agent '{metadata.name}' already registered")
        self._agents[metadata.name] = metadata

    def get(self, name: str) -> AgentMetadata | None:
        """Look up an agent type without raising on unknown names."""
        return self._agents.get(name)

    def resolve(self, name: str) -> AgentMetadata:
        if name not in self._agents:
            raise KeyError(f"Unknown agent type: {name}")
        return self._agents[name]

    def list_agents(self) -> list[AgentMetadata]:
        return list(self._agents.values())

    def discover_plugin_agents(
        self, plugin_path: Path, plugin_name: str = ""
    ) -> list[AgentMetadata]:
        """Discover agent definitions from a plugin's ``agents/`` directory.

        Reads ``*.md`` files in ``agents/`` and parses YAML frontmatter
        for agent metadata fields (name, description, model, maxTurns,
        color, tools). Each file becomes a registered AgentMetadata.

        Supports the knowledge-work-plugins / Claude Code agent format.
        """
        import re as _agent_re

        agents_dir = plugin_path / "agents"
        if not agents_dir.is_dir():
            return []

        discovered = []
        for agent_file in sorted(agents_dir.glob("*.md")):
            try:
                raw = agent_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Parse YAML frontmatter
            fm_match = _agent_re.match(r"^---\n(.*?)\n---", raw, _agent_re.DOTALL)
            if not fm_match:
                continue

            meta: dict[str, str] = {}
            for line in fm_match.group(1).split("\n"):
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip().strip("\"'")

            agent_name = meta.get("name", agent_file.stem)
            # Prefix with plugin name to avoid collisions
            if plugin_name:
                agent_name = f"kwp-{plugin_name}-{agent_name}"

            if agent_name in self._agents:
                continue  # Don't overwrite existing registrations

            # Parse tools list if present (YAML inline array or multi-line)
            tools_raw = meta.get("tools", "")
            available_tools: list[str] = []
            if tools_raw:
                # Handle both inline lists and commented placeholders
                for part in tools_raw.replace("[", "").replace("]", "").split(","):
                    part = part.strip().strip("-").strip()
                    if part and not part.startswith("#"):
                        available_tools.append(part)

            # If no explicit tools, default to the standard set
            if not available_tools or "# tools not restricted" in raw:
                available_tools = [
                    "read_file", "write_file", "edit_file", "list_dir",
                    "exec", "web_search", "web_fetch",
                ]

            max_turns = 25
            try:
                max_turns = int(meta.get("maxTurns", "25"))
            except (ValueError, TypeError):
                pass

            body = raw[fm_match.end():].strip() if fm_match else raw
            metadata = AgentMetadata(
                name=agent_name,
                display_name=meta.get("name", agent_name).replace("-", " ").title(),
                description=meta.get("description", body[:200]),
                system_prompt=body,
                available_tools=available_tools,
                max_iterations=max_turns,
                model_override=meta.get("model", None),
                is_builtin=False,
            )
            self.register(metadata)
            discovered.append(metadata)
            logger.debug(
                "Plugin agent registered: {} ({} tools, max_turns={})",
                agent_name, len(available_tools), max_turns,
            )

        return discovered

    def _register_builtins(self) -> None:
        """Register default built-in agent types."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")

        # Main agent — handles everything by default
        self.register(AgentMetadata(
            name="main",
            display_name="MiQroForge",
            description="General-purpose AI assistant for code and document tasks",
            system_prompt=self._build_main_prompt(now),
            available_tools=[
                "read_file", "write_file", "edit_file", "list_dir",
                "exec", "web_search", "web_fetch",
                "memory", "plan_create", "plan_update",
                "create_docx", "create_pptx", "create_xlsx",
                "create_pdf", "pdf_write", "pdf_read",
                "edit_docx", "append_xlsx",
                "docx_read", "docx_write", "pptx_read", "pptx_write",
                "xlsx_read", "xlsx_write",
                "session_search", "task_begin", "task_end",
                "skill_manage", "message", "spawn",
                "paper_search", "paper_get", "paper_download",
                "ask_user_confirm_card",
                "graph_render",  # issue #715: skill 产物 step-graph/data-graph 渲染
            ],
        ))

        # Code specialist agent
        self.register(AgentMetadata(
            name="code-agent",
            display_name="Code Agent",
            description="Specialized agent for code analysis and generation",
            system_prompt=self._build_code_agent_prompt(),
            available_tools=[
                "read_file", "write_file", "edit_file", "list_dir",
                "exec", "web_search", "web_fetch",
            ],
            max_iterations=25,
        ))

        # Document specialist agent
        self.register(AgentMetadata(
            name="doc-agent",
            display_name="Document Agent",
            description="Specialized agent for document creation and editing",
            system_prompt=self._build_doc_agent_prompt(),
            available_tools=[
                "read_file", "write_file", "list_dir",
                "create_docx", "create_pptx", "create_xlsx",
                "create_pdf", "pdf_read",
                "edit_docx", "append_xlsx",
                "docx_read", "docx_write",
                "pptx_read", "pptx_write",
                "xlsx_read", "xlsx_write",
                "web_search", "web_fetch",
            ],
            max_iterations=20,
        ))

        # Research agent — for web research sub-tasks
        self.register(AgentMetadata(
            name="research-agent",
            display_name="Research Agent",
            description="Specialized agent for web research",
            system_prompt=self._build_research_agent_prompt(),
            available_tools=[
                "web_search", "web_fetch",
                "read_file", "write_file",
            ],
            max_iterations=15,
        ))

    @staticmethod
    def _build_main_prompt(now: str) -> str:
        return f"""# MiQroForge Desktop Agent

## Current Time
{now}

You are MiQroForge, a desktop AI assistant. You can help with:

- **Code tasks**: read, write, edit, and execute code
- **Document tasks**: create and edit Word (.docx), PowerPoint (.pptx), Excel (.xlsx), and **PDF** files
- **PDF creation**: use the **`create_pdf`** tool (not ad-hoc scripts) for all PDF generation — it handles Chinese fonts, tables, lists, and page layout automatically
- **Web research**: search the web and fetch page content
- **File management**: navigate, organize, and manipulate files
- **Task scheduling**: create and manage recurring tasks

## Rules
1. Always use the most appropriate tool for the task
2. When editing files, prefer the edit_file tool for precision
3. Before running shell commands, verify they are safe
4. For long tasks, use the plan tool to break them into steps
5. Save important findings to memory
6. Write clear, helpful responses in the user's language
7. **Local skills: BEFORE claiming a capability is unavailable, check the "Local Skills" list in the system prompt. If the user's request matches a listed skill, load its SKILL.md via `skill_manage` (action=view, name=<name>) or read_file on its location, and follow its instructions. Never claim a skill does not exist without checking this list first.**
""" + _STRUCTURED_COMPARE_CONVENTION

    @staticmethod
    def _build_code_agent_prompt() -> str:
        return """# Code Agent

You are a specialized code agent. Focus exclusively on code tasks:
- Reading and understanding code
- Writing new code
- Editing existing code with precision
- Running tests and analyzing results
- Searching for patterns across the codebase

Do NOT:
- Initiate conversations with users
- Take on non-code tasks
- Spawn other sub-agents
"""

    @staticmethod
    def _build_doc_agent_prompt() -> str:
        return """# Document Agent

You are a specialized document agent. Focus on:
- Creating professional Word documents (.docx)
- Building presentation slides (.pptx)
- Generating data reports and spreadsheets (.xlsx)
- **Creating PDF documents (MUST use `create_pdf` tool — do NOT write ad-hoc scripts)**
- Reading and extracting information from documents
- Formatting and styling document content

Do NOT:
- Initiate conversations with users
- Take on non-document tasks
- Execute arbitrary shell commands unless document-related
"""

    @staticmethod
    def _build_research_agent_prompt() -> str:
        return """# Research Agent

You are a specialized research agent. Focus on:
- Searching the web for information
- Fetching and analyzing web content
- Synthesizing findings into clear summaries
- Citing sources accurately

Do NOT:
- Initiate conversations with users
- Modify files unless saving research results
- Execute shell commands
"""
