"""Codex-style thread/turn/item response projection types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ItemsView = Literal["notLoaded", "summary", "full"]
SortDirection = Literal["asc", "desc"]


@dataclass(frozen=True)
class ThreadStatusView:
    type: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type}


@dataclass(frozen=True)
class ThreadItemView:
    type: str
    id: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "id": self.id, **self.payload}


@dataclass(frozen=True)
class TurnView:
    id: str
    thread_id: str
    status: str
    items_view: ItemsView
    items: list[ThreadItemView]
    started_at: float | None = None
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "threadId": self.thread_id,
            "status": self.status,
            "itemsView": self.items_view,
            "items": [item.to_dict() for item in self.items],
        }
        if self.started_at is not None:
            data["startedAt"] = self.started_at
        if self.completed_at is not None:
            data["completedAt"] = self.completed_at
        return data


@dataclass(frozen=True)
class ThreadView:
    id: str
    session_id: str
    status: ThreadStatusView
    name: str | None
    parent_thread_id: str | None
    forked_from_id: str | None
    archived: bool
    ephemeral: bool
    turns: list[TurnView]
    created_at: float
    updated_at: float
    items_view: ItemsView
    # Optional richness hint: number of persisted turns/items in the thread.
    # When negative/absent the caller did not compute it. Used by the frontend
    # thread-resume (issue #490) to prefer the thread holding the most history
    # over the merely most-recently-touched one on fragmented legacy sessions.
    turn_count: int = -1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "status": self.status.to_dict(),
            "name": self.name,
            "parentThreadId": self.parent_thread_id,
            "forkedFromId": self.forked_from_id,
            "archived": self.archived,
            "ephemeral": self.ephemeral,
            "turns": [turn.to_dict() for turn in self.turns],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "itemsView": self.items_view,
            # Fall back to len(turns) when include_turns fetched them; otherwise
            # use the explicit turn_count the list path populated.
            "turnCount": self.turn_count if self.turn_count >= 0 else len(self.turns),
        }


@dataclass(frozen=True)
class Page:
    data: list[Any]
    next_cursor: str | None
    backwards_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "data": self.data,
            "nextCursor": self.next_cursor,
            "backwardsCursor": self.backwards_cursor,
        }


def page_items(
    items: list[Any],
    *,
    limit: int,
    cursor: str | None,
    sort_direction: SortDirection,
) -> Page:
    safe_limit = max(1, min(int(limit), 200))
    ordered = list(items)
    if sort_direction == "desc":
        ordered.reverse()
    start = int(cursor or "0")
    end = start + safe_limit
    data = ordered[start:end]
    next_cursor = str(end) if end < len(ordered) else None
    backwards_cursor = str(max(0, start - safe_limit)) if start > 0 else "0"
    return Page(data=data, next_cursor=next_cursor, backwards_cursor=backwards_cursor)
