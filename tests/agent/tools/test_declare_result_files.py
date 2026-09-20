"""#1104: declare_result_files 工具 —— agent 显式声明结果文件。

背景：面板「结果/过程」分区此前是纯前端扩展名白名单，skill 产出的
``*_report.md`` 必然落「过程文件」。本工具把 agent 点名的交付物写入会话
``tracked_files.json`` 的 ``result: true`` 标记，面板据此归入「结果文件」。

覆盖：新条目创建（op=write）、既有条目按绝对路径解析去重（保留原 op）、
missing 上报不阻断、重复入参去重、面板读端可见。
"""

import json
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import _session_files_dir_key
from miqi.agent.tools.result_files import DeclareResultFilesTool


def _default_ws() -> Path:
    from miqi.paths import get_miqi_home

    ws = Path(get_miqi_home()) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _session_files_dir(ws: Path, session_key: str) -> Path:
    d = ws / "sessions" / _session_files_dir_key(session_key) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_path(store_root: Path, session_key: str) -> Path:
    return store_root / "sessions" / _session_files_dir_key(session_key) / "tracked_files.json"


def _read_tracked(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("files", {})


def _panel_tracked(store_root: Path, session_key: str) -> dict:
    from miqi.session.manager import SessionManager

    return SessionManager(store_root).load_tracked_files(session_key)


def _tool(ws: Path, files_dir: Path) -> DeclareResultFilesTool:
    return DeclareResultFilesTool(
        workspace=files_dir,
        allowed_dir=files_dir,
        base_workspace=ws,
    )


@pytest.mark.asyncio
async def test_declare_creates_result_entry_and_panel_reads_it():
    ws = _default_ws()
    key = "desktop:1104tool"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "run" / "ZECKID_Na_report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# report", encoding="utf-8")

    result = await _tool(ws, files_dir).execute(
        paths=[str(report)], note="BVSE 报告", _session_key=key,
    )
    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["marked"] == 1

    tracked = _read_tracked(_store_path(ws, key))
    entry = tracked[str(report).replace("\\", "/")]
    assert entry["op"] == "write"
    assert entry["result"] is True
    assert entry["name"] == "ZECKID_Na_report.md"

    # 面板读端（sessions.get_tracked_files 同一回路）必须带 result 标记
    panel = _panel_tracked(ws, key)
    assert panel[str(report).replace("\\", "/")]["result"] is True


@pytest.mark.asyncio
async def test_declare_marks_existing_entry_without_duplicating():
    """同一文件既有相对条目（exec/文件工具口径）时，声明只打标不新增重复条目。"""
    from miqi.session.manager import SessionManager

    ws = _default_ws()
    key = "desktop:1104dedupe"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "run" / "summary.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("x", encoding="utf-8")

    # 预置：文件工具口径的相对条目（相对会话 files 目录），op=read
    sm = SessionManager(ws)
    sm.save_tracked_file(key, "run/summary.md", op="read")

    result = await _tool(ws, files_dir).execute(paths=[str(report)], _session_key=key)
    assert json.loads(result)["ok"] is True

    tracked = _read_tracked(_store_path(ws, key))
    assert set(tracked) == {"run/summary.md"}, f"出现重复条目：{sorted(tracked)}"
    assert tracked["run/summary.md"]["result"] is True
    assert tracked["run/summary.md"]["op"] == "read", "声明不得改写既有 op"


@pytest.mark.asyncio
async def test_declare_matches_same_file_in_another_path_form():
    """同一文件的另一种路径形态（`sub/..`、Windows 8.3 短名）不得产生重复条目。

    真实技能链路的坑：exec 快照存 8.3 短名（`INTERS~1`），声明用规范长名 →
    打标落到新建的重复条目上，原条目 `result` 依旧缺失，面板/断言都看不到。
    """
    from miqi.session.manager import SessionManager

    ws = _default_ws()
    key = "desktop:1104form"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "run" / "r.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("x", encoding="utf-8")

    sm = SessionManager(ws)
    plain = str(report).replace("\\", "/")
    sm.save_tracked_file(key, plain, op="write")

    odd_form = str(files_dir / "run" / ".." / "run" / "r.md")
    payload = json.loads(
        await _tool(ws, files_dir).execute(paths=[odd_form], _session_key=key)
    )
    assert payload["ok"] is True

    tracked = _read_tracked(_store_path(ws, key))
    assert set(tracked) == {plain}, f"出现重复条目：{sorted(tracked)}"
    assert tracked[plain]["result"] is True


