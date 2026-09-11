"""Context builder for assembling agent prompts."""

import base64
import importlib.resources
import mimetypes
import platform
import re as _ctx_re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from miqi.agent.memory import MemoryStore
from miqi.agent.skills import SkillsLoader

if TYPE_CHECKING:
    from miqi.agent.trace.store import TraceStore

# ── Tool usage guidance injected into the system prompt ─────────────────

MEMORY_GUIDANCE = """
## Memory Tool Guidance
Use the `memory` tool to save durable facts proactively — do not wait for the user to ask.

Save to `memory` target:
- Project conventions, build commands, architecture decisions
- Environment facts (OS, key paths, tool versions)
- Recurring patterns you observe

Save to `user` target:
- User's name, role, communication preferences
- Things the user repeatedly corrects you on
- User's preferred output format or verbosity

Do NOT save: task progress, ephemeral file contents, one-time results.
Write facts as declarative bullets: `- The project uses uv for dependency management`
""".strip()

SKILLS_GUIDANCE = """
## Skill Management Guidance
After completing any task that required 5+ tool calls, ask yourself: could this workflow
be useful again? If yes, save it as a skill using `skill_manage(action='create')`.

If you use a skill and find it outdated or incorrect, patch it immediately with
`skill_manage(action='patch')` — do not wait for the user to report the problem.

Skills should contain: goal, preconditions, step-by-step procedure, expected output.
""".strip()

# ── Knowledge Work Plugins placeholder resolution ──────────────────────────

# When a KWP skill references `~~category` placeholders, this mapping
# gives the MiQi-appropriate fallback guidance.
_PLACEHOLDER_RESOLVE: dict[str, str] = {
    "~~CRM": (
        "No CRM connected — use `web_search` and `web_fetch` for company "
        "research, or manually enter contact/account details"
    ),
    "~~email": (
        "No email connected — compose emails as text using "
        "`write_file` or respond directly in chat"
    ),
    "~~chat": (
        "No chat platform connected — use the current conversation or "
        "the `message` tool if configured"
    ),
    "~~calendar": (
        "No calendar connected — describe your schedule and meetings "
        "manually, or use `exec` to check local calendar files"
    ),
    "~~knowledge base": (
        "No knowledge base connected — use `web_search` and `read_file` "
        "to find documentation"
    ),
    "~~project tracker": (
        "No project tracker connected — use `write_file` and `edit_file` "
        "to maintain local project files"
    ),
    "~~data warehouse": (
        "Data analysis via `exec` with Python/R, `create_xlsx` for "
        "spreadsheets, or `web_search` for benchmarks"
    ),
    "~~enrichment": (
        "Use `web_search` and `web_fetch` for company and contact research"
    ),
    "~~ERP": (
        "No ERP connected — use `exec` for local data processing, "
        "`create_xlsx` for financial reports"
    ),
    "~~HRIS": (
        "No HRIS connected — describe people data manually or use "
        "`exec`/`create_xlsx` for analysis"
    ),
    "~~ATS": (
        "No ATS connected — use `write_file` to track candidates and "
        "pipelines"
    ),
    "~~support platform": (
        "No support platform connected — use `write_file` and "
        "`edit_file` to track tickets locally"
    ),
    "~~document store": (
        "Use `read_file` and `list_dir` to access local documents, "
        "`web_search` for external references"
    ),
    "~~CLM": (
        "No CLM connected — paste contract text directly or use "
        "`read_file` to load from local files"
    ),
    "~~esignature": (
        "No e-signature connected — draft the document with "
        "`create_docx` or `create_pdf`, then send manually"
    ),
    "~~social": (
        "Use `web_search` and `web_fetch` to research social media "
        "presence"
    ),
    "~~design tool": (
        "Use `create_pptx` and `create_pdf` for design deliverables, "
        "or describe visual specifications in text"
    ),
    "~~BI tool": (
        "Use `exec` with Python/matplotlib for visualization, "
        "`create_xlsx` for data tables, `create_pdf` for reports"
    ),
}

# Pattern to detect `~~placeholder` tokens in skill bodies
_PLACEHOLDER_PATTERN = _ctx_re.compile(
    r"~~[A-Za-z][A-Za-z0-9_ -]*[A-Za-z0-9)]"
)


