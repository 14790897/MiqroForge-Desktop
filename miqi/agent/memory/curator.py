"""LessonCurator — LLM-driven background consolidation of self-improvement lessons."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

CURATOR_STATE_FILE = "LESSONS_CURATOR_STATE.json"
CURATOR_REPORT_FILE = "LESSONS_CURATOR_REPORT.md"

CURATOR_SYSTEM_PROMPT = """You are a lesson curator. Your task is to find semantically similar lessons \
and suggest merges.

Given a list of lessons in the format:
  [id] trigger → better_action (confidence=N, hits=N)

Identify groups of lessons that express the same or very similar guidance. \
For each group, choose the best representative and write a merged trigger and better_action \
that captures the essence of all lessons in the group.

Return ONLY a JSON array of objects:
  {"keep_id": "<id>", "merge_ids": ["<id2>", "<id3>"], \
"new_trigger": "<merged trigger>", "new_better_action": "<merged better action>"}

Do NOT include lessons that have no similar peers. Do NOT merge lessons with clearly different topics."""


@dataclass
class CuratorReport:
    merged_count: int
    archived_count: int
    run_at: str


class LessonCurator:
    """LLM-driven lesson deduplication and merging.

    Called periodically from MemoryStore.flush() when the active lesson count
    exceeds the configured threshold. The LLM call follows the same pattern as
    the runtime's direct provider.chat() summarization — direct provider.chat()
    without going through ContextBuilder.build_system_prompt() to prevent
    self-reference loops.
    (Historical: pattern previously matched AgentLoop._call_llm_for_summary().)
    """

    def __init__(
        self,
        lesson_store: object,  # LessonStore (avoid circular import)
        llm_call: Callable[..., Awaitable[Any]],
        workspace: Path,
        *,
        enabled: bool = True,
        interval_days: int = 7,
        threshold: int = 150,
        model: str = "",
    ):
        self._lesson_store = lesson_store
        self._llm_call = llm_call
        self._workspace = workspace
        self._memory_dir = workspace / "memory"
        self.enabled = enabled
        self.interval_days = max(1, interval_days)
        self.threshold = max(1, threshold)
        self.model = model

    @property
    def _state_file(self) -> Path:
        return self._memory_dir / CURATOR_STATE_FILE

    @property
    def _report_file(self) -> Path:
        return self._workspace / CURATOR_REPORT_FILE

    async def maybe_run(self, force: bool = False) -> bool:
        """Check state and run curator if enough time has passed. Returns True if run."""
        if not self.enabled:
            return False

        if not force:
            try:
                if self._state_file.exists():
                    data = json.loads(self._state_file.read_text(encoding="utf-8"))
                    last_run = datetime.fromisoformat(data.get("last_run", ""))
                    elapsed = (datetime.now() - last_run).total_seconds() / 86400
                    if elapsed < self.interval_days:
                        return False
            except Exception:
                pass

        await self.run()
        return True

    async def run(self) -> CuratorReport | None:
        """Execute curation: cluster, merge, archive, write report."""
        store = self._lesson_store
        active = [
            lesson for lesson in store._lessons  # type: ignore[union-attr]
            if lesson.get("state", "active") in ("active", "stale")
               and lesson.get("enabled", True)
        ]
        if len(active) < 2:
            return None

        lesson_lines: list[str] = []
        for lesson in active:
            lid = lesson.get("id", "?")
            trigger = lesson.get("trigger", "")
            better = lesson.get("better_action", "")
            conf = lesson.get("confidence", 0)
            hits = lesson.get("hits", 0)
            lesson_lines.append(
                f"[{lid}] {trigger} → {better} (confidence={conf}, hits={hits})"
            )
        lessons_text = "\n".join(lesson_lines)

        # Direct LLM call, bypasses ContextBuilder — prevents self-reference loop.
        # Historical: pattern previously matched AgentLoop._call_llm_for_summary().
        try:
            response = await self._llm_call(
                messages=[
                    {"role": "system", "content": CURATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": lessons_text},
                ],
                tools=None,
                model=self.model,
                max_tokens=4096,
                temperature=0.3,
            )
        except Exception:
            return None

        content = self._extract_content(response)
        merges = self._parse_merges(content)
        if not merges:
            return None

        now = datetime.now().isoformat()
        archived_count = 0
        for merge in merges:
            keep_id = merge.get("keep_id", "")
            merge_ids = merge.get("merge_ids", [])
            if not keep_id or not merge_ids:
                continue
            for lesson in store._lessons:  # type: ignore[union-attr]
                if str(lesson.get("id", "")) == keep_id:
                    lesson["trigger"] = merge.get("new_trigger", lesson.get("trigger", ""))
                    lesson["better_action"] = merge.get(
                        "new_better_action", lesson.get("better_action", "")
                    )
                    lesson["updated_at"] = now
                    break
            for mid in merge_ids:
                for lesson in store._lessons:  # type: ignore[union-attr]
                    if str(lesson.get("id", "")) == mid:
                        lesson["state"] = "archived"
                        lesson["source"] = "curator_merged"
                        lesson["updated_at"] = now
                        archived_count += 1
                        break

        merged_count = len(merges)
        run_at = now

        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps({"last_run": run_at, "merged_count": merged_count,
                         "archived_count": archived_count}),
            encoding="utf-8",
        )

        report_lines = [
            "# Lesson Curator Report\n",
            f"**Run at:** {run_at}\n",
            f"**Merged clusters:** {merged_count}\n",
            f"**Archived lessons:** {archived_count}\n",
            "\n## Merges\n",
        ]
        for i, merge in enumerate(merges, 1):
            report_lines.append(
                f"### Cluster {i}\n"
                f"- **Keep:** [{merge.get('keep_id', '?')}] {merge.get('new_trigger', '')}\n"
                f"- **Merged:** {merge.get('merge_ids', [])}\n"
                f"- **Better:** {merge.get('new_better_action', '')}\n"
            )
        self._report_file.parent.mkdir(parents=True, exist_ok=True)
        self._report_file.write_text("".join(report_lines), encoding="utf-8")

        return CuratorReport(
            merged_count=merged_count,
            archived_count=archived_count,
            run_at=run_at,
        )

    @staticmethod
    def _extract_content(response: Any) -> str:
        """Extract text content from LLM response (string or object with .content)."""
        if isinstance(response, str):
            return response
        if hasattr(response, "content"):
            return str(response.content) or ""
        return str(response)

    @staticmethod
    def _parse_merges(content: str) -> list[dict[str, Any]]:
        """Parse JSON array from LLM response, handling markdown fences."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass
        return []
