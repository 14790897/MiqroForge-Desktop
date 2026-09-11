"""graph_render 工具测试（issue #715）。

用 issue 中的真实 schema 结构构造 fixture：
- step-graph：gate/compute/report/failure 类别、dashed fallback 边、implicit E 节点
- data-graph：input/intermediate/output/figure/evidence/report 类别、present、step_ref
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from miqi.agent.tools.graph_render import (
    GraphDataError,
    GraphRenderTool,
    _display_width,
    _wrap_text,
    parse_graph_json,
)

STEP_GRAPH = {
    "schema_version": "1.0",
    "graph_type": "step-nodes",
    "skill": "bvse-mof-k8s",
    "source": "runtime-scan",
    "run_dir": "C:/runs/ZECKID_freeONLY_Na_k8s/output",
    "run_state": "completed",
    "description": "以 step 为节点、data 为边(流程视角)。虚线边=失败/回退路径。",
    "nodes": [
        {
            "id": "S1",
            "step_no": "①",
            "title": "CIF 清洗与校验",
            "script": "scripts/mof_check.py",
            "desc": "校验 CIF 完整性并输出清洗后结构；输入不合法即阻断",
            "params": "mof_check.clean_mof(cif, out_dir)",
            "category": "gate",
            "status": "completed",
            "data_refs": ["D1"],
        },
        {
            "id": "S2",
            "step_no": "②",
            "title": "Zeo++ 孔道分析",
            "script": "scripts/zeopp_local.py",
            "desc": "计算孔径/孔体积/孔道连通性",
            "params": "zeopp_local.py --strict/--relaxed",
            "category": "compute",
            "status": "completed",
            "data_refs": ["D2"],
        },
        {
            "id": "S3",
            "step_no": "③",
            "title": "汇总与研究报告",
            "script": "scripts/generate_human_report.py",
            "desc": "生成中文研究报告 + summary.json + DONE.json",
            "params": "generate_human_report.py --out delivery/",
            "category": "report",
            "status": "running",
            "data_refs": ["D6"],
        },
        {
            "id": "E",
            "step_no": None,
            "title": "❌ 失败 / 回退（修复后重跑）",
            "script": None,
            "category": "failure",
            "failure": True,
            "implicit": True,
        },
    ],
    "edges": [
        {"from": "S1", "to": "S2", "label": "清洗后 CIF", "type": "data", "dashed": False},
        {"from": "S2", "to": "S3", "label": "孔道/骨架数据", "type": "data", "dashed": False},
        {"from": "S1", "to": "E", "label": "❌ CIF 清洗/校验失败", "type": "fallback", "dashed": True},
    ],
}

DATA_GRAPH = {
    "schema_version": "1.0",
    "graph_type": "data-nodes",
    "skill": "bvse-mof-k8s",
    "source": "runtime-scan",
    "run_dir": "C:/runs/ZECKID_freeONLY_Na_k8s/output",
    "run_state": "completed",
    "description": "以 data 为节点、step 为边(产物视角, 对偶图)。",
    "nodes": [
        {
            "id": "D0",
            "name": "原始输入 CIF",
            "category": "input",
            "present": True,
            "glob": "*.cif",
            "desc": "用户提供的原始结构文件（run 目录外输入）",
            "path": "cleaned.cif",
        },
        {
            "id": "D1",
            "name": "输入 CIF",
            "category": "intermediate",
            "present": True,
            "glob": "**/cleaned.cif",
            "path": ".skill/delivery/cleaned.cif",
        },
        {
            "id": "D2",
            "name": "BVSE 电荷密度",
            "category": "output",
            "present": True,
            "glob": "**/*.cube",
            "path": ".skill/delivery/Na_bvse.cube",
        },
        {
            "id": "D3",
            "name": "路径可视化",
            "category": "figure",
            "present": False,
            "glob": "**/*pathways*.png",
            "desc": "迁移路径图（本 run 缺失）",
        },
        {
            "id": "D4",
            "name": "完成标记",
            "category": "evidence",
            "present": True,
            "glob": "**/DONE.json",
        },
        {
            "id": "D5",
            "name": "研究报告",
            "category": "report",
            "present": True,
            "glob": "**/*_report.md",
            "path": ".skill/delivery/ZECKID_Na_report.md",
        },
    ],
    "edges": [
        {"from": "D0", "to": "D1", "label": "① CIF 清洗与校验", "step_ref": "S1", "type": "step", "dashed": False},
        {"from": "D1", "to": "D2", "label": "② Zeo++ 孔道分析", "step_ref": "S2", "type": "step", "dashed": False},
        {"from": "D2", "to": "D4", "label": "③ 汇总与研究报告", "step_ref": "S3", "type": "step", "dashed": False},
        {"from": "D4", "to": "D5", "label": "③ 汇总与研究报告", "step_ref": "S3", "type": "step", "dashed": False},
    ],
}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """构造含两份图 JSON 的 run 目录。"""
    (tmp_path / "step-graph.json").write_text(json.dumps(STEP_GRAPH, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "data-graph.json").write_text(json.dumps(DATA_GRAPH, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def make_tool(workspace: Path) -> GraphRenderTool:
    return GraphRenderTool(workspace=workspace, allowed_dir=workspace)


# ── 解析 ─────────────────────────────────────────────────────────────────
class TestParseGraphJson:
    def test_accepts_valid(self):
        g = parse_graph_json(json.dumps(STEP_GRAPH))
        assert g["graph_type"] == "step-nodes"
        assert len(g["nodes"]) == 4
        assert g["warnings"] == []

    def test_bad_json(self):
        with pytest.raises(GraphDataError, match="不是合法 JSON"):
            parse_graph_json("{not json")

    def test_unknown_graph_type(self):
        d = dict(STEP_GRAPH, graph_type="flowchart")
        with pytest.raises(GraphDataError, match="仅支持 step-nodes / data-nodes"):
            parse_graph_json(json.dumps(d))

    def test_missing_nodes(self):
        d = dict(STEP_GRAPH, nodes=[])
        with pytest.raises(GraphDataError, match="nodes 缺失或为空"):
            parse_graph_json(json.dumps(d))

    def test_missing_edges_defaults_empty(self):
        d = {k: v for k, v in STEP_GRAPH.items() if k != "edges"}
        g = parse_graph_json(json.dumps(d))
        assert g["edges"] == []

    def test_schema_version_warning(self):
        d = dict(STEP_GRAPH, schema_version="2.0")
        g = parse_graph_json(json.dumps(d))
        assert any("schema_version" in w for w in g["warnings"])

    def test_edge_ref_validation(self):
        g = parse_graph_json(json.dumps(STEP_GRAPH))
        g["warnings"].extend(
            ["边 X→Y 引用了不存在的节点 Z"] if any(
                e.get("to") == "GHOST" for e in STEP_GRAPH["edges"]
            ) else []
        )
        # 正常样例不应有缺失引用告警
        assert g["warnings"] == []


# ── 文本工具 ─────────────────────────────────────────────────────────────
class TestTextUtils:
    def test_display_width(self):
        assert _display_width("ab") == 2
        assert _display_width("中文") == 4
        assert _display_width("a中b") == 4

    def test_wrap_cjk(self):
        lines = _wrap_text("CIF 清洗与校验（含原子/电荷一致性检查）", 14)
        assert len(lines) >= 2
        assert all(_display_width(line) <= 15 for line in lines)


# ── 布局 ─────────────────────────────────────────────────────────────────
class TestLayoutEdges:
    def test_edge_y_uses_actual_node_height(self):
        """#776：多行标题节点 h > _NODE_H_BASE，边 y 须用实际 h/2 而非
        固定 _NODE_H_BASE/2——否则边连接点偏离节点垂直中心。"""
        from miqi.agent.tools.graph_render import _layout

        g = parse_graph_json(json.dumps(STEP_GRAPH))
        # 加长标题使源节点多行（h > _NODE_H_BASE）
        g["nodes"][0]["title"] = "超长标题" * 12
        layout = _layout(g)
        placed_h = {p["id"]: p["h"] for p in layout["nodes"]}
        for e in layout["edges"]:
            # 边起点/终点 y 应落在节点垂直中心：y_of + h/2（整数偏移）
            assert e["y1"] % 1 == 0 or abs(e["y1"] - round(e["y1"])) < 1e-9
            assert e["y2"] % 1 == 0 or abs(e["y2"] - round(e["y2"])) < 1e-9
            # 多行标题节点（h 增大）的边 y 应随之偏移，而不是固定 62/2=31
            assert placed_h[e["from"]] >= 62
        # 显式验证：超长标题节点 h 增加后，其出边 y1 比单行时下移
        g2 = parse_graph_json(json.dumps(STEP_GRAPH))  # 原始短标题
        layout2 = _layout(g2)
        edge_by_from = {e["from"]: e for e in layout2["edges"]}
        e_long = next(e for e in layout["edges"] if e["from"] == "S1")
        e_short = edge_by_from["S1"]
        assert e_long["y1"] > e_short["y1"], "多行标题节点出边 y 应下移"


# ── 渲染 ─────────────────────────────────────────────────────────────────
class TestRenderSvg:
    async def test_step_graph_svg(self, run_dir: Path):
        tool = make_tool(run_dir)
        result = json.loads(await tool.execute(path=str(run_dir / "step-graph.json")))
        assert result["ok"] is True
        assert result["rendered"][0]["graph_type"] == "step-nodes"
        svg = (run_dir / "step-graph.svg").read_text(encoding="utf-8")
        # 标题 + step_no
        for marker in ("① CIF 清洗与校验", "② Zeo++ 孔道分析", "③ 汇总与研究报告"):
            assert marker in svg
        # 边 label + dashed fallback + 箭头
        assert "清洗后 CIF" in svg
        assert "stroke-dasharray" in svg
        assert "url(#arrow-red)" in svg
        # 图例中文
        for zh in ("校验", "计算", "报告", "失败"):
            assert zh in svg
        # header
        assert "bvse-mof-k8s" in svg
        assert "运行状态: completed" in svg

    async def test_data_graph_svg(self, run_dir: Path):
        tool = make_tool(run_dir)
        result = json.loads(await tool.execute(path=str(run_dir / "data-graph.json")))
        assert result["ok"] is True
        assert result["rendered"][0]["graph_type"] == "data-nodes"
        svg = (run_dir / "data-graph.svg").read_text(encoding="utf-8")
        for name in ("原始输入 CIF", "BVSE 电荷密度", "路径可视化"):
            assert name in svg
        assert "✓ 存在" in svg
        assert "✗ 缺失" in svg
        for zh in ("输入", "中间", "输出", "图表", "证据", "报告"):
            assert zh in svg

    async def test_html_interactive(self, run_dir: Path):
        tool = make_tool(run_dir)
        result = json.loads(
            await tool.execute(path=str(run_dir / "step-graph.json"), format="html")
        )
        assert result["ok"] is True
        html_path = run_dir / "step-graph.html"
        svg_path = run_dir / "step-graph.svg"
        assert html_path.is_file()
        assert svg_path.is_file()
        content = html_path.read_text(encoding="utf-8")
        # hover 数据全部嵌入
        for field_value in (
            "scripts/mof_check.py",
            "mof_check.clean_mof(cif, out_dir)",
            "校验 CIF 完整性并输出清洗后结构",
        ):
            assert field_value in content
        assert "const DATA =" in content
        assert "bvse-mof-k8s · 流程图" in content
        # 输出文件名按 format 返回
        formats = {f["format"] for r in result["rendered"] for f in r["files"]}
        assert formats == {"svg", "html"}

    async def test_auto_discover_dir(self, run_dir: Path):
        tool = make_tool(run_dir)
        result = json.loads(await tool.execute(path=str(run_dir)))
        assert result["ok"] is True
        assert {r["graph_type"] for r in result["rendered"]} == {"step-nodes", "data-nodes"}
        assert (run_dir / "step-graph.svg").is_file()
        assert (run_dir / "data-graph.svg").is_file()

    async def test_out_dir_override(self, run_dir: Path, tmp_path: Path):
        out = tmp_path / "graphs"
        tool = make_tool(run_dir)
        result = json.loads(
            await tool.execute(path=str(run_dir / "step-graph.json"), out_dir=str(out))
        )
        assert result["ok"] is True
        assert (out / "step-graph.svg").is_file()

    async def test_nonexistent_path(self, tmp_path: Path):
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(tmp_path / "nope.json")))
        assert result["ok"] is False
        assert "不存在" in result["error"] or "未找到" in result["error"]

    async def test_dir_without_graphs(self, tmp_path: Path):
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(tmp_path)))
        assert result["ok"] is False


# ── 容错 ─────────────────────────────────────────────────────────────────
class TestTolerance:
    async def test_missing_optional_fields(self, tmp_path: Path):
        """节点缺 desc/params/status、边缺 label 不崩溃。"""
        g = {
            "schema_version": "1.0",
            "graph_type": "step-nodes",
            "skill": "minimal",
            "nodes": [
                {"id": "A", "title": "只有标题"},
                {"id": "B", "title": "另一节点"},
            ],
            "edges": [{"from": "A", "to": "B"}],
        }
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is True
        svg = (tmp_path / "step-graph.svg").read_text(encoding="utf-8")
        assert "只有标题" in svg
        assert "另一节点" in svg

    async def test_empty_edges(self, tmp_path: Path):
        g = dict(STEP_GRAPH, edges=[])
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is True
        assert result["rendered"][0]["edge_count"] == 0

    async def test_unknown_category_fallback(self, tmp_path: Path):
        g = json.loads(json.dumps(STEP_GRAPH))
        g["nodes"][0]["category"] = "quantum-magic"
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is True
        svg = (tmp_path / "step-graph.svg").read_text(encoding="utf-8")
        assert "#ECEFF1" in svg  # 兜底色
        assert "其他" in svg

    async def test_bad_graph_type_returns_error(self, tmp_path: Path):
        g = dict(STEP_GRAPH, graph_type="something-else")
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is False

    async def test_edge_ref_warning(self, tmp_path: Path):
        """边引用不存在节点 → warnings 中出现，但仍渲染。"""
        g = json.loads(json.dumps(STEP_GRAPH))
        g["edges"].append({"from": "S1", "to": "GHOST", "label": "断边"})
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is True
        warnings = result["rendered"][0]["warnings"]
        assert any("GHOST" in w for w in warnings)

    async def test_schema_version_warning_surfaces(self, tmp_path: Path):
        g = dict(STEP_GRAPH, schema_version="2.0-rc1")
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is True
        assert any("schema_version" in w for w in result["rendered"][0]["warnings"])

    async def test_warnings_escaped_in_svg(self, tmp_path: Path):
        """warnings 含外部 JSON 数据（边 from/to、schema_version），进 <text>
        必须转义——与 run_state/title 等一致（#775）。"""
        g = json.loads(json.dumps(STEP_GRAPH))
        # 节点 id / 边引用携带 < > & 字符 → 生成 warnings 里的原始尖括号不得出现
        g["edges"].append({"from": 'S1"x', "to": "<ghost>&", "label": "断边"})
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(tmp_path)
        await tool.execute(path=str(src))
        svg = (tmp_path / "step-graph.svg").read_text(encoding="utf-8")
        assert "&lt;ghost&gt;&amp;" in svg  # 转义后的形式
        assert "<ghost>" not in svg  # 原始尖括号不得出现


