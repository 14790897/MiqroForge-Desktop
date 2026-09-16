"""#983: 文档工具 tracked 条目写入孤儿路径 —— 存储根解耦（修复 A）与
store key 派生统一（修复 B）。

根因：``create_pdf/docx/pptx/xlsx`` 注册时 ``workspace=<ws>/sessions/<key>/files``，
``_persist_tracked_file`` 把该目录当仓库根构造 ``SessionManager`` →
条目落 ``<files>/sessions/<key>/tracked_files.json``（孤儿），
面板读 ``<ws>/sessions/<key>/tracked_files.json`` 永远看不到。

前置（方案 §3 L1）：会话 files 目录必须建在 ``MIQI_HOME/workspace`` 之下，
否则修复 A 的 fail-closed 守卫（``base != 默认根`` → 不剥）不触发，
精确路径断言会恒红而不是给出有效信号。故统一走 ``_default_ws()``。

修复 A 只剥「默认工作区下的会话 files 目录」；自定义工作区、key 不匹配、
``session_key=None`` 一律不剥（反例逐字不变）。修复 B 让 store key 与
目录名派生同源（``_session_files_dir_key``），非 desktop 渠道键
（``cli:other``）不再落错会话目录；同一条派生规则也用在 shell.py 的 exec
批量追踪（``_persist_changed_batch``）上，故 ``cli:direct`` 的 exec 产物与
文档产物落同一会话目录。
"""

import importlib
import json
from pathlib import Path

import pytest

from miqi.agent.tools.filesystem import _session_files_dir_key

# ── Helpers ────────────────────────────────────────────────────────────────


def _default_ws() -> Path:
    """``MIQI_HOME/workspace`` — 修复 A 的判别根（必须显式 mkdir）。"""
    from miqi.paths import get_miqi_home

    ws = Path(get_miqi_home()) / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _session_files_dir(ws: Path, session_key: str) -> Path:
    """``<ws>/sessions/<derived key>/files`` — 文档工具的注册工作区。"""
    d = ws / "sessions" / _session_files_dir_key(session_key) / "files"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_path(store_root: Path, session_key: str) -> Path:
    """``<store_root>/sessions/<derived key>/tracked_files.json``。"""
    return store_root / "sessions" / _session_files_dir_key(session_key) / "tracked_files.json"


def _read_tracked(path: Path) -> dict:
    assert path.exists(), f"tracked_files.json 不存在：{path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("version") == 1
    return data.get("files", {})


def _panel_tracked(store_root: Path, session_key: str) -> dict:
    """面板读取回路：与 ``sessions.get_tracked_files`` 同一条读端
    （``SessionManager(workspace).load_tracked_files(session_key)``）。"""
    from miqi.session.manager import SessionManager

    return SessionManager(store_root).load_tracked_files(session_key)


def _tool(dotted: str, **kwargs):
    module_name, cls_name = dotted.split(":")
    return getattr(importlib.import_module(module_name), cls_name)(**kwargs)


# ── 正例：4 个 Create* 工具 ────────────────────────────────────────────────

_CREATE_CASES = [
    pytest.param(
        "miqi.documents.pdf_create_tool:CreatePdfTool",
        {"filename": "report.pdf", "title": "Title", "content": "body"},
        "report.pdf",
        id="create_pdf",
    ),
    pytest.param(
        "miqi.documents.docx_tool:CreateDocxTool",
        {"filename": "report.docx", "title": "Title", "paragraphs": ["body"]},
        "report.docx",
        id="create_docx",
    ),
    pytest.param(
        "miqi.documents.pptx_tool:CreatePptxTool",
        {"filename": "deck.pptx", "slides": [{"title": "Cover"}]},
        "deck.pptx",
        id="create_pptx",
    ),
    pytest.param(
        "miqi.documents.xlsx_tool:CreateXlsxTool",
        {"filename": "book.xlsx", "rows": [["A", "B"], [1, 2]]},
        "book.xlsx",
        id="create_xlsx",
    ),
]


