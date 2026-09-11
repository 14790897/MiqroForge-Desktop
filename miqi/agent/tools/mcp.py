"""MCP client: connects to MCP servers and wraps their tools for runtime use."""

import asyncio
import re
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from miqi.agent.tools.base import Tool
from miqi.agent.tools.mcp_download_sink import (
    DOWNLOAD_TOOL_GUIDANCE,
    DownloadError,
    DownloadIoError,
    DownloadSink,
    is_download_tool,
)
from miqi.agent.tools.registry import ToolRegistry

_JOB_ID_PATTERNS = [
    re.compile(r"(?i)(?:submitted\s+batch\s+job|job\s+id|jobid|job_id|batch\s+job)[^\d]{0,12}(\d{3,})"),
    re.compile(r"(?i)\bslurm-(\d{3,})\b"),
]


def _extract_job_state(text: str) -> str | None:
    """从 MCP 工具输出中尽力提取作业 state（优先 JSON 解析，退化为文本匹配）。"""
    try:
        import json as _json

        data = _json.loads(text or "")
        if isinstance(data, dict) and data.get("state"):
            return str(data["state"])
    except Exception:
        pass
    match = re.search(
        r"(?i)\bstate[\"'\s:=]+(PENDING|RUNNING|COMPLETED|FAILED|CANCELLED|TIMEOUT|UNKNOWN)\b",
        text or "",
    )
    return match.group(1) if match else None


# 可扣费状态（作业已实际占用集群资源）：RUNNING=运行中；COMPLETED/FAILED/
# TIMEOUT=已执行到终态。快作业常在一次轮询间隔内从 PENDING 直接到
# COMPLETED，永远观测不到 RUNNING——只认 RUNNING 会漏扣（2026-09-11 实测：
# 自然提示词提交的 sleep 秒级作业跑完不扣分）。CANCELLED 可能是排队中被取消
# （未运行），不计费。
_CHARGEABLE_JOB_STATES = frozenset({"RUNNING", "COMPLETED", "FAILED", "TIMEOUT"})


def _extract_job_id(text: str) -> str | None:
    """从 MCP 工具输出中尽力提取 SLURM 作业 ID（无则 None）。"""
    for pattern in _JOB_ID_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return None


_SENSITIVE_ARG_KEYS = ("password", "passwd", "token", "secret", "api_key", "apikey", "key", "credential")


_CRED_ASSIGN_RE = re.compile(
    r'(?i)(export\s+)?([A-Z0-9_]*?(?:TOKEN|PASS(?:WORD|WD|PHRASE)?|SECRET|(?:PRIVATE|ACCESS|API)_?KEY|CREDENTIAL)[A-Z0-9_]*)\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s"\']+)'
)

_MAX_REDACT_DEPTH = 4


def _redact_value(value: Any, depth: int = 0) -> Any:
    """递归脱敏：嵌套 dict/list 逐层检查敏感键；文本内的凭据赋值
    （export API_TOKEN=... 等）与凭据形态长随机串一并脱敏。"""
    if depth > _MAX_REDACT_DEPTH:
        return "[REDACTED]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(s in str(key).lower() for s in _SENSITIVE_ARG_KEYS):
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = _redact_value(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        if _looks_like_credential(value):
            return "[REDACTED]"
        return _CRED_ASSIGN_RE.sub(lambda m: m.group(1) + m.group(2) + "=[REDACTED]", value)
    return value


def _summarize_args(kwargs: dict[str, Any]) -> str:
    """参数摘要（memo 用）：递归脱敏 + 截断，最多 200 字符。

    脚本内容/参数会随 memo 进入平台扣费记录与本地历史，密钥类字段、
    嵌套对象与脚本文本里的凭据赋值都必须先脱敏（CWE-200/201）。
    """
    try:
        import json as _json

        raw = _json.dumps(
            _redact_value(kwargs), ensure_ascii=False, default=str
        )
    except Exception:
        raw = str(kwargs)
    if len(raw) > 200:
        raw = raw[:197] + "..."
    return raw


def _looks_like_credential(value: str) -> bool:
    """启发式：长随机串（token/key 形态）视为凭据并脱敏。"""
    if len(value) < 24:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-+/=.]{24,}", value))


