"""Explicit App Server protocol specs for high-value Codex-style methods."""

from __future__ import annotations

from typing import Any

from miqi.runtime.core_request_models import (
    ConfigBatchWriteParams,
    ConfigUpdateParams,
    EmptyParams,
    ExperimentalFeatureEnablementSetParams,
    ExperimentalFeatureListParams,
    InitializeParams,
    ModelListParams,
    ModelProviderCapabilitiesReadParams,
    PermissionProfileListParams,
)
from miqi.runtime.core_response_models import CORE_METHOD_RESULT_MODELS
from miqi.runtime.filesystem_request_models import (
    FsCopyParams,
    FsCreateDirectoryParams,
    FsGetMetadataParams,
    FsReadDirectoryParams,
    FsReadFileParams,
    FsRemoveParams,
    FsUnwatchParams,
    FsWatchParams,
    FsWriteFileParams,
    FuzzyFileSearchParams,
    FuzzySessionStartParams,
    FuzzySessionStopParams,
    FuzzySessionUpdateParams,
)
from miqi.runtime.filesystem_response_models import (
    FILESYSTEM_EVENT_MODELS,
    FILESYSTEM_METHOD_RESULT_MODELS,
)
from miqi.runtime.plugin_skill_request_models import (
    PLUGIN_SKILL_METHOD_PARAM_MODELS,
    HooksListParams,
    MarketplaceAddParams,
    MarketplaceRemoveParams,
    MarketplaceUpgradeParams,
    PluginInstallParams,
    PluginReadParams,
    PluginSkillReadParams,
    PluginUninstallParams,
    SkillsExtraRootsSetParams,
    SkillsListParams,
)
from miqi.runtime.plugin_skill_response_models import PLUGIN_SKILL_METHOD_RESULT_MODELS
from miqi.runtime.process_request_models import (
    CommandExecParams,
    CommandExecResizeParams,
    CommandExecTerminateParams,
    CommandExecWriteParams,
    ProcessKillParams,
    ProcessResizePtyParams,
    ProcessSpawnParams,
    ProcessWriteStdinParams,
)
from miqi.runtime.process_response_models import (
    PROCESS_EVENT_MODELS,
    PROCESS_METHOD_RESULT_MODELS,
)
from miqi.runtime.protocol_model_schema import model_spec
from miqi.runtime.protocol_registry import (
    MethodScope,
    MethodStability,
    ProtocolMethodSpec,
)
from miqi.runtime.session_request_models import (
    SESSION_METHOD_PARAM_MODELS,
    SessionKeyParams,
)
from miqi.runtime.session_response_models import SESSION_METHOD_RESULT_MODELS
from miqi.runtime.thread_request_models import (
    ChatAbortParams,
    ThreadArchiveCompatParams,
    ThreadCreateCompatParams,
    ThreadDeleteCompatParams,
    ThreadExportParams,
    ThreadForkParams,
    ThreadImportParams,
    ThreadListCompatParams,
    ThreadListParams,
    ThreadLoadedListParams,
    ThreadNameSetParams,
    ThreadReadParams,
    ThreadRenameCompatParams,
    ThreadResumeParams,
    ThreadRollbackParams,
    ThreadStartParams,
    ThreadTurnsItemsListParams,
    ThreadTurnsListParams,
)
from miqi.runtime.thread_response_models import THREAD_METHOD_RESULT_MODELS
from miqi.runtime.turn_request_models import (
    ThreadCompactStartParams,
    ThreadInjectItemsParams,
    TurnInterruptParams,
    TurnStartParams,
    TurnSteerParams,
)

OBJECT: dict[str, Any] = {"type": "object"}
EMPTY_RESULT: dict[str, Any] = {"type": "object", "additionalProperties": True}


def object_schema(*, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "additionalProperties": True}
    if required:
        schema["required"] = required
    return schema


