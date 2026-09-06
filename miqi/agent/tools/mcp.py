"""MCP client: connects to MCP servers and wraps their tools for runtime use."""

import asyncio
import re
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from miqi.agent.tools.base import Tool
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
                 progress_interval: int = 15):
        self._session = session
        self._server_name = server_name
        self._original_name = tool_def.name
        self._name = f"mcp_{server_name}_{tool_def.name}"
        self._description = tool_def.description or tool_def.name
        self._parameters = tool_def.inputSchema or {"type": "object", "properties": {}}
        self._tool_timeout = tool_timeout
        self._progress_interval = progress_interval

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
        from mcp import types

        from miqi.agent.billing_resolver import (
            billing_charge_emitter_for,
            is_slurm_server,
        )

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

        parts = []
        for block in result.content:
            if isinstance(block, types.TextContent):
                parts.append(block.text)
            else:
                parts.append(str(block))
        output = "\n".join(parts) or "(no output)"

        # ── Slurm 作业计费触发（issue #927，2026-09-04 产品确认）──────
        # 作业状态变为 RUNNING 时由 Desktop 发起扣费（10 分/次）：
        # submit_slurm_job / check_job_status 的返回里 state=RUNNING 即
        # 触发一次 fire-and-forget 扣费事件（Desktop 按作业 ID 去重）。
        # 作业已在运行，扣费失败（如余额不足）不阻止作业，由 Desktop
        # 记录到扣费历史并提示。
        if session_key and is_slurm_server(self._server_name):
            job_state = _extract_job_state(output)
            if job_state and job_state.upper() == "RUNNING":
                # 响应里的 job_id 优先；check_job_status 的响应可能只有
                # state（作业 ID 在请求参数里），回退用请求参数保证去重键。
                job_id = _extract_job_id(output) or str(
                    kwargs.get("job_id") or kwargs.get("jobId") or ""
                )
                # 无稳定作业 ID 时不发计费事件：空 job_id 无法去重，
                # 轮询每次 RUNNING 都会再扣一次（数据完整性）。
                if not job_id:
                    return output
                from miqi.agent.billing_resolver import job_reported, mark_job_reported

                # 轮询会反复观察 RUNNING：同一会话同一服务器同一作业只
                # 发一次。先发事件、送达成功才标记——发射失败不标记，
                # 下一次 RUNNING 轮询重试（Desktop 侧去重兜底，重复送达无害）。
                if job_reported(session_key, self._server_name, job_id):
                    return output
                emitter = billing_charge_emitter_for(session_key)
                if emitter is not None:
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
                            "billing: RUNNING 扣费事件发送失败（不标记，下次轮询重试）"
                        )
                    if delivered:
                        mark_job_reported(session_key, self._server_name, job_id)

        return output


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


async def _connect_one_server(
    name: str,
    cfg,
    registry: ToolRegistry,
    keep_alive: asyncio.Event,
    registered: asyncio.Event,
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
                # insecure_http opt-in 放行）。
                _url_error = _validate_mcp_http_url(
                    cfg.url,
                    allow_insecure=bool(getattr(cfg, "insecure_http", False)),
                )
                if _url_error:
                    logger.error("MCP server '{}': {}", name, _url_error)
                    return
            if transport == "sse":
                from mcp.client.sse import sse_client

                # SSE 传输（平台托管 MCP 网关）：自定义 headers（如
                # Authorization）直接随 GET /sse 握手请求发送。
                read, write = await server_stack.enter_async_context(
                    sse_client(cfg.url, headers=cfg.headers or None)
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
                    httpx.AsyncClient(headers=cfg.headers, follow_redirects=False)
                    if cfg.headers
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
    mcp_servers: dict, registry: ToolRegistry, keep_alive: asyncio.Event
) -> list[asyncio.Task]:
    """Connect to configured MCP servers and register their tools.

    Each server connection runs in its own asyncio.Task so that anyio
    cancel-scopes (used internally by the MCP SDK / httpx) are fully
    isolated and always torn down inside the task that created them.

    Returns the list of connection tasks; the caller keeps them alive via
    *keep_alive* and awaits them after setting it (or cancels them) to
    close the connections.  A failure in one server cannot cancel siblings
    or the caller.
    """
    tasks: list[asyncio.Task] = []
    registered = [asyncio.Event() for _ in mcp_servers]
    for (name, cfg), ev in zip(mcp_servers.items(), registered):
        tasks.append(
            asyncio.create_task(_connect_one_server(name, cfg, registry, keep_alive, ev))
        )

    # Barrier: wait until every server has registered its tools (or failed)
    # so the caller's first turn sees a deterministic tool list.
    await asyncio.gather(*(ev.wait() for ev in registered))
    return tasks
