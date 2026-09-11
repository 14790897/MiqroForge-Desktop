"""Codex-style skills and hooks AppServer handlers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import AppServer, AppServerError, get_bridge_context
from miqi.runtime.plugin_skill_request_models import validate_plugin_skill_params


def _workspace_root(registry: Any) -> Path | None:
    """Return the configured workspace root, or None if unavailable."""
    state = get_bridge_context(registry, "state", None)
    if state is None:
        return None
    try:
        return Path(state.load_config().workspace_path).resolve()
    except Exception:
        return None


def _resolve_allowed_cwd(raw: str, workspace: Path | None) -> Path:
    """Resolve and validate a cwd path.

    Raises AppServerError if the workspace is configured and the resolved
    path falls outside it.
    """
    resolved = Path(raw).resolve()
    if workspace is not None:
        try:
            resolved.relative_to(workspace)
        except ValueError:
            raise AppServerError(
                f"CWD outside workspace: {raw}", code="INVALID_PARAMS"
            )
    return resolved


def _resolve_allowed_extra_root(raw: str, workspace: Path | None) -> Path:
    """Resolve and validate an extra root path.

    Raises AppServerError if the path does not exist on disk or falls
    outside a configured workspace.
    """
    resolved = Path(raw).resolve()
    if not resolved.exists():
        raise AppServerError(
            f"Extra root does not exist: {raw}", code="INVALID_PARAMS"
        )
    if workspace is not None:
        try:
            resolved.relative_to(workspace)
        except ValueError:
            raise AppServerError(
                f"Extra root outside workspace: {raw}", code="INVALID_PARAMS"
            )
    return resolved


def _get_client_extra_roots(registry: Any, client_id: str) -> list[Path]:
    """Get extra skills roots scoped to a specific client.

    Falls back to legacy global ``skills_extra_roots`` when no client-scoped
    value exists (read-only migration path). New writes always go to
    ``skills_extra_roots_by_client``.
    """
    by_client = registry.bridge_context.setdefault("skills_extra_roots_by_client", {})
    if client_id in by_client:
        return list(by_client[client_id])
    legacy = registry.bridge_context.get("skills_extra_roots")
    if legacy is not None:
        return [Path(p) for p in legacy]
    return []


def register_skills_app_handlers(server: AppServer) -> None:
    async def _skills_list(request_id, params, client_id, session_id, registry):
        from miqi.agent.skills import SkillsLoader

        typed = validate_plugin_skill_params("skills/list", params)
        workspace = _workspace_root(registry)
        roots = _get_client_extra_roots(registry, client_id)
        cwds = typed.cwds
        if not cwds:
            state = get_bridge_context(registry, "state", None)
            if state is not None:
                cwds = [str(state.load_config().workspace_path)]
        result = []
        seen: set[str] = set()
        for cwd_raw in cwds:
            cwd = _resolve_allowed_cwd(str(cwd_raw), workspace)
            loader = SkillsLoader(workspace=cwd)
            for skill in loader.list_skills(filter_unavailable=False):
                if skill["name"] in seen:
                    continue
                seen.add(skill["name"])
                # Normalize path separators so the renderer can use a
                # single '/kwp/' substring filter on Windows (where
                # Path values come back with backslashes). This matches
                # what SkillsLoader.build_skills_summary() does for the
                # agent-facing summary.
                skill_path = skill["path"].replace("\\", "/")
                result.append({
                    "name": skill["name"],
                    "path": skill_path,
                    "source": skill["source"],
                    "description": loader._get_skill_description(skill["name"]),
                    "available": loader._check_requirements(loader._get_skill_meta(skill["name"])),
                })
            for root in roots:
                if not root.exists():
                    continue
                for skill_dir in root.iterdir():
                    skill_md = skill_dir / "SKILL.md"
                    if not skill_dir.is_dir() or not skill_md.exists():
                        continue
                    if skill_dir.name in seen:
                        continue
                    seen.add(skill_dir.name)
                    result.append({
                        "name": skill_dir.name,
                        "path": str(skill_md),
                        "source": "extraRoot",
                        "description": skill_dir.name,
                        "available": True,
                    })
        result.sort(key=lambda s: s["name"])
        return {"result": {"skills": result}}

    async def _extra_roots_set(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("skills/extraRoots/set", params)
        workspace = _workspace_root(registry)
        validated: list[Path] = []
        for raw in typed.roots:
            validated.append(_resolve_allowed_extra_root(raw, workspace))
        by_client = registry.bridge_context.setdefault("skills_extra_roots_by_client", {})
        by_client[client_id] = validated
        # extraRoots 目前不在共享索引内（_skills_list 单独遍历），但统一失效
        # 语义下任何技能写操作都清空缓存，防御未来将 extraRoots 纳入索引。
        from miqi.agent.skills import invalidate_skill_index
        invalidate_skill_index()
        roots_payload = {"roots": [str(root) for root in validated]}
        await server.emit_event(
            session_id or "process",
            "skills/changed",
            roots_payload,
            request_id=request_id,
        )
        await server.emit_client_event(
            client_id,
            "skills/changed",
            roots_payload,
            request_id=request_id,
        )
        return {"result": {}}

    async def _hooks_list(request_id, params, client_id, session_id, registry):
        typed = validate_plugin_skill_params("hooks/list", params)
        workspace = _workspace_root(registry)
        cwds = typed.cwds
        hooks = []
        for cwd_raw in cwds:
            cwd = _resolve_allowed_cwd(str(cwd_raw), workspace)
            path = cwd / ".miqi" / "hooks" / "hooks.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "hooks/list: skipping invalid hooks file {}: {}", path, exc
                )
                continue
            entries = data.get("hooks", []) if isinstance(data, dict) else data
            for entry in entries:
                if isinstance(entry, dict):
                    hooks.append({"cwd": str(cwd), **entry})
        return {"result": {"hooks": hooks}}

    server.register_method("skills/list", _skills_list, spec=protocol_specs.SKILLS_LIST)
    server.register_method("skills/extraRoots/set", _extra_roots_set, spec=protocol_specs.SKILLS_EXTRA_ROOTS_SET)
    server.register_method("hooks/list", _hooks_list, spec=protocol_specs.HOOKS_LIST)
