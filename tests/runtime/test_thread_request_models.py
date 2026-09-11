"""Tests for thread request models."""

from __future__ import annotations

import pytest

from miqi.runtime.app_server import AppServerError
from miqi.runtime.thread_request_models import (
    THREAD_METHOD_PARAM_MODELS,
    validate_thread_params,
)


class TestAllMethodsExist:
    def test_all_18_methods_in_param_map(self):
        expected = {
            "thread/start", "thread/resume", "thread/fork",
            "thread/read", "thread/turns/list", "thread/turns/items/list",
            "thread/list", "thread/export", "thread/import",
            "thread/name/set", "thread/rollback", "thread/loaded/list",
            "thread.create", "thread.list", "thread.rename",
            "thread.archive", "thread.delete", "chat.abort",
        }
        assert set(THREAD_METHOD_PARAM_MODELS) == expected


class TestThreadStart:
    def test_accepts_empty_params(self):
        typed = validate_thread_params("thread/start", {})
        assert typed.title is None

    def test_accepts_title(self):
        typed = validate_thread_params("thread/start", {"title": "Hello"})
        assert typed.title == "Hello"

    def test_rejects_string_bool_ephemeral(self):
        with pytest.raises(AppServerError) as exc:
            validate_thread_params("thread/start", {"ephemeral": "true"})
        assert exc.value.code == "INVALID_PARAMS"


class TestThreadResume:
    def test_rejects_missing_thread_id(self):
        with pytest.raises(AppServerError) as exc:
            validate_thread_params("thread/resume", {})
        assert exc.value.code == "INVALID_PARAMS"

    def test_rejects_empty_thread_id(self):
        with pytest.raises(AppServerError) as exc:
            validate_thread_params("thread/resume", {"threadId": ""})
        assert exc.value.code == "INVALID_PARAMS"


class TestThreadList:
    def test_rejects_string_limit(self):
        with pytest.raises(AppServerError) as exc:
            validate_thread_params("thread/list", {"limit": "50"})
        assert exc.value.code == "INVALID_PARAMS"


class TestThreadRollback:
    def test_rejects_zero_drop_last_turns(self):
        with pytest.raises(AppServerError) as exc:
            validate_thread_params("thread/rollback", {"threadId": "t1", "dropLastTurns": 0})
        assert exc.value.code == "INVALID_PARAMS"


class TestThreadImport:
    def test_rejects_non_object_document(self):
        with pytest.raises(AppServerError) as exc:
            validate_thread_params("thread/import", {"document": "bad"})
        assert exc.value.code == "INVALID_PARAMS"


class TestThreadTurnsItemsList:
    def test_accepts_required_params_unsupported(self):
        """Validation passes; handler returns UNSUPPORTED_METHOD."""
        typed = validate_thread_params("thread/turns/items/list", {"threadId": "t1"})
        assert typed.thread_id == "t1"