class MCPToolWrapper(Tool):
    """Wrap a single MCP server tool as a native runtime Tool."""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 30,
                 progress_interval: int = 15, base_workspace=None):
        self._session = session
        self._server_name = server_name
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout
        self._progress_interval = progress_interval
        # #975 下载类工具分类：构造期按 (server_name, tool_name) 判定（执行期零查找）。
        # 命中后 description 追加 Artifact Boundary 指引（随工具定义进模型，不动
        # system prompt），并装配 sink——base_workspace 与 MCP 连接同源
        # （config.workspace_path，见 _connect_one_server / runtime/session.py）。
        self._is_download_tool = is_download_tool(server_name, tool_def.name)
        self._base_workspace = base_workspace
        if self._is_download_tool:
            self._description = self._description + DOWNLOAD_TOOL_GUIDANCE
            self._download_sink = (
                DownloadSink(base_workspace) if base_workspace is not None else None
            )

    @property
    def is_download_tool(self) -> bool:
        """构造期分类结果（供测试/审计检查）。"""
        return self._is_download_tool

    @property
    def execution_timeout(self) -> float | None:
        """Expose per-MCP-server toolTimeout so ToolRegistry defers to us."""
        # Return a value slightly larger than our own internal wait_for so
        # the outer wrapper never fires before the inner one does.
        return float(self._tool_timeout) + 5

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, *, _on_progress=None, **kwargs: Any) -> str:
        # 运行上下文注入（orchestrator 对 mcp_ 工具注入；不传给 MCP 服务端）。
        session_key = str(kwargs.pop("_session_key", "") or "")
        turn_id = str(kwargs.pop("_turn_id", "") or "")
        tool_call_id = str(kwargs.pop("_tool_call_id", "") or "")

        # The mcp SDK calls progress_callback as:
        #   await progress_callback(progress_token, progress, total)
        # (SDK added progress_token as the first arg in a recent version)
        progress_callback = None
        if _on_progress:
            async def progress_callback(progress_token: Any, progress: float, total: float | None) -> None:
                await _on_progress(progress, total or 0)

        # Heartbeat: periodically notify the user that a long-running
        # MCP tool is still executing, even if the server sends no
        # progress events.
        heartbeat_task: asyncio.Task | None = None
        if _on_progress and self._progress_interval > 0:
            async def _heartbeat():
                elapsed = 0
                try:
                    while True:
                        await asyncio.sleep(self._progress_interval)
                        elapsed += self._progress_interval
                        await _on_progress(
                            elapsed,
                            0,  # total unknown
                            heartbeat=True,
                        )
                except asyncio.CancelledError:
                    pass
            heartbeat_task = asyncio.create_task(_heartbeat())

        try:
            result = await asyncio.wait_for(
                self._session.call_tool(
                    self._original_name,
                    arguments=kwargs,
                    progress_callback=progress_callback,
                ),
                timeout=self._tool_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MCP tool '{}' timed out after {}s", self._name, self._tool_timeout
            )
            return f"(MCP tool call timed out after {self._tool_timeout}s)"
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        # ── #975 Artifact Boundary 分支 ─────────────────────────────────────
        # 下载类工具：billing 副作用先执行（与 materialization 解耦，杜绝专用
        # 分支早退旁路 #927 计费），随后内容由 sink 消费——base64 绝不进最终
        # output，也不为 billing 拼接全量文本（#988 评审 P2b：billing 只需
        # state/job_id，逐块扫描即可，不为 2.1MB base64 制造第二份字符串拷贝）；
        # 任何异常消化为错误 JSON 文本，绝不上抛 orchestrator。
        if self._is_download_tool:
            await self._handle_slurm_billing(
                session_key=session_key,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                kwargs=kwargs,
                blocks=result.content,
            )
            return await self._materialize_download(
                result=result,
                session_key=session_key,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                request_kwargs=kwargs,
            )

        output = self._join_text_blocks(result.content)
        await self._handle_slurm_billing(
            session_key=session_key,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            kwargs=kwargs,
            blocks=result.content,
        )
        return output

    def _join_text_blocks(self, blocks: list) -> str:
        """把 MCP 响应块拼成文本——普通工具返回 / billing 解析视图共用。

        **下载工具的内容解析不经此函数**（sink 直接消费 ContentBlock，
        保留块结构；多块 JSON/错误块/分片块不会被 join 破坏）。
        """
        from mcp import types

        parts = []
        for block in blocks:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts) or "(no output)"

    async def _handle_slurm_billing(
        self,
        *,
        session_key: str,
        turn_id: str,
        tool_call_id: str,
        kwargs: dict[str, Any],
        blocks: list,
    ) -> None:
        """Slurm 作业计费触发（issue #927，2026-09-04 产品确认）——**纯副作用**。

        作业进入可扣费状态时由 Desktop 发起扣费（10 分/次）：submit_slurm_job /
        check_job_status 的返回里 state ∈ {RUNNING, COMPLETED, FAILED, TIMEOUT}
        （即作业已实际占用集群资源——快作业常在两次轮询间从 PENDING 直接到终态，
        永远观测不到 RUNNING）即触发一次 fire-and-forget 扣费事件（Desktop 按
        作业 ID 去重）。作业已运行，扣费失败（如余额不足）不阻止作业，由 Desktop
        记录到扣费历史并提示。CANCELLED（可能排队中被取消、未运行）不计费。

        只负责 inspect/dedupe/emit/mark，**不决定调用方最终返回什么**
        （v6.2 R4：与 download materialization 解耦，早退不会旁路计费）。

        **输入是 ContentBlock 而非拼接字符串**（#988 评审 P2b）：billing 只
        关心 state/job_id，逐块扫描即可——下载类大响应绝不为计费制造一份
        全量文本副本（2.1MB+ base64 的 join 就是无谓的内存峰值）。
        """
        from mcp import types

        from miqi.agent.billing_resolver import (
            billing_charge_emitter_for,
            is_slurm_server,
        )

        if not (session_key and is_slurm_server(self._server_name)):
            return
        texts = [
            block.text
            for block in blocks or []
            if isinstance(block, types.TextContent) and block.text
        ]
        for text in texts:
            job_state = _extract_job_state(text)
            if not job_state or job_state.upper() not in _CHARGEABLE_JOB_STATES:
                continue
            # 响应里的 job_id 优先；check_job_status 的响应可能只有
            # state（作业 ID 在请求参数里），回退用请求参数保证去重键。
            job_id = _extract_job_id(text) or str(
                kwargs.get("job_id") or kwargs.get("jobId") or ""
            )
            # 无稳定作业 ID 时不发计费事件：空 job_id 无法去重，
            # 轮询每次 RUNNING 都会再扣一次（数据完整性）。
            if not job_id:
                return
            from miqi.agent.billing_resolver import job_reported, mark_job_reported

            # 轮询会反复观察 RUNNING：同一会话同一服务器同一作业只
            # 发一次。先发事件、送达成功才标记——发射失败不标记，
            # 下一次 RUNNING 轮询重试（Desktop 侧去重兜底，重复送达无害）。
            if job_reported(session_key, self._server_name, job_id):
                return
            emitter = billing_charge_emitter_for(session_key)
            if emitter is None:
                return
            import uuid as _uuid

            payload = {
                "charge_id": _uuid.uuid4().hex,
                "job_id": job_id or "",
                "state": job_state,
                "server_name": self._server_name,
                "tool_name": self._original_name,
                "args_summary": _summarize_args(kwargs),
                "session_key": session_key,
                "turn_id": turn_id,
                "tool_call_id": tool_call_id,
            }
            delivered = False
            try:
                result = emitter(payload)
                if asyncio.iscoroutine(result):
                    result = await result
                delivered = bool(result)
            except Exception:
                logger.exception(
                    "billing: 扣费事件发送失败（不标记，下次轮询重试）"
                )
            if delivered:
                mark_job_reported(session_key, self._server_name, job_id)
            return  # 一个可扣费状态处理完即可（语义同旧 join 后单次处理）

    async def _materialize_download(
        self,
        *,
        result,
        session_key: str,
        turn_id: str,
        tool_call_id: str,
        request_kwargs: dict[str, Any],
    ) -> str:
        """#975 下载分支：sink 落盘 → 摘要 JSON；异常一律消化为错误 JSON。

        sink 抛出的 DownloadError / OSError 在这里转为安全文本返回，绝不带
        traceback/repr/raw 内容上抛——否则会落入 orchestrator 的
        ``[Analyze the error above]`` 套壳与 UI 清洗（语义污染）。
        """
        sink = self._download_sink if self._is_download_tool else None
        if sink is None:
            return DownloadIoError(
                "下载失败：会话工作区不可用（MCP wrapper 未装配 base_workspace）。"
            ).to_model_text()
        try:
            artifact = await sink.materialize(
                result=result,
                session_key=session_key,
                server_name=self._server_name,
                tool_name=self._original_name,
                request_kwargs=request_kwargs,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
            )
            return artifact.to_model_text()
        except DownloadError as exc:
            # 结构化错误 JSON——只含 code/message/retryable，无内容、无 payload。
            return exc.to_model_text()
        except OSError:
            # resolve_downloads_dir/_sweep_stale_once 在 to_thread 之前执行，
            # 其文件系统失败也必须消化为结构化错误（CodeRabbit 06-48 Major：
            # 否则裸异常会漏到 orchestrator，违反"错误只以 download_error 出现"）。
            return DownloadIoError().to_model_text()


