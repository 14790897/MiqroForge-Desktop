"""内置工具：解析 skill 产物 step-graph.json / data-graph.json 并渲染流程图 / 对偶图。

Issue #715：bvse-mof、qraft-workflowspec-export 等 skill 运行后会生成两份图数据：

- ``step-graph.json`` — 以 step 为节点、data 为边的流程视角
  （"先做 A，产出数据 a，再做 B"），``graph_type: "step-nodes"``
- ``data-graph.json`` — 以 data 为节点、step 为边的对偶图
  （产物视角，"数据 a 由 A 产出，供 B 消费"），``graph_type: "data-nodes"``

本工具解析 JSON（``schema_version`` / ``nodes`` / ``edges``，容错缺失字段），
用纯 Python 手绘 SVG（零第三方依赖），并可选生成单文件交互 HTML
（hover 显示 ``script`` / ``params`` / ``desc`` / ``path`` / ``glob`` 详情）。

输出默认写到源 JSON 同目录（随 skill 产物交付），桌面端通过
``[Image: xxx.svg]`` 占位符内联展示（需 ``file_handlers`` 白名单含 .svg）。
"""

from __future__ import annotations

import html
import json
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from miqi.agent.tools.base import Tool
from miqi.agent.tools.filesystem import (
    _effective_shared_roots,
    _ensure_sandbox,
    _get_session_workspace,
    _is_default_workspace,
    _persist_tracked_file,
    _reject_foreign_session_path,
    _resolve_path,
    _resolve_sandbox_path,
    _resolve_session_dir,
    _sandbox_read_file,
    _sandbox_write_file,
)

# ── 图文件自动发现名称 ──────────────────────────────────────────────────
GRAPH_FILE_NAMES: tuple[str, ...] = ("step-graph.json", "data-graph.json")

# ── category → (中文名, 填充色, 描边色) ─────────────────────────────────
# step-graph: gate/compute/report/failure；data-graph: input/intermediate/
# output/figure/evidence/report。未知 category 走兜底灰（白名单映射，不硬编码）。
CATEGORY_META: dict[str, tuple[str, str, str]] = {
    "gate": ("校验", "#FFF3E0", "#E65100"),
    "compute": ("计算", "#E3F2FD", "#1565C0"),
    "report": ("报告", "#E8F5E9", "#2E7D32"),
    "failure": ("失败", "#FFEBEE", "#C62828"),
    "input": ("输入", "#F5F5F5", "#616161"),
    "intermediate": ("中间", "#FFFDE7", "#F9A825"),
    "output": ("输出", "#E0F7FA", "#00695C"),
    "figure": ("图表", "#F3E5F5", "#6A1B9A"),
    "evidence": ("证据", "#EFEBE9", "#4E342E"),
}
_DEFAULT_CATEGORY: tuple[str, str, str] = ("其他", "#ECEFF1", "#607D8B")

# ── status → 中文标记 ───────────────────────────────────────────────────
_STATUS_ZH: dict[str, str] = {
    "completed": "完成",
    "success": "完成",
    "failed": "失败",
    "failure": "失败",
    "running": "运行中",
    "pending": "等待",
    "skipped": "跳过",
    "blocked": "阻断",
    "unknown": "未知",
}

# ── 布局常量 ────────────────────────────────────────────────────────────
_NODE_W = 216
_NODE_H_BASE = 62
_ROW_GAP = 36
_COL_GAP = 72
_PAD = 40
_HEADER_H = 78
_LEGEND_H = 46
_FONT = "'Microsoft YaHei','PingFang SC','Noto Sans CJK SC',sans-serif"


# ── 文本工具 ────────────────────────────────────────────────────────────
def _display_width(text: str) -> int:
    """估算字符串显示宽度：CJK/全角字符计 2，其余计 1。"""
    w = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


def _wrap_text(text: str, max_width: int) -> list[str]:
    """按显示宽度换行，超长行硬截断。"""
    text = (text or "").strip()
    if not text:
        return []
    lines: list[str] = []
    for raw_line in text.splitlines():
        cur = ""
        cur_w = 0
        for ch in raw_line:
            ch_w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            if cur and cur_w + ch_w > max_width:
                lines.append(cur)
                cur = ""
                cur_w = 0
            cur += ch
            cur_w += ch_w
        if cur:
            lines.append(cur)
    return lines


