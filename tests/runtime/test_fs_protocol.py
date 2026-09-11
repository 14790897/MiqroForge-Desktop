"""Tests for Codex fs protocol helpers (Phase 46).

Covers:
- resolve_workspace_absolute_path()
- workspace_root()
- decode_data_base64() / encode_data_base64()
- metadata_response() / directory_entry_response()
- map_os_error()
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miqi.runtime.app_server import AppServerError, ClientSessionRegistry
from miqi.runtime.fs_protocol import (
    decode_data_base64,
    directory_entry_response,
    encode_data_base64,
    map_os_error,
    metadata_response,
    resolve_workspace_absolute_path,
    workspace_root,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_registry(workspace: Path) -> ClientSessionRegistry:
    """Create a registry with a fake BridgeState pointing to *workspace*."""
    from types import SimpleNamespace

    fake_config = SimpleNamespace()
    fake_config.workspace_path = workspace.resolve()

    state = MagicMock()
    state.load_config.return_value = fake_config

    registry = ClientSessionRegistry()
    registry.bridge_context = {"state": state}
    return registry


# ── workspace_root / resolve_workspace_absolute_path ─────────────────────────


class TestWorkspaceResolution:
    """Tests for workspace_root() and resolve_workspace_absolute_path()."""

    def test_workspace_root_returns_resolved_path(self, tmp_path):
        """workspace_root() returns the configured workspace path."""
        registry = _make_registry(tmp_path)
        root = workspace_root(registry)
        assert root == tmp_path.resolve()

    def test_absolute_path_inside_workspace_resolves(self, tmp_path):
        """Absolute path inside workspace resolves successfully."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        registry = _make_registry(tmp_path)
        resolved = resolve_workspace_absolute_path(registry, str(test_file))
        assert resolved == test_file.resolve()

    def test_relative_path_rejected(self, tmp_path):
        """Relative path raises INVALID_PARAMS."""
        registry = _make_registry(tmp_path)

        with pytest.raises(AppServerError) as exc_info:
            resolve_workspace_absolute_path(registry, "relative/path.txt")
        assert exc_info.value.code == "INVALID_PARAMS"
        assert "must be an absolute path" in exc_info.value.message

    def test_absolute_path_outside_workspace_rejected(self, tmp_path):
        """Path outside configured workspace raises INVALID_PARAMS."""
        registry = _make_registry(tmp_path)
        outside = tmp_path.parent / "outside.txt"

        with pytest.raises(AppServerError) as exc_info:
            resolve_workspace_absolute_path(registry, str(outside))
        assert exc_info.value.code == "INVALID_PARAMS"
        assert "must be inside workspace" in exc_info.value.message

    def test_non_string_path_rejected(self, tmp_path):
        """Non-string raw_path raises INVALID_PARAMS."""
        registry = _make_registry(tmp_path)

        with pytest.raises(AppServerError) as exc_info:
            resolve_workspace_absolute_path(registry, None)
        assert exc_info.value.code == "INVALID_PARAMS"

    def test_empty_string_path_rejected(self, tmp_path):
        """Empty string path raises INVALID_PARAMS."""
        registry = _make_registry(tmp_path)

        with pytest.raises(AppServerError) as exc_info:
            resolve_workspace_absolute_path(registry, "   ")
        assert exc_info.value.code == "INVALID_PARAMS"

    def test_custom_field_name_in_error(self, tmp_path):
        """Error message uses the provided field_name."""
        registry = _make_registry(tmp_path)

        with pytest.raises(AppServerError) as exc_info:
            resolve_workspace_absolute_path(
                registry, "bad/path", field_name="sourcePath",
            )
        assert "sourcePath" in exc_info.value.message

    def test_symlink_resolving_outside_workspace_rejected(self, tmp_path):
        """Symlink that resolves outside workspace raises INVALID_PARAMS."""
        registry = _make_registry(tmp_path)
        outside_dir = tmp_path.parent / "outside_dir"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "target.txt").write_text("outside")

        symlink = tmp_path / "escape_link"
        # On Windows, symlinks need specific permissions
        try:
            if sys.platform == "win32":
                os.symlink(str(outside_dir / "target.txt"), str(symlink))
            else:
                symlink.symlink_to(outside_dir / "target.txt")
        except OSError:
            pytest.skip("Symlink creation not available")

        try:
            with pytest.raises(AppServerError) as exc_info:
                resolve_workspace_absolute_path(registry, str(symlink))
            assert exc_info.value.code == "INVALID_PARAMS"
            assert "must be inside workspace" in exc_info.value.message
        finally:
            if symlink.exists():
                symlink.unlink()


# ── Base64 helpers ───────────────────────────────────────────────────────────


