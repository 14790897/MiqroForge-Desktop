"""Tests for SessionManager.truncate / Session.truncate_turns (#1020).

Validates:
- truncate_turns drops the last N user turns and returns the removed count
- truncate persists the shorter history (survives a manager reload)
- ownership is enforced (other-client / unowned sessions rejected)
- edge cases: N > user turns clears all; no user messages is a no-op
"""

from pathlib import Path

import pytest

from miqi.session.manager import OwnershipError, Session, SessionManager


def _make_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path)


def _two_turn_session(sm: SessionManager, key: str, client_id: str) -> Session:
    session = sm.get_or_create(key, client_id=client_id)
    session.add_message("user", "Q1")
    session.add_message("assistant", "A1")
    session.add_message("user", "Q2")
    session.add_message("assistant", "A2")
    sm.save(session)
    return session


def _roles(session: Session) -> list[str]:
    return [m["role"] for m in session.messages]


def test_truncate_turns_drops_last_turn():
    s = Session(key="k")
    s.add_message("user", "Q1")
    s.add_message("assistant", "A1")
    s.add_message("user", "Q2")
    s.add_message("assistant", "A2")

    removed = s.truncate_turns(1)
    assert removed == 2
    assert _roles(s) == ["user", "assistant"]
    assert s.messages[-1]["content"] == "A1"


def test_truncate_turns_drops_multiple_turns():
    s = Session(key="k")
    for i in range(3):
        s.add_message("user", f"Q{i}")
        s.add_message("assistant", f"A{i}")

    removed = s.truncate_turns(2)
    assert removed == 4
    assert _roles(s) == ["user", "assistant"]


def test_truncate_turns_drop_all_when_n_exceeds_users():
    s = Session(key="k")
    s.add_message("user", "Q1")
    s.add_message("assistant", "A1")
    s.add_message("user", "Q2")
    s.add_message("assistant", "A2")

    assert s.truncate_turns(5) == 4
    assert s.messages == []


def test_truncate_turns_no_user_messages_is_noop():
    s = Session(key="k")
    s.add_message("assistant", "orphan")
    assert s.truncate_turns(1) == 0
    assert len(s.messages) == 1


def test_truncate_turns_non_positive_is_noop():
    s = Session(key="k")
    s.add_message("user", "Q1")
    assert s.truncate_turns(0) == 0
    assert s.truncate_turns(-1) == 0
    assert len(s.messages) == 1


def test_truncate_turns_clamps_last_consolidated():
    s = Session(key="k")
    for i in range(3):
        s.add_message("user", f"Q{i}")
        s.add_message("assistant", f"A{i}")
    s.last_consolidated = 6  # 全部已归档

    removed = s.truncate_turns(2)  # 留 2 条(Q0/A0)
    assert removed == 4
    assert s.last_consolidated == 2  # 游标不能超过新长度


def test_truncate_turns_resets_last_consolidated_when_cleared():
    s = Session(key="k")
    s.add_message("user", "Q0")
    s.add_message("assistant", "A0")
    s.last_consolidated = 2

    assert s.truncate_turns(1) == 2
    assert s.messages == []
    assert s.last_consolidated == 0


def test_truncate_persists_across_reload(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _two_turn_session(sm, key, "c1")

    removed = sm.truncate(key, 1, client_id="c1")
    assert removed == 2

    # Reload from disk: the truncated turn must not come back.
    sm2 = _make_manager(tmp_path)
    session = sm2.get_or_create(key, client_id="c1")
    assert _roles(session) == ["user", "assistant"]
    assert [m["content"] for m in session.messages] == ["Q1", "A1"]


def test_truncate_returns_removed_count(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _two_turn_session(sm, key, "c1")
    assert sm.truncate(key, 2, client_id="c1") == 4


def test_truncate_enforces_ownership(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _two_turn_session(sm, key, "c1")

    with pytest.raises(OwnershipError):
        sm.truncate(key, 1, client_id="c2")


def test_truncate_unowned_session_requires_claim(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:legacy"
    session = sm.get_or_create(key)  # no client_id → unowned
    session.add_message("user", "legacy")
    sm.save(session)

    with pytest.raises(OwnershipError) as exc:
        sm.truncate(key, 1, client_id="c1")
    assert exc.value.code == "REQUIRES_CLAIM"
