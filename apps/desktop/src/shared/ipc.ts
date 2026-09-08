import { z } from 'zod';

// ---------------------------------------------------------------------------
// IPC channel names (invoke)
// ---------------------------------------------------------------------------

export const IPC = {
  // Runtime
  RUNTIME_START: 'runtime:start',
  RUNTIME_STOP: 'runtime:stop',
  RUNTIME_STATUS: 'runtime:status',
  RUNTIME_LOGS: 'runtime:logs',
  RUNTIME_FILE_LOGS: 'runtime:file-logs',
  RUNTIME_BACKEND_LOGS: 'runtime:backend-logs',

  // Chat
  CHAT_SEND: 'chat:send',
  CHAT_ABORT: 'chat:abort',
  CHAT_DISCARD_RESUME: 'chat:discard-resume',

  // Threads (Codex-style, Phase 36+)
  THREAD_START: 'thread:start',
  THREAD_LIST: 'thread:list',
  THREAD_READ: 'thread:read',
  THREAD_NAME_SET: 'thread:name:set',

  // Turns (Codex-style, Phase 37+)
  TURN_START: 'turn:start',
  TURN_INTERRUPT: 'turn:interrupt',

  // Sessions
  SESSIONS_LIST: 'sessions:list',
  SESSIONS_GET: 'sessions:get',
  SESSIONS_DELETE: 'sessions:delete',
  SESSIONS_ARCHIVE: 'sessions:archive',
  SESSIONS_UNARCHIVE: 'sessions:unarchive',
  SESSIONS_LIST_ARCHIVED: 'sessions:list_archived',
  SESSIONS_GET_TRACKED_FILES: 'sessions:get_tracked_files',
  SESSIONS_CLEAR_TRACKED_FILES: 'sessions:clear_tracked_files',
  SESSIONS_CLAIM_LEGACY: 'sessions:claim_legacy',
  SESSIONS_RENAME: 'sessions:rename',

  // Config
  CONFIG_GET: 'config:get',
  CONFIG_UPDATE: 'config:update',

  // Providers
  PROVIDERS_LIST: 'providers:list',
  PROVIDERS_TEST: 'providers:test',
  PROVIDERS_UPDATE: 'providers:update',
  PROVIDERS_ACTIVATE: 'providers:activate',
  PROVIDERS_DEACTIVATE: 'providers:deactivate',
  // Models (model/list catalog — issue #788 常用模型预设)
  MODEL_LIST: 'models:list',
  CHANNELS_LIST: 'channels:list',
  CHANNELS_UPDATE: 'channels:update',
  APPROVALS_LIST: 'approvals:list',
  APPROVALS_RESOLVE: 'approvals:resolve',
  APPROVALS_CLEAR_PERMANENT: 'approvals:clear_permanent',
  APPROVALS_ADD_PERMANENT: 'approvals:add_permanent',
  APPROVALS_HISTORY: 'approvals:history',
  // AI-initiated user confirmation (issue #646)
  USER_INPUT_RESOLVE: 'userInput:resolve',
  CRON_LIST: 'cron:list',
  CRON_CREATE: 'cron:create',
  CRON_UPDATE: 'cron:update',
  CRON_DELETE: 'cron:delete',
  CRON_TOGGLE: 'cron:toggle',
  CRON_RUN: 'cron:run',
  CRON_RUNS: 'cron:runs',
  MEMORY_LIST: 'memory:list',
  MEMORY_GET: 'memory:get',
  MEMORY_UPDATE: 'memory:update',
  MEMORY_DELETE: 'memory:delete',
  MEMORY_LESSONS: 'memory:lessons',
  MEMORY_LESSON_UNLEARN: 'memory:lesson:unlearn',

  // Experience store
  EXPERIENCE_LIST: 'experience:list',
  EXPERIENCE_DELETE: 'experience:delete',
  EXPERIENCE_TOGGLE: 'experience:toggle',
  EXPERIENCE_SEARCH: 'experience:search',
  SKILLS_LIST: 'skills:list',
  SKILLS_GET: 'skills:get',
  SKILLS_OPEN_FOLDER: 'skills:open_folder',
  SKILLS_CREATE: 'skills:create',
  SKILLS_UPLOAD: 'skills:upload',
  SKILLS_DELETE: 'skills:delete',

  // MCP
  MCP_LIST: 'mcp:list',
  MCP_UPSERT: 'mcp:upsert',
  MCP_DELETE: 'mcp:delete',
  FILES_TREE: 'files:tree',
  FILES_READ: 'files:read',
  FILES_WRITE: 'files:write',
  FILES_DELETE: 'files:delete',
  FILES_DIFF: 'files:diff',
  FILES_REVERT: 'files:revert',
  FILES_ACCEPT: 'files:accept',
  FILES_OPEN_EXTERNAL: 'files:openExternal',
  FILES_OPEN_CONTAINING_FOLDER: 'files:openContainingFolder',
  FILES_SAVE_AS: 'files:saveAs', // #877: 预览弹窗「下载/另存为」
  HTML_OPEN_IN_BROWSER: 'html:openInBrowser',
  DOWNLOADS_DOWNLOAD: 'downloads:download', // #667: 直接下载（论文 PDF 等）
  DOCUMENTS_PARSE: 'documents:parse',

  // Web URL HEAD-check (查看来源 dead-link 过滤)
  WEB_CHECK_URL: 'web:checkUrl',

  // Clipboard write — sandboxed preload cannot import electron's clipboard
  // module (not in the sandbox allow-list), so it must go through main.
  CLIPBOARD_WRITE_TEXT: 'clipboard:writeText',

  // Python check
  PYTHON_CHECK: 'python:check',

  // WSL2 check & install (Windows only, no bridge needed)
  WSL_CHECK: 'wsl:check',
  WSL_INSTALL: 'wsl:install',
  WSL_INSTALL_AND_PROVISION: 'wsl:installAndProvision',
  WSL_EXPORT_DISTRO: 'wsl:export_distro',
  WSL_IMPORT_DISTRO: 'wsl:import_distro',
  WSL_GET_STATS: 'wsl:getStats',

  // Sandbox runtime toggle
  SANDBOX_SET_ENABLED: 'sandbox:setEnabled',

  // #854: allow_system_installs runtime toggle (no restart)
  SANDBOX_SET_ALLOW_SYSTEM_INSTALLS: 'sandbox:setAllowSystemInstalls',

  // Write initial config (no bridge needed �? used by Setup Wizard)
  CONFIG_WRITE_INITIAL: 'config:write_initial',

  // Dialog
  DIALOG_OPEN_FILE: 'dialog:openFile',
  DIALOG_OPEN_DIRECTORY: 'dialog:openDirectory',

  // Sessions metadata
  SESSIONS_LIST_RECENT_WORKSPACES: 'sessions:listRecentWorkspaces',

  // New: Multi-Agent (Phase 1)
  AGENT_LIST: 'agent:list',
  AGENT_KILL: 'agent:kill',
  AGENT_SPAWN: 'agent:spawn',

  // New: Plan tracking (Phase 2)
  PLAN_GET: 'plan:get',

  // New: Permissions (Phase 1)
  PERMISSIONS_GET: 'permissions:get',
  PERMISSIONS_UPDATE: 'permissions:update',
  PERMISSIONS_PERMANENT_ADD: 'permissions:permanent:add',
  PERMISSIONS_PERMANENT_REMOVE: 'permissions:permanent:remove',

  // New: Plugin management (Phase 4)
  PLUGINS_LIST: 'plugins:list',
  PLUGINS_INSTALL: 'plugins:install',
  PLUGINS_UNINSTALL: 'plugins:uninstall',
  PLUGINS_TOGGLE: 'plugins:toggle',
  FEEDBACK_SUBMIT: 'feedback:submit',
  FEEDBACK_LIST: 'feedback:list',

  // MiQroForge 平台 OAuth2 登录 (issue #726, 主进程本地处理)
  QRAFT_LOGIN: 'qraft:login',
  QRAFT_BROWSER_LOGIN: 'qraft:browserLogin',
  QRAFT_STATUS: 'qraft:status',
  QRAFT_REFRESH: 'qraft:refresh',
  QRAFT_LOGOUT: 'qraft:logout',
  QRAFT_POINTS_BALANCE: 'qraft:pointsBalance',
  QRAFT_BILLING_HISTORY: 'qraft:billingHistory',

  // App lifecycle
  APP_QUIT: 'app:quit',
  APP_FOCUS: 'app:focus',
} as const;

