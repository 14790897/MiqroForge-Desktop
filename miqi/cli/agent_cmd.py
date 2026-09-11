"""Agent command registration for MiQi CLI."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import nullcontext
from typing import Any

import typer


def register_agent_command(
    app: typer.Typer,
    *,
    console,
    logo: str,
    make_provider,
    print_agent_response,
    init_prompt_session,
    flush_pending_tty_input,
    read_interactive_input_async,
    is_exit_command,
    restore_terminal,
) -> None:
    """Register agent command on the root app."""

    @app.command()
    def agent(
        message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
        session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
        markdown: bool = typer.Option(
            True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
        ),
        logs: bool = typer.Option(
            True, "--logs/--no-logs", help="Show runtime logs during chat"
        ),
    ):
        """Interact with the agent directly."""
        from loguru import logger

        from miqi.config.loader import get_data_dir, load_config
        from miqi.cron.service import CronService

        # Configure loguru: enable miqi namespace and set level
        logger.enable("miqi")
        if not logs:
            # When --no-logs, suppress loguru output but don't fully disable
            # (errors and warnings should still be visible)
            logger.remove()
            logger.add(
                sys.stderr,
                format="<level>[miqi] {name}:{function}:{line} | {message}</level>",
                level="WARNING",
                colorize=True,
            )

        config = load_config()
        provider = make_provider(config)

        cron_store_path = get_data_dir() / "cron" / "jobs.json"
        cron = CronService(cron_store_path, job_timeout=config.cron.job_timeout_seconds)

        def _thinking_ctx():
            if logs:
                return nullcontext()
            return console.status("[dim]miqi is thinking...[/dim]", spinner="dots")

        if message:
            async def run_once():
                with _thinking_ctx():
                    response = await _run_agent_once_via_runtime(
                        config, provider, message, session_id,
                    )
                print_agent_response(response, render_markdown=markdown)

            asyncio.run(run_once())
        else:
            from miqi.runtime.client import RuntimeClient
            from miqi.runtime.session import RuntimeSession

            init_prompt_session()
            console.print(
                f"{logo} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n"
            )

            def _exit_on_sigint(signum, frame):
                restore_terminal()
                console.print("\nGoodbye!")
                os._exit(0)

            signal.signal(signal.SIGINT, _exit_on_sigint)

            async def run_interactive():
                # Historical (Phase 14): create RuntimeSession, not AgentLoop
                runtime = RuntimeSession.create(
                    config=config,
                    provider=provider,
                    session_id=session_id,
                    workspace=config.workspace_path,
                )
                await runtime.start()
                await cron.start()
                client = RuntimeClient(runtime)

                async def _runtime_event_to_cli_progress(event):
                    name = event.__class__.__name__
                    if name.endswith("BeginEvent"):
                        data = getattr(event, "__dict__", {})
                        hint = data.get("tool_display") or data.get("tool_name", "")
                        if hint:
                            console.print(f"  [dim]↳ {hint}[/dim]")

                async def on_cron_job_via_runtime(job) -> str | None:
                    return await client.ask(
                        job.payload.message,
                        thread_id=f"cron:{job.id}",
                        on_event=_runtime_event_to_cli_progress,
                    )

                cron.on_job = on_cron_job_via_runtime

                try:
                    while True:
                        try:
                            flush_pending_tty_input()
                            user_input = await read_interactive_input_async()
                            command = user_input.strip()
                            if not command:
                                continue

                            if is_exit_command(command):
                                restore_terminal()
                                console.print("\nGoodbye!")
                                break

                            with _thinking_ctx():
                                response = await client.ask(
                                    user_input,
                                    thread_id=session_id,
                                    on_event=_runtime_event_to_cli_progress,
                                )

                            if response:
                                print_agent_response(response, render_markdown=markdown)
                        except KeyboardInterrupt:
                            restore_terminal()
                            console.print("\nGoodbye!")
                            break
                        except EOFError:
                            restore_terminal()
                            console.print("\nGoodbye!")
                            break
                finally:
                    await runtime.stop()

            asyncio.run(run_interactive())


async def _run_agent_once_via_runtime(
    config: Any,
    provider: Any,
    message: str,
    session_id: str,
) -> str:
    """Run a single-turn agent request through RuntimeSession + RuntimeClient.

    Phase 14: uses RuntimeClient.ask() instead of manual event drain.
    """
    from miqi.runtime.client import RuntimeClient
    from miqi.runtime.session import RuntimeSession

    runtime = RuntimeSession.create(
        config=config,
        provider=provider,
        session_id=session_id,
        workspace=config.workspace_path,
    )
    await runtime.start()
    try:
        return await RuntimeClient(runtime).ask(message, thread_id=session_id)
    finally:
        await runtime.stop()
