"""CLI commands for MiQi runtime."""

import os
import select
import sys
from pathlib import Path

import httpx
import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from miqi import __logo__, __version__
from miqi.cli.agent_cmd import register_agent_command
from miqi.cli.config_cmd import register_config_commands
from miqi.cli.gateway_cmd import register_gateway_command
from miqi.cli.management import register_management_commands
from miqi.cli.onboard import register_onboard_command
from miqi.cli.trace_cmd import trace_app
from miqi.config.schema import Config

app = typer.Typer(
    name="miqi",
    help=f"{__logo__} MiQroForge Runtime - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
SOUL_PRESETS: list[tuple[str, str, str, str, str, str]] = [
    (
        "Balanced (default)",
        "balanced",
        "Helpful and friendly",
        "Concise and to the point",
        "Curious and eager to learn",
        "Accuracy over speed",
    ),
    (
        "Concise Operator",
        "concise",
        "Direct and efficient",
        "Action-first and practical",
        "Calm and low-noise",
        "Clear output with minimal overhead",
    ),
    (
        "Mentor Guide",
        "mentor",
        "Patient and structured",
        "Explains tradeoffs clearly",
        "Supportive but honest",
        "Teach the why, not only the what",
    ),
    (
        "Builder Partner",
        "builder",
        "Pragmatic and engineering-focused",
        "Prefers concrete execution",
        "Strong ownership mindset",
        "Ship reliable results quickly",
    ),
]

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios

        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios

        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios

        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    from miqi.paths import get_miqi_home
    history_file = get_miqi_home() / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,  # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} miqi[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} miqi v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """MiQi runtime CLI."""
    pass


def _interactive_onboard_setup(config) -> tuple[str, str]:
    """Interactive setup for provider/model/search and identity defaults."""
    console.print("\n[bold]Interactive setup[/bold] (press Enter to accept defaults)")

    provider_options = [
        ("OpenRouter (recommended gateway)", "openrouter", "anthropic/claude-opus-4-5"),
        ("Anthropic", "anthropic", "claude-opus-4-5"),
        ("OpenAI", "openai", "gpt-4.1"),
        ("DeepSeek", "deepseek", "deepseek-chat"),
        ("Gemini", "gemini", "gemini-2.5-pro"),
        ("Moonshot", "moonshot", "kimi-k2.5"),
        ("DashScope (Qwen)", "dashscope", "qwen-max"),
        ("Zhipu", "zhipu", "glm-4"),
        ("MiniMax", "minimax", "MiniMax-M2.7"),
        ("AiHubMix", "aihubmix", "claude-opus-4.1"),
        ("SiliconFlow", "siliconflow", "deepseek-ai/DeepSeek-V3"),
        ("vLLM / local OpenAI-compatible", "vllm", "meta-llama/Llama-3.1-8B-Instruct"),
        ("Ollama Local", "ollama_local", "llama3.2"),
        ("Ollama Cloud", "ollama_cloud", "gpt-oss:20b-cloud"),
    ]

    console.print("\n[bold]1) Choose your primary LLM provider[/bold]")
    for idx, (label, _, _) in enumerate(provider_options, start=1):
        console.print(f"  {idx}. {label}")

    selected = typer.prompt(
        "Provider number",
        type=int,
        default=1,
        show_default=True,
    )
    if selected < 1 or selected > len(provider_options):
        selected = 1

    provider_label, provider_key, default_model = provider_options[selected - 1]
    console.print(f"Selected: [cyan]{provider_label}[/cyan]")

    provider_cfg = getattr(config.providers, provider_key)

    # SiliconFlow gateway: must set api_base for gateway detection
    if provider_key == "siliconflow":
        provider_cfg.api_base = "https://api.siliconflow.cn/v1"

    if provider_key == "ollama_local":
        provider_cfg.api_base = typer.prompt(
            "Ollama local base URL",
            default="http://localhost:11434",
            show_default=True,
        ).strip()
        provider_cfg.api_key = ""
        model = typer.prompt(
            "Model name",
            default=default_model,
            show_default=True,
        ).strip()
        config.agents.defaults.model = model or default_model
    elif provider_key == "vllm":
        provider_cfg.api_base = typer.prompt(
            "Local OpenAI-compatible base URL",
            default="http://localhost:8000/v1",
            show_default=True,
        ).strip()
        provider_cfg.api_key = typer.prompt(
            "API key (optional for local)",
            default="",
            show_default=False,
        ).strip()
        model = typer.prompt(
            "Model name",
            default=default_model,
            show_default=True,
        ).strip()
        config.agents.defaults.model = model or default_model
    elif provider_key == "ollama_cloud":
        provider_cfg.api_base = typer.prompt(
            "Ollama cloud base URL",
            default="https://ollama.com",
            show_default=True,
        ).strip()
        while True:
            key = typer.prompt("Ollama cloud API key", default="", show_default=False).strip()
            if key:
                provider_cfg.api_key = key
                break
            console.print("[yellow]API key is required for Ollama Cloud.[/yellow]")

        console.print("\n[bold]Model selection for Ollama Cloud[/bold]")
        console.print("  1. Manual model input")
        console.print("  2. Fetch model list from Ollama Cloud (recommended)")
        cloud_model_mode = typer.prompt("Mode", type=int, default=2, show_default=True)

        if cloud_model_mode == 2:
            models, error = _fetch_ollama_cloud_models(
                api_base=provider_cfg.api_base,
                api_key=provider_cfg.api_key,
            )
            if models:
                keyword = (
                    typer.prompt(
                        "Filter keyword (optional)",
                        default="",
                        show_default=False,
                    )
                    .strip()
                    .lower()
                )
                if keyword:
                    filtered_models = [
                        model_name for model_name in models if keyword in model_name.lower()
                    ]
                    if filtered_models:
                        models = filtered_models
                    else:
                        console.print(
                            "[yellow]No models matched filter, showing full list.[/yellow]"
                        )

                max_display = min(len(models), 30)
                console.print(f"Found {len(models)} model(s). Showing first {max_display}:")
                for index, model_name in enumerate(models[:max_display], start=1):
                    console.print(f"  {index}. {model_name}")

                selected_model = typer.prompt(
                    "Model number",
                    type=int,
                    default=1,
                    show_default=True,
                )
                if selected_model < 1:
                    selected_model = 1
                if selected_model > max_display:
                    selected_model = max_display
                config.agents.defaults.model = models[selected_model - 1]
                console.print(f"Selected model: [cyan]{config.agents.defaults.model}[/cyan]")
            else:
                console.print(
                    "[yellow]Failed to fetch model list; falling back to manual input.[/yellow]"
                )
                if error:
                    console.print(f"[dim]{error}[/dim]")
                model = typer.prompt(
                    "Model name",
                    default=default_model,
                    show_default=True,
                ).strip()
                config.agents.defaults.model = model or default_model
        else:
            model = typer.prompt(
                "Model name",
                default=default_model,
                show_default=True,
            ).strip()
            config.agents.defaults.model = model or default_model
    else:
        while True:
            key = typer.prompt(f"{provider_label} API key", default="", show_default=False).strip()
            if key:
                provider_cfg.api_key = key
                break
            console.print("[yellow]API key is required.[/yellow]")
        if typer.confirm("Set custom API base URL?", default=False):
            provider_cfg.api_base = (
                typer.prompt("Custom API base URL", default="", show_default=False).strip() or None
            )
        model = typer.prompt(
            "Model name",
            default=default_model,
            show_default=True,
        ).strip()
        config.agents.defaults.model = model or default_model

    console.print("\n[bold]2) Configure web search & fetch tools[/bold]")
    console.print("  Search provider options:")
    console.print("    1. Auto (Tavily → Brave → DDGS, recommended)")
    console.print("    2. Tavily")
    console.print("    3. Brave Search")
    console.print("    4. DuckDuckGo (ddgs, no API key)")
    search_mode = typer.prompt("Search mode", type=int, default=1, show_default=True)

    if search_mode == 2:
        config.tools.web.search.provider = "tavily"
    elif search_mode == 3:
        config.tools.web.search.provider = "brave"
    elif search_mode == 4:
        config.tools.web.search.provider = "ddgs"
    else:
        config.tools.web.search.provider = "auto"

    if search_mode in (2, 3):
        key_name = "Tavily" if search_mode == 2 else "Brave"
        key_attr = "tavily_api_key" if search_mode == 2 else "brave_api_key"
        key_value = typer.prompt(f"{key_name} Search API key", default="", show_default=False).strip()
        setattr(config.tools.web.search, key_attr, key_value)
        if not key_value:
            console.print(f"[yellow]{key_name} key not set: {key_name} search may be unavailable.[/yellow]")
    elif search_mode == 1:
        # Auto 模式沿用 config 里已有的 key（或迁移来的 brave_api_key），
        # 不在向导里强制询问——key 可在设置页随时配置。
        config.tools.web.search.api_key = ""

    console.print("\n  Fetch provider options:")
    console.print("    1. Built-in web_fetch (default)")
    console.print("    2. Ollama web_fetch")
    console.print("    3. Hybrid (built-in first, fallback to Ollama)")
    fetch_mode = typer.prompt("Fetch mode", type=int, default=1, show_default=True)

    if fetch_mode == 2:
        config.tools.web.fetch.provider = "ollama"
    elif fetch_mode == 3:
        config.tools.web.fetch.provider = "hybrid"
    else:
        config.tools.web.fetch.provider = "builtin"

    if config.tools.web.fetch.provider in {"ollama", "hybrid"}:
        default_fetch_base = config.tools.web.fetch.ollama_api_base or "https://ollama.com"
        default_fetch_key = config.tools.web.fetch.ollama_api_key or ""
        config.tools.web.fetch.ollama_api_base = typer.prompt(
            "Ollama web_fetch base URL",
            default=default_fetch_base,
            show_default=True,
        ).strip()

        if default_fetch_key:
            keep_fetch_key = typer.confirm(
                "Keep existing Ollama web_fetch API key?",
                default=True,
            )
            if keep_fetch_key:
                config.tools.web.fetch.ollama_api_key = default_fetch_key
            else:
                config.tools.web.fetch.ollama_api_key = typer.prompt(
                    "Ollama web_fetch API key",
                    default="",
                    show_default=False,
                ).strip()
        else:
            config.tools.web.fetch.ollama_api_key = typer.prompt(
                "Ollama web_fetch API key",
                default="",
                show_default=False,
            ).strip()

        if not config.tools.web.fetch.ollama_api_key:
            console.print(
                "[yellow]Ollama API key not set: Ollama web_fetch may be unavailable.[/yellow]"
            )
    else:
        config.tools.web.fetch.ollama_api_key = ""

    console.print("\n[bold]3) Configure paper research tools (optional)[/bold]")
    console.print("  Paper provider options:")
    console.print("    1. Hybrid (Semantic Scholar first, fallback to arXiv)")
    console.print("    2. Semantic Scholar only")
    console.print("    3. arXiv only")

    papers_mode = typer.prompt("Paper provider", type=int, default=1, show_default=True)
    if papers_mode == 2:
        config.tools.papers.provider = "semantic_scholar"
    elif papers_mode == 3:
        config.tools.papers.provider = "arxiv"
    else:
        config.tools.papers.provider = "hybrid"

    if config.tools.papers.provider in {"hybrid", "semantic_scholar"}:
        current_key = config.tools.papers.semantic_scholar_api_key or ""
        key = typer.prompt(
            "Semantic Scholar API key (optional, recommended)",
            default=current_key,
            show_default=False,
        ).strip()
        config.tools.papers.semantic_scholar_api_key = key
        if config.tools.papers.provider == "semantic_scholar" and not key:
            console.print(
                "[yellow]No API key set. Semantic Scholar requests may be rate-limited.[/yellow]"
            )

    config.tools.papers.timeout_seconds = typer.prompt(
        "Papers timeout seconds",
        type=int,
        default=max(3, config.tools.papers.timeout_seconds),
        show_default=True,
    )
    config.tools.papers.default_limit = typer.prompt(
        "Default paper result limit",
        type=int,
        default=max(1, config.tools.papers.default_limit),
        show_default=True,
    )
    config.tools.papers.max_limit = typer.prompt(
        "Maximum paper result limit",
        type=int,
        default=max(config.tools.papers.default_limit, config.tools.papers.max_limit),
        show_default=True,
    )
    if config.tools.papers.max_limit < config.tools.papers.default_limit:
        config.tools.papers.max_limit = config.tools.papers.default_limit
        console.print("[yellow]maxLimit was smaller than defaultLimit, adjusted automatically.[/yellow]")

    console.print("\n[bold]4) Customize assistant identity[/bold]")
    agent_name = _normalize_agent_name(
        typer.prompt(
            "Assistant name",
            default=config.agents.defaults.name,
            show_default=True,
        )
    )
    config.agents.defaults.name = agent_name

    console.print("  Soul preset options:")
    for idx, (label, _, _, _, _, _) in enumerate(SOUL_PRESETS, start=1):
        console.print(f"    {idx}. {label}")
    selected_soul = typer.prompt("Soul preset", type=int, default=1, show_default=True)
    if selected_soul < 1 or selected_soul > len(SOUL_PRESETS):
        selected_soul = 1
    soul_label, soul_preset, *_ = SOUL_PRESETS[selected_soul - 1]
    console.print(f"Selected soul: [cyan]{soul_label}[/cyan]")

    return agent_name, soul_preset


def _normalize_agent_name(name: str) -> str:
    """Normalize agent name from user input."""
    compact = " ".join(name.strip().split())
    return compact or "miqi"


def _parse_token_list(raw: str, delimiter: str = ",") -> list[str]:
    """Parse delimited text into normalized non-empty tokens."""
    if not raw:
        return []
    parts = [item.strip() for item in raw.split(delimiter)]
    return [item for item in parts if item]


def _parse_csv_list(raw: str) -> list[str]:
    """Parse comma-separated values into a cleaned list."""
    return _parse_token_list(raw, ",")


def _build_soul_template(agent_name: str, soul_preset: str) -> str:
    """Build SOUL.md content from a preset."""
    preset = next((item for item in SOUL_PRESETS if item[1] == soul_preset), SOUL_PRESETS[0])
    _, _, trait_1, trait_2, trait_3, value_1 = preset

    return f"""# Soul

I am {agent_name}, a lightweight AI assistant.

## Personality

- {trait_1}
- {trait_2}
- {trait_3}

## Values

- {value_1}
- User privacy and safety
- Transparency in actions
"""


def _build_identity_template(agent_name: str) -> str:
    """Build IDENTITY.md content."""
    return f"""# Identity

- Name: {agent_name}
- Role: Personal AI assistant

When introducing yourself, use this name naturally.
"""


def _fetch_ollama_cloud_models(api_base: str, api_key: str) -> tuple[list[str], str | None]:
    """Fetch available model names from Ollama cloud endpoints.

    Tries Ollama native endpoint first (/api/tags), then OpenAI-compatible
    endpoint (/v1/models) as fallback.
    """
    base = (api_base or "").rstrip("/")
    if not base:
        return [], "Missing apiBase"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    endpoint_candidates: list[str] = []
    if base.endswith("/api"):
        endpoint_candidates.append(f"{base}/tags")
    else:
        endpoint_candidates.append(f"{base}/api/tags")

    if base.endswith("/v1"):
        endpoint_candidates.append(f"{base}/models")
    else:
        endpoint_candidates.append(f"{base}/v1/models")

    errors: list[str] = []
    for endpoint in endpoint_candidates:
        try:
            response = httpx.get(endpoint, headers=headers, timeout=10.0)
            response.raise_for_status()
            payload = response.json()

            models: list[str] = []
            if isinstance(payload.get("models"), list):
                for item in payload["models"]:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("model")
                        if isinstance(name, str) and name:
                            models.append(name)
            if isinstance(payload.get("data"), list):
                for item in payload["data"]:
                    if isinstance(item, dict):
                        name = item.get("id")
                        if isinstance(name, str) and name:
                            models.append(name)

            uniq_models = sorted(set(models))
            if uniq_models:
                return uniq_models, None
            errors.append(f"{endpoint}: empty model list")
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")

    return [], "; ".join(errors)


# Bootstrap files that are system-maintained: always overwritten on onboard
# so that package updates (e.g. new MCP tool instructions) take effect
# automatically when users run 'miqi onboard'.
_SYSTEM_TEMPLATE_FILES = {"TOOLS.md", "AGENTS.md", "HEARTBEAT.md"}


def _create_workspace_templates(
    workspace: Path,
    agent_name: str = "miqi",
    soul_preset: str = "balanced",
):
    """Create default workspace template files from bundled templates."""
    from importlib.resources import files as pkg_files

    agent_name = _normalize_agent_name(agent_name)
    templates_dir = pkg_files("miqi") / "templates"

    # Create standard bootstrap files from templates, excluding SOUL (customized below).
    for item in templates_dir.iterdir():
        if not item.name.endswith(".md"):
            continue
        if item.name == "SOUL.md":
            continue
        dest = workspace / item.name
        is_system = item.name in _SYSTEM_TEMPLATE_FILES
        if not dest.exists():
            dest.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            console.print(f"  [dim]Created {item.name}[/dim]")
        elif is_system:
            dest.write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
            console.print(f"  [dim]Updated {item.name} (system template)[/dim]")

    soul_file = workspace / "SOUL.md"
    if not soul_file.exists():
        soul_file.write_text(
            _build_soul_template(agent_name, soul_preset),
            encoding="utf-8",
        )
        console.print("  [dim]Created SOUL.md[/dim]")

    identity_file = workspace / "IDENTITY.md"
    if not identity_file.exists():
        identity_file.write_text(_build_identity_template(agent_name), encoding="utf-8")
        console.print("  [dim]Created IDENTITY.md[/dim]")

    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)

    memory_template = templates_dir / "memory" / "MEMORY.md"
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text(memory_template.read_text(encoding="utf-8"), encoding="utf-8")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")

    (workspace / "skills").mkdir(exist_ok=True)

    from miqi.utils.helpers import ensure_sessions_gitignored
    ensure_sessions_gitignored(workspace)


