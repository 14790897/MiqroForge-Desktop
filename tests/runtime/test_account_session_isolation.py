"""跨账号的运行时缓存隔离（#1185）。

工作区按登录账号收口之后，进程内缓存必须跟着走：bridge 是长期驻留进程，
换账号不重启它。缓存键只有 ``client_id:session_key``，不含账号——谁拿着
上一个账号的会话身份来请求，谁就会拿到那份属于旧工作区的运行时。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from miqi.runtime.app_server import ClientSessionRegistry, _serves_workspace


class _FakeRuntime:
    """只保留注册表真正读到的形状：services.workspace + start/stop。"""

    def __init__(self, workspace: Path) -> None:
        self.services = SimpleNamespace(workspace=workspace)
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class _FakeSandboxManager:
    def __init__(self) -> None:
        self.destroyed: list[tuple[str, str | None]] = []

    async def destroy(self, session_key: str, *, client_id: str | None = None) -> bool:
        self.destroyed.append((session_key, client_id))
        return True


@pytest.fixture
def fake_runtime(monkeypatch):
    """把 RuntimeSession.create 换成返回 _FakeRuntime 的工厂。"""
    created: list[_FakeRuntime] = []

    def _create(*, workspace, **kwargs):
        runtime = _FakeRuntime(Path(workspace))
        created.append(runtime)
        return runtime

    from miqi.runtime.session import RuntimeSession

    monkeypatch.setattr(RuntimeSession, "create", staticmethod(_create))
    return created


def _config_for(workspace: Path) -> SimpleNamespace:
    # 注册表在 workspace 与 config.workspace_path 不同时会去写 folder 绑定；
    # 本测试只关心缓存复用，所以让两者一致，跳过那一段。
    return SimpleNamespace(workspace_path=workspace)


@pytest.mark.asyncio
async def test_same_workspace_reuses_the_cached_runtime(tmp_path: Path, fake_runtime):
    registry = ClientSessionRegistry()
    ws = tmp_path / "ws"

    first = await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws),
        provider=None,
        workspace=ws,
    )
    again = await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws),
        provider=None,
        workspace=ws,
    )

    assert again is first
    assert len(fake_runtime) == 1


@pytest.mark.asyncio
async def test_another_workspace_never_reuses_the_cached_runtime(tmp_path: Path, fake_runtime):
    """换了账号（工作区不同）→ 上一个账号的运行时必须退场，不能复用。"""
    registry = ClientSessionRegistry()
    ws_a = tmp_path / "accounts" / "19" / "workspace"
    ws_b = tmp_path / "accounts" / "20" / "workspace"
    sandboxes = _FakeSandboxManager()

    first = await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws_a),
        provider=None,
        workspace=ws_a,
        sandbox_manager=sandboxes,
    )
    second = await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws_b),
        provider=None,
        workspace=ws_b,
        sandbox_manager=sandboxes,
    )

    assert second is not first
    assert first.stopped, "旧工作区的运行时没有被停掉"
    assert len(fake_runtime) == 2
    # 旧沙箱绑的是旧工作区，且同样只按这个会话键索引 —— 一并销毁。
    assert sandboxes.destroyed == [("desktop:1", "miqi-desktop")]

    # 保持原样：切到新工作区之后，该键上留下的是新运行时。
    session_id = "miqi-desktop:desktop:1"
    assert registry._sessions[session_id] is second
    assert session_id not in registry._session_clients or registry._session_clients[
        session_id
    ] == {"miqi-desktop"}


@pytest.mark.asyncio
async def test_switching_back_rebuilds_instead_of_reusing_stale_state(tmp_path: Path, fake_runtime):
    """切回原账号：不得捡起中间那段留在缓存里的旧对象，重建即可。"""
    registry = ClientSessionRegistry()
    ws_a = tmp_path / "a"
    ws_b = tmp_path / "b"

    a1 = await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws_a),
        provider=None,
        workspace=ws_a,
    )
    await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws_b),
        provider=None,
        workspace=ws_b,
    )
    a2 = await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws_a),
        provider=None,
        workspace=ws_a,
    )

    assert a2 is not a1
    assert a1.stopped


def test_unknown_workspace_is_not_treated_as_a_match(tmp_path: Path):
    """读不出运行时的工作区时判不匹配 —— 重建比认错便宜得多。"""
    assert _serves_workspace(SimpleNamespace(services=SimpleNamespace(workspace=None)), tmp_path) is False
    assert _serves_workspace(SimpleNamespace(), tmp_path) is False
    assert _serves_workspace(SimpleNamespace(services=SimpleNamespace(workspace=str(tmp_path))), tmp_path) is True
