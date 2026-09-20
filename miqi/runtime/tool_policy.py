"""Shared tool classification for execution policy enforcement.

Single source of truth — when adding a new tool, update this file.
task_runner and turn_runner both import from here.

IMPORTANT: plan mode sets bypass_approval=True for its remaining tools.
Safety therefore depends on the filter below being complete.  Never
remove a tool from this set without verifying it has no write, execute,
or network-modify capability.  The permission engine's deny-list still
wins in all modes.

Note (#1102): bypass_approval only skips the category-based approval flow
in permission_engine.check() — it does NOT skip the Action Guard.  The
filter below removes `spawn` — today the only registered tool whose risk
reaches task_policy's confirm threshold — from what plan mode advertises,
so plan normally has nothing for the guard to catch; auto mode keeps it
available and the guard requires confirmation for it.  The filter shapes
what the model is offered, not what the orchestrator can dispatch, so it
is a boundary, not the only one.
"""

# Tools blocked in plan mode (read-only strategist).
# Everything NOT in this set is available to plan mode and auto-allowed by
# bypass_approval — unless it hits the Action Guard (see module docstring).
# Update carefully.
PLAN_BLOCKED_TOOLS: frozenset[str] = frozenset({
    "write_file", "edit_file", "apply_patch", "edit_diff",
    "write", "edit", "delete", "move",
    "exec", "bash", "shell",
    "spawn", "subagent", "cron",
    "skill_manage", "memory",
})
