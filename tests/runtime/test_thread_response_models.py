"""Tests for thread response models."""

from __future__ import annotations

from miqi.runtime.protocol_model_schema import result_schema_from_model
from miqi.runtime.thread_response_models import THREAD_METHOD_RESULT_MODELS


class TestAllMethodsExist:
    def test_all_18_result_models_in_map(self):
        expected = {
            "thread/start", "thread/resume", "thread/fork",
            "thread/read", "thread/turns/list", "thread/turns/items/list",
            "thread/list", "thread/export", "thread/import",
            "thread/name/set", "thread/rollback", "thread/loaded/list",
            "thread.create", "thread.list", "thread.rename",
            "thread.archive", "thread.delete", "chat.abort",
        }
        assert set(THREAD_METHOD_RESULT_MODELS) == expected


class TestWireFieldNames:
    def test_thread_result_has_thread(self):
        schema = result_schema_from_model(THREAD_METHOD_RESULT_MODELS["thread/start"])
        assert "thread" in schema["properties"]

    def test_page_result_has_next_cursor(self):
        schema = result_schema_from_model(THREAD_METHOD_RESULT_MODELS["thread/turns/list"])
        assert "nextCursor" in schema["properties"]
        assert "items" in schema["properties"]

    def test_export_result_has_document(self):
        schema = result_schema_from_model(THREAD_METHOD_RESULT_MODELS["thread/export"])
        assert "document" in schema["properties"]

    def test_loaded_list_result_has_thread_ids(self):
        schema = result_schema_from_model(THREAD_METHOD_RESULT_MODELS["thread/loaded/list"])
        assert "threadIds" in schema["properties"]
