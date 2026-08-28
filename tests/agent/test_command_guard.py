"""Unit tests for the capability-based command guard (issue #811)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from miqi.agent.command_guard import (
    RuntimePaths,
    evaluate_command,
    split_subcommands,
)


@pytest.fixture
def rt(tmp_path) -> RuntimePaths:
    ws = tmp_path / "workspace"
    sess = ws / "sessions" / "desktop_abc" / "files"
    sess.mkdir(parents=True)
    other = ws / "sessions" / "other_key" / "files"
    other.mkdir(parents=True)
    return RuntimePaths(
        host_cwd=str(sess),
        host_workspace=str(ws),
        session_files_dir=str(sess),
        sandbox_active=False,
        sandbox_cwd="/home/miqi/workspace",
        miqi_home=str(tmp_path / "miqi-home"),
        host_home=str(tmp_path / "home"),
    )


def sandbox_rt(tmp_path) -> RuntimePaths:
    ws = tmp_path / "workspace"
    sess = ws / "sessions" / "desktop_abc" / "files"
    sess.mkdir(parents=True)
    return RuntimePaths(
        host_cwd=str(sess),
        host_workspace=str(ws),
        session_files_dir=str(sess),
        sandbox_active=True,
        sandbox_cwd="/home/miqi/workspace",
        miqi_home=str(tmp_path / "miqi-home"),
        host_home=str(tmp_path / "home"),
    )


def v(cmd: str, rt: RuntimePaths):
    return evaluate_command(cmd, rt)


# ── split_subcommands ────────────────────────────────────────────────


def test_split_basic_operators():
    assert split_subcommands("a && b") == ["a", "b"]
    assert split_subcommands("a; b") == ["a", "b"]
    assert split_subcommands("a || b") == ["a", "b"]
    assert split_subcommands("a & b") == ["a", "b"]
    assert split_subcommands("a && b && c") == ["a", "b", "c"]
    assert split_subcommands("a;b") == ["a", "b"]


def test_split_quotes_backticks_parens_protect():
    assert split_subcommands('echo "a && b"') == ['echo "a && b"']
    assert split_subcommands("echo $(echo a && b)") == ["echo $(echo a && b)"]
    assert split_subcommands("echo `echo a && b`") == ["echo `echo a && b`"]
    assert split_subcommands("(a && b) && c") == ["(a && b)", "c"]


def test_split_redirects_not_separators():
    assert split_subcommands("echo x 2>&1") == ["echo x 2>&1"]
    assert split_subcommands("echo x &> out") == ["echo x &> out"]


def test_split_unbalanced_quote_is_single_part():
    assert split_subcommands('echo "a && b') == ['echo "a && b']


# ── session 内 rm -rf 放行（issue #811 核心） ─────────────────────────


def test_rm_in_scope_allowed(rt):
    assert v("rm -rf ./run-output", rt).allowed
    assert v("rm -rf run-output", rt).allowed
    assert v("rm -rf build*", rt).allowed is False  # glob → uncertain
    assert v("rm file.txt", rt).allowed


def test_rm_session_tree_allowed(rt):
    # ../out resolves into sessions/desktop_abc/out — the session tree.
    assert v("rm -rf ../out", rt).allowed
    assert v("rm -rf ../..", rt).allowed is False  # sessions root
    # the session tree ROOT itself is protected
    verdict = v("rm -rf ../", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "workspace_root"


def test_rm_other_session_denied(rt):
    # ../../other_key from files/ → sessions/other_key (sibling session)
    verdict = v("rm -rf ../../other_key", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "other_session"
    assert "其他 session" in verdict.message
    assert "安全替代" in verdict.message


def test_rm_system_and_outside_denied(rt):
    verdict = v("rm -rf /etc/guard811-test", rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    assert verdict.message.startswith("Error: 命令被沙箱护栏拦截。")
    assert "安全替代" in verdict.message

    assert not v("rm -rf /", rt).allowed


def test_rm_workspace_root_denied(rt):
    # Windows paths must be QUOTED: unquoted backslashes are shell
    # escapes (bash executes C:\ws as C:ws).
    verdict = v(f'rm -rf "{rt.host_workspace}"', rt)
    assert not verdict.allowed
    assert verdict.reason_code == "workspace_root"

    # "rm -rf ." from the session files dir = deleting the files root
    verdict = v("rm -rf .", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "workspace_root"


def test_rm_uncertain_paths_denied(rt):
    assert not v("rm -rf $BUILD_DIR", rt).allowed
    assert not v("rm -rf $(pwd)", rt).allowed
    assert not v("rm -rf build*", rt).allowed
    assert not v("rm -rf ~/outside", rt).allowed  # home outside ws


def test_rm_quoted_not_an_op(rt):
    # "rm -rf" inside a quoted echo is not a delete — the engine passes it;
    # (the legacy deny-pattern scan in ExecTool still blocks it, unchanged).
    assert v('echo "rm -rf x"', rt).allowed


def test_partially_quoted_operators_not_bypassed(rt):
    """Issue #811 review (critical): quote removal means 'rm' / s"udo"
    in command position EXECUTE as rm/sudo — must not bypass the checks."""
    # 'rm' in command position executes as rm after quote removal
    verdict = v("'rm' -rf /etc/x", rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    # s"udo" / 'sudo' in command position execute as sudo
    assert v('s"udo" whoami', rt).reason_code == "privilege"
    assert v("'sudo' whoami", rt).reason_code == "privilege"
    assert v("'su''do' whoami", rt).reason_code == "privilege"
    # quoted rm after a pipe is still the command word
    assert not v("ls | 'rm' -rf /etc/x", rt).allowed
    # fully quoted ARGUMENTS stay arguments
    assert v('echo "rm -rf x"', rt).allowed


def test_backslash_escaped_operators_not_bypassed(rt):
    """Issue #811 review: the shell removes backslash escapes, so
    \\rm executes as rm — must not bypass the checks."""
    verdict = v("\\rm -rf /etc/x", rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    assert v("\\sudo whoami", rt).reason_code == "privilege"
    assert not v("\\r\\m -rf /etc/x", rt).allowed
    # an escaped redirect operator is a literal word, not a redirect
    assert v("echo \\> out.txt", rt).allowed


def test_quoted_flags_still_classified(rt):
    """Issue #811 review: quoted/escaped flags execute as flags —
    python '-c' and find '-delete' must not skip the checks."""
    verdict = v("python '-c' \"import shutil; shutil.rmtree('/etc/x')\"", rt)
    assert not verdict.allowed
    verdict = v("find /etc '-delete'", rt)
    assert not verdict.allowed
    assert v("find . '-delete'", rt).allowed


# ── 复合命令逐子命令判定 ──────────────────────────────────────────────


def test_compound_per_subcommand(rt):
    verdict = v("ls && rm -rf ./run-output", rt)
    assert verdict.allowed
    assert verdict.subcommands == ["ls", "rm -rf ./run-output"]
    assert verdict.handled == [1]


def test_compound_any_bad_subcommand_denies(rt):
    verdict = v("mkdir a && rm -rf /etc/b", rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    assert "安全替代" in verdict.message


def test_compound_command_substitution_stays_atomic(rt):
    # The whole $(...) stays ONE subcommand (no split inside); the engine
    # sees no top-level file op here — the $() deny pattern in
    # ExecTool._guard_command is the backstop that blocks it.
    verdict = v("echo $(rm -rf /etc/x && echo done)", rt)
    assert verdict.subcommands == ["echo $(rm -rf /etc/x && echo done)"]


# ── cp / mv 双侧检查 ──────────────────────────────────────────────────


def test_cp_target_classified(rt):
    assert v("cp a.txt b.txt", rt).allowed
    assert v("cp a.txt /etc/evil", rt).allowed is False
    # source readable even from system paths
    assert v("cp /etc/hostname ./out", rt).allowed
    # source from ANOTHER session is a cross-session read → denied
    assert v("cp ../../other_key/files/x.txt ./out", rt).allowed is False


def test_mv_source_and_target_classified(rt):
    assert v("mv a.txt b.txt", rt).allowed
    assert v("mv /etc/x ./out", rt).allowed is False  # source out of scope
    assert v("mv ./out /etc/x", rt).allowed is False  # target system


def test_install_as_file_op_only_standalone(rt):
    assert v("install -m 644 a.txt b.txt", rt).allowed
    assert v("install a.txt /etc/evil", rt).allowed is False
    # package managers are NOT file ops
    assert v("pip install requests", rt).allowed
    assert v("npm install react", rt).allowed
    assert v("make install", rt).allowed
    assert v("cmake --install build", rt).allowed


# ── 内联脚本能力对等（换个写法同样被拦） ────────────────────────────────


def test_inline_python_shutil_in_scope_allowed(rt):
    verdict = v('python -c "import shutil; shutil.rmtree(\'./out\')"', rt)
    assert verdict.allowed


def test_inline_python_shutil_out_of_scope_denied(rt):
    verdict = v("python -c \"import shutil; shutil.rmtree('/etc/x')\"", rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")


def test_inline_python_unresolvable_denied(rt):
    verdict = v('python -c "import shutil, sys; shutil.rmtree(sys.argv[1])"', rt)
    assert not verdict.allowed
    assert verdict.reason_code == "script_uncertain"
    assert "安全替代" in verdict.message


def test_inline_node_fs_denied(rt):
    verdict = v("node -e \"fs.rmSync('/etc/x', {recursive: true})\"", rt)
    assert not verdict.allowed


def test_inline_perl_unlink_denied(rt):
    verdict = v("perl -e 'unlink \"/etc/x\"'", rt)
    assert not verdict.allowed


def test_nested_bash_c_parsed(rt):
    verdict = v('bash -c "rm -rf /etc/x"', rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    assert v('bash -c "rm -rf ./out"', rt).allowed


def test_nested_bash_c_find_denied(rt):
    verdict = v('bash -c "find /etc -delete"', rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    # nested find -exec is refused outright
    verdict = v('bash -c "find . -exec rm {} \\\\;"', rt)
    assert not verdict.allowed
    assert verdict.reason_code == "find_exec"


def test_nested_bash_c_redirects_checked(rt):
    """Issue #811 review: redirect targets inside a nested shell payload
    must be classified — bash -c "echo x > /etc/evil" writes /etc/evil."""
    verdict = v('bash -c "echo x > /etc/evil"', rt)
    assert not verdict.allowed
    assert verdict.reason_code in ("system_path", "outside_workspace")
    assert v('bash -c "echo x > ./out.txt"', rt).allowed


def test_plain_python_c_not_flagged(rt):
    assert v('python -c "print(1)"', rt).allowed


# ── sudo 结构化拒绝 ───────────────────────────────────────────────────


def test_sudo_structured_refusal(rt):
    verdict = v("sudo whoami", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "privilege"
    assert "提权" in verdict.message
    assert "pip install --user" in verdict.message
    assert "设置 > 沙箱隔离" in verdict.message
    # even compound sudo in any subcommand
    verdict = v("echo ok && sudo rm -rf ./x", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "privilege"


# ── find / 重定向 / 写入操作 ──────────────────────────────────────────


def test_find_delete_in_scope_allowed(rt):
    assert v("find . -name '*.pyc' -delete", rt).allowed
    assert v("find ./sub -type d -empty -delete", rt).allowed


def test_find_out_of_scope_or_exec_denied(rt):
    assert not v("find /etc -delete", rt).allowed
    verdict = v("find . -exec rm {} \\;", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "find_exec"


def test_redirect_targets_classified(rt):
    assert v("echo x > out.txt", rt).allowed
    assert not v("echo x > /etc/evil", rt).allowed
    assert v("echo x 2>/dev/null", rt).allowed
    assert v("echo x > /dev/null", rt).allowed
    assert v("cmd 2>&1", rt).allowed
    assert v("python s.py > logs/out.txt", rt).allowed


def test_redirect_multiple_targets_all_checked(rt):
    """Issue #811 review: every redirect target must be classified —
    an allowed first target must not mask a later out-of-scope one."""
    assert not v("echo a > ok.txt 2> /etc/evil", rt).allowed
    assert v("echo a > ok.txt 2> other.txt", rt).allowed
    # a quoted TARGET is still a redirect (only the operator counts)
    assert not v('echo a 2>"/etc/evil"', rt).allowed
    # a quoted OPERATOR is a literal word, not a redirect
    assert v("echo '>' out.txt", rt).allowed


def test_write_ops_classified(rt):
    assert v("mkdir out", rt).allowed
    assert not v("mkdir /etc/evil", rt).allowed
    assert v("touch a.txt", rt).allowed
    assert not v("tee /etc/evil", rt).allowed
    assert not v("touch /etc/evil", rt).allowed


def test_ln_checks_only_link_name(rt):
    # dangling /etc target is legal; only the link NAME must be in scope
    assert v("ln -s /etc/passwd out", rt).allowed
    assert not v("ln -s x /etc/evil", rt).allowed


# ── 沙箱语义 ──────────────────────────────────────────────────────────


def test_sandbox_internal_paths_allowed(tmp_path):
    rt = sandbox_rt(tmp_path)
    assert v("rm -rf /home/miqi/workspace/x", rt).allowed
    assert v("rm -rf /home/miqi/.venv", rt).allowed
    assert v("rm -rf /tmp/x", rt).allowed
    assert v("rm -rf ./run-output", rt).allowed  # relative → sandbox cwd
    assert v("rm -rf ~/x", rt).allowed  # ~ → /home/miqi


def test_sandbox_roots_and_system_denied(tmp_path):
    rt = sandbox_rt(tmp_path)
    verdict = v("rm -rf /home/miqi/workspace", rt)
    assert not verdict.allowed
    assert verdict.reason_code == "workspace_root"
    assert not v("rm -rf /", rt).allowed
    assert not v("rm -rf /etc/x", rt).allowed
    assert not v("rm -rf /usr/bin/python3", rt).allowed


def test_sandbox_mnt_c_maps_to_host(tmp_path):
    rt = sandbox_rt(tmp_path)
    assert not v("rm -rf /mnt/c/Windows/System32/x", rt).allowed
    if os.name != "nt":
        # /mnt/c host mapping only exists on Windows + WSL; on native
        # Linux a host path inside the sandbox context is just outside.
        return
    # a host path inside the session scope, spelled in the /mnt/<drive>
    # form, is writable — exercises the /mnt/<drive> → host mapping
    # itself.  The drive letter comes from the actual host path (CI
    # runners may put TEMP on D: — issue #811 review).
    host = rt.session_files_dir.replace("\\", "/")
    mnt = f"/mnt/{host[0].lower()}" + host[2:]  # C:/... → /mnt/c/...
    verdict = v(f"rm -rf {mnt}/x", rt)
    assert verdict.allowed


def test_sandbox_traversal_resolved(tmp_path):
    rt = sandbox_rt(tmp_path)
    # /home/miqi/workspace/../../etc/x → /etc/x → system
    assert not v("rm -rf /home/miqi/workspace/../../etc/x", rt).allowed


# ── Windows 绝对路径（仅 Windows 平台有意义） ─────────────────────────


def test_guard_runtime_paths_uses_cwd_not_working_dir(tmp_path, monkeypatch):
    """Issue #811 review: the session key must come from the per-call
    cwd, not from self.working_dir (a caller-passed working_dir can
    point at a different session)."""
    from miqi.agent.tools.shell import ExecTool

    ws = tmp_path / "workspace"
    sess_a = ws / "sessions" / "aaa" / "files"
    sess_b = ws / "sessions" / "bbb" / "files"
    sess_a.mkdir(parents=True)
    sess_b.mkdir(parents=True)
    monkeypatch.setattr(
        "miqi.runtime.file_handlers._get_workspace_path", lambda: str(ws),
    )
    tool = ExecTool(working_dir=str(sess_a))
    rt = tool._guard_runtime_paths(str(sess_b), sandbox_active=False)
    assert rt.current_session_key == "bbb"
    assert rt.host_workspace == str(ws.resolve())
    assert Path(rt.session_files_dir) == sess_b.resolve()


def test_guard_runtime_paths_falls_back_to_working_dir(tmp_path, monkeypatch):
    from miqi.agent.tools.shell import ExecTool

    ws = tmp_path / "workspace"
    sess_a = ws / "sessions" / "aaa" / "files"
    sess_a.mkdir(parents=True)
    monkeypatch.setattr(
        "miqi.runtime.file_handlers._get_workspace_path", lambda: str(ws),
    )
    tool = ExecTool(working_dir=str(sess_a))
    # per-call cwd outside any session → fall back to working_dir
    rt = tool._guard_runtime_paths(str(tmp_path), sandbox_active=False)
    assert rt.current_session_key == "aaa"


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_windows_system_paths_denied(rt):
    # Windows paths must be QUOTED: unquoted backslashes are shell
    # escapes (bash executes C:\Windows as C:Windows).
    assert not v(r'rm -rf "C:\Windows\Temp"', rt).allowed
    assert not v(r'rm -rf "C:\\"', rt).allowed
    assert not v(r'del /f /q "C:\Windows\Temp\x.txt"', rt).allowed


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_windows_session_scope_allowed(rt):
    target = str(
        rt.session_files_dir + os.sep + "out"
    )
    assert v(f'rm -rf "{target}"', rt).allowed


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_windows_unc_denied(rt):
    assert not v(r'rm -rf "\\\\server\\share\\x"', rt).allowed
