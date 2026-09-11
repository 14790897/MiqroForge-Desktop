from __future__ import annotations

from miqi.runtime.filesystem_response_models import (
    FILESYSTEM_EVENT_MODELS,
    FILESYSTEM_METHOD_RESULT_MODELS,
    DirectoryEntry,
    EmptyResult,
    FsChangedEvent,
    FsReadDirectoryResult,
    FsReadFileResult,
    FuzzyFileSearchResult,
    FuzzyMatch,
    FuzzySessionCompletedEvent,
    FuzzySessionUpdatedEvent,
)


def test_fs_read_file_result_shape():
    result = FsReadFileResult(data_base64="aGVsbG8=")

    assert result.model_dump(by_alias=True) == {"dataBase64": "aGVsbG8="}


def test_read_directory_result_shape():
    result = FsReadDirectoryResult(
        entries=[
            DirectoryEntry(file_name="a.txt", is_directory=False, is_file=True),
        ],
    )

    assert result.model_dump(by_alias=True) == {
        "entries": [
            {"fileName": "a.txt", "isDirectory": False, "isFile": True},
        ],
    }


def test_fuzzy_result_shape():
    result = FuzzyFileSearchResult(
        files=[
            FuzzyMatch(
                root="/repo",
                path="README.md",
                match_type="file",
                file_name="README.md",
                score=1000,
                indices=[0, 1],
            ),
        ],
    )

    dumped = result.model_dump(by_alias=True)
    assert dumped["files"][0]["match_type"] == "file"
    assert dumped["files"][0]["file_name"] == "README.md"


def test_fs_changed_event_shape():
    event = FsChangedEvent(watch_id="watch-1", changed_paths=["/repo/a.txt"])

    assert event.model_dump(by_alias=True) == {
        "watchId": "watch-1",
        "changedPaths": ["/repo/a.txt"],
    }


def test_fuzzy_session_events_shape():
    updated = FuzzySessionUpdatedEvent(session_id="s1", query="read", files=[])
    completed = FuzzySessionCompletedEvent(session_id="s1")

    assert updated.model_dump(by_alias=True)["sessionId"] == "s1"
    assert completed.model_dump(by_alias=True) == {"sessionId": "s1"}


def test_model_maps_cover_filesystem_methods_and_events():
    assert FILESYSTEM_METHOD_RESULT_MODELS["fs/readFile"] is FsReadFileResult
    assert FILESYSTEM_METHOD_RESULT_MODELS["fs/writeFile"] is EmptyResult
    assert FILESYSTEM_METHOD_RESULT_MODELS["fuzzyFileSearch"] is FuzzyFileSearchResult
    assert FILESYSTEM_EVENT_MODELS["fs/changed"] is FsChangedEvent
    assert FILESYSTEM_EVENT_MODELS["fuzzyFileSearch/sessionUpdated"] is FuzzySessionUpdatedEvent
