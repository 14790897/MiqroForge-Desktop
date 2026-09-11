#!/usr/bin/env python3
"""Build-environment preflight check for packaging miqi-bridge.exe.

Run this BEFORE `pyinstaller miqi.spec` (i.e. before `npm run build:bridge` /
`npm run build:win`). It verifies that onnxruntime's VC++ runtime dependency
is satisfied, because that is the one step that silently breaks a clean build
machine.

Background
----------
onnxruntime 1.26 needs ``msvcp140.dll`` >= 14.40. PyInstaller's Analysis phase
actually *imports* onnxruntime (to discover its DLLs), so a machine whose
``msvcp140.dll`` is too old / mismatched will fail with
"initialization routine failed" / "DLL load failed" and abort the build.

The final packaged exe is NOT affected (PyInstaller bundles the correct
``msvcp140.dll``), so end users running the exe need nothing. This check only
matters for machines that (re)build the bridge.

The authoritative signal is ``import onnxruntime`` itself: if it imports, the
VC++ runtime is fine (Python resolves the DLL from its own prefix dir first).
Only when the import fails do we diagnose the DLL versions.

Usage
-----
    python scripts/check-build-env.py           # report pass/fail
    python scripts/check-build-env.py --verbose # show every resolved DLL path

Exit code: 0 = ready to build, 1 = blocking problem (fix before packaging).
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

# onnxruntime 1.26 was observed to fail with msvcp140.dll 14.36 and work with
# 14.40. Treat 14.40 as the minimum safe version.
_MIN_MSVCP = (14, 40)
_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def _win_file_version(path: str) -> str | None:
    """Read the FileVersion of a PE/DLL via the Win32 version API."""
    if not path or not os.path.isfile(path):
        return None
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(path, 0, size, data):
            return None
        p = ctypes.c_void_p()
        u = wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
            data, "\\", ctypes.byref(p), ctypes.byref(u)
        ):
            return None

        class VS_FIXEDFILEINFO(ctypes.Structure):  # noqa: N801 (Win32 API name)
            _fields_ = [
                ("dwSignature", wintypes.DWORD),
                ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD),
                ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD),
                ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD),
                ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD),
                ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        info = ctypes.cast(p, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def _parse(ver: str | None) -> tuple[int, int] | None:
    if not ver:
        return None
    parts = ver.split(".")
    if len(parts) < 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _find_dll(name: str) -> list[str]:
    """Candidate locations in Python's own DLL search order (prefix first)."""
    candidates = []
    prefix = getattr(sys, "base_prefix", sys.prefix)
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for base in (prefix, os.path.join(windir, "System32")):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            candidates.append(p)
    seen, out = set(), []
    for p in candidates:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def _onnxruntime_status() -> str:
    try:
        import onnxruntime  # noqa: F401

        return "ok:" + getattr(onnxruntime, "__version__", "?")
    except ModuleNotFoundError:
        return "missing"
    except (OSError, ImportError) as e:
        return f"load-failed:{e}"


def _dump_dll_versions() -> None:
    """Print resolved versions of the two runtime DLLs (diagnostic aid)."""
    for name in ("msvcp140.dll", "vcruntime140.dll"):
        paths = _find_dll(name)
        if not paths:
            print(f"  {name}: NOT FOUND")
            continue
        for p in paths:
            ver = _win_file_version(p)
            parsed = _parse(ver)
            flag = "OK" if parsed and parsed >= _MIN_MSVCP else "TOO OLD"
            print(f"  {name}  {ver}  [{flag}]  {p}")


def _diagnose_vc_runtime() -> list[str]:
    """Explain WHY onnxruntime failed to load, by inspecting the runtime DLLs."""
    minimum = ".".join(str(n) for n in _MIN_MSVCP)
    problems: list[str] = []
    for name in ("msvcp140.dll", "vcruntime140.dll"):
        paths = _find_dll(name)
        if not paths:
            problems.append(f"{name} not found — VC++ Redistributable missing.")
            continue
        # The first path in _find_dll is the one Python actually loads (prefix
        # dir wins over System32). That one decides the build.
        primary = paths[0]
        ver = _win_file_version(primary)
        parsed = _parse(ver)
        state = "OK" if parsed and parsed >= _MIN_MSVCP else f"TOO OLD (need {minimum})"
        print(f"  {name} -> {ver} [{state}]  {primary}")
        if state.startswith("TOO OLD") or ver is None:
            problems.append(
                f"{name} is {ver or 'missing'} at {primary}; need >= {minimum}."
            )
    return problems


def main() -> int:
    verbose = "--verbose" in sys.argv
    print("=== MiQroForge bridge build env check ===")
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})")

    if sys.platform != "win32":
        status = _onnxruntime_status()
        if status.startswith("ok"):
            print(f"onnxruntime: OK ({status[3:]})")
            print("Result: PASS (non-Windows; no VC++ runtime to verify)")
            return 0
        print(f"onnxruntime: {status}")
        print("Result: FAIL — onnxruntime not importable (run `uv sync` first)")
        return 1

    status = _onnxruntime_status()
    if status.startswith("ok"):
        print(f"onnxruntime: OK ({status[3:]})")
        if verbose:
            _dump_dll_versions()
        print("Result: PASS — ready to run `pyinstaller miqi.spec`.")
        return 0

    if status == "missing":
        print("onnxruntime: NOT INSTALLED — run `uv sync` first.")
        print("Result: FAIL")
        return 1

    # load-failed: diagnose the VC++ runtime
    print(f"onnxruntime: FAILED TO LOAD — {status[12:]}")
    print()
    problems = _diagnose_vc_runtime()

    print()
    print("Result: FAIL")
    for p in problems:
        print(f"  - {p}")
    print()
    print(f"Fix: install the latest Microsoft Visual C++ 2015-2022 "
          f"Redistributable (x64): {_REDIST_URL}")
    print("then re-run this check. (Do NOT hand-copy DLLs into System32.)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
