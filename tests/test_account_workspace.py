"""按登录账号划分本地存储（#1185）的路径规则。

覆盖 `miqi.paths` 的账号维度与 `Config.workspace_path` 的接线。桌面主进程那
一侧（`apps/desktop/src/main/ipc/workspace-path.ts`）实现同一条规则，由
`workspace-path.test.ts` 覆盖；两边不同步会让渲染层的包含性检查和运行时的
写入落在不同目录上。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from miqi.config.schema import Config
from miqi.paths import (
    ACCOUNTS_DIR_NAME,
    ACTIVE_ACCOUNT_FILE,
    DEFAULT_WORKSPACE_VALUE,
    LEGACY_WORKSPACE_OWNER_FILE,
    get_account_workspace,
    get_active_account,
    get_default_workspace_path,
    get_legacy_workspace_owner,
    is_valid_account_sub,
)
from miqi.session.manager import SessionManager
from miqi.utils.helpers import get_workspace_path

ACCOUNT_A = "19"
ACCOUNT_B = "20"


@pytest.fixture
def data_root(monkeypatch, tmp_path: Path) -> Path:
    """把数据根钉在临时目录上，避免读到开发机真实的 ~/.forge。"""
    root = tmp_path / "miqi-home"
    root.mkdir()
    monkeypatch.setenv("MIQI_HOME", str(root))
    return root


def _set_active(root: Path, sub: str) -> None:
    accounts = root / ACCOUNTS_DIR_NAME
    accounts.mkdir(parents=True, exist_ok=True)
    (accounts / ACTIVE_ACCOUNT_FILE).write_text(sub, encoding="utf-8")


def _set_legacy_owner(root: Path, sub: str) -> None:
    accounts = root / ACCOUNTS_DIR_NAME
    accounts.mkdir(parents=True, exist_ok=True)
    (accounts / LEGACY_WORKSPACE_OWNER_FILE).write_text(sub, encoding="utf-8")


# ── 无账号态 ───────────────────────────────────────────────────────────


def test_no_account_keeps_the_shared_workspace(data_root: Path):
    """未登录（CLI / 测试 / 登录前）沿用 <数据根>/workspace，行为不变。"""
    assert get_active_account() is None
    assert get_default_workspace_path() == data_root / "workspace"


def test_workspace_path_matches_helper_without_account(data_root: Path):
    """Config.workspace_path 与 get_workspace_path 必须指向同一处。

    两条解析此前各写各的（一个走 get_miqi_home，一个走 get_data_path），
    账号维度落点不一致就会写出「运行时写 A、界面读 B」。
    """
    assert Config().workspace_path == get_default_workspace_path()
    assert get_workspace_path() == get_default_workspace_path()


# ── 账号维度 ───────────────────────────────────────────────────────────


def test_active_account_scopes_the_workspace(data_root: Path):
    _set_active(data_root, ACCOUNT_A)

    assert get_active_account() == ACCOUNT_A
    assert get_default_workspace_path() == data_root / ACCOUNTS_DIR_NAME / ACCOUNT_A / "workspace"
    assert Config().workspace_path == get_account_workspace(ACCOUNT_A)


def test_switching_account_switches_the_root(data_root: Path):
    """换账号即换根，且切回去能拿回自己的目录。"""
    _set_active(data_root, ACCOUNT_A)
    ws_a = Config().workspace_path
    _set_active(data_root, ACCOUNT_B)
    ws_b = Config().workspace_path

    assert ws_a != ws_b
    _set_active(data_root, ACCOUNT_A)
    assert Config().workspace_path == ws_a
    assert Config().workspace_path == get_account_workspace(ACCOUNT_A)


def test_workspace_path_is_not_cached_across_an_account_switch(data_root: Path):
    """同一进程内即时生效：bridge 是长期驻留进程，登录/登出不重启它。

    属性每次访问都重算标记文件，所以换了账号的第一次读就落在新根上；
    若在这里加了进程级缓存，切到 B 后的第一个请求会写进 A 的目录。
    """
    cfg = Config()
    _set_active(data_root, ACCOUNT_A)
    assert cfg.workspace_path == get_account_workspace(ACCOUNT_A)
    _set_active(data_root, ACCOUNT_B)
    assert cfg.workspace_path == get_account_workspace(ACCOUNT_B)


def test_custom_workspace_is_not_account_scoped(data_root: Path, tmp_path: Path):
    """用户指定过工作区目录时尊重他的选择，不按账号收口（#1185 item 6）。"""
    custom = tmp_path / "my-project"
    _set_active(data_root, ACCOUNT_A)
    cfg = Config()
    cfg.agents.defaults.workspace = str(custom)

    assert cfg.workspace_path == custom.resolve()