class MCPGatewayTool(Tool):
    """Entry-point tool for a lazy-loaded MCP server.

    Instead of registering all tools upfront (which inflates the tool list
    sent to the LLM on every call), a single gateway tool per server is
    registered.  When the LLM selects the gateway, all real tools for that
    server are injected into the ToolRegistry for the remainder of the
    current agent-loop run.  After the run completes, ``deactivate()`` is
    called to unregister them, returning to the compact tool list.

    This keeps the per-call tool-definition token cost at ~18 tools
    (12 built-ins + N gateway stubs) instead of 90+.
    """

    def __init__(
        self,
        server_name: str,
        wrappers: list,  # list[MCPToolWrapper]
        registry: ToolRegistry,
        gateway_description: str = "",
    ):
        self._server_name = server_name
        self._wrappers = wrappers
        self._registry = registry
        self._gateway_description = gateway_description
        self._active = False

    @property
    def name(self) -> str:
        return f"use_{self._server_name}"

    @property
    def description(self) -> str:
        base = self._gateway_description or f"激活 {self._server_name} 工具集"
        sample = ", ".join(w._original_name for w in self._wrappers[:6])
        if len(self._wrappers) > 6:
            sample += "..."
        return (
            f"{base}。"
            f"共 {len(self._wrappers)} 个工具（如 {sample}）。"
            "调用此工具并描述你的任务，即可激活该工具集，之后可直接调用其中的具体工具。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "需要完成的任务描述，用于激活后的上下文提示",
                }
            },
            "required": ["task"],
        }

    async def execute(self, task: str = "", **kwargs) -> str:  # type: ignore[override]
        """Activate: register all real tools into the shared registry."""
        if not self._active:
            for wrapper in self._wrappers:
                self._registry.register(wrapper)
            self._active = True
            logger.info(
                "MCPGateway: activated '{}' ({} tools loaded)",
                self._server_name, len(self._wrappers),
            )
        tool_lines = "\n".join(
            f"- {w.name}: {(w.description or '')[:80]}"
            for w in self._wrappers
        )
        return (
            f"{self._server_name} 工具集已激活，共 {len(self._wrappers)} 个工具：\n\n"
            f"{tool_lines}\n\n"
            f"任务：{task}\n"
            "请直接调用上述工具完成任务。"
        )

    def deactivate(self) -> None:
        """Unregister all real tools; call after each agent-loop run."""
        if self._active:
            for wrapper in self._wrappers:
                self._registry.unregister(wrapper.name)
            self._active = False
            logger.debug("MCPGateway: deactivated '{}'", self._server_name)

    @property
    def is_active(self) -> bool:
        return self._active