def spec(
    method: str,
    *,
    scope: MethodScope,
    stability: MethodStability = MethodStability.STABLE,
    required: list[str] | None = None,
    emits: list[str] | None = None,
    description: str | None = None,
) -> ProtocolMethodSpec:
    return ProtocolMethodSpec(
        method=method,
        stability=stability,
        scope=scope,
        params_schema=object_schema(required=required),
        result_schema=EMPTY_RESULT,
        emits=emits or [],
        description=description,
    )


INITIALIZE = model_spec(
    "initialize",
    InitializeParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["initialize"],
    description="Negotiate client identity and capabilities.",
)

INITIALIZED = model_spec(
    "initialized",
    EmptyParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["initialized"],
    description="Acknowledge initialize completion.",
)

STATUS = model_spec(
    "status",
    EmptyParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["status"],
)

PYTHON_CHECK = model_spec(
    "python.check",
    EmptyParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["python.check"],
)

CONFIG_READ = model_spec(
    "config/read",
    EmptyParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["config/read"],
)

CONFIG_BATCH_WRITE = model_spec(
    "config/batchWrite",
    ConfigBatchWriteParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["config/batchWrite"],
)

CONFIG_GET = model_spec(
    "config.get",
    EmptyParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["config.get"],
)

CONFIG_UPDATE = model_spec(
    "config.update",
    ConfigUpdateParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["config.update"],
)

MODEL_LIST = model_spec(
    "model/list",
    ModelListParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["model/list"],
)

MODEL_PROVIDER_CAPABILITIES_READ = model_spec(
    "modelProvider/capabilities/read",
    ModelProviderCapabilitiesReadParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["modelProvider/capabilities/read"],
)

EXPERIMENTAL_FEATURE_LIST = model_spec(
    "experimentalFeature/list",
    ExperimentalFeatureListParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["experimentalFeature/list"],
)

EXPERIMENTAL_FEATURE_ENABLEMENT_SET = model_spec(
    "experimentalFeature/enablement/set",
    ExperimentalFeatureEnablementSetParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["experimentalFeature/enablement/set"],
)

PERMISSION_PROFILE_LIST = model_spec(
    "permissionProfile/list",
    PermissionProfileListParams,
    scope=MethodScope.CONNECTION,
    result_model=CORE_METHOD_RESULT_MODELS["permissionProfile/list"],
)

# ── plugin / marketplace ─────────────────────────────────────────────────

PLUGIN_LIST = model_spec(
    "plugin/list",
    PLUGIN_SKILL_METHOD_PARAM_MODELS["plugin/list"],
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/list"],
)
PLUGIN_INSTALLED = model_spec(
    "plugin/installed",
    PLUGIN_SKILL_METHOD_PARAM_MODELS["plugin/installed"],
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/installed"],
)
PLUGIN_READ = model_spec(
    "plugin/read",
    PluginReadParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/read"],
)
PLUGIN_SKILL_READ = model_spec(
    "plugin/skill/read",
    PluginSkillReadParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/skill/read"],
)
PLUGIN_INSTALL = model_spec(
    "plugin/install",
    PluginInstallParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/install"],
)
PLUGIN_UNINSTALL = model_spec(
    "plugin/uninstall",
    PluginUninstallParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["plugin/uninstall"],
)
MARKETPLACE_ADD = model_spec(
    "marketplace/add",
    MarketplaceAddParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["marketplace/add"],
)
MARKETPLACE_REMOVE = model_spec(
    "marketplace/remove",
    MarketplaceRemoveParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["marketplace/remove"],
)
MARKETPLACE_UPGRADE = model_spec(
    "marketplace/upgrade",
    MarketplaceUpgradeParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["marketplace/upgrade"],
)

# ── skills / hooks ───────────────────────────────────────────────────────

SKILLS_LIST = model_spec(
    "skills/list",
    SkillsListParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["skills/list"],
)
SKILLS_EXTRA_ROOTS_SET = model_spec(
    "skills/extraRoots/set",
    SkillsExtraRootsSetParams,
    scope=MethodScope.CONNECTION,
    emits=["skills/changed"],
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["skills/extraRoots/set"],
)
HOOKS_LIST = model_spec(
    "hooks/list",
    HooksListParams,
    scope=MethodScope.CONNECTION,
    result_model=PLUGIN_SKILL_METHOD_RESULT_MODELS["hooks/list"],
)

