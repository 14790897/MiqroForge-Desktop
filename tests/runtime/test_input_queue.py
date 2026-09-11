"""Tests for miqi.runtime.input_queue."""

import pytest

from miqi.runtime.input_queue import InputQueue


@pytest.mark.asyncio
async def test_push_and_pop():
    queue = InputQueue()
    await queue.push({"type": "user_message", "content": "hello"})
    item = await queue.pop()
    assert item["content"] == "hello"


@pytest.mark.asyncio
async def test_priority_ordering():
    queue = InputQueue()
    await queue.push({"type": "user_message", "content": "low"}, priority=10)
    await queue.push({"type": "approval_response", "content": "allow"}, priority=1)
    await queue.push({"type": "abort_turn", "content": "stop"}, priority=0)

    first = await queue.pop()
    assert first["type"] == "abort_turn"
    second = await queue.pop()
    assert second["type"] == "approval_response"
    third = await queue.pop()
    assert third["type"] == "user_message"


@pytest.mark.asyncio
async def test_pop_timeout():
    queue = InputQueue()
    item = await queue.pop(timeout=0.05)
    assert item is None


@pytest.mark.asyncio
async def test_clear():
    queue = InputQueue()
    await queue.push({"content": "test"})
    await queue.clear()
    item = await queue.pop(timeout=0.05)
    assert item is None


@pytest.mark.asyncio
async def test_len():
    queue = InputQueue()
    assert len(queue) == 0
    await queue.push({"content": "a"})
    assert len(queue) == 1
    await queue.push({"content": "b"})
    assert len(queue) == 2
    await queue.pop()
    assert len(queue) == 1