# ── 资产栏追踪（tracked_files.json）──────────────────────────────────────
class TestTaskAssetTracking:
    async def test_generated_files_tracked(self, run_dir: Path, tmp_path: Path):
        """生成的 svg（+源 JSON 作为过程文件）进入资产栏 tracked_files.json。"""
        from miqi.session.manager import SessionManager

        tool = make_tool(run_dir)
        result = json.loads(
            await tool.execute(
                path=str(run_dir / "step-graph.json"),
                _session_key="desktop:asset-test-1",
            )
        )
        assert result["ok"] is True

        tracked = SessionManager(run_dir).load_tracked_files("desktop:asset-test-1")
        # 结果文件：svg（op=write）
        assert "step-graph.svg" in tracked
        assert tracked["step-graph.svg"]["op"] == "write"
        # 过程文件：源 JSON（op=read）
        assert "step-graph.json" in tracked
        assert tracked["step-graph.json"]["op"] == "read"

    async def test_html_also_tracked(self, run_dir: Path):
        from miqi.session.manager import SessionManager

        tool = make_tool(run_dir)
        await tool.execute(
            path=str(run_dir), format="html", _session_key="desktop:asset-test-2",
        )
        tracked = SessionManager(run_dir).load_tracked_files("desktop:asset-test-2")
        for name in ("step-graph.svg", "step-graph.html", "data-graph.svg", "data-graph.html"):
            assert name in tracked, f"{name} 应出现在资产栏"
            assert tracked[name]["op"] == "write"

    async def test_no_session_key_skips_tracking(self, run_dir: Path):
        """无 _session_key 注入时（非会话上下文），不写 tracked_files.json。"""
        from miqi.session.manager import SessionManager

        tool = make_tool(run_dir)
        await tool.execute(path=str(run_dir))
        tracked = SessionManager(run_dir).load_tracked_files("desktop:never-used")
        assert "step-graph.svg" not in tracked


