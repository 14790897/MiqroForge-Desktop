"""Management command registration for MiQi CLI."""

from __future__ import annotations

import asyncio
from typing import Callable

import typer
from rich.table import Table


def register_management_commands(
    app: typer.Typer,
    *,
    console,
    logo: str,
    make_provider_getter: Callable[[], Callable],
    print_agent_response,
) -> None:
    """Register channels/memory/session/cron/status/provider command groups."""

    channels_app = typer.Typer(help="Manage channels")
    app.add_typer(channels_app, name="channels")

    @channels_app.command("status")
    def channels_status():
        from miqi.config.loader import load_config

        load_config()

        console.print("[dim]No built-in channels configured.[/dim]")
        console.print("[dim]Use tools.mcpServers to connect external services (e.g. feishu-mcp).[/dim]")

    memory_app = typer.Typer(help="Manage agent memory")
    app.add_typer(memory_app, name="memory")

    def _new_memory_store(config):
        from miqi.agent.memory import MemoryStore

        return MemoryStore(
            workspace=config.workspace_path,
            flush_every_updates=config.agents.memory.flush_every_updates,
            flush_interval_seconds=config.agents.memory.flush_interval_seconds,
            short_term_turns=config.agents.memory.short_term_turns,
            pending_limit=config.agents.memory.pending_limit,
            self_improvement_enabled=config.agents.self_improvement.enabled,
            max_lessons_in_prompt=config.agents.self_improvement.max_lessons_in_prompt,
            min_lesson_confidence=config.agents.self_improvement.min_lesson_confidence,
            max_lessons=config.agents.self_improvement.max_lessons,
            feedback_max_message_chars=config.agents.self_improvement.feedback_max_message_chars,
            feedback_require_prefix=config.agents.self_improvement.feedback_require_prefix,
            promotion_enabled=config.agents.self_improvement.promotion_enabled,
            promotion_min_users=config.agents.self_improvement.promotion_min_users,
            promotion_triggers=config.agents.self_improvement.promotion_triggers,
        )

    @memory_app.command("status")
    def memory_status():
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        status = memory.get_status()

        table = Table(title="Memory Status")
        table.add_column("Field", style="cyan")
        table.add_column("Value")

        fields = [
            ("Snapshot Path", status["snapshot_path"]),
            ("Audit Path", status["audit_path"]),
            ("Snapshot Exists", "yes" if status["snapshot_exists"] else "no"),
            ("Long-term Items", str(status["ltm_items"])),
            ("Short-term Sessions", str(status["short_term_sessions"])),
            ("Pending Sessions", str(status["pending_sessions"])),
            ("Dirty Updates", str(status["dirty_updates"])),
            ("Flush Every Updates", str(status["flush_every_updates"])),
            ("Flush Interval Seconds", str(status["flush_interval_seconds"])),
            ("Last Snapshot At", status["last_snapshot_at"] or "-"),
            ("Self Improvement Enabled", "yes" if status["self_improvement_enabled"] else "no"),
            ("Lessons Count", str(status["lessons_count"])),
            ("Lessons File", status["lessons_file"]),
            ("Lessons Audit File", status["lessons_audit_file"]),
            ("Max Lessons In Prompt", str(status["max_lessons_in_prompt"])),
            ("Min Lesson Confidence", str(status["min_lesson_confidence"])),
            ("Max Lessons", str(status["max_lessons"])),
            ("Feedback Max Chars", str(status["feedback_max_message_chars"])),
            ("Feedback Require Prefix", "yes" if status["feedback_require_prefix"] else "no"),
            ("Promotion Enabled", "yes" if status["promotion_enabled"] else "no"),
            ("Promotion Min Users", str(status["promotion_min_users"])),
            ("Promotion Triggers", ", ".join(status["promotion_triggers"]) or "-"),
        ]

        for name, value in fields:
            table.add_row(name, value)

        console.print(table)

    @memory_app.command("flush")
    def memory_flush():
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        changed = memory.flush(force=True)
        if changed:
            console.print("[green]✓[/green] Memory flushed")
        else:
            console.print("[dim]No pending memory updates[/dim]")

    @memory_app.command("compact")
    def memory_compact(
        max_items: int = typer.Option(300, "--max-items", help="Maximum long-term items to keep"),
    ):
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        removed = memory.compact(max_items=max_items, auto_flush=False)
        memory.flush(force=True)
        console.print(f"[green]✓[/green] Memory compacted (removed {removed} items)")

    @memory_app.command("list")
    def memory_list(
        limit: int = typer.Option(20, "--limit", help="Maximum long-term items to show"),
        session: str | None = typer.Option(
            None, "--session", help="Optional session key to filter snapshot items"
        ),
    ):
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        items = memory.list_snapshot_items(session_key=session, limit=limit)
        if not items:
            console.print("[dim]No snapshot items[/dim]")
            return

        table = Table(title="Long-term Snapshot Items")
        table.add_column("ID", style="cyan")
        table.add_column("Text")
        table.add_column("Source", style="yellow")
        table.add_column("Hits", justify="right")
        table.add_column("Session", style="magenta")
        table.add_column("Updated At", style="green")

        for item in items:
            text = str(item.get("text", ""))
            if len(text) > 90:
                text = text[:87] + "..."
            table.add_row(
                str(item.get("id", "")),
                text,
                str(item.get("source", "")),
                str(item.get("hits", 0)),
                str(item.get("session_key", "")) or "-",
                str(item.get("updated_at", "")),
            )
        console.print(table)

    @memory_app.command("delete")
    def memory_delete(
        memory_id: str = typer.Argument(..., help="Snapshot item id to delete"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Delete without confirmation"),
    ):
        from miqi.config.loader import load_config

        if not yes and not typer.confirm(f"Delete snapshot item {memory_id}?"):
            console.print("[dim]Canceled[/dim]")
            return

        config = load_config()
        memory = _new_memory_store(config)
        removed = memory.delete_snapshot_item(memory_id, immediate=True)
        if removed:
            console.print(f"[green]✓[/green] Deleted snapshot item: {memory_id}")
        else:
            console.print(f"[red]Item not found:[/red] {memory_id}")
            raise typer.Exit(1)

    memory_lessons_app = typer.Typer(help="Manage self-improvement lessons")
    memory_app.add_typer(memory_lessons_app, name="lessons")

    @memory_lessons_app.command("status")
    def memory_lessons_status():
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        status = memory.get_status()

        table = Table(title="Lessons Status")
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_row("Enabled", "yes" if status["self_improvement_enabled"] else "no")
        table.add_row("Lessons Count", str(status["lessons_count"]))
        table.add_row("Lessons File", status["lessons_file"])
        table.add_row("Lessons Audit File", status["lessons_audit_file"])
        table.add_row("Max Lessons In Prompt", str(status["max_lessons_in_prompt"]))
        table.add_row("Min Lesson Confidence", str(status["min_lesson_confidence"]))
        table.add_row("Max Lessons", str(status["max_lessons"]))
        table.add_row("Feedback Max Chars", str(status["feedback_max_message_chars"]))
        table.add_row("Feedback Require Prefix", "yes" if status["feedback_require_prefix"] else "no")
        table.add_row("Promotion Enabled", "yes" if status["promotion_enabled"] else "no")
        table.add_row("Promotion Min Users", str(status["promotion_min_users"]))
        table.add_row("Promotion Triggers", ", ".join(status["promotion_triggers"]) or "-")
        console.print(table)

    @memory_lessons_app.command("list")
    def memory_lessons_list(
        scope: str = typer.Option(
            "all", "--scope", help="Filter by scope: all | session | global"
        ),
        session: str | None = typer.Option(None, "--session", help="Filter by session key"),
        limit: int = typer.Option(20, "--limit", help="Maximum lessons to show"),
        include_disabled: bool = typer.Option(
            False, "--include-disabled", help="Include disabled lessons"
        ),
    ):
        from miqi.config.loader import load_config

        normalized_scope = scope.strip().lower()
        if normalized_scope not in {"all", "session", "global"}:
            console.print("[red]Invalid scope.[/red] Use one of: all, session, global")
            raise typer.Exit(2)

        config = load_config()
        memory = _new_memory_store(config)
        lessons = memory.list_lessons(
            scope=normalized_scope,
            session_key=session,
            limit=limit,
            include_disabled=include_disabled,
        )
        if not lessons:
            console.print("[dim]No lessons matched the filters[/dim]")
            return

        table = Table(title="Self-improvement Lessons")
        table.add_column("ID", style="cyan")
        table.add_column("Scope")
        table.add_column("Enabled")
        table.add_column("Trigger", style="yellow")
        table.add_column("Confidence", justify="right")
        table.add_column("Effective", justify="right")
        table.add_column("Hits", justify="right")
        table.add_column("Updated At", style="green")
        table.add_column("Action")

        for lesson in lessons:
            action = str(lesson.get("better_action", ""))
            if len(action) > 72:
                action = action[:69] + "..."
            table.add_row(
                str(lesson.get("id", "")),
                str(lesson.get("scope", "")),
                "yes" if lesson.get("enabled", True) else "no",
                str(lesson.get("trigger", "")),
                str(lesson.get("confidence", 0)),
                f"{float(lesson.get('effective_confidence', 0.0)):.2f}",
                str(lesson.get("hits", 0)),
                str(lesson.get("updated_at", "")),
                action,
            )
        console.print(table)

    @memory_lessons_app.command("disable")
    def memory_lessons_disable(
        lesson_id: str = typer.Argument(..., help="Lesson id to disable"),
    ):
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        changed = memory.set_lesson_enabled(lesson_id, enabled=False, immediate=True)
        if changed:
            console.print(f"[green]✓[/green] Disabled lesson: {lesson_id}")
        else:
            console.print(f"[red]Lesson not found:[/red] {lesson_id}")
            raise typer.Exit(1)

    @memory_lessons_app.command("enable")
    def memory_lessons_enable(
        lesson_id: str = typer.Argument(..., help="Lesson id to enable"),
    ):
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        changed = memory.set_lesson_enabled(lesson_id, enabled=True, immediate=True)
        if changed:
            console.print(f"[green]✓[/green] Enabled lesson: {lesson_id}")
        else:
            console.print(f"[red]Lesson not found:[/red] {lesson_id}")
            raise typer.Exit(1)

    @memory_lessons_app.command("delete")
    def memory_lessons_delete(
        lesson_id: str = typer.Argument(..., help="Lesson id to delete"),
        yes: bool = typer.Option(False, "--yes", "-y", help="Delete without confirmation"),
    ):
        from miqi.config.loader import load_config

        if not yes and not typer.confirm(f"Delete lesson {lesson_id}?"):
            console.print("[dim]Canceled[/dim]")
            return

        config = load_config()
        memory = _new_memory_store(config)
        removed = memory.delete_lesson(lesson_id, immediate=True)
        if removed:
            console.print(f"[green]✓[/green] Deleted lesson: {lesson_id}")
        else:
            console.print(f"[red]Lesson not found:[/red] {lesson_id}")
            raise typer.Exit(1)

    @memory_lessons_app.command("compact")
    def memory_lessons_compact(
        max_lessons: int = typer.Option(200, "--max-lessons", help="Maximum lessons to keep"),
    ):
        from miqi.config.loader import load_config

        config = load_config()
        memory = _new_memory_store(config)
        removed = memory.compact_lessons(max_lessons=max_lessons, auto_flush=False)
        memory.flush(force=True)
        console.print(f"[green]✓[/green] Lessons compacted (removed {removed} items)")

    @memory_lessons_app.command("reset")
    def memory_lessons_reset(
        yes: bool = typer.Option(False, "--yes", "-y", help="Reset without confirmation"),
    ):
        from miqi.config.loader import load_config

        if not yes and not typer.confirm("Reset all lessons?"):
            console.print("[dim]Canceled[/dim]")
            return

        config = load_config()
        memory = _new_memory_store(config)
        removed = memory.reset_lessons()
        console.print(f"[green]✓[/green] Lessons reset (removed {removed} items)")

    session_app = typer.Typer(help="Manage conversation sessions")
    app.add_typer(session_app, name="session")

    def _new_session_manager(config):
        from miqi.session.manager import SessionManager

        return SessionManager(
            config.workspace_path,
            compact_threshold_messages=config.agents.sessions.compact_threshold_messages,
            compact_threshold_bytes=config.agents.sessions.compact_threshold_bytes,
            compact_keep_messages=config.agents.sessions.compact_keep_messages,
        )

    @session_app.command("compact")
    def session_compact(
        session_id: str | None = typer.Option(None, "--session", "-s", help="Session ID to compact"),
        all: bool = typer.Option(False, "--all", "-a", help="Compact all sessions"),
    ):
        from miqi.config.loader import load_config

        config = load_config()
        manager = _new_session_manager(config)

        if all:
            compacted = manager.compact_all()
            console.print(f"[green]✓[/green] Compacted {compacted} sessions")
            return

        if not session_id:
            console.print("[red]Error: use --session <id> or --all[/red]")
            raise typer.Exit(1)

        if manager.compact(session_id):
            console.print(f"[green]✓[/green] Compacted session {session_id}")
        else:
            console.print(f"[red]Session {session_id} not found[/red]")

    cron_app = typer.Typer(help="Manage scheduled tasks")
    app.add_typer(cron_app, name="cron")

    @cron_app.command("list")
    def cron_list(
        all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
    ):
        import time
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo

        from miqi.config.loader import get_data_dir
        from miqi.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)

        jobs = service.list_jobs(include_disabled=all)

        if not jobs:
            console.print("No scheduled jobs.")
            return

        table = Table(title="Scheduled Jobs")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Schedule")
        table.add_column("Status")
        table.add_column("Next Run")

        for job in jobs:
            if job.schedule.kind == "every":
                sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
            elif job.schedule.kind == "cron":
                sched = f"{job.schedule.expr or ''} ({job.schedule.tz})" if job.schedule.tz else (job.schedule.expr or "")
            else:
                sched = "one-time"

            next_run = ""
            if job.state.next_run_at_ms:
                ts = job.state.next_run_at_ms / 1000
                try:
                    tz = ZoneInfo(job.schedule.tz) if job.schedule.tz else None
                    next_run = _dt.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    next_run = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

            status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

            table.add_row(job.id, job.name, sched, status, next_run)

        console.print(table)

    @cron_app.command("add")
    def cron_add(
        name: str = typer.Option(..., "--name", "-n", help="Job name"),
        message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
        every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
        cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
        tz: str | None = typer.Option(None, "--tz", help="IANA timezone for cron (e.g. 'America/Vancouver')"),
        at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
        deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
        to: str = typer.Option(None, "--to", help="Recipient for delivery"),
        channel: str = typer.Option(
            None, "--channel", help="Channel for delivery (e.g. 'feishu')"
        ),
    ):
        from miqi.config.loader import get_data_dir
        from miqi.cron.service import CronService
        from miqi.cron.types import CronSchedule

        if tz and not cron_expr:
            console.print("[red]Error: --tz can only be used with --cron[/red]")
            raise typer.Exit(1)

        if every:
            schedule = CronSchedule(kind="every", every_ms=every * 1000)
        elif cron_expr:
            schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
        elif at:
            import datetime

            dt = datetime.datetime.fromisoformat(at)
            schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
        else:
            console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
            raise typer.Exit(1)

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)

        try:
            job = service.add_job(
                name=name,
                schedule=schedule,
                message=message,
                deliver=deliver,
                to=to,
                channel=channel,
            )
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1) from e

        console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")

    @cron_app.command("remove")
    def cron_remove(
        job_id: str = typer.Argument(..., help="Job ID to remove"),
    ):
        from miqi.config.loader import get_data_dir
        from miqi.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)

        if service.remove_job(job_id):
            console.print(f"[green]✓[/green] Removed job {job_id}")
        else:
            console.print(f"[red]Job {job_id} not found[/red]")

    @cron_app.command("enable")
    def cron_enable(
        job_id: str = typer.Argument(..., help="Job ID"),
        disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
    ):
        from miqi.config.loader import get_data_dir
        from miqi.cron.service import CronService

        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)

        job = service.enable_job(job_id, enabled=not disable)
        if job:
            status = "disabled" if disable else "enabled"
            console.print(f"[green]✓[/green] Job '{job.name}' {status}")
        else:
            console.print(f"[red]Job {job_id} not found[/red]")

    @cron_app.command("run")
    def cron_run(
        job_id: str = typer.Argument(..., help="Job ID to run"),
        force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
    ):
        from loguru import logger

        from miqi.config.loader import get_data_dir, load_config
        from miqi.cron.service import CronService
        from miqi.cron.types import CronJob

        logger.enable("miqi")
        logger.remove()
        import sys as _sys
        logger.add(
            _sys.stderr,
            format="<level>[miqi] {name}:{function}:{line} | {message}</level>",
            level="INFO",
            colorize=True,
        )

        config = load_config()
        store_path = get_data_dir() / "cron" / "jobs.json"
        service = CronService(store_path)
        provider = make_provider_getter()(config)

        # Historical (Phase 14): use RuntimeSession, not AgentLoop
        result_holder = []

        async def on_job(job: CronJob) -> str | None:
            from miqi.runtime.client import RuntimeClient
            from miqi.runtime.session import RuntimeSession

            session_id = f"cron:{job.id}"
            runtime = RuntimeSession.create(
                config=config,
                provider=provider,
                session_id=session_id,
                workspace=config.workspace_path,
            )
            await runtime.start()
            try:
                response = await RuntimeClient(runtime).ask(
                    job.payload.message,
                    thread_id=session_id,
                )
                result_holder.append(response)
                return response
            finally:
                await runtime.stop()

        service.on_job = on_job

        async def run():
            return await service.run_job(job_id, force=force)

        if asyncio.run(run()):
            console.print("[green]✓[/green] Job executed")
            if result_holder:
                print_agent_response(result_holder[0], render_markdown=True)
        else:
            console.print(f"[red]Failed to run job {job_id}[/red]")

    @app.command()
    def status():
        from miqi.config.loader import get_config_path, load_config

        config_path = get_config_path()
        config = load_config()
        workspace = config.workspace_path

        console.print(f"{logo} MiQroForge Status\n")

        console.print(
            f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}"
        )
        console.print(
            f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}"
        )

        if config_path.exists():
            from miqi.providers.registry import PROVIDERS

            console.print(f"Model: {config.agents.defaults.model}")
            active_provider = config.get_provider_name(config.agents.defaults.model)

            for spec in PROVIDERS:
                p = getattr(config.providers, spec.name, None)
                if p is None:
                    continue
                active_badge = " [cyan](active)[/cyan]" if spec.name == active_provider else ""
                if spec.is_local:
                    if p.api_base:
                        console.print(f"{spec.label}{active_badge}: [green]✓ {p.api_base}[/green]")
                    else:
                        console.print(f"{spec.label}{active_badge}: [dim]not set[/dim]")
                else:
                    has_key = bool(p.api_key)
                    if has_key and p.api_base:
                        console.print(f"{spec.label}{active_badge}: [green]✓[/green] ({p.api_base})")
                    else:
                        console.print(
                            f"{spec.label}{active_badge}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}"
                        )
