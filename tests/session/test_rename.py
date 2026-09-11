"""Tests for SessionManager.rename — session custom titles.

Validates:
- metadata.title persisted to disk and returned by list_sessions
- Empty/whitespace titles fall back to the existing title
- Long titles are truncated to 100 chars
- Ownership is enforced (unowned/other-client sessions rejected)
"""

import json
from pathlib import Path

import pytest

from miqi.session.manager import OwnershipError, SessionManager


def _make_manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path)


def _read_metadata_on_disk(sessions_dir: Path, key: str) -> dict:
    safe_key = key.replace(":", "_")
    path = sessions_dir / safe_key / "conversation.jsonl"
    with open(path, encoding="utf-8") as f:
        return json.loads(f.readline())


def _create_session(sm: SessionManager, key: str, client_id: str, first_msg: str) -> None:
    session = sm.get_or_create(key, client_id=client_id)
    session.add_message("user", first_msg)
    sm.save(session)


def test_rename_sets_metadata_title_and_list_prefers_it(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "帮我处理这篇论文摘要")

    sm.rename(key, "MOF 合成路线报告", client_id="c1")

    listed = [s for s in sm.list_sessions(client_id="c1") if s["key"] == key][0]
    assert listed["title"] == "MOF 合成路线报告"


def test_rename_persists_across_manager_reload(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "帮我处理这篇论文摘要")
    sm.rename(key, "MOF 合成路线报告", client_id="c1")

    sm2 = _make_manager(tmp_path)
    listed = [s for s in sm2.list_sessions(client_id="c1") if s["key"] == key][0]
    assert listed["title"] == "MOF 合成路线报告"


def test_rename_without_custom_title_falls_back_to_extracted(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "帮我处理这篇论文摘要")
    listed = [s for s in sm.list_sessions(client_id="c1") if s["key"] == key][0]
    assert listed["title"] == "帮我处理这篇论文摘要"


def test_rename_empty_title_keeps_existing_title(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "帮我处理这篇论文摘要")
    sm.rename(key, "MOF 合成路线报告", client_id="c1")

    result = sm.rename(key, "   ", client_id="c1")
    assert result == "MOF 合成路线报告"

    listed = [s for s in sm.list_sessions(client_id="c1") if s["key"] == key][0]
    assert listed["title"] == "MOF 合成路线报告"


def test_rename_long_title_truncated_to_100(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "first")
    result = sm.rename(key, "x" * 150, client_id="c1")
    assert len(result) == 100


def test_rename_enforces_ownership(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "first")

    # Another client cannot rename
    with pytest.raises(OwnershipError):
        sm.rename(key, "hijacked", client_id="c2")

    # Unowned legacy session cannot be renamed without claim
    legacy_key = "desktop:legacy"
    session = sm.get_or_create(legacy_key)
    session.add_message("user", "legacy message")
    sm.save(session)
    with pytest.raises(OwnershipError):
        sm.rename(legacy_key, "x", client_id="c3")


def test_rename_updates_disk_metadata_line(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "first")
    sm.rename(key, "New Title", client_id="c1")

    meta = _read_metadata_on_disk(sm.sessions_dir, key)
    assert meta["metadata"]["title"] == "New Title"


def test_rename_returns_effective_title(tmp_path):
    sm = _make_manager(tmp_path)
    key = "desktop:test1"
    _create_session(sm, key, "c1", "first")
    result = sm.rename(key, "  标题带空格  ", client_id="c1")
    assert result == "标题带空格"


def test_rename_global_legacy_session_requires_claim(tmp_path):
    """A session that lives only in the global legacy dir must raise
    REQUIRES_CLAIM instead of silently creating a replacement session."""
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    sm = SessionManager(tmp_path, legacy_sessions_dir=legacy_dir)

    key = "desktop:legacy"
    safe_key = key.replace(":", "_")
    (legacy_dir / f"{safe_key}.jsonl").write_text(
        json.dumps(
            {
                "_type": "metadata",
                "key": key,
                "created_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-01T00:00:00",
                "metadata": {},
                "last_consolidated": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(OwnershipError) as exc:
        sm.rename(key, "renamed", client_id="c1")
    assert exc.value.code == "REQUIRES_CLAIM"
