"""Gateway command registration for MiQi CLI."""

from __future__ import annotations

import asyncio

import typer


def register_gateway_command(
    app: typer.Typer,
    *,
    console,
    logo: str,
    make_provider,
) -> None:
    """Register gateway command on the root app."""

    @app.command()
    def gateway(
        port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    ):
        """Start the MiQi gateway."""
        from loguru import logger

        from miqi.bus.queue import MessageBus
        from miqi.channels.manager import ChannelManager
        from miqi.config.loader import get_data_dir, load_config
        from miqi.cron.service import CronService
        from miqi.cron.types import CronJob
        from miqi.heartbeat.service import HeartbeatService
        from miqi.session.manager import SessionManager

        # Configure loguru for gateway mode
        logger.enable("miqi")
        logger.remove()
        import sys
        if verbose:
            logger.add(
                sys.stderr,
                format="<level>[miqi-gateway] {name}:{function}:{line} | {message}</level>",
                level="DEBUG",
                colorize=True,
            )
        else:
            logger.add(
                sys.stderr,
                format="<level>[miqi-gateway] {name}:{function}:{line} | {message}</level>",
                level="INFO",
                colorize=True,
            )

        console.print(f"{logo} Starting MiQroForge gateway on port {port}...")

        config = load_config()

        bus = MessageBus()
        provider = make_provider(config)
        session_manager = SessionManager(
            config.workspace_path,
            compact_threshold_messages=config.agents.sessions.compact_threshold_messages,
            compact_threshold_bytes=config.agents.sessions.compact_threshold_bytes,
            compact_keep_messages=config.agents.sessions.compact_keep_messages,
        )

        cron_store_path = get_data_dir() / "cron" / "jobs.json"
        cron = CronService(cron_store_path, job_timeout=config.cron.job_timeout_seconds)

        # Historical (Phase 14 follow-up): RuntimeSession owns ALL gateway processing.
        # No more AgentLoop — channels route through GatewayRuntimeDispatcher.
        from miqi.runtime.client import RuntimeClient
        from miqi.runtime.gateway_dispatcher import GatewayRuntimeDispatcher
        from miqi.runtime.session import RuntimeSession

        gateway_runtime = RuntimeSession.create(
            config=config,
            provider=provider,
            session_id="gateway:default",
            workspace=config.workspace_path,
        )
        gateway_client = RuntimeClient(gateway_runtime)

        async def on_cron_job_via_runtime(job: CronJob) -> str | None:
            response = await gateway_client.ask(
                job.payload.message,
                thread_id=f"cron:{job.id}",
            )
            if job.payload.deliver and job.payload.to:
                from miqi.bus.events import OutboundMessage
                await bus.publish_outbound(
                    OutboundMessage(
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to,
                        content=response or "",
                    )
                )
            return response

        cron.on_job = on_cron_job_via_runtime

        channels = ChannelManager(config, bus)

        def _pick_heartbeat_target() -> tuple[str, str]:
            enabled = set(channels.enabled_channels)
            for item in session_manager.list_sessions():
                key = item.get("key") or ""
                if ":" not in key:
                    continue
                channel, chat_id = key.split(":", 1)
                if channel in {"cli", "system"}:
                    continue
                if channel in enabled and chat_id:
                    return channel, chat_id
            return "cli", "direct"

        async def on_heartbeat(prompt: str) -> str:
            return await gateway_client.ask(
                prompt,
                thread_id="heartbeat",
            )

        async def on_heartbeat_notify(response: str) -> None:
            from miqi.bus.events import OutboundMessage
            channel, chat_id = _pick_heartbeat_target()
            if channel == "cli":
                return
            await bus.publish_outbound(
                OutboundMessage(channel=channel, chat_id=chat_id, content=response)
            )

        heartbeat_interval_s = max(1, config.heartbeat.interval_seconds)
        heartbeat = HeartbeatService(
            workspace=config.workspace_path,
            on_heartbeat=on_heartbeat,
            on_notify=on_heartbeat_notify,
            interval_s=heartbeat_interval_s,
            enabled=config.heartbeat.enabled,
        )

        # Channel dispatch: GatewayRuntimeDispatcher (Historical: replaces AgentLoop.run())
        channel_dispatcher = GatewayRuntimeDispatcher(
            bus=bus,
            channel_manager=channels,
            runtime=gateway_runtime,
            client=gateway_client,
        )

        if channels.enabled_channels:
            console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
        else:
            console.print("[yellow]Warning: No channels enabled[/yellow]")

        if config.heartbeat.enabled:
            if heartbeat_interval_s % 60 == 0:
                console.print(
                    f"[green]✓[/green] Heartbeat: every {heartbeat_interval_s // 60}m"
                )
            else:
                console.print(f"[green]✓[/green] Heartbeat: every {heartbeat_interval_s}s")
        else:
            console.print("[yellow]Heartbeat disabled[/yellow]")

        async def run():
            try:
                # Phase 14 follow-up: start runtime once, reuse for all calls
                await gateway_runtime.start()
                await cron.start()

                cron_status = cron.status()
                if cron_status["jobs"] > 0:
                    console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

                await heartbeat.start()
                await asyncio.gather(
                    channel_dispatcher.run(),
                    channels.start_all(),
                )
            except KeyboardInterrupt:
                console.print("\nShutting down...")
            finally:
                # Phase 14 follow-up: stop runtime once
                await gateway_runtime.stop()
                heartbeat.stop()
                cron.stop()
                await channels.stop_all()

        asyncio.run(run())
