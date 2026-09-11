from __future__ import annotations

from miqi.runtime.filesystem_request_models import FsWriteFileParams
from miqi.runtime.process_request_models import CommandExecParams, ProcessSpawnParams
from miqi.runtime.protocol_model_schema import params_schema_from_model
from miqi.runtime.turn_request_models import TurnStartParams


def test_params_schema_uses_external_alias_names():
    schema = params_schema_from_model(TurnStartParams)

    assert schema["type"] == "object"
    assert "threadId" in schema["properties"]
    assert "clientUserMessageId" in schema["properties"]
    assert "thread_id" not in schema["properties"]
    assert "client_user_message_id" not in schema["properties"]
    assert sorted(schema["required"]) == ["input", "threadId"]


def test_params_schema_preserves_extra_allowed_contract():
    schema = params_schema_from_model(FsWriteFileParams)

    assert schema["additionalProperties"] is True


def test_params_schema_exposes_required_and_optional_fields():
    schema = params_schema_from_model(CommandExecParams)

    assert schema["required"] == ["command"]
    assert "command" in schema["properties"]
    assert "processId" in schema["properties"]
    assert "cwd" in schema["properties"]
    assert "timeoutMs" in schema["properties"]
    assert "outputBytesCap" in schema["properties"]


def test_params_schema_skips_internal_runtime_fields():
    schema = params_schema_from_model(CommandExecParams)

    assert "cwd" in schema["properties"]
    assert "cwd_raw" not in schema["properties"]


def test_params_schema_is_deterministic():
    first = params_schema_from_model(FsWriteFileParams)
    second = params_schema_from_model(FsWriteFileParams)

    assert first == second


# ---------------------------------------------------------------------------
# Plan 65 fix: ensure process/spawn and command/exec cwd schemas come from
# the wire field (cwd_raw: str) and are NOT polluted by the internal runtime
# field (cwd: Path | None).
# ---------------------------------------------------------------------------


def test_process_spawn_cwd_is_wire_string_schema():
    schema = params_schema_from_model(ProcessSpawnParams)

    assert "cwd" in schema["required"], "cwd must be required for process/spawn"
    assert "cwd" in schema["properties"]

    cwd_schema = schema["properties"]["cwd"]
    # Must be a plain string schema, not polluted by the internal Path field.
    assert cwd_schema["type"] == "string"
    assert "format" not in cwd_schema, f"cwd must not have format, got {cwd_schema.get('format')!r}"
    assert "default" not in cwd_schema, f"cwd must not have default, got {cwd_schema.get('default')!r}"
    assert "anyOf" not in cwd_schema, f"cwd must not have anyOf, got {cwd_schema.get('anyOf')!r}"

    # The internal field names must not leak into the catalog schema.
    assert "cwd_raw" not in schema["properties"]


def test_command_exec_cwd_is_wire_string_schema():
    schema = params_schema_from_model(CommandExecParams)

    assert "cwd" in schema["properties"]

    cwd_schema = schema["properties"]["cwd"]
    # CommandExecParams.cwd_raw is str|None with default=None, so it is
    # optional on the wire.  The anyOf+default are legitimate from the
    # Optional[str] type — what must NOT be present is format:"path" from
    # the internal runtime cwd: Path|None field.
    assert "format" not in cwd_schema, f"cwd must not have format, got {cwd_schema.get('format')!r}"

    # The internal field names must not leak into the catalog schema.
    assert "cwd_raw" not in schema["properties"]


def test_result_schema_from_model_preserves_wire_names():
    from pydantic import BaseModel, Field

    from miqi.runtime.protocol_model_schema import result_schema_from_model

    class ExampleResult(BaseModel):
        exit_code: int = Field(validation_alias="exitCode", serialization_alias="exitCode")
        stdout: str

    schema = result_schema_from_model(ExampleResult)

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["exitCode", "stdout"]
    assert "exitCode" in schema["properties"]
    assert "exit_code" not in schema["properties"]


def test_protocol_method_spec_exports_event_schemas():
    from miqi.runtime.protocol_registry import (
        MethodScope,
        MethodStability,
        ProtocolMethodSpec,
    )

    spec = ProtocolMethodSpec(
        method="process/spawn",
        stability=MethodStability.STABLE,
        scope=MethodScope.PROCESS,
        params_schema={"type": "object"},
        result_schema={"type": "object"},
        emits=["process/exited"],
        event_schemas={
            "process/exited": {
                "type": "object",
                "properties": {"processHandle": {"type": "string"}},
                "required": ["processHandle"],
            },
        },
    )

    data = spec.to_json()

    assert data["eventSchemas"]["process/exited"]["required"] == ["processHandle"]
