"""Tests for Codex fs/* AppServer handlers (Phase 46).

Covers:
- fs/readFile, fs/writeFile, fs/createDirectory, fs/getMetadata
- fs/readDirectory, fs/remove, fs/copy
- Workspace containment, base64 encoding, error handling
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServer, ClientSessionRegistry
from miqi.runtime.fs_app_handlers import register_fs_handlers

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_server_and_registry(workspace: Path):
    """Create AppServer with fs/* handlers registered."""
    from types import SimpleNamespace

    fake_config = SimpleNamespace()
    fake_config.workspace_path = workspace.resolve()

    state = MagicMock()
    state.load_config.return_value = fake_config

    registry = ClientSessionRegistry()
    registry.bridge_context = {"state": state}

    server = AppServer(registry)
    register_fs_handlers(server)
    return server, registry


async def _dispatch(server, registry, method, params, client_id="client-1"):
    """Dispatch a request through AppServer and return the response."""
    return await server.dispatch(
        request_id="req-1",
        method=method,
        params=params,
        client_id=client_id,
        session_id=None,
    )


# ── fs/readFile ──────────────────────────────────────────────────────────────


class TestFsReadFile:
    """Tests for fs/readFile handler."""

    @pytest.mark.asyncio
    async def test_read_file_returns_base64(self, tmp_path):
        """Reading a file returns its contents as base64."""
        test_file = tmp_path / "hello.txt"
        test_file.write_bytes(b"Hello, Phase 46!")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readFile", {
            "path": str(test_file),
        })

        assert "result" in resp, f"Expected result, got: {resp}"
        assert resp["result"]["dataBase64"] == base64.b64encode(
            b"Hello, Phase 46!"
        ).decode("ascii")

    @pytest.mark.asyncio
    async def test_read_binary_file(self, tmp_path):
        """Reading a binary file returns correct base64."""
        test_file = tmp_path / "binary.bin"
        data = bytes(range(256))
        test_file.write_bytes(data)

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readFile", {
            "path": str(test_file),
        })

        assert "result" in resp
        decoded = base64.b64decode(resp["result"]["dataBase64"])
        assert decoded == data

    @pytest.mark.asyncio
    async def test_read_directory_fails(self, tmp_path):
        """Reading a directory returns INVALID_PARAMS."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir()

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readFile", {
            "path": str(test_dir),
        })

        assert resp.get("code") == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_read_nonexistent_file_fails(self, tmp_path):
        """Reading a nonexistent file returns NOT_FOUND."""
        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readFile", {
            "path": str(tmp_path / "nonexistent.txt"),
        })

        assert resp.get("code") == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_read_outside_workspace_fails(self, tmp_path):
        """Reading a file outside workspace returns INVALID_PARAMS."""
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("outside")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readFile", {
            "path": str(outside),
        })

        assert resp.get("code") == "INVALID_PARAMS"


# ── fs/writeFile ─────────────────────────────────────────────────────────────


class TestFsWriteFile:
    """Tests for fs/writeFile handler."""

    @pytest.mark.asyncio
    async def test_write_file_creates_content(self, tmp_path):
        """Writing base64 data creates the file with correct bytes."""
        test_file = tmp_path / "output.bin"
        data = b"Hello, Write!"
        encoded = base64.b64encode(data).decode("ascii")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(test_file),
            "dataBase64": encoded,
        })

        assert "result" in resp, f"Expected result, got: {resp}"
        assert test_file.read_bytes() == data

    @pytest.mark.asyncio
    async def test_write_and_read_roundtrip(self, tmp_path):
        """Write then read returns the same data."""
        test_file = tmp_path / "roundtrip.bin"
        data = bytes([0, 255, 128, 64, 32])
        encoded = base64.b64encode(data).decode("ascii")

        server, registry = _make_server_and_registry(tmp_path)

        # Write
        resp_w = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(test_file),
            "dataBase64": encoded,
        })
        assert "result" in resp_w

        # Read
        resp_r = await _dispatch(server, registry, "fs/readFile", {
            "path": str(test_file),
        })
        assert "result" in resp_r
        assert base64.b64decode(resp_r["result"]["dataBase64"]) == data

    @pytest.mark.asyncio
    async def test_invalid_base64_fails(self, tmp_path):
        """Invalid base64 in dataBase64 returns INVALID_PARAMS."""
        test_file = tmp_path / "output.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(test_file),
            "dataBase64": "!!!not-valid!!!",
        })

        assert resp.get("code") == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_write_outside_workspace_fails(self, tmp_path):
        """Writing outside workspace returns INVALID_PARAMS."""
        outside = tmp_path.parent / "outside.txt"
        encoded = base64.b64encode(b"test").decode("ascii")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(outside),
            "dataBase64": encoded,
        })

        assert resp.get("code") == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_write_to_nonexistent_directory_fails(self, tmp_path):
        """Writing to a path whose parent doesn't exist returns NOT_FOUND."""
        test_file = tmp_path / "nonexistent_dir" / "file.txt"
        encoded = base64.b64encode(b"test").decode("ascii")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(test_file),
            "dataBase64": encoded,
        })

        # Should fail because parent doesn't exist
        assert resp.get("code") in ("NOT_FOUND", "INVALID_PARAMS", "INTERNAL")


# ── fs/createDirectory ───────────────────────────────────────────────────────


class TestFsCreateDirectory:
    """Tests for fs/createDirectory handler."""

    @pytest.mark.asyncio
    async def test_create_directory_default_recursive(self, tmp_path):
        """Create directory with default recursive=true succeeds."""
        new_dir = tmp_path / "new-dir"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/createDirectory", {
            "path": str(new_dir),
        })

        assert "result" in resp
        assert new_dir.is_dir()

    @pytest.mark.asyncio
    async def test_create_nested_directory_recursive(self, tmp_path):
        """Creating nested directories with recursive=true succeeds."""
        nested = tmp_path / "a" / "b" / "c"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/createDirectory", {
            "path": str(nested),
            "recursive": True,
        })

        assert "result" in resp
        assert nested.is_dir()

    @pytest.mark.asyncio
    async def test_create_directory_no_recursive_fails_missing_parent(self, tmp_path):
        """Creating nested directory with recursive=false fails with NOT_FOUND."""
        nested = tmp_path / "x" / "y"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/createDirectory", {
            "path": str(nested),
            "recursive": False,
        })

        assert "error" in resp, f"Expected error, got: {resp}"
        assert resp.get("code") == "NOT_FOUND", (
            f"Missing parent should map to NOT_FOUND, got: {resp}"
        )
        assert not nested.exists(), "Directory must not be created"

    @pytest.mark.asyncio
    async def test_create_existing_directory_succeeds(self, tmp_path):
        """Creating an existing directory succeeds (with force semantics)."""
        existing = tmp_path / "existing"
        existing.mkdir()

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/createDirectory", {
            "path": str(existing),
        })

        assert "result" in resp

    @pytest.mark.asyncio
    async def test_create_outside_workspace_fails(self, tmp_path):
        """Creating directory outside workspace returns INVALID_PARAMS."""
        outside = tmp_path.parent / "outside-dir"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/createDirectory", {
            "path": str(outside),
        })

        assert resp.get("code") == "INVALID_PARAMS"


# ── fs/getMetadata ───────────────────────────────────────────────────────────


class TestFsGetMetadata:
    """Tests for fs/getMetadata handler."""

    @pytest.mark.asyncio
    async def test_file_metadata(self, tmp_path):
        """File returns isFile=True, isDirectory=False."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/getMetadata", {
            "path": str(test_file),
        })

        assert "result" in resp
        result = resp["result"]
        assert result["isFile"] is True
        assert result["isDirectory"] is False
        assert result["isSymlink"] is False
        assert isinstance(result["createdAtMs"], int)
        assert isinstance(result["modifiedAtMs"], int)

    @pytest.mark.asyncio
    async def test_directory_metadata(self, tmp_path):
        """Directory returns isDirectory=True, isFile=False."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir()

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/getMetadata", {
            "path": str(test_dir),
        })

        assert "result" in resp
        result = resp["result"]
        assert result["isDirectory"] is True
        assert result["isFile"] is False

    @pytest.mark.asyncio
    async def test_symlink_metadata(self, tmp_path):
        """Symlink returns isSymlink=True."""
        target = tmp_path / "target.txt"
        target.write_text("target")

        link = tmp_path / "link.txt"
        try:
            if sys.platform == "win32":
                os.symlink(str(target), str(link))
            else:
                link.symlink_to(target)
        except OSError:
            pytest.skip("Symlink creation not available")

        try:
            server, registry = _make_server_and_registry(tmp_path)
            resp = await _dispatch(server, registry, "fs/getMetadata", {
                "path": str(link),
            })

            assert "result" in resp
            assert resp["result"]["isSymlink"] is True
        finally:
            if link.exists():
                link.unlink()

    @pytest.mark.asyncio
    async def test_symlink_inside_workspace_reports_is_symlink_true(self, tmp_path):
        """Regression: fs/getMetadata on a symlink inside workspace returns isSymlink=True.

        resolve_workspace_absolute_path with resolve_symlinks=False must
        return the original (unresolved) path so that Path.lstat() can
        report symlink metadata, not the target's metadata.
        """
        target = tmp_path / "target.txt"
        target.write_text("target content")

        link = tmp_path / "link.txt"
        try:
            if sys.platform == "win32":
                os.symlink(str(target), str(link))
            else:
                link.symlink_to(target)
        except OSError:
            pytest.skip("Symlink creation not available")

        try:
            server, registry = _make_server_and_registry(tmp_path)
            resp = await _dispatch(server, registry, "fs/getMetadata", {
                "path": str(link),
            })

            assert "result" in resp, f"Expected result, got: {resp}"
            result = resp["result"]
            assert result["isSymlink"] is True, (
                f"isSymlink must be True for symlink path, got: {result}"
            )
        finally:
            if link.exists():
                link.unlink()

    @pytest.mark.asyncio
    async def test_camelcase_response_keys(self, tmp_path):
        """Response keys are camelCase."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/getMetadata", {
            "path": str(test_file),
        })

        result = resp["result"]
        expected = {"isDirectory", "isFile", "isSymlink", "createdAtMs", "modifiedAtMs"}
        assert set(result.keys()) == expected


# ── fs/readDirectory ─────────────────────────────────────────────────────────


class TestFsReadDirectory:
    """Tests for fs/readDirectory handler."""

    @pytest.mark.asyncio
    async def test_read_directory_returns_sorted_entries(self, tmp_path):
        """Direct children are returned sorted by fileName."""
        # Use a dedicated subdirectory so test isolation directories don't
        # interfere with the entry count assertion.
        work_dir = tmp_path / "listing-test"
        work_dir.mkdir()
        # Create files in non-sorted order
        (work_dir / "zebra.txt").write_text("z")
        (work_dir / "alpha.txt").write_text("a")
        (work_dir / "mike.txt").write_text("m")
        (work_dir / "subdir").mkdir()

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readDirectory", {
            "path": str(work_dir),
        })

        assert "result" in resp
        entries = resp["result"]["entries"]
        assert len(entries) == 4

        # Should be sorted by fileName
        names = [e["fileName"] for e in entries]
        assert names == sorted(names), f"Entries not sorted: {names}"

        # Check first entry is directory
        dir_entry = [e for e in entries if e["fileName"] == "subdir"][0]
        assert dir_entry["isDirectory"] is True
        assert dir_entry["isFile"] is False

        # Check file entry
        file_entry = [e for e in entries if e["fileName"] == "alpha.txt"][0]
        assert file_entry["isDirectory"] is False
        assert file_entry["isFile"] is True

    @pytest.mark.asyncio
    async def test_read_empty_directory(self, tmp_path):
        """Empty directory returns empty entries list."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readDirectory", {
            "path": str(empty_dir),
        })

        assert "result" in resp
        assert resp["result"]["entries"] == []

    @pytest.mark.asyncio
    async def test_read_file_fails(self, tmp_path):
        """Reading a file (not directory) returns INVALID_PARAMS or NOT_FOUND."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readDirectory", {
            "path": str(test_file),
        })

        assert "error" in resp or "code" in resp

    @pytest.mark.asyncio
    async def test_entry_keys_camelcase(self, tmp_path):
        """Directory entry keys are camelCase."""
        (tmp_path / "test.txt").write_text("hello")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readDirectory", {
            "path": str(tmp_path),
        })

        entry = resp["result"]["entries"][0]
        expected = {"fileName", "isDirectory", "isFile"}
        assert set(entry.keys()) == expected


# ── fs/remove ────────────────────────────────────────────────────────────────


class TestFsRemove:
    """Tests for fs/remove handler."""

    @pytest.mark.asyncio
    async def test_remove_file(self, tmp_path):
        """Removing a file succeeds."""
        test_file = tmp_path / "to_remove.txt"
        test_file.write_text("delete me")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/remove", {
            "path": str(test_file),
        })

        assert "result" in resp
        assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_remove_directory_recursive(self, tmp_path):
        """Removing a directory with recursive=true succeeds."""
        test_dir = tmp_path / "to_remove_dir"
        test_dir.mkdir()
        (test_dir / "child.txt").write_text("child")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/remove", {
            "path": str(test_dir),
            "recursive": True,
        })

        assert "result" in resp
        assert not test_dir.exists()

    @pytest.mark.asyncio
    async def test_remove_missing_path_default_force_succeeds(self, tmp_path):
        """Removing a missing path with default force=true succeeds."""
        missing = tmp_path / "nonexistent.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/remove", {
            "path": str(missing),
        })

        assert "result" in resp

    @pytest.mark.asyncio
    async def test_remove_missing_path_force_false_fails(self, tmp_path):
        """Removing a missing path with force=false returns NOT_FOUND."""
        missing = tmp_path / "nonexistent.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/remove", {
            "path": str(missing),
            "force": False,
        })

        assert resp.get("code") == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_remove_nonempty_directory_no_recursive_fails(self, tmp_path):
        """Removing a non-empty directory without recursive fails."""
        test_dir = tmp_path / "nonempty_dir"
        test_dir.mkdir()
        (test_dir / "child.txt").write_text("child")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/remove", {
            "path": str(test_dir),
            "recursive": False,
        })

        assert "error" in resp or "code" in resp


# ── fs/copy ──────────────────────────────────────────────────────────────────


class TestFsCopy:
    """Tests for fs/copy handler."""

    @pytest.mark.asyncio
    async def test_copy_file(self, tmp_path):
        """Copying a file copies its bytes."""
        src = tmp_path / "source.txt"
        src.write_bytes(b"copy me!")

        dst = tmp_path / "dest.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/copy", {
            "sourcePath": str(src),
            "destinationPath": str(dst),
        })

        assert "result" in resp
        assert dst.exists()
        assert dst.read_bytes() == b"copy me!"

    @pytest.mark.asyncio
    async def test_copy_directory_recursive(self, tmp_path):
        """Copying a directory with recursive=true copies contents."""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("a")
        (src_dir / "b.txt").write_text("b")
        (src_dir / "sub").mkdir()
        (src_dir / "sub" / "c.txt").write_text("c")

        dst_dir = tmp_path / "dst_dir"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/copy", {
            "sourcePath": str(src_dir),
            "destinationPath": str(dst_dir),
            "recursive": True,
        })

        assert "result" in resp
        assert dst_dir.is_dir()
        assert (dst_dir / "a.txt").read_text() == "a"
        assert (dst_dir / "b.txt").read_text() == "b"
        assert (dst_dir / "sub" / "c.txt").read_text() == "c"

    @pytest.mark.asyncio
    async def test_copy_directory_no_recursive_fails(self, tmp_path):
        """Copying a directory without recursive fails."""
        src_dir = tmp_path / "src_dir2"
        src_dir.mkdir()

        dst_dir = tmp_path / "dst_dir2"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/copy", {
            "sourcePath": str(src_dir),
            "destinationPath": str(dst_dir),
            "recursive": False,
        })

        assert "error" in resp or "code" in resp

    @pytest.mark.asyncio
    async def test_copy_outside_workspace_fails(self, tmp_path):
        """Copying a path outside workspace returns INVALID_PARAMS."""
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("outside")

        dst = tmp_path / "inside.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/copy", {
            "sourcePath": str(outside),
            "destinationPath": str(dst),
        })

        assert resp.get("code") == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_copy_source_nonexistent_fails(self, tmp_path):
        """Copying a nonexistent source fails."""
        src = tmp_path / "nonexistent.txt"
        dst = tmp_path / "dest.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/copy", {
            "sourcePath": str(src),
            "destinationPath": str(dst),
        })

        assert resp.get("code") == "NOT_FOUND"


# ── No text suffix filtering ─────────────────────────────────────────────────


class TestNoTextSuffixFiltering:
    """Verify that fs/* handlers do not apply MiQi files.* text suffix filtering."""

    @pytest.mark.asyncio
    async def test_read_binary_file_no_suffix_filter(self, tmp_path):
        """Binary file without text suffix can still be read."""
        # .bin is not in the text suffix list
        test_file = tmp_path / "data.bin"
        test_file.write_bytes(b"\x00\xFF\x00")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/readFile", {
            "path": str(test_file),
        })

        assert "result" in resp
        decoded = base64.b64decode(resp["result"]["dataBase64"])
        assert decoded == b"\x00\xFF\x00"

    @pytest.mark.asyncio
    async def test_write_binary_file_no_suffix_filter(self, tmp_path):
        """Binary file without text suffix can still be written."""
        test_file = tmp_path / "output.bin"
        data = bytes([0, 1, 2, 253, 254, 255])
        encoded = base64.b64encode(data).decode("ascii")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(test_file),
            "dataBase64": encoded,
        })

        assert "result" in resp
        assert test_file.read_bytes() == data


# ── Phase 64 typed validation regressions ───────────────────────────────


class TestPhase64TypedValidation:
    @pytest.mark.asyncio
    async def test_create_directory_rejects_string_recursive_without_creating(self, tmp_path):
        target = tmp_path / "bad-recursive"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/createDirectory", {
            "path": str(target),
            "recursive": "true",
        })

        assert resp.get("code") == "INVALID_PARAMS"
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_remove_rejects_string_force_without_removing(self, tmp_path):
        target = tmp_path / "keep.txt"
        target.write_text("keep", encoding="utf-8")

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/remove", {
            "path": str(target),
            "force": "true",
        })

        assert resp.get("code") == "INVALID_PARAMS"
        assert target.exists()

    @pytest.mark.asyncio
    async def test_copy_rejects_string_recursive_without_copying(self, tmp_path):
        source = tmp_path / "source.txt"
        source.write_text("source", encoding="utf-8")
        destination = tmp_path / "destination.txt"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/copy", {
            "sourcePath": str(source),
            "destinationPath": str(destination),
            "recursive": "false",
        })

        assert resp.get("code") == "INVALID_PARAMS"
        assert not destination.exists()

    @pytest.mark.asyncio
    async def test_write_file_rejects_non_string_data_before_write(self, tmp_path):
        target = tmp_path / "bad.bin"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(target),
            "dataBase64": 123,
        })

        assert resp.get("code") == "INVALID_PARAMS"
        assert not target.exists()

    @pytest.mark.asyncio
    async def test_write_file_empty_data_creates_empty_file(self, tmp_path):
        target = tmp_path / "empty.bin"

        server, registry = _make_server_and_registry(tmp_path)
        resp = await _dispatch(server, registry, "fs/writeFile", {
            "path": str(target),
            "dataBase64": "",
        })

        assert "result" in resp
        assert target.exists()
        assert target.read_bytes() == b""
