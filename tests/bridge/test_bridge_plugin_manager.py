"""Tests for bridge PluginManager initialization — ensures discover()
is awaited properly in async contexts without RuntimeWarning."""

import warnings

import pytest


@pytest.mark.asyncio
async def test_discover_awaited_in_async_context_no_runtime_warning():
    """Verify that PluginManager.discover() is properly awaited and
    does not produce 'never awaited' RuntimeWarnings.

    Uses the same event loop as other async bridge tests so that
    no additional ProactorEventLoop (and its internal socket pair)
    is created — avoiding ResourceWarning on cleanup.
    """
    import tempfile
    from pathlib import Path

    from miqi.skills.plugin_manager import PluginManager

    with tempfile.TemporaryDirectory() as tmp:
        user_dir = Path(tmp) / "user"
        user_dir.mkdir()
        system_dir = Path(tmp) / "system"
        system_dir.mkdir()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            pm = PluginManager(
                user_plugins_dir=user_dir,
                system_plugins_dir=system_dir,
                workspace=Path(tmp),
            )
            result = await pm.discover()

        runtime_warnings = [
            x for x in w
            if "never awaited" in str(x.message)
        ]
        assert len(runtime_warnings) == 0, (
            f"RuntimeWarning detected: {[str(x.message) for x in runtime_warnings]}"
        )
        assert isinstance(result, list)