# ── CodeRabbit #761 修复回归 ─────────────────────────────────────────────
class TestCodeRabbitFixes:
    async def test_legend_rect_not_nested_in_text(self, run_dir: Path):
        """图例色块 <rect> 不能嵌套在 <text> 内（SVG 渲染器会丢弃）。"""
        tool = make_tool(run_dir)
        await tool.execute(path=str(run_dir / "step-graph.json"))
        svg = (run_dir / "step-graph.svg").read_text(encoding="utf-8")
        # 图例标签独立 <text>，色块 <rect> 是兄弟元素
        assert ">图例：</text>" in svg
        legend_rects = svg.count('rx="3"')  # 图例色块 rx=3（节点矩形 rx=8）
        assert legend_rects >= 2  # gate + compute + report + failure 至少 2 个
        # 不存在 <text ...>图例：<rect 的嵌套
        assert "图例：<rect" not in svg

    async def test_html_escapes_close_script(self, run_dir: Path):
        """字段值含 </script> 不能闭合内联 script 块（XSS）。"""
        g = json.loads(json.dumps(STEP_GRAPH))
        g["nodes"][0]["desc"] = '恶意</script><script>alert(1)</script>'
        src = run_dir / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(run_dir)
        await tool.execute(path=str(src), format="html")
        content = (run_dir / "step-graph.html").read_text(encoding="utf-8")
        # DATA JSON 中的 </ 被转义为 <\/
        assert "恶意<\\/script>" in content
        # 模板 script 块闭合唯一：恶意注入的 </script> 已转义，无法提前闭合
        assert content.count("</script>") == 1
        assert "恶意</script>" not in content

    async def test_run_state_escaped_in_svg(self, tmp_path: Path):
        """run_state 含 < > 必须转义（外部 JSON 输入）。"""
        g = json.loads(json.dumps(STEP_GRAPH))
        g["run_state"] = "failed <on> & \"node\""
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(tmp_path)
        await tool.execute(path=str(src))
        svg = (tmp_path / "step-graph.svg").read_text(encoding="utf-8")
        assert "运行状态: failed &lt;on&gt; &amp;" in svg
        assert "<on>" not in svg  # 原始尖括号不得出现

    async def test_implicit_node_without_id_no_keyerror(self, tmp_path: Path):
        """implicit/failure 节点缺 id 不抛 KeyError（CodeRabbit #761）。"""
        g = json.loads(json.dumps(STEP_GRAPH))
        g["nodes"][-1] = {  # E 节点去掉 id
            "step_no": None,
            "title": "❌ 失败",
            "category": "failure",
            "failure": True,
            "implicit": True,
        }
        src = tmp_path / "step-graph.json"
        src.write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8")
        tool = make_tool(tmp_path)
        result = json.loads(await tool.execute(path=str(src)))
        assert result["ok"] is True


