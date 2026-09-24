"""Unit tests for sandbox workspace bind-mount behavior (#PR).

Custom workspace → bind-mount the user's project dir into /home/miqi/workspace
so exec and file tools operate on the SAME directory.
Default workspace → keep the per-session private sandbox dir.
"""
from pathlib import Path

from miqi.sandbox.bwrap import BwrapSandbox


def _make_sandbox(workspace: Path, *, custom: bool) -> BwrapSandbox:
    sb = BwrapSandbox(
        session_key="desktop:test123",
        workspace=workspace,
        sandbox_base_dir=Path("/tmp/miqi-sandboxes"),
        wsl_distro="Ubuntu",
        wsl_base_dir="/tmp/miqi-sandboxes",
    )
    sb._bwrap_path = "/usr/bin/bwrap"
    # Simulate start(): custom workspace resolves to a Linux path that gets
    # bind-mounted; default leaves _linux_workspace None.
    if custom:
        sb._linux_workspace = str(workspace).replace("\\", "/")
    else:
        sb._linux_workspace = None
    return sb


def _bind_mounts(args: list[str]) -> list[tuple[str, str]]:
    """Extract (src, dest) pairs from the flat bwrap arg list."""
    pairs = []
    for i, a in enumerate(args[:-1]):
        if a == "--bind":
            pairs.append((args[i + 1], args[i + 2]))
    return pairs


def test_default_workspace_binds_private_sandbox_dir():
    sb = _make_sandbox(Path("/home/user/.forge/workspace"), custom=False)
    args = sb._build_bwrap_args("echo hi")
    mounts = _bind_mounts(args)
    # Should bind the private sandbox_workspace, NOT any host path.
    assert (sb.sandbox_workspace, "/home/miqi/workspace") in mounts


def test_custom_workspace_binds_real_project_dir():
    sb = _make_sandbox(Path("/mnt/c/git-program/auto_display_light"), custom=True)
    args = sb._build_bwrap_args("echo hi")
    mounts = _bind_mounts(args)
    # Should bind the real custom workspace into /home/miqi/workspace.
    assert ("/mnt/c/git-program/auto_display_light", "/home/miqi/workspace") in mounts
