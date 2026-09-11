"""Tests for exec environment description helpers and Git Bash exec.

The exec tool description and the per-turn session context used to
hard-code "exec 在沙箱中运行 / /home/miqi / /mnt/c" regardless of the
actual runtime state.  With the sandbox disabled, exec runs directly on
the host — and on Windows it now runs through Git Bash (bash.exe) when
available, so the AI's bash habits (; chains, ls/find/grep) keep working.
See the MOF-5 re-upload thrashing (bridge log 2026-08-24) for the
failure mode this prevents.
"""

import os

import pytest

from miqi.sandbox.manager import (
    describe_exec_environment,
    find_git_bash,
    sandbox_is_active,
    windows_path_to_msys,
)


class FakeSandboxManager:
    def __init__(
        self,
        enabled: bool = False,
        initialized: bool = False,
        allow_system_installs: bool = False,
    ):
        self.enabled = enabled
        self._initialized = initialized
        self.allow_system_installs = allow_system_installs


@pytest.mark.parametrize(
    "manager,expected",
    [
        (None, False),
        ("disabled", False),
        (FakeSandboxManager(enabled=False, initialized=True), False),
        (FakeSandboxManager(enabled=True, initialized=False), False),
        (FakeSandboxManager(enabled=True, initialized=True), True),
    ],
)
def test_sandbox_is_active(manager, expected):
    assert sandbox_is_active(manager) is expected


@pytest.mark.parametrize(
    "win_path,expected",
    [
        (r"C:\Users\Intership003\.miqi\workspace", "/c/Users/Intership003/.miqi/workspace"),
        ("D:/data/out", "/d/data/out"),
        ("/already/posix", "/already/posix"),
    ],
)
def test_windows_path_to_msys(win_path, expected):
    assert windows_path_to_msys(win_path) == expected


def _reset_git_bash_cache(monkeypatch):
    monkeypatch.setattr("miqi.sandbox.manager._git_bash_checked", False)
    monkeypatch.setattr("miqi.sandbox.manager._git_bash_path", None)


def test_find_git_bash_from_path(monkeypatch):
    _reset_git_bash_cache(monkeypatch)
    monkeypatch.setattr("miqi.sandbox.manager.shutil.which", lambda name: r"D:\tools\Git\bin\bash.exe")
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    assert find_git_bash() == r"D:\tools\Git\bin\bash.exe"


def test_find_git_bash_rejects_wsl_system32_bash(monkeypatch):
    """The WSL entrypoint (System32\bash.exe) must never be selected —
    it would run commands inside a Linux distro."""
    _reset_git_bash_cache(monkeypatch)
    monkeypatch.setattr(
        "miqi.sandbox.manager.shutil.which",
        lambda name: r"C:\Windows\System32\bash.exe",
    )
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    assert find_git_bash() is None


def test_find_git_bash_rejects_cygwin_bash(monkeypatch):
    """Cygwin bash.exe must never be selected — it uses /cygdrive/c
    paths, not the /c/ convention the environment description promises."""
    _reset_git_bash_cache(monkeypatch)
    monkeypatch.setattr(
        "miqi.sandbox.manager.shutil.which",
        lambda name: r"C:\cygwin64\bin\bash.exe",
    )
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    assert find_git_bash() is None


@pytest.mark.parametrize(
    "path,expected",
    [
        (r"C:\cygwin64\bin\bash.exe", True),
        (r"C:\cygwin\bin\bash.exe", True),
        (r"C:\Program Files\Git\bin\bash.exe", False),
        (r"C:\Windows\System32\bash.exe", False),
    ],
)
def test_is_cygwin_bash(path, expected):
    from miqi.sandbox.manager import _is_cygwin_bash

    assert _is_cygwin_bash(path) is expected


def test_find_git_bash_prefers_install_location_over_path(monkeypatch):
    _reset_git_bash_cache(monkeypatch)
    fake = r"C:\Program Files\Git\bin\bash.exe"
    monkeypatch.setattr(
        "miqi.sandbox.manager.shutil.which",
        lambda name: r"C:\Windows\System32\bash.exe",
    )
    monkeypatch.setattr(
        "miqi.sandbox.manager.os",
        _FakeOs(existing=(fake,)),
        raising=False,
    )
    assert find_git_bash() == fake


