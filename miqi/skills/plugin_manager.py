"""Plugin discovery and lifecycle management.

Plugins are the top-level packaging format. A plugin can contain:
- Multiple MCP servers (tools)
- Multiple skills (instruction sets)
- Slash commands
- Additional configuration
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from miqi.execution.hook_runtime import (
    HookOutcome,
    HookPoint,
    HookRegistration,
    HookRuntime,
)

# ── shared plugin-name validator ──────────────────────────────────────────

_PLUGIN_NAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9_.-]{0,62}[a-zA-Z0-9])?$")

# Hosts allowed for direct plugin installation via URL.
ALLOWED_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}

# 状态文件读-改-写的进程内互斥（#1267 评审 P2）：_set_disabled /
# _set_workspace_trust 全程持锁，并发写不丢更新、不共享临时文件。
# 跨进程并发为文档化约束：单用户桌面场景，最后写者胜出。
_STATE_LOCK = threading.Lock()


def validate_plugin_name(name: str) -> None:
    """Validate a plugin name for safe filesystem and registry use.

    Raises ValueError if the name is invalid.
    """
    if not _PLUGIN_NAME_RE.match(name):
        raise ValueError("Invalid plugin manifest name")
    if ".." in name:
        raise ValueError("Invalid plugin manifest name")


@dataclass
class PluginManifest:
    """Manifest file found in a plugin directory (plugin.json)."""
    name: str
    version: str
    description: str
    author: str = ""
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    slash_commands: list[dict[str, str]] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoadedPlugin:
    """A successfully loaded and active plugin."""
    manifest: PluginManifest
    path: Path
    scope: str  # "user" | "workspace" | "system"
    status: str = "active"  # "active" | "error" | "disabled"
    error: str | None = None
    # Slash commands discovered from filesystem <plugin>/commands/*.md
    # Each entry: {name, description, argument_hint, body}
    slash_command_files: list[dict[str, Any]] = field(default_factory=list)


class PluginManager:
    """Discovers, loads, and manages plugins.

    Plugin search paths (in order):
    1. ~/.forge/plugins/           — user plugins
    2. <workspace>/.forge/plugins/ — workspace plugins
    3. <miqi_install>/plugins/    — system/builtin plugins

    Workspace 插件内容随被打开的项目而来、**默认不可信**（#1267 评审）：
    未显式启用前以 disabled 装载——不注册 hooks、不并入 MCP、命令不注入。
    """

    def __init__(
        self,
        user_plugins_dir: Path,
        system_plugins_dir: Path,
        workspace: Path | None = None,
        hook_runtime: HookRuntime | None = None,
    ):
        self.user_dir = Path(user_plugins_dir)
        self.system_dir = Path(system_plugins_dir)
        self.workspace = workspace
        self._hook_runtime = hook_runtime
        self._plugins: dict[str, LoadedPlugin] = {}
        # #1267 评审：跨会话/跨启动共享的停用状态（与 user 插件目录同级）。
        self._state_path = self.user_dir.parent / "plugins_state.json"

    def _make_command_callback(self, target: str):
        """Build an async callback that runs ``target`` through a shell."""

        async def _callback(ctx) -> HookOutcome:
            proc = await asyncio.create_subprocess_shell(
                target,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode(errors="replace").strip() or "command hook failed"
                return HookOutcome.block(err)
            return HookOutcome.continue_()

        return _callback

    def _make_module_callback(self, plugin_path: Path, target: str):
        """Build an async callback that imports ``pkg.mod:func`` from the plugin path."""
        if ":" not in target:
            raise ValueError(
                f"Module hook target must be 'module.path:func', got: {target}"
            )
        module_path, func_name = target.rsplit(":", 1)

        async def _callback(ctx):
            import sys

            p = str(plugin_path)
            if p not in sys.path:
                sys.path.insert(0, p)
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            result = func(ctx)
            if inspect.isawaitable(result):
                result = await result
            return result

        return _callback

    def _build_hook_registration(
        self,
        source: str,
        plugin_path: Path,
        spec: dict,
    ):
        """Convert a manifest hook spec into a HookRegistration."""
        point = spec["point"].replace("-", "_")
        hook_point = HookPoint[point.upper()]
        tool_pattern = spec.get("match", "*")
        priority = spec.get("priority", 0)
        hook_type = spec["type"]
        target = spec["target"]

        if hook_type == "command":
            callback = self._make_command_callback(target)
        elif hook_type == "module":
            callback = self._make_module_callback(plugin_path, target)
        else:
            raise ValueError(f"Unsupported hook type: {hook_type}")

        return HookRegistration(
            hook_point=hook_point,
            tool_pattern=tool_pattern,
            callback=callback,
            priority=priority,
            source=source,
        )

    def _register_plugin_hooks(self, plugin) -> None:
        """Register all hooks declared by a plugin."""
        if self._hook_runtime is None:
            return
        for spec in plugin.manifest.hooks:
            try:
                reg = self._build_hook_registration(
                    plugin.manifest.name,
                    plugin.path,
                    spec,
                )
            except Exception:
                logger.exception(
                    "Failed to register hook for plugin {}: {}",
                    plugin.manifest.name,
                    spec,
                )
                continue
            self._hook_runtime.register(reg)

    def _unregister_plugin_hooks(self, name: str) -> None:
        """Remove all hook registrations sourced from ``name``."""
        if self._hook_runtime is None:
            return
        self._hook_runtime.unregister_source(name)

    async def discover(self) -> list[LoadedPlugin]:
        """Discover all plugins across all search paths."""
        search_paths = [
            (self.user_dir, "user"),
            (self.system_dir, "system"),
        ]
        if self.workspace:
            search_paths.append(
                (self.workspace / ".forge" / "plugins", "workspace")
            )

        discovered = []
        for base_dir, scope in search_paths:
            if not base_dir.exists():
                continue
            for plugin_dir in base_dir.iterdir():
                if not plugin_dir.is_dir():
                    continue

                # Support both MiQi format (plugin.json) and KWP/Claude Code
                # format (.claude-plugin/plugin.json)
                manifest_path = None
                for candidate in [
                    plugin_dir / "plugin.json",
                    plugin_dir / ".claude-plugin" / "plugin.json",
                ]:
                    if candidate.exists():
                        manifest_path = candidate
                        break

                if manifest_path is None:
                    continue

                try:
                    plugin = await self._load_plugin(
                        plugin_dir, manifest_path, scope
                    )
                    if plugin:
                        self._plugins[plugin.manifest.name] = plugin
                        discovered.append(plugin)
                except Exception as e:
                    logger.error(
                        "Failed to load plugin {}: {}",
                        plugin_dir.name, e,
                    )

        logger.info("Discovered {} plugins", len(discovered))
        return discovered

    async def _load_plugin(
        self,
        plugin_dir: Path,
        manifest_path: Path,
        scope: str,
    ) -> LoadedPlugin | None:
        """Load a single plugin from its directory."""
        import json
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Auto-discover skills from filesystem for KWP-style plugins
        # that don't declare skills explicitly in manifest
        if not manifest_data.get("skills"):
            skills_dir = plugin_dir / "skills"
            if skills_dir.is_dir():
                discovered: list[str] = []
                for d in sorted(skills_dir.iterdir()):
                    if d.is_dir() and (d / "SKILL.md").exists():
                        discovered.append(d.name)
                if discovered:
                    manifest_data["skills"] = discovered

        manifest = PluginManifest(**manifest_data)
        plugin = LoadedPlugin(
            manifest=manifest, path=plugin_dir, scope=scope
        )
        # #1267 评审（P1 信任边界）：全局停用或「未授权的工作区插件」直接
        # 以 disabled 装载——不注册 hooks、不并入 MCP、命令不注入。工作区
        # 内容随被打开的项目而来、可能不可信，必须由用户显式启用（授权按
        # 工作区路径 + 插件名持久化，不跨工作区泄漏）。
        if plugin.manifest.name in self._disabled_names() or (
            plugin.scope == "workspace"
            and not self._workspace_trusted(plugin.manifest.name)
        ):
            plugin.status = "disabled"
        self._attach_plugin_commands(plugin)
        if plugin.status == "active":
            self._register_plugin_hooks(plugin)
        return plugin

    def _attach_plugin_commands(self, plugin: LoadedPlugin) -> None:
        """Discover ``<plugin>/commands/*.md`` and populate slash command cache.

        Called from both ``_load_plugin`` and ``load_plugin_from_dir`` so
        the auto-discovery behaves identically across discovery and
        post-install loading paths.
        """
        commands_dir = plugin.path / "commands"
        if commands_dir.is_dir():
            plugin.slash_command_files = self._load_command_files(
                commands_dir, plugin_name=plugin.manifest.name
            )

    @staticmethod
    def _load_command_files(
        commands_dir: Path, plugin_name: str
    ) -> list[dict[str, Any]]:
        """Parse KWP-style ``commands/<name>.md`` files.

        Returns a list of dicts with keys: name, plugin, description,
        argument_hint, body, path. Body is the markdown content with
        the YAML frontmatter stripped.
        """
        import re as _re

        results: list[dict[str, Any]] = []
        for cmd_file in sorted(commands_dir.glob("*.md")):
            try:
                raw = cmd_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            description = ""
            argument_hint = ""
            body = raw

            # Optional YAML frontmatter (KWP uses simple `key: value`
            # lines, not full YAML). Same regex-based pattern used by
            # miqi/agent/tools/skill_manage.py — fine for our needs.
            fm = _re.match(r"^---\n(.*?)\n---\n", raw, _re.DOTALL)
            if fm:
                for line in fm.group(1).split("\n"):
                    if ":" not in line:
                        continue
                    key, _, value = line.partition(":")
                    value = value.strip().strip("\"'")
                    if key.strip() == "description":
                        description = value
                    elif key.strip() == "argument-hint":
                        argument_hint = value
                body = raw[fm.end():].strip()

            # Strip leading "# /<name>" → "# <name>" so the heading
            # reads as a normal section title.
            body = _re.sub(r"^#\s+/([^\n]+)", r"# \1", body, count=1, flags=_re.MULTILINE)

            results.append({
                "name": cmd_file.stem,
                "plugin": plugin_name,
                "description": description,
                "argument_hint": argument_hint,
                "body": body,
                "path": str(cmd_file),
            })
        return results

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """Collect all MCP server configs from active plugins."""
        servers = []
        for plugin in self._plugins.values():
            if plugin.status != "active":
                continue
            for server in plugin.manifest.mcp_servers:
                resolved = dict(server)
                if "cwd" not in resolved:
                    resolved["cwd"] = str(plugin.path)
                servers.append(resolved)
        return servers

    def get_slash_commands(self) -> dict[str, str]:
        """Collect all slash commands from active plugins.

        Sources (in order of preference):
        1. ``commands/*.md`` files discovered at plugin load time
           (KWP/Cowork convention — body + frontmatter content).
        2. ``manifest.slash_commands`` list (legacy MiQi format).

        Returns a ``{name: description}`` mapping suitable for the
        plugin handler listing, *not* for command dispatch — use
        :meth:`get_slash_command` to retrieve the full body.
        """
        commands: dict[str, str] = {}
        for plugin in self._plugins.values():
            if plugin.status != "active":
                continue
            for cmd in plugin.slash_command_files:
                commands[cmd["name"]] = cmd["description"]
            for cmd in plugin.manifest.slash_commands:
                commands[cmd["name"]] = cmd["description"]
        return commands

    def get_slash_command(self, name: str) -> dict[str, Any] | None:
        """Return a single slash command by name, or ``None`` if missing.

        The returned dict contains ``name``, ``plugin``, ``description``,
        ``argument_hint``, ``body``, ``path``, and ``status`` (copied
        from the owning plugin's status so callers can check whether
        the plugin is active).
        """
        name = (name or "").lstrip(":").strip().lower()
        if not name:
            return None
        for plugin in self._plugins.values():
            for cmd in plugin.slash_command_files:
                if cmd["name"].lower() == name:
                    return {**cmd, "status": plugin.status}
        # Fall back to legacy manifest-declared commands (no body).
        for plugin in self._plugins.values():
            if plugin.status != "active":
                continue
            for cmd in plugin.manifest.slash_commands:
                if cmd.get("name") == name:
                    return {
                        "name": cmd["name"],
                        "plugin": plugin.manifest.name,
                        "description": cmd.get("description", ""),
                        "argument_hint": "",
                        "body": "",
                        "path": "",
                        "status": plugin.status,
                    }
        return None

    def list_plugins(self) -> list[LoadedPlugin]:
        """List all loaded plugins."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> LoadedPlugin | None:
        """Get a loaded plugin by name."""
        return self._plugins.get(name)

    def install_plugin(self, name: str, url: str) -> LoadedPlugin:
        """Install a plugin from a GitHub URL.

        Validates plugin name, clones from URL, discovers the new plugin,
        and returns the loaded plugin. Raises ValueError on invalid input
        or subprocess.CalledProcessError on clone failure.
        """
        import shutil
        import subprocess
        from urllib.parse import urlparse

        # Validate plugin name
        validate_plugin_name(name)

        target_dir = (self.user_dir / name).resolve()
        try:
            target_dir.relative_to(self.user_dir.resolve())
        except ValueError:
            raise ValueError("Invalid plugin path")

        if target_dir.exists():
            raise ValueError(f"Plugin '{name}' already installed")

        # Validate URL
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("Only HTTPS URLs are supported")
        if parsed.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"Unsupported host: {parsed.hostname}")
        if "@" in parsed.netloc:
            raise ValueError("Credentials in URL are not allowed")

        try:
            subprocess.run(
                ["git", "clone", "--depth=1", "--", url, str(target_dir)],
                check=True, capture_output=True, text=True, timeout=60,
            )
        except subprocess.CalledProcessError as e:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise ValueError(f"Clone failed: {e.stderr}") from e
        except Exception:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise

        # Load the newly-installed plugin synchronously.
        # No background discovery — deterministic and immediate.
        try:
            plugin = self.load_plugin_from_dir(target_dir, "user", expected_name=name)
        except Exception:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            raise
        return plugin

    # ── 持久状态与信任（#1267 评审：P1 信任边界 + P2 并发）──────────────────

    def _read_state(self) -> tuple[set[str], set[str]]:
        """(disabled, enabled) 两个集合。缺失/损坏一律视为空——状态文件
        绝不被允许拖垮发现（宁可工具缺席，不可会话起不来）。"""
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set(), set()
        if not isinstance(data, dict):
            return set(), set()

        def _names(key: str) -> set[str]:
            value = data.get(key)
            if not isinstance(value, list):
                return set()
            return {n for n in value if isinstance(n, str)}

        return _names("disabled"), _names("enabled")

    def _write_state(self, disabled: set[str], enabled: set[str]) -> None:
        """原子落盘（临时文件 + replace）。写失败降级为仅本实例内存态并告警。"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_name(self._state_path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {"disabled": sorted(disabled), "enabled": sorted(enabled)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, self._state_path)
        except OSError:
            logger.warning(
                "插件状态写入失败（仅本会话内生效）: {}", self._state_path
            )

    def _set_disabled(self, name: str, disabled: bool) -> None:
        """全局停用/恢复：读-改-写在进程内锁下完成（P2：并发写不丢更新）。"""
        with _STATE_LOCK:
            disabled_set, enabled_set = self._read_state()
            if disabled:
                disabled_set.add(name)
            else:
                disabled_set.discard(name)
            self._write_state(disabled_set, enabled_set)

    def _disabled_names(self) -> set[str]:
        return self._read_state()[0]

    def _workspace_key(self, name: str) -> str | None:
        """工作区插件的信任键：绝对工作区路径 + 插件名（授权不跨工作区）。"""
        if self.workspace is None:
            return None
        return f"{Path(self.workspace).resolve()}::{name}"

    def _workspace_trusted(self, name: str) -> bool:
        key = self._workspace_key(name)
        if key is None:
            return False
        return key in self._read_state()[1]

    def _set_workspace_trust(self, name: str, trusted: bool) -> None:
        """工作区插件的显式授权/撤销（与 _set_disabled 同持锁）。"""
        key = self._workspace_key(name)
        if key is None:
            return
        with _STATE_LOCK:
            disabled_set, enabled_set = self._read_state()
            if trusted:
                enabled_set.add(key)
            else:
                enabled_set.discard(key)
            self._write_state(disabled_set, enabled_set)

    def uninstall_plugin(self, name: str) -> bool:
        """Uninstall a plugin by name.

        ``user`` 作用域：目录真实删除并清除其停用记录（同名重装不被误停用）。
        ``system``（随包内置）作用域：磁盘副本**不删除**——onefile 构建每次启动
        重新解包，开发机上它还是仓库源码；卸载折叠为**持久停用**（写入状态文件，
        由每次发现时生效）。报告成功且下次启动不会「又回来」——不再假装删掉了
        一个删不掉的目录（#1267 评审）。
        """
        import shutil

        validate_plugin_name(name)

        user_target = (self.user_dir / name).resolve()
        try:
            user_target.relative_to(self.user_dir.resolve())
        except ValueError:
            pass
        else:
            if user_target.exists():
                self._unregister_plugin_hooks(name)
                shutil.rmtree(user_target, ignore_errors=True)
                self._plugins.pop(name, None)
                self._set_disabled(name, False)
                return True

        plugin = self._plugins.get(name)
        system_target = (self.system_dir / name).resolve()
        try:
            system_target.relative_to(self.system_dir.resolve())
        except ValueError:
            return False
        if system_target.exists() or (plugin is not None and plugin.scope == "system"):
            self._unregister_plugin_hooks(name)
            self._set_disabled(name, True)
            if plugin is not None:
                plugin.status = "disabled"
            return True
        return False

    def discover_sync(self) -> list[LoadedPlugin]:
        """Synchronous discovery used after install/uninstall in AppServer handlers."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.discover())

        # In a running event loop, direct sync discovery is unsafe.
        # AppServer async handlers should call discover() themselves.
        raise RuntimeError("discover_sync cannot run inside an active event loop")

    def load_plugin_from_dir(
        self,
        plugin_dir: Path,
        scope: str,
        *,
        expected_name: str | None = None,
    ) -> LoadedPlugin:
        """Load one plugin directory synchronously after install.

        Validates the manifest name and optionally confirms it matches an
        expected name (used by install_plugin to prevent name divergence).
        """
        import json

        manifest_path = plugin_dir / "plugin.json"
        if not manifest_path.exists():
            raise ValueError(f"Missing plugin.json in {plugin_dir.name}")
        try:
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError(f"Invalid plugin.json in {plugin_dir.name}")
        manifest_name = manifest_data.get("name", "")
        validate_plugin_name(manifest_name)
        if expected_name is not None and manifest_name != expected_name:
            raise ValueError(
                f"Plugin manifest name '{manifest_name}' does not match "
                f"requested name '{expected_name}'"
            )
        manifest = PluginManifest(**manifest_data)
        plugin = LoadedPlugin(manifest=manifest, path=plugin_dir, scope=scope)
        self._plugins[plugin.manifest.name] = plugin
        self._attach_plugin_commands(plugin)
        self._register_plugin_hooks(plugin)
        return plugin

    def toggle_plugin(self, name: str, enabled: bool) -> LoadedPlugin:
        """Toggle a plugin enabled/disabled（持久化，跨会话/重启生效，#1267 评审）。

        Raises ValueError if the plugin is not found.
        """

        validate_plugin_name(name)

        plugin = self._plugins.get(name)
        if plugin is None:
            raise ValueError(f"Plugin '{name}' not found")

        if enabled:
            plugin.status = "active"
            self._unregister_plugin_hooks(name)
            self._register_plugin_hooks(plugin)
        else:
            plugin.status = "disabled"
            self._unregister_plugin_hooks(name)
        if plugin.scope == "workspace":
            # 工作区插件：启用/停用即授权/撤销（按工作区路径持久化，#1267 评审）
            self._set_workspace_trust(name, enabled)
        else:
            self._set_disabled(name, not enabled)
        return plugin
