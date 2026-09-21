"""跨账号的运行时缓存隔离（#1185）。

工作区按登录账号收口之后，进程内缓存必须跟着走：bridge 是长期驻留进程，
换账号不重启它。缓存键只有 ``client_id:session_key``，不含账号——谁拿着
上一个账号的会话身份来请求，谁就会拿到那份属于旧账号的运行时。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from miqi.paths import ACCOUNTS_DIR_NAME, ACTIVE_ACCOUNT_FILE
from miqi.runtime.app_server import ClientSessionRegistry, _current_account_root


class _FakeRuntime:
    def __init__(self, workspace: Path) -> None:
        self.services = SimpleNamespace(workspace=workspace)
        self.stopped = False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stopped = True


class _FakeSandboxManager:
    def __init__(self) -> None:
        self.destroyed: list[tuple[str, str | None]] = []

    async def destroy(self, session_key: str, *, client_id: str | None = None) -> bool:
        self.destroyed.append((session_key, client_id))
        return True


@pytest.fixture
def data_root(monkeypatch, tmp_path: Path) -> Path:
    """把数据根钉在临时目录上，账号切换＝改 <数据根>/accounts/.active。"""
    root = tmp_path / "miqi-home"
    root.mkdir()
    monkeypatch.setenv("MIQI_HOME", str(root))
    return root


@pytest.fixture
def fake_runtime(monkeypatch):
    created: list[_FakeRuntime] = []

    def _create(*, workspace, **kwargs):
        runtime = _FakeRuntime(Path(workspace))
        created.append(runtime)
        return runtime

    from miqi.runtime.session import RuntimeSession

    monkeypatch.setattr(RuntimeSession, "create", staticmethod(_create))
    return created


def _set_active(root: Path, sub: str) -> None:
    accounts = root / ACCOUNTS_DIR_NAME
    accounts.mkdir(parents=True, exist_ok=True)
    (accounts / ACTIVE_ACCOUNT_FILE).write_text(sub, encoding="utf-8")


def _config_for(workspace: Path) -> SimpleNamespace:
    # 注册表在 workspace 与 config.workspace_path 不同时会去写 folder 绑定；
    # 本测试只关心缓存复用，所以让两者一致，跳过那一段。
    return SimpleNamespace(workspace_path=workspace)


async def _open(registry: ClientSessionRegistry, ws: Path, **kwargs):
    return await registry.create_session(
        client_id="miqi-desktop",
        session_key="desktop:1",
        config=_config_for(ws),
        provider=None,
        workspace=ws,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_same_account_reuses_the_cached_runtime(data_root: Path, fake_runtime):
    registry = ClientSessionRegistry()
    _set_active(data_root, "19")
    ws = data_root / "accounts" / "19" / "workspace"

    first = await _open(registry, ws)
    again = await _open(registry, ws)

    assert again is first
    assert len(fake_runtime) == 1


@pytest.mark.asyncio
async def test_get_session_refuses_a_runtime_from_another_account(
    data_root: Path, fake_runtime
):
    """``chat.send`` 命中缓存走的是 get_session —— 这里也必须拦。"""
    registry = ClientSessionRegistry()
    _set_active(data_root, "19")
    ws_a = data_root / "accounts" / "19" / "workspace"
    await _open(registry, ws_a)

    _set_active(data_root, "20")

    assert await registry.get_session("miqi-desktop", "miqi-desktop:desktop:1") is None


@pytest.mark.asyncio
async def test_another_account_retires_the_cached_runtime_and_its_sandbox(
    data_root: Path, fake_runtime
):
    registry = ClientSessionRegistry()
    _set_active(data_root, "19")
    ws_a = data_root / "accounts" / "19" / "workspace"
    ws_b = data_root / "accounts" / "20" / "workspace"
    sandboxes = _FakeSandboxManager()

    first = await _open(registry, ws_a, sandbox_manager=sandboxes)
    _set_active(data_root, "20")
    second = await _open(registry, ws_b, sandbox_manager=sandboxes)

    assert second is not first
    assert first.stopped, "上一个账号的运行时没有被停掉"
    assert len(fake_runtime) == 2
    # 旧沙箱绑的是旧账号的工作区，且同样只按这个会话键索引 —— 一并销毁。
    assert sandboxes.destroyed == [("desktop:1", "miqi-desktop")]
    assert registry._sessions["miqi-desktop:desktop:1"] is second


@pytest.mark.asyncio
async def test_switching_back_rebuilds_instead_of_reusing_stale_state(
    data_root: Path, fake_runtime
):
    registry = ClientSessionRegistry()
    _set_active(data_root, "19")
    ws_a = data_root / "accounts" / "19" / "workspace"
    ws_b = data_root / "accounts" / "20" / "workspace"

    a1 = await _open(registry, ws_a)
    _set_active(data_root, "20")
    await _open(registry, ws_b)
    _set_active(data_root, "19")
    a2 = await _open(registry, ws_a)

    assert a2 is not a1
    assert a1.stopped


@pytest.mark.asyncio
async def test_folder_bound_session_survives_within_one_account(
    data_root: Path, fake_runtime
):
    """同一账号内的文件夹绑定会话不能因为「工作区不同」被误杀。

    判据是账号根而不是工作区根，正是为了这个：绑定会话的 workpace 是绑定的
    那个目录，与配置里的工作区本来就不同。
    """
    registry = ClientSessionRegistry()
    _set_active(data_root, "19")
    bound = data_root / "some-project"

    first = await _open(registry, bound)
    again = await _open(registry, bound)

    assert again is first
    assert not first.stopped


def test_a_directly_seeded_session_is_still_served(data_root: Path):
    """没经过 create_session 的（测试直接塞的）会话不因「没记账」被拒。

    把「不知道属于谁」当成「属于别人」会把调用方推进「会话不见了」，
    而那是卡住而不是保护 —— 只有**记了账且不一致**才该拒。
    """
    registry = ClientSessionRegistry()
    _set_active(data_root, "19")
    runtime = _FakeRuntime(data_root / "workspace")
    session_id = "miqi-desktop:desktop:1"
    registry._sessions[session_id] = runtime
    registry._session_clients[session_id] = {"miqi-desktop"}

    served = asyncio.run(registry.get_session("miqi-desktop", session_id))

    assert served is runtime


def test_account_root_follows_the_marker(data_root: Path):
    assert _current_account_root() == data_root / "workspace"
    _set_active(data_root, "19")
    assert _current_account_root() == data_root / "accounts" / "19" / "workspace"