def test_empty_workspace_means_the_default(data_root: Path):
    """清空工作目录字段写下的空串 = 用默认目录，而不是「进程当前目录」。

    设置页的工作目录输入框清空后把 `""` 落进配置（placeholder 就是默认路径），
    `get_workspace_path()` 之类的读取方也一律把空值当未设置；只有
    `Config.workspace_path` 会把它字面解析成 `Path("").resolve()` —— 也就是
    bridge 进程的当前目录，既不是工作区也不按账号隔离。
    """
    _set_active(data_root, ACCOUNT_A)
    cfg = Config()
    cfg.agents.defaults.workspace = ""

    assert cfg.workspace_path == get_account_workspace(ACCOUNT_A)


# ── 标记文件的安全性 ───────────────────────────────────────────────────


@pytest.mark.parametrize("sub", ["19", "a-b_c.d", "A" * 64])
def test_valid_account_subs(sub: str):
    assert is_valid_account_sub(sub)


@pytest.mark.parametrize(
    "sub",
    ["", "..", "../evil", "a/b", "a\\b", "a:b", "x" * 65, ".", None],
)
def test_invalid_account_subs(sub):
    assert not is_valid_account_sub(sub)


@pytest.mark.parametrize("sub", ["..", "../evil", "a/b", "a\\b"])
def test_traversal_in_marker_is_treated_as_no_account(data_root: Path, sub: str):
    """标记文件被写成 `..` / 带分隔符时退回共享工作区，绝不当作路径片段。

    「清洗后使用」会把 `../evil` 变成另一个账号的目录 —— 不认识的账号一律
    当作未登录，退回共享根，是一个明确且不越界的位置。
    """
    _set_active(data_root, sub)

    assert get_active_account() is None
    assert get_default_workspace_path() == data_root / "workspace"


# ── 存量数据归属 ───────────────────────────────────────────────────────


def test_legacy_workspace_goes_to_the_first_account_that_claimed_it(data_root: Path):
    """升级前 <数据根>/workspace 里的数据归首个登录账号，且就地保留。

    认领方继续用旧目录（不搬家的原因见 claimLegacyWorkspace 的注释），
    另一个账号拿到自己的空目录 —— 谁都不会读到对方的历史。
    """
    legacy = data_root / "workspace"
    (legacy / "sessions" / "desktop_k").mkdir(parents=True)
    _set_legacy_owner(data_root, ACCOUNT_A)
    _set_active(data_root, ACCOUNT_A)

    assert get_legacy_workspace_owner() == ACCOUNT_A
    assert get_default_workspace_path() == legacy
    assert Config().workspace_path == legacy

    # 换账号：B 看不到那份存量数据。
    _set_active(data_root, ACCOUNT_B)
    assert get_default_workspace_path() == get_account_workspace(ACCOUNT_B)


def test_unclaimed_legacy_workspace_is_invisible_to_a_logged_in_account(data_root: Path):
    """有存量目录但没人认领（桌面还没写过标记）时，账号不从旧目录里读。

    此时宁可让用户先看不到旧数据（桌面在登录时会认领），也不能把上一个
    账号的东西端给当前账号。
    """
    (data_root / "workspace").mkdir()
    _set_active(data_root, ACCOUNT_A)

    assert get_legacy_workspace_owner() is None
    assert get_default_workspace_path() == get_account_workspace(ACCOUNT_A)