def _resolve_placeholders(content: str) -> str:
    """Replace KWP ``~~placeholder`` tokens with MiQi tool guidance.

    Uses longest-first replacement so multi-word tokens like
    ``~~data warehouse`` match before ``~~data``.
    """
    sorted_tokens = sorted(_PLACEHOLDER_RESOLVE, key=len, reverse=True)
    for token in sorted_tokens:
        content = content.replace(token, _PLACEHOLDER_RESOLVE[token])
    return content

SESSION_SEARCH_GUIDANCE = """
## Session Search Guidance
Before asking the user to repeat something, use `session_search` to check if it was
discussed in a previous session. Use it proactively when the user references a past
task, project, or decision you don't have in current context.
""".strip()


def _format_traces_for_context(traces: list) -> str:
    lines = ["## Similar Task History (reference, do not blindly copy)"]
    for t in traces:
        lines.append(
            f"- **{t.task_name}** (outcome={t.outcome}, score={t.similarity_score:.2f})"
        )
        lines.append(f"  Goal: {t.goal}")
        if t.tool_calls:
            chain = ", ".join(s.tool_name for s in t.tool_calls[:8])
            lines.append(f"  Tools used: {chain}")
        if t.outcome_notes:
            lines.append(f"  Notes: {t.outcome_notes}")
    return "\n".join(lines)


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]

    def __init__(
        self,
        workspace: Path,
        memory_store: MemoryStore | None = None,
        agent_name: str = "miqi",
        trace_store: "TraceStore | None" = None,
        session_work_dir: Path | None = None,
    ):
        self.workspace = workspace
        self.memory = memory_store or MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self.agent_name = agent_name.strip() or "miqi"
        self.trace_store = trace_store
        self.session_work_dir = session_work_dir
        # Phase 4: SkillsManager for typed skill injection
        self._skills_manager: Any = None

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        session_key: str | None = None,
        current_message: str | None = None,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            skill_names: Optional list of skills to include.

        Returns:
            Complete system prompt.
        """
        parts = []

        # Core identity
        parts.append(self._get_identity())

        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        # Tool usage guidance (injected before memory context)
        guidance_parts = [MEMORY_GUIDANCE, SKILLS_GUIDANCE, SESSION_SEARCH_GUIDANCE]
        parts.append("\n\n".join(guidance_parts))

        # Memory context
        memory = self.memory.get_memory_context(
            session_key=session_key, current_message=current_message
        )
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        if current_message and self.trace_store is not None and self.trace_store.enabled:
            try:
                traces = self.trace_store.search_traces(
                    current_message,
                    limit=3,
                    threshold=0.65,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"trace_search failed: {e}")
                traces = []
            if traces:
                parts.append(_format_traces_for_context(traces))

        # Skills - progressive loading
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        # 2. Available skills: only show summary (agent uses read_file to load)
        # Following Anthropic's progressive-disclosure design:
        #   Layer 1 — name + description + location (this summary, always loaded)
        #   Layer 2 — full SKILL.md body (loaded via read_file on demand)
        #   Layer 3 — references/ siblings (loaded on demand when mentioned)
        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            # Resolve KWP ~~placeholders so the agent sees MiQi-appropriate
            # guidance when reading skill files later via read_file.
            skills_summary = _resolve_placeholders(skills_summary)
            parts.append(f"""# Skills

The following skills extend your capabilities. **Progressive disclosure**:
only the name and description are pre-loaded — read the SKILL.md file
itself to see the full instructions before applying the skill.
- Available skills: read the file at `<location>` with `read_file`
- For deeper reference material listed under `<references>`, read those
  files only when SKILL.md points you to them.
