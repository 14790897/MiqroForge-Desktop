"""Onboarding command registration for MiQi CLI."""

from __future__ import annotations

import sys
from typing import Callable

import typer


def register_onboard_command(
    app: typer.Typer,
    *,
    console,
    logo: str,
    normalize_agent_name: Callable[[str], str],
    interactive_onboard_setup: Callable,
    create_workspace_templates: Callable,
) -> None:
    """Register onboard command on the root app."""

    @app.command()
    def onboard():
        """Initialize MiQi configuration and workspace."""
        from miqi.config.loader import get_config_path, load_config, save_config
        from miqi.config.schema import DEFAULT_AGENT_NAME, Config
        from miqi.utils.helpers import get_workspace_path

        config_path = get_config_path()
        interactive_onboard = bool(sys.stdin.isatty() and sys.stdout.isatty())

        agent_name = DEFAULT_AGENT_NAME
        soul_preset = "balanced"

        if config_path.exists():
            console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
            console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
            console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
            if typer.confirm("Overwrite?"):
                config = Config()
                if interactive_onboard:
                    agent_name, soul_preset = interactive_onboard_setup(config)
                else:
                    agent_name = normalize_agent_name(config.agents.defaults.name)
                save_config(config)
                console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
            else:
                config = load_config()
                save_config(config)
                agent_name = normalize_agent_name(getattr(config.agents.defaults, "name", DEFAULT_AGENT_NAME))
                console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
        else:
            config = Config()
            if interactive_onboard:
                agent_name, soul_preset = interactive_onboard_setup(config)
            else:
                agent_name = normalize_agent_name(config.agents.defaults.name)
            save_config(config)
            console.print(f"[green]✓[/green] Created config at {config_path}")

        workspace = get_workspace_path()
        created_workspace = not workspace.exists()
        workspace.mkdir(parents=True, exist_ok=True)

        if created_workspace:
            console.print(f"[green]✓[/green] Created workspace at {workspace}")

        create_workspace_templates(
            workspace,
            agent_name=agent_name,
            soul_preset=soul_preset,
        )

        console.print(f"\n{logo} miqi is ready!")
        provider_name = config.get_provider_name(config.agents.defaults.model)
        console.print(f"  Name: [cyan]{config.agents.defaults.name}[/cyan]")
        if provider_name:
            console.print(f"  Provider: [cyan]{provider_name}[/cyan]")
        console.print(f"  Model: [cyan]{config.agents.defaults.model}[/cyan]")
        search_provider = config.tools.web.search.provider
        if search_provider == "tavily":
            status = (
                "[green]Tavily enabled[/green]"
                if config.tools.web.search.tavily_api_key
                else "[yellow]Tavily selected, API key missing[/yellow]"
            )
        elif search_provider == "brave":
            status = (
                "[green]Brave enabled[/green]"
                if config.tools.web.search.brave_api_key
                else "[yellow]Brave selected, API key missing[/yellow]"
            )
        elif search_provider == "auto":
            parts = []
            if config.tools.web.search.tavily_api_key:
                parts.append("Tavily")
            if config.tools.web.search.brave_api_key:
                parts.append("Brave")
            if not parts:
                status = "[green]ddgs enabled (no API key required)[/green]"
            else:
                status = "[green]Auto: " + " → ".join(parts) + " → DDGS[/green]"
        else:
            status = "[green]ddgs enabled (no API key required)[/green]"
        console.print(f"  Web search: {status}")

        console.print("\nNext steps:")
        console.print('  1. Chat: [cyan]miqi agent -m "Hello!"[/cyan]')
        console.print("  2. Start gateway: [cyan]miqi gateway[/cyan]")
        console.print(
            "\n[dim]Want chat app setup? See repository docs: Chat Apps section[/dim]"
        )
