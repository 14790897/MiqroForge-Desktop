"""Helpers for deriving protocol catalog schemas from typed request models."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel

from miqi.runtime.protocol_registry import (
    MethodScope,
    MethodStability,
    ProtocolMethodSpec,
)

# Python field names that exist only for runtime use (e.g. Path resolution
# after validation) and must NOT appear in catalog params schemas.
_INTERNAL_MODEL_FIELDS: set[str] = {
    "cwd",
}


def _build_wire_name_map(model: type[BaseModel]) -> dict[str, str]:
    """Return {python_field_name: wire_name} for every non-internal field."""
    mapping: dict[str, str] = {}
    for name, field_info in model.model_fields.items():
        if name in _INTERNAL_MODEL_FIELDS:
            continue
        alias = field_info.validation_alias
        mapping[name] = str(alias) if alias is not None else name
    return mapping


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(schema)
    result["type"] = "object"
    result["additionalProperties"] = True

    if "required" in result:
        result["required"] = sorted(result["required"])

    # Pydantic emits class titles that make schemas noisy and do not add
    # contract value for protocol/catalog consumers.
    result.pop("title", None)

    properties = result.get("properties")
    if isinstance(properties, dict):
        for prop in properties.values():
            if isinstance(prop, dict):
                prop.pop("title", None)

    return result


def params_schema_from_model(model: type[BaseModel]) -> dict[str, Any]:
    """Return a catalog-ready paramsSchema for a Pydantic request model.

    Uses ``by_alias=False`` and then manually maps Python field names to
    external wire names via ``model.model_fields``.  This avoids alias
    collisions when a model has both a wire field (e.g. ``cwd_raw`` with
    ``validation_alias="cwd"``) and an internal runtime field (e.g.
    ``cwd: Path | None``) that happen to resolve to the same wire name.
    """
    wire_map = _build_wire_name_map(model)
    raw = model.model_json_schema(by_alias=False)

    # ---- properties: drop internal fields, rename the rest to wire names ----
    raw_properties: dict[str, Any] = dict(raw.get("properties") or {})
    properties: dict[str, Any] = {}
    for py_name, prop_schema in raw_properties.items():
        if py_name in _INTERNAL_MODEL_FIELDS:
            continue
        properties[wire_map[py_name]] = prop_schema

    # ---- required: rename Python field names to wire names ----
    raw_required: list[str] = list(raw.get("required") or [])
    required = sorted(wire_map[name] for name in raw_required if name in wire_map)

    result = dict(raw)
    if properties:
        result["properties"] = properties
    else:
        result.pop("properties", None)
    if required:
        result["required"] = required
    else:
        result.pop("required", None)

    return _normalize_schema(result)


def result_schema_from_model(model: type[BaseModel]) -> dict[str, Any]:
    """Return a catalog-ready result/event schema for a Pydantic model.

    Result/event models should be closed: additionalProperties=false.

    Uses ``by_alias=False`` and then manually maps Python field names to
    wire names via ``serialization_alias`` (result/event models use
    ``serialization_alias`` for output wire naming, not ``validation_alias``).
    """
    # Build wire name map from serialization_alias (output models)
    wire_map: dict[str, str] = {}
    for name, field_info in model.model_fields.items():
        alias = field_info.serialization_alias or field_info.validation_alias
        wire_map[name] = str(alias) if alias is not None else name

    raw = model.model_json_schema(by_alias=False)

    # ---- properties: rename to wire names ----
    raw_properties: dict[str, Any] = dict(raw.get("properties") or {})
    properties: dict[str, Any] = {}
    for py_name, prop_schema in raw_properties.items():
        properties[wire_map.get(py_name, py_name)] = prop_schema

    # ---- required: rename to wire names ----
    raw_required: list[str] = list(raw.get("required") or [])
    required = sorted(wire_map.get(name, name) for name in raw_required)

    result = dict(raw)
    if properties:
        result["properties"] = properties
    else:
        result.pop("properties", None)
    if required:
        result["required"] = required
    else:
        result.pop("required", None)

    result = _normalize_schema(result)
    result["additionalProperties"] = False
    return result


def event_schema_map(models: dict[str, type[BaseModel]]) -> dict[str, dict[str, Any]]:
    """Return {eventName: schema} for event payload models."""
    return {
        event_name: result_schema_from_model(model)
        for event_name, model in sorted(models.items())
    }


def model_spec(
    method: str,
    model: type[BaseModel],
    *,
    scope: MethodScope,
    stability: MethodStability = MethodStability.STABLE,
    result_schema: dict[str, Any] | None = None,
    result_model: type[BaseModel] | None = None,
    emits: list[str] | None = None,
    event_models: dict[str, type[BaseModel]] | None = None,
    description: str | None = None,
) -> ProtocolMethodSpec:
    """Create a ProtocolMethodSpec whose paramsSchema is derived from *model*."""
    final_result_schema = (
        result_schema_from_model(result_model)
        if result_model is not None
        else result_schema or {"type": "object", "additionalProperties": True}
    )
    final_event_schemas = event_schema_map(event_models or {})
    return ProtocolMethodSpec(
        method=method,
        stability=stability,
        scope=scope,
        params_schema=params_schema_from_model(model),
        result_schema=final_result_schema,
        emits=emits or [],
        event_schemas=final_event_schemas,
        description=description,
    )
