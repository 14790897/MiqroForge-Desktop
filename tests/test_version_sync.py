"""Guard against the package version drifting from the release manifests.

``scripts/update-version.sh`` (invoked by semantic-release's ``prepareCmd``)
rewrites the ``__version__`` literal in ``miqi/__init__.py`` together with the
three manifests, but nothing verified afterwards that it had — the constant sat
at ``0.1.4.post1`` for the whole 0.x line, so the CLI banner, the protocol
handshake and the diagnose scripts all reported a stale version (#1106).
These tests fail loudly if any of the four files is edited on its own.
"""

import json
import tomllib
from pathlib import Path

import pytest

from miqi import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFESTS = (
    "pyproject.toml",
    "package.json",
    "apps/desktop/package.json",
)


def _manifest_version(relative_path: str) -> str:
    path = REPO_ROOT / relative_path
    if path.suffix == ".toml":
        with path.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]
    return json.loads(path.read_text(encoding="utf-8"))["version"]


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_package_version_matches_release_manifest(manifest: str) -> None:
    expected = _manifest_version(manifest)
    assert __version__ == expected, (
        f"miqi/__init__.py declares __version__ = {__version__!r} but {manifest} "
        f"declares {expected!r}. All four are rewritten together by "
        "scripts/update-version.sh at release time; update them in the same commit."
    )
