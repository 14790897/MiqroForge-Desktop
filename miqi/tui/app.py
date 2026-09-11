"""MiQroForge TUI — Textual-based terminal interface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static


class MiQiTui(App):
    """MiQroForge Terminal User Interface.

    Launch with:
        uv run python -m miqi.tui.app
    or via CLI:
        miqi tui
    """

    BINDINGS = [
        Binding("ctrl+c", "abort", "Abort current turn", priority=True),
        Binding("ctrl+n", "new_thread", "New conversation thread"),
        Binding("ctrl+p", "toggle_plan", "Toggle plan sidebar"),
        Binding("ctrl+s", "save_session", "Save current session"),
    ]

    CSS = """
    #chat {
        height: 1fr;
        overflow-y: auto;
        padding: 1;
    }
    #plan-sidebar {
        width: 30%;
        border-left: solid gray;
        padding: 1;
    }
    #input {
        dock: bottom;
        margin: 1;
    }
    """

    _plan_visible: bool = False
    _runtime: object = None  # RuntimeSession (Phase 14)
    _client: object = None   # RuntimeClient (Phase 14)

    async def connect_runtime(
        self,
        provider: Any,
        workspace: Path,
        model: str = "default",
    ) -> None:
        """Connect to MiQroForge runtime via RuntimeSession (Phase 14).

        Historical: Does NOT construct AgentLoop directly. Uses RuntimeSession
        which owns the full service graph internally.
        """
        from miqi.config.loader import load_config
        from miqi.runtime.client import RuntimeClient
        from miqi.runtime.session import RuntimeSession

        config = load_config(workspace)
        self._runtime = RuntimeSession.create(
            config=config,
            provider=provider,
            session_id="tui:default",
            workspace=workspace,
        )
        await self._runtime.start()
        self._client = RuntimeClient(self._runtime)

        self._append_message(
            "System",
            f"Connected to MiQroForge runtime (model: {model}, workspace: {workspace})",
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield Vertical(Static(id="chat"), id="chat-container")
            yield Vertical(Static(id="plan-sidebar"), id="plan-container")
        yield Input(placeholder="Type a message...", id="input")
        yield Footer()

    def on_mount(self) -> None:
        """Called when the app is mounted."""
        self.query_one("#chat", Static).update(
            "Welcome to MiQroForge TUI!\n"
            "Type a message below and press Enter to send.\n"
            "Ctrl+C: abort | Ctrl+N: new thread | Ctrl+P: toggle plan",
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Send message when user presses Enter."""
        if not event.value.strip():
            return
        content = event.value.strip()
        self._append_message("You", content)
        event.input.value = ""

        # If runtime is connected, process the message
        if self._client is not None:
            self._process_message(content)
        else:
            self._append_message(
                "MiQroForge",
                "(No runtime connected. Use `miqi tui --connect` to connect, "
                "or call app.connect_runtime(provider, workspace) in code.)",
            )

    def action_abort(self) -> None:
        """Abort the current turn."""
        if self._runtime is not None:
            try:
                import asyncio as _asyncio

                from miqi.protocol.commands import AbortTurn
                _asyncio.run(self._runtime.submit(AbortTurn(thread_id="tui:default")))
                self._append_message("System", "Turn aborted.")
            except Exception:
                pass

    def action_new_thread(self) -> None:
        """Start a new conversation thread."""
        chat = self.query_one("#chat", Static)
        chat.update("New thread started.\nType a message below and press Enter.")
        self._append_message("System", "New thread.")

    def action_toggle_plan(self) -> None:
        """Toggle plan sidebar visibility."""
        sidebar = self.query_one("#plan-sidebar", Static)
        self._plan_visible = not self._plan_visible
        sidebar.display = self._plan_visible
        sidebar.update("Plan sidebar" if self._plan_visible else "")

    def action_save_session(self) -> None:
        """Save current session."""
        self._append_message("System", "Session saved.")

    def update_plan(self, plan_data: dict) -> None:
        """Update the plan sidebar with current plan state."""
        from rich.markup import escape

        sidebar = self.query_one("#plan-sidebar", Static)
        steps = plan_data.get("steps", [])
        if not steps:
            sidebar.update("")
            return

        title = escape(str(plan_data.get("title", "Plan")))
        lines = [f"[bold]{title}[/bold]\n"]
        icons = {"pending": "○", "in_progress": "●", "completed": "✓", "skipped": "—"}
        for step in steps:
            icon = icons.get(step.get("status", "pending"), "?")
            desc = escape(str(step.get("description", "")))
            status = step.get("status", "pending")
            if status == "in_progress":
                lines.append(f"[bold blue]{icon} {desc}[/bold blue]")
            elif status == "completed":
                lines.append(f"[green]{icon} {desc}[/green]")
            elif status == "skipped":
                lines.append(f"[dim]{icon} {desc}[/dim]")
            else:
                lines.append(f"{icon} {desc}")
        sidebar.update("\n".join(lines))

    def show_diff(self, filepath: str, old: str, new: str) -> None:
        """Show an inline diff in the chat area."""
        import difflib

        from rich.markup import escape

        chat = self.query_one("#chat", Static)
        current = str(chat.renderable or "")

        diff = list(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{filepath}",
            tofile=f"b/{filepath}",
            lineterm="",
        ))

        formatted: list[str] = []
        for line in diff:
            escaped = escape(line)
            if line.startswith("@@"):
                formatted.append(f"[cyan]{escaped}[/cyan]")
            elif line.startswith("+"):
                formatted.append(f"[green]{escaped}[/green]")
            elif line.startswith("-"):
                formatted.append(f"[red]{escaped}[/red]")
            else:
                formatted.append(escaped)

        chat.update(
            f"{current}\n\n[bold]Diff: {escape(filepath)}[/bold]\n"
            + "\n".join(formatted)
        )

    def _append_message(self, sender: str, content: str) -> None:
        """Append a message to the chat display."""
        chat = self.query_one("#chat", Static)
        current = chat.renderable or ""
        chat.update(f"{current}\n\n[{sender}]: {content}")

    def _process_message(self, content: str) -> None:
        """Process a message through RuntimeClient (Phase 14)."""
        import asyncio

        async def _run() -> None:
            try:
                response = await self._client.ask(  # type: ignore[union-attr]
                    content,
                    thread_id="tui:default",
                    on_event=self._handle_runtime_event,
                )
                self._append_message("MiQroForge", response or "(no response)")
            except Exception as e:
                self._append_message("MiQroForge", f"Error: {e}")

        asyncio.create_task(_run())

    def _handle_runtime_event(self, event: object) -> None:
        """Handle runtime progress events — update plan/diff when appropriate."""
        name = event.__class__.__name__
        if name.lower().endswith("beginevent"):
            data = getattr(event, "__dict__", {})
            hint = data.get("tool_display") or data.get("tool_name", "")
            if hint:
                self._append_message("Tool", f"🔧 {hint}")
        elif name.lower().endswith("endevent"):
            pass  # Tool end — output shown in main answer


def _load_runtime_from_config() -> tuple[Any, Path, str] | None:
    """Load a provider, workspace, and model from config for TUI.

    Extracted so it can be tested without launching Textual.
    Returns (provider, workspace, model) or None on failure.
    """
    from pathlib import Path

    from miqi.config.loader import load_config
    from miqi.providers.factory import make_provider

    try:
        workspace = Path.cwd()
        config = load_config(workspace)
        provider = make_provider(config)
        model = getattr(config.agents.defaults, 'model', 'default')
        return provider, workspace, model
    except Exception:
        return None


async def main() -> None:
    """Entry point: create TUI with optional runtime connection."""
    runtime = _load_runtime_from_config()
    app = MiQiTui()
    if runtime:
        provider, workspace, model = runtime
        await app.connect_runtime(provider, workspace, model)
    await app.run_async()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
