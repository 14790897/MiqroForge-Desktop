import os
import shutil
import tempfile
from argparse import Namespace
from pathlib import Path

import pytest

from miqi.paths import get_miqi_home
from tests.conftest import (
    _AUTO_BASETEMP_ATTR,
    _is_safe_to_clean,
    _repo_root,
    pytest_configure,
    pytest_unconfigure,
)


def test_pytest_basetemp_is_inside_repo(request, tmp_path):
    """Regression: tmp_path must be created under a repository-local base temp."""
    repo_root = Path(request.config.rootpath).resolve()
    resolved = tmp_path.resolve()
    assert resolved.is_relative_to(repo_root)
    assert ".pytest-basetemp" in str(resolved)


def test_global_fixture_isolates_home_and_temp(tmp_path):
    root = tmp_path.resolve()

    assert get_miqi_home().is_relative_to(root)
    assert Path(os.environ["HOME"]).resolve().is_relative_to(root)
    assert Path(tempfile.gettempdir()).resolve().is_relative_to(root)


@pytest.mark.self_managed_env
def test_self_managed_env_still_uses_test_owned_paths(monkeypatch, tmp_path):
    home = tmp_path / "managed-home"
    miqi_home = tmp_path / "managed-miqi"
    temp_dir = tmp_path / "managed-temp"
    home.mkdir()
    temp_dir.mkdir()

    monkeypatch.setenv("MIQI_HOME", str(miqi_home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TEMP", str(temp_dir))
    monkeypatch.setenv("TMP", str(temp_dir))
    monkeypatch.setenv("TMPDIR", str(temp_dir))
    monkeypatch.setattr(tempfile, "tempdir", str(temp_dir))

    assert get_miqi_home() == miqi_home.resolve()
    assert Path(tempfile.gettempdir()).resolve() == temp_dir.resolve()


def test_pytest_auto_basetemp_is_recorded_and_cleaned():
    """An automatic basetemp recorded on the config is cleaned after the session."""
    repo_root = _repo_root()
    basetemp = repo_root / ".pytest-basetemp-testcleanup"
    basetemp.mkdir(parents=True, exist_ok=True)
    (basetemp / "session-0").mkdir()

    fake_config = Namespace(option=Namespace(basetemp=None))
    setattr(fake_config, _AUTO_BASETEMP_ATTR, basetemp)

    pytest_unconfigure(fake_config)
    assert not basetemp.exists()


def test_pytest_explicit_basetemp_is_not_overridden():
    """pytest_configure must leave an explicit --basetemp unchanged."""
    explicit = "/some/explicit/basetemp"
    fake_config = Namespace(option=Namespace(basetemp=explicit))
    pytest_configure(fake_config)
    assert fake_config.option.basetemp == explicit
    assert not hasattr(fake_config, _AUTO_BASETEMP_ATTR)


def test_pytest_explicit_basetemp_is_not_cleaned():
    """Non-automatic basetemp names are not eligible for auto-clean."""
    repo_root = _repo_root()
    explicit_dir = repo_root / ".explicit-test-temp"
    explicit_dir.mkdir(parents=True, exist_ok=True)

    assert not _is_safe_to_clean(explicit_dir, repo_root)

    shutil.rmtree(explicit_dir)
    assert not explicit_dir.exists()