// ---------------------------------------------------------------------------
// IPC event channels (main �? renderer)
// ---------------------------------------------------------------------------

export const IPC_EVENTS = {
  RUNTIME_STATE: 'runtime:state',
  RUNTIME_LOG: 'runtime:log',
  CHAT_DELTA: 'chat:delta',
  CHAT_PROGRESS: 'chat:progress',
  CHAT_FINAL: 'chat:final',
  CHAT_ERROR: 'chat:error',
  CHAT_ABORTED: 'chat:aborted',
  CHAT_SUBAGENT_RESULT: 'chat:subagent_result',
  APPROVAL_REQUEST: 'approval:request',
  APPROVAL_CLEARED: 'approval:cleared',

  // AI-initiated user confirmation (issue #646) — ask_user_confirm_card
  USER_INPUT_REQUEST: 'userInput:request',
  USER_INPUT_RESOLVED: 'userInput:resolved',

  // Config hot-reload broadcast (issue #789) — emitted by the bridge after
  // config.save with tier A/B/C classification. Orphan events are mapped
  // as CHAT_<TYPE> by the main process.
  CONFIG_UPDATED: 'config:updated',
  CHAT_CONFIG_UPDATED: 'config:updated',

  // New events (Phase 1)
  AGENT_SPAWNED: 'agent:spawned',
  AGENT_COMPLETED: 'agent:completed',
  PLAN_UPDATED: 'plan:updated',
  TURN_STARTED: 'turn:started',
  TURN_COMPLETED: 'turn:completed',
  THREAD_STARTED: 'thread:started',

  // WSL install progress events
  WSL_INSTALL_PROGRESS: 'wsl:installProgress',
  WSL_CHECK_UPDATED: 'wsl:checkUpdated',

  // MiQroForge 登录态变化（自动刷新/过期时由主进程推送）
  QRAFT_STATUS_CHANGED: 'qraft:statusChanged',
} as const;

// ---------------------------------------------------------------------------
// Zod schemas for IPC payload validation
// ---------------------------------------------------------------------------

export const ChatSendInput = z.object({
  content: z.string().optional(),
  session_key: z.string().optional(),
  thread_id: z.string().optional(),
  mode: z.enum(['plan', 'manual', 'edit', 'auto']).optional(),
  workspace: z.string().optional(),
  resume_turn_id: z.string().optional(),
  attachments: z
    .array(
      z.object({
        name: z.string(),
        data_base64: z.string().optional(),
        mime_type: z.string().optional(),
      })
    )
    .optional(),
});

