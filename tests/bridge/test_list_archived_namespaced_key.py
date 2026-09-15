"""#1014 读侧锁定：``sessions.list_archived`` 必须按 canonical 目录找 ``.archived``。

写侧 ``SessionManager.archive`` 把标记写进 ``get_session_dir(key)`` 指向的
canonical 目录；读侧 handler 若按历史 raw 公式（``key.replace(":", "_")``）
去找，三段 namespaced key（``A:desktop:1786807046853`` → raw 名
``A_desktop_1786807046853``、canonical 名 ``desktop_1786807046853``）下会永远
读不到，归档会话从面板消失（#1014 评审 B-3 缺口 1）。

变异鉴别力：把 ``miqi/runtime/session_handlers.py`` 里
``session_files_dir_key(s["key"])`` 改回 ``s["key"].replace(":", "_")``，
本用例必须红。

与 ``tests/bridge/test_sessions_empty_ephemeral.py`` 的 harness 相比，这里只把
``_get_session_manager`` 换成 tmp_path 上的 manager（handler 主体、registry、
client 作用域过滤全部照跑）：避免向真实 ``~/.miqi/workspace`` 写测试会话，也
不受并发/残留目录影响；被测的读侧派生逻辑不受影响。
"""

from __future__ import annotations

import pytest

from miqi.runtime import session_handlers
from miqi.runtime.app_server import ClientSessionRegistry
from miqi.session.manager import SessionManager
from miqi.session.session_keys import session_files_dir_key

# 三段 namespaced key，首段与 client_id 一致（桌面端 ``<client>:desktop:<chat_id>``
# 的真实形态）。raw 名与 canonical 名在此分叉，是本用例的鉴别力来源。
_KEY = "A:desktop:1786807046853"
_CLIENT = "A"
_CANONICAL_DIR = "desktop_1786807046853"


def _make_owned(sm: SessionManager, key: str, *, message: str, client: str) -> None:
    s = sm.get_or_create(key, client_id=client)
    s.add_message("user", message)
    sm.save(s)
    sm.invalidate(key)


@pytest.mark.asyncio
async def test_list_archived_finds_namespaced_key_in_canonical_dir(tmp_path, monkeypatch):
    """归档标记写在 canonical 目录 → list_archived 必须把它列出来。"""
    sm = SessionManager(tmp_path / "ws")
    monkeypatch.setattr(session_handlers, "_get_session_manager", lambda: sm)
    registry = ClientSessionRegistry()

    canonical_dir = sm.sessions_dir / session_files_dir_key(_KEY)
    raw_dir = sm.sessions_dir / _KEY.replace(":", "_")
    assert canonical_dir.name == _CANONICAL_DIR

    _make_owned(sm, _KEY, message="归档读侧", client=_CLIENT)
    sm.archive(_KEY, client_id=_CLIENT)

    assert (canonical_dir / ".archived").exists(), (
        "写侧标记应落在 canonical 目录（archive → get_session_dir）"
    )
    assert not raw_dir.exists(), "写侧不该产生 raw 名目录"

    result = await session_handlers.sessions_list_archived_handler(
        "req-1", {}, _CLIENT, None, registry
    )
    keys = [s["key"] for s in result["result"]["sessions"]]
    assert _KEY in keys, (
        "list_archived 没找到 canonical 目录下的 .archived —— "
        "读侧又按 raw 名找，归档会话对面板不可见"
    )

    await registry.stop_all()


@pytest.mark.asyncio
async def test_list_archived_handler_still_client_scoped(tmp_path, monkeypatch):
    """回归护栏：client 作用域过滤照旧（别的 client 看不到 A 的归档会话）。"""
    sm = SessionManager(tmp_path / "ws")
    monkeypatch.setattr(session_handlers, "_get_session_manager", lambda: sm)
    registry = ClientSessionRegistry()

    _make_owned(sm, _KEY, message="归档读侧", client=_CLIENT)
    sm.archive(_KEY, client_id=_CLIENT)

    result = await session_handlers.sessions_list_archived_handler(
        "req-1", {}, "B", None, registry
    )
    keys = [s["key"] for s in result["result"]["sessions"]]
    assert _KEY not in keys, "list_archived 不能跨 client 泄露会话"

    await registry.stop_all()