@pytest.mark.parametrize(
    "path,expected",
    [
        (r"C:\Windows\System32\bash.exe", True),
        (r"C:\windows\system32\bash.exe", True),
        (r"C:\Program Files\Git\bin\bash.exe", False),
        (r"D:\tools\bash.exe", False),
        ("relative/bash.exe", False),
    ],
)
def test_is_windows_system_bash(path, expected):
    from miqi.sandbox.manager import _is_windows_system_bash

    assert _is_windows_system_bash(path) is expected


class _FakeOsPath:
    """Stand-in for os.path in find_git_bash detection tests."""

    def __init__(self, existing: tuple[str, ...] = (), expandvars_result: str | None = None):
        self._existing = set(existing)
        self._expandvars_result = expandvars_result

    def exists(self, path: str) -> bool:
        return path in self._existing

    def isfile(self, path: str) -> bool:
        return path in self._existing

    def join(self, a: str, b: str) -> str:
        return a + "\\" + b

    def expandvars(self, path: str) -> str:
        return self._expandvars_result if self._expandvars_result is not None else path


class _FakeOs:
    """Stand-in for the os module inside miqi.sandbox.manager.

    Replaces the manager's ``os`` REFERENCE instead of patching the
    global os module — patching os.name globally breaks pathlib.Path on
    the other platform (Path() reads os.name and would try WindowsPath
    on Linux).
    """

    def __init__(
        self,
        existing: tuple[str, ...] = (),
        expandvars_result: str | None = None,
        name: str = "nt",
        env_path: str = "",
    ):
        self.name = name
        self.path = _FakeOsPath(existing, expandvars_result)
        self.pathsep = ";"
        self.environ = {"PATH": env_path}


def test_find_git_bash_from_common_location(monkeypatch, tmp_path):
    _reset_git_bash_cache(monkeypatch)
    monkeypatch.setattr("miqi.sandbox.manager.shutil.which", lambda name: None)
    fake = tmp_path / "Git" / "bin" / "bash.exe"
    monkeypatch.setattr(
        "miqi.sandbox.manager.os",
        _FakeOs(existing=(str(fake),), expandvars_result=str(fake)),
        raising=False,
    )
    assert find_git_bash() == str(fake)


def test_find_git_bash_not_installed(monkeypatch):
    _reset_git_bash_cache(monkeypatch)
    monkeypatch.setattr("miqi.sandbox.manager.shutil.which", lambda name: None)
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    assert find_git_bash() is None


def test_describe_exec_environment_sandbox_active():
    manager = FakeSandboxManager(enabled=True, initialized=True)
    text = describe_exec_environment(manager)
    assert "WSL" in text
    assert "/home/miqi/workspace" in text


def test_describe_exec_environment_sandbox_python_guidance(monkeypatch):
    """#822: inside the bwrap sandbox Windows .exe cannot run (no WSL
    interop), so the description must recommend sandbox-internal python3
    instead of the host venv interpreter path."""
    manager = FakeSandboxManager(enabled=True, initialized=True)
    monkeypatch.setattr("miqi.sandbox.manager._is_windows", lambda: True)

    class _FakeSys:
        executable = r"C:\git-program\venv\Scripts\python.exe"

    monkeypatch.setattr("miqi.sandbox.manager.sys", _FakeSys(), raising=False)
    text = describe_exec_environment(manager, workspace=r"C:\Users\demo\ws")
    assert "python3" in text
    assert "pip install --user" in text
    assert "externally-managed" in text
    assert "interop" in text
    # the host venv python must NOT be recommended inside the sandbox
    assert "推荐 Python 解释器" not in text
    assert "Scripts/python.exe" not in text


def test_describe_exec_environment_sandbox_python_no_interop_note_on_posix(monkeypatch):
    """The interop caveat is WSL-specific — on a POSIX host it must not
    mention /mnt/c or Windows .exe."""
    manager = FakeSandboxManager(enabled=True, initialized=True)
    monkeypatch.setattr("miqi.sandbox.manager._is_windows", lambda: False)
    text = describe_exec_environment(manager)
    assert "python3" in text
    assert "pip install --user" in text
    assert "interop" not in text
    assert "python.exe" not in text
    assert "Windows 程序" not in text


