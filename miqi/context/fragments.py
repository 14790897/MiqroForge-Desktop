"""ContextualFragment — typed context injection protocol.

Each fragment represents a bounded piece of context that can be
injected into the agent's system prompt. Fragments are rendered
in priority order and have hard caps on size.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ContextualFragment(ABC):
    """Protocol for injectable context fragments.

    Implementations define what context to inject and how to render it.
    Each fragment has a priority (lower = earlier in prompt) and a
    maximum size in characters.
    """

    # Lower number = higher priority (injected first)
    priority: int = 100

    # Hard cap on rendered content in characters (~2K tokens)
    max_chars: int = 8_000

    @abstractmethod
    def render(self) -> str:
        """Render the fragment content for injection.

        Returns:
            The rendered string, truncated to max_chars if needed.
        """
        ...

    def render_safe(self) -> str:
        """Render with safety truncation."""
        content = self.render()
        if len(content) > self.max_chars:
            return (
                content[:self.max_chars]
                + f"\n... [truncated: {len(content)} chars total]"
            )
        return content

    @property
    def name(self) -> str:
        """Human-readable name for logging."""
        return self.__class__.__name__


@dataclass
class SkillInstructionsFragment(ContextualFragment):
    """Injects available skill instructions into the system prompt."""
    priority: int = 50
    max_chars: int = 20_000
    skills_text: str = ""

    def render(self) -> str:
        return self.skills_text


@dataclass
class PermissionContextFragment(ContextualFragment):
    """Informs the model about its current permission boundaries."""
    priority: int = 30
    max_chars: int = 2_000
    filesystem_policy: str = ""
    network_policy: str = ""
    sandbox_type: str = ""

    def render(self) -> str:
        parts = ["## Permission Context", ""]
        if self.filesystem_policy:
            parts.append(f"Filesystem: {self.filesystem_policy}")
        if self.network_policy:
            parts.append(f"Network: {self.network_policy}")
        if self.sandbox_type:
            parts.append(f"Sandbox: {self.sandbox_type}")
        return "\n".join(parts)


@dataclass
class PlanContextFragment(ContextualFragment):
    """Injects the current plan state into the system prompt."""
    priority: int = 40
    max_chars: int = 4_000
    plan_json: dict | None = None

    def render(self) -> str:
        if not self.plan_json:
            return ""
        steps = self.plan_json.get("steps", [])
        lines = ["## Current Plan", f"Title: {self.plan_json.get('title', 'Untitled')}", ""]
        for s in steps:
            status_icon = {
                "pending": "⬜",
                "in_progress": "🔄",
                "completed": "✅",
                "skipped": "⏭️",
            }.get(s.get("status", "pending"), "❓")
            lines.append(
                f"{status_icon} {s.get('id', '?')}: {s.get('description', '')}"
            )
        return "\n".join(lines)


@dataclass
class MemoryContextFragment(ContextualFragment):
    """Injects relevant memory items into the system prompt."""
    priority: int = 60
    max_chars: int = 3_000
    memories: list[str] = None  # type: ignore[assignment]

    def render(self) -> str:
        if not self.memories:
            return ""
        lines = ["## Relevant Memory", ""]
        for i, mem in enumerate(self.memories, 1):
            lines.append(f"{i}. {mem}")
        return "\n".join(lines)
