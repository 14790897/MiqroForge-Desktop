"""Runtime-owned tool registry factory.

Historical: Creates a fully populated ToolRegistry without depending on the
legacy AgentLoop. Replaces AgentLoop._register_default_tools() for
runtime-owned sessions.

Registration order is kept stable so model tool specs remain deterministic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from miqi.agent.tools.registry import ToolRegistry
from miqi.agent.tools.user_roots import _is_protected_extra_root
from miqi.paths import get_config_path

_log = logging.getLogger(__name__)


def _default_read_roots() -> list[Path]:
    """Read-whitelist expansion for the file tools (issue #864).

    Reads are widened to the user's home directory and, on Windows, every
    mounted drive root (whole-disk read-only).  Writes keep the narrow
    ``_shared_roots`` whitelist — this asymmetry matches Codex/Gemini/Cursor
    (workspace-write + workspace-external read-only).
    """
    import sys as _sys

    roots: list[Path] = []
    try:
        roots.append(Path.home())
    except Exception:
        pass
    if _sys.platform == "win32":
        for drive in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            p = Path(f"{drive}:\\")
            if p.exists():
                roots.append(p)
    else:
        roots.append(Path("/"))
    return roots


def _make_system_install_approver(*, resolver, sandbox_manager):
    """#854: 系统包安装授权确认卡 approver（once/always/deny）。

    - 复用 #646/#864 的 user-input 通道（同一 gate，桌面端渲染确认卡）
    - 卡片展示结构化信息：操作/命令/权限(root)/范围(WSL)/持久性/风险
      ——命令由拦截点传入**归一化后**的最终执行命令（显示 = 执行，
      #875 review P3-3），并预告批准后仍会经过命令安全审批（P3-5）
    - "允许本次"（once）= 调用级授权，不修改任何全局状态
    - "允许并记住"（always）走统一入口：**config 持久化在前，runtime
      属性在后**——持久化失败时 runtime 保持关闭（fail-closed 方向，
      #875 review P2），本次安装仍放行但 persist_failed=True 透出
    - 决策契约（#875 review F3）："once" / "always" / "deny"（用户拒绝
      或超时）/ "deny_no_channel"（无桌面通道——shell 据此给出设置页指引
      而非"去用刚拒绝的卡"）
    - 任何异常 / 未知选项 → deny（fail-closed）；卡等待有 120s 墙钟上限
      （含排队时间，#875 review F5：gate 的 per-slot 等待无超时）
    """
    import asyncio
    import logging

    logger = logging.getLogger(__name__)

    async def _approver(command: str) -> tuple[str, bool]:
        if resolver is None:
            return ("deny_no_channel", False)
        payload = {
            "title": "系统包安装授权",
            "message": (
                "AI 请求执行系统包安装：\n\n"
                f"操作：系统包安装\n"
                f"命令：{command}\n"
                "权限：root（WSL 发行版）\n"
                "范围：Windows + WSL 环境\n"
                "持久性：仅「允许并记住」跨会话持续生效；「允许本次安装」仅本次有效\n"
                "风险：会修改系统软件环境\n"
                "注意：批准后命令仍会经过命令安全审批，可能再次弹出确认"
            ),
            "choices": [
                {"id": "allow_once", "label": "允许本次安装"},
                {"id": "allow_always", "label": "允许并记住（开启开关）"},
                {"id": "deny", "label": "拒绝"},
            ],
            "timeout_seconds": 120,
        }
        try:
            # 墙钟上限：gate 内排队（同 turn 已有其他卡）不计入其自身超时，
            # 这里整体兜底（#875 review F5）。
            gate_result = await asyncio.wait_for(resolver(payload), timeout=120)
        except TimeoutError:
            logger.warning("system install card wait timed out — deny")
            return ("deny", False)
        except Exception as exc:  # noqa: BLE001 - fail-closed
            logger.warning("system install card resolver failed: %s — deny", exc)
            return ("deny", False)
        if gate_result.get("status") != "submitted":
            # 无桌面通道（emitter 未注册）→ 卡从未出现——shell 应给设置页
            # 指引而非"去用刚拒绝的卡"（#875 review F3）。
            reason = str(gate_result.get("reason") or "")
            if "no user-input channel" in reason:
                return ("deny_no_channel", False)
            return ("deny", False)
        choice_id = str((gate_result.get("answers") or {}).get("choice_id") or "")
        if choice_id == "allow_always":
            # 统一入口（外部审阅 #854）：**config 持久化在前，runtime 在后**。
            # 先开 runtime 会让「保存失败」时权限在本会话仍然生效（fail-open），
            # 与本 PR 的 fail-closed 原则矛盾（#875 review P2）。
            # 持久化走 loader.update_config_field：共享锁 + legacy 路径回退 +
            # 迁移（#875 review F1/F2——裸 get_config_path() 会丢 legacy 配置、
            # 无锁并发写会互相覆盖）。
            from miqi.config.loader import update_config_field

            try:
                persist_failed = not update_config_field(
                    lambda cfg: setattr(
                        cfg.tools.sandbox, "allow_system_installs", True,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 失败仅透出，不放行 runtime
                logger.error("system install allow persist error: %s", exc)
                persist_failed = True
            # 持久化成功才开 runtime（fail-closed 方向）
            if not persist_failed and sandbox_manager is not None:
                try:
                    sandbox_manager.allow_system_installs = True
                except Exception as exc:  # noqa: BLE001 - config 已持久化，重启自愈
                    logger.warning(
                        "system install allow: runtime update failed (config "
                        "persisted, restart will apply): %s", exc,
                    )
            # (decision, persist_failed)：shell 拦截点据此向用户透出
            # "本次已放行但未记住，重启后需重新授权"（#875 review P2）
            return ("always", persist_failed)
        if choice_id == "allow_once":
            return ("once", False)
        return ("deny", False)

    return _approver


def _make_extra_root_persister():
    """Build an async callback that appends a directory to ``tools.extra_roots``.

    Used by the write authorization card's [本目录不再询问] choice (issue #864).
    Persisting is best-effort and guarded: a protected path is never added, and
    any failure must not fail the write that already succeeded.

    The read-modify-write goes through :func:`loader.update_config_field`
    (shared lock + fresh disk read, #875 review F12) — ``load_config``
    returns a 5-second-cached ``Config``, so a cached read could clobber a
    concurrent config change, and per-instance locks don't serialize
    cross-session writers.
    """
    from miqi.agent.tools.user_roots import _is_protected_extra_root
    from miqi.config.loader import update_config_field

    async def persist(root: Path) -> None:
        resolved = Path(root).expanduser().resolve(strict=False)

        def _mutate(cfg) -> None:
            if _is_protected_extra_root(resolved, cfg.workspace_path):
                _log.warning("extra_root persister: refusing protected path %s", resolved)
                return
            tools_cfg = getattr(cfg, "tools", None)
            extra = list(getattr(tools_cfg, "extra_roots", []) or [])
            root_str = str(resolved)
            if root_str not in extra:
                extra.append(root_str)
                tools_cfg.extra_roots = extra

        # 失败静默（best-effort）：读不到配置不覆盖（update_config_field 返回
        # False，不抛出）——单个 root 不值得毁掉用户配置。
        update_config_field(_mutate)

    return persist


def _resolve_default_shared_dir(workspace: Path, sub: str) -> Path | None:
    """Create/validate a workspace-owned shared root.

    Returns the resolved directory, or ``None`` when the directory is (or
    points through a symlink to) a path outside the workspace.  External
    skill/memory locations must be authorized explicitly with
    ``tools.extra_roots``.
    """
    shared_dir = workspace / sub
    try:
        shared_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _log.warning("Skipping shared root %s: cannot create directory", shared_dir)
        return None
    resolved = shared_dir.resolve()
    try:
        resolved.relative_to(workspace.resolve())
    except ValueError:
        _log.warning("Skipping shared root %s: resolves outside workspace", shared_dir)
        return None
    return resolved


def create_runtime_tool_registry(
    *,
    config: Any,
    workspace: Path,
    session_id: str = "",
    provider: Any = None,
    bus: Any = None,
    approval_callback: Any = None,
    sandbox_manager: Any = None,
    cron_service: Any = None,
    subagent_manager: Any = None,
    memory_store: Any = None,
    trace_store: Any = None,
    session_manager: Any = None,
    plan_tracker: Any = None,
) -> ToolRegistry:
    """Create the runtime-owned ToolRegistry.

    Historical: This replaces RuntimeServices' dependency on the legacy
    AgentLoop._setup_tools().
    Registration order is kept stable so model tool specs remain deterministic.

    Args:
        config: MiQi Config object (config.schema.Config).
        workspace: Session workspace directory.
        session_id: Namespaced session key (``<client_id>:<session_key>``)
            used to derive the per-session files dir.  Falls back to
            ``config._session_key`` for legacy callers.
        provider: LLM provider (needed for SubagentManager if spawn is desired).
        bus: MessageBus (needed for MessageTool).
        approval_callback: Optional approval callback for ExecTool.
        sandbox_manager: Optional sandbox manager for filesystem isolation.
        cron_service: Optional CronService for cron tool.
        subagent_manager: Optional SubagentManager for spawn tool.
        memory_store: Optional MemoryStore for memory tool.
        trace_store: Optional TraceStore for task_begin/end/trace_search tools.
        session_manager: Optional SessionManager for session_search tool.
        plan_tracker: Optional PlanTracker for plan_create/plan_update tools.

    Returns:
        ToolRegistry populated with the runtime's default tool set.
    """

    defaults = getattr(config, "agents", None)
    defaults = getattr(defaults, "defaults", None) if defaults is not None else None

    # Resolve session-key-dependent paths (Historical: mirrors the legacy
    # AgentLoop._register_default_tools)
    _session_key = session_id or getattr(config, "_session_key", "") or ""
    _snap_dir: Path | None = None
    _work_dir: Path | None = None
    _write_workspace: Path = workspace

    session_config = getattr(defaults, "session_config", None) if defaults is not None else None
    if not session_config:
        session_config = getattr(config, "agents", None)
        session_config = getattr(session_config, "sessions", None) if session_config is not None else None

    if _session_key:
        from miqi.agent.tools.filesystem import _session_files_dir_key

        # Strip the client_id prefix (first colon segment) so the directory
        # key matches what files.read / attachment saving use on disk
        # (sessions/<safe_key>/files) — the naive replace(":", "_") over the
        # full namespaced key produced a divergent directory.
        safe_key = _session_files_dir_key(_session_key)
        _snap_dir = workspace / "sessions" / safe_key / "snapshots"
        _snap_dir.mkdir(parents=True, exist_ok=True)
        # A custom (non-default) workspace is the project directory the user
        # picked in the workspace picker — file tools must operate directly
        # on it, NOT nested under sessions/<key>/files (that would hide the
        # project's own files from the AI). Only the default global workspace
        # gets per-session files isolation.
        from miqi.agent.tools.filesystem import _is_default_workspace

        is_default_ws = _is_default_workspace(workspace)
        if (
            is_default_ws
            and session_config is not None
            and getattr(session_config, "session_workspace_enabled", False)
        ):
            _work_dir = workspace / "sessions" / safe_key / "files"
            _work_dir.mkdir(parents=True, exist_ok=True)
            from miqi.utils.helpers import ensure_sessions_gitignored

            ensure_sessions_gitignored(workspace)

    if _work_dir is not None:
        _write_workspace = _work_dir

    # Host-global shared roots the system prompt legitimately directs the
    # agent to read/write (issue #516): memory/ (MEMORY.md, LTM_SNAPSHOT, ...)
    # and skills/ (<name>/SKILL.md), plus the .skills/ layout used by some
    # clients (issue #567).  These live outside the per-session files dir,
    # so without them the WSL sandbox containment check wrongly rejects
    # memory/skill paths.  Per-session isolation is unaffected: only these
    # shared dirs are whitelisted, never another session's files.
    tools_cfg = getattr(config, "tools", None)
    _shared_roots: list[Path] = []
    # Workspace root itself (issue #689): the agent legitimately needs to
    # read/write files directly under ~/.miqi/workspace/ (e.g. reports,
    # test files).  Cross-session access stays blocked by the per-session
    # isolation check in filesystem.py — only this root is whitelisted,
    # never another session's files.
    _shared_roots.append(workspace.resolve())
    for _sub in ("memory", "skills", ".skills"):
        _shared_dir = _resolve_default_shared_dir(workspace, _sub)
        if _shared_dir is not None:
            _shared_roots.append(_shared_dir)

    # User-configured extra roots (issue #567): explicit authorization for
    # directories outside the workspace, e.g. C:\Users\<user>\Desktop\work.
    _extra_roots_cfg = getattr(tools_cfg, "extra_roots", []) if tools_cfg is not None else []
    for _raw_root in _extra_roots_cfg:
        if not isinstance(_raw_root, str) or not _raw_root.strip():
            continue
        _root = Path(_raw_root).expanduser().resolve(strict=False)
        if _is_protected_extra_root(_root, workspace):
            _log.warning(
                "Ignoring tools.extra_roots entry %s: covers protected config/session paths",
                _root,
            )
            continue
        _shared_roots.append(_root)

    # Auto-sensed user-mentioned output dirs (issue #821): the KUN runtime
    # injects them per tool call (``_user_roots``); this flag gates the
    # tools' acceptance of that injection.
    _auto_user_dirs = (
        bool(getattr(tools_cfg, "auto_user_dirs", True))
        if tools_cfg is not None else True
    )

    # Read-only whitelist for the host config file (issue #553): agents may
    # inspect settings, but write/edit/patch tools keep rejecting it so the
    # config (API keys, model setup) can never be tampered with.
    # Issue #864: reads are further widened to home + whole disk (read/write
    # asymmetry), while writes keep the narrow _shared_roots whitelist below.
    _read_shared_roots = [*_shared_roots, get_config_path(), *_default_read_roots()]

    # Resolve config sections
    restrict_to_workspace = getattr(tools_cfg, "restrict_to_workspace", False) if tools_cfg is not None else False

    exec_cfg = getattr(tools_cfg, "exec", None) if tools_cfg is not None else None
    web_cfg = getattr(tools_cfg, "web", None) if tools_cfg is not None else None
    paper_cfg = getattr(tools_cfg, "papers", None) if tools_cfg is not None else None

    allowed_dir = workspace if restrict_to_workspace else None

    _sbm = sandbox_manager

    # ── Core tools (always registered) ──────────────────────────────────
    from miqi.agent.tools.apply_patch import ApplyPatchTool
    from miqi.agent.tools.filesystem import (
        EditFileTool,
        ListDirTool,
        ReadFileTool,
        WriteFileTool,
    )
    from miqi.agent.tools.shell import ExecTool

    registry = ToolRegistry()

    # 0. AI-initiated user confirmation (issue #646): registered for schema
    #    exposure; execution blocks on the shared user-input gate so the
    #    desktop can render the confirm card (legacy bridge path).
    from miqi.agent.tools.ask_user_confirm import AskUserConfirmCardTool
    from miqi.agent.user_input_resolver import make_resolver

    registry.register(AskUserConfirmCardTool(resolver=make_resolver()))

    # Write authorization card (issue #864): the file write tools pop a
    # confirm card when a target escapes the write whitelist.  They share the
    # same user-input gate as ask_user_confirm_card; "本目录不再询问" persists
    # the directory into tools.extra_roots via the persister.
    # The approval-bypass switches (approvals.bypass_all /
    # approvals.bypass_file_write_approval) also skip the card — the target
    # dir is granted session-scoped but never persisted to extra_roots.
    _write_resolver = make_resolver()
    _persist_extra_root = _make_extra_root_persister()
    _bypass = getattr(config, "effective_approval_bypass", None)
    _bypass = _bypass() if callable(_bypass) else _bypass
    _bypass_approval = bool(
        getattr(_bypass, "bypasses_category", None)
        and _bypass.bypasses_category("file_write")
    )

    # 1. Filesystem tools
    registry.register(
        ListDirTool(
            workspace=workspace,
            allowed_dir=allowed_dir,
            sandbox_manager=_sbm,
            shared_roots=_read_shared_roots,
            allow_user_roots=_auto_user_dirs,
        )
    )
    # ReadFileTool additionally gets the per-session files dir so reads of
    # files the agent wrote (redirected into sessions/<key>/files) fall back
    # to the session dir when they miss at the root.
    registry.register(
        ReadFileTool(
            workspace=workspace,
            allowed_dir=allowed_dir,
            sandbox_manager=_sbm,
            shared_roots=_read_shared_roots,
            session_files_dir=_work_dir,
            allow_user_roots=_auto_user_dirs,
        )
    )
    registry.register(
        WriteFileTool(
            workspace=_write_workspace,
            allowed_dir=allowed_dir,
            snapshot_dir=_snap_dir,
            sandbox_manager=_sbm,
            shared_roots=_shared_roots,
            session_files_dir=_work_dir,
            base_workspace=workspace,
            allow_user_roots=_auto_user_dirs,
            write_resolver=_write_resolver,
            persist_extra_root=_persist_extra_root,
            bypass_approval=_bypass_approval,
        )
    )
    registry.register(
        EditFileTool(
            workspace=_write_workspace,
            allowed_dir=allowed_dir,
            snapshot_dir=_snap_dir,
            sandbox_manager=_sbm,
            shared_roots=_shared_roots,
            session_files_dir=_work_dir,
            base_workspace=workspace,
            allow_user_roots=_auto_user_dirs,
            write_resolver=_write_resolver,
            persist_extra_root=_persist_extra_root,
            bypass_approval=_bypass_approval,
        )
    )
    registry.register(
        ApplyPatchTool(
            workspace=_write_workspace,
            allowed_dir=allowed_dir,
            snapshot_dir=_snap_dir,
            sandbox_manager=_sbm,
            shared_roots=_shared_roots,
            session_files_dir=_work_dir,
            base_workspace=workspace,
            allow_user_roots=_auto_user_dirs,
            write_resolver=_write_resolver,
            persist_extra_root=_persist_extra_root,
            bypass_approval=_bypass_approval,
        )
    )

    # 2. Exec tool
    registry.register(
        ExecTool(
            working_dir=str(_work_dir or workspace),
            timeout=getattr(exec_cfg, "timeout", 60) if exec_cfg is not None else 60,
            max_timeout=getattr(exec_cfg, "max_timeout", 1800) if exec_cfg is not None else 1800,
            idle_timeout=getattr(exec_cfg, "idle_timeout", 90) if exec_cfg is not None else 90,
            heartbeat_interval=getattr(exec_cfg, "heartbeat_interval", 30) if exec_cfg is not None else 30,
            kill_grace_seconds=getattr(exec_cfg, "kill_grace_seconds", 5) if exec_cfg is not None else 5,
            restrict_to_workspace=restrict_to_workspace,
            env_passthrough=list(getattr(exec_cfg, "env_passthrough", [])) if exec_cfg is not None else [],
            approval_callback=approval_callback,
            sandbox_manager=_sbm,
            # #854: 系统包安装授权确认卡——关闭状态下拦截点弹卡（once/always/deny）。
            # "允许并记住"走统一入口：runtime 属性 + config 持久化（外部审阅 #854）。
            system_install_approver=_make_system_install_approver(
                resolver=_write_resolver, sandbox_manager=_sbm,
            ),
        )
    )

    # 3. Web tools
    from miqi.agent.tools.web import WebFetchTool, WebSearchTool

    if web_cfg is not None:
        search_cfg = getattr(web_cfg, "search", None)
        fetch_cfg = getattr(web_cfg, "fetch", None)
    else:
        search_cfg = None
        fetch_cfg = None

    # DeepSeek 联网搜索复用 LLM key（零配置；仅官方 base 生效）——从 providers 配置读取
    _ds_cfg = getattr(getattr(config, "providers", None), "deepseek", None)
    deepseek_api_key = getattr(_ds_cfg, "api_key", "") if _ds_cfg is not None else ""
    deepseek_api_base = getattr(_ds_cfg, "api_base", "") if _ds_cfg is not None else ""
    # 当前对话模型名（"对应模型的联网搜索"：如 DeepSeek 模型 → DeepSeek 搜索）。
    # 用回调每次读取——配置改动（换模型）即时生效，不在注册表构建时冻结（外部审阅 #844）
    def _current_model_name() -> str:
        _defaults = getattr(getattr(config, "agents", None), "defaults", None)
        return getattr(_defaults, "model", "") if _defaults is not None else ""

    registry.register(
        WebSearchTool(
            provider=getattr(search_cfg, "provider", "auto") if search_cfg is not None else "auto",
            api_key=getattr(search_cfg, "api_key", None) if search_cfg is not None else None,
            tavily_api_key=getattr(search_cfg, "tavily_api_key", None) if search_cfg is not None else None,
            brave_api_key=getattr(search_cfg, "brave_api_key", None) if search_cfg is not None else None,
            deepseek_api_key=deepseek_api_key or None,
            deepseek_api_base=deepseek_api_base or "https://api.deepseek.com",
            model_provider=_current_model_name,
            max_results=getattr(search_cfg, "max_results", 5) if search_cfg is not None else 5,
        )
    )
    registry.register(
        WebFetchTool(
            provider=getattr(fetch_cfg, "provider", "builtin") if fetch_cfg is not None else "builtin",
            ollama_api_key=getattr(fetch_cfg, "ollama_api_key", None) if fetch_cfg is not None else None,
            ollama_api_base=getattr(fetch_cfg, "ollama_api_base", None) if fetch_cfg is not None else None,
        )
    )

    # 4. Paper tools
    from miqi.agent.tools.papers import PaperDownloadTool, PaperGetTool, PaperSearchTool

    paper_provider = getattr(paper_cfg, "provider", "hybrid") if paper_cfg is not None else "hybrid"
    paper_api_key = getattr(paper_cfg, "semantic_scholar_api_key", None) if paper_cfg is not None else None
    paper_core_key = getattr(paper_cfg, "core_api_key", None) if paper_cfg is not None else None
    paper_timeout = getattr(paper_cfg, "timeout_seconds", 20) if paper_cfg is not None else 20
    paper_default_limit = getattr(paper_cfg, "default_limit", 8) if paper_cfg is not None else 8
    paper_max_limit = getattr(paper_cfg, "max_limit", 20) if paper_cfg is not None else 20

    registry.register(
        PaperSearchTool(
            provider=paper_provider,
            semantic_scholar_api_key=paper_api_key,
            timeout_seconds=paper_timeout,
            default_limit=paper_default_limit,
            max_limit=paper_max_limit,
        )
    )
    registry.register(
        PaperGetTool(
            provider=paper_provider,
            semantic_scholar_api_key=paper_api_key,
            timeout_seconds=paper_timeout,
        )
    )
    registry.register(
        PaperDownloadTool(
            workspace=workspace,
            provider=paper_provider,
            semantic_scholar_api_key=paper_api_key,
            core_api_key=paper_core_key,
            timeout_seconds=paper_timeout,
        )
    )

    # 5. Skill manage tool
    from miqi.agent.tools.skill_manage import SkillManageTool

    registry.register(SkillManageTool(workspace=workspace))

    # 6. Office document tools
    from miqi.documents.docx_tool import CreateDocxTool, DocxReadTool, DocxWriteTool, EditDocxTool
    from miqi.documents.pptx_tool import CreatePptxTool, PptxReadTool, PptxWriteTool
    from miqi.documents.xlsx_tool import AppendXlsxTool, CreateXlsxTool, XlsxReadTool, XlsxWriteTool

    registry.register(DocxReadTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    # Office write tools always write inside the workspace, independently
    # of the `restrict_to_workspace` config (which only controls
    # WriteFileTool / EditFileTool).
    registry.register(CreateDocxTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(DocxWriteTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(EditDocxTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(PptxReadTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(CreatePptxTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(PptxWriteTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(XlsxReadTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(CreateXlsxTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(XlsxWriteTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(AppendXlsxTool(workspace=_write_workspace, allowed_dir=_write_workspace))

    # PDF tools (RAG + creation)
    from miqi.documents.pdf_create_tool import CreatePdfTool, PdfWriteTool
    from miqi.documents.pdf_read_tool import PdfReadTool

    registry.register(PdfReadTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(CreatePdfTool(workspace=_write_workspace, allowed_dir=_write_workspace))
    registry.register(PdfWriteTool(workspace=_write_workspace, allowed_dir=_write_workspace))

    # ── Optional tools (require external dependencies) ─────────────────

    # 7. Memory tool (requires MemoryStore)
    if memory_store is not None:
        from miqi.agent.tools.memory import MemoryTool

        registry.register(MemoryTool(memory_store=memory_store))

    # 8. Task trace tools (require TraceStore)
    if trace_store is not None:
        from miqi.agent.tools.task_trace import TaskBeginTool, TaskEndTool, TraceSearchTool

        registry.register(TaskBeginTool(trace_store=trace_store))
        registry.register(TaskEndTool(trace_store=trace_store))
        registry.register(TraceSearchTool(trace_store=trace_store))

    # 9. Session search tool (requires MemoryStore + SessionManager)
    if memory_store is not None and session_manager is not None:
        from miqi.agent.tools.session_search import SessionSearchTool

        registry.register(SessionSearchTool(memory=memory_store, session_manager=session_manager))

    # 10. Message tool (requires MessageBus)
    if bus is not None:
        from miqi.agent.tools.message import MessageTool

        registry.register(MessageTool(send_callback=bus.publish_outbound))

    # 11. Spawn tool — always registered (Phase 13 removed the legacy
    # SubagentManager fallback; the tool executes via AgentControl, which
    # RuntimeServices wires in after the registry is built).  Gating it on
    # subagent_manager silently dropped it from every runtime session —
    # the main agent's available_tools advertised "spawn" but the registry
    # had no implementation, so model calls failed with "Tool not found"
    # (issue #246).
    from miqi.agent.tools.spawn import SpawnTool

    registry.register(
        SpawnTool(
            manager=subagent_manager,
            agent_control=None,  # Wired later by RuntimeServices
            event_emitter=None,
        )
    )

    # 12. Cron tool (requires CronService)
    if cron_service is not None:
        from miqi.agent.tools.cron import CronTool

        registry.register(CronTool(cron_service))

    # 13. Plan tools (require PlanTracker)
    if plan_tracker is not None:
        from miqi.plan.plan_tool import PlanCreateTool, PlanUpdateTool

        registry.register(PlanCreateTool(tracker=plan_tracker))
        registry.register(PlanUpdateTool(tracker=plan_tracker))

    # 14. Graph render tool (issue #715): 解析 skill 产物 step-graph.json /
    #     data-graph.json 渲染流程图/对偶图。零依赖，始终注册。
    from miqi.agent.tools.graph_render import GraphRenderTool

    registry.register(
        GraphRenderTool(
            # 与写工具一致：相对路径/out_dir 解析到 session 工作区
            # （_write_workspace），绝对 run 目录路径不受影响（CodeRabbit #761）
            workspace=_write_workspace,
            allowed_dir=allowed_dir,
            sandbox_manager=_sbm,
            shared_roots=_shared_roots,
            base_workspace=workspace,
            allow_user_roots=_auto_user_dirs,
        )
    )

    return registry
