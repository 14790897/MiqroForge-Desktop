from __future__ import annotations

from miqi.runtime.process_response_models import (
    PROCESS_EVENT_MODELS,
    PROCESS_METHOD_RESULT_MODELS,
    CommandExecOutputDeltaEvent,
    CommandExecResult,
    EmptyResult,
    ProcessExitedEvent,
    ProcessOutputDeltaEvent,
)


def test_command_exec_result_uses_wire_aliases():
    result = CommandExecResult(
        exit_code=0,
        stdout="out",
        stderr="err",
        stdout_cap_reached=False,
        stderr_cap_reached=False,
        duration_ms=12,
        termination_reason="exited",
    )

    assert result.model_dump(by_alias=True) == {
        "exitCode": 0,
        "stdout": "out",
        "stderr": "err",
        "stdoutCapReached": False,
        "stderrCapReached": False,
        "durationMs": 12,
        "terminationReason": "exited",
    }


def test_process_output_delta_event_uses_wire_aliases():
    event = ProcessOutputDeltaEvent(
        process_handle="proc-1",
        stream="stdout",
        delta_base64="aGVsbG8=",
        cap_reached=False,
    )

    assert event.model_dump(by_alias=True) == {
        "processHandle": "proc-1",
        "stream": "stdout",
        "deltaBase64": "aGVsbG8=",
        "capReached": False,
    }


def test_command_output_delta_event_uses_process_id():
    event = CommandExecOutputDeltaEvent(
        process_id="cmd-1",
        stream="stderr",
        delta_base64="",
        cap_reached=True,
    )

    assert event.model_dump(by_alias=True)["processId"] == "cmd-1"


def test_process_exited_event_shape():
    event = ProcessExitedEvent(
        process_handle="proc-1",
        exit_code=0,
        stdout="",
        stderr="",
        stdout_cap_reached=False,
        stderr_cap_reached=False,
        duration_ms=1,
        termination_reason="exited",
    )

    assert event.model_dump(by_alias=True)["processHandle"] == "proc-1"
    assert event.model_dump(by_alias=True)["exitCode"] == 0


def test_model_maps_cover_process_methods_and_events():
    assert PROCESS_METHOD_RESULT_MODELS["command/exec"] is CommandExecResult
    assert PROCESS_METHOD_RESULT_MODELS["command/exec/write"] is EmptyResult
    assert PROCESS_METHOD_RESULT_MODELS["process/spawn"] is EmptyResult
    assert PROCESS_EVENT_MODELS["process/outputDelta"] is ProcessOutputDeltaEvent
    assert PROCESS_EVENT_MODELS["process/exited"] is ProcessExitedEvent