def _transport_for(cfg) -> str:
    """决定 MCP 连接传输方式；返回 'sse' / 'stdio' / 'http'，空串 = 无可用配置。

    显式 ``type`` 优先（平台托管网关用 SSE）；未指定时按字段推断
    （command → stdio，url → streamable HTTP）。
    """
    cfg_type = (getattr(cfg, "type", "") or "").lower()
    if cfg_type == "sse" and getattr(cfg, "url", ""):
        return "sse"
    if getattr(cfg, "command", ""):
        return "stdio"
    if getattr(cfg, "url", ""):
        return "http"
    return ""


# 内置默认网关服务器名（schema.DEFAULT_MCP_SERVERS 的键）：该服务器
# 未显式配置 headers 时，连接阶段从登录态 token 文件注入凭据。
try:
    from miqi.config.schema import DEFAULT_MCP_SERVERS as _DEFAULT_MCP_SERVERS

    _DEFAULT_GATEWAY_NAME = next(iter(_DEFAULT_MCP_SERVERS), "")
except Exception:  # pragma: no cover — schema 不可用的极端环境禁用注入
    _DEFAULT_GATEWAY_NAME = ""


def _validate_mcp_http_url(url: str, *, allow_insecure: bool = False) -> str | None:
    """校验 MCP HTTP 端点；返回错误信息，None 表示可用。

    自定义 headers（如 Authorization）随初始请求明文发送——非回环的
    http:// 端点等于凭据明文传输（CWE-319）。只允许回环 http（本地
    测试端点）、任意 https，或显式 opt-in（``insecure_http: true``，
    平台托管网关暂无 https 时的过渡方案）。
    """
    if allow_insecure:
        return None
    from urllib.parse import urlsplit

    parsed = urlsplit(url or "")
    if parsed.scheme.lower() != "http":
        return None  # https / 无 scheme（连接阶段自然失败）
    host = (parsed.hostname or "").lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return None
    return f"非回环 http:// MCP 端点被拒绝（凭据明文传输风险）：{url}"


