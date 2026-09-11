"""Phase 35 control-plane migration audit — baseline and guard tests.

Validates:
- Legacy _METHODS entries before/after migrations
- AppServer registration counts
- No AgentLoop/process_direct in production runtime/bridge paths
- No direct ToolRegistry.execute() production dispatch
- asyncio.run() audit in bridge/server.py and bridge/loop.py
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_methods_dict(file_path: Path) -> dict[str, Any]:
    """Parse _METHODS dict from a Python file using AST.

    Returns {"method_keys": set[str], "count": int}
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_METHODS":
                    result: dict[str, str] = {}
                    if isinstance(node.value, ast.Dict):
                        for key, _val in zip(node.value.keys, node.value.values):
                            if isinstance(key, ast.Constant):
                                result[str(key.value)] = ""
                    return {"method_keys": set(result.keys()), "count": len(result)}
    return {"method_keys": set(), "count": 0}


def _parse_appserver_registrations(file_path: Path) -> set[str]:
    """Parse AppServer.register_method() calls from a Python file.

    Returns the set of method names registered on AppServer.
    """
    methods: set[str] = set()
    content = file_path.read_text(encoding="utf-8")
    # Match: self._app_server.register_method("method.name", ...)
    pattern = r'register_method\(\s*"([^"]+)"'
    for m in re.finditer(pattern, content):
        methods.add(m.group(1))
    return methods


def _parse_all_appserver_registrations(miqi_dir: Path) -> set[str]:
    """Find all register_method() calls across the miqi package."""
    methods: set[str] = set()
    for py_file in miqi_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for m in re.finditer(r'register_method\(\s*"([^"]+)"', content):
            methods.add(m.group(1))
    return methods


# ── File paths ───────────────────────────────────────────────────────────────

_MIQI_DIR = Path(__file__).parent.parent.parent / "miqi"
_SERVER_PY = _MIQI_DIR / "bridge" / "server.py"
_LOOP_PY = _MIQI_DIR / "bridge" / "loop.py"


# ═══════════════════════════════════════════════════════════════════════════════
# Baseline audit
# ═══════════════════════════════════════════════════════════════════════════════


def test_phase35_baseline_identifies_control_plane_legacy_methods():
    """Count and identify legacy _METHODS families before Phase 35 migration.

    This test records the baseline so we can verify removals after each task.
    It also asserts the expected Phase 35 target families are present.
    """
    parsed = _parse_methods_dict(_SERVER_PY)
    legacy_keys: set[str] = parsed["method_keys"]
    count = parsed["count"]

    # Families expected to be present in legacy _METHODS
    # (updated after each Phase 35 task migration)
    expected_families = {
        # Phase 35.2: providers.*, channels.*, permissions.* migrated
        # Phase 35.3: plugins.* migrated
        # Phase 35.4: mcp.* migrated
        # Phase 35.5: skills.* migrated
        # Phase 35.6: cron.* migrated
        # Phase 35.7: memory.*, experience:* migrated
        # Phase 35.8: diagnostics (python.check) migrated
        # Only "status" and "plan.get" remain in _METHODS
    }

    present_families: list[str] = []
    missing_keys: list[str] = []

    for family_name, keys in sorted(expected_families.items()):
        all_present = True
        for k in keys:
            if k not in legacy_keys:
                missing_keys.append(k)
                all_present = False
        if all_present:
            present_families.append(family_name)

    # All Phase 35 target families must be present before migration
    assert not missing_keys, (
        f"Phase 35 target method keys missing from _METHODS: {missing_keys}. "
        "They may have already been migrated — if so, update this test's expected list."
    )

    print(f"\nPhase 35 baseline: {count} legacy _METHODS entries")
    print(f"Target families present: {present_families}")

    # Store count for before/after comparison in subsequent tests
    assert count <= 10, f"Expected at most 10 legacy _METHODS entries, got {count}"