@pytest.mark.parametrize("tool_dotted, kwargs, filename", _CREATE_CASES)
@pytest.mark.asyncio
async def test_create_tools_track_into_session_store_root(tool_dotted, kwargs, filename):
    """Create* 工具的条目必须落会话存储根，而不是会话 files 目录下的孤儿路径。"""
    ws = _default_ws()
    key = "desktop:983create"
    files_dir = _session_files_dir(ws, key)
    tool = _tool(tool_dotted, workspace=files_dir, allowed_dir=files_dir)

    result = await tool.execute(_session_key=key, **kwargs)
    assert "Created:" in result, result

    tracked = _read_tracked(_store_path(ws, key))
    assert filename in tracked, f"条目未落会话存储根：{sorted(tracked)}"
    assert tracked[filename]["op"] == "write"
    assert tracked[filename]["name"] == filename

    # 孤儿路径（会话 files 目录被当作仓库根）不得再出现
    orphan = _store_path(files_dir, key)
    assert not orphan.exists(), f"条目仍落孤儿路径：{orphan}"

    # 面板读取回路：走与 sessions.get_tracked_files 相同的读端
    # （SessionManager(<默认工作区>).load_tracked_files(key)）必须读到该条目。
    assert filename in _panel_tracked(ws, key)


@pytest.mark.asyncio
async def test_append_xlsx_tracks_edit_into_session_store_root():
    """AppendXlsxTool：先用 openpyxl 直接造 xlsx（不经 CreateXlsxTool，
    避免 op 升级干扰）→ append → op=='edit'（miqi/documents/xlsx_tool.py:463）。"""
    from openpyxl import Workbook

    ws = _default_ws()
    key = "desktop:983append"
    files_dir = _session_files_dir(ws, key)
    target = files_dir / "data.xlsx"
    wb = Workbook()
    wb.active.title = "Data"
    wb.active.append(["A", "B"])
    wb.save(str(target))

    tool = _tool("miqi.documents.xlsx_tool:AppendXlsxTool",
                 workspace=files_dir, allowed_dir=files_dir)
    result = await tool.execute(
        filename="data.xlsx", sheet_name="Data", rows=[[1, 2]], _session_key=key,
    )
    assert "Appended:" in result, result

    tracked = _read_tracked(_store_path(ws, key))
    assert tracked["data.xlsx"]["op"] == "edit"
    assert not _store_path(files_dir, key).exists()


@pytest.mark.asyncio
async def test_create_then_append_keeps_write_op():
    """op 升级口径：跨调用 create→append 不降级（manager.py:417-420
    read<edit<write<delete），仍为 write。"""
    ws = _default_ws()
    key = "desktop:983upgrade"
    files_dir = _session_files_dir(ws, key)

    create = _tool("miqi.documents.xlsx_tool:CreateXlsxTool",
                   workspace=files_dir, allowed_dir=files_dir)
    assert "Created:" in await create.execute(
        filename="up.xlsx", rows=[["A"]], _session_key=key,
    )
    append = _tool("miqi.documents.xlsx_tool:AppendXlsxTool",
                   workspace=files_dir, allowed_dir=files_dir)
    assert "Appended:" in await append.execute(
        filename="up.xlsx", rows=[[1]], _session_key=key,
    )

    tracked = _read_tracked(_store_path(ws, key))
    assert tracked["up.xlsx"]["op"] == "write"


