from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServerError
from miqi.runtime.process_request_models import (
    CommandExecParams,
    CommandExecResizeParams,
    CommandExecTerminateParams,
    CommandExecWriteParams,
    ProcessKillParams,
    ProcessResizePtyParams,
    ProcessSpawnParams,
    ProcessWriteStdinParams,
    validate_process_params,
)
from miqi.runtime.workbench_process_runtime import (
    DEFAULT_OUTPUT_BYTES_CAP,
    DEFAULT_TIMEOUT_MS,
)


def test_command_exec_params_compute_defaults(tmp_path):
    params = validate_process_params(
        CommandExecParams,
        {
            "command": ["python", "-c", "print('hi')"],
            "processId": "cmd-1",
            "cwd": str(tmp_path),
        },
        workspace=tmp_path,
    )

    assert params.command == ["python", "-c", "print('hi')"]
    assert params.process_id == "cmd-1"
    assert params.cwd == tmp_path
    assert params.output_cap == DEFAULT_OUTPUT_BYTES_CAP
    assert params.timeout_ms == DEFAULT_TIMEOUT_MS
    assert params.stdin_enabled is False
    assert params.client_visible is True


def test_command_exec_streaming_requires_process_id(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "streamStdoutStderr": True},
            workspace=tmp_path,
        )

    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_rejects_blocked_env(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "env": {"PYTHONPATH": "bad"}},
            workspace=tmp_path,
        )

    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_write_requires_delta_or_close():
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecWriteParams,
            {"processId": "cmd-1"},
        )

    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_write_decodes_base64():
    params = validate_process_params(
        CommandExecWriteParams,
        {"processId": "cmd-1", "deltaBase64": "aGVsbG8="},
    )

    assert params.process_id == "cmd-1"
    assert params.delta_bytes == b"hello"
    assert params.close_stdin is False


def test_command_exec_terminate_validates_process_id():
    params = validate_process_params(
        CommandExecTerminateParams,
        {"processId": "cmd-1"},
    )

    assert params.process_id == "cmd-1"


def test_resize_models_parse_but_remain_unsupported():
    command_resize = validate_process_params(
        CommandExecResizeParams,
        {"processId": "cmd-1"},
    )
    process_resize = validate_process_params(
        ProcessResizePtyParams,
        {"processHandle": "proc-1"},
    )

    assert command_resize.process_id == "cmd-1"
    assert process_resize.process_handle == "proc-1"


def test_process_spawn_requires_process_handle_and_cwd(tmp_path):
    params = validate_process_params(
        ProcessSpawnParams,
        {
            "command": ["python", "-c", "print('hi')"],
            "processHandle": "proc-1",
            "cwd": str(tmp_path),
        },
        workspace=tmp_path,
    )

    assert params.process_handle == "proc-1"
    assert params.cwd == tmp_path
    assert params.timeout_ms == DEFAULT_TIMEOUT_MS
    assert params.output_cap == DEFAULT_OUTPUT_BYTES_CAP


def test_process_spawn_null_timeout_disables_timeout(tmp_path):
    params = validate_process_params(
        ProcessSpawnParams,
        {
            "command": ["python"],
            "processHandle": "proc-1",
            "cwd": str(tmp_path),
            "timeoutMs": None,
        },
        workspace=tmp_path,
    )

    assert params.timeout_ms is None


def test_process_write_stdin_decodes_base64():
    params = validate_process_params(
        ProcessWriteStdinParams,
        {"processHandle": "proc-1", "deltaBase64": "aGVsbG8="},
    )

    assert params.process_handle == "proc-1"
    assert params.delta_bytes == b"hello"


def test_process_kill_validates_handle():
    params = validate_process_params(
        ProcessKillParams,
        {"processHandle": "proc-1"},
    )

    assert params.process_handle == "proc-1"


# ── Phase 63-fix: strict type rejection tests ────────────────────────────


def test_command_exec_rejects_string_timeout_ms(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "timeoutMs": "5"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_rejects_string_output_bytes_cap(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "outputBytesCap": "5"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_rejects_string_disable_timeout(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "disableTimeout": "true"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_rejects_string_disable_output_cap(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "disableOutputCap": "true"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_rejects_string_stream_stdout_stderr(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "streamStdoutStderr": "true"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_rejects_string_stream_stdin(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecParams,
            {"command": ["python"], "streamStdin": "true"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_command_exec_write_rejects_string_close_stdin():
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            CommandExecWriteParams,
            {"processId": "cmd-1", "closeStdin": "true", "deltaBase64": "aGVsbG8="},
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_process_spawn_rejects_string_timeout_ms(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            ProcessSpawnParams,
            {"command": ["python"], "processHandle": "proc-1", "cwd": str(tmp_path), "timeoutMs": "5"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_process_spawn_rejects_string_output_bytes_cap(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            ProcessSpawnParams,
            {"command": ["python"], "processHandle": "proc-1", "cwd": str(tmp_path), "outputBytesCap": "5"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_process_spawn_rejects_string_stream_stdout_stderr(tmp_path):
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            ProcessSpawnParams,
            {"command": ["python"], "processHandle": "proc-1", "cwd": str(tmp_path), "streamStdoutStderr": "true"},
            workspace=tmp_path,
        )
    assert exc.value.code == "INVALID_PARAMS"


def test_process_write_stdin_rejects_string_close_stdin():
    with pytest.raises(AppServerError) as exc:
        validate_process_params(
            ProcessWriteStdinParams,
            {"processHandle": "proc-1", "closeStdin": "true", "deltaBase64": "aGVsbG8="},
        )
    assert exc.value.code == "INVALID_PARAMS"