class TestBase64:
    """Tests for decode_data_base64() and encode_data_base64()."""

    def test_valid_base64_decode(self):
        """Valid base64 string decodes correctly."""
        import base64

        data = b"Hello, World!"
        encoded = base64.b64encode(data).decode("ascii")
        decoded = decode_data_base64(encoded)
        assert decoded == data

    def test_invalid_base64_rejected(self):
        """Invalid base64 string raises INVALID_PARAMS."""
        with pytest.raises(AppServerError) as exc_info:
            decode_data_base64("!!!not-valid-base64!!!")
        assert exc_info.value.code == "INVALID_PARAMS"

    def test_non_string_base64_rejected(self):
        """Non-string base64 raises INVALID_PARAMS."""
        with pytest.raises(AppServerError) as exc_info:
            decode_data_base64(12345)
        assert exc_info.value.code == "INVALID_PARAMS"

    def test_encode_base64_roundtrip(self):
        """encode_data_base64() returns valid base64 decodable by decode_data_base64()."""
        import base64

        data = b"\x00\x01\x02\xFF\xFE\xFD"
        encoded = encode_data_base64(data)
        # Should decode back
        assert base64.b64decode(encoded) == data
        assert decode_data_base64(encoded) == data

    def test_decode_binary_data(self):
        """decode handles binary (non-UTF8) data."""
        import base64

        data = bytes(range(256))
        encoded = base64.b64encode(data).decode("ascii")
        decoded = decode_data_base64(encoded)
        assert decoded == data


# ── metadata_response ────────────────────────────────────────────────────────


class TestMetadataResponse:
    """Tests for metadata_response()."""

    def test_file_metadata(self, tmp_path):
        """Regular file returns correct camelCase fields."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        meta = metadata_response(test_file)
        assert meta["isDirectory"] is False
        assert meta["isFile"] is True
        assert meta["isSymlink"] is False
        assert isinstance(meta["createdAtMs"], int)
        assert isinstance(meta["modifiedAtMs"], int)
        assert meta["modifiedAtMs"] > 0

    def test_directory_metadata(self, tmp_path):
        """Directory returns isDirectory=True."""
        test_dir = tmp_path / "subdir"
        test_dir.mkdir()

        meta = metadata_response(test_dir)
        assert meta["isDirectory"] is True
        assert meta["isFile"] is False
        assert meta["isSymlink"] is False

    def test_symlink_metadata(self, tmp_path):
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
            meta = metadata_response(link)
            assert meta["isSymlink"] is True
        finally:
            if link.exists():
                link.unlink()

    def test_camelcase_keys(self, tmp_path):
        """All metadata keys are camelCase."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        meta = metadata_response(test_file)
        expected_keys = {"isDirectory", "isFile", "isSymlink", "createdAtMs", "modifiedAtMs"}
        assert set(meta.keys()) == expected_keys


# ── directory_entry_response ─────────────────────────────────────────────────


class TestDirectoryEntryResponse:
    """Tests for directory_entry_response()."""

    def test_file_entry(self, tmp_path):
        """File entry returns correct fields."""
        test_file = tmp_path / "README.md"
        test_file.write_text("# README")

        entry = directory_entry_response(test_file)
        assert entry["fileName"] == "README.md"
        assert entry["isDirectory"] is False
        assert entry["isFile"] is True

    def test_directory_entry(self, tmp_path):
        """Directory entry returns isDirectory=True."""
        test_dir = tmp_path / "src"
        test_dir.mkdir()

        entry = directory_entry_response(test_dir)
        assert entry["fileName"] == "src"
        assert entry["isDirectory"] is True
        assert entry["isFile"] is False

    def test_camelcase_keys(self, tmp_path):
        """All directory entry keys are camelCase."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        entry = directory_entry_response(test_file)
        expected_keys = {"fileName", "isDirectory", "isFile"}
        assert set(entry.keys()) == expected_keys


# ── map_os_error ─────────────────────────────────────────────────────────────


class TestMapOsError:
    """Tests for map_os_error()."""

    def test_permission_error(self):
        """PermissionError maps to PERMISSION_DENIED."""
        exc = PermissionError("Access denied")
        mapped = map_os_error(exc)
        assert mapped.code == "PERMISSION_DENIED"

    def test_file_not_found(self):
        """FileNotFoundError maps to NOT_FOUND."""
        exc = FileNotFoundError("No such file")
        mapped = map_os_error(exc)
        assert mapped.code == "NOT_FOUND"

    def test_file_exists(self):
        """FileExistsError maps to ALREADY_EXISTS."""
        exc = FileExistsError("File exists")
        mapped = map_os_error(exc)
        assert mapped.code == "ALREADY_EXISTS"

    def test_not_a_directory(self):
        """NotADirectoryError maps to INVALID_PARAMS."""
        exc = NotADirectoryError("Not a directory")
        mapped = map_os_error(exc)
        assert mapped.code == "INVALID_PARAMS"

    def test_is_a_directory(self):
        """IsADirectoryError maps to INVALID_PARAMS."""
        exc = IsADirectoryError("Is a directory")
        mapped = map_os_error(exc)
        assert mapped.code == "INVALID_PARAMS"

    def test_default_code(self):
        """Unmapped OSError uses default_code."""
        exc = OSError("Something went wrong")
        mapped = map_os_error(exc)
        assert mapped.code == "INTERNAL"

    def test_custom_default_code(self):
        """Custom default_code is used for unmapped errors."""
        exc = OSError("Something went wrong")
        mapped = map_os_error(exc, default_code="CUSTOM_ERROR")
        assert mapped.code == "CUSTOM_ERROR"
