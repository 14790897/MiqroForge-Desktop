"""Phase 41 audit tests for Codex-style turn API registration."""

from __future__ import annotations

from pathlib import Path

import pytest


class _CaptureSend:
    def __init__(self):
        self.messages: list[dict] = []

    def send(self, data: dict) -> None:
        self.messages.append(data)


def _dispatch_legacy(_req_id: str, _method: str, _params: dict) -> None:
    pass


@pytest.mark.asyncio
async def test_phase41_codex_turn_methods_registered():
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    methods = loop.app_server._methods

    expected = {
        "turn/start",
        "turn/interrupt",
        "turn/steer",
        "thread/compact/start",
        "thread/inject_items",
    }
    missing = expected - set(methods)
    assert not missing, f"Missing Phase 41 methods: {sorted(missing)}"

    await loop.app_server.stop()


@pytest.mark.asyncio
async def test_phase41_chat_send_remains_registered_for_legacy_desktop():
    from miqi.bridge.loop import BridgeRuntimeLoop

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
    )
    await loop._init_app_server()
    assert "chat.send" in loop.app_server._methods
    await loop.app_server.stop()


def test_phase41_new_runtime_modules_do_not_import_bridge_server():
    paths = [
        Path("miqi/runtime/turn_protocol.py"),
        Path("miqi/runtime/turn_event_adapter.py"),
        Path("miqi/runtime/turn_app_handlers.py"),
    ]
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert "import miqi.bridge.server" not in text
        assert "from miqi.bridge" not in text


def test_phase41_turn_api_not_implemented_as_bridge_loop_inline_handler():
    text = Path("miqi/bridge/loop.py").read_text(encoding="utf-8")
    assert "def _turn_start_handler" not in text
    assert "def _turn_interrupt_handler" not in text
    assert "def _turn_steer_handler" not in text
