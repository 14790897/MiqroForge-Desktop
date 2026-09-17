"""Agent tool: declare user-facing result files (交付物) for the task (#1104).

The 「任务资产」panel classifies tracked files into 「结果文件」/「过程文件」.
Classification used to be a renderer-only extension whitelist, so a skill's
Markdown report (``*_report.md``) fell into 「过程文件」.  This tool lets the
agent explicitly declare the files the user actually wants to take away; the
ledger stores ``result: true`` and the panel shows them under 「结果文件」.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from miqi.agent.tools.base import Tool

# Injected into the system prompt wherever skill guidance is assembled — the
# explicit declaration is the only channel that overrides the frontend
# extension whitelist.
DECLARE_RESULT_FILES_INSTRUCTION = (
    "## 交付物登记（结果文件）\n"
    "当任务产出用户真正要带走的文件（分析报告、表格、图表、文档，尤其是 skill 或"
    "脚本跑出来的成品）时，在给出最终答复前**必须**调用一次 `declare_result_files` "
    "声明它们——这是「任务资产」面板「结果文件」区的唯一显式入口。只登记最终交付物，"
    "不要把中间产物、脚本、日志、缓存登记进来；目录类产出请逐个文件列出。"
).strip()


class DeclareResultFilesTool(Tool):
    """把本次任务的最终交付物登记到「任务资产」的「结果文件」区。"""

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
    def name(self) -> str:
        return "declare_result_files"

    @property
    def description(self) -> str:
        return (
            "把本次任务的最终交付物（结果文件）登记到右侧「任务资产」面板的"
            "「结果文件」区。交付类任务（尤其是运行 skill/脚本产出报告、表格、图表、"
            "文档后）给出最终答复前必须调用一次，声明用户真正要带走的文件；不要登记"
            "中间产物、脚本、日志。paths 支持工作区相对路径与绝对路径（含用户点明的"
            "工作区外输出目录）。登记不移动、不修改任何文件，只影响面板归类。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "结果文件路径列表（工作区相对路径或绝对路径）。"
                        "目录产出请逐个文件列出。"
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "可选。一句话交付说明（会显示在登记结果里）。",
                },
            },
            "required": ["paths"],
        }

    async def execute(
        self, paths: list[str] | str, note: str | None = None, **kwargs: Any
    ) -> str:
        from miqi.agent.tools.filesystem import (
            _effective_shared_roots,
            _persist_tracked_result_files,
            _resolve_path,
        )

        _session_key = kwargs.pop("_session_key", None)
        shared = _effective_shared_roots(
            self._shared_roots, kwargs.pop("_user_roots", None), self._allow_user_roots,
        )

        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list):
            return json.dumps(
                {"ok": False, "error": "paths 必须是字符串数组"}, ensure_ascii=False
            )

        # 去重保序（normalize 反斜杠，Windows 模型常给反斜杠）
        declared: list[str] = []
        seen: set[str] = set()
        for raw in paths:
            if not isinstance(raw, str) or not raw.strip():
                continue
            norm = raw.strip().replace("\\", "/")
            if norm not in seen:
                seen.add(norm)
                declared.append(norm)
        if not declared:
            return json.dumps(
                {"ok": False, "error": "paths 为空——没有可登记的文件"},
                ensure_ascii=False,
            )

        host_paths: list[str] = []
        missing: list[str] = []
        unverified: list[str] = []
        for norm in declared:
            try:
                resolved = _resolve_path(
                    norm,
                    self._workspace,
                    self._allowed_dir,
                    self._sandbox_manager,
                    shared_roots=shared,
                )
            except PermissionError:
                # 工作区外且未授权：仍登记（agent 明确点名），只是无法校验存在
                unverified.append(norm)
                host_paths.append(norm)
                continue
            resolved_str = str(resolved)
            if not resolved.exists():
                missing.append(norm)
            host_paths.append(resolved_str)

        marked = _persist_tracked_result_files(
            self._base_workspace or self._workspace,
            host_paths,
            _session_key,
            files_dir=self._workspace,
        )
        if marked == 0:
            # 台账写失败（缺会话上下文或持久化异常）必须如实返回失败——
            # 否则 agent 以为登记成功就收尾，面板上却什么都没有（CodeRabbit 复审）
            logger.warning(
                "declare_result_files: ledger write skipped (session_key={})",
                bool(_session_key),
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        "结果文件登记失败：会话台账不可写（缺少会话上下文或持久化异常）。"
                        "可在最终答复里直接给出文件路径，并告知用户结果文件区可能未更新。"
                    ),
                    "declared": declared,
                },
                ensure_ascii=False,
            )

        result: dict[str, Any] = {
            "ok": True,
            "declared": declared,
            "marked": marked,
        }
        if note:
            result["note"] = note
        if missing:
            result["missing"] = missing
            result["hint"] = "以下路径在磁盘上不存在，已登记但请核对：" + ", ".join(missing)
        if unverified:
            result["unverified"] = unverified
        return json.dumps(result, ensure_ascii=False)
