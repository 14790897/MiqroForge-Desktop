"""Tests for Codex-style turn protocol helpers."""

from __future__ import annotations

import pytest


def test_normalize_turn_input_accepts_text_items():
    from miqi.runtime.turn_protocol import input_text, normalize_turn_input

    items = normalize_turn_input([
        {"type": "text", "text": "Run tests"},
        {"type": "text", "text": "Then summarize"},
    ])

    assert items == [
        {"type": "text", "text": "Run tests"},
        {"type": "text", "text": "Then summarize"},
    ]
    assert input_text(items) == "Run tests\nThen summarize"


def test_normalize_turn_input_rejects_empty_input():
    from miqi.runtime.turn_protocol import TurnProtocolError, normalize_turn_input

    with pytest.raises(TurnProtocolError, match="input must contain at least one item"):
        normalize_turn_input([])


def test_normalize_turn_input_preserves_mentions_and_skills():
    from miqi.runtime.turn_protocol import input_text, normalize_turn_input

    items = normalize_turn_input([
        {"type": "text", "text": "@sample summarize"},
        {"type": "mention", "name": "Sample", "path": "plugin://sample@local"},
        {"type": "skill", "name": "skill-creator", "path": "/skills/skill-creator/SKILL.md"},
    ])

    assert items[1]["type"] == "mention"
    assert items[2]["type"] == "skill"
    assert input_text(items) == "@sample summarize"


def test_user_message_item_echoes_client_id():
    from miqi.runtime.turn_protocol import user_message_item

    item = user_message_item(
        turn_id="turn-1",
        input_items=[{"type": "text", "text": "hello"}],
        client_user_message_id="client-msg-1",
    )

    assert item["type"] == "userMessage"
    assert item["id"] == "turn-1:user"
    assert item["clientId"] == "client-msg-1"
    assert item["content"] == [{"type": "text", "text": "hello"}]


def test_turn_view_shapes_status_and_error():
    from miqi.runtime.turn_protocol import turn_view

    assert turn_view("turn-1", "thread-1", "inProgress") == {
        "id": "turn-1",
        "threadId": "thread-1",
        "status": "inProgress",
        "items": [],
        "error": None,
    }

    failed = turn_view("turn-1", "thread-1", "failed", error_message="Nope")
    assert failed["status"] == "failed"
    assert failed["error"] == {"message": "Nope"}


def test_injected_message_to_provider_message_extracts_text_content():
    from miqi.runtime.turn_protocol import injected_message_to_provider_message

    provider_message = injected_message_to_provider_message({
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "Previously computed context."}],
    })

    assert provider_message == {
        "role": "assistant",
        "content": "Previously computed context.",
        "message_fields": {
            "raw_item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Previously computed context."}],
            }
        },
    }
