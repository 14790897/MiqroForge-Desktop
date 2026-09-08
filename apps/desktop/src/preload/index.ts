import type { z } from 'zod';
import { contextBridge, ipcRenderer } from 'electron';
import { IPC, IPC_EVENTS, FeedbackSubmitInput } from '../shared/ipc';
import type {
  RuntimeStatus,
  SessionInfo,
  SessionDetail,
  SessionClaimLegacyResult,
  ProvidersListResult,
  ProviderUpdateResult,
  ModelsListResult,
  ChannelsConfig,
  PendingApproval,
  ApprovalCleared,
  ApprovalsListResult,
  ApprovalsAddPermanentResult,
  ApprovalsHistoryResult,
  UserInputCardRequest,
  UserInputResolvedData,
  UserInputResolveResult,
  CronJob,
  CronListResult,
  CronCreateResult,
  CronUpdateResult,
  CronRunEntry,
  CronRunsResult,
  MemoryFileInfo,
  MemoryListResult,
  MemoryGetResult,
  MemoryLessonEntry,
  MemoryLessonsResult,
  MemoryLessonUnlearnResult,
  ExperienceEntry,
  SkillSummary,
  WslExportDistroResult,
  WslImportDistroResult,
  WslStatsResult,
  WslInstallProgress,
  WslInstallAndProvisionResult,
  SkillsListResult,
  SkillDetail,
  McpServerConfig,
  McpServerInfo,
  FileNode,
  FilesTreeResult,
  FilesReadResult,
  FilesWriteResult,
  FilesDiffResult,
  FilesRevertResult,
  FilesOpenExternalResult,
  FilesOpenContainingFolderResult,
  FilesSaveAsResult,
  HtmlOpenInBrowserResult,
  DocumentsParseResult,
  TrackedFileInfo,
  ChatProgress,
  ChatFinal,
  ChatError,
  ChatAborted,
  ChatSubagentResult,
  PythonCheckResult,
  WslCheckResult,
  LiveAgentInfo,
  AgentSpawnedEvent,
  AgentCompletedEvent,
  Plan,
  PlanUpdatedEvent,
  ThreadStartResult,
  ThreadListResult,
  ThreadReadResult,
  ThreadStartedEvent,
  TurnStartResult,
  TurnInterruptResult,
  SandboxSetEnabledResult,
  FeedbackEntry,
  FeedbackListResult,
  FeedbackSubmitResult,
  QraftLoginResult,
  QraftPointsBalance,
  QraftBillingHistoryEntry,
  QraftErrorCode,
  QraftStatus,
  ConfigUpdatedPayload,
} from '../shared/ipc';

type FeedbackSubmitInputType = z.infer<typeof FeedbackSubmitInput>;

// ---------------------------------------------------------------------------
// Typed API exposed to the renderer via contextBridge
// ---------------------------------------------------------------------------

