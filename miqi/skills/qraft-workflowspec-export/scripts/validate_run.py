#!/usr/bin/env python3
"""Validate a WorkflowRun JSON against references/workflowspec.schema.json.

Usage:
    python validate_run.py <run.json> [--schema <path>] [--strict] [--semantic]

Exit codes:
    0  VALID
    1  INVALID (schema or semantic A-level errors found)
    2  ERROR   (file not found / unreadable)

--strict    Also check document_kind in (workflow_definition, workflow_run)
--semantic  Run A/B-level semantic sanity checks (see SKILL.md Step 4.5):
            A-level findings → exit 1 (blocked, no official file);
            B-level findings → reported as warnings, exit 0.

The schema's top-level `oneOf` accepts either WorkflowDefinition or
WorkflowRun; --strict requires an explicit document_kind of either type.
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

try:
    import jsonschema  # noqa: F401 (availability probe; validator used below)
    from jsonschema import Draft202012Validator

except ImportError:  # pragma: no cover
    sys.stderr.write("ERROR: jsonschema package not installed. Run: pip install jsonschema\n")
    sys.exit(2)


def _ensure_utf8_streams() -> None:
    """Windows 默认 stdout/stderr 编码为 cp1252 等本地代码页，打印中文会
    UnicodeEncodeError 崩溃。统一重配置为 UTF-8（Python 3.7+）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "references" / "workflowspec.schema.json"


def load_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.stderr.write(f"ERROR: file not found: {path}\n")
        sys.exit(2)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: invalid JSON in {path}: {e}\n")
        sys.exit(2)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _semantic_check_definition(doc: dict):
    """workflow_definition 语义检查（官方 OAuth2 文档 8.4/8.6 必填与错误表）。"""
    a = []
    b = []
    meta = doc.get("metadata") or {}
    if not (meta.get("title") or "").strip():
        a.append("A1: metadata.title 不能为空")
    if not (meta.get("description") or "").strip():
        a.append("A2: metadata.description 不能为空")
    if not (meta.get("id") or "").strip():
        a.append("A3: metadata.id 不能为空（唯一标识符）")
    if not (meta.get("version") or "").strip():
        a.append("A4: metadata.version 不能为空（语义化版本号）")
    if not (meta.get("name") or "").strip():
        a.append("A8: metadata.name 不能为空（机器可读名称）")
    nodes = (doc.get("graph") or {}).get("nodes") or []
    if not nodes:
        a.append("A5: graph.nodes 为空 → DAG 至少需要 1 个节点")
    for i, node in enumerate(nodes):
        presentation = node.get("presentation") or {}
        if "data_view" not in presentation:
            a.append(f"A6: 节点 {node.get('id', i)} 的数据视图不能为空（presentation.data_view 缺失）")
        if "action_view" not in presentation:
            a.append(f"A7: 节点 {node.get('id', i)} 的动作视图不能为空（presentation.action_view 缺失）")
    if not (doc.get("graph") or {}).get("edges"):
        b.append("B1: graph.edges 为空 → 节点间无依赖边（线性流程）")
    return a, b


def _nonfinite_paths(doc, path=""):
    """递归扫描文档中的非有限浮点数（NaN/Infinity），返回其路径列表。

    Python 的 json.load 默认接受 NaN/Infinity，jsonschema 的 number 类型
    检查也放行；这些值 json.dump 时会写成非标准 JSON，必须单独拦截。
    """
    found = []
    if isinstance(doc, dict):
        for key, value in doc.items():
            p = f"{path}.{key}" if path else str(key)
            found.extend(_nonfinite_paths(value, p))
    elif isinstance(doc, list):
        for i, value in enumerate(doc):
            found.extend(_nonfinite_paths(value, f"{path}[{i}]"))
    elif isinstance(doc, float) and (math.isnan(doc) or math.isinf(doc)):
        found.append(path)
    return found