# ── 反例：自定义工作区（真实生产形态）────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_workspace_is_not_stripped(fake_config, tmp_path):
    """真实 custom workspace（sijie-Z 发现 1）：走 ``create_runtime_tool_registry()``
    的生产调用链，而不是手工拼 ``sessions/<key>/files`` 形状。

    非默认工作区不启用 per-session files 隔离（``tool_registry_factory.py:310-316``）：
    ``_write_workspace = workspace``，文档工具的 workspace 就是用户选定的 custom
    根。此时 ``_tracked_store_root`` 不剥（fail-closed 反例语义保留），条目落
    ``<custom>/sessions/<key>/tracked_files.json``；面板读端同根同 key。
    """
    from miqi.runtime.tool_registry_factory import create_runtime_tool_registry
    from miqi.session.manager import SessionManager

    custom = tmp_path / "project"
    custom.mkdir()
    key = "desktop:983custom"
    # 生产：工作区选择器把选中目录写进 config.agents.defaults.workspace
    # （apps/desktop/src/main/ipc/index.ts CONFIG_WRITE_INITIAL）→ 面板读端根 = custom
    fake_config.agents.defaults.workspace = str(custom)

    registry = create_runtime_tool_registry(
        config=fake_config, workspace=custom, session_id=key,
    )
    tool = registry.get("create_docx")
    assert tool is not None
    # 生产形态：custom 下不嵌套 sessions/<key>/files
    assert tool._workspace == custom

    result = await tool.execute(filename="custom.docx", title="T", _session_key=key)
    assert "Created:" in result, result

    # tracked 存储根 == 工具 workspace == 面板读端根（同一个根，未被剥回默认根）
    store_root = tool._workspace
    tracked = _read_tracked(_store_path(store_root, key))
    assert "custom.docx" in tracked
    assert "custom.docx" in SessionManager(store_root).load_tracked_files(key)
    # 面板读端根独立取一次：sessions.get_tracked_files → SessionManager(
    # config.workspace_path)（session_handlers.py:32-41）——若 registry 的
    # workspace 与 config 派生的读端根分叉，这里会红。
    panel_root = fake_config.workspace_path
    assert panel_root == custom
    assert "custom.docx" in SessionManager(panel_root).load_tracked_files(key)

    # fail-closed 反例语义保留：默认工作区下不得出现该会话条目
    from miqi.paths import get_miqi_home

    default_root = Path(get_miqi_home()) / "workspace"
    assert not _store_path(default_root, key).exists()
    # 也不得出现「会话 files 目录被当仓库根」的嵌套孤儿路径
    nested = _store_path(
        store_root / "sessions" / _session_files_dir_key(key) / "files", key,
    )
    assert not nested.exists(), f"条目仍落嵌套孤儿路径：{nested}"


def test_tracked_store_root_guard_matrix(tmp_path):
    """``_tracked_store_root`` 的守卫语义：只剥默认根下的会话 files 目录。"""
    from miqi.agent.tools.filesystem import _tracked_store_root

    ws = _default_ws()
    key = "desktop:983guard"
    files_dir = _session_files_dir(ws, key)

    # 默认根 + 形状匹配 + 目录名匹配 → 剥回默认根
    assert _tracked_store_root(files_dir, key) == ws.resolve()
    # str 入参（docstring 承诺「归一（防 str 入参）」）→ 同样剥离
    assert _tracked_store_root(str(files_dir), key) == ws.resolve()
    # 目录名非本会话派生名 → 不剥
    other = ws / "sessions" / "desktop_other" / "files"
    other.mkdir(parents=True, exist_ok=True)
    assert _tracked_store_root(other, key) == other.resolve()
    # 自定义工作区（base != 默认根）→ 不剥
    custom = tmp_path / "proj" / "sessions" / _session_files_dir_key(key) / "files"
    custom.mkdir(parents=True)
    assert _tracked_store_root(custom, key) == custom.resolve()
    # 形状不符（不是 sessions/<key>/files）→ 不剥
    assert _tracked_store_root(ws, key) == ws.resolve()
    # session_key=None → 不剥
    assert _tracked_store_root(files_dir, None) == files_dir.resolve()
    # session_key='' → 不剥（与 None 同口径）
    assert _tracked_store_root(files_dir, "") == files_dir.resolve()
    # workspace=None → None
    assert _tracked_store_root(None, key) is None


# ── key 形态（修复 B 证据）───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_desktop_channel_key_lands_in_matching_dir():
    """修复 B：store key 与目录名派生同源。``cli:other`` 的旧剥离规则
    （``parts[0] != "desktop"`` → ``other``）会写进 ``sessions/other/``。"""
    ws = _default_ws()
    key = "cli:other"
    files_dir = _session_files_dir(ws, key)  # …/sessions/cli_other/files
    tool = _tool("miqi.documents.docx_tool:CreateDocxTool",
                 workspace=files_dir, allowed_dir=files_dir)

    assert "Created:" in await tool.execute(
        filename="cli.docx", title="T", _session_key=key,
    )

    tracked = _read_tracked(ws / "sessions" / "cli_other" / "tracked_files.json")
    assert "cli.docx" in tracked
    assert not (ws / "sessions" / "other" / "tracked_files.json").exists()