def _xml_escape(text: str) -> str:
    return html.escape(text or "", quote=True)


def _truncate(text: str, max_width: int) -> str:
    """截断到显示宽度，加省略号。"""
    lines = _wrap_text(text, max_width)
    if not lines:
        return ""
    if len(lines) > 1 or _display_width(text) > max_width:
        return lines[0] + "…"
    return lines[0]


# ── 图数据解析 ──────────────────────────────────────────────────────────
class GraphDataError(ValueError):
    """图 JSON 结构不合法。"""


def parse_graph_json(raw: str, source_name: str = "graph") -> dict[str, Any]:
    """解析并校验 graph JSON，返回规范化 dict。

    校验规则（容错设计）：
    - ``schema_version`` 缺失/非 1.0：仅告警不阻断（warnings 字段）
    - ``graph_type`` 必须为 step-nodes / data-nodes
    - ``nodes`` 必须为非空 list；``edges`` 缺失视为空
    - 节点/边字段全部走默认值
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GraphDataError(f"{source_name} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GraphDataError(f"{source_name} 顶层必须是对象")

    warnings: list[str] = []
    version = data.get("schema_version")
    if version != "1.0":
        warnings.append(f"schema_version={version!r}，仅保证 1.0 兼容")

    graph_type = data.get("graph_type")
    if graph_type not in ("step-nodes", "data-nodes"):
        raise GraphDataError(
            f"{source_name} graph_type={graph_type!r}，仅支持 step-nodes / data-nodes"
        )

    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise GraphDataError(f"{source_name} nodes 缺失或为空")
    if not all(isinstance(n, dict) for n in nodes):
        raise GraphDataError(f"{source_name} nodes 中存在非对象元素")

    edges = data.get("edges")
    if edges is None:
        edges = []
    if not isinstance(edges, list) or not all(isinstance(e, dict) for e in edges):
        raise GraphDataError(f"{source_name} edges 必须是对象数组")

    return {
        "schema_version": version,
        "graph_type": graph_type,
        "skill": data.get("skill", ""),
        "source": data.get("source", ""),
        "run_dir": data.get("run_dir", ""),
        "run_state": data.get("run_state", ""),
        "description": data.get("description", ""),
        "nodes": nodes,
        "edges": edges,
        "warnings": warnings,
    }


def _node_ids(graph: dict[str, Any]) -> set[str]:
    return {str(n.get("id", "")) for n in graph["nodes"]}


def _validate_edge_refs(graph: dict[str, Any]) -> list[str]:
    """校验边的 from/to 是否指向存在的节点，返回缺失引用告警。"""
    ids = _node_ids(graph)
    missing = []
    for e in graph["edges"]:
        for key in ("from", "to"):
            ref = e.get(key)
            if ref is not None and str(ref) not in ids:
                missing.append(f"边 {e.get('from')}→{e.get('to')} 引用了不存在的节点 {ref}")
    return missing


# ── 分层布局（DAG 通用：最长路径分层 + 层内垂直堆叠）────────────────────
def _compute_layers(graph: dict[str, Any]) -> tuple[list[str], dict[str, int]]:
    """返回 (主链拓扑序, id→层号)。implicit/failure 节点放最后。"""
    nodes = graph["nodes"]
    ids = _node_ids(graph)

    # 隐式汇聚节点（failure/implicit）不参与主链分层，置于末尾
    implicit = {
        str(n.get("id", "")) for n in nodes if n.get("implicit") or n.get("failure")
    }
    active = [i for i in ids if i not in implicit]

    indeg = {i: 0 for i in active}
    out_edges: dict[str, list[str]] = {i: [] for i in active}
    for e in graph["edges"]:
        f, t = str(e.get("from", "")), str(e.get("to", ""))
        if f in active and t in active:
            out_edges[f].append(t)
            indeg[t] += 1

    # Kahn 拓扑排序（稳定序）
    import heapq

    heap = [i for i in active if indeg[i] == 0]
    heapq.heapify(heap)
    order: list[str] = []
    while heap:
        node = heapq.heappop(heap)
        order.append(node)
        for t in out_edges[node]:
            indeg[t] -= 1
            if indeg[t] == 0:
                heapq.heappush(heap, t)
    if len(order) < len(active):
        # 有环：把剩余节点按原顺序追加（不报错，流程图尽力渲染）
        order.extend(sorted(set(active) - set(order)))
    order.extend(sorted(implicit))

    # 最长路径分层（保证边指向同层或更右层）
    layer: dict[str, int] = {i: 0 for i in order}
    for node in order:
        if node in implicit:
            continue
        for t in out_edges.get(node, []):
            layer[t] = max(layer[t], layer[node] + 1)
    return order, layer


def _layout(graph: dict[str, Any]) -> dict[str, Any]:
    """计算画布布局。返回 {nodes, edges, canvas_w, canvas_h, by_id}。

    nodes: [{id, x, y, w, h, ...原始字段}]（x/y 为左上角）
    edges: [{from, to, label, dashed, type, x1, y1, x2, y2}]
    """
    order, layer = _compute_layers(graph)
    by_id = {str(n.get("id", "")): n for n in graph["nodes"]}

    # 层号 → 该层节点（按拓扑序排列）
    layers: dict[int, list[str]] = {}
    for node in order:
        layers.setdefault(layer[node], []).append(node)

    y_of: dict[str, float] = {}
    for ids_in_layer in layers.values():
        n_rows = len(ids_in_layer)
        total_h = n_rows * _NODE_H_BASE + (n_rows - 1) * _ROW_GAP
        start_y = _PAD + _HEADER_H + total_h / 2
        for idx, nid in enumerate(ids_in_layer):
            y_of[nid] = start_y - total_h / 2 + idx * (_NODE_H_BASE + _ROW_GAP)

    x_of: dict[str, float] = {
        nid: _PAD + layer[nid] * (_NODE_W + _COL_GAP) for nid in order
    }

    # 节点矩形高度按标题行数微调（最多 +1 行）
    placed: list[dict[str, Any]] = []
    for nid in order:
        node = by_id[nid]
        lines = max(1, len(_wrap_text(node.get("title") or node.get("name") or nid, 14)))
        h = _NODE_H_BASE + 14 * max(0, lines - 1)
        placed.append(
            {
                "id": nid,
                "x": x_of[nid],
                "y": y_of[nid],
                "w": _NODE_W,
                "h": h,
                "node": node,
            }
        )

    # 节点实际高度映射（多行标题节点 h > _NODE_H_BASE，边须连接垂直中心）
    h_of = {p["id"]: p["h"] for p in placed}

    # 边（含虚线 fallback）
    edge_list: list[dict[str, Any]] = []
    for e in graph["edges"]:
        f, t = str(e.get("from", "")), str(e.get("to", ""))
        if f not in by_id or t not in by_id:
            continue
        x1 = x_of[f] + _NODE_W
        y1 = y_of[f] + h_of[f] / 2
        x2 = x_of[t]
        y2 = y_of[t] + h_of[t] / 2
        edge_list.append(
            {
                "from": f,
                "to": t,
                "label": e.get("label", ""),
                "type": e.get("type", "data"),
                "dashed": bool(e.get("dashed")),
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
            }
        )

    max_layer = max(layer.values()) if layer else 0
    canvas_w = _PAD * 2 + (max_layer + 1) * _NODE_W + max_layer * _COL_GAP
    canvas_h = _PAD * 2 + _HEADER_H + _LEGEND_H + _NODE_H_BASE
    for ids_in_layer in layers.values():
        need = len(ids_in_layer) * _NODE_H_BASE + (len(ids_in_layer) - 1) * _ROW_GAP
        canvas_h = max(canvas_h, _PAD * 2 + _HEADER_H + need + _LEGEND_H)

    return {
        "nodes": placed,
        "edges": edge_list,
        "canvas_w": canvas_w,
        "canvas_h": canvas_h,
        "by_id": by_id,
    }


# ── SVG 渲染 ─────────────────────────────────────────────────────────────
def _category_meta(node: dict[str, Any]) -> tuple[str, str, str]:
    cat = str(node.get("category", "")).lower()
    return CATEGORY_META.get(cat, _DEFAULT_CATEGORY)


def _status_text(node: dict[str, Any]) -> str:
    return _STATUS_ZH.get(str(node.get("status", "")).lower(), "")


def _node_title(node: dict[str, Any], graph_type: str) -> str:
    if graph_type == "step-nodes":
        step_no = node.get("step_no")
        title = node.get("title") or node.get("id", "")
        return f"{step_no or ''} {title}".strip()
    return node.get("name") or node.get("id", "")


def _render_svg(graph: dict[str, Any], layout: dict[str, Any]) -> str:
    graph_type = graph["graph_type"]
    skill = graph.get("skill", "") or "未命名 skill"
    zh_type = "流程图（step 视角）" if graph_type == "step-nodes" else "对偶图（data 视角）"
    run_state = graph.get("run_state", "")
    # run_state 来自外部 JSON，须转义后再进 <text>（CodeRabbit #761）
    state_tag = (
        f" · 运行状态: {_xml_escape(str(run_state))}"
        if run_state and run_state != "unknown"
        else ""
    )

    nodes_svg: list[str] = []
    for p in layout["nodes"]:
        node = p["node"]
        nid = p["id"]
        x, y, w, h = p["x"], p["y"], p["w"], p["h"]
        zh_cat, fill, stroke = _category_meta(node)
        is_implicit = bool(node.get("implicit") or node.get("failure"))
        dash_attr = ' stroke-dasharray="7,4"' if is_implicit else ""
        title = _node_title(node, graph_type)
        title_lines = _wrap_text(title, 16) or [nid]
        cx = x + w / 2

        # 标题（最多 2 行，超过截断）
        shown = title_lines[:2]
        body = "".join(
            f'<text x="{cx}" y="{y + 24 + i * 18}" text-anchor="middle" '
            f'font-size="13" font-weight="bold" fill="#1A1A1A">{_xml_escape(line)}</text>'
            for i, line in enumerate(shown)
        )

        # 副行：category 中文 + 状态 / present
        sub_parts = [zh_cat]
        if graph_type == "step-nodes":
            st = _status_text(node)
            if st:
                sub_parts.append(st)
        else:
            present = node.get("present")
            if present is True:
                sub_parts.append("✓ 存在")
            elif present is False:
                sub_parts.append("✗ 缺失")
        sub = " · ".join(sub_parts)
        body += (
            f'<text x="{cx}" y="{y + h - 14}" text-anchor="middle" font-size="11" '
            f'fill="#666666">{_xml_escape(sub)}</text>'
        )

        # tooltip 数据（HTML 版用；SVG 版供 title 提示）
        tip_parts = []
        for key, zh in (
            ("script", "脚本"),
            ("params", "参数"),
            ("desc", "说明"),
            ("detail", "详情"),
            ("path", "路径"),
            ("glob", "匹配"),
        ):
            val = node.get(key)
            if val:
                tip_parts.append(f"{zh}: {val}")
        tip = " | ".join(tip_parts) if tip_parts else nid
        tip_attr = f'<title>{_xml_escape(tip)}</title>'

        nodes_svg.append(
            f'<g data-id="{_xml_escape(nid)}">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.6"{dash_attr}/>'
            f"{body}{tip_attr}</g>"
        )

    edges_svg: list[str] = []
    for e in layout["edges"]:
        label = e["label"] or ""
        dashed = e["dashed"]
        stroke = "#C62828" if dashed else "#546E7A"
        marker = "url(#arrow-red)" if dashed else "url(#arrow)"
        mid_x = (e["x1"] + e["x2"]) / 2
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        edges_svg.append(
            f'<path d="M {e["x1"]} {e["y1"]} C {mid_x} {e["y1"]}, {mid_x} {e["y2"]}, '
            f'{e["x2"]} {e["y2"]}" fill="none" stroke="{stroke}" stroke-width="1.6" '
            f'stroke-linecap="round"{dash} marker-end="{marker}"/>'
        )
        if label:
            edges_svg.append(
                f'<text x="{mid_x}" y="{max(e["y1"], e["y2"]) - 6}" text-anchor="middle" '
                f'font-size="10.5" fill="{stroke}">{_xml_escape(_truncate(label, 20))}</text>'
            )

    legend_label = (
        f'<text x="{_PAD}" y="{layout["canvas_h"] - 14}" font-size="10.5" fill="#888">'
        f"图例：</text>"
    )
    # 图例项作为 <text> 的兄弟元素（SVG <text> 不接受 <rect> 子元素，
    # 嵌套会被渲染器丢弃——CodeRabbit #761）
    legend_items = "".join(
        f'<rect x="{_PAD + 48 + i * 96}" y="{layout["canvas_h"] - _LEGEND_H + 8}" width="14" '
        f'height="14" rx="3" fill="{fill}" stroke="{stroke}"/>'
        f'<text x="{_PAD + 48 + i * 96 + 20}" y="{layout["canvas_h"] - _LEGEND_H + 20}" '
        f'font-size="11" fill="#444">{zh}</text>'
        for i, (zh, fill, stroke) in enumerate(
            sorted({_category_meta(n["node"]) for n in layout["nodes"]})
        )
    )

    warnings = graph.get("warnings", [])
    warn_text = ""
    if warnings:
        # warnings 含外部 JSON 数据（schema_version、节点 id、边 from/to），
        # 须转义后再进 <text>，与 run_state/title/tip 等一致（#775）。
        warn_text = (
            f'<text x="{_PAD}" y="{_HEADER_H + 34}" font-size="11" fill="#B26A00">'
            f'⚠ {_xml_escape("；".join(warnings))}</text>'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{layout["canvas_w"]}" '
        f'height="{layout["canvas_h"]}" viewBox="0 0 {layout["canvas_w"]} '
        f'{layout["canvas_h"]}" font-family="{_FONT}">'
        f"<defs>"
        f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="#546E7A"/></marker>'
        f'<marker id="arrow-red" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="#C62828"/></marker>'
        f"</defs>"
        f'<rect x="0" y="0" width="{layout["canvas_w"]}" height="{layout["canvas_h"]}" '
        f'fill="#FFFFFF"/>'
        f'<text x="{_PAD}" y="34" font-size="19" font-weight="bold" fill="#1A1A1A">'
        f"{_xml_escape(skill)} · {zh_type}</text>"
        f'<text x="{_PAD}" y="56" font-size="12" fill="#666666">'
        f"{_xml_escape(graph.get('description') or '')}{state_tag}</text>"
        f"{warn_text}"
        f'<line x1="{_PAD}" y1="{_HEADER_H}" x2="{layout["canvas_w"] - _PAD}" '
        f'y2="{_HEADER_H}" stroke="#DDDDDD"/>'
        + "".join(edges_svg)
        + "".join(nodes_svg)
        + f'<line x1="{_PAD}" y1="{layout["canvas_h"] - _LEGEND_H}" '
        f'x2="{layout["canvas_w"] - _PAD}" y2="{layout["canvas_h"] - _LEGEND_H}" '
        f'stroke="#EEEEEE"/>'
        f"{legend_label}{legend_items}"
        f"</svg>"
    )
    return svg


# ── HTML 渲染（单文件交互版）─────────────────────────────────────────────
def _render_html(graph: dict[str, Any], layout: dict[str, Any], svg_content: str) -> str:
    skill = graph.get("skill", "") or "未命名 skill"
    zh_type = "流程图（step 视角）" if graph["graph_type"] == "step-nodes" else "对偶图（data 视角）"

    # 节点详情数据（供 hover / 点击 tooltip）
    node_data = {}
    for p in layout["nodes"]:
        node = p["node"]
        fields = {
            "id": node.get("id", ""),
            "title": _node_title(node, graph["graph_type"]),
            "category": _category_meta(node)[0],
            "status": _status_text(node),
            "script": node.get("script", ""),
            "params": node.get("params", ""),
            "desc": node.get("desc", ""),
            "detail": node.get("detail", ""),
            "path": node.get("path", ""),
            "glob": node.get("glob", ""),
        }
        node_data[node.get("id", "")] = fields
    # json.dumps 不转义 "/"：字段值含 "</script>" 会闭合内联 script 块
    # （XSS——CodeRabbit #761）。转义 "</" 后 JSON 语义不变（"\/" 合法）。
    data_json = json.dumps(node_data, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{html.escape(skill)} · {html.escape(zh_type)}</title>
<style>
  body {{ font-family: {_FONT}; background: #F5F6F8; margin: 0; padding: 24px; }}
  .card {{ background: #FFF; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,.08);
          padding: 16px 20px; max-width: 1200px; margin: 0 auto; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; color: #1A1A1A; }}
  .sub {{ font-size: 12px; color: #888; margin-bottom: 14px; }}
  svg {{ display: block; width: 100%; height: auto; }}
  svg g[data-id] {{ cursor: pointer; }}
  svg g[data-id]:hover rect {{ stroke-width: 2.4; }}
  #tip {{ position: fixed; max-width: 380px; background: rgba(26,26,26,.94); color: #FFF;
         border-radius: 8px; padding: 10px 12px; font-size: 12px; line-height: 1.7;
         box-shadow: 0 4px 16px rgba(0,0,0,.25); pointer-events: none; display: none;
         white-space: pre-wrap; z-index: 10; }}
  #tip b {{ color: #FFD54F; }}
  .legend {{ font-size: 11px; color: #888; margin-top: 10px; }}
</style>
</head>
<body>
<div class="card">
  <h1>{html.escape(skill)} · {html.escape(zh_type)}</h1>
  <div class="sub">{html.escape(graph.get('description') or '')}</div>
  {svg_content}
  <div class="legend">悬停节点查看脚本 / 参数 / 说明详情；虚线边为失败或回退路径。</div>
</div>
<div id="tip"></div>
<script>
const DATA = {data_json};
const svg = document.querySelector('svg');
const tip = document.getElementById('tip');
function showTip(e) {{
  const g = e.target.closest('g[data-id]');
  if (!g) {{ tip.style.display = 'none'; return; }}
  const d = DATA[g.dataset.id];
  if (!d) {{ tip.style.display = 'none'; return; }}
  const rows = [['id', d.id], ['title', d.title], ['category', d.category], ['status', d.status],
                ['script', d.script], ['params', d.params], ['desc', d.desc],
                ['detail', d.detail], ['path', d.path], ['glob', d.glob]];
  const text = rows.filter(r => r[1]).map(r => `<b>${{r[0]}}</b> ${{r[1]}}`).join('\\n');
  tip.textContent = text;
  tip.style.display = 'block';
  const r = svg.getBoundingClientRect();
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 400) + 'px';
  tip.style.top = (e.clientY + 14) + 'px';
}}
svg.addEventListener('mousemove', showTip);
svg.addEventListener('mouseleave', () => {{ tip.style.display = 'none'; }});
</script>
</body>
</html>
"""


