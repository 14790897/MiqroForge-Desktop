"""#984 layer 3 — python write-family spellings in the command guard.

The inline-script vocabulary now covers the write spellings an agent
actually reaches for (``open(..., 'w')``, ``write_text``/``write_bytes``,
``os.mkdir``/``makedirs``, ``shutil.copy*``, ``os.rename``) so re-spelling
a write as python no longer bypasses the check (issue #984 期望 1/3).

Boundaries locked here:

* **fail-open**: a READ spelling (``open(f)`` / ``open(f, 'r')``) is never
  flagged, and an unrecognised spelling is not flagged either — the kernel
  layer (``/mnt`` ro-bind + per-call rw binds) is the enforcement layer;
* **no false positives**: session scope AND the per-call authorized roots
  (#821 ``_user_roots``, which layer 1 binds rw in the sandbox) keep
  working — the tightening ships with the authorization channel;
* **heredoc fidelity**: heredoc bodies are read from the RAW text
  (``_raw_heredoc_body``) so quotes survive; otherwise every authorized
  heredoc delivery would degrade to ``script_uncertain``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from miqi.agent.command_guard import RuntimePaths, evaluate_command
from miqi.agent.tools.shell import ExecTool

# ── helpers ──────────────────────────────────────────────────────────────

#: Sandbox-mode paths are synthetic POSIX paths: they classify identically
#: on every host (``/srv`` is neither a system root nor a sandbox overlay),
#: so the same payloads exercise Windows and Linux CI.
AUTH = "/srv/data/out"          # authorized root (#821 grant)
SIBLING = "/srv/data/other"     # same tree, NOT authorized


def _posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def host_rt(tmp_path: Path, roots: tuple[str, ...] = ()) -> RuntimePaths:
    """Host-semantics context (sandbox off)."""
    ws = tmp_path / "workspace"
    sess = ws / "sessions" / "desktop_abc" / "files"
    sess.mkdir(parents=True, exist_ok=True)
    return RuntimePaths(
        host_cwd=str(sess),
        host_workspace=str(ws),
        session_files_dir=str(sess),
        sandbox_active=False,
        sandbox_cwd="/home/miqi/workspace",
        miqi_home=str(tmp_path / "miqi-home"),
        host_home=str(tmp_path / "home"),
        extra_write_roots=roots,
    )


def sandbox_rt(tmp_path: Path, roots: tuple[str, ...] = ()) -> RuntimePaths:
    """bwrap-semantics context (sandbox on)."""
    ws = tmp_path / "workspace"
    sess = ws / "sessions" / "desktop_abc" / "files"
    sess.mkdir(parents=True, exist_ok=True)
    return RuntimePaths(
        host_cwd=str(sess),
        host_workspace=str(ws),
        session_files_dir=str(sess),
        sandbox_active=True,
        sandbox_cwd="/home/miqi/workspace",
        miqi_home=str(tmp_path / "miqi-home"),
        host_home=str(tmp_path / "home"),
        extra_write_roots=roots,
    )


def v(cmd: str, rt: RuntimePaths):
    return evaluate_command(cmd, rt)


# ── 期望 3：python 写会话外被拦截（换写法不再绕过） ─────────────────────


class TestWriteSpellingsDenied:
    """Every write spelling in the #984 list is refused out of scope."""

    @pytest.mark.parametrize(
        "payload",
        [
            # open(..., <write mode>) — the issue's bypass point
            f"open('{SIBLING}/a.txt','w').write('x')",
            f"open('{SIBLING}/a.txt','a').write('x')",
            f"open('{SIBLING}/a.txt','x').write('x')",
            f"open('{SIBLING}/a.txt','wb').write(b'x')",
            f"open('{SIBLING}/a.txt','ab').write(b'x')",
            f"open('{SIBLING}/a.txt','r+').write('x')",
            f"open('{SIBLING}/a.txt', mode='w').write('x')",
            # pathlib
            f"from pathlib import Path; Path('{SIBLING}/a.txt').write_text('x')",
            f"from pathlib import Path; Path('{SIBLING}/a.txt').write_bytes(b'x')",
            f"from pathlib import Path; Path('{SIBLING}/a.txt').open('w')",
            # mkdir family
            f"import os; os.makedirs('{SIBLING}/assets')",
            f"import os; os.mkdir('{SIBLING}/assets')",
            # copy / move / rename
            f"import shutil; shutil.copy('/etc/hosts','{SIBLING}/a.txt')",
            f"import shutil; shutil.copy2('/etc/hosts','{SIBLING}/a.txt')",
            f"import shutil; shutil.copyfile('/etc/hosts','{SIBLING}/a.txt')",
            f"import shutil; shutil.move('{SIBLING}/a','{SIBLING}/b')",
            f"import os; os.rename('{SIBLING}/a','{SIBLING}/b')",
            f"import os; os.replace('{SIBLING}/a','{SIBLING}/b')",
            f"import shutil; shutil.copytree('/etc/x','{SIBLING}/y')",
        ],
    )
    def test_spelling_out_of_scope_denied(self, tmp_path, payload):
        verdict = v(f'python3 -c "{payload}"', sandbox_rt(tmp_path))
        assert not verdict.allowed, payload
        assert verdict.reason_code == "outside_workspace", payload
        assert "安全替代" in verdict.message

    def test_shell_and_python_now_agree(self, tmp_path):
        """The issue's exact comparison: bash write refused, python write
        refused too (was: python allowed)."""
        rt = sandbox_rt(tmp_path)
        shell = v(f"mkdir -p {SIBLING}/xxx", rt)
        py = v(
            f'python3 -c "from pathlib import Path; '
            f"Path('{SIBLING}/xxx/a.txt').write_text('x')\"",
            rt,
        )
        assert not shell.allowed
        assert not py.allowed
        assert shell.reason_code == py.reason_code == "outside_workspace"

    def test_heredoc_write_denied(self, tmp_path):
        """The real delivery shape from the issue log (heredoc)."""
        rt = sandbox_rt(tmp_path)
        verdict = v(
            "python3 - <<'PYEOF'\n"
            "from pathlib import Path\n"
            f"Path('{SIBLING}/_probe.txt').write_text('ok')\n"
            "PYEOF",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "outside_workspace"

    def test_copy_source_is_read_but_target_is_checked(self, tmp_path):
        """cp parity: the SOURCE may live outside the scope (read), the
        TARGET may not (write)."""
        rt = sandbox_rt(tmp_path)
        assert v(
            "python3 -c \"import shutil; shutil.copy('/etc/hosts','./out.txt')\"",
            rt,
        ).allowed
        verdict = v(
            f"python3 -c \"import shutil; shutil.copy('./x','{SIBLING}/a.txt')\"",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "outside_workspace"

    def test_copy_source_that_is_also_written_stays_a_mutation(self, tmp_path):
        """A literal used as a copy source AND elsewhere is not a read."""
        rt = sandbox_rt(tmp_path)
        verdict = v(
            "python3 -c \"import shutil, os; "
            f"shutil.copy('{SIBLING}/a','./b'); os.remove('{SIBLING}/a')\"",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "outside_workspace"
        # same literal on both sides of one copy: the target wins
        verdict = v(
            f"python3 -c \"import shutil; shutil.copy('{SIBLING}/a','{SIBLING}/a')\"",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "outside_workspace"

    def test_copy_keyword_source_is_read(self, tmp_path):
        rt = sandbox_rt(tmp_path)
        assert v(
            "python3 -c \"import shutil; "
            "shutil.copy(src='/etc/x', dst='./y')\"",
            rt,
        ).allowed

    @pytest.mark.parametrize(
        "call",
        ["copy", "copy2", "copyfile", "copytree"],
    )
    def test_copy_keyword_dst_first_target_is_still_a_write(self, tmp_path, call):
        """``dst=`` may be written BEFORE ``src=`` (#1007 review).

        The guard used to take ``args[0]`` as the source unconditionally,
        so in this order it labelled the out-of-scope TARGET a copy source
        → ``for_write=False`` → "reads anywhere else are unrestricted" →
        the write was allowed.  The source here is in-scope (the ordinary
        case: the agent copies a file it may write to a directory it may
        not), which is what makes the target the only thing the guard has
        to get right.
        """
        rt = sandbox_rt(tmp_path)
        payload = (
            f"import shutil; shutil.{call}(dst='{SIBLING}/evil.txt', "
            "src='./in.txt')"
        )
        verdict = v(f'python3 -c "{payload}"', rt)
        assert not verdict.allowed, payload
        assert verdict.reason_code == "outside_workspace", payload

    def test_copy_keyword_order_does_not_matter(self, tmp_path):
        """Both keyword orders classify identically to the positional form:
        source = read, target = write."""
        rt = sandbox_rt(tmp_path)
        for payload in (
            f"import shutil; shutil.copy('./in.txt','{SIBLING}/evil.txt')",
            f"import shutil; shutil.copy(src='./in.txt', dst='{SIBLING}/evil.txt')",
            f"import shutil; shutil.copy(dst='{SIBLING}/evil.txt', src='./in.txt')",
        ):
            verdict = v(f'python3 -c "{payload}"', rt)
            assert not verdict.allowed, payload
            assert verdict.reason_code == "outside_workspace", payload
        # ...and an in-scope target stays allowed in either order
        for payload in (
            "import shutil; shutil.copy(src='/etc/hostname', dst='./out.txt')",
            "import shutil; shutil.copy(dst='./out.txt', src='/etc/hostname')",
        ):
            assert v(f'python3 -c "{payload}"', rt).allowed, payload

    def test_copy_keyword_source_is_read_even_when_dst_first(self, tmp_path):
        """The authorised-root parity case: keyword order must not turn the
        granted target into a read and the read source into a write."""
        rt = sandbox_rt(tmp_path, roots=(AUTH,))
        for payload in (
            f"import shutil; shutil.copy(src='/etc/hostname', dst='{AUTH}/a.txt')",
            f"import shutil; shutil.copy(dst='{AUTH}/a.txt', src='/etc/hostname')",
        ):
            verdict = v(f'python3 -c "{payload}"', rt)
            assert verdict.allowed, payload


# ── fail-open：读写法与无法识别的写法不得误报 ───────────────────────────


class TestNoFalsePositives:
    @pytest.mark.parametrize(
        "payload",
        [
            "print(open('data.txt').read())",
            "print(open('data.txt','r').read())",
            "print(open('/etc/hosts','rb').read())",
            "print(open('/etc/hosts','rb',encoding=None).read())",
            "import os; print(open(os.path.join('/etc','hosts'),'r').read())",
            "import json; json.load(open('x.json'))",
            "print(1)",
            "from pathlib import Path; print(Path('a.txt').read_text())",
            "import shutil; print(shutil.which('python3'))",
        ],
    )
    def test_read_and_benign_payloads_allowed(self, tmp_path, payload):
        verdict = v(f'python3 -c "{payload}"', sandbox_rt(tmp_path))
        assert verdict.allowed, payload

    def test_read_heredoc_allowed(self, tmp_path):
        verdict = v(
            "python3 - <<'EOF'\nprint(open('data.txt').read())\nEOF",
            sandbox_rt(tmp_path),
        )
        assert verdict.allowed

    def test_unrecognised_spelling_fails_open(self, tmp_path):
        """Static analysis is incomplete by design — a spelling outside the
        vocabulary is left to the kernel layer, never blanket-denied."""
        rt = sandbox_rt(tmp_path)
        assert v(
            "python3 -c \"import numpy; numpy.save('/srv/data/other/a.npy', 1)\"",
            rt,
        ).allowed
        assert v(
            "python3 -c \"import os; fd=os.open('/srv/data/other/a', os.O_WRONLY)\"",
            rt,
        ).allowed


# ── 显式授权目录（#821 user_roots）不得被误拦 ───────────────────────────


class TestAuthorizedRootsAllowed:
    def test_write_spellings_into_authorized_root_allowed(self, tmp_path):
        rt = sandbox_rt(tmp_path, roots=(AUTH,))
        payloads = [
            f"from pathlib import Path; Path('{AUTH}/a.txt').write_text('x')",
            f"from pathlib import Path; Path('{AUTH}/a.txt').write_bytes(b'x')",
            f"open('{AUTH}/a.txt','w').write('x')",
            f"open('{AUTH}/sub/a.txt','wb').write(b'x')",
            f"import os; os.makedirs('{AUTH}/assets')",
            f"import os; os.mkdir('{AUTH}/d')",
            f"import shutil; shutil.copy('/etc/hosts','{AUTH}/a.txt')",
            f"import shutil; shutil.copy2('/etc/hosts','{AUTH}/a.txt')",
            f"import shutil; shutil.move('{AUTH}/a','{AUTH}/b')",
            f"import os; os.rename('{AUTH}/a','{AUTH}/b')",
        ]
        for payload in payloads:
            verdict = v(f'python3 -c "{payload}"', rt)
            assert verdict.allowed, payload

    def test_authorized_root_itself_allowed(self, tmp_path):
        """Equality counts for WRITE-kind targets: the file tools accept the
        root itself (``Path.relative_to``) and so does the bind."""
        rt = sandbox_rt(tmp_path, roots=(AUTH,))
        assert v(f"python3 -c \"import os; os.mkdir('{AUTH}')\"", rt).allowed

    def test_authorized_root_itself_not_removable(self, tmp_path):
        """The grant is "deliver here", not "delete my folder": a delete /
        move-SOURCE at the root itself is refused, inside it is fine."""
        rt = sandbox_rt(tmp_path, roots=(AUTH,))
        for cmd in (
            f"rm -rf {AUTH}",
            f"python3 -c \"import shutil; shutil.rmtree('{AUTH}')\"",
            f"python3 -c \"import shutil; shutil.move('{AUTH}','./moved')\"",
            f"python3 -c \"import os; os.rename('{AUTH}','./moved')\"",
        ):
            verdict = v(cmd, rt)
            assert not verdict.allowed, cmd
            assert verdict.reason_code == "authorized_root", cmd
        # contents are mutable
        assert v(f"python3 -c \"open('{AUTH}/a.txt','w').write('x')\"", rt).allowed
        assert v(f"rm -rf {AUTH}/sub", rt).allowed

    def test_sibling_still_denied(self, tmp_path):
        rt = sandbox_rt(tmp_path, roots=(AUTH,))
        verdict = v(
            f'python3 -c "from pathlib import Path; '
            f"Path('{SIBLING}/a.txt').write_text('x')\"",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "outside_workspace"

    def test_system_path_beats_authorized_root(self, tmp_path):
        """Defense in depth: a grant covering a system path still loses."""
        rt = sandbox_rt(tmp_path, roots=("/etc",))
        verdict = v(
            "python3 -c \"from pathlib import Path; "
            "Path('/etc/guard984/x').write_text('x')\"",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "system_path"

    def test_host_home_and_config_home_beats_authorized_root(self, tmp_path):
        """POSIX-native paths reach the host home / config home directly in
        the sandbox (no /mnt/<drive> mapping) — ``_is_system_path`` still
        wins over a per-call grant."""
        rt = sandbox_rt(tmp_path, roots=("/srv/home/.ssh", "/srv/home/.miqi"))
        rt = RuntimePaths(
            **{**rt.__dict__, "host_home": "/srv/home",
               "miqi_home": "/srv/home/.miqi"}
        )
        for target in ("/srv/home/.ssh/id_rsa", "/srv/home/.miqi/config.json"):
            verdict = v(
                f'python3 -c "open(\'{target}\',\'w\').write(\'x\')"', rt,
            )
            assert not verdict.allowed, target
            assert verdict.reason_code == "system_path", target

    def test_authorized_heredoc_delivery_allowed(self, tmp_path):
        """The delivery shape the issue says must not break: a heredoc
        writing into the directory the user asked for."""
        rt = sandbox_rt(tmp_path, roots=(AUTH,))
        verdict = v(
            "python3 - <<'PYEOF'\n"
            "from pathlib import Path\n"
            f"Path('{AUTH}/report.md').write_text('ok')\n"
            "PYEOF",
            rt,
        )
        assert verdict.allowed

    def test_session_scope_still_allowed(self, tmp_path):
        rt = sandbox_rt(tmp_path)
        assert v("python3 -c \"open('./out/a.txt','w').write('x')\"", rt).allowed
        assert v(
            "python3 -c \"open('/home/miqi/workspace/a.txt','w').write('x')\"",
            rt,
        ).allowed
        assert v("python3 -c \"open('/tmp/a.txt','w').write('x')\"", rt).allowed


# ── heredoc 正文保真（引号不被 tokenizer 吃掉） ─────────────────────────


class TestHeredocFidelity:
    def test_heredoc_path_literals_are_classified_not_uncertain(self, tmp_path):
        """Before the raw-body read, the payload was rebuilt from tokens
        (quotes stripped) → no literals → blanket ``script_uncertain``."""
        rt = sandbox_rt(tmp_path)
        verdict = v(
            "python3 - <<'PYEOF'\n"
            "import shutil; shutil.rmtree('/etc/x')\n"
            "PYEOF",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "system_path"

    def test_heredoc_without_literals_still_uncertain(self, tmp_path):
        rt = sandbox_rt(tmp_path)
        verdict = v(
            "python3 - <<'PYEOF'\n"
            "import shutil, sys; shutil.rmtree(sys.argv[1])\n"
            "PYEOF",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "script_uncertain"

    def test_heredoc_double_quoted_delimiter(self, tmp_path):
        rt = sandbox_rt(tmp_path)
        verdict = v(
            'python3 - <<"PYEOF"\n'
            f'open("{SIBLING}/a.txt","w").write("x")\n'
            "PYEOF",
            rt,
        )
        assert not verdict.allowed
        assert verdict.reason_code == "outside_workspace"

    def test_nested_shell_python_write_denied(self, tmp_path):
        """``bash -c "python3 -c ..."`` must not evade the vocabulary one
        level down (the nested launcher payload is checked too)."""
        rt = sandbox_rt(tmp_path)
        cmd = (
            "bash -c \"python3 -c \\\"open('"
            f"{SIBLING}/a.txt','w').write('x')\\\"\""
        )
        verdict = v(cmd, rt)
        assert not verdict.allowed, cmd
        assert verdict.reason_code == "outside_workspace"
        # an in-scope nested write stays allowed
        ok_cmd = "bash -c \"python3 -c \\\"open('./out.txt','w').write('x')\\\"\""
        assert v(ok_cmd, rt).allowed
        # nested READ stays allowed (fail-open)
        read_cmd = "bash -c \"python3 -c \\\"print(open('/etc/hosts').read())\\\"\""
        assert v(read_cmd, rt).allowed


# ── ExecTool 接线：per-call _user_roots 进入 guard 的授权范围 ─────────────


class TestExecToolPlumbing:
    def test_guard_write_roots_tracks_allow_user_dirs(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        on = ExecTool(working_dir=str(tmp_path), allow_user_dirs=True)
        off = ExecTool(working_dir=str(tmp_path), allow_user_dirs=False)
        assert on._guard_write_roots([str(out)]) == (os.path.normpath(str(out)),)
        assert off._guard_write_roots([str(out)]) == ()

    def test_guard_write_roots_drops_non_absolute(self, tmp_path):
        tool = ExecTool(working_dir=str(tmp_path))
        assert tool._guard_write_roots(["relative/dir", "", None, 42]) == ()

    def test_guard_write_roots_resolves_spelling(self, tmp_path):
        """The classifier resolves every operand; an unresolved spelling
        (``..`` / trailing separator / 8.3 / symlink) must still match."""
        out = tmp_path / "out"
        out.mkdir()
        weird = f"{out}{os.sep}..{os.sep}{out.name}{os.sep}"
        tool = ExecTool(working_dir=str(tmp_path))
        assert tool._guard_write_roots([weird]) == (str(out.resolve()),)

    def test_static_shared_roots_are_not_a_guard_scope(self, tmp_path):
        """Documented asymmetry: ``tools.extra_roots`` feeds the file tools
        and the layer-1 bind, but the exec static guard keeps its
        session-scope rule (the static set contains the WORKSPACE root, so
        adopting it wholesale would make Level 1 writable)."""
        ws = tmp_path / "ws"
        ws.mkdir()
        extra = tmp_path / "extra"
        extra.mkdir()
        tool = ExecTool(working_dir=str(ws), shared_roots=[ws, extra])
        assert tool._guard_write_roots(None) == ()
        assert str(extra) in tool._exec_rw_binds(None)
        # the same dir DOES become a scope when the user mentions it
        assert tool._guard_write_roots([str(extra)]) == (str(extra.resolve()),)

    def test_runtime_paths_carry_user_roots(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        sess = tmp_path / "ws" / "sessions" / "k" / "files"
        sess.mkdir(parents=True)
        tool = ExecTool(working_dir=str(sess))
        rt = tool._guard_runtime_paths(str(sess), False, [str(out)])
        assert rt.extra_write_roots == (os.path.normpath(str(out)),)
        assert tool._guard_runtime_paths(str(sess), False).extra_write_roots == ()

    def test_guard_command_honours_user_roots(self, tmp_path):
        out = tmp_path / "out"
        out.mkdir()
        sess = tmp_path / "ws" / "sessions" / "k" / "files"
        sess.mkdir(parents=True)
        target = _posix(out / "report.md")
        cmd = (
            "python3 -c \"from pathlib import Path; "
            f"Path('{target}').write_text('x')\""
        )
        tool = ExecTool(working_dir=str(sess))
        assert tool._guard_command(cmd, str(sess), user_roots=[str(out)]) is None
        assert tool._guard_command(cmd, str(sess)) is not None
        # the switch that gates the bind gates the guard too
        gated = ExecTool(working_dir=str(sess), allow_user_dirs=False)
        assert gated._guard_command(cmd, str(sess), user_roots=[str(out)]) is not None

    def test_guard_write_roots_matches_bind_set(self, tmp_path):
        """Same condition as layer 1: a root is writable in the guard iff
        it is bound rw in the sandbox."""
        out = tmp_path / "out"
        out.mkdir()
        ws = tmp_path / "ws"
        ws.mkdir()
        for allow in (True, False):
            tool = ExecTool(
                working_dir=str(ws), shared_roots=[], allow_user_dirs=allow,
            )
            binds = tool._exec_rw_binds([str(out)])
            roots = tool._guard_write_roots([str(out)])
            assert (str(out) in binds) == (bool(roots)), allow


class TestRegistryChokePoint:
    """``ToolRegistry.execute`` is the funnel for callers that skip the
    orchestrator (legacy SubagentManager): a MODEL-authored ``_user_roots``
    must not survive there — only the harness ``**extra`` channel may
    carry it (R4 review F3)."""

    async def test_registry_strips_model_user_roots(self, tmp_path):
        from miqi.agent.tools.registry import ToolRegistry

        seen: dict = {}

        class _Recorder:
            name = "exec"
            execution_timeout = None

            def validate_params(self, params):
                return []

            async def execute(self, **kwargs):
                seen.update(kwargs)
                return "ok"

        registry = ToolRegistry()
        registry.register(_Recorder())

        await registry.execute(
            "exec", {"command": "echo x", "_user_roots": [str(tmp_path)]},
        )
        assert "_user_roots" not in seen

        await registry.execute(
            "exec", {"command": "echo x"}, _user_roots=[str(tmp_path)],
        )
        assert seen["_user_roots"] == [str(tmp_path)]