@pytest.mark.asyncio
async def test_namespaced_desktop_key_matches_two_segment_dir():
    """三段 namespaced key（``miqi-desktop:desktop:983namespaced``）剥掉
    client_id 后，与两段 key 派生同一目录名 ``desktop_983namespaced``。"""
    ws = _default_ws()
    key = "miqi-desktop:desktop:983namespaced"
    files_dir = _session_files_dir(ws, key)
    tool = _tool("miqi.documents.docx_tool:CreateDocxTool",
                 workspace=files_dir, allowed_dir=files_dir)

    assert "Created:" in await tool.execute(
        filename="ns.docx", title="T", _session_key=key,
    )

    tracked = _read_tracked(ws / "sessions" / "desktop_983namespaced" / "tracked_files.json")
    assert "ns.docx" in tracked
    assert not _store_path(files_dir, key).exists()


@pytest.mark.parametrize(
    "key, derived",
    [
        pytest.param("desktop:1786807046853", "desktop_1786807046853", id="desktop"),
        pytest.param("cli:direct", "cli_direct", id="cli_direct"),
        pytest.param("cli:other", "cli_other", id="cli_other"),
        pytest.param("gateway:default", "gateway_default", id="gateway_default"),
        pytest.param("miqi-desktop:desktop:1786807046853", "desktop_1786807046853",
                     id="namespaced"),
        pytest.param("thread_nomap", "thread_nomap", id="no_colon"),
    ],
)
def test_session_files_dir_key_is_idempotent(key, derived):
    """不变量：``_session_files_dir_key`` 幂等 —— 把已派生的 key 再喂进来
    返回它本身，因此调用方传原始 key 或已派生 key 都可以
    （``_tracked_store_root`` 的目录名一致性校验依赖这一点）。

    期望表照抄 #1005：两种形态不会派生出第二个会话目录。
    """
    once = _session_files_dir_key(key)
    assert once == derived
    assert _session_files_dir_key(once) == once


# ── exec 批量追踪（shell.py：统一的第 3 个文件）────────────────────────────


@pytest.mark.asyncio
async def test_exec_batch_persist_shares_session_dir_with_doc_tools(monkeypatch):
    """shell.py ``_persist_changed_batch`` 与文档工具同源派生 store key：
    ``cli:direct`` → ``cli_direct``（旧规则给 ``direct``）→ exec 产物与文档产物
    落同一会话目录，面板读端可读到。"""
    from miqi.agent.tools.shell import ExecTool

    ws = _default_ws()
    key = "cli:direct"
    files_dir = _session_files_dir(ws, key)  # …/sessions/cli_direct/files
    monkeypatch.setattr(
        "miqi.runtime.file_handlers._get_workspace_path", lambda: str(ws),
    )

    # 文档产物（同会话）
    doc = _tool("miqi.documents.docx_tool:CreateDocxTool",
                workspace=files_dir, allowed_dir=files_dir)
    assert "Created:" in await doc.execute(
        filename="cli.docx", title="T", _session_key=key,
    )

    # exec 产物（真实调用形态：ExecTool 把会话自己的工作区一并传下去，
    # 写端才能与文档工具同根、同键形）
    exec_tool = ExecTool()
    exec_tool.working_dir = None
    artifact = files_dir / "out.md"
    exec_tool._persist_changed_batch([str(artifact)], key, files_dir)

    # 字面目录名钉住派生：两者同落 sessions/cli_direct/
    tracked = _read_tracked(ws / "sessions" / "cli_direct" / "tracked_files.json")
    assert "cli.docx" in tracked
    # 与文档产物同样的相对 key 形态（工作区相对），不再是绝对路径
    assert "out.md" in tracked
    # 旧剥离规则目录（sessions/direct）不得再出现
    assert not (ws / "sessions" / "direct" / "tracked_files.json").exists()
    # 面板读取回路能读到 exec 产物
    assert "out.md" in _panel_tracked(ws, key)


