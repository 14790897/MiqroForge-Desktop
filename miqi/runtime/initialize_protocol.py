"""Codex-style initialize/initialized protocol helpers (Phase 45).

Provides dataclasses and functions for parsing initialize requests,
building initialize responses, deriving client IDs, and managing
per-connection state.

The strict initialize handshake gate lives in BridgeRuntimeLoop.
Handlers registered here are transport-agnostic and can be used with
direct AppServer.dispatch() for testing.
"""

from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

import miqi
import miqi.runtime.protocol_specs as protocol_specs
from miqi.runtime.app_server import (
    AppServer,
    AppServerError,
    ClientCapabilities,
)

# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class ClientInfo:
    """Parsed client identity from initialize.params.clientInfo."""

    name: str
    title: str = ""
    version: str = ""


@dataclass
class InitializeCapabilities:
    """Parsed client capabilities from initialize.params.capabilities."""

    experimental_api: bool = False
    opt_out_notification_methods: set[str] = field(default_factory=set)

    @classmethod
    def from_params(cls, caps: dict[str, Any] | None) -> "InitializeCapabilities":
        """Parse client capabilities from params dict, applying defaults."""
        if caps is None:
            return cls()
        if not isinstance(caps, dict):
            raise AppServerError(
                "capabilities must be an object",
                code="INVALID_PARAMS",
            )

        if "experimentalApi" in caps:
            exp = caps["experimentalApi"]
            if not isinstance(exp, bool):
                raise AppServerError(
                    f"experimentalApi must be a boolean, got {type(exp).__name__}",
                    code="INVALID_PARAMS",
                )
            experimental = exp
        else:
            experimental = False

        opt_out_raw = caps.get("optOutNotificationMethods")
        opt_out: set[str] = set()
        if opt_out_raw is not None:
            if not isinstance(opt_out_raw, list):
                raise AppServerError(
                    "optOutNotificationMethods must be a list of strings",
                    code="INVALID_PARAMS",
                )
            for item in opt_out_raw:
                if not isinstance(item, str):
                    raise AppServerError(
                        "optOutNotificationMethods must be a list of strings",
                        code="INVALID_PARAMS",
                    )
                opt_out.add(item)

        return cls(
            experimental_api=experimental,
            opt_out_notification_methods=opt_out,
        )


@dataclass
class ConnectionState:
    """Per-connection state managed by BridgeRuntimeLoop.

    Tracks the Codex initialize handshake lifecycle for a single
    transport connection.
    """

    initialized: bool = False
    initialized_ack: bool = False  # True after initialized notification
    client_id: str | None = None
    client_info: ClientInfo | None = None
    capabilities: InitializeCapabilities | None = None


# ── Validation helpers ──────────────────────────────────────────────────────


def parse_initialize_params(
    params: dict[str, Any],
) -> tuple[ClientInfo, InitializeCapabilities]:
    """Validate and parse initialize params.

    Returns:
        (ClientInfo, InitializeCapabilities)

    Raises:
        AppServerError(INVALID_PARAMS) on validation failure.
    """
    # clientInfo is required
    client_info_raw = params.get("clientInfo")
    if client_info_raw is None:
        raise AppServerError(
            "clientInfo is required",
            code="INVALID_PARAMS",
        )
    if not isinstance(client_info_raw, dict):
        raise AppServerError(
            "clientInfo must be an object",
            code="INVALID_PARAMS",
        )

    # clientInfo.name is required and must be a non-empty string
    name = client_info_raw.get("name")
    if name is None:
        raise AppServerError(
            "clientInfo.name is required",
            code="INVALID_PARAMS",
        )
    if not isinstance(name, str) or not name.strip():
        raise AppServerError(
            "clientInfo.name must be a non-empty string",
            code="INVALID_PARAMS",
        )

    title = str(client_info_raw.get("title", ""))
    version = str(client_info_raw.get("version", ""))

    client_info = ClientInfo(name=name.strip(), title=title, version=version)

    # Parse capabilities (may be omitted)
    caps_raw = params.get("capabilities")
    capabilities = InitializeCapabilities.from_params(caps_raw)

    return client_info, capabilities


# ── Client ID derivation ────────────────────────────────────────────────────


_SAFE_SEGMENT_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def safe_client_id_segment(value: str) -> str:
    """Sanitize a string for use in a client_id segment.

    Replaces non-alphanumeric characters (except ., _, -) with _
    and truncates to 32 characters.
    """
    cleaned = _SAFE_SEGMENT_RE.sub("_", value).strip("_")
    if not cleaned:
        cleaned = "unknown"
    return cleaned[:32]


_EXPLICIT_CLIENT_ID_MAX_LEN = 128


def validate_explicit_client_id(raw: str) -> str:
    """Validate an explicit clientId from initialize params.

    Returns the stripped, validated client_id.

    Raises:
        AppServerError(INVALID_PARAMS) if the value is unsafe.
    """
    if not isinstance(raw, str):
        raise AppServerError(
            "clientId must be a string",
            code="INVALID_PARAMS",
        )
    stripped = raw.strip()
    if not stripped:
        raise AppServerError(
            "clientId must not be empty",
            code="INVALID_PARAMS",
        )
    if len(stripped) > _EXPLICIT_CLIENT_ID_MAX_LEN:
        raise AppServerError(
            f"clientId must not exceed {_EXPLICIT_CLIENT_ID_MAX_LEN} characters",
            code="INVALID_PARAMS",
        )
    # Reject path characters, parent-directory traversal, control chars
    if ".." in stripped:
        raise AppServerError(
            "clientId must not contain '..'",
            code="INVALID_PARAMS",
        )
    if "/" in stripped or "\\" in stripped:
        raise AppServerError(
            "clientId must not contain path separators",
            code="INVALID_PARAMS",
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in stripped):
        raise AppServerError(
            "clientId must not contain control characters",
            code="INVALID_PARAMS",
        )
    return stripped


