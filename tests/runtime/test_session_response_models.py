"""Tests for session response models."""

from __future__ import annotations

from miqi.runtime.protocol_model_schema import result_schema_from_model
from miqi.runtime.session_response_models import SESSION_METHOD_RESULT_MODELS


class TestAllMethodsExist:
    def test_all_10_methods_in_result_map(self):
        expected = {
            "sessions.list",
            "sessions.get",
            "sessions.delete",
            "sessions.archive",
            "sessions.unarchive",
            "sessions.list_archived",
            "sessions.get_tracked_files",
            "sessions.clear_tracked_files",
            "sessions.claim_legacy",
            "sessions.rename",
        }
        assert set(SESSION_METHOD_RESULT_MODELS) == expected


class TestWireFieldNames:
    def test_sessions_get_uses_session_id(self):
        schema = result_schema_from_model(SESSION_METHOD_RESULT_MODELS["sessions.get"])
        props = schema["properties"]
        assert "session_id" in props  # wire name, not snake_case alias

    def test_sessions_get_tracked_files_uses_tracked_files(self):
        schema = result_schema_from_model(SESSION_METHOD_RESULT_MODELS["sessions.get_tracked_files"])
        props = schema["properties"]
        assert "tracked_files" in props

    def test_sessions_claim_legacy_uses_was_already_claimed(self):
        schema = result_schema_from_model(SESSION_METHOD_RESULT_MODELS["sessions.claim_legacy"])
        props = schema["properties"]
        assert "was_already_claimed" in props