@pytest.mark.asyncio
async def test_exec_batch_custom_workspace_persists_under_bound_root(tmp_path, monkeypatch):
    """复现用户报的确切场景（#1096）：切到指定工作目录后，一次对话里先生成两个
    PDF，再用脚本把这两个合成第三个 —— 第三个在「任务资产」里不显示。

    写端曾经分叉：`CreatePdfTool` 走 `_persist_tracked_file`（绑定根、相对 key），
    exec 产物追踪走 `_persist_changed_batch`（app-home、绝对 key）。读端
    `_find_ledger_root` 按「哪份账本已有条目哪份说了算」只认一份，于是合并产物
    永远读不到。

    这里从 exec 的**真实调用形态**走一遍（`_track_workspace_changes` + 构造时的
    两个工作区属性），并断言**读端 API**（`SessionManager.load_tracked_files`，
    `sessions.get_tracked_files` 用的就是它）能拿到全部三个 —— 即面板真的会显示。
    """
    from miqi.agent.tools.shell import ExecTool

    ws = _default_ws()
    bound = tmp_path / "poems"
    bound.mkdir()
    key = "desktop:1063"
    monkeypatch.setattr(
        "miqi.runtime.file_handlers._get_workspace_path", lambda: str(ws),
    )

    # 两首诗各一个 PDF（文档工具 → 绑定根账本、相对 key）
    pdf = _tool("miqi.documents.pdf_create_tool:CreatePdfTool",
                workspace=bound, allowed_dir=bound)
    for name in ("poem1.pdf", "poem2.pdf"):
        result = await pdf.execute(
            filename=name, title=name, content="x", _session_key=key,
        )
        assert "Created:" in result, result

    # 合并：脚本在会话工作区写出第三个 PDF，随后 exec 追踪做快照差分。
    # 绑定会话（自定义工作区）里 _session_files_dir 为 None、_workspace_root 是
    # 绑定根本身 —— 与 tool_registry_factory 构造 ExecTool 时一致。
    exec_tool = ExecTool()
    exec_tool.working_dir = None
    exec_tool._workspace_root = str(bound)
    exec_tool._session_files_dir = None
    before = exec_tool._snapshot_workspace(bound)
    (bound / "poems_merged.pdf").write_bytes(b"%PDF-1.4 merged")
    await exec_tool._track_workspace_changes(
        before, key, bound,
        workspace=exec_tool._session_files_dir or exec_tool._workspace_root,
    )

    # 读端（面板数据源）必须能看到全部三个，key 都是绑定根相对路径。
    visible = _panel_tracked(bound, key)
    for name in ("poem1.pdf", "poem2.pdf", "poems_merged.pdf"):
        assert name in visible, f"{name} 不在面板读得到的条目里：{sorted(visible)}"
    # app-home 那份不得收下这台会话的 exec 产物（旧行为在那里写绝对路径）。
    app_home = _store_path(ws, key)
    if app_home.exists():
        assert str(bound / "poems_merged.pdf").replace("\\", "/") not in _read_tracked(app_home)


@pytest.mark.asyncio
async def test_no_session_key_writes_nothing(tmp_path):
    """``session_key=None`` → 不剥、也不写（早退）。"""
    ws = _default_ws()
    files_dir = _session_files_dir(ws, "desktop:983none")
    tool = _tool("miqi.documents.docx_tool:CreateDocxTool",
                 workspace=files_dir, allowed_dir=files_dir)

    assert "Created:" in await tool.execute(filename="none.docx", title="T")

    assert not _store_path(ws, "desktop:983none").exists()
    assert not _store_path(files_dir, "desktop:983none").exists()


# ── 调用方矩阵（相对路径基准不变）────────────────────────────────────────


@pytest.mark.parametrize(
    "file_rel, expected_key",
    [
        pytest.param("papers/x.pdf", "papers/x.pdf", id="papers"),
        pytest.param("x.md", "x.md", id="shell"),
    ],
)
def test_persist_with_base_workspace_keeps_relative_key(file_rel, expected_key):
    """papers / shell 传 ``workspace=<base>``（非会话 files 目录）→ 不剥，
    键仍为 base 相对路径。"""
    from miqi.agent.tools.filesystem import _persist_tracked_file

    ws = _default_ws()
    key = "desktop:983matrix"
    target = ws / file_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x", encoding="utf-8")

    _persist_tracked_file(ws, target, op="write", session_key=key)

    assert expected_key in _read_tracked(_store_path(ws, key))