def test_describe_exec_environment_sandbox_python_persistent_install(monkeypatch):
    """With system installs enabled, python deps can be installed into the
    distro persistently via apt — the description should say so."""
    manager = FakeSandboxManager(
        enabled=True, initialized=True, allow_system_installs=True,
    )
    monkeypatch.setattr("miqi.sandbox.manager._is_windows", lambda: False)
    text = describe_exec_environment(manager)
    assert "python3" in text
    assert "apt-get install python3-" in text


def test_describe_exec_environment_no_sandbox_windows_cmd_fallback(monkeypatch):
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    text = describe_exec_environment(None)
    assert "Windows" in text
    assert "cmd" in text
    assert "&&" in text
    assert "/mnt/c" not in text
    assert "/home/miqi" not in text


def test_describe_exec_environment_cmd_fallback_warns_no_bash_or_wsl(monkeypatch):
    """On a Windows host where find_git_bash() returns None, the cmd
    fallback must warn the AI not to run raw bash/wsl commands — PATH may
    resolve ``bash`` to System32\\bash.exe (the WSL entrypoint stub), which
    errors with ``EXECUTABLE NOT FOUND`` when WSL is not enabled."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    text = describe_exec_environment(None)
    assert "本机未检测到 Git Bash" in text
    assert "不要直接运行 bash/wsl 命令" in text
    assert "EXECUTABLE NOT FOUND" in text
    assert "System32" in text


def test_describe_exec_environment_cmd_fallback_does_not_assert_wsl_absent(monkeypatch):
    """CodeRabbit #865: find_git_bash() is None only proves Git Bash is
    missing, NOT that WSL is unavailable.  The message must phrase the
    WSL-stub risk conditionally (``若 WSL 未启用``) rather than assert
    ``WSL 未安装`` as fact — a host with WSL installed but no Git Bash
    would otherwise get incorrect guidance to skip valid wsl commands."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    text = describe_exec_environment(None)
    assert "若子系统未启用" in text
    assert "WSL 未安装" not in text
    assert "Windows 子系统" not in text


def test_describe_exec_environment_git_bash_omits_bash_warning(monkeypatch):
    """The 'do not run bash/wsl' warning only applies to the cmd fallback;
    the Git Bash branch (which CAN run bash) must not carry it."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr(
        "miqi.sandbox.manager.find_git_bash",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    text = describe_exec_environment(None)
    assert "不要直接运行 bash/wsl" not in text
    assert "EXECUTABLE NOT FOUND" not in text


def test_describe_exec_environment_no_sandbox_windows_git_bash(monkeypatch):
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr(
        "miqi.sandbox.manager.find_git_bash",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    text = describe_exec_environment(None, workspace=r"C:\Users\demo\ws")
    assert "Git Bash" in text
    assert "bash 语法" in text
    assert "/c/Users/demo/ws" in text
    assert "cmd" not in text
    assert "/mnt/c" not in text
    assert "/home/miqi" not in text


def test_describe_exec_environment_no_sandbox_posix(monkeypatch):
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(name="posix"), raising=False)
    text = describe_exec_environment(None)
    assert "bash" in text
    assert "/mnt/c" not in text
    assert "/home/miqi" not in text


def test_describe_exec_environment_skills_dirs_git_bash(monkeypatch):
    """The description must point the AI to skill_manage for skill
    locations instead of hard-coding machine-specific builtin paths."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr(
        "miqi.sandbox.manager.find_git_bash",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    text = describe_exec_environment(None, workspace=r"C:\Users\demo\ws")
    assert "技能定位" in text
    assert "/c/Users/demo/ws/skills" in text
    assert "skill_manage" in text
    # machine-specific builtin paths must NOT leak into the prompt
    assert "miqi/skills" not in text


def test_describe_exec_environment_skills_dirs_cmd_fallback(monkeypatch):
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    text = describe_exec_environment(None, workspace=r"C:\Users\demo\ws")
    assert "技能定位" in text
    assert "skill_manage" in text
    # Separator-agnostic: the workspace path is joined on the host
    # platform (backslash on Windows, mixed on POSIX test runs).
    assert "demo" in text and "ws" in text and "skills" in text
    assert "miqi/skills" not in text


