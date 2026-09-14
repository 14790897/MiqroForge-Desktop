"""#1014 调用点锁定：chat.send 的附件要落在 canonical 会话目录的 files/ 下。

``miqi/bridge/loop.py`` 的 ``attachment_dest_dir()`` 只是纯函数，真正决定附件去
哪的是 ``_chat_send_handler`` 里那一次调用。把该行改回本地 raw 派生
（``session_key.replace(":", "_")``）而 helper 保持正确时，三段 key 下浏览器
上传的附件会重新落进没人读的目录（#1014 评审 B-3 缺口 2，改前全部用例零红）。

驱动方式沿用 ``tests/bridge/test_mode_new_thread.py``：假 registry 返回一个已存在
的 ``FakeRuntime``，从而跳过 provider / create_session 分支，真实 handler 一路走到
附件解码落盘。drain 任务替换为 no-op —— 它属于落盘之后的独立链路（事件转发与
心跳另有 test_chat_drain_heartbeat.py 覆盖），在假 runtime 上只会产生与断言无关的
后台异常。
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from miqi.bridge.loop import BridgeRuntimeLoop
from miqi.session.session_keys import session_files_dir_key

_KEY = "A:desktop:1786807046853"
_CLIENT = "A"


class FakeRuntime:
    """最小 RuntimeSession：只为满足 handler 的 services / submit 调用。"""

    def __init__(self) -> None:
        self.services = SimpleNamespace(thread_runtime=None)
        self.submitted: list = []

    async def submit(self, message) -> None:
        self.submitted.append(message)


class FakeRegistry:
    def __init__(self, runtime) -> None:
        self._runtime = runtime

    async def get_session(self, client_id, runtime_id):
        return self._runtime


async def _noop_emit_client_event(*args, **kwargs) -> None:
    return None


def _make_loop(workspace: Path) -> BridgeRuntimeLoop:
    loop = BridgeRuntimeLoop(
        send_func=lambda *a, **k: None,
        dispatch_legacy_func=lambda *a, **k: None,
        bridge_state=SimpleNamespace(
            load_config=lambda: SimpleNamespace(workspace_path=workspace)
        ),
        dev_mode=False,
    )
    loop._session_drain_tasks = {}
    loop._app_server = SimpleNamespace(
        subscribe=lambda *a, **k: None,
        emit_event=_noop_emit_client_event,
        emit_client_event=_noop_emit_client_event,
    )
    return loop


@pytest.mark.asyncio
async def test_chat_send_saves_attachment_to_canonical_session_dir(tmp_path, monkeypatch):
    payload = b"attachment body"
    runtime = FakeRuntime()
    loop = _make_loop(tmp_path)

    async def _noop_drain(**kwargs) -> None:
        return None

    monkeypatch.setattr(loop, "_drain_chat_events", _noop_drain)

    await loop._chat_send_handler(
        request_id="req-att-1",
        params={
            "session_key": _KEY,
            "thread_id": "t1",
            "content": "带附件的消息",
            "attachments": [
                {
                    "name": "note.txt",
                    "data_base64": base64.b64encode(payload).decode("ascii"),
                }
            ],
        },
        client_id=_CLIENT,
        session_id=None,
        registry=FakeRegistry(runtime),
    )
    await asyncio.sleep(0)  # 让 no-op drain 任务在事件循环关闭前跑完

    canonical = (
        tmp_path / "sessions" / session_files_dir_key(_KEY) / "files" / "note.txt"
    )
    raw = tmp_path / "sessions" / _KEY.replace(":", "_") / "files" / "note.txt"
    assert canonical.parent.parent.name == "desktop_1786807046853"
    assert canonical.read_bytes() == payload, (
        "附件没落在 canonical 会话目录 —— chat.send 调用点又用了 raw 派生"
    )
    assert not raw.exists(), "附件不该落在 raw 名目录"
    assert runtime.submitted, "handler 应把消息交给 runtime.submit"
