"""Contract and integration tests for miqi.paths path resolution."""

from pathlib import Path, PurePosixPath

import pytest

from miqi.config.loader import load_config, save_config
from miqi.config.schema import Config
from miqi.paths import (
    get_config_path,
    get_legacy_config_path,
    get_legacy_data_dir,
    get_miqi_home,
    normalize_declared_separators,
    normalize_session_prefixed,
)
from miqi.utils.helpers import get_data_path, get_workspace_path


def test_windows_separator_path_is_a_single_component_on_posix():
    """为什么分隔符归一必须发生在判定会话前缀**之前**。

    反斜杠在 POSIX 上只是普通文件名字符，`sessions\\k\\files\\a.pdf` 在那里
    是**一个**组件，前缀规则根本看不见它 —— 于是退回朴素拼接，文件被解析到
    一个带字面反斜杠的假路径上（#1131 的另一副面孔）。用 PurePosixPath 断言，
    使这条在任何平台上都判别同一件事。
    """
    assert len(PurePosixPath(r"sessions\k\files\a.pdf").parts) == 1
    assert len(PurePosixPath(normalize_declared_separators(r"sessions\k\files\a.pdf")).parts) == 4


def test_normalize_declared_separators_rewrites_only_windows_forms():
    assert normalize_declared_separators(r"sessions\k\files\a.pdf") == "sessions/k/files/a.pdf"
    # Windows 根相对 `\sessions\...`：只去掉那个来自反斜杠的前导分隔符。
    assert normalize_declared_separators(r"\sessions\k\files\a.pdf") == "sessions/k/files/a.pdf"
    # 真正的 POSIX 绝对路径与 UNC 路径必须原样保留。
    assert normalize_declared_separators("/home/u/a.pdf") == "/home/u/a.pdf"
    assert normalize_declared_separators("//server/share/a.pdf") == "//server/share/a.pdf"
    # 反斜杠形态的 UNC 同样要保留**两个**前导分隔符：少一个就变成 POSIX 根路径
    # `/server/share/a.pdf`，那是另一个位置，边界检查也会锚错根。
    assert normalize_declared_separators(r"\\server\share\a.pdf") == "//server/share/a.pdf"


def test_normalize_session_prefixed_accepts_windows_separators(tmp_path):
    """会话前缀规则自己做入参归一，不指望每个调用方都记得（#1131）。"""
    base = tmp_path / "ws"
    files = base / "sessions" / "desktop_k" / "files"
    files.mkdir(parents=True)

    for declared in (r"sessions\desktop_k\files\a.pdf", "sessions/desktop_k/files/a.pdf"):
        got = normalize_session_prefixed(declared, files)
        assert got is not None, f"未识别出会话前缀：{declared}"
        assert got.resolve() == (files / "a.pdf").resolve()

    # 归一之后，「指向别的会话」才能被正确地拒绝，而不是变成一个怪文件名。
    with pytest.raises(PermissionError):
        normalize_session_prefixed(r"sessions\other\files\a.pdf", files)


def test_miqi_home_defaults_to_dot_miqi(monkeypatch, tmp_path):
    monkeypatch.delenv("MIQI_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert get_miqi_home() == (tmp_path / "home" / ".forge").resolve()


def test_miqi_home_uses_absolute_override(monkeypatch, tmp_path):
    configured = tmp_path / "custom-miqi"
    monkeypatch.setenv("MIQI_HOME", str(configured))

    assert get_miqi_home() == configured.resolve()


def test_miqi_home_resolves_relative_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MIQI_HOME", "state/miqi")

    assert get_miqi_home() == (tmp_path / "state" / "miqi").resolve()


def test_blank_miqi_home_uses_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MIQI_HOME", "   ")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert get_miqi_home() == (tmp_path / "home" / ".forge").resolve()


def test_config_and_legacy_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("MIQI_HOME", str(tmp_path / "miqi"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert get_config_path() == (tmp_path / "miqi" / "config.json").resolve()
    assert get_legacy_data_dir() == (tmp_path / "home" / ".assistant").resolve()
    assert get_legacy_config_path() == (
        tmp_path / "home" / ".assistant" / "config.json"
    ).resolve()


def test_path_getters_do_not_create_directories(monkeypatch, tmp_path):
    configured = tmp_path / "does-not-exist"
    monkeypatch.setenv("MIQI_HOME", str(configured))

    assert get_miqi_home() == configured.resolve()
    assert not configured.exists()


def test_data_path_uses_miqi_home_when_configured(monkeypatch, tmp_path):
    """When MIQI_HOME is set explicitly, get_data_path follows it."""
    miqi_home = tmp_path / "configured-miqi"
    legacy = tmp_path / "home" / ".assistant"
    legacy.mkdir(parents=True)
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    assert get_data_path() == miqi_home.resolve()


def test_data_path_defaults_to_dot_miqi_without_legacy(monkeypatch, tmp_path):
    """Fresh install: no MIQI_HOME and no legacy dir -> default ~/.forge."""
    monkeypatch.delenv("MIQI_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    data_path = get_data_path()

    assert data_path == (tmp_path / "home" / ".forge").resolve()


def test_data_path_falls_back_to_legacy_assistant(monkeypatch, tmp_path):
    """Legacy install: no MIQI_HOME but ~/.assistant exists -> use legacy."""
    monkeypatch.delenv("MIQI_HOME", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    legacy = home / ".assistant"
    legacy.mkdir(parents=True)

    data_path = get_data_path()

    assert data_path == legacy.resolve()


def test_data_path_prefers_miqi_when_both_homes_exist(monkeypatch, tmp_path):
    """If both legacy and current home exist, prefer the current ~/.forge."""
    monkeypatch.delenv("MIQI_HOME", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    legacy = home / ".assistant"
    default_home = home / ".forge"
    legacy.mkdir(parents=True)
    default_home.mkdir(parents=True)

    data_path = get_data_path()

    assert data_path == default_home.resolve()


def test_data_path_does_not_fallback_to_old_miqi_dir(monkeypatch, tmp_path):
    """#1175: 旧的 ~/.miqi 不做迁移——即使它存在，也新建/返回 ~/.forge。"""
    monkeypatch.delenv("MIQI_HOME", raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    old_home = home / ".miqi"
    old_home.mkdir(parents=True)
    default_home = home / ".forge"

    data_path = get_data_path()

    assert data_path == default_home.resolve()
    assert default_home.exists()


def test_config_loader_uses_miqi_home(monkeypatch, tmp_path):
    miqi_home = tmp_path / "isolated"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))
    config = Config()

    save_config(config)

    assert (miqi_home / "config.json").is_file()
    assert load_config().model_dump() == config.model_dump()


def test_data_and_default_workspace_use_miqi_home(monkeypatch, tmp_path):
    miqi_home = tmp_path / "isolated"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    assert get_data_path() == miqi_home.resolve()
    assert get_workspace_path() == (miqi_home / "workspace").resolve()


def test_default_config_workspace_uses_miqi_home(monkeypatch, tmp_path):
    miqi_home = tmp_path / "isolated"
    monkeypatch.setenv("MIQI_HOME", str(miqi_home))

    assert Config().agents.defaults.workspace == "~/.forge/workspace"
    assert Config().workspace_path == (miqi_home / "workspace").resolve()


def test_explicit_workspace_is_not_rebased_to_miqi_home(monkeypatch, tmp_path):
    monkeypatch.setenv("MIQI_HOME", str(tmp_path / "isolated"))
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "explicit-workspace")

    assert config.workspace_path == (tmp_path / "explicit-workspace").resolve()
