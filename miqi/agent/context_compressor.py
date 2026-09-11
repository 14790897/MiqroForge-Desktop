"""Context compressor: 5-phase algorithm ported from Hermes Agent.

Algorithm (matches Hermes agent/context_compressor.py exactly):
  Phase 1 - _prune_old_tool_results(): strip old tool results by token budget
  Phase 2 - protect head (first N messages always kept)
  Phase 3 - _find_tail_cut_by_tokens(): token-budget tail protection with 1.5x soft ceiling
  Phase 4 - _generate_summary(): LLM summarises the middle with structured template
  Phase 5 - iterative: re-use PREVIOUS SUMMARY so info accumulates across compressions

Constants tuned to match Hermes defaults:
  _MIN_SUMMARY_TOKENS   = 2000
  _SUMMARY_RATIO        = 0.20  (keep 20% of context as tail)
  _SUMMARY_TOKENS_CEILING = 12_000
  _CHARS_PER_TOKEN      = 2.5
  _CONTENT_MAX          = 6000  (truncate individual messages before summarising)
  _CONTENT_HEAD         = 4000
  _CONTENT_TAIL         = 1500
  _TOOL_ARGS_MAX        = 1500
  _FAILURE_COOLDOWN     = 600   (stop trying to compress for 10 min after failure)
"""
from __future__ import annotations

import json
import time
from typing import Any, Awaitable, Callable

from loguru import logger

from miqi.agent.context_engine import ContextEngine

# ── Tuning constants (matching Hermes) ────────────────────────────────────
_MIN_SUMMARY_TOKENS = 2000
_SUMMARY_RATIO = 0.20
_SUMMARY_TOKENS_CEILING = 12_000
_CHARS_PER_TOKEN = 2.5
_CONTENT_MAX = 6_000
_CONTENT_HEAD = 4_000
_CONTENT_TAIL = 1_500
_TOOL_ARGS_MAX = 1_500
_FAILURE_COOLDOWN = 600       # seconds to skip compression after LLM failure

_HEAD_PROTECT_MSGS = 4        # always keep first N messages after system prompt


# ── Structured summary template ────────────────────────────────────────────
_SUMMARY_TEMPLATE = """## CONVERSATION SUMMARY

**Goal:** {goal}

**Progress:**
{progress}

**Key Decisions:**
{decisions}

**Files & Resources Created/Modified:**
{files}

**Next Steps:**
{next_steps}

**Critical Context:**
{critical_context}

**Tools & Patterns Used:**
{tools_patterns}
"""

_INITIAL_SUMMARY_PROMPT = """\
You are summarizing a conversation between a user and an AI assistant. \
Create a structured summary that preserves all critical information needed \
to continue the task. Focus on what was accomplished, what decisions were made, \
what files were created/modified, and what still needs to be done.

Use this exact structure:
## CONVERSATION SUMMARY

**Goal:** [1-2 sentences describing the overall task/goal]

**Progress:**
[Bullet points of what has been accomplished so far]

**Key Decisions:**
[Important decisions made during the conversation]

**Files & Resources Created/Modified:**
[List of files created, modified, or that are important to the task]

**Next Steps:**
[What still needs to be done to complete the task]

**Critical Context:**
[Any other critical information needed to continue effectively]

**Tools & Patterns Used:**
[Key tools used and successful approaches/patterns discovered]

Conversation to summarize:
{conversation}"""

_UPDATE_SUMMARY_PROMPT = """\
You are updating a conversation summary with new information from recent turns. \
Merge the new information into the existing summary, updating all sections as needed. \
Preserve all critical information from the previous summary unless it has been superseded.

PREVIOUS SUMMARY:
{previous_summary}

NEW TURNS TO INCORPORATE:
{new_turns}

Produce an updated summary using the same structure as the previous one."""


