"""miqi config — CLI sub-commands for managing configuration."""

from __future__ import annotations

import re
import sys

# Matches model names that signal "small" parameter count (≤ ~13B), e.g.
#   Qwen/Qwen2.5-7B-Instruct, llama-3.1-8b, mistral-7B, qwen-1.8b, gemma-2b.
# Bare "13B" / "8B" / "7B" / single-digit-B / decimal-B forms all match.
_SMALL_MODEL_RE = re.compile(
    r"\b(0?\.[0-9]+B|[1-9]B|1[0-3]B)\b",
    re.IGNORECASE,
)


def _looks_like_small_model(model: str | None) -> bool:
    """Return True if the model name looks like a ≤13B-parameter model."""
    if not model:
        return False
    return bool(_SMALL_MODEL_RE.search(model))


def register_config_commands(app, *, console) -> None:
    """Add `miqi config` sub-command group to *app*."""
    import typer

    config_app = typer.Typer(help="Manage MiQroForge configuration")
    app.add_typer(config_app, name="config")

    mcp_app = typer.Typer(help="Manage MCP server connections")
    config_app.add_typer(mcp_app, name="mcp")

    # ------------------------------------------------------------------ #
    # miqi config show
    # ------------------------------------------------------------------ #

    @config_app.command("show")
    def config_show():
        """Show current non-default configuration values."""
        from miqi.config.loader import get_config_path, load_config

        config_path = get_config_path()
        if not config_path.exists():
            console.print("[yellow]No config file found. Run `miqi onboard` first.[/yellow]")
            raise typer.Exit(1)

        config = load_config(config_path)
        cfg = config.agents.defaults
        provider_name = config.get_provider_name(cfg.model)

        console.print("\n[bold]Agent[/bold]")
        console.print(f"  name:  [cyan]{cfg.name}[/cyan]")
        console.print(f"  model: [cyan]{cfg.model}[/cyan]"
                      + (f"  [dim]({provider_name})[/dim]" if provider_name else ""))

        # Providers
        configured = {
            k: v for k, v in config.providers.model_dump(by_alias=True).items()
            if v.get("apiKey") or v.get("apiBase") is not None
        }
        if configured:
            console.print("\n[bold]Providers[/bold]")
            for name, p in configured.items():
                key_display = ("*" * 8 + p["apiKey"][-4:]) if p.get("apiKey") else "[dim](no key)[/dim]"
                base_display = f"  base={p['apiBase']}" if p.get("apiBase") else ""
                console.print(f"  {name}: {key_display}{base_display}")

        # Channels
        console.print("\n[bold]Channels[/bold]")
        feishu = config.channels.feishu
        if feishu.enabled:
            console.print(f"  feishu: [green]enabled[/green]  app_id={feishu.app_id}")
        else:
            console.print("  feishu: [dim]disabled[/dim]")

        # MCP servers
        if config.tools.mcp_servers:
            console.print("\n[bold]MCP Servers[/bold]")
            for sname, srv in config.tools.mcp_servers.items():
                if srv.command:
                    console.print(f"  [cyan]{sname}[/cyan]  stdio  {srv.command} {' '.join(srv.args)}")
                elif srv.url:
                    console.print(f"  [cyan]{sname}[/cyan]  http   {srv.url}")
        else:
            console.print("\n[bold]MCP Servers[/bold]  [dim](none)[/dim]")

        console.print(f"\n[dim]Config file: {config_path}[/dim]\n")

    # ------------------------------------------------------------------ #
    # miqi config provider <name>
    # ------------------------------------------------------------------ #

    @config_app.command("provider")
    def config_provider(
        name: str = typer.Argument(..., help="Provider name (e.g. moonshot, openai, deepseek)"),
        api_key: str = typer.Option(None, "--api-key", "-k", help="API key"),
        api_base: str = typer.Option(None, "--api-base", "-b", help="Custom API base URL"),
    ):
        """Set or update an LLM provider's API key / base URL."""
        from miqi.config.loader import get_config_path, load_config, save_config

        config = load_config()

        # Normalise name (supports both camel and snake)
        norm = name.lower().replace("-", "_").replace(" ", "_")
        if not hasattr(config.providers, norm):
            console.print(f"[red]Unknown provider:[/red] {name}")
            valid = [k for k in config.providers.model_fields]
            console.print(f"Valid: {', '.join(valid)}")
            raise typer.Exit(1)

        provider_cfg = getattr(config.providers, norm)

        if api_key is None and api_base is None:
            # Interactive mode
            current_key = provider_cfg.api_key or ""
            current_base = provider_cfg.api_base or ""
            console.print(f"\n[bold]Configure provider:[/bold] {name}")
            new_key = typer.prompt(
                "API key (leave blank to keep current)",
                default="",
                show_default=False,
            ).strip()
            if new_key:
                provider_cfg.api_key = new_key
            elif current_key:
                console.print(f"  [dim]Keeping existing key …{current_key[-4:]}[/dim]")

            new_base = typer.prompt(
                "API base URL (leave blank to keep current)",
                default=current_base,
                show_default=bool(current_base),
            ).strip()
            if new_base:
                provider_cfg.api_base = new_base or None
        else:
            if api_key is not None:
                provider_cfg.api_key = api_key
            if api_base is not None:
                provider_cfg.api_base = api_base or None

        save_config(config, get_config_path())
        console.print(f"[green]✓[/green] Provider [cyan]{name}[/cyan] updated")

    # ------------------------------------------------------------------ #
    # miqi config feishu
    # ------------------------------------------------------------------ #

    @config_app.command("feishu")
    def config_feishu(
        app_id: str = typer.Option(None, "--app-id", help="Feishu App ID (cli_xxx)"),
        app_secret: str = typer.Option(None, "--app-secret", help="Feishu App Secret"),
        mcp_python: str = typer.Option(
            None, "--mcp-python",
            help="Python executable for feishu-mcp (default: auto-detect)",
        ),
        mcp_module: str = typer.Option(
            "feishu_mcp.server", "--mcp-module",
            help="Module to run as MCP server",
        ),
        disable: bool = typer.Option(False, "--disable", help="Disable Feishu channel"),
    ):
        """Configure Feishu channel and feishu-mcp server in one shot.

        Sets both channels.feishu AND tools.mcpServers.feishu-mcp so you
        never have to copy credentials into two places.
        """
        from miqi.config.loader import get_config_path, load_config, save_config
        from miqi.config.schema import MCPServerConfig

        config = load_config()

        if disable:
            config.channels.feishu.enabled = False
            save_config(config, get_config_path())
            console.print("[yellow]Feishu channel disabled[/yellow]")
            return

        interactive = sys.stdin.isatty() and sys.stdout.isatty()

        # Credentials
        if app_id is None:
            if interactive:
                current = config.channels.feishu.app_id or ""
                app_id = typer.prompt(
                    "Feishu App ID (cli_xxx)",
                    default=current,
                    show_default=bool(current),
                ).strip()
            else:
                app_id = config.channels.feishu.app_id

        if app_secret is None:
            if interactive:
                current = config.channels.feishu.app_secret or ""
                prompt_default = current if current else ""
                app_secret = typer.prompt(
                    "Feishu App Secret",
                    default=prompt_default,
                    show_default=False,
                    hide_input=not bool(current),
                ).strip()
            else:
                app_secret = config.channels.feishu.app_secret

        if not app_id or not app_secret:
            console.print("[red]app_id and app_secret are required[/red]")
            raise typer.Exit(1)

        # Set channel config
        config.channels.feishu.enabled = True
        config.channels.feishu.app_id = app_id
        config.channels.feishu.app_secret = app_secret

        # Auto-detect feishu-mcp python if not given
        if mcp_python is None:
            mcp_python = _detect_feishu_mcp_python()
            if mcp_python is None:
                if interactive:
                    mcp_python = typer.prompt(
                        "Path to feishu-mcp Python executable",
                        default="",
                        show_default=False,
                    ).strip()
                if not mcp_python:
                    console.print(
                        "[yellow]feishu-mcp not found. Skipping mcpServers entry.[/yellow]\n"
                        "Install it: pip install feishu-mcp  or  "
                        "git clone https://github.com/lichman0405/feishu-mcp && pip install -e ."
                    )
                    save_config(config, get_config_path())
                    console.print("[green]✓[/green] Feishu channel enabled (no MCP server)")
                    return

        # Set or update mcpServers.feishu-mcp
        existing = config.tools.mcp_servers.get("feishu-mcp", MCPServerConfig())
        existing.command = mcp_python
        existing.args = ["-m", mcp_module]
        existing.env = {
            "FEISHU_APP_ID": app_id,
            "FEISHU_APP_SECRET": app_secret,
        }
        config.tools.mcp_servers["feishu-mcp"] = existing

        save_config(config, get_config_path())
        console.print(f"[green]✓[/green] Feishu channel enabled  (app_id={app_id})")
        console.print(f"[green]✓[/green] MCP server feishu-mcp configured  ({mcp_python})")

    # ------------------------------------------------------------------ #
    # miqi config pdf2zh
    # ------------------------------------------------------------------ #

    @config_app.command("pdf2zh")
    def config_pdf2zh(
        mcp_python: str = typer.Option(None, "--mcp-python", help="Python executable for pdf2zh"),
        mcp_module: str = typer.Option("pdf2zh.mcp_server", "--mcp-module", help="Module to run"),
        provider: str = typer.Option(
            None, "--provider", "-p",
            help="Provider name to copy credentials from (e.g. moonshot, openai). "
                 "Default: auto-detect from current model.",
        ),
        model: str = typer.Option(None, "--model", "-m", help="Model name for pdf2zh translation"),
        timeout: int = typer.Option(3600, "--timeout", "-t", help="Tool call timeout seconds (default 3600 = 1 h; increase for large documents)"),
        no_prompt: bool = typer.Option(
            False, "--no-prompt",
            help="Never ask interactively. Use values from miqi defaults; "
                 "if anything is missing, exit non-zero. Used by configure_mcps.sh.",
        ),
    ):
        """Configure pdf2zh MCP server, auto-filling LLM credentials from miqi config.

        pdf2zh needs OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL env vars.
        This command reads them from your miqi provider configuration so
        you never have to copy-paste credentials twice.

        With ``--no-prompt`` the command runs unattended: it uses the current
        miqi default model + provider verbatim and refuses to fall back to
        interactive prompts. This is what scripts/configure_mcps.sh uses to
        keep pdf2zh permanently in lock-step with `miqi config defaults`.
        """
        from miqi.config.loader import get_config_path, load_config, save_config
        from miqi.config.schema import MCPServerConfig

        config = load_config()
        interactive = sys.stdin.isatty() and sys.stdout.isatty() and not no_prompt

        # ---- Resolve provider ----
        if provider is None:
            # Auto-detect from the current model
            provider = config.get_provider_name(config.agents.defaults.model)

        norm_provider = (provider or "").lower().replace("-", "_").replace(" ", "_")
        provider_cfg = getattr(config.providers, norm_provider, None) if norm_provider else None

        api_key = (provider_cfg.api_key if provider_cfg else "") or ""
        api_base = (provider_cfg.api_base if provider_cfg else "") or ""

        if not api_key and interactive:
            api_key = typer.prompt(
                f"API key for {norm_provider or 'provider'} (OPENAI_API_KEY)",
                default="",
                show_default=False,
            ).strip()

        if not api_base and interactive:
            api_base = typer.prompt(
                "API base URL (OPENAI_BASE_URL, e.g. https://api.openai.com/v1)",
                default="",
                show_default=False,
            ).strip()

        # Ensure /v1 suffix for base URL
        if api_base and not api_base.rstrip("/").endswith("/v1"):
            api_base = api_base.rstrip("/") + "/v1"

        if no_prompt and not api_key:
            console.print(
                f"[red]No API key configured for provider '{norm_provider or '(unset)'}'.[/red]\n"
                "Run `miqi onboard` (or `miqi config provider <name> --api-key …`) first, "
                "then re-run this command."
            )
            raise typer.Exit(1)

        # ---- Resolve model ----
        if model is None:
            model = config.agents.defaults.model
            # Strip provider prefix if present (e.g. "anthropic/claude-opus-4-5" → "claude-opus-4-5")
            if "/" in model:
                model = model.split("/", 1)[1]

        if interactive:
            model = typer.prompt("Model name for translation", default=model, show_default=True).strip()

        # ---- Resolve python executable ----
        if mcp_python is None:
            mcp_python = _detect_pdf2zh_mcp_python()
            if mcp_python is None:
                if interactive:
                    mcp_python = typer.prompt(
                        "Path to pdf2zh Python executable (e.g. /path/to/.venv/bin/python)",
                        default="",
                        show_default=False,
                    ).strip()
                if not mcp_python:
                    console.print(
                        "[red]pdf2zh not found.[/red]\n"
                        "Install: git clone https://github.com/lichman0405/pdftranslate-mcp && "
                        "cd pdftranslate-mcp && python -m venv .venv && .venv/bin/pip install -e ."
                    )
                    raise typer.Exit(1)

        # ---- Build / update mcpServers.pdf2zh ----
        existing = config.tools.mcp_servers.get("pdf2zh", MCPServerConfig())
        existing.command = mcp_python
        existing.args = ["-m", mcp_module]
        existing.tool_timeout = timeout
        existing.env = {
            "OPENAI_API_KEY": api_key,
            "OPENAI_BASE_URL": api_base,
            "OPENAI_MODEL": model,
        }
        config.tools.mcp_servers["pdf2zh"] = existing

        save_config(config, get_config_path())
        console.print("[green]✓[/green] MCP server [cyan]pdf2zh[/cyan] configured")
        console.print(f"  provider:  [cyan]{norm_provider or '(none)'}[/cyan]")
        console.print(f"  model:     [cyan]{model}[/cyan]")
        console.print(f"  api_base:  [cyan]{api_base or '(default)'}[/cyan]")
        console.print(f"  python:    [dim]{mcp_python}[/dim]")
        console.print(f"  timeout:   {timeout}s")

        # ---- Small-model advisory ----------------------------------------
        if _looks_like_small_model(model):
            console.print(
                "[yellow]⚠[/yellow]  [yellow]This model looks small (≤ ~13B parameters). "
                "Academic / scientific PDF translation quality with such models is often "
                "poor. To switch, run:[/yellow]\n"
                "    [cyan]miqi config defaults --model <larger-model>[/cyan]\n"
                "    [cyan]miqi config sync-llm[/cyan]"
            )

    # ------------------------------------------------------------------ #
    # miqi config sync-llm
    # ------------------------------------------------------------------ #

    @config_app.command("sync-llm")
    def config_sync_llm():
        """Re-sync every LLM-backed MCP server with the current miqi default model.

        Any MCP server whose ``env`` block contains an ``OPENAI_API_KEY`` /
        ``OPENAI_MODEL`` pair is treated as LLM-backed. Their three env vars
        (``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``) are
        rewritten from the miqi defaults so they never drift.

        Run this after changing the main model, e.g.::

            miqi config defaults --model anthropic/claude-opus-4-5
            miqi config sync-llm
        """
        from rich.table import Table

        from miqi.config.loader import get_config_path, load_config, save_config

        config = load_config()
        default_model = config.agents.defaults.model
        provider_name = config.get_provider_name(default_model) or ""
        norm_provider = provider_name.lower().replace("-", "_").replace(" ", "_")
        provider_cfg = getattr(config.providers, norm_provider, None) if norm_provider else None

        api_key = (provider_cfg.api_key if provider_cfg else "") or ""
        api_base = (provider_cfg.api_base if provider_cfg else "") or ""
        if api_base and not api_base.rstrip("/").endswith("/v1"):
            api_base = api_base.rstrip("/") + "/v1"

        # Strip provider prefix from model name (anthropic/claude-… → claude-…)
        env_model = default_model.split("/", 1)[1] if "/" in default_model else default_model

        if not api_key:
            console.print(
                f"[red]No API key configured for provider '{norm_provider or '(unset)'}'.[/red]\n"
                "Run `miqi onboard` first."
            )
            raise typer.Exit(1)

        updated: list[tuple[str, str, str]] = []  # (name, old_model, new_model)
        skipped: list[str] = []

        for name, srv in config.tools.mcp_servers.items():
            env = dict(srv.env or {})
            if "OPENAI_API_KEY" not in env or "OPENAI_MODEL" not in env:
                skipped.append(name)
                continue
            old_model = env.get("OPENAI_MODEL", "")
            env["OPENAI_API_KEY"] = api_key
            env["OPENAI_BASE_URL"] = api_base
            env["OPENAI_MODEL"] = env_model
            srv.env = env
            updated.append((name, old_model, env_model))

        save_config(config, get_config_path())

        table = Table(title=f"Synced LLM-backed MCP servers → {env_model}")
        table.add_column("Server", style="cyan")
        table.add_column("Old model")
        table.add_column("New model", style="green")
        for name, old, new in updated:
            table.add_row(name, old or "[dim](unset)[/dim]", new)
        if updated:
            console.print(table)
        else:
            console.print("[dim]No LLM-backed MCP servers found (nothing to sync).[/dim]")

        if skipped:
            console.print(
                f"[dim]Skipped {len(skipped)} non-LLM MCP server(s): "
                f"{', '.join(skipped)}[/dim]"
            )

        if _looks_like_small_model(env_model):
            console.print(
                "[yellow]⚠[/yellow]  [yellow]The current default model looks small "
                "(≤ ~13B parameters). Academic / scientific work may suffer.[/yellow]"
            )

    # ------------------------------------------------------------------ #
    # miqi config mcp list
    # ------------------------------------------------------------------ #

    @mcp_app.command("list")
    def mcp_list():
        """List configured MCP servers."""
        from rich.table import Table

        from miqi.config.loader import load_config

        config = load_config()
        servers = config.tools.mcp_servers

        if not servers:
            console.print("[dim]No MCP servers configured.[/dim]")
            console.print("Add one: [cyan]miqi config mcp add[/cyan]")
            return

        table = Table(title="MCP Servers")
        table.add_column("Name", style="cyan")
        table.add_column("Type")
        table.add_column("Command / URL")
        table.add_column("Timeout", justify="right")
        table.add_column("Lazy")

        for name, srv in servers.items():
            lazy_mark = "[green]yes[/green]" if srv.lazy else "[dim]no[/dim]"
            if srv.command:
                cmd = f"{srv.command} {' '.join(srv.args)}".strip()
                table.add_row(name, "stdio", cmd, f"{srv.tool_timeout}s", lazy_mark)
            elif srv.url:
                table.add_row(name, "http", srv.url, f"{srv.tool_timeout}s", lazy_mark)

        console.print(table)

    # ------------------------------------------------------------------ #
    # miqi config mcp add
    # ------------------------------------------------------------------ #

    @mcp_app.command("add")
    def mcp_add(
        name: str = typer.Argument(None, help="Server name (e.g. my-tool)"),
        command: str = typer.Option(None, "--command", "-c", help="Stdio command"),
        args: list[str] = typer.Option(None, "--arg", "-a", help="Arg (repeatable)"),
        env: list[str] = typer.Option(
            None, "--env", "-e",
            help="Env var KEY=VALUE (repeatable)",
        ),
        url: str = typer.Option(None, "--url", "-u", help="Streamable HTTP URL"),
        header: list[str] = typer.Option(
            None, "--header", "-H",
            help="HTTP header KEY=VALUE (repeatable)",
        ),
        timeout: int = typer.Option(30, "--timeout", "-t", help="Tool call timeout (seconds)"),
        lazy: bool = typer.Option(
            False, "--lazy/--no-lazy",
            help="Register a single gateway entry-point instead of all tools upfront. "
                 "Reduces per-call token cost when the server has many tools.",
        ),
        description: str = typer.Option(
            "", "--description", "-d",
            help="Description shown to the LLM in the gateway tool (lazy mode only). "
                 "Leave blank to auto-generate from tool names.",
        ),
    ):
        """Add or update an MCP server connection.

        Examples:\n
          # stdio\n
          miqi config mcp add pdf2zh --command /path/to/python --arg -m --arg pdf2zh.mcp_server\n\n
          # HTTP with auth header\n
          miqi config mcp add zeopp --url http://192.168.1.10:9877/mcp --header "Authorization=Bearer secret"\n\n
          # lazy mode (reduces token cost for servers with many tools)\n
          miqi config mcp add raspa2 --command /path/to/python --arg -m --arg raspa_mcp.server --lazy --description "RASPA molecular simulation: gas adsorption, GCMC/MD"
        """
        from miqi.config.loader import get_config_path, load_config, save_config
        from miqi.config.schema import MCPServerConfig

        config = load_config()
        interactive = sys.stdin.isatty() and sys.stdout.isatty()

        if name is None:
            if not interactive:
                console.print("[red]Server name is required[/red]")
                raise typer.Exit(1)
            name = typer.prompt("Server name").strip()

        if not command and not url:
            if interactive:
                console.print("\n[bold]Connection type[/bold]")
                console.print("  1. stdio  (local command)")
                console.print("  2. http   (Streamable HTTP)")
                mode = typer.prompt("Type", type=int, default=1)
                if mode == 2:
                    url = typer.prompt("HTTP URL (e.g. http://host:port/mcp)").strip()
                    raw_headers = typer.prompt(
                        "Headers (KEY=VALUE comma-separated, blank for none)",
                        default="",
                        show_default=False,
                    ).strip()
                    if raw_headers:
                        header = [h.strip() for h in raw_headers.split(",") if "=" in h.strip()]
                else:
                    command = typer.prompt("Command (e.g. /path/to/python)").strip()
                    raw_args = typer.prompt(
                        "Args (space-separated, e.g. -m mymodule.server)",
                        default="",
                        show_default=False,
                    ).strip()
                    args = raw_args.split() if raw_args else []
                    raw_env = typer.prompt(
                        "Env vars (KEY=VALUE comma-separated, blank for none)",
                        default="",
                        show_default=False,
                    ).strip()
                    if raw_env:
                        env = [e.strip() for e in raw_env.split(",") if "=" in e.strip()]
                timeout = typer.prompt("Tool timeout (seconds)", type=int, default=timeout)
            else:
                console.print("[red]Provide --command or --url[/red]")
                raise typer.Exit(1)

        srv = config.tools.mcp_servers.get(name, MCPServerConfig())
        srv.tool_timeout = timeout
        srv.lazy = lazy
        if description:
            srv.description = description

        if url:
            srv.url = url
            srv.command = ""
            srv.args = []
            srv.env = {}
            if header:
                srv.headers = dict(h.split("=", 1) for h in header if "=" in h)
        else:
            srv.command = command or srv.command
            srv.args = list(args) if args else srv.args
            srv.url = ""
            srv.headers = {}
            if env:
                env_dict = {k: v for k, v in (e.split("=", 1) for e in env if "=" in e)}
                srv.env.update(env_dict)

        exists = name in config.tools.mcp_servers
        config.tools.mcp_servers[name] = srv
        save_config(config, get_config_path())

        verb = "Updated" if exists else "Added"
        lazy_hint = "  [green](lazy gateway)[/green]" if lazy else ""
        console.print(f"[green]✓[/green] {verb} MCP server [cyan]{name}[/cyan]{lazy_hint}")

    # ------------------------------------------------------------------ #
    # miqi config mcp remove
    # ------------------------------------------------------------------ #

    @mcp_app.command("remove")
    def mcp_remove(
        name: str = typer.Argument(..., help="Server name to remove"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ):
        """Remove an MCP server connection."""
        from miqi.config.loader import get_config_path, load_config, save_config

        config = load_config()
        if name not in config.tools.mcp_servers:
            console.print(f"[red]Server not found:[/red] {name}")
            raise typer.Exit(1)

        if not yes and not typer.confirm(f"Remove MCP server '{name}'?"):
            console.print("[dim]Canceled[/dim]")
            return

        del config.tools.mcp_servers[name]
        save_config(config, get_config_path())
        console.print(f"[green]✓[/green] Removed MCP server [cyan]{name}[/cyan]")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _detect_feishu_mcp_python() -> str | None:
    """Try common installation paths for feishu-mcp."""
    from pathlib import Path

    # Bundled submodule path (set up by scripts/setup_mcps.sh)
    repo_root = Path(__file__).resolve().parents[2]
    bundled = repo_root / "mcps" / "feishu-mcp" / ".venv" / "bin" / "python"
    if bundled.exists():
        return str(bundled)

    # Check PATH for feishu-mcp virtual env python
    candidates = [
        Path.home() / "feishu-mcp" / "feishu-mcp" / "bin" / "python",
        Path.home() / "feishu-mcp" / ".venv" / "bin" / "python",
        Path.home() / ".local" / "share" / "feishu-mcp" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists():
            return str(p)

    # Try finding feishu_mcp module in current python
    try:
        import feishu_mcp  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    return None


def _detect_pdf2zh_mcp_python() -> str | None:
    """Try common installation paths for pdftranslate-mcp / pdf2zh."""
    from pathlib import Path

    # Bundled submodule path (set up by scripts/setup_mcps.sh)
    repo_root = Path(__file__).resolve().parents[2]
    bundled = repo_root / "mcps" / "pdftranslate-mcp" / ".venv" / "bin" / "python"
    if bundled.exists():
        return str(bundled)

    candidates = [
        Path.home() / "pdftranslate-mcp" / ".venv" / "bin" / "python",
        Path.home() / "pdftranslate-mcp" / "pdftranslate-mcp" / "bin" / "python",
        Path.home() / "pdf2zh" / ".venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists():
            return str(p)

    # Try finding pdf2zh module in current python
    try:
        import pdf2zh  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    return None
