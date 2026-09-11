"""Tests for miqi.skills.xlsx.scripts.recalc — workbook lifecycle (Issue #87).

The xlsx recalc path opens two workbooks (data_only and formulas) and must
close both even when processing raises. These tests short-circuit the
LibreOffice subprocess and drive load_workbook with fakes that record
close() calls, so we can assert no workbook is leaked on the error path.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

# recalc.py is a script that imports a sibling `office` package via
# `from office.soffice import get_soffice_env` (it runs with scripts/ on
# sys.path). Stub it out before importing recalc so the test does not depend
# on LibreOffice's office package layout.
if "office" not in sys.modules:
    _office_pkg = types.ModuleType("office")
    _office_soffice = types.ModuleType("office.soffice")
    _office_soffice.get_soffice_env = lambda: {}
    _office_pkg.soffice = _office_soffice
    sys.modules["office"] = _office_pkg
    sys.modules["office.soffice"] = _office_soffice

from miqi.skills.xlsx.scripts import recalc  # noqa: E402


class _FakeSheet:
    """A sheet whose iter_rows() yields nothing — enough for the scan loops."""

    def iter_rows(self):
        return []


class _FakeWorkbook:
    """Records close() calls so tests can assert cleanup happened."""

    def __init__(self, *, raise_on_iter: bool = False):
        self.sheetnames = ["Sheet1"]
        self._closed = False
        self._raise_on_iter = raise_on_iter

    def __getitem__(self, name):
        if self._raise_on_iter:
            raise RuntimeError("boom during sheet access")
        return _FakeSheet()

    def iter_rows(self):
        return []

    def close(self):
        self._closed = True


def _patch_libreoffice(monkeypatch):
    """Skip the real LibreOffice subprocess plumbing."""
    monkeypatch.setattr(recalc, "setup_libreoffice_macro", lambda: True)
    monkeypatch.setattr(
        recalc.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stderr="", stdout="")
    )


def test_recalc_closes_data_workbook_on_success(tmp_path, monkeypatch):
    """Both workbooks are closed on the happy path."""
    f = tmp_path / "book.xlsx"
    f.write_bytes(b"fake")  # exists() check only

    wb_data = _FakeWorkbook()
    wb_formulas = _FakeWorkbook()
    monkeypatch.setattr(
        recalc, "load_workbook", lambda filename, data_only=None: wb_formulas if not data_only else wb_data
    )
    _patch_libreoffice(monkeypatch)

    recalc.recalc(str(f))

    assert wb_data._closed, "data workbook leaked on success"
    assert wb_formulas._closed, "formulas workbook leaked on success"


def test_recalc_closes_formulas_workbook_on_exception(tmp_path, monkeypatch):
    """If the formulas workbook raises mid-scan, it must still be closed."""
    f = tmp_path / "book.xlsx"
    f.write_bytes(b"fake")

    wb_data = _FakeWorkbook()
    wb_formulas = _FakeWorkbook(raise_on_iter=True)
    calls = {"data": 0, "formulas": 0}

    def _loader(filename, data_only=None):
        if data_only:
            calls["data"] += 1
            return wb_data
        calls["formulas"] += 1
        return wb_formulas

    monkeypatch.setattr(recalc, "load_workbook", _loader)
    _patch_libreoffice(monkeypatch)

    result = recalc.recalc(str(f))

    # The exception is caught and returned as an error dict, not raised.
    assert "error" in result
    assert wb_data._closed, "data workbook leaked on the error path"
    assert wb_formulas._closed, "formulas workbook leaked on the error path"


def test_recalc_surfaces_load_failure_without_nameerror(tmp_path, monkeypatch):
    """If load_workbook itself raises, the real error must be returned
    (Issue #87: the finally block must not mask it with a NameError on `wb`)."""
    f = tmp_path / "book.xlsx"
    f.write_bytes(b"fake")

    def _boom(filename, data_only=None):
        raise ValueError("corrupt file")

    monkeypatch.setattr(recalc, "load_workbook", _boom)
    _patch_libreoffice(monkeypatch)

    result = recalc.recalc(str(f))

    assert result == {"error": "corrupt file"}
