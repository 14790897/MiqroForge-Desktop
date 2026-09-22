"""Shared tool classification for execution policy enforcement.

Single source of truth — when adding a new tool, update this file.
task_runner and turn_runner both import from here.

IMPORTANT: plan mode sets bypass_approval=True for its remaining tools.
Safety therefore depends on the filter below being complete.  Never
remove a tool from this set without verifying it has no write, execute,
or network-modify capability.  The permission engine's deny-list still
wins in all modes.
"""

# Tools blocked in plan mode (read-only strategist).
# Everything NOT in this set is available to plan mode and will be
# auto-allowed (bypass_approval=True).  Update carefully.
PLAN_BLOCKED_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch", "edit_diff",
    "write", "edit", "delete", "move",
    "exec", "bash", "shell",
    "spawn", "subagent", "cron",
    "skill_manage", "memory",
})