export const SessionGetInput = z.object({
  session_key: z.string().min(1),
  workspace: z.string().optional(),
});

export const SessionDeleteInput = z.object({
  session_key: z.string().min(1),
});

export const SessionClaimLegacyInput = z.object({
  session_key: z.string().min(1),
});

export const SessionRenameInput = z.object({
  session_key: z.string().min(1),
  title: z.string().min(1).max(100),
});

export interface SessionClaimLegacyResult {
  claimed: boolean;
  session_key: string;
  owner_client_id: string;
  error?: string;
}

export const ConfigUpdateInput = z.object({
  config: z.record(z.unknown()),
});

export const ProviderTestInput = z.object({
  provider_name: z.string().min(1),
  api_key: z.string().optional(),
  api_base: z.string().nullable().optional(),
  model: z.string().optional(),
});

export const ProviderUpdateInput = z.object({
  provider_name: z.string().min(1),
  api_key: z.string().optional(),
  api_base: z.string().nullable().optional(),
  extra_headers: z.record(z.string()).nullable().optional(),
  model: z.string().optional(),
});

export const ProviderActivateInput = z.object({
  provider_name: z.string().min(1),
  activation_code: z.string().min(1),
});

export const ProviderDeactivateInput = z.object({
  provider_name: z.string().min(1),
});

// New Phase 1 schemas
export const AgentSpawnInput = z.object({
  agent_type: z.string().min(1),
  task: z.string().min(1),
  label: z.string().optional(),
  session_key: z.string().optional(),
});

export const PermissionsUpdateInput = z.object({
  filesystem: z
    .object({
      rules: z.array(
        z.object({
          path: z.string(),
          mode: z.enum(['read', 'write', 'none']),
          recursive: z.boolean().optional(),
        })
      ),
      default_mode: z.enum(['read', 'write', 'none']).optional(),
    })
    .optional(),
  network: z.enum(['allow_all', 'block_all', 'allow_list']).optional(),
  exec_approval: z.enum(['never', 'dangerous', 'always']).optional(),
});

// ---------------------------------------------------------------------------
// Runtime state
// ---------------------------------------------------------------------------

export type RuntimeState = 'stopped' | 'starting' | 'running' | 'stopping' | 'error';

export interface RuntimeStatus {
  state: RuntimeState;
  configured: boolean;
  python_version?: string;
  sandbox_available?: boolean;
  error?: string;
}

// ---------------------------------------------------------------------------
// Session types
// ---------------------------------------------------------------------------

export interface SessionInfo {
  key: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
  path?: string;
  message_count?: number;
  workspace?: string;
}

export interface SessionDetail {
  key: string;
  messages: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
  workspace?: string;
}

// ---------------------------------------------------------------------------
// Provider types
// ---------------------------------------------------------------------------

export interface ProviderInfo {
  name: string;
  display_name: string;
  env_key: string;
  provider_type: string;
  is_gateway: boolean;
  is_local: boolean;
  default_api_base: string;
  configured: boolean;
  api_key_hint?: string | null;
  api_base: string | null;
  configured_model?: string;
  verification_status?: 'missing' | 'unverified' | 'success' | 'failed';
  verified_at?: string | null;
  verification_message?: string | null;
  builtin_available?: boolean;
  builtin_activated?: boolean;
}

export interface ProvidersListResult {
  providers: ProviderInfo[];
  active_model?: string;
  active_provider?: string | null;
}

export interface ProviderUpdateResult {
  saved: boolean;
  provider_name: string;
}

// ---------------------------------------------------------------------------
// Model catalog types (model/list — issue #788)
// ---------------------------------------------------------------------------

export interface ModelInfo {
  id: string; // "provider/model-name"
  name: string; // human display name
  provider: string;
  providerDisplayName: string;
  hidden: boolean;
  default: boolean;
  supportedReasoningEfforts?: string[];
  serviceTiers?: string[];
  defaultServiceTier?: string | null;
}

export interface ModelsListResult {
  models: ModelInfo[];
}

export interface ProviderActivateResult {
  activated: boolean;
  provider_name: string;
  error?: string;
}

export interface FeishuChannelConfig {
  enabled: boolean;
  app_id: string;
  app_secret: string;
  allow_from: string[];
  reply_delay_ms: number;
  require_mention_in_groups: boolean;
}

export interface ChannelsConfig {
  send_progress: boolean;
  send_tool_hints: boolean;
  send_queue_notifications: boolean;
  feishu: FeishuChannelConfig;
}

export const ChannelsUpdateInput = z.object({
  channels: z.record(z.unknown()),
});

export interface ApprovalRequest {
  approval_id: string;
  command?: string; // may be empty for non-exec approvals
  description: string;
  allow_permanent: boolean;
  category?: string; // "exec" | "file_write" | "unknown_tool" | ...
  details?: Record<string, unknown>; // e.g. { command, path, operation, tool_name }
}

export interface PendingApproval {
  approval_id: string;
  command?: string; // may be empty for non-exec approvals
  description: string;
  category?: string; // "exec" | "file_write" | "network" | "patch_apply"
  details?: Record<string, unknown>; // structured approval metadata
  allow_permanent: boolean;
  created_at: number;
  age_seconds: number;
}

export interface PermanentEntry {
  pattern: string;
  added_at: number;
}

export interface ApprovalHistoryEntry {
  id: string;
  pattern_key: string;
  description: string;
  command: string;
  decision: string;
  timestamp: number;
  session_key: string;
}

export interface ApprovalsListResult {
  pending: PendingApproval[];
  pending_ids: string[];
  permanent_allowlist: string[];
  permanent_entries: PermanentEntry[];
  enabled: boolean;
  timeout: number;
}

