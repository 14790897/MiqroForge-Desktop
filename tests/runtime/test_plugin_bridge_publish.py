"""#1267 评审:插件管理器在 bridge 上的发布生命周期。

发布发生在会话通过账号校验并完成登记之后(app_server.create_session);
会话退役(_discard_session)时,若发布的正是该会话的实例,则一并清空——
plugin/* 处理器不得继续操作已停止会话的管理器;其他会话的实例不受影响。
"""

import pytest


class _Services:
    plugin_manager = None


class _FakeSession:
    def __init__(self, plugin_manager):
        self.services = _Services()
        self.services.plugin_manager = plugin_manager
        self.stopped = False

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_discard_session_clears_its_published_plugin_manager():
    from unittest.mock import MagicMock

    from miqi.runtime.app_server import ClientSessionRegistry

    pm = object()
    state = MagicMock()
    state._plugin_manager = pm
    session = _FakeSession(pm)

    registry = ClientSessionRegistry()
    registry.bridge_context = {"plugin_manager": pm, "state": state}
    registry._sessions["c:s"] = session

    await registry._discard_session("c:s", session)

    assert session.stopped
    assert registry.bridge_context["plugin_manager"] is None
    assert state._plugin_manager is None


@pytest.mark.asyncio
async def test_discard_session_keeps_other_sessions_plugin_manager():
    """退役的会话不是发布来源 → bridge 引用原样保留(对象身份比较)。"""
    from miqi.runtime.app_server import ClientSessionRegistry

    published = object()
    mine = object()
    session = _FakeSession(mine)

    registry = ClientSessionRegistry()
    registry.bridge_context = {"plugin_manager": published, "state": None}
    registry._sessions["c:other"] = session

    await registry._discard_session("c:other", session)

    assert registry.bridge_context["plugin_manager"] is published


@pytest.mark.asyncio
async def test_stop_session_clears_its_published_plugin_manager():
    """正常停止/空闲淘汰路径同样清理已发布引用(#1267 评审)。"""
    from unittest.mock import MagicMock

    from miqi.runtime.app_server import ClientSessionRegistry

    pm = object()
    state = MagicMock()
    state._plugin_manager = pm
    session = _FakeSession(pm)

    registry = ClientSessionRegistry()
    registry.bridge_context = {"plugin_manager": pm, "state": state}
    registry._sessions["c:s"] = session

    await registry.stop_session("c:s")

    assert session.stopped
    assert registry.bridge_context["plugin_manager"] is None
    assert state._plugin_manager is None


@pytest.mark.asyncio
async def test_stop_session_keeps_other_sessions_plugin_manager():
    from miqi.runtime.app_server import ClientSessionRegistry

    published = object()
    mine = object()
    session = _FakeSession(mine)

    registry = ClientSessionRegistry()
    registry.bridge_context = {"plugin_manager": published, "state": None}
    registry._sessions["c:x"] = session

    await registry.stop_session("c:x")

    assert registry.bridge_context["plugin_manager"] is published