@pytest.mark.asyncio
async def test_write_file_tracks_session_relative_segment():
    """write_file 传 ``_tracking_workspace=<base>`` 与
    ``<base>/sessions/<k>/files/x.md`` → 键保持含 ``sessions/<k>/files/`` 段。"""
    from miqi.agent.tools.filesystem import WriteFileTool

    ws = _default_ws()
    key = "desktop:983write"
    files_dir = _session_files_dir(ws, key)
    tool = WriteFileTool(workspace=ws, base_workspace=ws, session_files_dir=files_dir)

    result = await tool.execute(
        path=str(files_dir / "note.md"), content="hi", _session_key=key,
    )
    assert "Successfully wrote" in result, result

    tracked = _read_tracked(_store_path(ws, key))
    expected = f"sessions/{_session_files_dir_key(key)}/files/note.md"
    assert expected in tracked
    assert not _store_path(files_dir, key).exists()


def test_tracked_persist_target_rejects_sibling_prefix_collision(tmp_path):
    """`<ws>-old/x.pdf` 不算 workspace 内部（#1104 review）。

    裸字符串前缀判断会把 `/tmp/project-old/result.pdf` 裁成 `old/result.pdf`，
    但文件并不在 `/tmp/project` 里 —— 读端随后会去 `<ws>/old/result.pdf` 找一个
    不存在的文件。这类路径必须保持绝对 key。
    """
    from miqi.agent.tools.filesystem import _tracked_persist_target

    ws = tmp_path / "project"
    ws.mkdir()
    sibling = tmp_path / "project-old"
    sibling.mkdir()
    outside = sibling / "result.pdf"
    outside.write_text("x")

    target = _tracked_persist_target(ws, str(outside), "desktop:1104")
    assert target is not None
    _, rel_key = target
    assert rel_key == str(outside).replace("\\", "/"), (
        f"兄弟目录不得被裁成 workspace 相对路径：{rel_key}"
    )

    # 真正的子路径仍然相对化（别把边界收紧成「一律绝对」）。
    inside = ws / "sub" / "ok.pdf"
    inside.parent.mkdir()
    inside.write_text("y")
    _, inside_key = _tracked_persist_target(ws, str(inside), "desktop:1104")
    assert inside_key == "sub/ok.pdf"


@pytest.mark.asyncio
async def test_mirror_uses_session_workspace(tmp_path, monkeypatch):
    """sandbox→宿主镜像这条写入口也按会话工作区落账（#1104 review）。

    绑定（自定义）工作区会被 bind-mount 到沙箱的 ``/home/miqi/workspace``，所以
    路径映射、包含性检查和落账必须用同一个根。写死全局工作区会让镜像产物落到
    app-home（读端看不到），还在全局工作区里多留一份位置错误的副本。

    这里把沙箱侧的几个 helper 打桩，但 ``_sandbox_to_host_path`` 用**真实实现** ——
    这样才能钉住「映射到会话工作区」这一步，而不只是钉住落账参数。
    """
    from miqi.agent.tools.shell import ExecTool

    ws = _default_ws()
    bound = tmp_path / "bound-dl"
    bound.mkdir()
    monkeypatch.setattr(
        "miqi.runtime.file_handlers._get_workspace_path", lambda: str(ws),
    )

    class _FakeSandboxManager:
        async def get_or_create(self, session_key):
            return object()

    async def _exists(sandbox, path):
        return True

    async def _read(sandbox, path):
        return b"%PDF-1.4 downloaded"

    fs = "miqi.agent.tools.filesystem."
    monkeypatch.setattr(fs + "_get_session_workspace", lambda workspace, sandbox: bound)
    monkeypatch.setattr(
        fs + "_resolve_sandbox_path",
        lambda filename, session_ws, sandbox: "/home/miqi/workspace/dl.pdf",
    )
    monkeypatch.setattr(fs + "_sandbox_file_exists", _exists)
    monkeypatch.setattr(fs + "_sandbox_read_file", _read)

    exec_tool = ExecTool(sandbox_manager=_FakeSandboxManager())
    exec_tool._workspace_root = str(bound)
    exec_tool._session_files_dir = None

    key = "desktop:1104mirror"
    await exec_tool._mirror_downloaded_files(
        "curl -o dl.pdf https://example.invalid/x.pdf", object(), key,
    )

    assert (bound / "dl.pdf").exists(), "镜像产物必须落在会话工作区"
    assert not (ws / "dl.pdf").exists(), "不得落到全局工作区"
    assert "dl.pdf" in _read_tracked(_store_path(bound, key)), "未落绑定根账本"