- Skills with `available="false"` need missing dependencies installed first.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        import time as _time
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        agent_name = self.agent_name

        if self.session_work_dir:
            work_dir_line = (
                f"\n- Your working directory (for new files): {self.session_work_dir}"
                "\n  → Use relative paths to write here; "
                "use absolute paths to modify project files directly."
            )
        else:
            work_dir_line = ""

        return f"""You are {agent_name}, a helpful AI assistant.

## Current Time
{now} ({tz})

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Memory files: {workspace_path}/memory/MEMORY.md
- Runtime snapshot: {workspace_path}/memory/LTM_SNAPSHOT.json
- Self-improvement lessons: {workspace_path}/memory/LESSONS.jsonl
- Daily notes: {workspace_path}/memory/YYYY-MM-DD.md
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md{work_dir_line}

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel.

## Tool Call Guidelines
- Before calling tools, you may briefly state your intent (e.g. "Let me check that"), but NEVER predict or describe the expected result before receiving it.
- Tool calls are made through the tool-calling interface only — never write them as text (e.g. `functions.paper_download(...)`). A call written as code is not executed and will confuse the user.
- Before modifying a file, read it first to confirm its current content.
- Do not assume a file or directory exists — use list_dir or read_file to verify.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.

## Memory
- Remember important facts: write to {workspace_path}/memory/MEMORY.md
- Recall past context: the system automatically maintains short-term conversation memory and long-term snapshots — you don't need to manage HISTORY files manually."""

    # Bootstrap files that are system-maintained (always loaded from the installed
    # package templates so updates propagate automatically without re-onboarding).
    # If a copy also exists in the workspace, it is appended as user overrides.
    SYSTEM_BOOTSTRAP_FILES = {"TOOLS.md"}

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace (and package for system files)."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            sections: list[str] = []

            # System-maintained files: always read from the installed package first.
            if filename in self.SYSTEM_BOOTSTRAP_FILES:
                try:
                    pkg_text = (
                        importlib.resources.files("miqi.templates")
                        .joinpath(filename)
                        .read_text(encoding="utf-8")
                    )
                    if pkg_text.strip():
                        sections.append(pkg_text)
                except Exception:
                    pass  # fall through to workspace copy below

            # Workspace copy: always used for non-system files;
            # appended as user extensions for system files.
            ws_path = self.workspace / filename
            if ws_path.exists():
                ws_text = ws_path.read_text(encoding="utf-8")
                if ws_text.strip():
                    if sections:
                        # Avoid duplicating content if workspace is identical to package
                        if ws_text.strip() != sections[0].strip():
                            sections.append(f"## {filename} (workspace overrides)\n\n{ws_text}")
                    else:
                        sections.append(ws_text)

            if sections:
                combined = "\n\n".join(sections)
                parts.append(f"## {filename}\n\n{combined}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        session_key = f"{channel}:{chat_id}" if channel and chat_id else None
        system_prompt = self.build_system_prompt(
            skill_names, session_key=session_key, current_message=current_message
        )
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # Phase 4: inject skill instructions from SkillsManager
        if self._skills_manager is not None:
            try:
                outcome = self._skills_manager.load_all()
                injections = self._skills_manager.build_injections(outcome)
                if injections.system_skills:
                    messages.append({
                        "role": "system",
                        "content": injections.system_skills,
                    })
                if injections.workspace_skills:
                    messages.append({
                        "role": "system",
                        "content": injections.workspace_skills,
                    })
            except Exception:
                pass  # Skill injection is best-effort; never crash the agent

        # History
        messages.extend(history)

        # Current message (with optional image attachments).
        # De-dup: if the current_message is identical to the last history
        # entry, the caller (typically the desktop bridge) has already
        # persisted it. Emitting it twice would cause the LLM to see the
        # same user turn back-to-back and the session-saver to duplicate it.
        user_content = self._build_user_content(current_message, media)
        if not history or not (
            history[-1].get("role") == "user"
            and history[-1].get("content") == user_content
        ):
            messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
        arguments: Any = None,
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.

        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
            arguments: Tool call arguments (recorded so the frontend can show
                the exact URL a web_fetch/web_search actually touched, instead
                of guessing from result text).

        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result,
        }
        if arguments is not None:
            msg["arguments"] = arguments
        messages.append(msg)
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.

        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).

        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant"}

        # Always include content — some providers (e.g. StepFun) reject
        # assistant messages that omit the key entirely.
        msg["content"] = content

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Include reasoning content when provided (required by some thinking models)
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content

        messages.append(msg)
        return messages