# ── 序列化默认值 ───────────────────────────────────────────────────────


def test_default_field_still_serializes_to_the_documented_value(data_root: Path):
    """账号维度不进 config.json：默认值仍是 `~/.forge/workspace`。

    写进配置就成了一次 tier-B 变更，每次切换账号都会弹「新会话生效」提示，
    而且会把用户配置改写成某个账号的绝对路径。
    """
    assert Config().agents.defaults.workspace == DEFAULT_WORKSPACE_VALUE
    assert DEFAULT_WORKSPACE_VALUE == "~/.forge/workspace"


# ── 端到端：issue #1185 item 8 的验收场景 ─────────────────────────────


def _session_manager() -> SessionManager:
    """按当前账号的工作区根建一个会话管理器 —— 与运行时同一条解析路径。"""
    return SessionManager(Config().workspace_path)


def test_a_then_b_then_a_keeps_their_own_sessions_and_memory(data_root: Path):
    """A 发消息 → 换 B（看不到 A 的会话、记忆为空）→ 切回 A，数据仍在。

    这条是 issue 里写明的验收路径。用真实的 SessionManager + 真实的
    `Config.workspace_path`，避免「测试里拼对了路径、运行时走的是另一条」。
    """
    # A 账号：建一个会话并留一条记忆
    _set_active(data_root, ACCOUNT_A)
    mgr_a = _session_manager()
    session = mgr_a.get_or_create("desktop:aaa", client_id="miqi-desktop")
    session.add_message("user", "A 的私密问题")
    mgr_a.save(session)
    memory_dir = Config().workspace_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text("A 的记忆", encoding="utf-8")

    # 换到 B：会话列表为空、记忆为空
    _set_active(data_root, ACCOUNT_B)
    mgr_b = _session_manager()
    assert mgr_b.list_sessions(client_id="miqi-desktop") == []
    assert not (Config().workspace_path / "memory" / "MEMORY.md").exists()

    # B 自己发消息也不影响 A
    session_b = mgr_b.get_or_create("desktop:bbb", client_id="miqi-desktop")
    session_b.add_message("user", "B 的问题")
    mgr_b.save(session_b)

    # 切回 A：自己的会话与记忆都还在，且看不到 B 的
    _set_active(data_root, ACCOUNT_A)
    mgr_a2 = _session_manager()
    keys = {s["key"] for s in mgr_a2.list_sessions(client_id="miqi-desktop")}
    assert "desktop:aaa" in keys
    assert "desktop:bbb" not in keys
    memory = (Config().workspace_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert memory == "A 的记忆"


# ── 文件工具的「默认工作区」判定必须跟着账号走 ─────────────────────────


def test_file_tools_treat_the_account_workspace_as_default(data_root: Path):
    """账号工作区必须被文件工具认作**默认**工作区。

    否则 `create_runtime_tool_registry` 会把它当用户自定义目录，跳过
    `sessions/<key>/files` 的 per-session 资产隔离与台账根处理 —— 账号会话表面
    上能用，文件却全落在工作区根上（#1185 评审抓到的回归，当时的单测与 E2E 都
    没覆盖：E2E 里 A 恰好认领了共享根，所以只有 A 那侧是真的）。
    """
    from miqi.agent.tools.filesystem import _is_default_workspace

    # 拿到账号级目录的账号
    _set_active(data_root, ACCOUNT_A)
    assert _is_default_workspace(Config().workspace_path) is True

    # 认领了旧目录的账号（工作区是 <数据根>/workspace）同样成立
    _set_legacy_owner(data_root, ACCOUNT_B)
    _set_active(data_root, ACCOUNT_B)
    assert _is_default_workspace(Config().workspace_path) is True

    # 而真正自定义的目录不该被认成默认 —— 那会把项目文件藏进 sessions/<key>/files
    assert _is_default_workspace(data_root / "some-project") is False