class ContextCompressor(ContextEngine):
    """Default context compression engine with 5-phase algorithm.

    Usage:
        compressor = ContextCompressor(llm_call_fn)
        compressed = await compressor.compress(messages, model, session_id)

    llm_call_fn signature:
        async (messages: list[dict], model: str) -> str
    """

    def __init__(
        self,
        llm_call_fn: Callable[[list[dict[str, Any]], str], Awaitable[str]],
        context_limit_chars: int = 0,
    ):
        """
        Args:
            llm_call_fn: Async function to call the LLM for summary generation.
                         Signature: async (messages, model) -> summary_str
            context_limit_chars: Soft limit; compression triggers when exceeded.
        """
        self._llm_call = llm_call_fn
        self.context_limit_chars = context_limit_chars
        self._last_failure_time: float = 0.0
        self._previous_summary: str = ""    # carries across sequential compressions

    @property
    def name(self) -> str:
        return "ContextCompressor"

    # ── Public entry point ─────────────────────────────────────────────────

    async def compress(
        self,
        messages: list[dict[str, Any]],
        model: str,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        """Compress messages with the 5-phase algorithm."""
        if time.time() - self._last_failure_time < _FAILURE_COOLDOWN:
            logger.debug("[compress] Skipping: in failure cooldown ({}s remaining)",
                         int(_FAILURE_COOLDOWN - (time.time() - self._last_failure_time)))
            return messages

        if len(messages) < 6:
            return messages

        try:
            return await self._compress_impl(messages, model, session_id)
        except Exception as exc:
            logger.error("[compress] Failed ({}), entering {}s cooldown: {}", self.name, _FAILURE_COOLDOWN, exc)
            self._last_failure_time = time.time()
            return messages

    # ── Phase pipeline ─────────────────────────────────────────────────────

    async def _compress_impl(
        self,
        messages: list[dict[str, Any]],
        model: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        # Separate system prompt
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        # Phase 1 — prune old tool results (cheap, no LLM needed)
        non_system = self._prune_old_tool_results(non_system)

        # Phase 2 — protect head (first _HEAD_PROTECT_MSGS messages)
        head = non_system[:_HEAD_PROTECT_MSGS]
        body = non_system[_HEAD_PROTECT_MSGS:]

        if not body:
            return messages   # nothing left to compress

        # Total budget for "tail" (recent messages we keep verbatim)
        # _SUMMARY_RATIO of (context_limit_chars / _CHARS_PER_TOKEN) — or use message count heuristic
        if self.context_limit_chars > 0:
            total_tokens = int(self.context_limit_chars / _CHARS_PER_TOKEN)
        else:
            # estimate from current message count
            total_chars = sum(self._msg_chars(m) for m in non_system)
            total_tokens = int(total_chars / _CHARS_PER_TOKEN)

        tail_token_budget = min(
            int(total_tokens * _SUMMARY_RATIO),
            _SUMMARY_TOKENS_CEILING,
        )
        tail_token_budget = max(tail_token_budget, _MIN_SUMMARY_TOKENS)

        # Phase 3 — find tail cut by token budget
        tail_start, tail = self._find_tail_cut_by_tokens(body, tail_token_budget)
        middle = body[:tail_start]

        if not middle:
            logger.debug("[compress] Nothing in middle section, skipping compression")
            return messages

        # Phase 4 — LLM summarise the middle (+ head)
        to_summarise = head + middle
        summary = await self._generate_summary(to_summarise, model, session_id)

        # Inject summary as a system message at the front
        summary_msg = {
            "role": "system",
            "content": f"[CONTEXT SUMMARY — earlier conversation compressed]\n\n{summary}",
        }

        # Phase 5 — build result and sanitize tool pairs
        result = system_msgs + [summary_msg] + tail
        result = self._sanitize_tool_pairs(result)

        logger.info(
            "[compress][{}] {} msgs → {} msgs (summary={} chars, tail={} msgs)",
            session_id, len(messages), len(result),
            len(summary), len(tail),
        )
        return result

    # ── Phase 1: prune old tool results ────────────────────────────────────

    def _prune_old_tool_results(
        self,
        messages: list[dict[str, Any]],
        token_budget: int = 8000,
    ) -> list[dict[str, Any]]:
        """Strip tool result content from old messages to save tokens.

        Counts tokens from the END (newest messages) and once the budget
        is exhausted, replaces older tool results with a placeholder.
        """
        tokens_so_far = 0
        result = list(messages)
        # Walk backwards keeping a running token count
        for i in range(len(result) - 1, -1, -1):
            msg = result[i]
            chars = self._msg_chars(msg)
            tokens_so_far += int(chars / _CHARS_PER_TOKEN)

            if tokens_so_far > token_budget and msg.get("role") == "tool":
                result[i] = {
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "name": msg.get("name", msg.get("tool_name", "")),
                    "content": "[tool result pruned to save context]",
                }
        return result

    # ── Phase 3: find tail cut by token budget ──────────────────────────────

    def _find_tail_cut_by_tokens(
        self,
        messages: list[dict[str, Any]],
        token_budget: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        """Find split point so tail fits within token_budget.

        Returns (split_index, tail_messages).
        Uses 1.5x soft ceiling — will exceed budget up to 50% to keep
        tool groups together (avoid orphaned tool_result without tool_call).
        """
        soft_ceiling = int(token_budget * 1.5)
        tokens = 0
        # Walk from the end, accumulate until budget exceeded
        split = len(messages)  # default: all goes to tail (nothing to middle)
        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = int(self._msg_chars(messages[i]) / _CHARS_PER_TOKEN)
            if tokens + msg_tokens > soft_ceiling and split < len(messages):
                break
            tokens += msg_tokens
            split = i
            if tokens >= token_budget and i > 0:
                # Hard budget hit; check if we can align backward (keep group intact)
                split = self._align_boundary_backward(messages, split)
                break

        # Enforce minimum: never eat into the last 3 messages
        split = min(split, max(0, len(messages) - 3))
        return split, messages[split:]

    # ── Phase 4: LLM-based summary generation ──────────────────────────────

    async def _generate_summary(
        self,
        messages: list[dict[str, Any]],
        model: str,
        session_id: str,
    ) -> str:
        """Call LLM to produce or update a structured summary."""
        serialised = self._serialize_for_summary(messages)

        if self._previous_summary:
            # Iterative update: inject previous summary so context accumulates
            prompt_content = _UPDATE_SUMMARY_PROMPT.format(
                previous_summary=self._previous_summary,
                new_turns=serialised,
            )
        else:
            prompt_content = _INITIAL_SUMMARY_PROMPT.format(
                conversation=serialised,
            )

        summary = await self._llm_call(
            [{"role": "user", "content": prompt_content}],
            model,
        )
        # Strip possible markdown code fences
        summary = summary.strip()
        if summary.startswith("```"):
            summary = "\n".join(summary.split("\n")[1:])
        if summary.endswith("```"):
            summary = "\n".join(summary.split("\n")[:-1])
        summary = summary.strip()

        self._previous_summary = summary
        return summary

    # ── Serialisation for summary ──────────────────────────────────────────

    def _serialize_for_summary(self, messages: list[dict[str, Any]]) -> str:
        """Serialise messages to a compact text form for the summary prompt.

        Truncates long content to prevent the summary prompt itself from
        being too large.
        """
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content") or ""
            if isinstance(content, list):
                # Multimodal: flatten text blocks
                content = " ".join(
                    block.get("text", "") for block in content
                    if isinstance(block, dict)
                )

            # Truncate large content
            if len(content) > _CONTENT_MAX:
                content = content[:_CONTENT_HEAD] + "\n...[truncated]...\n" + content[-_CONTENT_TAIL:]

            if role == "assistant" and msg.get("tool_calls"):
                tc_list = msg["tool_calls"]
                tc_text = ""
                for tc in (tc_list if isinstance(tc_list, list) else [tc_list]):
                    fn = tc.get("function", tc) if isinstance(tc, dict) else {}
                    name = fn.get("name", "?")
                    args_raw = fn.get("arguments", "{}")
                    if isinstance(args_raw, str) and len(args_raw) > _TOOL_ARGS_MAX:
                        args_raw = args_raw[:_TOOL_ARGS_MAX] + "...[truncated]"
                    tc_text += f"\n  → {name}({args_raw})"
                parts.append(f"[{role}]{tc_text}")
                if content:
                    parts.append(f"  text: {content}")
            elif role == "tool":
                tool_name = msg.get("name") or msg.get("tool_name") or ""
                parts.append(f"[tool:{tool_name}] {content}")
            else:
                parts.append(f"[{role}] {content}")

        return "\n\n".join(parts)

    # ── Tool-pair sanitization ─────────────────────────────────────────────

    def _sanitize_tool_pairs(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ensure every tool_call has a matching tool result and vice versa.

        Removes orphaned tool results and injects stubs for missing results.
        This prevents provider API errors from mismatched tool_call pairs.
        """
        # Pass 1: collect all tool_call IDs that have results
        result_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                result_ids.add(msg["tool_call_id"])

        # Pass 2: collect all expected IDs from assistant tool_calls
        call_ids: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                for tc in (msg["tool_calls"] if isinstance(msg["tool_calls"], list) else [msg["tool_calls"]]):
                    if isinstance(tc, dict):
                        tc_id = tc.get("id", "")
                        if tc_id:
                            call_ids.add(tc_id)

        # Pass 3: rebuild — drop orphan results, inject stubs for missing results
        output: list[dict[str, Any]] = []
        pending_stubs: list[str] = []   # IDs needing stub injection

        for msg in messages:
            role = msg.get("role")
            if role == "tool":
                tc_id = msg.get("tool_call_id", "")
                if tc_id in call_ids:
                    output.append(msg)
                # else: orphaned tool result — drop silently
            elif role == "assistant" and msg.get("tool_calls"):
                # Check which IDs in this batch are missing results
                tc_list = msg["tool_calls"] if isinstance(msg["tool_calls"], list) else [msg["tool_calls"]]
                for tc in tc_list:
                    if isinstance(tc, dict):
                        tc_id = tc.get("id", "")
                        if tc_id and tc_id not in result_ids:
                            pending_stubs.append(tc_id)
                output.append(msg)
                # Inject stubs immediately if we have any
                for stub_id in pending_stubs:
                    output.append({
                        "role": "tool",
                        "tool_call_id": stub_id,
                        "content": "[result not available — context was compressed]",
                    })
                pending_stubs = []
            else:
                output.append(msg)

        return output

    # ── Boundary alignment helpers ─────────────────────────────────────────

    def _align_boundary_backward(
        self,
        messages: list[dict[str, Any]],
        idx: int,
    ) -> int:
        """Move idx backward to avoid splitting a tool_call / tool_result group."""
        # Walk backward until we hit an assistant or user message
        while idx > 0 and messages[idx].get("role") == "tool":
            idx -= 1
        # Skip the assistant message that owns those tools too
        while idx > 0 and messages[idx - 1].get("role") == "tool":
            idx -= 1
        return idx

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _msg_chars(msg: dict[str, Any]) -> int:
        """Estimate character count of a single message."""
        content = msg.get("content") or ""
        if isinstance(content, str):
            n = len(content)
        elif isinstance(content, list):
            n = sum(len(b.get("text", "")) for b in content if isinstance(b, dict))
        else:
            n = 0
        # Add tool_calls JSON size for assistant messages
        if msg.get("tool_calls"):
            try:
                n += len(json.dumps(msg["tool_calls"]))
            except (TypeError, ValueError):
                pass
        return n
