"""#875 review: sandbox.setAllowSystemInstalls must fail closed when the
runtime toggle cannot be applied.

config persisted + runtime applied → success;
config persisted + runtime update raised → AppServerError (UI stays off,
restart applies the persisted config).  Returning success here would
show "已开启" while the sandbox still denies installs (UI=true /
config=true / runtime=false).
"""

import pytest

from tests.bridge.test_bridge_loop import _CaptureSend, _dispatch_legacy


class _FailingSandboxManager:
    """Sandbox manager whose runtime toggle raises — simulating a broken
    manager after config persistence succeeded."""

    def __init__(self) -> None:
        object.__setattr__(self, "allow_system_installs", False)

    def __setattr__(self, name, value) -> None:
        if name == "allow_system_installs":
            raise RuntimeError("sandbox manager not writable (test)")
        super().__setattr__(name, value)


class _BridgeState:
    """Minimal bridge state for the toggle handler."""

    def __init__(self) -> None:
        self._sandbox_manager = _FailingSandboxManager()
        self.config = None

    def load_config(self):
        return self.config


async def test_set_allow_system_installs_fails_closed_on_runtime_error(tmp_path):
    """Runtime toggle failure must raise AppServerError, not return success."""
    from miqi.bridge.loop import BridgeRuntimeLoop
    from miqi.config.loader import update_config_field
    from miqi.runtime.app_server import AppServerError

    loop = BridgeRuntimeLoop(
        send_func=_CaptureSend().send,
        dispatch_legacy_func=_dispatch_legacy,
        bridge_state=_BridgeState(),
    )
    await loop._init_app_server()

    # point the config at a temp path so persistence writes into the test
    # sandbox instead of the real user config — must patch the name INSIDE
    # miqi.config.loader (it did `from miqi.paths import get_config_path`),
    # patching miqi.paths would not redirect the loader's writes.
    import miqi.config.loader as loader_mod

    real_config_path = loader_mod.get_config_path
    real_load_path = loader_mod._get_load_path
    fake_cfg = tmp_path / "config.json"
    loader_mod.get_config_path = lambda: fake_cfg
    loader_mod._get_load_path = lambda: fake_cfg
    try:
        # AppServerError is converted by dispatch into an error envelope
        # (fail-closed: the toggle must NOT report success).
        resp = await loop.app_server.dispatch(
            "req-1", "sandbox.setAllowSystemInstalls",
            {"enabled": True}, client_id="test-client",
        )
    finally:
        loader_mod.get_config_path = real_config_path
        loader_mod._get_load_path = real_load_path

    assert resp.get("result") is None
    assert "Runtime update failed" in resp.get("error", "")
    assert resp.get("code") == "INTERNAL"

    # config was persisted (restart will apply), but the runtime toggle
    # raised — that is exactly the partial state we surface as an error
    assert fake_cfg.exists()
    import json
    data = json.loads(fake_cfg.read_text(encoding="utf-8"))
    assert data["tools"]["sandbox"]["allowSystemInstalls"] is True

    await loop._shutdown()