# ── 跨会话隔离（CodeRabbit #851）────────────────────────────────────────
class TestSessionIsolation:
    """graph_render 不得读取/写入其他会话的 files 目录（#689 红线）。"""

    @pytest.mark.asyncio
    async def test_foreign_session_graphs_rejected(self) -> None:
        from miqi.paths import get_miqi_home

        ws = Path(get_miqi_home()) / "workspace"
        other = ws / "sessions" / "other-session-851" / "files"
        other.mkdir(parents=True, exist_ok=True)
        (other / "step-graph.json").write_text(
            json.dumps(STEP_GRAPH, ensure_ascii=False), encoding="utf-8",
        )
        tool = GraphRenderTool(workspace=ws, base_workspace=ws)
        result = json.loads(
            await tool.execute(str(other), _session_key="desktop:own-session-851")
        )
        assert result["ok"] is False
        assert "权限拒绝" in result["error"]

    @pytest.mark.asyncio
    async def test_own_session_graphs_allowed(self) -> None:
        from miqi.paths import get_miqi_home

        ws = Path(get_miqi_home()) / "workspace"
        own = ws / "sessions" / "desktop_own-session-851" / "files"
        own.mkdir(parents=True, exist_ok=True)
        (own / "step-graph.json").write_text(
            json.dumps(STEP_GRAPH, ensure_ascii=False), encoding="utf-8",
        )
        tool = GraphRenderTool(workspace=ws, base_workspace=ws)
        result = json.loads(
            await tool.execute(str(own), _session_key="desktop:own-session-851")
        )
        assert result["ok"] is True