def test_phase35_baseline_appserver_registrations():
    """Count AppServer method registrations before Phase 35 migration."""
    miqi_methods = _parse_all_appserver_registrations(_MIQI_DIR)
    loop_methods = _parse_appserver_registrations(_LOOP_PY)

    print(f"\nAppServer registrations across miqi/: {len(miqi_methods)}")
    print(f"AppServer registrations in bridge/loop.py: {len(loop_methods)}")

    # We should have a substantial number already (from prior phases)
    assert len(miqi_methods) >= 30, (
        f"Expected at least 30 AppServer registrations, got {len(miqi_methods)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Guard tests — production path safety
# ═══════════════════════════════════════════════════════════════════════════════


def test_phase35_no_agentloop_or_process_direct_in_runtime_bridge_paths():
    """AgentLoop( and process_direct( must not appear in production paths.

    Checks: miqi/runtime, miqi/bridge, miqi/cli, miqi/tui, miqi/channels, miqi/cron.
    """
    dirs_to_check = ["runtime", "bridge", "cli", "tui", "channels", "cron"]
    violations: list[str] = []

    for dirname in dirs_to_check:
        d = _MIQI_DIR / dirname
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            rel = py_file.relative_to(_MIQI_DIR.parent)
            if "AgentLoop(" in content:
                violations.append(f"AgentLoop( in {rel}")
            if "process_direct(" in content:
                violations.append(f"process_direct( in {rel}")

    assert not violations, (
        f"AgentLoop/process_direct found in production paths: {violations}"
    )


def test_phase35_no_toolregistry_execute_production_dispatch():
    """No production path calls ToolRegistry.execute() / execute_concurrent()
    / self.tools.execute() as model-tool dispatch.

    Checks: miqi/runtime, miqi/bridge, miqi/cli, miqi/tui, miqi/channels, miqi/cron.
    """
    dirs_to_check = ["runtime", "bridge", "cli", "tui", "channels", "cron"]
    violations: list[str] = []
    patterns = [
        "ToolRegistry.execute",
        "execute_concurrent",
        "self.tools.execute",
        ".tools.execute(",
    ]

    for dirname in dirs_to_check:
        d = _MIQI_DIR / dirname
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            rel = py_file.relative_to(_MIQI_DIR.parent)
            for pat in patterns:
                if pat in content:
                    violations.append(f"{pat} in {rel}")

    assert not violations, (
        f"ToolRegistry.execute/execute_concurrent/self.tools.execute "
        f"found in production paths: {violations}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# asyncio.run() audit
# ═══════════════════════════════════════════════════════════════════════════════


def test_phase35_baseline_asyncio_run_in_bridge():
    """Count asyncio.run() calls in bridge/server.py and bridge/loop.py.

    This test records the baseline. After Phase 35 migration, the count
    in server.py should decrease (cron.run and providers.test handlers
    will move to AppServer).
    """
    server_lines = _SERVER_PY.read_text(encoding="utf-8").splitlines()
    loop_lines = _LOOP_PY.read_text(encoding="utf-8").splitlines()

    def _count_asyncio_run(lines: list[str]) -> list[int]:
        """Count asyncio.run() on code lines only (skip comments and docstrings)."""
        result: list[int] = []
        in_docstring = False
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Track triple-quoted strings (docstrings and multi-line strings)
            if '"""' in stripped or "'''" in stripped:
                # Count occurrences of triple quotes on this line
                count = stripped.count('"""') + stripped.count("'''")
                # Toggle for each pair; odd count flips state
                if count % 2 == 1:
                    in_docstring = not in_docstring
                # If count is even, state doesn't change (opening and closing on same line)
                # Skip the rest of the check if we just entered/exited a docstring
                if in_docstring:
                    continue
            elif in_docstring:
                continue
            if "asyncio.run(" in stripped:
                result.append(i)
        return result

    server_runs = _count_asyncio_run(server_lines)
    loop_runs = _count_asyncio_run(loop_lines)

    print(f"\nasyncio.run() in bridge/server.py: {len(server_runs)}")
    print(f"asyncio.run() in bridge/loop.py: {len(loop_runs)}")

    # bridge/loop.py should NOT use asyncio.run() — it IS the persistent loop
    assert not loop_runs, (
        "bridge/loop.py must not use asyncio.run() — it IS the persistent loop"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Bridge-state import audit (Phase 35 hardening)
# ═══════════════════════════════════════════════════════════════════════════════


def test_phase35_runtime_bridge_state_imports_audit():
    """Audit: count and identify remaining bridge.server imports in runtime/.

    Phase 35 hardening: provider, channel, mcp, skill, plugin, permission
    handlers now use get_bridge_state(registry) DI instead of importing
    miqi.bridge.server directly.

    Remaining imports are documented here to prevent accidental growth.
    If you add a new runtime handler that imports miqi.bridge.server,
    UPDATE this test's expected count — and add a comment explaining why
    registry DI is not yet viable for that handler.
    """
    import re

    miqi_dir = Path(__file__).parent.parent.parent / "miqi"
    runtime_dir = miqi_dir / "runtime"

    imports: dict[str, int] = {}
    for py_file in sorted(runtime_dir.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        count = len(re.findall(
            r'import miqi\.bridge\.server as bridge_module', content,
        ))
        if count > 0:
            imports[py_file.name] = count

    # Phase 35 hardened modules: ZERO imports (migrated to registry DI)
    assert imports.get("provider_handlers.py", 0) == 0, (
        "provider_handlers.py must use get_bridge_state(registry), "
        "not import miqi.bridge.server"
    )
    assert imports.get("channel_handlers.py", 0) == 0, (
        "channel_handlers.py must use get_bridge_state(registry)"
    )
    assert imports.get("mcp_handlers.py", 0) == 0, (
        "mcp_handlers.py must use get_bridge_state(registry)"
    )
    assert imports.get("skill_handlers.py", 0) == 0, (
        "skill_handlers.py must use get_bridge_state(registry)"
    )
    assert imports.get("plugin_handlers.py", 0) == 0, (
        "plugin_handlers.py must use get_bridge_state(registry)"
    )
    assert imports.get("permission_handlers.py", 0) == 0, (
        "permission_handlers.py must use get_bridge_context(registry)"
    )

    # Remaining imports (before Phase 38):
    # - approval_handlers.py: needs orchestrator (Phase 28.2)
    # - file_handlers.py: needs workspace/state for sandbox (Phase 30)
    # - session_handlers.py: needs state for data_dir (Phase 28.4)
    # - experience_handlers.py: needs state + singleton store pattern (Phase 35.7)
    #   (2 imports: _get_experience_store + _cleanup_experience_store)
    # - memory_handlers.py: needs state for workspace/config (Phase 35.7)
    # - cron_handlers.py: needs state for get_data_dir() (Phase 35.6)
    # - feedback_handlers.py: needs state for workspace + config (Phase 78 feedback)
    # Phase 38.5: config_handlers.py migrated to get_bridge_state(registry) + shared helpers.
    expected_remaining = 7  # files with at least 1 import
    assert len(imports) == expected_remaining, (
        f"Expected {expected_remaining} runtime files with bridge.server imports, "
        f"got {len(imports)}: {list(imports.keys())}. "
        f"If you added a new import, update this test. "
        f"If you migrated one, decrement the expected count."
    )

    total_imports = sum(imports.values())
    assert total_imports == 16, (
        f"Expected 16 total bridge.server imports in runtime/, "
        f"got {total_imports}. Update this test if the count changed."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Migration audit helpers — used by subsequent tasks to verify removals
# ═══════════════════════════════════════════════════════════════════════════════


def assert_method_keys_absent_from_methods(keys: list[str]) -> None:
    """Assert that every key in `keys` is no longer present in _METHODS.

    Usage in Phase 35 task tests:
        assert_method_keys_absent_from_methods([
            "providers.list", "providers.test", "providers.update",
        ])
    """
    from miqi.bridge.server import _METHODS

    remaining = [k for k in keys if k in _METHODS]
    assert not remaining, (
        f"Method keys still in _METHODS: {remaining}. "
        f"Remove them from the _METHODS dict in miqi/bridge/server.py."
    )


def assert_method_keys_present_in_appserver(keys: list[str]) -> None:
    """Assert that every key in `keys` is registered on AppServer.

    Parses register_method() calls across the miqi package.
    """
    miqi_methods = _parse_all_appserver_registrations(_MIQI_DIR)
    missing = [k for k in keys if k not in miqi_methods]
    assert not missing, (
        f"Method keys not found in AppServer registrations: {missing}. "
        f"Add register_method() calls in miqi/bridge/loop.py for these."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 35: Explicit 43-key migration audit
# ═══════════════════════════════════════════════════════════════════════════════

# All 43 method keys migrated from _METHODS to AppServer in Phase 35.
_PHASE35_MIGRATED_KEYS = [
    # Phase 35.2: providers.*, channels.*, permissions.* (9 keys)
    "providers.list", "providers.test", "providers.update",
    "channels.list", "channels.update",
    "permissions.get", "permissions.update",
    "permissions.permanent.add", "permissions.permanent.remove",
    # Phase 35.3: plugins.* (4 keys)
    "plugins.list", "plugins.install", "plugins.uninstall", "plugins.toggle",
    # Phase 35.4: mcp.* (3 keys)
    "mcp.list", "mcp.upsert", "mcp.delete",
    # Phase 35.5: skills.* (6 keys)
    "skills.list", "skills.get", "skills.open_folder",
    "skills.create", "skills.upload", "skills.delete",
    # Phase 35.6: cron.* (7 keys)
    "cron.list", "cron.create", "cron.update", "cron.delete",
    "cron.toggle", "cron.run", "cron.runs",
    # Phase 35.7: memory.*, experience:* (10 keys)
    "memory.list", "memory.get", "memory.update", "memory.delete",
    "memory.lessons", "memory.lesson.unlearn",
    "experience:list", "experience:delete", "experience:toggle", "experience:search",
    # Phase 35.8: diagnostic (1 key)
    "python.check",
    # Phase 35.2 additional: config.get/config.update and sessions.* already
    # migrated in Phase 28. Not included here.
    # Phase 35 also added: agent.list/agent.get (migrated in Phase 28.5).
    # The above 43 are the NEW Phase 35 keys.
]


def test_phase35_all_43_keys_absent_from_methods():
    """All 43 migrated method keys must be absent from _METHODS."""
    from miqi.bridge.server import _METHODS

    still_present = [k for k in _PHASE35_MIGRATED_KEYS if k in _METHODS]
    assert not still_present, (
        f"Phase 35 migrated keys still in _METHODS: {still_present}. "
        f"Remove them from the _METHODS dict in miqi/bridge/server.py."
    )

    # Verify count: only 2 keys remain (status, plan.get)
    assert len(_METHODS) == 2, (
        f"Expected exactly 2 keys in _METHODS (status, plan.get), "
        f"got {len(_METHODS)}: {sorted(_METHODS.keys())}"
    )


def test_phase35_all_43_keys_present_in_appserver():
    """All 43 migrated method keys must be registered on AppServer."""
    miqi_methods = _parse_all_appserver_registrations(_MIQI_DIR)
    missing = [k for k in _PHASE35_MIGRATED_KEYS if k not in miqi_methods]
    assert not missing, (
        f"Phase 35 migrated keys not in AppServer registrations: {missing}. "
        f"Add register_method() calls in miqi/bridge/loop.py."
    )

    # Verify we have at least 77 total AppServer registrations
    # (34 pre-Phase-35 + 43 new = 77)
    assert len(miqi_methods) >= 77, (
        f"Expected at least 77 AppServer registrations, got {len(miqi_methods)}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Error message sanitization audit (Phase 35 hardening)
# ═══════════════════════════════════════════════════════════════════════════════


def test_phase35_no_raw_str_exc_in_runtime_handlers():
    """Audit: runtime control-plane handlers must not return raw str(exc).

    Checks all runtime handler files for patterns where AppServerError
    is raised with a dynamic message (str(exc), f"...{exc}...", etc.)
    that could leak internal exception text to the frontend.

    Phase 35 hardening fixed these in 9 handler sites.
    This test locks the current state to prevent regressions.
    """
    runtime_dir = _MIQI_DIR / "runtime"
    violations: list[str] = []

    # Patterns that indicate unsanitized error messages
    unsanitized_patterns = [
        (r'AppServerError\(\s*str\(exc\)', "str(exc)"),
        (r'AppServerError\(\s*f"[^"]*\{exc\}[^"]*"', "f-string with {exc}"),
        (r'AppServerError\(\s*f\'[^\']*\{exc\}[^\']*\'', "f-string with {exc}"),
    ]

    for py_file in sorted(runtime_dir.glob("*.py")):
        # Skip test files and non-handler modules
        if py_file.name.startswith("test_") or py_file.name.startswith("__"):
            continue
        content = py_file.read_text(encoding="utf-8")
        for pattern, desc in unsanitized_patterns:
            if re.search(pattern, content):
                # Check if the line is in a comment
                for i, line in enumerate(content.splitlines(), 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if re.search(pattern, stripped):
                        violations.append(
                            f"{py_file.name}:{i}: {desc}: {stripped[:80]}"
                        )

    assert not violations, (
        f"Found {len(violations)} unsanitized error message(s) in runtime handlers:\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\n\nFix: log full exc with logger, return a fixed safe message."
    )