// ---------------------------------------------------------------------------
// AI-initiated user confirmation (issue #646) — ask_user_confirm_card
// ---------------------------------------------------------------------------

/** A structured choice on a confirm card: {id, label}. */
export interface ConfirmChoice {
  id: string;
  label: string;
  /** Semantic role so the client can style/classify choices without
   *  hard-coding ids (issue #646 review): 'cancel' = abort the action,
   *  'adjust' = user wants the plan reworked (still a non-confirmation). */
  role?: 'cancel' | 'adjust';
}

/** A step listed on a confirm card: {id, title}. Shared with the execution
 *  progress state via step_id (the same ids drive step_started/completed). */
export interface ConfirmStep {
  id: string;
  title: string;
}

/** Card payload pushed from the backend when the model calls
 *  ask_user_confirm_card (blocking human-in-the-loop). */
export interface UserInputCardRequest {
  input_id: string;
  thread_id?: string;
  turn_id?: string;
  /** Originating session — cards are scoped per session and dropped on
   *  session switch (CodeRabbit #666 review). */
  session_key?: string;
  title: string;
  message: string;
  steps?: ConfirmStep[];
  choices?: ConfirmChoice[];
  timeout_seconds?: number;
  allow_remember_choice?: boolean;
}

/** Resolution pushed from the backend once the user picks / cancels. */
export interface UserInputResolvedData {
  input_id: string;
  status: 'submitted' | 'cancelled';
  resolution?: { choice_id?: string; choice_label?: string; [k: string]: unknown };
}

export interface UserInputResolveResult {
  resolved: boolean;
}

export interface ApprovalsAddPermanentResult {
  added: boolean;
  pattern: string;
}

export interface ApprovalsHistoryResult {
  history: ApprovalHistoryEntry[];
}

export const ApprovalsAddPermanentInput = z.object({
  pattern: z.string().min(1),
});

export interface ApprovalCleared {
  reason: 'abort' | 'resolved' | 'timeout';
}

// ---------------------------------------------------------------------------
// Cron schemas
// ---------------------------------------------------------------------------

export const CronCreateInput = z.object({
  name: z.string().min(1),
  scheduleKind: z.enum(['at', 'every', 'cron']),
  atMs: z.number().optional(),
  everyMs: z.number().optional(),
  expr: z.string().optional(),
  tz: z.string().optional(),
  message: z.string().optional(),
  deliver: z.boolean().optional(),
  channel: z.string().nullable().optional(),
  to: z.string().nullable().optional(),
});

export const CronUpdateInput = z.object({
  jobId: z.string().min(1),
  name: z.string().optional(),
  scheduleKind: z.enum(['at', 'every', 'cron']).optional(),
  atMs: z.number().optional(),
  everyMs: z.number().optional(),
  expr: z.string().optional(),
  tz: z.string().nullable().optional(),
  message: z.string().optional(),
  deliver: z.boolean().optional(),
  channel: z.string().nullable().optional(),
  to: z.string().nullable().optional(),
});

export const CronToggleInput = z.object({
  jobId: z.string().min(1),
  enabled: z.boolean(),
});

export const CronDeleteInput = z.object({
  jobId: z.string().min(1),
});

export const CronRunInput = z.object({
  jobId: z.string().min(1),
});

export const CronRunsInput = z.object({
  jobId: z.string().optional(),
});

export interface CronSchedule {
  kind: 'at' | 'every' | 'cron';
  atMs: number | null;
  everyMs: number | null;
  expr: string | null;
  tz: string | null;
}

// Issue #789: config hot-reload broadcast payload (tier classification).
export interface ConfigUpdatedPayload {
  /** Tier A paths — hot-applied, no restart needed. */
  applied: string[];
  /** Tier B paths — take effect for new sessions/turns. */
  newSessionsOnly: string[];
  /** Tier C paths — require an app restart. */
  restartRequired: string[];
  /** Human-readable reasons for the restart-required paths. */
  restartReasons: string[];
  /** Number of active runtime sessions hot-applied. */
  propagatedSessions?: number;
  /**
   * False when a provider rebuild failed during hot-apply (e.g. bad API
   * key): the old provider stays in the active session, so the save is
   * persisted but NOT live — UI must not claim "已生效".
   */
  providerRebuilt?: boolean;
}

export interface CronPayload {
  kind: 'system_event' | 'agent_turn';
  message: string;
  deliver: boolean;
  channel: string | null;
  to: string | null;
}

export interface CronState {
  nextRunAtMs: number | null;
  lastRunAtMs: number | null;
  lastStatus: 'ok' | 'error' | 'skipped' | null;
  lastError: string | null;
}

export interface CronJob {
  id: string;
  name: string;
  enabled: boolean;
  schedule: CronSchedule;
  payload: CronPayload;
  state: CronState;
  createdAtMs: number;
  updatedAtMs: number;
  deleteAfterRun: boolean;
}

export interface CronRunEntry {
  jobId: string;
  jobName: string;
  startedAtMs: number;
  status: 'ok' | 'error' | 'skipped' | null;
  error: string | null;
}

export interface CronListResult {
  jobs: CronJob[];
}

export interface CronCreateResult {
  job: CronJob;
}

export interface CronUpdateResult {
  job: CronJob;
}

export interface CronRunsResult {
  runs: CronRunEntry[];
}

// ---------------------------------------------------------------------------
// Memory schemas
// ---------------------------------------------------------------------------

export const MemoryGetInput = z.object({
  path: z.string().min(1),
});