def semantic_check(doc: dict):
    """A/B-level semantic sanity checks. Returns (a_errors, b_warnings)."""
    a = []
    b = []

    # 非有限数值拦截先于 document_kind 分流，两种文档类型都覆盖
    # （workflow_definition 的 extensions 等 JsonValue 字段同样会放行 NaN/Infinity）。
    for p in _nonfinite_paths(doc):
        a.append(f"A0: {p} 为非有限数值（NaN/Infinity）→ 非标准 JSON，禁止输出")

    if doc.get("document_kind") == "workflow_definition":
        # _semantic_check_definition 自建 a/b，需合并外层已累计的 A0 拦截。
        a_def, b_def = _semantic_check_definition(doc)
        return a + a_def, b + b_def

    summary = doc.get("summary") or {}
    if not summary.get("human_summary"):
        a.append("A1: summary.human_summary 为空 → 无人类可读摘要，记录失去归档意义")

    arrays = {k: doc.get(k) or [] for k in ("artifacts", "metrics", "evidence", "claims")}
    diag = doc.get("diagnostics") or []
    if all(len(v) == 0 for v in arrays.values()) and not diag:
        a.append("A2: artifacts/metrics/evidence/claims 同时为空且无 diagnostics 说明 → 导出未收集到任何内容，疑似输入遗漏")

    for m in doc.get("metrics") or []:
        v = m.get("value")
        if not _is_number(v):
            a.append(f"A3: metrics[{m.get('id')}].value 不是 number ({type(v).__name__}: {v!r}) → 指标不可比较")
        elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            a.append(f"A3: metrics[{m.get('id')}].value 为 NaN/Infinity → 非标准 JSON，禁止输出")
        else:
            absv = abs(v)
            if absv > 1e6:
                b.append(f"B6: metrics[{m.get('id')}].value 绝对值 {v} 超出常见物理量级，请确认")

    for c in doc.get("claims") or []:
        if not (c.get("statement") or "").strip():
            a.append(f"A4: claims[{c.get('id')}].statement 为空 → 结论为空")

    wf = doc.get("workflow_ref") or {}
    if not wf.get("version"):
        a.append("A5: workflow_ref 缺 version → 追溯链断裂")
    req = doc.get("request") or {}
    if not (req.get("prompt") or "").strip():
        a.append("A6: request.prompt 为空 → 追溯链断裂")

    if not (req or {}):
        b.append("B7: request 为空对象 → 缺少用户请求上下文，追溯性受损")

    for art in doc.get("artifacts") or []:
        if art.get("size_bytes") == 0 and art.get("checksum"):
            b.append(f"B8: artifacts[{art.get('id')}].size_bytes 为 0 但有 checksum → 确认是否为空文件")
        cs = art.get("checksum") or {}
        algo = cs.get("algorithm")
        val = cs.get("value") or ""
        expected_len = {"sha256": 64, "sha1": 40, "md5": 32}.get(algo)
        if expected_len and not re.fullmatch(r"[a-fA-F0-9]{" + str(expected_len) + r"}", val):
            b.append(f"B11: artifacts[{art.get('id')}].checksum 与算法 {algo} 不匹配（期望 {expected_len} 位 hex）→ 校验和可能错误")

    if not doc.get("node_runs"):
        b.append("B9: node_runs 为空数组 → 未记录任何 skill 调用")

    bk = (doc.get("execution") or {}).get("backend") or {}
    if bk.get("kind") in ("other", "manual"):
        b.append("B10: execution.backend.kind 为 other/manual → 后端不明确，确认 selection_reason 是否充分")

    return a, b


def main() -> int:
    # 先于 parse_args 重配置编码：argparse 的 help/错误输出（含中文）在
    # parse_args 内部就会写入 stdout/stderr，Windows cp1252 下会先崩溃。
    _ensure_utf8_streams()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_json", help="Path to the WorkflowRun JSON to validate")
    ap.add_argument("--schema", default=str(DEFAULT_SCHEMA), help="Path to workflowspec.schema.json")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Also check document_kind in (workflow_definition, workflow_run)",
    )
    ap.add_argument("--semantic", action="store_true", help="Run A/B-level semantic sanity checks")
    ap.add_argument(
        "--report-json",
        action="store_true",
        help="输出机器可读的 validation_report（{status, schemaErrors, aErrors, bWarnings}），"
        "供 SKILL 渲染方案视图/上传前拦截（#674 功能描述 2）",
    )
    args = ap.parse_args()

    run_path = Path(args.run_json)
    schema_path = Path(args.schema)
    run_doc = load_json(run_path)
    schema_doc = load_json(schema_path)

    validator = Draft202012Validator(schema_doc)
    errors = sorted(validator.iter_errors(run_doc), key=lambda e: list(e.path))

    strict_error = None
    a_errs: list[str] = []
    b_warns: list[str] = []
    if not errors and args.strict:
        kind = run_doc.get("document_kind")
        if kind not in ("workflow_definition", "workflow_run"):
            strict_error = (
                f"document_kind is {kind!r}, expected 'workflow_definition' or 'workflow_run'"
            )
    if not errors and strict_error is None and args.semantic:
        a_errs, b_warns = semantic_check(run_doc)

    status = "INVALID" if errors else ("INVALID" if strict_error else ("SEMANTIC_BLOCKED" if a_errs else "VALID"))
    if args.report_json:
        print(
            json.dumps(
                {
                    "status": status,
                    "schemaErrors": [
                        {"path": "/".join(str(p) for p in e.path) or "<root>", "message": e.message}
                        for e in errors[:50]
                    ],
                    "aErrors": a_errs,
                    "bWarnings": b_warns,
                    "strictError": strict_error,
                },
                ensure_ascii=False,
            )
        )
        return 0 if status == "VALID" else 1

    if errors:
        print(f"INVALID: {run_path} has {len(errors)} error(s):")
        for e in errors[:50]:
            loc = "/".join(str(p) for p in e.path) or "<root>"
            print(f"  - {loc}: {e.message}")
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more")
        return 1

    if strict_error:
        sys.stderr.write(f"ERROR: {strict_error}\n")
        return 1

    # 明确标注这是 schema 层面的结果：A 级语义问题会在此之后输出
    # SEMANTIC BLOCKED 并返回 1，避免子串匹配把被拦截的文档误判为 VALID。
    print(f"SCHEMA VALID: {run_path} conforms to {schema_path.name}")
    if args.semantic:
        for w in b_warns:
            print(f"  WARNING (B): {w}")
        if a_errs:
            print(f"SEMANTIC BLOCKED: {len(a_errs)} A-level error(s) — do NOT output an official file:")
            for e in a_errs:
                print(f"  - {e}")
            return 1
        if b_warns:
            print(f"Semantic OK with {len(b_warns)} warning(s) (B-level, allowed; add to diagnostics)")
        else:
            print("Semantic OK (no A/B findings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