@pytest.mark.asyncio
async def test_declare_normalizes_workspace_base_prefixed_path():
    """#1131 复现：`sessions/<key>/files/<name>` 不得在会话 files 根下再叠一层。

    agent 常按**工作区基准**写路径，而会话工具的 workspace 已经是会话 files
    根；朴素拼接会得到 ``<files>/sessions/<key>/files/<name>`` —— 磁盘上不
    存在。后果：「定位」报 File not found，且台账里多出一条重复前缀的条目
    （与既有裸文件名条目 canonical 形态不同，去重匹配不上），永不自动清理。
    """
    from miqi.session.manager import SessionManager

    ws = _default_ws()
    key = "desktop:1131prefix"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "MiQroForge_文件生成演示.pdf"
    report.write_text("x", encoding="utf-8")

    # 预置：文档工具口径的裸文件名条目
    sm = SessionManager(ws)
    sm.save_tracked_file(key, report.name, op="write")

    declared = f"sessions/{_session_files_dir_key(key)}/files/{report.name}"
    payload = json.loads(
        await _tool(ws, files_dir).execute(paths=[declared], _session_key=key)
    )

    assert payload["ok"] is True
    assert "missing" not in payload, (
        f"前缀被重复拼接，解析到磁盘上不存在的路径：{payload.get('missing')}"
    )
    tracked = _read_tracked(_store_path(ws, key))
    assert set(tracked) == {report.name}, f"出现重复条目：{sorted(tracked)}"
    assert tracked[report.name]["result"] is True


@pytest.mark.asyncio
async def test_declare_normalizes_backslash_prefixed_path():
    """Windows 分隔符形态的 `sessions\\<key>\\files\\<name>` 也要认得出来。

    反斜杠在 POSIX 上是普通文件名字符，`Path("sessions\\<key>\\files\\x")`
    在那里只有**一个**组件。判定会话前缀之前不做分隔符归一，规则就看不到
    这个前缀、退回朴素拼接，文件被解析到一个带字面反斜杠的假路径上 ——
    同一个 #1131 缺陷的另一副面孔：收敛了规则、却漏了规则的入参归一。
    """
    from miqi.session.manager import SessionManager

    ws = _default_ws()
    key = "desktop:1131backslash"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "报告.pdf"
    report.write_text("x", encoding="utf-8")

    sm = SessionManager(ws)
    sm.save_tracked_file(key, report.name, op="write")

    declared = f"sessions\\{_session_files_dir_key(key)}\\files\\{report.name}"
    payload = json.loads(
        await _tool(ws, files_dir).execute(paths=[declared], _session_key=key)
    )

    assert payload["ok"] is True
    assert "missing" not in payload, (
        f"反斜杠路径被当成单个文件名，解析到不存在的路径：{payload.get('missing')}"
    )
    tracked = _read_tracked(_store_path(ws, key))
    assert set(tracked) == {report.name}, f"出现重复条目：{sorted(tracked)}"


@pytest.mark.asyncio
async def test_declare_dedupes_same_file_across_path_forms():
    """同一次调用里同一文件的两种形态（`run/r.md` 与 `run/../run/r.md`）只登记一条。"""
    ws = _default_ws()
    key = "desktop:1104dupform"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "run" / "r.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("x", encoding="utf-8")

    odd_form = str(files_dir / "run" / ".." / "run" / "r.md")
    payload = json.loads(
        await _tool(ws, files_dir).execute(
            paths=[str(report), odd_form], _session_key=key
        )
    )
    assert payload["ok"] is True
    tracked = _read_tracked(_store_path(ws, key))
    assert len(tracked) == 1, f"同一文件出现多条：{sorted(tracked)}"
    assert next(iter(tracked.values()))["result"] is True


@pytest.mark.asyncio
async def test_declare_reports_missing_but_still_marks():
    ws = _default_ws()
    key = "desktop:1104missing"
    files_dir = _session_files_dir(ws, key)
    ghost = files_dir / "not_there.md"

    payload = json.loads(
        await _tool(ws, files_dir).execute(paths=[str(ghost)], _session_key=key)
    )
    assert payload["ok"] is True
    assert payload["missing"] == [str(ghost).replace("\\", "/")]
    assert payload["marked"] == 1
    assert _read_tracked(_store_path(ws, key))[str(ghost).replace("\\", "/")]["result"] is True


@pytest.mark.asyncio
async def test_declare_dedupes_input_paths():
    ws = _default_ws()
    key = "desktop:1104dup"
    files_dir = _session_files_dir(ws, key)
    report = files_dir / "a.md"
    report.write_text("x", encoding="utf-8")

    payload = json.loads(
        await _tool(ws, files_dir).execute(
            paths=[str(report), str(report).replace("/", "\\"), str(report)],
            _session_key=key,
        )
    )
    assert payload["declared"] == [str(report).replace("\\", "/")]
    assert len(_read_tracked(_store_path(ws, key))) == 1


@pytest.mark.asyncio
async def test_declare_accepts_single_string_and_empty_list():
    ws = _default_ws()
    key = "desktop:1104misc"
    files_dir = _session_files_dir(ws, key)
    f = files_dir / "b.md"
    f.write_text("x", encoding="utf-8")
    tool = _tool(ws, files_dir)

    payload = json.loads(await tool.execute(paths=str(f), _session_key=key))
    assert payload["ok"] is True and payload["marked"] == 1

    empty = json.loads(await tool.execute(paths=[], _session_key=key))
    assert empty["ok"] is False