# ── sessions ──────────────────────────────────────────────────────────────

SESSIONS_LIST = model_spec(
    "sessions.list",
    SESSION_METHOD_PARAM_MODELS["sessions.list"],
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.list"],
)
SESSIONS_GET = model_spec(
    "sessions.get",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.get"],
)
SESSIONS_DELETE = model_spec(
    "sessions.delete",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.delete"],
)
SESSIONS_ARCHIVE = model_spec(
    "sessions.archive",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.archive"],
)
SESSIONS_UNARCHIVE = model_spec(
    "sessions.unarchive",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.unarchive"],
)
SESSIONS_LIST_ARCHIVED = model_spec(
    "sessions.list_archived",
    SESSION_METHOD_PARAM_MODELS["sessions.list_archived"],
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.list_archived"],
)
SESSIONS_GET_TRACKED_FILES = model_spec(
    "sessions.get_tracked_files",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.get_tracked_files"],
)
SESSIONS_CLEAR_TRACKED_FILES = model_spec(
    "sessions.clear_tracked_files",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.clear_tracked_files"],
)
SESSIONS_CLAIM_LEGACY = model_spec(
    "sessions.claim_legacy",
    SessionKeyParams,
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.claim_legacy"],
)
SESSIONS_RENAME = model_spec(
    "sessions.rename",
    SESSION_METHOD_PARAM_MODELS["sessions.rename"],
    scope=MethodScope.SESSION,
    result_model=SESSION_METHOD_RESULT_MODELS["sessions.rename"],
)

# ── thread (Codex-style) ──────────────────────────────────────────────────

THREAD_START = model_spec(
    "thread/start",
    ThreadStartParams,
    scope=MethodScope.THREAD,
    emits=["thread/started"],
    result_model=THREAD_METHOD_RESULT_MODELS["thread/start"],
)
THREAD_RESUME = model_spec(
    "thread/resume",
    ThreadResumeParams,
    scope=MethodScope.THREAD,
    emits=["thread/started"],
    result_model=THREAD_METHOD_RESULT_MODELS["thread/resume"],
)
THREAD_FORK = model_spec(
    "thread/fork",
    ThreadForkParams,
    scope=MethodScope.THREAD,
    emits=["thread/started"],
    result_model=THREAD_METHOD_RESULT_MODELS["thread/fork"],
)
THREAD_READ = model_spec(
    "thread/read",
    ThreadReadParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/read"],
)
THREAD_TURNS_LIST = model_spec(
    "thread/turns/list",
    ThreadTurnsListParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/turns/list"],
)
THREAD_TURNS_ITEMS_LIST = model_spec(
    "thread/turns/items/list",
    ThreadTurnsItemsListParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/turns/items/list"],
)
THREAD_LIST = model_spec(
    "thread/list",
    ThreadListParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/list"],
)
THREAD_EXPORT = model_spec(
    "thread/export",
    ThreadExportParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/export"],
)
THREAD_IMPORT = model_spec(
    "thread/import",
    ThreadImportParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/import"],
)
THREAD_NAME_SET = model_spec(
    "thread/name/set",
    ThreadNameSetParams,
    scope=MethodScope.THREAD,
    emits=["thread/name/updated"],
    result_model=THREAD_METHOD_RESULT_MODELS["thread/name/set"],
)
THREAD_ROLLBACK = model_spec(
    "thread/rollback",
    ThreadRollbackParams,
    scope=MethodScope.THREAD,
    emits=["thread/rollback"],
    result_model=THREAD_METHOD_RESULT_MODELS["thread/rollback"],
)
THREAD_LOADED_LIST = model_spec(
    "thread/loaded/list",
    ThreadLoadedListParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread/loaded/list"],
)

