"""System-install authorization audit (issue #935).

The audit must survive a restart (append-only JSONL), fold the two rows of
one grant back together for the API, and never let an IO problem change a
decision or break an install.
"""

import json

import pytest

from miqi.agent import system_install_audit as audit


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    """Every test gets a fresh ring and no file behind it."""
    monkeypatch.setattr(audit, "_audit_file", None)
    audit.clear_audit()
    yield
    monkeypatch.setattr(audit, "_audit_file", None)
    audit.clear_audit()


def test_authorization_and_result_merge_into_one_record():
    grant_id = audit.record_authorization(
        decision="always",
        command="apt-get install -y texlive-xetex",
        session_key="client-1:session-1",
        thread_id="thread-1",
        turn_id="turn-1",
        persist_failed=False,
        runtime_failed=True,
    )
    audit.record_result(grant_id, exit_code=0, duration_ms=1234)

    records = audit.get_install_audit()
    assert len(records) == 1
    record = records[0]
    assert record["id"] == grant_id
    assert record["source"] == "system_install"
    assert record["decision"] == "always"
    assert record["command"] == "apt-get install -y texlive-xetex"
    assert record["session_key"] == "client-1:session-1"
    assert record["thread_id"] == "thread-1"
    assert record["turn_id"] == "turn-1"
    assert record["runtime_failed"] is True
    assert record["persist_failed"] is False
    assert record["result"] == {
        "exit_code": 0,
        "success": True,
        "duration_ms": 1234,
        "reason": "",
    }


def test_failed_install_is_not_recorded_as_success():
    grant_id = audit.record_authorization(decision="once", command="apt-get install x")
    audit.record_result(grant_id, exit_code=100, duration_ms=50)

    result = audit.get_install_audit()[0]["result"]
    assert result["success"] is False
    assert result["exit_code"] == 100


def test_denial_has_no_result_row():
    """拒绝没有执行结果 —— result 保持 None，不等于「还在跑」之外的任何状态。"""
    audit.record_authorization(decision="deny", command="apt-get install x")

    records = audit.get_install_audit()
    assert len(records) == 1
    assert records[0]["decision"] == "deny"
    assert records[0]["result"] is None


def test_intercepted_install_records_reason():
    grant_id = audit.record_authorization(decision="once", command="apt-get install x")
    audit.record_result(grant_id, exit_code=1, reason="no live sandbox")

    assert audit.get_install_audit()[0]["result"]["reason"] == "no live sandbox"


def test_result_without_authorization_is_dropped():
    """授权行滚出内存环后，孤儿 result 行不得作为独立记录返回。"""
    audit.record_result("grant-that-never-existed", exit_code=0)

    assert audit.get_install_audit() == []


def test_records_survive_restart(tmp_path):
    """落盘后新进程（重新 init）仍能读到 —— #935 的「可追溯」底线。"""
    path = tmp_path / "system_install_audit.jsonl"
    audit.init_audit_file(str(path))
    grant_id = audit.record_authorization(
        decision="always", command="apt-get install -y gcc", session_key="c:s",
    )
    audit.record_result(grant_id, exit_code=0, duration_ms=10)

    # Simulate a bridge restart: fresh module state reading the same file.
    audit.clear_audit()
    audit._audit_file = None
    audit.init_audit_file(str(path))

    records = audit.get_install_audit()
    assert len(records) == 1
    assert records[0]["id"] == grant_id
    assert records[0]["command"] == "apt-get install -y gcc"
    assert records[0]["result"]["success"] is True


def test_init_is_idempotent_per_path(tmp_path):
    path = tmp_path / "system_install_audit.jsonl"
    audit.init_audit_file(str(path))
    audit.record_authorization(decision="once", command="apt-get install x")

    audit.init_audit_file(str(path))  # re-init must not duplicate the row

    assert len(audit.get_install_audit()) == 1


def test_ring_is_capped_but_file_keeps_everything(tmp_path, monkeypatch):
    path = tmp_path / "system_install_audit.jsonl"
    monkeypatch.setattr(audit, "_MAX_ENTRIES", 4)
    audit.init_audit_file(str(path))
    for i in range(5):
        audit.record_authorization(decision="once", command=f"apt-get install pkg{i}")

    assert len(audit.get_install_audit()) == 4  # ring keeps the last 4 rows
    with open(path, "r", encoding="utf-8") as fh:
        assert sum(1 for line in fh if line.strip()) == 5  # file keeps all


def test_unwritable_file_never_raises(tmp_path):
    """审计是尽力而为：写盘失败不得把异常抛给安装流程。"""
    audit.init_audit_file(str(tmp_path))  # a directory, not a file → OSError on open

    grant_id = audit.record_authorization(decision="always", command="apt-get install x")
    audit.record_result(grant_id, exit_code=0)

    assert len(audit.get_install_audit()) == 1  # in-memory trail still works


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "system_install_audit.jsonl"
    good = json.dumps({
        "id": "r1", "kind": "authorization", "grant_id": "g1", "timestamp": 1.0,
        "session_key": "", "thread_id": "", "turn_id": "",
        "decision": "always", "command": "apt-get install x",
        "persist_failed": False, "runtime_failed": False,
    })
    path.write_text(f"not json\n{good}\n", encoding="utf-8")

    audit.init_audit_file(str(path))

    records = audit.get_install_audit()
    assert len(records) == 1
    assert records[0]["id"] == "g1"


def test_newest_grant_first():
    audit.record_authorization(decision="once", command="apt-get install first")
    audit.record_authorization(decision="once", command="apt-get install second")

    commands = [r["command"] for r in audit.get_install_audit()]
    assert commands == ["apt-get install second", "apt-get install first"]
