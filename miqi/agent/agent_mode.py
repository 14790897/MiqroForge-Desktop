"""Agent Reasoning Modes (issue #680): Fast / Think.

Two orthogonal dimensions of the agent runtime:

- Reasoning Mode (this module): HOW the agent thinks.
    - fast  — Answer-oriented: shortest path to a good-enough answer.
    - think — Task-oriented: maximize task quality, no limits (current behavior).
- Permission Mode (ExecutionPolicy, untouched): whether to modify files,
  auto-execute, or confirm. ``plan`` / ``manual`` / ``edit`` / ``auto``.

Keeping the two axes separate avoids an NxM switch explosion
(Fast-Edit-Auto / Think-Plan …). Future modes (Deep Research, Coding,
Analysis) attach to Reasoning Mode; permission semantics stay in
ExecutionPolicy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ── Generation profile: the model-side budget ────────────────────────────────
# Without this, a "fast" mode that optimizes search still stalls on a model
# that reasons for 40s (DeepSeek TTFT is the dominant latency variable).
@dataclass(frozen=True)
class GenerationProfile:
    max_tokens: int
    temperature: float | None = None


# ── Tool policy: execution-side guardrails ───────────────────────────────────
@dataclass(frozen=True)
class ToolPolicy:
    # Fuse on the agent DECISION LOOP, not a tool-count budget: caps how many
    # model→tool→model rounds a turn may take, so the model cannot spiral into
    # search→search→search. This is deliberately NOT "web_search max N calls"
    # (a count budget would just cut information quality).
    max_tool_rounds: int | None
    parallel_limit: int
    # "info_only": informational tools (search/read/fetch) run automatically;
    # permission-typed actions (write/delete/shell) still go through
    # ExecutionPolicy confirmation. "full": current confirmation behavior.
    confirm_policy: Literal["info_only", "full"]
    # Time budget (FAST v2, ChatGPT 评审收敛版): the turn gets
    # time_budget_s total; entering the finalization phase at
    # (time_budget_s - finalization_grace_s) the loop stops issuing NEW tool
    # calls and injects a finalize prompt so the model wraps up with what it
    # has.  time_budget_s=None disables the fuse (think).  This is a runtime
    # budget, NOT a hard kill — the model always gets to produce its answer.
    time_budget_s: int | None = None
    finalization_grace_s: int = 5
    # Search phase budget (FAST v2): max_search_phase=1 allows ONE web_search
    # phase per turn — the fan-out inside that phase is configured by
    # SearchStrategy.fanout_queries.  Repeated search→think→search loops
    # are rejected with a skip notice.  None = unlimited (think).
    max_search_phase: int | None = None


# ── Search strategy: consumed by SearchOrchestrator ──────────────────────────
@dataclass(frozen=True)
class SearchStrategy:
    name: Literal["breadth", "depth"]
    fanout_queries: int = 1   # parallel search queries per web_search call
    fanout_fetches: int = 0   # parallel page fetches after dedup
    verify: bool = False      # reserved (Phase 2): second-pass verification


@dataclass(frozen=True)
class AgentModeConfig:
    mode: Literal["fast", "think"]
    generation: GenerationProfile
    tool: ToolPolicy
    search: SearchStrategy
    # Fast-mode system-prompt snippet appended to the base prompt (think: none).
    prompt_snippet: str = ""


FAST_PROMPT = (
    "你是「极速回答」模式：目标是在 30 秒内给出足够好的答案。"
    "优先凭已有知识直接回答，最多使用一轮 web_search（内部并行抓取），"
    "不要探索性浏览，不要重复搜索；时间内完成，简短直接。"
)

THINK_PROMPT = (
    "你是「深度研究」模式：目标最大化任务质量。"
    "请全面分析问题，多角度思考（背景、机制、对比、数据、局限性），"
    "给出结构化、详尽、有依据的回答（分点、对比、引用来源）；"
    "确需信息时使用 web_search/web_fetch 深入调研，允许多轮工具调用。"
    "注意：思考应聚焦问题本身（分析、推理、权衡、拆解），"
    "不要复述工具调用计划（如'先调用 X 工具'、'让我列出技能'）——"
    "工具调用由系统自动编排，你只需呈现真正的思考。"
)

FAST = AgentModeConfig(
    mode="fast",
    generation=GenerationProfile(max_tokens=2048),
    tool=ToolPolicy(
        max_tool_rounds=3,
        parallel_limit=5,
        confirm_policy="info_only",
        # FAST v2 (ChatGPT 评审收敛版): 25s 进入收尾 + 30s 兜底；搜索 1 phase
        time_budget_s=30,
        finalization_grace_s=5,
        max_search_phase=1,
    ),
    search=SearchStrategy(name="breadth", fanout_queries=2, fanout_fetches=3),
    prompt_snippet=FAST_PROMPT,
)

THINK = AgentModeConfig(
    mode="think",
    generation=GenerationProfile(max_tokens=8192),
    # None = unlimited rounds; current loop behavior unchanged.
    tool=ToolPolicy(max_tool_rounds=None, parallel_limit=3, confirm_policy="full"),
    # depth reserved for Phase 2 (exploratory parallel research).
    search=SearchStrategy(name="depth", fanout_queries=1, fanout_fetches=0),
    prompt_snippet=THINK_PROMPT,
)

_MODES: dict[str, AgentModeConfig] = {m.mode: m for m in (FAST, THINK)}


def get_mode_config(mode: str | None) -> AgentModeConfig:
    """Resolve a mode name to its config; unknown/None → fast (默认极速版)."""
    return _MODES.get(mode or "", FAST)


def mode_names() -> tuple[str, ...]:
    return tuple(_MODES.keys())
