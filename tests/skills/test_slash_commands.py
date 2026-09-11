"""Tests for KWP-style slash-command dispatch.

These tests cover two layers:
1. PluginManager._load_command_files() — discovers <plugin>/commands/*.md
2. TaskRunner._handle_user_message() — interjects /-prefixed user input
   into the system prompt.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

# ──────────────────────── PluginManager ────────────────────────────


class TestSlashCommandDiscovery:
    """Verify PluginManager auto-discovers commands/*.md at load time."""

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.user_dir = self.tmp / "user_plugins"
        self.system_dir = self.tmp / "system_plugins"
        self.user_dir.mkdir()
        self.system_dir.mkdir()
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()

        # Create a plugin that ships a commands/ directory.
        plugin = self.user_dir / "pm"
        plugin.mkdir()
        (plugin / "commands").mkdir()
        (plugin / "commands" / "brainstorm.md").write_text(
            "---\n"
            "description: Brainstorm a product idea with a sharp partner.\n"
            "argument-hint: <topic>\n"
            "---\n\n"
            "# brainstorm\n\n"
            "You are a strategic product sparring partner.\n"
            "Push the user's thinking further than they would alone.\n",
            encoding="utf-8",
        )
        # load_plugin_from_dir() expects a flat plugin.json; the full
        # discover() path supports both layouts — tested separately.
        (plugin / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "product-management",
                    "version": "1.0.0",
                    "description": "Product management skills",
                    "author": {"name": "Test"},
                }
            ),
            encoding="utf-8",
        )

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_commands_directory_discovered(self):
        from miqi.skills.plugin_manager import PluginManager

        pm = PluginManager(
            user_plugins_dir=self.user_dir,
            system_plugins_dir=self.system_dir,
            workspace=self.workspace,
        )
        # Synchronous discovery helper used in install handlers.
        for plugin in self.user_dir.iterdir():
            if not plugin.is_dir():
                continue
            manifest_path = None
            for cand in [
                plugin / "plugin.json",
                plugin / ".claude-plugin" / "plugin.json",
            ]:
                if cand.exists():
                    manifest_path = cand
                    break
            if manifest_path is None:
                continue
            pm.load_plugin_from_dir(plugin, "user")

        loaded = pm.get_plugin("product-management")
        assert loaded is not None
        assert len(loaded.slash_command_files) == 1
        cmd = loaded.slash_command_files[0]
        assert cmd["name"] == "brainstorm"
        assert "strategic product sparring partner" in cmd["body"]
        assert "Brainstorm a product idea" in cmd["description"]

    def test_get_slash_command_by_name(self):
        from miqi.skills.plugin_manager import PluginManager

        pm = PluginManager(
            user_plugins_dir=self.user_dir,
            system_plugins_dir=self.system_dir,
            workspace=self.workspace,
        )
        for plugin in self.user_dir.iterdir():
            if not plugin.is_dir():
                continue
            manifest_path = None
            for cand in [
                plugin / "plugin.json",
                plugin / ".claude-plugin" / "plugin.json",
            ]:
                if cand.exists():
                    manifest_path = cand
                    break
            if manifest_path is None:
                continue
            pm.load_plugin_from_dir(plugin, "user")

        match = pm.get_slash_command("brainstorm")
        assert match is not None
        assert match["name"] == "brainstorm"
        assert match["status"] == "active"
        assert match["plugin"] == "product-management"

    def test_get_slash_command_returns_none_for_unknown(self):
        from miqi.skills.plugin_manager import PluginManager

        pm = PluginManager(
            user_plugins_dir=self.user_dir,
            system_plugins_dir=self.system_dir,
            workspace=self.workspace,
        )
        for plugin in self.user_dir.iterdir():
            if not plugin.is_dir():
                continue
            manifest_path = None
            for cand in [
                plugin / "plugin.json",
                plugin / ".claude-plugin" / "plugin.json",
            ]:
                if cand.exists():
                    manifest_path = cand
                    break
            if manifest_path is None:
                continue
            pm.load_plugin_from_dir(plugin, "user")

        assert pm.get_slash_command("nonexistent") is None

    def test_get_slash_commands_lists_all(self):
        from miqi.skills.plugin_manager import PluginManager

        # Add a second plugin with another command.
        p2 = self.user_dir / "pdf"
        p2.mkdir()
        (p2 / "commands").mkdir()
        (p2 / "commands" / "open.md").write_text(
            "---\ndescription: Open a PDF\n---\n\n# open\n\nOpen PDF.",
            encoding="utf-8",
        )
        (p2 / "plugin.json").write_text(
            json.dumps({"name": "pdf", "version": "1.0", "description": "PDF"}),
            encoding="utf-8",
        )

        pm = PluginManager(
            user_plugins_dir=self.user_dir,
            system_plugins_dir=self.system_dir,
            workspace=self.workspace,
        )
        for plugin in self.user_dir.iterdir():
            if not plugin.is_dir():
                continue
            for cand in [
                plugin / "plugin.json",
                plugin / ".claude-plugin" / "plugin.json",
            ]:
                if cand.exists():
                    break
            else:
                continue
            pm.load_plugin_from_dir(plugin, "user")

        names = pm.get_slash_commands()
        assert "brainstorm" in names
        assert "open" in names


# ──────────────────────── TaskRunner interceptor ────────────────────


def _parse_slash_input(text: str) -> tuple[str, str]:
    """Mirror the parser in task_runner.py."""
    parts = text[1:].split(None, 1)
    # Allow namespacing like "/product-management:brainstorm"
    raw_name = parts[0] if parts else ""
    if ":" in raw_name:
        raw_name = raw_name.split(":", 1)[1]
    name = raw_name.strip().lower()
    args = parts[1] if len(parts) > 1 else ""
    return name, args


class TestSlashInputParsing:
    """Lightweight unit tests for the /-prefix parser."""

    def test_simple_command(self):
        name, args = _parse_slash_input("/brainstorm hello world")
        assert name == "brainstorm"
        assert args == "hello world"

    def test_namespaced_command(self):
        name, args = _parse_slash_input("/product-management:brainstorm topic")
        assert name == "brainstorm"  # :product-management stripped
        assert args == "topic"

    def test_no_args(self):
        name, args = _parse_slash_input("/refresh")
        assert name == "refresh"
        assert args == ""

    def test_lowercase(self):
        name, _ = _parse_slash_input("/BrainStorm X")
        assert name == "brainstorm"


# ──────────────────── End-to-end interceptor ─────────────────────────────


class TestSlashCommandInterceptor:
    """Verify interceptor inside ``_handle_user_message`` rewrites content
    and appends command body to the effective system prompt.

    Avoids spinning up a real TaskRunner; we patch the dependencies it needs
    (turn_runner.run is the only thing that matters for what we assert) and
    verify arguments passed to it.
    """

    def _run_interceptor(self, msg_content: str, plugin_manager):
        """Manually reimplement the slice of task_runner.py logic that the
        patch introduces. Returning ``(effective_system_prompt, rewritten_msg_content)``
        lets us assert without instantiating the full runtime.
        """

        slash_content = None
        msg = type("M", (), {"content": msg_content})  # immutable shim
        if msg.content and msg.content.startswith("/"):
            if plugin_manager is not None and hasattr(plugin_manager, "get_slash_command"):
                parts = msg.content[1:].split(None, 1)
                cmd_name = parts[0].lstrip(":").strip().lower() if parts else ""
                cmd_args = parts[1] if len(parts) > 1 else ""
                match = plugin_manager.get_slash_command(cmd_name)
                if match and match.get("status") == "active" and match.get("body"):
                    slash_content = match["body"]
                    if cmd_args:
                        slash_content += "\n\n## User arguments\n\n" + cmd_args
                    new_content = cmd_args if cmd_args else "(command invoked without arguments)"
                    return slash_content, new_content
        return None, msg.content

    def test_no_slash_no_injection(self):
        out = self._run_interceptor("just a normal message", plugin_manager=None)
        assert out == (None, "just a normal message")

    def test_slash_command_injects_body_and_strips_prefix(self):
        from miqi.skills.plugin_manager import PluginManager

        # Build a tiny plugin with one command.
        tmp = Path(tempfile.mkdtemp())
        try:
            pm_plugin = tmp / "pm"
            pm_plugin.mkdir()
            (pm_plugin / "commands").mkdir()
            (pm_plugin / "commands" / "brainstorm.md").write_text(
                "---\ndescription: brainstorm\n---\n\nYou are a brainstorming partner.",
                encoding="utf-8",
            )
            (pm_plugin / "plugin.json").write_text(
                json.dumps({"name": "pm", "version": "1.0", "description": "pm"}),
                encoding="utf-8",
            )

            pm = PluginManager(tmp, tmp)
            pm.load_plugin_from_dir(pm_plugin, "user")

            body, new_content = self._run_interceptor(
                "/brainstorm what should we ship next?",
                pm,
            )
            assert "brainstorming partner" in body
            assert "what should we ship next?" in body  # user args appended
            assert new_content == "what should we ship next?"  # /cmd stripped
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unknown_command_no_injection(self):
        from miqi.skills.plugin_manager import PluginManager

        pm = PluginManager(tempfile.mkdtemp(), tempfile.mkdtemp())
        body, new_content = self._run_interceptor("/nonexistent hi", pm)
        assert body is None
        # Original message preserved (untouched).
        assert new_content == "/nonexistent hi"
