from __future__ import annotations

import json

from miqi.runtime.protocol_snapshot import (
    DEFAULT_SNAPSHOT,
    build_protocol_snapshot,
    canonical_json,
)


def _load_expected_snapshot() -> dict:
    return json.loads(DEFAULT_SNAPSHOT.read_text(encoding="utf-8"))


def _by_method(snapshot: dict) -> dict[str, dict]:
    return {
        item["method"]: item
        for item in snapshot["catalog"]["methods"]
    }


def test_protocol_snapshot_is_current():
    """The built snapshot must match the committed one.

    This is the detection mechanism for any contract change: a modified
    required field, added/removed method, or schema drift makes
    build_protocol_snapshot() diverge from the committed snapshot and
    this test fails.
    """
    expected = _load_expected_snapshot()
    actual = build_protocol_snapshot()

    assert actual == expected, (
        "App protocol compatibility snapshot changed. "
        "If the change is intentional, run "
        "`python -m miqi.runtime.protocol_snapshot`, review the diff, "
        "and summarize breaking vs additive changes in the commit message."
    )


def test_protocol_snapshot_file_is_canonical():
    expected = _load_expected_snapshot()
    actual_text = DEFAULT_SNAPSHOT.read_text(encoding="utf-8")

    assert actual_text == canonical_json(expected)


def test_protocol_snapshot_has_expected_top_level_shape():
    snapshot = _load_expected_snapshot()

    assert set(snapshot) == {
        "schemaVersion",
        "catalogVersion",
        "methodCounts",
        "catalog",
        "generatedTypes",
    }
    assert snapshot["schemaVersion"] == 1
    assert snapshot["catalogVersion"] == 1
    assert snapshot["methodCounts"]["total"] == len(snapshot["catalog"]["methods"])
    assert snapshot["methodCounts"]["typed"] >= 32
    assert snapshot["generatedTypes"]["outputPath"] == "apps/desktop/src/shared/app-protocol.ts"
    assert len(snapshot["generatedTypes"]["sha256"]) == 64


def test_protocol_snapshot_methods_are_unique_and_sorted():
    snapshot = _load_expected_snapshot()
    names = [item["method"] for item in snapshot["catalog"]["methods"]]

    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_protocol_snapshot_covers_typed_request_result_and_event_contracts():
    snapshot = _load_expected_snapshot()
    methods = _by_method(snapshot)

    command_exec = methods["command/exec"]
    assert command_exec["paramsSchema"]["required"] == ["command"]
    pid_prop = command_exec["paramsSchema"]["properties"]["processId"]
    assert pid_prop["anyOf"][0]["type"] == "string"
    assert command_exec["emits"] == ["command/exec/outputDelta"]
    assert "command/exec/outputDelta" in command_exec["eventSchemas"]

    read_file = methods["fs/readFile"]
    assert read_file["paramsSchema"]["required"] == ["path"]
    assert read_file["resultSchema"]["properties"]["dataBase64"]["type"] == "string"

    turn_start = methods["turn/start"]
    assert "paramsSchema" in turn_start
    assert turn_start["stability"] == "stable"
