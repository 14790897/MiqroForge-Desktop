"""Configuration schema using Pydantic."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

if TYPE_CHECKING:
    from miqi.providers.base import LLMProvider


class Base(BaseModel):
    """Base model that accepts both camelCase and snake_case keys."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class TelegramConfig(Base):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    reply_to_message: bool = False  # If true, bot replies quote the original message


class DingTalkConfig(Base):
    """DingTalk channel configuration using Stream mode."""

    enabled: bool = False
    client_id: str = ""  # AppKey
    client_secret: str = ""  # AppSecret
    allow_from: list[str] = Field(default_factory=list)  # Allowed staff_ids


class DiscordConfig(Base):
    """Discord channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from Discord Developer Portal
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs
    gateway_url: str = "wss://gateway.discord.gg/?v=10&encoding=json"
    intents: int = 37377  # GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT


class EmailConfig(Base):
    """Email channel configuration (IMAP inbound + SMTP outbound)."""

    enabled: bool = False
    consent_granted: bool = False  # Explicit owner permission to access mailbox data

    # IMAP (receive)
    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    # SMTP (send)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    # Behavior
    auto_reply_enabled: bool = True  # If false, inbound email is read but no automatic reply is sent
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender email addresses


class MochatMentionConfig(Base):
    """Mochat mention behavior configuration."""

    require_in_groups: bool = False


class MochatGroupRule(Base):
    """Mochat per-group mention requirement."""

    require_mention: bool = False


class MochatConfig(Base):
    """Mochat channel configuration."""

    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0  # 0 means unlimited retries
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"  # off | non-mention
    reply_delay_ms: int = 120000


class SlackDMConfig(Base):
    """Slack DM policy configuration."""

    enabled: bool = True
    policy: str = "open"  # "open" or "allowlist"
    allow_from: list[str] = Field(default_factory=list)  # Allowed Slack user IDs


class SlackConfig(Base):
    """Slack channel configuration."""

    enabled: bool = False
    mode: str = "socket"  # "socket" supported
    webhook_path: str = "/slack/events"
    bot_token: str = ""  # xoxb-...
    app_token: str = ""  # xapp-...
    user_token_read_only: bool = True
    reply_in_thread: bool = True
    react_emoji: str = "eyes"
    group_policy: str = "mention"  # "mention", "open", "allowlist"
    group_allow_from: list[str] = Field(default_factory=list)  # Allowed channel IDs if allowlist
    dm: SlackDMConfig = Field(default_factory=SlackDMConfig)


class QQConfig(Base):
    """QQ channel configuration using botpy SDK."""

    enabled: bool = False
    app_id: str = ""  # 机器人 ID (AppID) from q.qq.com
    secret: str = ""  # 机器人密钥 (AppSecret) from q.qq.com
    allow_from: list[str] = Field(default_factory=list)  # Allowed user openids (empty = public access)


class FeishuChannelConfig(Base):
    """Feishu (Lark) channel configuration using WebSocket long connection."""

    enabled: bool = False
    app_id: str = ""      # Developer Console App ID (cli_xxx)
    app_secret: str = ""  # Developer Console App Secret
    allow_from: list[str] = Field(default_factory=list)  # Allowed open_ids (empty = anyone)
    reply_delay_ms: int = 3000  # Debounce window in ms: coalesce rapid messages before sending to agent (0 = off)
    require_mention_in_groups: bool = True  # If true, only respond when @mentioned in group chats


class FeedbackConfig(Base):
    """User feedback collection configuration (submit to Feishu Bitable).

    Hardcoded defaults ship with the application so that end users can
    submit feedback with zero configuration.  The Feishu App permissions
    are scoped to the single target Bitable table only.
    """

    enabled: bool = True
    feishu_app_id: str = "cli_aaea960221b8dbea"
    feishu_app_secret: str = "0mutMgBMlqCOH4xoR2vkTcxSFQvrRIWk"
    bitable_app_token: str = "XdLzbs2cUaLubKsDisBcVnXknrf"
    bitable_table_id: str = "tblXphMl4M8vjyco"


class ChannelsConfig(Base):
    """Configuration for chat channels."""

    send_progress: bool = True    # stream agent's text progress to the channel
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    send_queue_notifications: bool = True  # Notify users about their position in the task queue
    feishu: FeishuChannelConfig = Field(default_factory=FeishuChannelConfig)
    feedback: FeedbackConfig = Field(default_factory=FeedbackConfig)


class FallbackChainEntry(Base):
    """One entry in the provider fallback chain."""

    model: str = ""    # full model string, e.g. "openai/gpt-4o"


class AgentDefaults(Base):
    """Default agent configuration."""

    name: str = "miqi"
    workspace: str = "~/.miqi/workspace"
    model: str = "anthropic/claude-opus-4-5"
    max_tokens: int = 8192
    temperature: float = 0.1
    max_tool_iterations: int = 500
    memory_window: int = 100
    reflect_after_tool_calls: bool = True
    # Maximum characters kept per tool result in the live prompt.
    max_tool_result_chars: int = 16000
    # Soft cap on total estimated context chars before LLM call.
    context_limit_chars: int = 600000
    # Runtime engine: "legacy" (original AgentLoop) or "kun" (desktop-workbench runtime)
    runtime: str = "legacy"
    # Provider fallback chain — tried in order when primary fails
    fallback_chain: list[FallbackChainEntry] = Field(default_factory=list)


class AgentMemoryConfig(Base):
    """Agent memory runtime configuration."""

    flush_every_updates: int = 8
    flush_interval_seconds: int = 120
    short_term_turns: int = 12
    pending_limit: int = 20


class AgentSessionConfig(Base):
    """Session storage runtime configuration."""

    compact_threshold_messages: int = 400
    compact_threshold_bytes: int = 2_000_000
    compact_keep_messages: int = 300
    session_tool_result_max_chars: int = 500
    # SQLite backend (new): when True use miqi/session/sqlite_store.py instead of JSONL
    use_sqlite: bool = False
    # When True, agent file writes (relative paths) go to sessions/{key}/files/
    # instead of workspace root. Set to False to restore legacy behavior.
    session_workspace_enabled: bool = True


class SmartRoutingCheapModel(Base):
    """Cheap model config for smart routing."""

    provider: str = ""   # e.g. "openai"
    model: str = ""      # e.g. "gpt-4o-mini"


class SmartRoutingConfig(Base):
    """Smart model routing configuration (routes simple turns to a cheaper model)."""

    enabled: bool = False
    cheap_model: SmartRoutingCheapModel = Field(default_factory=SmartRoutingCheapModel)
    max_chars: int = 160    # turns exceeding this go to primary
    max_words: int = 28     # turns exceeding this go to primary


class CommandApprovalConfig(Base):
    """Dangerous command approval configuration."""

    enabled: bool = True         # when False, all commands approved silently
    mode: str = "manual"         # manual | off
    timeout: int = 60            # CLI approval prompt timeout (seconds)
    allowlist: list[str] = Field(default_factory=list)  # pattern descriptions permanently approved


class ApprovalBypassConfig(Base):
    """Approval bypass switches.

    ``bypass_all`` is intentionally separate from the legacy
    agents.command_approval.enabled flag: it bypasses all approval prompts while
    leaving explicit deny rules and validation failures in force.
    """

    bypass_all: bool = False
    bypass_command_approval: bool = False
    bypass_file_write_approval: bool = False
    bypass_tool_confirmation: bool = False
    bypass_network_approval: bool = False

    @property
    def enabled(self) -> bool:
        return any((
            self.bypass_all,
            self.bypass_command_approval,
            self.bypass_file_write_approval,
            self.bypass_tool_confirmation,
            self.bypass_network_approval,
        ))

    def bypasses_category(self, category: str) -> bool:
        if self.bypass_all:
            return True
        if category == "exec":
            return self.bypass_command_approval
        if category == "file_write":
            return self.bypass_file_write_approval
        if category == "network":
            return self.bypass_network_approval
        if category == "tool_confirmation":
            return self.bypass_tool_confirmation
        return self.bypass_tool_confirmation


class AgentSelfImprovementConfig(Base):
    """Self-improvement lesson configuration."""

    enabled: bool = True
    max_lessons_in_prompt: int = 5
    min_lesson_confidence: int = 3
    max_lessons: int = 200
    lesson_stale_days: int = 30
    lesson_archive_days: int = 90
    curator_enabled: bool = True
    curator_interval_days: int = 7
    curator_threshold: int = 150
    feedback_max_message_chars: int = 220
    feedback_require_prefix: bool = True
    promotion_enabled: bool = True
    promotion_min_users: int = 3
    promotion_triggers: list[str] = Field(
        default_factory=lambda: ["response:length", "response:language"]
    )
    memory_nudge_interval: int = 8   # inject memory nudge every N turns
    skill_nudge_interval: int = 10   # inject skill nudge every N turns
    trace_enabled: bool = True
    embedding_model: str = "intfloat/multilingual-e5-small"
    trace_inject_top_k: int = 3
    trace_similarity_threshold: float = 0.65
    trace_nudge_interval: int = 8   # deprecated: trace is now auto-instrumented
    lessons_legacy_inject_enabled: bool = True


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)
    sessions: AgentSessionConfig = Field(default_factory=AgentSessionConfig)
    self_improvement: AgentSelfImprovementConfig = Field(default_factory=AgentSelfImprovementConfig)
    smart_routing: SmartRoutingConfig = Field(default_factory=SmartRoutingConfig)
    command_approval: CommandApprovalConfig = Field(default_factory=CommandApprovalConfig)
    permanent_approvals: list[str] = Field(
        default_factory=list,
        description="Permanent approval patterns persisted across sessions and restarts",
    )


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)

    @field_validator("api_key", mode="before")
    @classmethod
    def _clean_api_key(cls, v: Any) -> str:
        """API keys travel in the ASCII-only Authorization header.

        Users sometimes paste the key and then type a note into the same
        field (e.g. ``"sk-xxx  用这个"``). The trailing annotation makes
        httpx raise ``UnicodeEncodeError: 'ascii' codec can't encode`` when
        building the ``Authorization: Bearer …`` header — before any HTTP
        request leaves the process, so the provider error is masked by the
        encoding crash. Strip surrounding whitespace and drop non-ASCII
        characters (they can never be part of a valid key) so the config
        self-heals at load time.
        """
        if v is None:
            return ""
        value = str(v)
        return "".join(ch for ch in value if ord(ch) < 128).strip()


class ProvidersConfig(Base):
    """Configuration for LLM providers."""

    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)  # 阿里云通义千问
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    ollama_local: ProviderConfig = Field(default_factory=ProviderConfig)
    ollama_cloud: ProviderConfig = Field(default_factory=ProviderConfig)
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow (硅基流动) API gateway
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine (火山引擎) API gateway


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790


class HeartbeatConfig(Base):
    """Background heartbeat configuration."""

    enabled: bool = True
    interval_seconds: int = 30 * 60


class CronConfig(Base):
    """Cron scheduler configuration."""

    job_timeout_seconds: int = 86400  # Max time a single cron job may run (default 24 h)


class WebSearchConfig(Base):
    """Web search tool configuration."""

    provider: str = "auto"  # auto | tavily | brave | ddgs（旧值 hybrid → auto 回落链）
    api_key: str = ""  # 旧 Brave key（启动迁移到 brave_api_key 后清除）
    tavily_api_key: str = ""  # Tavily Search API key
    brave_api_key: str = ""  # Brave Search API key
    max_results: int = 5

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> str:
        provider = str(value or "auto").lower()
        # "hybrid" (旧语义: ddgs 优先 brave 兜底) 升级为 auto 回落链
        if provider == "hybrid":
            return "auto"
        return provider if provider in {"auto", "deepseek", "tavily", "brave", "ddgs"} else "auto"


class WebFetchConfig(Base):
    """Web fetch tool configuration."""

    provider: str = "builtin"  # builtin | ollama | hybrid
    ollama_api_key: str = ""  # Ollama web fetch API key
    ollama_api_base: str = "https://ollama.com"


class WebToolsConfig(Base):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    fetch: WebFetchConfig = Field(default_factory=WebFetchConfig)


class SandboxConfig(Base):
    """Sandbox isolation configuration for per-session environments."""

    enabled: bool = True
    share_net: bool = True  # Share host network with sandbox (enabled by default so pip/apt inside the sandbox can reach the network; set false for full network isolation)
    # Route system package installs (apt-get/apt/dnf/... install) to the WSL
    # distro as root instead of failing inside the unprivileged read-only
    # bwrap sandbox.  Installs persist across sessions and are immediately
    # visible inside the sandbox via its ro-bind of the distro's system dirs
    # (#759).  Disabled by default: running root commands in the WSL distro
    # is a deliberate privilege the user must opt into.
    allow_system_installs: bool = False
    max_sandboxes: int = 10  # Maximum concurrent sandboxes
    auto_cleanup: bool = True  # Clean up sandbox on session archive/delete
    auto_install_deps: bool = True  # Auto-install bwrap/coreutils/rsync in WSL if missing
    wsl_distro: str = "AIShadowSandbox"  # WSL distribution name (e.g. "AIShadowSandbox"). Auto-detected if empty on Windows.

    wsl_base_dir: str = "/tmp/miqi-sandboxes"  # Sandbox directory inside WSL filesystem
    sandbox_distro_name: str = "AIShadowSandbox"  # Dedicated sandbox distro name (imported from the default distro)


class ExecToolConfig(Base):
    """Shell exec tool configuration.

    Timeout model (#810): the *execution budget* (``timeout``) is the
    maximum wall-clock time a command may run before the whole process
    tree is terminated.  ``max_timeout`` is the hard cap for per-call
    ``timeout`` values requested by the model — requests above it are
    rejected before the command starts.  ``idle_timeout`` is a
    staleness signal (no output for this long ⇒ likely stuck); it never
    kills the process — the heartbeat keeps the turn alive and the
    execution timeout is the backstop.  ``heartbeat_interval`` controls
    how often a silent command emits a progress delta so the bridge
    chat drain (600 s idle) and the frontend watchdog never end the
    turn while the command is still running.
    """

    timeout: int = Field(60, ge=1)
    max_timeout: int = Field(1800, ge=1)  # Hard cap for per-call timeout requests (30 min)
    idle_timeout: int = Field(90, ge=1)  # No-output staleness threshold (seconds)
    heartbeat_interval: int = Field(30, ge=1)  # Progress heartbeat cadence (seconds)
    kill_grace_seconds: int = Field(5, ge=1)  # terminate → SIGKILL grace period
    env_passthrough: list[str] = Field(
        default_factory=list,
        description=(
            "Explicit list of environment variable names that are permitted to pass through "
            "to shell subprocesses spawned by the exec tool.  By default ALL credential "
            "variables (API keys, tokens, secrets, passwords) are stripped before the "
            "subprocess starts (SEC-09).  Use this list to selectively restore access to "
            "specific variables needed by your scripts, e.g. ['OPENAI_API_KEY'].  "
            "Note: MCP server processes are NOT affected — they always inherit the full "
            "parent environment via StdioServerParameters."
        ),
    )

    @model_validator(mode="after")
    def _validate_timeout_ordering(self) -> "ExecToolConfig":
        """Default ``timeout`` must not exceed the hard cap.

        Otherwise a model that omits ``timeout`` runs with the default
        (e.g. 3600 s) while the outer backstop is only max_timeout
        (1800 s) — the "cap" is silently bypassed by not passing the
        argument (#845 review).
        """
        if self.timeout > self.max_timeout:
            raise ValueError(
                f"tools.exec.timeout ({self.timeout}) 不能大于 "
                f"tools.exec.max_timeout ({self.max_timeout})"
            )
        return self


class PapersToolConfig(Base):
    """Paper research tools configuration."""

    provider: str = "hybrid"  # hybrid | semantic_scholar | arxiv | core
    semantic_scholar_api_key: str = ""
    core_api_key: str = ""
    timeout_seconds: int = 20
    default_limit: int = 8
    max_limit: int = 20


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio, streamable HTTP, or SSE)."""

    type: str = ""  # Transport override: "sse" | "http" | "stdio"; empty = auto (command→stdio, url→http)
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: Custom HTTP Headers
    insecure_http: bool = False  # Explicit opt-in: allow non-loopback http:// endpoints (credentials in cleartext; platform gateway has no https yet)
    tool_timeout: int = 30  # Seconds before a tool call is cancelled
    progress_interval_seconds: int = 15  # Interval for heartbeat progress messages during long-running tool calls (0 = off)
    description: str = ""  # Description shown to LLM in the gateway entry-point tool (lazy mode)
    lazy: bool = False  # If true, register a single gateway tool instead of all tools upfront; activate on demand


class ObservabilityConfig(Base):
    """OpenTelemetry observability configuration (Plan 59).

    Default disabled. When enabled and opentelemetry-sdk is installed,
    runtime events are exported as traces/metrics to an OTLP endpoint
    (or console for dev). The entire feature is a no-op when disabled
    or when the optional dependency is missing.
    """

    enabled: bool = False
    endpoint: str | None = None  # OTLP gRPC/HTTP endpoint URL
    service_name: str = "miqi"
    console_export: bool = False  # Console exporter for dev
    sample_ratio: float = 1.0
    capture_content: bool = False  # Never put message text on spans unless this is true


class ToolsConfig(Base):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    papers: PapersToolConfig = Field(default_factory=PapersToolConfig)
    restrict_to_workspace: bool = False  # If true, restrict all tool access to workspace directory
    extra_roots: list[str] = Field(default_factory=list)  # Additional filesystem roots allowed by file tools
    auto_user_dirs: bool = True  # Auto-sense output directories the user mentions and authorize file tools for the session (#821)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)


class BillingConfig(Base):
    """Platform points billing (OAuth2 第三方接入 /oauth2/points/deduct).

    When enabled, a logged-in session deducts ``cost_per_task`` points
    before the first tool/skill execution of the session (one deduction
    per session; plain conversation is free).  Deduction failures are
    fail-closed — the task does not run.
    """

    enabled: bool = True
    cost_per_task: int = 30
    source: str = "desktop-agent-task"


class Config(BaseSettings):
    """Root configuration for MiQi runtime."""

    approvals: ApprovalBypassConfig = Field(default_factory=ApprovalBypassConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    billing: BillingConfig = Field(default_factory=BillingConfig)
    # Opaque Desktop-owned settings (e.g. theme, layout).  Not validated —
    # the Desktop UI reads/writes this via config/batchWrite desktop.* paths.
    desktop: dict[str, object] = Field(default_factory=dict)

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path.

        When the raw workspace equals the serialized default
        ``~/.miqi/workspace`` *and* MIQI_HOME is explicitly set to a
        non-blank value, the runtime path is rebased to
        ``<MIQI_HOME>/workspace`` so that the default follows the
        configured MiQi home.  An explicit (non-default) workspace is
        never rebased — it is expanded as-is.
        """
        raw = self.agents.defaults.workspace
        if raw == "~/.miqi/workspace" and os.environ.get("MIQI_HOME", "").strip():
            from miqi.paths import get_miqi_home

            return get_miqi_home() / "workspace"
        return Path(raw).expanduser().resolve()

    def _match_provider(self, model: str | None = None) -> tuple["ProviderConfig | None", str | None]:
        """Match provider config and its registry name. Returns (config, spec_name)."""
        from miqi.providers.registry import PROVIDERS

        model_lower = (model or self.agents.defaults.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        def _is_configured(spec, provider) -> bool:
            if spec.is_local:
                return bool(provider.api_base)
            return bool(provider.api_key)

        # Explicit provider prefix wins.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if _is_configured(spec, p):
                    return p, spec.name

        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if _is_configured(spec, p):
                    return p, spec.name

        # Fallback: gateways first, then others (follows registry order)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and _is_configured(spec, p):
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """Get API base URL for the given model. Applies default URLs for known gateways."""
        from miqi.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        # Only gateways get a default api_base here. Standard providers
        # set their base URL directly in OpenAIProvider._normalize_api_base.
        if name:
            spec = find_by_name(name)
            if spec and spec.is_gateway and spec.default_api_base:
                return spec.default_api_base
        return None

    def is_builtin_activated(self, provider_name: str) -> bool:
        """Whether a provider holds a built-in (enterprise) activation.

        Tolerates all historical store shapes: missing key, legacy bool
        (``{"deepseek": true}``) and the current dict (``{"builtin": True}``).
        A present-but-null/string entry is treated as not activated instead
        of crashing the decode (#929 review).
        """
        store = self.desktop.get("providerActivation")
        if not isinstance(store, dict):
            return False
        entry = store.get(provider_name)
        if isinstance(entry, bool):
            return entry
        if isinstance(entry, dict):
            return entry.get("builtin") is True
        return False

    def build_provider(self, model: str) -> "LLMProvider | None":
        """Build an LLMProvider instance for the given model string.

        Used by ProviderFallbackChain to construct fallback provider instances.
        Returns None if the model/provider cannot be resolved.
        """
        api_key = self.get_api_key(model)
        api_base = self.get_api_base(model)
        provider_name = self.get_provider_name(model)

        if not provider_name:
            return None

        from miqi.providers.registry import find_by_name
        spec = find_by_name(provider_name)
        if spec is None:
            return None

        try:
            if spec.provider_type == "anthropic":
                from miqi.providers.anthropic_provider import AnthropicProvider
                return AnthropicProvider(api_key=api_key, api_base=api_base, provider_name=provider_name, default_model=model)
            elif spec.provider_type == "gemini":
                from miqi.providers.gemini_provider import GeminiProvider
                return GeminiProvider(api_key=api_key, api_base=api_base, provider_name=provider_name, default_model=model)
            else:
                from miqi.providers.openai_provider import OpenAIProvider
                extra_headers = getattr(self.providers, provider_name, None)
                headers = extra_headers.extra_headers if extra_headers else None
                return OpenAIProvider(api_key=api_key, api_base=api_base, extra_headers=headers, provider_name=provider_name, default_model=model)
        except Exception:
            return None

    def effective_approval_bypass(self) -> ApprovalBypassConfig:
        """Return approval bypass settings including legacy config semantics."""
        if self.agents.command_approval.enabled:
            return self.approvals
        return self.approvals.model_copy(
            update={"bypass_command_approval": True},
        )

    model_config = ConfigDict(env_prefix="MIQI_", env_nested_delimiter="__")