# ── thread compatibility ──────────────────────────────────────────────────

THREAD_CREATE_COMPAT = model_spec(
    "thread.create",
    ThreadCreateCompatParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread.create"],
)
THREAD_LIST_COMPAT = model_spec(
    "thread.list",
    ThreadListCompatParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread.list"],
)
THREAD_RENAME_COMPAT = model_spec(
    "thread.rename",
    ThreadRenameCompatParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread.rename"],
)
THREAD_ARCHIVE_COMPAT = model_spec(
    "thread.archive",
    ThreadArchiveCompatParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread.archive"],
)
THREAD_DELETE_COMPAT = model_spec(
    "thread.delete",
    ThreadDeleteCompatParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["thread.delete"],
)
CHAT_ABORT = model_spec(
    "chat.abort",
    ChatAbortParams,
    scope=MethodScope.THREAD,
    result_model=THREAD_METHOD_RESULT_MODELS["chat.abort"],
)

TURN_START = model_spec(
    "turn/start",
    TurnStartParams,
    scope=MethodScope.TURN,
    emits=["turn/started", "item/started", "item/completed", "turn/completed"],
)
TURN_INTERRUPT = model_spec("turn/interrupt", TurnInterruptParams, scope=MethodScope.TURN)
TURN_STEER = model_spec("turn/steer", TurnSteerParams, scope=MethodScope.TURN)
THREAD_COMPACT_START = model_spec(
    "thread/compact/start",
    ThreadCompactStartParams,
    scope=MethodScope.THREAD,
    emits=["turn/started", "item/started", "item/completed", "turn/completed"],
)
THREAD_INJECT_ITEMS = model_spec("thread/inject_items", ThreadInjectItemsParams, scope=MethodScope.THREAD)
THREAD_SHELL_COMMAND = spec(
    "thread/shellCommand",
    scope=MethodScope.THREAD,
    required=["threadId", "command"],
    emits=["turn/started", "item/started", "item/commandExecution/outputDelta", "item/completed", "turn/completed"],
)

COMMAND_EXEC = model_spec(
    "command/exec",
    CommandExecParams,
    scope=MethodScope.PROCESS,
    emits=["command/exec/outputDelta"],
    result_model=PROCESS_METHOD_RESULT_MODELS["command/exec"],
    event_models={
        "command/exec/outputDelta": PROCESS_EVENT_MODELS["command/exec/outputDelta"],
    },
)
COMMAND_EXEC_WRITE = model_spec(
    "command/exec/write",
    CommandExecWriteParams,
    scope=MethodScope.PROCESS,
    result_model=PROCESS_METHOD_RESULT_MODELS["command/exec/write"],
    description="Send stdin data or close stdin on a command/exec process. "
    "At least one of deltaBase64 or closeStdin must be provided.",
)
COMMAND_EXEC_RESIZE = model_spec(
    "command/exec/resize",
    CommandExecResizeParams,
    scope=MethodScope.PROCESS,
    stability=MethodStability.EXPERIMENTAL,
    result_model=PROCESS_METHOD_RESULT_MODELS["command/exec/resize"],
    description="PTY resize is not supported in this version — always returns UNSUPPORTED_FEATURE.",
)
COMMAND_EXEC_TERMINATE = model_spec(
    "command/exec/terminate",
    CommandExecTerminateParams,
    scope=MethodScope.PROCESS,
    result_model=PROCESS_METHOD_RESULT_MODELS["command/exec/terminate"],
)