def derive_client_id(
    params: dict[str, Any],
    client_info: ClientInfo,
) -> str:
    """Derive a stable client_id for the connection.

    If params.clientId is provided, it is validated via
    :func:`validate_explicit_client_id` before use.  Dangerous values
    (blank, path chars, ``..``, control chars, overly long) are
    rejected with INVALID_PARAMS instead of being silently replaced.

    When no explicit clientId is given, the id is derived from
    clientInfo.name: ``client-{safe(name)}-{uuid_hex[:8]}``
    """
    explicit = params.get("clientId")
    if explicit is not None:
        return validate_explicit_client_id(explicit)

    safe_name = safe_client_id_segment(client_info.name)
    short_id = uuid.uuid4().hex[:8]
    return f"client-{safe_name}-{short_id}"


# ── Initialize result builder ───────────────────────────────────────────────


def _get_data_home() -> str:
    """Return the MiQi data home directory path as a string."""
    try:
        from miqi.config.loader import get_data_dir

        return str(get_data_dir())
    except Exception:
        from miqi.paths import get_miqi_home
        return str(get_miqi_home())


def _get_miqi_version() -> str:
    """Get the MiQi package version."""
    try:
        return getattr(miqi, "__version__", "0.1.0")
    except Exception:
        return "0.1.0"


def build_initialize_result(
    client_id: str,
) -> dict[str, Any]:
    """Build the initialize response result envelope.

    Includes serverInfo, userAgent, miqiHome/codexHome, platform info,
    and server capabilities.
    """
    version = _get_miqi_version()
    data_home = _get_data_home()

    server_info = {
        "name": "miqi",
        "title": "MiQroForge",
        "version": version,
    }

    platform_family = sys.platform
    platform_os = sys.platform
    if platform_family.startswith("win"):
        platform_family = "windows"
    elif platform_family.startswith("linux"):
        platform_family = "linux"
    elif platform_family == "darwin":
        platform_family = "macos"

    return {
        "serverInfo": server_info,
        "userAgent": f"miqi-app-server/{version}",
        "miqiHome": data_home,
        "codexHome": data_home,
        "platformFamily": platform_family,
        "platformOs": platform_os,
        "clientId": client_id,
        "capabilities": {
            "experimentalApi": True,
            "supportsNotificationOptOut": True,
            "supportsWorkbenchProcesses": True,
            "supportsPty": False,
        },
    }


# ── Handler registration ────────────────────────────────────────────────────


def register_initialize_handler(server: AppServer) -> None:
    """Register initialize and initialized handlers on *server*.

    The initialize handler:
    - Validates params (clientInfo, capabilities)
    - Derives client_id
    - Stores ClientCapabilities on the AppServer
    - Returns the initialize result

    The initialized handler:
    - Accepts the initialized notification silently
    - Does NOT produce a response (it's a notification, not a request)
    - For direct AppServer.dispatch(), returns an empty result (no-op)

    The strict handshake gate (NOT_INITIALIZED / ALREADY_INITIALIZED) is
    enforced by BridgeRuntimeLoop, not by these handlers.
    """

    async def _initialize_handler(
        request_id: str,
        params: dict[str, Any],
        client_id: str,
        session_id: str | None,
        registry: Any,
    ) -> dict[str, Any]:
        """Handle the initialize request."""
        # Typed validation rejects non-string title/version etc.
        from miqi.runtime.core_request_models import validate_core_params
        validate_core_params("initialize", params)

        # Parse and validate params
        client_info, capabilities = parse_initialize_params(params)

        # Derive client_id
        cid = derive_client_id(params, client_info)

        # Store capabilities on AppServer
        server.set_client_capabilities(
            cid,
            ClientCapabilities(
                experimental_api=capabilities.experimental_api,
                opt_out_notification_methods=capabilities.opt_out_notification_methods,
                client_info={
                    "name": client_info.name,
                    "title": client_info.title,
                    "version": client_info.version,
                },
            ),
        )

        # Build and return response
        result = build_initialize_result(cid)
        return {"result": result}

    async def _initialized_handler(
        request_id: str,
        params: dict[str, Any],
        client_id: str,
        session_id: str | None,
        registry: Any,
    ) -> dict[str, Any]:
        """Handle the initialized notification.

        This is a notification — at the bridge level it produces no response.
        When dispatched directly through AppServer (tests), it returns an
        empty acknowledgment so the caller doesn't get UNKNOWN_METHOD.
        """
        # No-op: the bridge-level state machine advances initialized_ack.
        # For direct dispatch, return an empty result.
        return {"result": {"acknowledged": True}}

    server.register_method("initialize", _initialize_handler, spec=protocol_specs.INITIALIZE)
    server.register_method("initialized", _initialized_handler, spec=protocol_specs.INITIALIZED)