export const MemoryUpdateInput = z.object({
  path: z.string().min(1),
  content: z.string(),
});

export interface MemoryFileInfo {
  path: string;
  scope: 'workspace' | 'agent';
  size: number;
  updatedAtMs: number;
}

export interface MemoryListResult {
  files: MemoryFileInfo[];
}

export interface MemoryGetResult {
  path: string;
  content: string;
  size: number;
}

export interface MemoryLessonEntry {
  id: string;
  trigger: string;
  badAction: string;
  betterAction: string;
  scope: string;
  sessionKey: string | null;
  confidence: number;
  effectiveConfidence: number;
  hits: number;
  state: string;
  enabled: boolean;
  source: string;
  createdAt: string;
  updatedAt: string;
}

export interface MemoryLessonsResult {
  lessons: MemoryLessonEntry[];
}

export const MemoryLessonUnlearnInput = z.object({
  lesson_id: z.string().min(1),
});

export interface MemoryLessonUnlearnResult {
  unlearned: string[];
}

export interface ExperienceEntry {
  id: string;
  type: 'fact' | 'rule' | 'trace';
  title: string;
  content: string;
  confidence: number;
  enabled: boolean;
  scope: string;
  source: string;
  session_key: string;
  created_at: number;
  updated_at: number;
  metadata: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Skills schemas
// ---------------------------------------------------------------------------

export const SkillsGetInput = z.object({
  name: z.string().min(1),
});

export interface SkillSummary {
  name: string;
  source: 'builtin' | 'workspace';
  path: string;
  description: string;
  available: boolean;
  missingRequirements: string | null;
}

export interface SkillsListResult {
  skills: SkillSummary[];
}

export interface SkillDetail {
  name: string;
  source: 'builtin' | 'workspace';
  path: string;
  description: string;
  available: boolean;
  missingRequirements: string | null;
  content: string;
  metadata: Record<string, unknown> | null;
}

// ---------------------------------------------------------------------------
// MCP schemas
// ---------------------------------------------------------------------------

export interface McpServerConfig {
  type?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  insecure_http?: boolean;
  tool_timeout?: number;
  progress_interval_seconds?: number;
  description?: string;
  lazy?: boolean;
}

export interface McpServerInfo extends McpServerConfig {
  name: string;
}

export const McpUpsertInput = z.object({
  name: z.string().min(1),
  type: z.string().optional(),
  command: z.string().optional(),
  args: z.array(z.string()).optional(),
  env: z.record(z.string()).optional(),
  url: z.string().optional(),
  headers: z.record(z.string()).optional(),
  insecure_http: z.boolean().optional(),
  tool_timeout: z.number().optional(),
  progress_interval_seconds: z.number().optional(),
  description: z.string().optional(),
  lazy: z.boolean().optional(),
});

export const McpDeleteInput = z.object({
  name: z.string().min(1),
});

// ---------------------------------------------------------------------------
// Files schemas
// ---------------------------------------------------------------------------

export const FilesReadInput = z.object({
  path: z.string().min(1),
  session_key: z.string().optional(),
  /** #877: read raw bytes (Office files etc.) for「下载/另存为」 */
  as_binary: z.boolean().optional(),
});

export const FilesSaveAsInput = z.object({
  default_name: z.string().min(1),
  data_base64: z.string(),
});

export const FilesWriteInput = z.object({
  path: z.string().min(1),
  content: z.string(),
  session_key: z.string().optional(),
  data_base64: z.string().optional(),
});

export interface FileNode {
  name: string;
  path: string;
  is_dir: boolean;
  children?: FileNode[];
}

export interface FilesTreeResult {
  root: FileNode;
  workspace_path: string;
}

export interface FilesReadResult {
  path: string;
  content?: string;
  data_base64?: string;
  size: number;
  mime_type?: string;
  is_binary?: boolean;
}

export interface FilesWriteResult {
  saved: boolean;
  path: string;
}

export interface FilesDiffResult {
  path: string;
  diff: string | null;
  has_diff: boolean;
  original_content: string | null;
  current_content: string | null;
  error?: string;
  is_new_file?: boolean;
}

export interface FilesRevertResult {
  reverted: boolean;
  path: string;
}

export interface FilesOpenExternalResult {
  opened: boolean;
  path: string;
  error?: string;
}

/** Result of writing an HTML string to a temp file and opening it in the
 *  system default browser. */
export interface HtmlOpenInBrowserResult {
  opened: boolean;
  path: string;
  error?: string;
}

export interface FilesOpenContainingFolderResult {
  revealed: boolean;
  path: string;
  error?: string;
}

/** #877: native save dialog result for the preview「下载/另存为」button. */
export interface FilesSaveAsResult {
  saved: boolean;
  canceled?: boolean;
  path?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// Structured document preview types (issue #877: rich in-app rendering)
// ---------------------------------------------------------------------------

export interface CellMerge {
  /** 0-based row/col bounds of a merged range (inclusive). */
  start_row: number;
  start_col: number;
  end_row: number;
  end_col: number;
}

export interface StructuredSheet {
  name: string;
  rows: string[][];
  merges?: CellMerge[];
}

export interface SpreadsheetData {
  kind: 'spreadsheet';
  sheets: StructuredSheet[];
}

export type DocBlock =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'table'; rows: string[][] }
  | { type: 'image'; data_url: string };

export interface DocumentBlocks {
  kind: 'document';
  blocks: DocBlock[];
}

export type StructuredParseResult = SpreadsheetData | DocumentBlocks;

export interface DocumentsParseResult {
  path: string;
  text: string;
  page_count: number;
  size_bytes: number;
  mime_type: string;
  ocr_used: boolean;
  parse_ms: number;
  /** #877: present when structured parsing succeeded and the frontend
   *  requested it.  Absent for formats without a rich renderer. */
  structured?: StructuredParseResult;
}

export interface TrackedFileInfo {
  path: string;
  op: 'read' | 'write' | 'edit' | 'delete';
  name: string;
  lastSeen: number;
}

// ---------------------------------------------------------------------------
// Chat types
// ---------------------------------------------------------------------------

export interface ChatProgress {
  /** Text content — absent for pure-lifecycle events like stream:'turn'. */
  text?: string;
  /** Tool-hint flag — absent for pure-lifecycle events like stream:'turn'. */
  tool_hint?: boolean;
  stream?: 'stdout' | 'stderr' | 'reasoning' | 'turn' | 'points';
  delta?: string;
  tool_call_id?: string;
  /** Original tool-call arguments (e.g. web_fetch's url) — carried on the
   *  begin event so the live tool row can show the exact target. */
  tool_args?: unknown;
  /** Tool result output (full for paper_search/web_search) — carried on the
   *  end event so the live row can render result cards. */
  tool_output?: string;
  /** Session key for frontend-side event filtering (fix #212). */
  session_key?: string;
  /** Document progress events from server-side parsing */
  type?: 'doc_progress' | 'billed' | 'blocked';
  file?: string;
  stage?: string;
  message?: string;
  /** Backend-issued turn id — carried on turn_started progress so the
   *  frontend can drop terminal events from superseded turns (#542). */
  turn_id?: string;
  /** Platform points billing events (stream:'points')：本次扣费数量。 */
  points_cost?: number;
  /** Platform points billing events (stream:'points')：扣费后的可用余额。 */
  balance?: number;
}

export interface ChatFinal {
  content: string;
  aborted?: boolean;
  tool_calls?: unknown[];
  /** Model reasoning / chain-of-thought from thinking models
   *  (DeepSeek-R1, Kimi). Rendered as a collapsible thinking block. */
  reasoning?: string;
  /** Server-side thinking proxy (seconds): time from request start to the
   *  first reasoning delta, measured by the backend (#834). Preferred over
   *  frontend first/last-delta timing, which only measures transport time
   *  for buffered reasoning providers (DeepSeek). */
  reasoning_elapsed_s?: number;
  /** Session key for frontend-side event filtering (fix #212).  Optional
   *  for backward compatibility; see ChatProgress.session_key. */
  session_key?: string;
  /** Backend-issued turn id — lets the frontend drop a final event from a
   *  superseded turn (#542). */
  turn_id?: string;
}

export interface ChatError {
  message: string;
  /** Error code from backend (e.g. NO_API_KEY, INTERNAL) */
  code?: string;
  /** Session key for frontend-side event filtering (fix #212).  Optional
   *  for backward compatibility; see ChatProgress.session_key. */
  session_key?: string;
  /** Backend-issued turn id (#542). */
  turn_id?: string;
}

export interface ChatAborted {
  message: string;
  /** Session key for frontend-side event filtering (fix #212).  Optional
   *  for backward compatibility; see ChatProgress.session_key. */
  session_key?: string;
  /** Backend-issued turn id — lets the frontend drop an aborted event from
   *  a superseded turn instead of stopping the replacement turn (#542). */
  turn_id?: string;
}

export interface ChatSubagentResult {
  task_id: string;
  label: string;
  task: string;
  result: string;
  status: string; // "ok" | "error"
  session_key: string;
}

// ---------------------------------------------------------------------------
// Python check result
// ---------------------------------------------------------------------------

export interface PythonCheckResult {
  ok: boolean;
  python_version: string;
  issues: string[];
  config_exists: boolean;
}

// ---------------------------------------------------------------------------
// WSL2 check result
// ---------------------------------------------------------------------------

/** Granular WSL feature states detected during check */
export type WslFeatureState =
  | 'not-supported' // Non-Windows or WSL not available
  | 'not-enabled' // Windows Optional Features not turned on
  | 'not-installed' // WSL kernel/package not installed
  | 'installed-but-not-initialized' // WSL installed but no distro launched
  | 'ready'; // Fully functional

export interface WslCheckResult {
  isWindows: boolean;
  installed: boolean;
  version: string | null; // e.g. "2" or "1"
  distros: string[]; // e.g. ["Ubuntu"]
  defaultDistro: string | null;
  running: boolean; // whether WSL is currently active