const api = {
  // -- Environment ------------------------------------------------------------
  // E2E 标记：main 在 MIQI_E2E=1 时通过 additionalArguments 下发 --miqi-e2e，
  // sandbox preload 的 process polyfill 提供 argv（#837 隐私协议确认门绕过）。
  env: {
    isE2E:
      typeof process !== 'undefined' &&
      Array.isArray(process.argv) &&
      process.argv.includes('--miqi-e2e'),
  },
  // -- App lifecycle -----------------------------------------------------------
  // 隐私协议拒绝退出 (#837)：走主进程 app.quit()（macOS 上 window.close 不退出）。
  app: {
    quit: (): Promise<{ ok: boolean }> => ipcRenderer.invoke(IPC.APP_QUIT),
    focus: (opts?: { hard?: boolean }): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.APP_FOCUS, opts),
    // 资产面板推开聊天区时加宽窗口(extra≈面板宽),聊天列 flex-1 分到新增宽度
    // 而保持原宽; 关闭(extra=0)还原。主进程记录实际加宽量,最大化/满屏时跳过。
    setPanelWindowExtra: (
      extra: number
    ): Promise<{ ok: boolean; applied: number; skipped?: boolean }> =>
      ipcRenderer.invoke(IPC.APP_PANEL_EXTRA, extra),
  },
  // -- Runtime ----------------------------------------------------------------
  runtime: {
    start: (): Promise<RuntimeStatus> => ipcRenderer.invoke(IPC.RUNTIME_START),
    stop: (): Promise<RuntimeStatus> => ipcRenderer.invoke(IPC.RUNTIME_STOP),
    status: (): Promise<RuntimeStatus> => ipcRenderer.invoke(IPC.RUNTIME_STATUS),
    logs: (): Promise<string[]> => ipcRenderer.invoke(IPC.RUNTIME_LOGS),
    fileLogs: (): Promise<string[]> => ipcRenderer.invoke(IPC.RUNTIME_FILE_LOGS),
    backendLogs: (): Promise<string[]> => ipcRenderer.invoke(IPC.RUNTIME_BACKEND_LOGS),
    onStateChange: (callback: (status: RuntimeStatus) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, status: RuntimeStatus) =>
        callback(status);
      ipcRenderer.on(IPC_EVENTS.RUNTIME_STATE, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.RUNTIME_STATE, handler);
    },
    onLog: (callback: (message: string) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, message: string) => callback(message);
      ipcRenderer.on(IPC_EVENTS.RUNTIME_LOG, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.RUNTIME_LOG, handler);
    },
    reportRendererLog: (entry: {
      level: string;
      message: string;
      source?: string;
      sessionKey?: string;
    }) => {
      ipcRenderer.send('runtime:renderer-log', entry);
    },
  },

  // -- Chat -------------------------------------------------------------------
  chat: {
    send: (
      content: string,
      sessionKey?: string,
      threadId?: string,
      mode?: string,
      attachments?: Array<{ name: string; data_base64?: string; mime_type?: string }>,
      workspace?: string,
      reasoningMode?: string,
      resumeTurnId?: string
    ): Promise<unknown> =>
      ipcRenderer.invoke(IPC.CHAT_SEND, {
        content,
        session_key: sessionKey,
        thread_id: threadId,
        mode,
        attachments,
        workspace,
        reasoning_mode: reasoningMode,
        resume_turn_id: resumeTurnId,
      }),
    abort: (sessionKey?: string, threadId?: string): Promise<unknown> =>
      ipcRenderer.invoke(IPC.CHAT_ABORT, { session_key: sessionKey, thread_id: threadId }),
    discardResume: (resumeTurnId: string, sessionKey?: string): Promise<unknown> =>
      ipcRenderer.invoke(IPC.CHAT_DISCARD_RESUME, {
        resume_turn_id: resumeTurnId,
        session_key: sessionKey,
      }),
    onProgress: (callback: (data: ChatProgress) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ChatProgress) => callback(data);
      ipcRenderer.on(IPC_EVENTS.CHAT_PROGRESS, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.CHAT_PROGRESS, handler);
    },
    onFinal: (callback: (data: ChatFinal) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ChatFinal) => callback(data);
      ipcRenderer.on(IPC_EVENTS.CHAT_FINAL, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.CHAT_FINAL, handler);
    },
    onError: (callback: (data: ChatError) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ChatError) => callback(data);
      ipcRenderer.on(IPC_EVENTS.CHAT_ERROR, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.CHAT_ERROR, handler);
    },
    onAborted: (callback: (data: ChatAborted) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ChatAborted) => callback(data);
      ipcRenderer.on(IPC_EVENTS.CHAT_ABORTED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.CHAT_ABORTED, handler);
    },
    onSubagentResult: (callback: (data: ChatSubagentResult) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ChatSubagentResult) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.CHAT_SUBAGENT_RESULT, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.CHAT_SUBAGENT_RESULT, handler);
    },
  },

  // -- Sessions ---------------------------------------------------------------
  sessions: {
    list: (): Promise<{ sessions: SessionInfo[] }> => ipcRenderer.invoke(IPC.SESSIONS_LIST),
    get: (sessionKey: string, extra?: Record<string, unknown>): Promise<SessionDetail> =>
      ipcRenderer.invoke(IPC.SESSIONS_GET, { session_key: sessionKey, ...(extra ?? {}) }),
    delete: (sessionKey: string): Promise<{ deleted: boolean }> =>
      ipcRenderer.invoke(IPC.SESSIONS_DELETE, { session_key: sessionKey }),
    archive: (sessionKey: string): Promise<{ archived: boolean }> =>
      ipcRenderer.invoke(IPC.SESSIONS_ARCHIVE, { session_key: sessionKey }),
    unarchive: (sessionKey: string): Promise<{ unarchived: boolean }> =>
      ipcRenderer.invoke(IPC.SESSIONS_UNARCHIVE, { session_key: sessionKey }),
    listArchived: (): Promise<{ sessions: SessionInfo[] }> =>
      ipcRenderer.invoke(IPC.SESSIONS_LIST_ARCHIVED),
    getTrackedFiles: (sessionKey: string): Promise<{ tracked_files: TrackedFileInfo[] }> =>
      ipcRenderer.invoke(IPC.SESSIONS_GET_TRACKED_FILES, { session_key: sessionKey }),
    clearTrackedFiles: (sessionKey: string): Promise<{ cleared: boolean }> =>
      ipcRenderer.invoke(IPC.SESSIONS_CLEAR_TRACKED_FILES, { session_key: sessionKey }),
    claimLegacy: (sessionKey: string): Promise<SessionClaimLegacyResult> =>
      ipcRenderer.invoke(IPC.SESSIONS_CLAIM_LEGACY, { session_key: sessionKey }),
    rename: (sessionKey: string, title: string): Promise<{ renamed: boolean; title: string }> =>
      ipcRenderer.invoke(IPC.SESSIONS_RENAME, { session_key: sessionKey, title }),
    listRecentWorkspaces: (): Promise<{ workspaces: string[] }> =>
      ipcRenderer.invoke(IPC.SESSIONS_LIST_RECENT_WORKSPACES),
  },

  // -- Config -----------------------------------------------------------------
  config: {
    get: (): Promise<Record<string, unknown>> => ipcRenderer.invoke(IPC.CONFIG_GET),
    update: (config: Record<string, unknown>): Promise<unknown> =>
      ipcRenderer.invoke(IPC.CONFIG_UPDATE, { config }),
    // Issue #789: hot-reload broadcast after config.save. Payload:
    // { applied, newSessionsOnly, restartRequired, restartReasons }.
    onUpdated: (callback: (payload: ConfigUpdatedPayload) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, payload: ConfigUpdatedPayload) =>
        callback(payload);
      ipcRenderer.on(IPC_EVENTS.CONFIG_UPDATED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.CONFIG_UPDATED, handler);
    },
  },

  // -- Providers --------------------------------------------------------------
  providers: {
    list: (): Promise<ProvidersListResult> => ipcRenderer.invoke(IPC.PROVIDERS_LIST),
    test: (
      providerName: string,
      apiKey?: string,
      apiBase?: string,
      model?: string
    ): Promise<{ ok: boolean; model?: string }> =>
      ipcRenderer.invoke(IPC.PROVIDERS_TEST, {
        provider_name: providerName,
        api_key: apiKey,
        api_base: apiBase ?? null,
        model,
      }),
    update: (
      providerName: string,
      apiKey?: string,
      apiBase?: string | null,
      extraHeaders?: Record<string, string> | null,
      model?: string
    ): Promise<ProviderUpdateResult> =>
      ipcRenderer.invoke(IPC.PROVIDERS_UPDATE, {
        provider_name: providerName,
        api_key: apiKey,
        api_base: apiBase ?? null,
        extra_headers: extraHeaders ?? null,
        model: model ?? undefined,
      }),
    activate: (
      providerName: string,
      activationCode: string
    ): Promise<{ activated: boolean; provider_name: string; error?: string }> =>
      ipcRenderer.invoke(IPC.PROVIDERS_ACTIVATE, {
        provider_name: providerName,
        activation_code: activationCode,
      }),
    deactivate: (providerName: string): Promise<{ deactivated: boolean; provider_name: string }> =>
      ipcRenderer.invoke(IPC.PROVIDERS_DEACTIVATE, {
        provider_name: providerName,
      }),
  },

  // -- Models (model/list catalog — issue #788 常用模型预设) ----------------
  models: {
    list: (): Promise<ModelsListResult> => ipcRenderer.invoke(IPC.MODEL_LIST),
  },

  // -- Channels ---------------------------------------------------------------
  channels: {
    list: (): Promise<{ channels: ChannelsConfig }> => ipcRenderer.invoke(IPC.CHANNELS_LIST),
    update: (channels: Partial<Record<string, unknown>>): Promise<{ saved: boolean }> =>
      ipcRenderer.invoke(IPC.CHANNELS_UPDATE, { channels }),
  },

  // -- Approvals --------------------------------------------------------------
  approvals: {
    list: (): Promise<ApprovalsListResult> => ipcRenderer.invoke(IPC.APPROVALS_LIST),
    resolve: (approvalId: string, decision: string): Promise<{ resolved: boolean }> =>
      ipcRenderer.invoke(IPC.APPROVALS_RESOLVE, { approval_id: approvalId, decision }),
    clearPermanent: (pattern?: string): Promise<{ cleared: boolean }> =>
      ipcRenderer.invoke(IPC.APPROVALS_CLEAR_PERMANENT, pattern ? { pattern } : {}),
    addPermanent: (pattern: string): Promise<ApprovalsAddPermanentResult> =>
      ipcRenderer.invoke(IPC.APPROVALS_ADD_PERMANENT, { pattern }),
    history: (limit?: number): Promise<ApprovalsHistoryResult> =>
      ipcRenderer.invoke(IPC.APPROVALS_HISTORY, limit ? { limit } : {}),
    onRequest: (callback: (data: PendingApproval) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: PendingApproval) => callback(data);
      ipcRenderer.on(IPC_EVENTS.APPROVAL_REQUEST, handler);
      return () => {
        ipcRenderer.removeListener(IPC_EVENTS.APPROVAL_REQUEST, handler);
      };
    },
    onCleared: (callback: (data: ApprovalCleared) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ApprovalCleared) => callback(data);
      ipcRenderer.on(IPC_EVENTS.APPROVAL_CLEARED, handler);
      return () => {
        ipcRenderer.removeListener(IPC_EVENTS.APPROVAL_CLEARED, handler);
      };
    },
  },

  // -- User input (issue #646: ask_user_confirm_card) --------------------------
  userInput: {
    resolve: (
      inputId: string,
      choiceId: string,
      choiceLabel: string,
      remember?: boolean
    ): Promise<UserInputResolveResult> =>
      ipcRenderer.invoke(IPC.USER_INPUT_RESOLVE, {
        input_id: inputId,
        choice_id: choiceId,
        choice_label: choiceLabel,
        remember: remember === true,
      }),
    onRequest: (callback: (data: UserInputCardRequest) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: UserInputCardRequest) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.USER_INPUT_REQUEST, handler);
      return () => {
        ipcRenderer.removeListener(IPC_EVENTS.USER_INPUT_REQUEST, handler);
      };
    },
    onResolved: (callback: (data: UserInputResolvedData) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: UserInputResolvedData) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.USER_INPUT_RESOLVED, handler);
      return () => {
        ipcRenderer.removeListener(IPC_EVENTS.USER_INPUT_RESOLVED, handler);
      };
    },
  },

  // -- Cron --------------------------------------------------------------------
  cron: {
    list: (): Promise<CronListResult> => ipcRenderer.invoke(IPC.CRON_LIST),
    create: (payload: Record<string, unknown>): Promise<CronCreateResult> =>
      ipcRenderer.invoke(IPC.CRON_CREATE, payload),
    update: (payload: Record<string, unknown>): Promise<CronUpdateResult> =>
      ipcRenderer.invoke(IPC.CRON_UPDATE, payload),
    delete: (jobId: string): Promise<{ deleted: boolean }> =>
      ipcRenderer.invoke(IPC.CRON_DELETE, { jobId }),
    toggle: (jobId: string, enabled: boolean): Promise<CronUpdateResult> =>
      ipcRenderer.invoke(IPC.CRON_TOGGLE, { jobId, enabled }),
    run: (jobId: string): Promise<CronUpdateResult> => ipcRenderer.invoke(IPC.CRON_RUN, { jobId }),
    runs: (jobId?: string): Promise<CronRunsResult> =>
      ipcRenderer.invoke(IPC.CRON_RUNS, jobId ? { jobId } : {}),
  },

  // -- Memory ------------------------------------------------------------------
  memory: {
    list: (): Promise<MemoryListResult> => ipcRenderer.invoke(IPC.MEMORY_LIST),
    get: (path: string): Promise<MemoryGetResult> => ipcRenderer.invoke(IPC.MEMORY_GET, { path }),
    update: (path: string, content: string): Promise<{ saved: boolean; path: string }> =>
      ipcRenderer.invoke(IPC.MEMORY_UPDATE, { path, content }),
    delete: (path: string): Promise<{ deleted: boolean; path: string }> =>
      ipcRenderer.invoke(IPC.MEMORY_DELETE, { path }),
    lessons: (): Promise<MemoryLessonsResult> => ipcRenderer.invoke(IPC.MEMORY_LESSONS),
    lessonUnlearn: (lesson_id: string): Promise<MemoryLessonUnlearnResult> =>
      ipcRenderer.invoke(IPC.MEMORY_LESSON_UNLEARN, { lesson_id }),
  },

  // -- Experience ---------------------------------------------------------------
  experience: {
    list: (params?: {
      type?: string;
      scope?: string;
      session_key?: string;
      limit?: number;
    }): Promise<{ entries: ExperienceEntry[] }> =>
      ipcRenderer.invoke(IPC.EXPERIENCE_LIST, params ?? {}),
    delete: (type: string, id: string): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.EXPERIENCE_DELETE, { type, id }),
    toggle: (type: string, id: string, enabled: boolean): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.EXPERIENCE_TOGGLE, { type, id, enabled }),
    search: (
      query: string,
      type?: string,
      limit?: number
    ): Promise<{ entries: ExperienceEntry[] }> =>
      ipcRenderer.invoke(IPC.EXPERIENCE_SEARCH, { query, type, limit }),
  },

  // -- Skills ------------------------------------------------------------------
  skills: {
    list: (): Promise<SkillsListResult> => ipcRenderer.invoke(IPC.SKILLS_LIST),
    get: (name: string): Promise<SkillDetail> => ipcRenderer.invoke(IPC.SKILLS_GET, { name }),
    openFolder: (name: string): Promise<{ opened: boolean; path: string }> =>
      ipcRenderer.invoke(IPC.SKILLS_OPEN_FOLDER, { name }),
    create: (
      name: string,
      description: string
    ): Promise<{ ok: boolean; error?: string; path?: string }> =>
      ipcRenderer.invoke(IPC.SKILLS_CREATE, { name, description }),
    upload: (name: string, content: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.SKILLS_UPLOAD, { name, content }),
    delete: (name: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.SKILLS_DELETE, { name }),
  },

  // -- MCP --------------------------------------------------------------------
  mcps: {
    list: (): Promise<{ servers: McpServerInfo[] }> => ipcRenderer.invoke(IPC.MCP_LIST),
    upsert: (name: string, config: McpServerConfig): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.MCP_UPSERT, { name, ...config }),
    delete: (name: string): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.MCP_DELETE, { name }),
  },

  // -- Files (Workspace Editor) ------------------------------------------------
  files: {
    tree: (): Promise<FilesTreeResult> => ipcRenderer.invoke(IPC.FILES_TREE),
    read: (
      path: string,
      sessionKey?: string,
      options?: { asBinary?: boolean }
    ): Promise<FilesReadResult> =>
      ipcRenderer.invoke(IPC.FILES_READ, {
        path,
        session_key: sessionKey,
        as_binary: options?.asBinary ?? false,
      }),
    write: (
      path: string,
      content: string,
      sessionKey?: string,
      dataBase64?: string
    ): Promise<FilesWriteResult> =>
      ipcRenderer.invoke(IPC.FILES_WRITE, {
        path,
        content,
        session_key: sessionKey,
        data_base64: dataBase64,
      }),
    delete: (path: string): Promise<{ deleted: boolean; path: string }> =>
      ipcRenderer.invoke(IPC.FILES_DELETE, { path }),
    diff: (path: string, sessionKey?: string): Promise<FilesDiffResult> =>
      ipcRenderer.invoke(IPC.FILES_DIFF, { path, session_key: sessionKey }),
    revert: (path: string, sessionKey?: string): Promise<FilesRevertResult> =>
      ipcRenderer.invoke(IPC.FILES_REVERT, { path, session_key: sessionKey }),
    accept: (path: string, sessionKey?: string): Promise<{ accepted: boolean; path: string }> =>
      ipcRenderer.invoke(IPC.FILES_ACCEPT, { path, session_key: sessionKey }),
    openExternal: (path: string): Promise<FilesOpenExternalResult> =>
      ipcRenderer.invoke(IPC.FILES_OPEN_EXTERNAL, { path }),
    openContainingFolder: (path: string): Promise<FilesOpenContainingFolderResult> =>
      ipcRenderer.invoke(IPC.FILES_OPEN_CONTAINING_FOLDER, { path }),
    /** #877: native save dialog for the preview「下载/另存为」button. */
    saveAs: (defaultName: string, dataBase64: string): Promise<FilesSaveAsResult> =>
      ipcRenderer.invoke(IPC.FILES_SAVE_AS, {
        default_name: defaultName,
        data_base64: dataBase64,
      }),
    /** #740: open AI-generated HTML in the system browser (temp file + auto-cleanup). */
    openInBrowser: (html: string): Promise<{ opened: boolean; path: string; error?: string }> =>
      ipcRenderer.invoke(IPC.HTML_OPEN_IN_BROWSER, { html }),
  },

  // -- HTML preview (issue #751): open an HTML string in the system browser --
  html: {
    openInBrowser: (html: string): Promise<HtmlOpenInBrowserResult> =>
      ipcRenderer.invoke(IPC.HTML_OPEN_IN_BROWSER, { html }),
  },

  // -- Direct downloads (issue #667: paper PDF etc.) -----------------------
  downloads: {
    download: (
      url: string,
      filename?: string
    ): Promise<{ ok: boolean; error?: string; savePath?: string }> =>
      ipcRenderer.invoke(IPC.DOWNLOADS_DOWNLOAD, { url, filename }),
  },

  // -- Web helpers ------------------------------------------------------------
  // checkUrl restored from pre-#577 (issue #677): 来源弹窗的 URL 检查桥
  web: {
    checkUrl: (url: string): Promise<{ ok: boolean; status: number }> =>
      ipcRenderer.invoke(IPC.WEB_CHECK_URL, { url }),
  },

  // -- Clipboard ------------------------------------------------------------
  // navigator.clipboard fails under file:// (non-secure context) in packaged
  // builds, and electron's clipboard module is unavailable in the sandboxed
  // preload — route the write through the main process instead.
  clipboard: {
    writeText: (text: string): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.CLIPBOARD_WRITE_TEXT, { text }),
  },

  // -- Document parsing ----------------------------------------------------
  documents: {
    parse: (
      path: string,
      sessionKey?: string,
      options?: {
        forceOcr?: boolean;
        preview?: boolean;
        /** #877: return structured render data (sheets/blocks) for rich preview. */
        structured?: boolean;
        /** #877: in-memory file bytes — used by attachment chip previews. */
        dataBase64?: string;
      }
    ): Promise<DocumentsParseResult> =>
      ipcRenderer.invoke(IPC.DOCUMENTS_PARSE, {
        path,
        session_key: sessionKey,
        force_ocr: options?.forceOcr ?? false,
        preview: options?.preview ?? false,
        structured: options?.structured ?? false,
        data_base64: options?.dataBase64,
      }),
  },

  // -- Python check -----------------------------------------------------------
  python: {
    check: (): Promise<PythonCheckResult> => ipcRenderer.invoke(IPC.PYTHON_CHECK),
  },

  // -- WSL2 check & install (Windows only) ------------------------------------
  wsl: {
    check: (): Promise<WslCheckResult> => ipcRenderer.invoke(IPC.WSL_CHECK),
    install: (): Promise<{ launched: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.WSL_INSTALL),
    installAndProvision: (): Promise<WslInstallAndProvisionResult> =>
      ipcRenderer.invoke(IPC.WSL_INSTALL_AND_PROVISION),
    onInstallProgress: (callback: (data: WslInstallProgress) => void): (() => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: WslInstallProgress) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.WSL_INSTALL_PROGRESS, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.WSL_INSTALL_PROGRESS, handler);
    },
    onCheckUpdated: (callback: () => void): (() => void) => {
      const handler = () => callback();
      ipcRenderer.on(IPC_EVENTS.WSL_CHECK_UPDATED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.WSL_CHECK_UPDATED, handler);
    },
    exportDistro: (distroName: string): Promise<WslExportDistroResult> =>
      ipcRenderer.invoke(IPC.WSL_EXPORT_DISTRO, distroName),
    importDistro: (options: {
      tarPath: string;
      distroName: string;
    }): Promise<WslImportDistroResult> => ipcRenderer.invoke(IPC.WSL_IMPORT_DISTRO, options),
    getStats: (distroName?: string): Promise<WslStatsResult> =>
      ipcRenderer.invoke(IPC.WSL_GET_STATS, distroName ?? undefined),
  },

  // -- Sandbox runtime toggle -----------------------------------------------
  sandbox: {
    setEnabled: (enabled: boolean): Promise<SandboxSetEnabledResult> =>
      ipcRenderer.invoke(IPC.SANDBOX_SET_ENABLED, enabled),
    // #854: allow_system_installs runtime toggle (no restart)
    setAllowSystemInstalls: (enabled: boolean): Promise<{ allowSystemInstalls: boolean }> =>
      ipcRenderer.invoke(IPC.SANDBOX_SET_ALLOW_SYSTEM_INSTALLS, enabled),
  },

  // -- Initial config write (no bridge needed) --------------------------------
  setup: {
    writeInitialConfig: (
      config: Record<string, unknown>
    ): Promise<{ saved: boolean; path: string }> =>
      ipcRenderer.invoke(IPC.CONFIG_WRITE_INITIAL, config),
  },

  // -- Dialog -----------------------------------------------------------------
  dialog: {
    openFile: (): Promise<string | null> => ipcRenderer.invoke(IPC.DIALOG_OPEN_FILE),
    openDirectory: (): Promise<string | null> => ipcRenderer.invoke(IPC.DIALOG_OPEN_DIRECTORY),
  },

  // -- Agents (Phase 1) --------------------------------------------------------
  agents: {
    list: (sessionKey?: string): Promise<{ agents: LiveAgentInfo[] }> =>
      ipcRenderer.invoke(IPC.AGENT_LIST, { session_key: sessionKey }),
    spawn: (agentType: string, task: string, label?: string): Promise<{ agent: LiveAgentInfo }> =>
      ipcRenderer.invoke(IPC.AGENT_SPAWN, { agent_type: agentType, task, label }),
    kill: (agentId: string): Promise<{ killed: boolean }> =>
      ipcRenderer.invoke(IPC.AGENT_KILL, { agent_id: agentId }),
    onSpawned: (callback: (data: AgentSpawnedEvent) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: AgentSpawnedEvent) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.AGENT_SPAWNED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.AGENT_SPAWNED, handler);
    },
    onCompleted: (callback: (data: AgentCompletedEvent) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: AgentCompletedEvent) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.AGENT_COMPLETED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.AGENT_COMPLETED, handler);
    },
  },

  // -- Plan (Phase 2) ----------------------------------------------------------
  plan: {
    get: (threadId: string): Promise<{ plan: Plan | null }> =>
      ipcRenderer.invoke(IPC.PLAN_GET, { thread_id: threadId }),
    onUpdated: (callback: (data: PlanUpdatedEvent) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: PlanUpdatedEvent) => callback(data);
      ipcRenderer.on(IPC_EVENTS.PLAN_UPDATED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.PLAN_UPDATED, handler);
    },
  },

  // -- Permissions (Phase 1) ---------------------------------------------------
  permissions: {
    get: (): Promise<Record<string, unknown>> => ipcRenderer.invoke(IPC.PERMISSIONS_GET),
    update: (config: Record<string, unknown>): Promise<{ saved: boolean }> =>
      ipcRenderer.invoke(IPC.PERMISSIONS_UPDATE, { config }),
    addPermanent: (pattern: string): Promise<{ added: boolean }> =>
      ipcRenderer.invoke(IPC.PERMISSIONS_PERMANENT_ADD, { pattern }),
    removePermanent: (pattern: string): Promise<{ removed: boolean }> =>
      ipcRenderer.invoke(IPC.PERMISSIONS_PERMANENT_REMOVE, { pattern }),
  },

  // -- Plugins (Phase 4) -------------------------------------------------------
  plugins: {
    list: (): Promise<{ plugins: Record<string, unknown>[] }> =>
      ipcRenderer.invoke(IPC.PLUGINS_LIST),
    install: (name: string): Promise<{ ok: boolean; error?: string }> =>
      ipcRenderer.invoke(IPC.PLUGINS_INSTALL, { name }),
    uninstall: (name: string): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.PLUGINS_UNINSTALL, { name }),
    toggle: (name: string, enabled: boolean): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke(IPC.PLUGINS_TOGGLE, { name, enabled }),
  },

  // -- Threads (Phase 36+) -----------------------------------------------------
  threads: {
    start: (params: {
      title?: string;
      session_key?: string;
      thread_id?: string;
    }): Promise<ThreadStartResult> => ipcRenderer.invoke(IPC.THREAD_START, params),
    list: (params?: { session_key?: string }): Promise<ThreadListResult> =>
      ipcRenderer.invoke(IPC.THREAD_LIST, params ?? {}),
    read: (threadId: string, sessionKey?: string): Promise<ThreadReadResult> =>
      ipcRenderer.invoke(IPC.THREAD_READ, { thread_id: threadId, session_key: sessionKey }),
    nameSet: (
      threadId: string,
      name: string,
      sessionKey?: string
    ): Promise<{ thread: Record<string, unknown> }> =>
      ipcRenderer.invoke(IPC.THREAD_NAME_SET, {
        thread_id: threadId,
        name,
        session_key: sessionKey,
      }),
    onStarted: (callback: (data: ThreadStartedEvent) => void) => {
      const handler = (_event: Electron.IpcRendererEvent, data: ThreadStartedEvent) =>
        callback(data);
      ipcRenderer.on(IPC_EVENTS.THREAD_STARTED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.THREAD_STARTED, handler);
    },
  },

  // -- Turns (Phase 37+) --------------------------------------------------------
  turns: {
    start: (params: {
      thread_id: string;
      content: string;
      session_key?: string;
      model?: string;
      effort?: string;
    }): Promise<TurnStartResult> => ipcRenderer.invoke(IPC.TURN_START, params),
    interrupt: (
      threadId: string,
      turnId: string,
      sessionKey?: string
    ): Promise<TurnInterruptResult> =>
      ipcRenderer.invoke(IPC.TURN_INTERRUPT, {
        thread_id: threadId,
        turn_id: turnId,
        session_key: sessionKey,
      }),
  },

  // -- Feedback ---------------------------------------------------------------
  feedback: {
    submit: (params: FeedbackSubmitInputType): Promise<FeedbackSubmitResult> =>
      ipcRenderer.invoke(IPC.FEEDBACK_SUBMIT, params),
    list: (params?: { limit?: number }): Promise<FeedbackListResult> =>
      ipcRenderer.invoke(IPC.FEEDBACK_LIST, params ?? {}),
  },

  // -- MiQroForge 平台 OAuth2 登录 (issue #726) ------------------------------------
  qraft: {
    login: (
      phone: string,
      password: string,
      opts?: {
        env?: 'test' | 'prod';
        baseUrl?: string;
        clientId?: string;
        clientSecret?: string;
        redirectUri?: string;
      }
    ): Promise<QraftLoginResult> =>
      ipcRenderer.invoke(IPC.QRAFT_LOGIN, { phone, password, ...(opts ?? {}) }),
    browserLogin: (opts?: {
      env?: 'test' | 'prod';
      baseUrl?: string;
      clientId?: string;
      clientSecret?: string;
      redirectUri?: string;
    }): Promise<QraftLoginResult> => ipcRenderer.invoke(IPC.QRAFT_BROWSER_LOGIN, opts ?? {}),
    status: (): Promise<QraftStatus> => ipcRenderer.invoke(IPC.QRAFT_STATUS),
    refresh: (): Promise<QraftLoginResult> => ipcRenderer.invoke(IPC.QRAFT_REFRESH),
    logout: (): Promise<{ ok: boolean }> => ipcRenderer.invoke(IPC.QRAFT_LOGOUT),
    pointsBalance: (): Promise<
      | { ok: true; points: QraftPointsBalance }
      | { ok: false; code: QraftErrorCode; message: string }
    > => ipcRenderer.invoke(IPC.QRAFT_POINTS_BALANCE),
    billingHistory: (): Promise<QraftBillingHistoryEntry[]> =>
      ipcRenderer.invoke(IPC.QRAFT_BILLING_HISTORY),
    onStatusChanged: (callback: (status: QraftStatus) => void): (() => void) => {
      const handler = (_event: Electron.IpcRendererEvent, status: QraftStatus) => callback(status);
      ipcRenderer.on(IPC_EVENTS.QRAFT_STATUS_CHANGED, handler);
      return () => ipcRenderer.removeListener(IPC_EVENTS.QRAFT_STATUS_CHANGED, handler);
    },
  },
};

contextBridge.exposeInMainWorld('miqi', api);

// Type declaration for renderer
export type MiQiAPI = typeof api;