def _gateway_key_from_token_file(token_file) -> str | None:
    """从 .qraft/token.json 读取平台下发的 MCP 网关凭据（mcpGatewayKey）。

    Desktop 在登录/刷新时写入该字段（0600 文件）；凭据不入仓库、
    不进 config.json。文件缺失/损坏/无字段一律返回 None（静默降级，
    连接阶段由网关返回认证错误）。
    """
    try:
        import json
        from pathlib import Path

        token_file = Path(token_file)
        if not token_file.is_file():
            return None
        data = json.loads(token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    key = data.get("mcpGatewayKey")
    return key if isinstance(key, str) and key else None


def _is_https_url(url: str) -> bool:
    """URL 是否为 https。"""
    from urllib.parse import urlsplit

    return urlsplit(url or "").scheme.lower() == "https"


def _inject_gateway_key_over_url(cfg) -> bool:
    """登录态网关凭据是否允许随该端点发送。

    https 一律允许；明文 http 仅在该服务器显式 opt-in ``insecure_http``
    时允许（平台暂无 https 网关域名，内置网关默认 opt-in——共享 token
    明文传输的已知权衡，见 schema.DEFAULT_MCP_SERVERS）。
    """
    return _is_https_url(getattr(cfg, "url", "")) or bool(
        getattr(cfg, "insecure_http", False)
    )


def _url_matches_trusted_gateway(url: str) -> bool:
    """URL 是否等于内置可信网关端点（防止同名服务器把凭据导向他处）。"""
    trusted = _DEFAULT_MCP_SERVERS.get(_DEFAULT_GATEWAY_NAME) or {}
    return bool(url) and url == trusted.get("url", "")


async def _connect_one_server(
    name: str,
    cfg,
    registry: ToolRegistry,
    keep_alive: asyncio.Event,
    registered: asyncio.Event,
    workspace=None,
) -> None:
    """Connect a single MCP server and keep the connection alive.

    Runs as its own asyncio.Task.  The connection stays open until
    *keep_alive* is set (or the task is cancelled), and the server's
    AsyncExitStack is closed HERE — inside the same task that entered it.

    This task-boundary discipline matters: the MCP SDK / anyio transports
    enter cancel-scopes while connecting, and anyio requires those scopes
    to be exited in the same task they were entered in.  Handing the stack
    to another task and calling ``aclose()`` there raises
    "Attempted to exit cancel scope in a different task".

    *registered* is set once the server's tools are registered (or the
    connection failed) so the caller can wait for a deterministic tool
    list before the first turn.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server_stack = AsyncExitStack()
    await server_stack.__aenter__()

    try:
        try:
            transport = _transport_for(cfg)
            if transport in ("sse", "http"):
                # SSE 与 streamable-http 都随初始请求发送自定义 headers：
                # 非回环 http 端点先过校验（回环 http / https / 显式
                # insecure_http opt-in 放行）。校验先于凭据注入——被拒
                # 的端点绝不接触登录凭据（CWE-319，CodeRabbit #949）。
                _url_error = _validate_mcp_http_url(
                    cfg.url,
                    allow_insecure=bool(getattr(cfg, "insecure_http", False)),
                )
                if _url_error:
                    logger.error("MCP server '{}': {}", name, _url_error)
                    return
            # 登录态注入：仅当 ① 默认网关服务器未显式配置 headers；
            # ② 名称与 URL 都匹配内置可信端点（防止同名服务器把
            # workspace 凭据导向其他地址）；③ 端点允许携带凭据——https，
            # 或该服务器已显式 opt-in insecure_http（平台暂无 https，内置
            # 网关默认 opt-in 明文 http）。三者缺一即不注入、fail-closed。
            effective_headers = dict(getattr(cfg, "headers", None) or {})
            if (
                not effective_headers
                and name == _DEFAULT_GATEWAY_NAME
                and workspace is not None
                and _url_matches_trusted_gateway(getattr(cfg, "url", ""))
                and _inject_gateway_key_over_url(cfg)
            ):
                _gw_key = _gateway_key_from_token_file(workspace / ".qraft" / "token.json")
                if _gw_key:
                    effective_headers["Authorization"] = f"Bearer {_gw_key}"
                    logger.info("MCP server '{}': 登录态注入网关凭据（token 文件）", name)
            if transport == "sse":
                from mcp.client.sse import sse_client

                # SSE 传输（平台托管 MCP 网关）：自定义 headers（如
                # Authorization）直接随 GET /sse 握手请求发送。
                read, write = await server_stack.enter_async_context(
                    sse_client(cfg.url, headers=effective_headers or None)
                )
            elif transport == "stdio":
                params = StdioServerParameters(
                    command=cfg.command, args=cfg.args, env=cfg.env or None
                )
                read, write = await server_stack.enter_async_context(stdio_client(params))
            elif transport == "http":
                from mcp.client.streamable_http import streamable_http_client

                # follow_redirects=False：自定义 headers（如 Authorization）
                # 绝不随跨域重定向带到第三方主机（CWE-201 评审）。
                http_client = (
                    httpx.AsyncClient(headers=effective_headers, follow_redirects=False)
                    if effective_headers
                    else None
                )
                read, write, _ = await server_stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': no command or url configured, skipping", name)
                return

            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            tools = await session.list_tools()
            progress_interval = getattr(cfg, "progress_interval_seconds", 15)
            wrappers = [
                MCPToolWrapper(
                    session, name, tool_def,
                    tool_timeout=cfg.tool_timeout,
                    progress_interval=progress_interval,
                    # #975：下载类工具落盘的 base_workspace 与 MCP 连接同源
                    # （config.workspace_path；会话目录在 execute 期按注入的
                    # _session_key 解析，构造期不绑死具体会话）。
                    base_workspace=workspace,
                )
                for tool_def in tools.tools
            ]

            lazy = getattr(cfg, "lazy", False)
            if lazy:
                # Register a single gateway entry-point tool; real tools are
                # injected into the registry on demand when the gateway executes.
                gateway_desc = getattr(cfg, "description", "") or ""
                gateway = MCPGatewayTool(name, wrappers, registry, gateway_desc)
                registry.register(gateway)
                logger.info(
                    "MCP server '{}': connected, {} tools ready (lazy gateway registered)",
                    name, len(wrappers),
                )
            else:
                for wrapper in wrappers:
                    registry.register(wrapper)
                    logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)
                logger.info("MCP server '{}': connected, {} tools registered", name, len(wrappers))
        except BaseException as e:
            logger.error("MCP server '{}': failed to connect: {}", name, e)
    finally:
        registered.set()

    # Stay alive so the connection (and its cancel-scopes) never leaves this
    # task.  The session sets keep_alive (or cancels this task) on stop.
    try:
        await keep_alive.wait()
    finally:
        try:
            await server_stack.aclose()
        except Exception:
            pass


async def connect_mcp_servers(
    mcp_servers: dict,
    registry: ToolRegistry,
    keep_alive: asyncio.Event,
    workspace=None,
) -> list[asyncio.Task]:
    """Connect to configured MCP servers and register their tools.

    Each server connection runs in its own asyncio.Task so that anyio
    cancel-scopes (used internally by the MCP SDK / httpx) are fully
    isolated and always torn down inside the task that created them.

    Returns the list of connection tasks; the caller keeps them alive via
    *keep_alive* and awaits them after setting it (or cancels them) to
    close the connections.  A failure in one server cannot cancel siblings
    or the caller.

    *workspace* 供登录态凭据注入使用（workspace/.qraft/token.json 的
    mcpGatewayKey），可为 None（无注入）。
    """
    tasks: list[asyncio.Task] = []
    registered = [asyncio.Event() for _ in mcp_servers]
    for (name, cfg), ev in zip(mcp_servers.items(), registered):
        tasks.append(
            asyncio.create_task(
                _connect_one_server(name, cfg, registry, keep_alive, ev, workspace)
            )
        )

    # Barrier: wait until every server has registered its tools (or failed)
    # so the caller's first turn sees a deterministic tool list.
    await asyncio.gather(*(ev.wait() for ev in registered))
    return tasks