  /** Granular feature state (new in #361) */
  featureState: WslFeatureState;
  /** Whether a system reboot is required before WSL can be used */
  rebootRequired: boolean;
}
export interface WslExportDistroResult {
  exported: boolean;
  distro: string | null; // exported distro name
  tarPath: string | null; // path to exported tar file
  error: string | null;
}
export interface WslImportDistroResult {
  imported: boolean;
  distro: string | null; // imported distro name
  installLocation: string | null; // where the distro was installed
  error: string | null;
}

// ---------------------------------------------------------------------------
// WSL runtime stats (memory / CPU / disk)
// ---------------------------------------------------------------------------

export interface WslStatsResult {
  ok: boolean;
  error?: string;
  distro: string; // which distro was queried
  memory: {
    total_mb: number;
    used_mb: number;
    free_mb: number;
    used_pct: number; // 0-100
  };
  cpu: {
    usage_pct: number; // 0-100, instantaneous snapshot
    cores: number;
  };
  disk: {
    total_gb: number;
    used_gb: number;
    free_gb: number;
    used_pct: number;
  };
  uptime_sec: number;
}

// ---------------------------------------------------------------------------
// WSL install & provision progress (new in #361)
// ---------------------------------------------------------------------------

export interface WslInstallProgress {
  phase:
    | 'checking'
    | 'enabling_features'
    | 'installing_wsl'
    | 'installing_distro'
    | 'complete'
    | 'error';
  message: string;
  error?: string;
  rebootRequired?: boolean;
}

export interface WslInstallAndProvisionResult {
  success: boolean;
  phase: string;
  rebootRequired?: boolean;
  error?: string;
  errorCode?: string;
  nextStep?: string;
}

// ---------------------------------------------------------------------------
// Phase 1: New types for multi-agent, plan, permissions
// ---------------------------------------------------------------------------

export interface LiveAgentInfo {
  agent_id: string;
  thread_id: string;
  type: string;
  status:
    'idle' | 'thinking' | 'executing' | 'waiting_approval' | 'completed' | 'error' | 'aborted';
  parent: string | null;
  label: string;
  spawned_at: number;
}

export interface PlanStep {
  id: string;
  description: string;
  status: 'pending' | 'in_progress' | 'completed' | 'skipped';
  depends_on: string[];
}

export interface Plan {
  plan_id: string;
  title: string;
  steps: PlanStep[];
  created_at: number;
  updated_at: number;
}

export interface AgentSpawnedEvent {
  sub_agent_id: string;
  sub_thread_id: string;
  agent_type: string;
  task_label: string;
}

export interface AgentCompletedEvent {
  sub_agent_id: string;
  sub_thread_id: string;
  outcome: string;
  summary: string;
}

export interface PlanUpdatedEvent {
  plan: Plan;
}

export interface TurnStartedEvent {
  turn_id: string;
  agent_name: string;
  thread_id: string;
}

export interface TurnCompletedEvent {
  turn_id: string;
  thread_id: string;
  outcome: string;
  tools_used: string[];
  token_usage: Record<string, number>;
}

// ── Thread / Turn types (Phase 36+) ───────────────────────────────────────

export const ThreadStartInput = z.object({
  title: z.string().optional(),
  session_key: z.string().optional(),
  thread_id: z.string().optional(),
});

export const ThreadReadInput = z.object({
  thread_id: z.string().min(1),
  session_key: z.string().optional(),
});

export const ThreadNameSetInput = z.object({
  thread_id: z.string().min(1),
  name: z.string().min(1),
  session_key: z.string().optional(),
});

export const TurnStartInput = z.object({
  thread_id: z.string().min(1),
  content: z.string().min(1),
  session_key: z.string().optional(),
  model: z.string().optional(),
  effort: z.string().optional(),
});

export const TurnInterruptInput = z.object({
  thread_id: z.string().min(1),
  turn_id: z.string().min(1),
  session_key: z.string().optional(),
});

export const ChatAbortInput = z.object({
  session_key: z.string().optional(),
  thread_id: z.string().optional(),
});

export const AgentListInput = z.object({
  session_key: z.string().optional(),
});

export interface ThreadInfo {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  turn_count: number;
}

export interface ThreadStartResult {
  thread: Record<string, unknown>;
}

export interface ThreadListResult {
  items: Record<string, unknown>[];
  nextCursor?: null | string;
}

export interface ThreadReadResult {
  thread: Record<string, unknown>;
}

export interface TurnStartResult {
  turn: Record<string, unknown>;
}

export interface TurnInterruptResult {
  interrupted: boolean;
}

export interface ThreadStartedEvent {
  thread: Record<string, unknown>;
}

export interface SandboxSetEnabledResult {
  enabled: boolean;
  destroyed?: number;
  already?: boolean;
  initializing?: boolean;
}

// ---------------------------------------------------------------------------
// Feedback schemas
// ---------------------------------------------------------------------------

// Per-screenshot validator: must be a `data:image/<mime>;base64,<...>`
// URL whose decoded byte size is within the documented 10 MB limit.
// Mirrors the server-side check in miqi/runtime/feedback_handlers.py
// _decode_data_url so oversized/malformed payloads are rejected at the
// IPC boundary before they reach the bridge.
const MAX_DATA_URL_BYTES = 10 * 1024 * 1024;
const dataUrlScreenshot = z
  .string()
  .refine(
    (s) => s.startsWith('data:image/') && s.includes(';base64,'),
    'Screenshot must be a base64-encoded data URL with image MIME type'
  )
  .refine((s) => {
    const comma = s.indexOf(',');
    if (comma < 0) return false;
    const b64 = s.slice(comma + 1);
    // base64 inflates ~4/3, so 14 MB encoded → ~10.5 MB decoded
    return b64.length * 3 <= MAX_DATA_URL_BYTES * 4 + 4;
  }, 'Screenshot exceeds 10 MB limit');

export const FeedbackSubmitInput = z.object({
  category: z.enum(['bug', 'question', 'suggestion', 'other']),
  title: z.string().min(1).max(200),
  content: z.string().min(1).max(10000),
  contact: z.string().max(200).optional(),
  app_version: z.string().max(50).optional(),
  screenshots: z.array(dataUrlScreenshot).max(5).optional(),
  prompt_used: z.string().max(10000).optional(),
  repro_frequency: z.string().max(200).optional(),
});

export interface FeedbackEntry {
  id: string;
  category: 'bug' | 'question' | 'suggestion' | 'other';
  title: string;
  content: string;
  contact: string;
  app_version: string;
  os: string;
  python_version: string;
  feishu_record_id: string;
  created_at: string;
}

export interface FeedbackListResult {
  entries: FeedbackEntry[];
}

export interface FeedbackSubmitResult {
  ok: boolean;
  record_id: string;
}

// ---------------------------------------------------------------------------
// MiQroForge 平台 OAuth2 登录 (issue #726)
// ---------------------------------------------------------------------------

/** MiQroForge 接入配置的 URL 校验（IPC 边界拦截非法值，避免进入网络/OAuth 流程）。 */
const qraftBaseUrlSchema = z
  .string()
  .max(500)
  .refine((s) => /^https:\/\/.+/i.test(s), '必须是 https:// 开头的完整地址')
  .optional();
const qraftRedirectUriSchema = z
  .string()
  .max(500)
  .refine(
    (s) => /^https?:\/\//i.test(s),
    '必须是 http(s):// 开头的完整地址（测试环境可用 http://localhost 回调）'
  )
  .optional();

/** 登录请求：手机号 + 密码 + 可选的环境/接入配置覆盖（高级设置）。 */
export const QraftLoginInput = z.object({
  phone: z.string().min(1).max(32),
  password: z.string().min(1).max(256),
  env: z.enum(['test', 'prod']).optional(),
  baseUrl: qraftBaseUrlSchema,
  clientId: z.string().max(200).optional(),
  clientSecret: z.string().max(500).optional(),
  redirectUri: qraftRedirectUriSchema,
});

/** 浏览器登录请求：打开 MiQroForge 页面由用户登录并点击"同意"，无需手机号/密码。 */
export const QraftBrowserLoginInput = z.object({
  env: z.enum(['test', 'prod']).optional(),
  baseUrl: qraftBaseUrlSchema,
  clientId: z.string().max(200).optional(),
  clientSecret: z.string().max(500).optional(),
  redirectUri: qraftRedirectUriSchema,
});

export interface QraftAccount {
  /** 登录用的手机号（脱敏展示与存储，日志中不出现完整值）。浏览器登录路径无手机号，为空字符串。 */
  phone: string;
  sub: string;
  username: string;
  nickname: string;
}

/** 登录失败时的稳定错误码（渲染进程据此展示修复指引）。 */
export type QraftErrorCode =
  | 'IP_NOT_WHITELISTED'
  | 'NETWORK_UNREACHABLE'
  | 'LOGIN_FAILED'
  | 'PUBLIC_KEY_EXTRACT_FAILED'
  | 'SESSION_EXPIRED'
  | 'AUTHORIZE_FAILED'
  | 'TOKEN_EXCHANGE_FAILED'
  | 'REFRESH_FAILED'
  | 'REFRESH_TOKEN_INVALID'
  | 'USERINFO_FAILED'
  | 'LOGIN_CANCELLED'
  | 'BROWSER_LOGIN_FAILED'
  | 'INVALID_CONFIG'
  | 'POINTS_FAILED'
  | 'INSUFFICIENT_POINTS'
  | 'INTERNAL';

export interface QraftLoginResult {
  ok: boolean;
  account?: QraftAccount;
  code?: QraftErrorCode;
  message?: string;
}

/** 本地留存的扣费历史条目（issue #927；平台无扣费历史查询接口）。 */
export interface QraftBillingHistoryEntry {
  /** 计费请求唯一 ID（Python 侧生成，去重键）。 */
  chargeId: string;
  /** 扣费时间（ISO 8601）。 */
  deductedAt: string;
  /** 本次扣除积分。 */
  cost: number;
  /** 扣费后可用余额（成功时）。 */
  balanceAfter?: number;
  status: 'billed' | 'insufficient' | 'error';
  /** SLURM 作业 ID（作业提交成功后回传补充）。 */
  jobId?: string;
  serverName?: string;
  toolName?: string;
  /** 提交命令/脚本参数摘要。 */
  argsSummary?: string;
  sessionKey?: string;
  /** 扣费时的登录账号 sub（历史按账号隔离展示，换账号互不可见）。 */
  accountSub?: string;
}

/** 平台积分余额（GET /oauth2/points/balance 的 data 字段）。 */
export interface QraftPointsBalance {
  /** 可用积分 */
  availablePoints: number;
  /** 托管（冻结）积分 */
  heldPoints: number;
  /** 累计获得积分 */
  totalEarned: number;
  /** 累计支出积分 */
  totalSpent: number;
}

/** 平台 AI 网关开通状态（userinfo 下发；"active" 才允许模型调用走网关）。 */
export type QraftAiGatewayStatus = string;

/** 登录态中下发的网关开通信息（仅非敏感字段进渲染进程；encryptedApiKey 永不外发）。 */
export interface QraftAiGatewayInfo {
  status: QraftAiGatewayStatus;
  /** 平台配置版本号（本切片仅展示/透出，热刷新留后续）。 */
  configVersion?: number;
}

export interface QraftStatus {
  loggedIn: boolean;
  account?: QraftAccount;
  env?: 'test' | 'prod';
  baseUrl?: string;
  /** access_token 到期时间（epoch 毫秒）。 */
  expiresAt?: number;
  /** 计划中的自动刷新时间（epoch 毫秒）。 */
  refreshScheduledAt?: number;
  refreshError?: QraftErrorCode;
  requiresRelogin?: boolean;
  /** 最近一次拉取的积分余额（设置页拉取后缓存，随状态事件推送）。 */
  points?: QraftPointsBalance;
  /** 平台 AI 网关开通状态（登录且 active 时模型调用走网关）。 */
  aiGateway?: QraftAiGatewayInfo;
}