# ── 工具 ─────────────────────────────────────────────────────────────────
class GraphRenderTool(Tool):
    """解析 step-graph.json / data-graph.json 并渲染为流程图 / 对偶图。"""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        sandbox_manager=None,
        shared_roots: Iterable[Path] | None = None,
        base_workspace: Path | None = None,
        allow_user_roots: bool = True,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._shared_roots = list(shared_roots or [])
        self._base_workspace = base_workspace
        self._allow_user_roots = allow_user_roots

    @property
    def _tracking_workspace(self) -> Path | None:
        """tracked_files.json 记账用的 workspace 根（与 WriteFileTool 一致）。"""
        return self._base_workspace or self._workspace

    @property
    def name(self) -> str:
        return "graph_render"

    @property
    def description(self) -> str:
        return (
            "解析 skill 运行产物 step-graph.json（以 step 为节点、data 为边的流程图）"
            "或 data-graph.json（以 data 为节点、step 为边的对偶图），渲染为中文 SVG "
            "矢量图（可内联展示 / 嵌入 Markdown）或单文件交互 HTML（悬停节点查看 "
            "script/params/desc/path/glob 详情）。输出默认写到源 JSON 同目录，随 "
            "skill 产物一起交付。路径可传单个 JSON 文件，或包含这两份文件的 run 目录"
            "（自动发现并渲染全部存在的图）。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "step-graph.json / data-graph.json 的文件路径，或包含这两份"
                        "文件的 run 目录路径（自动发现并渲染全部存在的图）。"
                    ),
                },
                "format": {
                    "type": "string",
                    "enum": ["svg", "html"],
                    "description": (
                        "输出格式，默认 svg。svg=矢量图（可 [Image: xxx.svg] 内联展示、"
                        "嵌入 Markdown 交付）；html=单文件交互版（悬停查看详情），"
                        "需要交互版时请再次调用本工具并传 format=html。"
                    ),
                },
                "out_dir": {
                    "type": "string",
                    "description": (
                        "可选。输出目录；默认与源 JSON 同目录（随 skill 产物交付）。"
                    ),
                },
            },
            "required": ["path"],
        }

    # ── 文件读写（native + WSL sandbox，与 filesystem 工具一致）─────────
    def _resolve(self, path: str, shared: Iterable[Path] | None = None) -> Path:
        return _resolve_path(
            path,
            self._workspace,
            self._allowed_dir,
            self._sandbox_manager,
            shared_roots=list(shared) if shared is not None else self._shared_roots,
        )

    async def _read_text(
        self,
        resolved: Path,
        sandbox,
        shared: Iterable[Path] | None = None,
        session_dir: Path | None = None,
    ) -> str:
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            sandbox_path = _resolve_sandbox_path(
                str(resolved), self._workspace, sandbox,
                extra_roots=list(shared) if shared is not None else self._shared_roots,
                session_files_dir=session_dir,
            )
            return await _sandbox_read_file(sandbox, sandbox_path)
        return resolved.read_text(encoding="utf-8")

    async def _write_text(
        self,
        resolved: Path,
        content: str,
        sandbox,
        session_key: str | None = None,
        shared: Iterable[Path] | None = None,
        session_dir: Path | None = None,
        base_ws: Path | None = None,
    ) -> None:
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            from miqi.agent.tools.filesystem import _sandbox_to_host_path

            sandbox_path = _resolve_sandbox_path(
                str(resolved), self._workspace, sandbox,
                extra_roots=list(shared) if shared is not None else self._shared_roots,
                session_files_dir=session_dir,
            )
            await _sandbox_write_file(sandbox, sandbox_path, content)
            # 镜像到宿主 workspace，供 files.read 读取（与 WriteFileTool 一致）
            host_path = _sandbox_to_host_path(sandbox_path, self._workspace, sandbox)
            if not sandbox_path.startswith("/mnt/"):
                Path(host_path).parent.mkdir(parents=True, exist_ok=True)
                Path(host_path).write_text(content, encoding="utf-8")
            # 资产栏追踪（结果文件，与 WriteFileTool 同约定）
            _persist_tracked_file(
                self._tracking_workspace, host_path, op="write", session_key=session_key,
            )
            return
        # Cross-session isolation for the native path (CodeRabbit #851).
        _reject_foreign_session_path(resolved, base_ws, session_dir)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        # 资产栏追踪（结果文件）：svg/html 随源 JSON 旁交付，前端按
        # taskAssetClassification 白名单分类展示（#715）。
        _persist_tracked_file(
            self._tracking_workspace, resolved, op="write", session_key=session_key,
        )

    async def _collect_targets(
        self,
        path: str,
        sandbox,
        shared: Iterable[Path] | None = None,
        session_dir: Path | None = None,
        base_ws: Path | None = None,
    ) -> list[tuple[Path, str]]:
        """返回 [(源 JSON resolved 路径, 文件名)]；目录则自动发现。"""
        roots = list(shared) if shared is not None else self._shared_roots
        resolved = self._resolve(path, roots)
        # Cross-session isolation for the native path (CodeRabbit #851):
        # another session's files dir must not be readable.
        _reject_foreign_session_path(resolved, base_ws, session_dir)
        if sandbox is not None and getattr(sandbox, "_use_wsl", False):
            from miqi.agent.tools.filesystem import _sandbox_dir_exists, _sandbox_file_exists

            sp = _resolve_sandbox_path(
                str(resolved), self._workspace, sandbox, extra_roots=roots,
                session_files_dir=session_dir,
            )
            is_dir = await _sandbox_dir_exists(sandbox, sp)
            is_file = await _sandbox_file_exists(sandbox, sp)
        else:
            is_dir = resolved.is_dir()
            is_file = resolved.is_file()

        if is_dir:
            targets = []
            for name in GRAPH_FILE_NAMES:
                candidate = resolved / name
                if sandbox is not None and getattr(sandbox, "_use_wsl", False):
                    sp = _resolve_sandbox_path(
                        str(candidate), self._workspace, sandbox,
                        extra_roots=roots,
                        session_files_dir=session_dir,
                    )
                    exists = await _sandbox_file_exists(sandbox, sp)
                else:
                    exists = candidate.is_file()
                if exists:
                    targets.append((candidate, name))
            if not targets:
                raise GraphDataError(f"目录 {resolved} 中未找到 {GRAPH_FILE_NAMES}")
            return targets
        if is_file:
            return [(resolved, resolved.name)]
        raise GraphDataError(f"路径不存在: {resolved}")

    async def execute(
        self, path: str, format: str = "svg", out_dir: str | None = None, **kwargs: Any
    ) -> str:
        _sess_key = kwargs.pop("_session_key", None)
        shared = _effective_shared_roots(
            self._shared_roots, kwargs.pop("_user_roots", None), self._allow_user_roots,
        )
        sandbox = await _ensure_sandbox(self._sandbox_manager, session_key=_sess_key)
        session_ws = _get_session_workspace(self._workspace, sandbox)
        base_ws = self._base_workspace or (
            self._workspace if _is_default_workspace(self._workspace) else None
        )
        # Cross-session isolation (CodeRabbit #851): the workspace root is a
        # shared legal root, so without the session dir a foreign session's
        # files dir would pass the WSL containment check.
        session_dir = _resolve_session_dir(
            None, session_ws, self._workspace, _sess_key, base_ws,
        )

        results = []
        errors = []
        try:
            targets = await self._collect_targets(path, sandbox, shared, session_dir, base_ws)
        except GraphDataError as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        except PermissionError as exc:
            return json.dumps({"ok": False, "error": f"权限拒绝: {exc}"}, ensure_ascii=False)

        for resolved, name in targets:
            try:
                raw = await self._read_text(resolved, sandbox, shared, session_dir)
                # 资产栏追踪（过程文件）：源图数据 JSON 随 skill 产物生成
                _persist_tracked_file(
                    self._tracking_workspace, resolved, op="read", session_key=_sess_key,
                )
                graph = parse_graph_json(raw, source_name=name)
                graph["warnings"].extend(_validate_edge_refs(graph))
                layout = _layout(graph)

                out_paths = []
                svg_content = _render_svg(graph, layout)
                base_dir = (
                    self._resolve(out_dir, shared)
                    if out_dir
                    else resolved.parent
                )
                stem = resolved.stem  # step-graph / data-graph
                if format == "html":
                    svg_path = base_dir / f"{stem}.svg"
                    html_path = base_dir / f"{stem}.html"
                    await self._write_text(
                        svg_path, svg_content, sandbox,
                        session_key=_sess_key, shared=shared,
                        session_dir=session_dir, base_ws=base_ws,
                    )
                    await self._write_text(
                        html_path,
                        _render_html(graph, layout, svg_content),
                        sandbox,
                        session_key=_sess_key,
                        shared=shared,
                        session_dir=session_dir, base_ws=base_ws,
                    )
                    out_paths = [svg_path, html_path]
                else:
                    svg_path = base_dir / f"{stem}.svg"
                    await self._write_text(
                        svg_path, svg_content, sandbox,
                        session_key=_sess_key, shared=shared,
                        session_dir=session_dir, base_ws=base_ws,
                    )
                    out_paths = [svg_path]

                results.append(
                    {
                        "graph_type": graph["graph_type"],
                        "source": name,
                        "skill": graph.get("skill", ""),
                        "node_count": len(graph["nodes"]),
                        "edge_count": len(graph["edges"]),
                        "files": [
                            {
                                "format": "svg" if p.suffix == ".svg" else "html",
                                "path": str(p),
                                "name": p.name,
                            }
                            for p in out_paths
                        ],
                        "warnings": graph["warnings"],
                    }
                )
            except GraphDataError as exc:
                errors.append({"source": name, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 — 工具边界，聚合错误返回给 agent
                errors.append({"source": name, "error": f"{type(exc).__name__}: {exc}"})

        if not results:
            return json.dumps(
                {"ok": False, "error": "渲染失败", "errors": errors}, ensure_ascii=False
            )

        return json.dumps(
            {
                "ok": True,
                "rendered": results,
                "errors": errors or None,
                "hint": (
                    "会话中展示请用 [Image: <name>.svg]；需交互版（悬停查看脚本/参数/说明）"
                    "时再以 format=html 调用一次。"
                ),
            },
            ensure_ascii=False,
        )