PROCESS_SPAWN = model_spec(
    "process/spawn",
    ProcessSpawnParams,
    scope=MethodScope.PROCESS,
    emits=["process/outputDelta", "process/exited"],
    result_model=PROCESS_METHOD_RESULT_MODELS["process/spawn"],
    event_models={
        "process/outputDelta": PROCESS_EVENT_MODELS["process/outputDelta"],
        "process/exited": PROCESS_EVENT_MODELS["process/exited"],
    },
)
PROCESS_WRITE_STDIN = model_spec(
    "process/writeStdin",
    ProcessWriteStdinParams,
    scope=MethodScope.PROCESS,
    result_model=PROCESS_METHOD_RESULT_MODELS["process/writeStdin"],
    description="Send stdin data or close stdin on a background process. "
    "At least one of deltaBase64 or closeStdin must be provided.",
)
PROCESS_RESIZE_PTY = model_spec(
    "process/resizePty",
    ProcessResizePtyParams,
    scope=MethodScope.PROCESS,
    stability=MethodStability.EXPERIMENTAL,
    result_model=PROCESS_METHOD_RESULT_MODELS["process/resizePty"],
    description="PTY resize is not supported in this version — always returns UNSUPPORTED_FEATURE.",
)
PROCESS_KILL = model_spec(
    "process/kill",
    ProcessKillParams,
    scope=MethodScope.PROCESS,
    result_model=PROCESS_METHOD_RESULT_MODELS["process/kill"],
)

FS_READ_FILE = model_spec(
    "fs/readFile",
    FsReadFileParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/readFile"],
)
FS_WRITE_FILE = model_spec(
    "fs/writeFile",
    FsWriteFileParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/writeFile"],
)
FS_CREATE_DIRECTORY = model_spec(
    "fs/createDirectory",
    FsCreateDirectoryParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/createDirectory"],
)
FS_GET_METADATA = model_spec(
    "fs/getMetadata",
    FsGetMetadataParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/getMetadata"],
)
FS_READ_DIRECTORY = model_spec(
    "fs/readDirectory",
    FsReadDirectoryParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/readDirectory"],
)
FS_REMOVE = model_spec(
    "fs/remove",
    FsRemoveParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/remove"],
)
FS_COPY = model_spec(
    "fs/copy",
    FsCopyParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/copy"],
)
FS_WATCH = model_spec(
    "fs/watch",
    FsWatchParams,
    scope=MethodScope.FILESYSTEM,
    emits=["fs/changed"],
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/watch"],
    event_models={"fs/changed": FILESYSTEM_EVENT_MODELS["fs/changed"]},
)
FS_UNWATCH = model_spec(
    "fs/unwatch",
    FsUnwatchParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fs/unwatch"],
)

FUZZY_FILE_SEARCH = model_spec(
    "fuzzyFileSearch",
    FuzzyFileSearchParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fuzzyFileSearch"],
)
FUZZY_FILE_SEARCH_SESSION_START = model_spec(
    "fuzzyFileSearch/sessionStart",
    FuzzySessionStartParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fuzzyFileSearch/sessionStart"],
)
FUZZY_FILE_SEARCH_SESSION_UPDATE = model_spec(
    "fuzzyFileSearch/sessionUpdate",
    FuzzySessionUpdateParams,
    scope=MethodScope.FILESYSTEM,
    emits=["fuzzyFileSearch/sessionUpdated", "fuzzyFileSearch/sessionCompleted"],
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fuzzyFileSearch/sessionUpdate"],
    event_models={
        "fuzzyFileSearch/sessionUpdated": FILESYSTEM_EVENT_MODELS["fuzzyFileSearch/sessionUpdated"],
        "fuzzyFileSearch/sessionCompleted": FILESYSTEM_EVENT_MODELS["fuzzyFileSearch/sessionCompleted"],
    },
)
FUZZY_FILE_SEARCH_SESSION_STOP = model_spec(
    "fuzzyFileSearch/sessionStop",
    FuzzySessionStopParams,
    scope=MethodScope.FILESYSTEM,
    result_model=FILESYSTEM_METHOD_RESULT_MODELS["fuzzyFileSearch/sessionStop"],
)

REPLAY_TURNS = spec("replay.turns", scope=MethodScope.DEBUG, required=["threadId"])
REPLAY_TIMELINE = spec("replay.timeline", scope=MethodScope.DEBUG, required=["threadId", "turnId"])
REPLAY_MESSAGES = spec("replay.messages", scope=MethodScope.DEBUG, required=["threadId"])