def _make_provider(config: Config):
    """Create the appropriate LLM provider from config (CLI-friendly wrapper)."""
    from miqi.providers.factory import make_provider as _factory_make_provider
    try:
        return _factory_make_provider(config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# Register split command modules.
register_onboard_command(
    app,
    console=console,
    logo=__logo__,
    normalize_agent_name=_normalize_agent_name,
    interactive_onboard_setup=_interactive_onboard_setup,
    create_workspace_templates=_create_workspace_templates,
)

register_gateway_command(
    app,
    console=console,
    logo=__logo__,
    make_provider=_make_provider,
)

register_agent_command(
    app,
    console=console,
    logo=__logo__,
    make_provider=_make_provider,
    print_agent_response=_print_agent_response,
    init_prompt_session=_init_prompt_session,
    flush_pending_tty_input=_flush_pending_tty_input,
    read_interactive_input_async=_read_interactive_input_async,
    is_exit_command=_is_exit_command,
    restore_terminal=_restore_terminal,
)

register_management_commands(
    app,
    console=console,
    logo=__logo__,
    make_provider_getter=lambda: _make_provider,
    print_agent_response=_print_agent_response,
)

register_config_commands(app, console=console)

app.add_typer(trace_app, name="trace")


if __name__ == "__main__":
    app()
