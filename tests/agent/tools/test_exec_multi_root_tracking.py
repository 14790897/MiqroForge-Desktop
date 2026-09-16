"""#1104: exec 产物追踪扩展到 cwd 之外的输出目录。

用户反馈：skill 把产物写到 ``--out-dir`` 指向的用户目录（工作区外）时，
「任务资产」面板只显示零头。根因：快照只覆盖 exec cwd。修复后快照根 =
cwd + 用户点名目录（#821 ``_user_roots``）+ 命令里声明的 ``--out-dir``。
"""

import json
from pathlib import Path

import pytest

from miqi.agent.tools.shell import ExecTool


@pytest.fixture
def exec_tool() -> ExecTool:
    tool = ExecTool()
    tool.working_dir = None
    return tool


@pytest.fixture
def fake_workspace(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()

    def _fake_workspace_path():
        return str(root)

    monkeypatch.setattr(
        "miqi.runtime.file_handlers._get_workspace_path", _fake_workspace_path,
    )
    return root


def test_snapshot_roots_include_user_roots_and_out_dir_flags(exec_tool, tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    user_dir = tmp_path / "Desktop" / "BVSE_MOF相关"
    user_dir.mkdir(parents=True)
    out_dir = tmp_path / "outdir"
    out_dir.mkdir()
    rel_out = cwd / "rel_out"
    rel_out.mkdir()

    roots = exec_tool._snapshot_roots(
        cwd,
        [str(user_dir), str(user_dir)],  # 去重
        f'python pipeline.py in.cif --out-dir "{out_dir}" --low-e-max 0.5',
    )
    assert roots[0] == cwd
    assert user_dir in roots
    assert out_dir in roots
    assert roots.count(user_dir) == 1

    # 相对 out-dir 按 cwd 解析；--out-dir= 等号形式同样识别
    roots2 = exec_tool._snapshot_roots(cwd, [], "run --outdir=rel_out")
    assert rel_out in roots2

    # 不存在的目录也保留（out-dir 运行时才创建）——空快照兜底，见下个用例；
    # 无 out-dir 命令只留 cwd + 用户根
    roots3 = exec_tool._snapshot_roots(cwd, [str(tmp_path / "nope")], "echo hi")
    assert roots3 == [cwd, tmp_path / "nope"]


@pytest.mark.asyncio
async def test_nonexistent_out_dir_is_tracked_after_creation(
    exec_tool, fake_workspace, tmp_path,
):
    """#1104 关键回归：out-dir 运行前不存在时也必须能 diff 到产物。

    修复前 `_snapshot_roots` 用 ``is_dir()`` 过滤掉了尚未创建的 out-dir，
    运行后新建的整棵产物树因此全都进不了台账（用户实测 33 个产物只剩 1 个）。
    """
    cwd = fake_workspace
    out_dir = tmp_path / "not_yet_created"

    roots = exec_tool._snapshot_roots(cwd, [], f'run --out-dir "{out_dir}"')
    assert out_dir in roots
    before = exec_tool._snapshot_roots_map(roots)
    assert before[str(out_dir)] == {}, "不存在的根要有空快照兜底，不能是 None"

    (out_dir / "analysis").mkdir(parents=True)
    (out_dir / "analysis" / "report.json").write_text("{}", encoding="utf-8")
    (out_dir / "summary.json").write_text("{}", encoding="utf-8")

    await exec_tool._track_workspace_changes_multi(before, "desktop:1104new", roots)

    tracked = json.loads(
        (fake_workspace / "sessions" / "desktop_1104new" / "tracked_files.json")
        .read_text(encoding="utf-8")
    )["files"]
    assert any(k.endswith("analysis/report.json") for k in tracked)
    assert any(k.endswith("summary.json") for k in tracked)


def test_out_dir_survives_root_truncation(exec_tool, tmp_path):
    """用户根很多导致截断时，命令声明的 out-dir 必须存活（CodeRabbit 复审）。

    截断按加入顺序取前 N 个；out-dir 若排在用户根之后就会被丢掉，
    产物又回到「diff 不到」的状态。
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    out_dir = tmp_path / "declared_out"
    out_dir.mkdir()
    user_roots = []
    for i in range(12):
        d = tmp_path / f"u{i}"
        d.mkdir()
        user_roots.append(str(d))

    roots = exec_tool._snapshot_roots(cwd, user_roots, f'run --out-dir "{out_dir}"')
    assert len(roots) <= exec_tool._MAX_SNAPSHOT_ROOTS
    assert out_dir in roots, f"out-dir 被截断丢了：{roots}"


@pytest.mark.asyncio
async def test_multi_root_tracking_captures_files_outside_cwd(
    exec_tool, fake_workspace, tmp_path,
):
    """用户目录里新产出的报告要进台账（修复前只在 cwd 内 diff，看不到）。"""
    cwd = fake_workspace
    user_dir = tmp_path / "user_out"
    user_dir.mkdir()

    roots = exec_tool._snapshot_roots(cwd, [str(user_dir)], "run")
    before = exec_tool._snapshot_roots_map(roots)

    # 子进程在用户目录里产出文件（模拟 skill 写 --out-dir）
    report = user_dir / "ZECKID_Na_report.md"
    report.write_text("# report", encoding="utf-8")
    (user_dir / "analysis").mkdir()
    (user_dir / "analysis" / "Na_bvse_analysis.json").write_text("{}", encoding="utf-8")

    await exec_tool._track_workspace_changes_multi(before, "desktop:1104roots", roots)

    tracked = json.loads(
        (fake_workspace / "sessions" / "desktop_1104roots" / "tracked_files.json")
        .read_text(encoding="utf-8")
    )["files"]
    assert any(k.endswith("ZECKID_Na_report.md") for k in tracked)
    assert any(k.endswith("Na_bvse_analysis.json") for k in tracked)
    assert all(v["op"] == "write" for v in tracked.values())


@pytest.mark.asyncio
async def test_oversized_root_does_not_disable_cwd_tracking(
    exec_tool, fake_workspace, tmp_path, monkeypatch,
):
    """某个根快照超限被跳过时，其它根照常追踪（修复前整体禁用）。"""
    cwd = fake_workspace
    big = tmp_path / "big_user_dir"
    big.mkdir()
    roots = [cwd, big]

    real_snapshot = exec_tool._snapshot_workspace

    def _snapshot(root=None):
        if root is not None and Path(root) == big:
            return None  # 超限/不可用
        return real_snapshot(root)

    monkeypatch.setattr(exec_tool, "_snapshot_workspace", _snapshot)
    before = exec_tool._snapshot_roots_map(roots)
    assert before[str(big)] is None

    (cwd / "cwd_artifact.md").write_text("x", encoding="utf-8")
    await exec_tool._track_workspace_changes_multi(before, "desktop:1104big", roots)

    tracked = json.loads(
        (fake_workspace / "sessions" / "desktop_1104big" / "tracked_files.json")
        .read_text(encoding="utf-8")
    )["files"]
    assert any(k.endswith("cwd_artifact.md") for k in tracked)