def test_describe_exec_environment_discloses_python_interpreter(monkeypatch):
    """The description must disclose the bridge's real python interpreter
    (with the Git Bash path form) — the AI otherwise resolves `python` to
    the WindowsApps store stub or a hung interpreter."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr(
        "miqi.sandbox.manager.find_git_bash",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )

    class _FakeSys:
        executable = r"C:\git-program\venv\Scripts\python.exe"

    monkeypatch.setattr(
        "miqi.sandbox.manager.sys", _FakeSys(), raising=False,
    )
    text = describe_exec_environment(None, workspace=r"C:\Users\demo\ws")
    assert "python" in text
    assert "/c/git-program/venv/Scripts/python.exe" in text


def test_describe_exec_environment_skills_dirs_sandbox_wsl():
    manager = FakeSandboxManager(enabled=True, initialized=True)
    text = describe_exec_environment(manager, workspace=r"C:\Users\demo\ws")
    assert "技能定位" in text
    assert "/mnt/c/Users/demo/ws/skills" in text
    assert "skill_manage" in text
    assert "miqi/skills" not in text


@pytest.mark.parametrize(
    "win_path,expected",
    [
        (r"C:\Users\demo\ws", "/mnt/c/Users/demo/ws"),
        ("D:/data", "/mnt/d/data"),
        ("/already/posix", "/already/posix"),
    ],
)
def test_windows_path_to_mnt(win_path, expected):
    from miqi.sandbox.manager import windows_path_to_mnt

    assert windows_path_to_mnt(win_path) == expected


def test_exec_tool_description_reflects_sandbox_state(monkeypatch):
    from miqi.agent.tools.shell import ExecTool

    tool_active = ExecTool(
        working_dir=".",
        sandbox_manager=FakeSandboxManager(enabled=True, initialized=True),
    )
    assert "WSL" in tool_active.description
    assert "/home/miqi/workspace" in tool_active.description

    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    tool_off = ExecTool(working_dir=".")
    assert "Windows" in tool_off.description
    assert "cmd" in tool_off.description
    assert "/mnt/c" not in tool_off.description
    assert "/home/miqi" not in tool_off.description


def test_exec_tool_description_git_bash(monkeypatch):
    from miqi.agent.tools.shell import ExecTool

    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr(
        "miqi.sandbox.manager.find_git_bash",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    tool = ExecTool(working_dir=r"C:\Users\demo\ws")
    assert "Git Bash" in tool.description
    assert "/c/Users/demo/ws" in tool.description


def test_session_context_reflects_sandbox_state(monkeypatch, tmp_path):
    from miqi.runtime.agent_control import build_session_context

    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    ctx = build_session_context(
        workspace=tmp_path,
        session_id="miqi-desktop:desktop:test",
        sandbox_manager=None,
    )
    assert "Windows" in ctx
    assert "cmd" in ctx
    assert str(tmp_path) in ctx
    assert "/mnt/c" not in ctx
    # the legacy "不要说 /home/miqi/workspace" disclaimer may remain,
    # but the WSL sandbox environment story must not be injected.  The
    # cmd-fallback caveat may mention the System32\bash.exe stub risk
    # (lowercase "bash/wsl"), but it must never claim the AI is running
    # IN a WSL sandbox (uppercase "WSL", /home/miqi/workspace story).
    assert "WSL" not in ctx

    ctx_active = build_session_context(
        workspace=tmp_path,
        session_id="miqi-desktop:desktop:test",
        sandbox_manager=FakeSandboxManager(enabled=True, initialized=True),
    )
    assert "/home/miqi/workspace" in ctx_active
    assert "WSL" in ctx_active


@pytest.mark.skipif(os.name != "nt", reason="Git Bash exec path is Windows-only")
def test_exec_direct_runs_through_git_bash_on_windows(tmp_path):
    """With Git Bash installed, ;-chained commands must actually execute
    (Windows cmd would echo the whole string verbatim instead)."""
    import asyncio

    from miqi.agent.tools.shell import ExecTool

    if find_git_bash() is None:
        pytest.skip("Git Bash not installed on this machine")

    tool = ExecTool(working_dir=str(tmp_path))

    async def _run() -> object:
        return await tool._execute_direct("echo A; echo B; echo C", str(tmp_path))

    result = asyncio.run(_run())
    assert result.exit_code == 0
    assert "A" in result.output and "B" in result.output and "C" in result.output
    # cmd would have echoed the command text verbatim; bash executed it
    assert "echo" not in result.output


def _reset_host_python_cache(monkeypatch):
    monkeypatch.setattr("miqi.sandbox.manager._host_python_checked", False)
    monkeypatch.setattr("miqi.sandbox.manager._host_python_path", None)


def test_find_host_python_skips_windowsapps_store_stub(monkeypatch):
    """The PATH scan must skip the WindowsApps python.exe store stub
    (opens the Microsoft Store, hangs) and pick the real interpreter."""
    from miqi.sandbox.manager import _find_host_python

    _reset_host_python_cache(monkeypatch)
    monkeypatch.setattr(
        "miqi.sandbox.manager.os",
        _FakeOs(
            existing=(r"C:\WindowsApps\python.exe", r"C:\Python313\python.exe"),
            env_path=r"C:\WindowsApps;C:\Python313",
        ),
        raising=False,
    )
    assert _find_host_python() == r"C:\Python313\python.exe"


def test_find_host_python_none_when_only_store_stub(monkeypatch):
    """All-PATH-stub machines (no real Python) must yield None, not the
    store stub — the AI then gets the 'ask the user to install' note."""
    from miqi.sandbox.manager import _find_host_python

    _reset_host_python_cache(monkeypatch)
    monkeypatch.setattr(
        "miqi.sandbox.manager.os",
        _FakeOs(
            existing=(r"C:\WindowsApps\python.exe",),
            env_path=r"C:\WindowsApps",
        ),
        raising=False,
    )
    assert _find_host_python() is None


def test_find_host_python_none_on_empty_path(monkeypatch):
    from miqi.sandbox.manager import _find_host_python

    _reset_host_python_cache(monkeypatch)
    monkeypatch.setattr(
        "miqi.sandbox.manager.os", _FakeOs(env_path=""), raising=False,
    )
    assert _find_host_python() is None


def test_describe_exec_environment_frozen_no_host_python(monkeypatch):
    """Packaged build, no host Python: sys.executable is the bridge exe
    itself and must NEVER be recommended as an interpreter (running it
    would launch a second bridge). The AI is told to have the user
    install Python or enable the sandbox instead."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr("miqi.sandbox.manager.find_git_bash", lambda: None)
    monkeypatch.setattr("miqi.sandbox.manager._find_host_python", lambda: None)

    class _FrozenSys:
        frozen = True
        executable = r"C:\Program Files\MiQroForge\miqi-bridge.exe"

    monkeypatch.setattr("miqi.sandbox.manager.sys", _FrozenSys(), raising=False)
    text = describe_exec_environment(None)
    assert "miqi-bridge.exe" not in text
    assert "推荐 Python 解释器" not in text
    assert "让用户安装" in text
    assert "沙箱" in text


def test_describe_exec_environment_frozen_uses_host_python(monkeypatch):
    """Packaged build with a real Python on PATH: recommend the host
    interpreter (msys path form under Git Bash), never the bridge exe."""
    monkeypatch.setattr("miqi.sandbox.manager.os", _FakeOs(), raising=False)
    monkeypatch.setattr(
        "miqi.sandbox.manager.find_git_bash",
        lambda: r"C:\Program Files\Git\bin\bash.exe",
    )
    monkeypatch.setattr(
        "miqi.sandbox.manager._find_host_python",
        lambda: r"D:\Python313\python.exe",
    )

    class _FrozenSys:
        frozen = True
        executable = r"C:\Program Files\MiQroForge\miqi-bridge.exe"

    monkeypatch.setattr("miqi.sandbox.manager.sys", _FrozenSys(), raising=False)
    text = describe_exec_environment(None, workspace=r"C:\Users\demo\ws")
    assert "/d/Python313/python.exe" in text
    assert "miqi-bridge.exe" not in text
