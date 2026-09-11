import { electron } from '../../shared/electron';
import { spawn, spawnSync } from 'child_process';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, unlinkSync } from 'fs';
import { homedir, tmpdir } from 'os';
import { randomUUID } from 'crypto';
import { join } from 'path';
import type { BrowserWindow } from 'electron';
import type { BridgeManager } from '../bridge';
import {
  IPC,
  IPC_EVENTS,
  ChatSendInput,
  ChatAbortInput,
  SessionGetInput,
  SessionDeleteInput,
  SessionClaimLegacyInput,
  SessionRenameInput,
  ConfigUpdateInput,
  ProviderTestInput,
  ProviderUpdateInput,
  ProviderActivateInput,
  ProviderDeactivateInput,
  ChannelsUpdateInput,
  CronCreateInput,
  CronUpdateInput,
  CronToggleInput,
  CronDeleteInput,
  CronRunInput,
  CronRunsInput,
  MemoryGetInput,
  MemoryUpdateInput,
  MemoryLessonUnlearnInput,
  SkillsGetInput,
  FilesReadInput,
  FilesWriteInput,
  FilesSaveAsInput,
  McpUpsertInput,
  McpDeleteInput,
  AgentSpawnInput,
  PermissionsUpdateInput,
  ThreadStartInput,
  ThreadReadInput,
  ThreadNameSetInput,
  TurnStartInput,
  TurnInterruptInput,
  FeedbackSubmitInput,
} from '../../shared/ipc';
import type {
  WslCheckResult,
  WslStatsResult,
  WslInstallProgress,
  WslInstallAndProvisionResult,
} from '../../shared/ipc';
import { registerQraftIpcHandlers } from '../qraft/ipc';
import {
  classifyKernelInstall,
  classifyWslFeatureState,
  hasNonRootUser,
  isBashCapableDistro,
  readFeatureStates,
  runElevated,
  summarizeElevated,
  wslKernelPresent,
} from './wsl-state';
import {
  getConfigDir,
  getConfigPath,
  getWorkspacePath,
  isWithinCanonicalWorkspace,
  readLocalConfig,
  resolveWorkspacePath,
} from './workspace-path';

const { ipcMain, dialog, shell, app } = electron;

function readWorkspaceLogLines(
  projectRoot: string,
  includeFile: (name: string) => boolean
): string[] {
  const logDir = join(projectRoot, 'workspace', 'logs');
  const lines: string[] = [];
  try {
    const files = readdirSync(logDir)
      .filter((name) => name.endsWith('.log') && includeFile(name))
      .sort();
    for (const file of files) {
      try {
        const content = readFileSync(join(logDir, file), 'utf8');
        lines.push(...content.split('\n').filter(Boolean));
      } catch {
        /* skip unreadable files */
      }
    }
  } catch {
    /* log dir may not exist yet */
  }
  return lines;
}

// Re-exported for consumers that import from this module (e.g. qraft/ipc.ts).
export { getWorkspacePath };

function deepMergeConfig(
  base: Record<string, unknown>,
  updates: Record<string, unknown>
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...base };
  for (const [key, value] of Object.entries(updates)) {
    const current = result[key];
    if (
      value &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      current &&
      typeof current === 'object' &&
      !Array.isArray(current)
    ) {
      result[key] = deepMergeConfig(
        current as Record<string, unknown>,
        value as Record<string, unknown>
      );
    } else {
      result[key] = value;
    }
  }
  return result;
}

function writeLocalConfig(updates: Record<string, unknown>): {
  saved: boolean;
  path: string;
  offline: boolean;
} {
  const configDir = getConfigDir();
  const configPath = getConfigPath();
  const merged = deepMergeConfig(readLocalConfig(), updates);
  mkdirSync(configDir, { recursive: true });
  writeFileSync(configPath, JSON.stringify(merged, null, 2), 'utf8');
  return { saved: true, path: configPath, offline: true };
}

function isApprovalBypassUpdate(updates: Record<string, unknown>): boolean {
  const allowedTopLevel = new Set(['approvals', 'agents']);
  if (!Object.keys(updates).every((key) => allowedTopLevel.has(key))) return false;

  if ('approvals' in updates) {
    const approvals = updates['approvals'];
    if (!approvals || typeof approvals !== 'object' || Array.isArray(approvals)) return false;
    const allowedApprovalKeys = new Set([
      'bypassAll',
      'bypassCommandApproval',
      'bypassFileWriteApproval',
      'bypassToolConfirmation',
      'bypassNetworkApproval',
    ]);
    for (const [key, value] of Object.entries(approvals as Record<string, unknown>)) {
      if (!allowedApprovalKeys.has(key) || typeof value !== 'boolean') return false;
    }
  }

  if ('agents' in updates) {
    const agents = updates['agents'];
    if (!agents || typeof agents !== 'object' || Array.isArray(agents)) return false;
    const agentKeys = Object.keys(agents as Record<string, unknown>);
    if (agentKeys.length !== 1 || agentKeys[0] !== 'commandApproval') return false;
    const commandApproval = (agents as Record<string, unknown>)['commandApproval'];
    if (!commandApproval || typeof commandApproval !== 'object' || Array.isArray(commandApproval)) {
      return false;
    }
    const commandKeys = Object.keys(commandApproval as Record<string, unknown>);
    if (commandKeys.length !== 1 || commandKeys[0] !== 'enabled') return false;
    if (typeof (commandApproval as Record<string, unknown>)['enabled'] !== 'boolean') return false;
  }

  return 'approvals' in updates || 'agents' in updates;
}

export function registerIpcHandlers(bridge: BridgeManager): void {
  // -----------------------------------------------------------------------
  // Runtime
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.RUNTIME_START, async () => {
    await bridge.start();
    return bridge.getStatus();
  });

  ipcMain.handle(IPC.RUNTIME_STOP, async () => {
    await bridge.stop();
    return bridge.getStatus();
  });

  ipcMain.handle(IPC.RUNTIME_STATUS, () => {
    return bridge.getStatus();
  });

  ipcMain.handle(IPC.RUNTIME_LOGS, () => {
    return bridge.getLogs();
  });

  ipcMain.handle(IPC.RUNTIME_FILE_LOGS, () => {
    return readWorkspaceLogLines(bridge.getProjectRoot(), () => true);
  });

  ipcMain.handle(IPC.RUNTIME_BACKEND_LOGS, () => {
    const backendPrefixes = ['bridge-', 'sandbox-', 'tool-', 'electron-main-'];
    return readWorkspaceLogLines(bridge.getProjectRoot(), (name) =>
      backendPrefixes.some((prefix) => name.startsWith(prefix))
    );
  });

  ipcMain.on('runtime:renderer-log', (_event, payload: unknown) => {
    if (!payload || typeof payload !== 'object') return;
    const entry = payload as Record<string, unknown>;
    const level =
      typeof entry.level === 'string' && ['INFO', 'WARN', 'ERROR'].includes(entry.level)
        ? entry.level
        : 'INFO';
    const rawMessage = typeof entry.message === 'string' ? entry.message : 'Renderer log';
    // Cap message length to prevent log file bloat from runaway renderers
    const message =
      rawMessage.length > 4096 ? rawMessage.slice(0, 4096) + '…[truncated]' : rawMessage;
    const sessionKey =
      typeof entry.sessionKey === 'string' && /^[\w:.-]{1,128}$/.test(entry.sessionKey)
        ? entry.sessionKey
        : undefined;
    const sessionPart = sessionKey ? ` session=${sessionKey}` : '';
    bridge.recordMainLog(level, `[renderer${sessionPart}] ${message}`, 'renderer');
  });

  // -----------------------------------------------------------------------
  // Chat
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.CHAT_SEND, async (_event, payload: unknown) => {
    const input = ChatSendInput.parse(payload);

    const sender = _event.sender;
    const safeSend = (channel: string, data: unknown) => {
      if (!sender.isDestroyed()) {
        sender.send(channel, data);
      }
    };
    const result = await bridge.send(
      'chat.send',
      {
        content: input.content,
        session_key: input.session_key ?? 'desktop:default',
        thread_id: (input as any).thread_id ?? undefined,
        mode: input.mode,
        attachments: input.attachments,
        workspace: input.workspace,
        resume_turn_id: (input as any).resume_turn_id ?? undefined,
      },
      (type: string, data: unknown) => {
        if (type === 'progress') {
          safeSend('chat:progress', data);
        } else if (type === 'final') {
          safeSend('chat:final', data);
        } else if (type === 'error') {
          safeSend('chat:error', data);
        } else if (type === 'aborted') {
          safeSend('chat:aborted', data);
        } else if (type === 'approval_request') {
          safeSend('approval:request', data);
        } else if (type === 'approval_cleared') {
          safeSend('approval:cleared', data);
        } else if (type === 'user_input_requested') {
          safeSend('userInput:request', data);
        } else if (type === 'user_input_resolved') {
          safeSend('userInput:resolved', data);
        } else if (type === 'subagent_result') {
          safeSend('chat:subagent_result', data);
        } else if (type === 'slurm_job_running') {
          // Slurm 作业 RUNNING 扣费（issue #927）：主进程发起扣费（10 分/次，
          // 按作业 ID 去重），结果以 #915 的 points 事件流在聊天区展示。
          // 作业已在运行，扣费失败（余额不足等）不阻断作业，仅记录并提示。
          void (async () => {
            const { getQraftService } = await import('../qraft/ipc');
            const payload = (data ?? {}) as Record<string, unknown>;
            const result = await getQraftService().chargeSlurmJob({
              charge_id: String(payload.charge_id ?? ''),
              job_id: String(payload.job_id ?? ''),
              server_name: String(payload.server_name ?? ''),
              tool_name: String(payload.tool_name ?? ''),
              args_summary: String(payload.args_summary ?? ''),
              session_key: String(payload.session_key ?? ''),
              turn_id: String(payload.turn_id ?? ''),
            });
            // 去重命中（该作业已计费过）：不当作新的扣费播报，聊天区
            // 不出现重复的「已扣 10 积分」（CodeRabbit #936 评审）。
            if (result.dedup) return;
            safeSend('chat:progress', {
              stream: 'points',
              type: result.ok ? 'billed' : 'blocked',
              points_cost: 10,
              balance: result.balance ?? null,
              message: result.ok
                ? `Slurm 作业已扣 10 积分，可用余额 ${result.balance}`
                : (result.message ?? 'Slurm 作业计费失败'),
            });
          })().catch((err) => {
            console.error(
              `[qraft] slurm 计费处理异常：${err instanceof Error ? err.message : err}`
            );
          });
        } else if (type === 'chat:delta' || type === 'delta') {
          safeSend('chat:progress', data);
        }
      }
    );

    return result;
  });

  ipcMain.handle(IPC.CHAT_ABORT, async (_event, payload: unknown) => {
    const input = ChatAbortInput.parse(payload);
    return bridge.send('chat.abort', {
      session_key: input.session_key,
      thread_id: input.thread_id,
    });
  });

  // #740: discard an interrupted turn's execution snapshot (重新开始)
  ipcMain.handle(IPC.CHAT_DISCARD_RESUME, async (_event, payload: unknown) => {
    const input = (payload ?? {}) as {
      resume_turn_id?: string;
      session_key?: string;
    };
    return bridge.send('chat.discard_resume', {
      resume_turn_id: input.resume_turn_id,
      session_key: input.session_key,
    });
  });

  // Clipboard write from the sandboxed renderer.  The electron clipboard
  // module is NOT available in sandboxed preloads, so the write is routed
  // here to the main process (works for file:// packaged builds too).
  ipcMain.handle(IPC.CLIPBOARD_WRITE_TEXT, (_event, payload: { text?: unknown }) => {
    try {
      const text = typeof payload?.text === 'string' ? payload.text : String(payload?.text ?? '');
      electron.clipboard.writeText(text);
      return { ok: true };
    } catch {
      return { ok: false };
    }
  });

  // HEAD-check a URL in the main process (no CORS) — used by "查看来源"
  // to drop dead links before the user clicks them.
  ipcMain.handle(IPC.WEB_CHECK_URL, async (_event, payload: { url?: unknown }) => {
    const url = typeof payload?.url === 'string' ? payload.url : '';
    if (!/^https?:\/\//i.test(url)) return { ok: false, status: 0 };
    // SSRF guard: never reach loopback / link-local / private ranges from the
    // privileged main process (mirrors _validate_url on the Python side).
    // URL.hostname keeps brackets for IPv6 literals ("[::1]") — strip them so
    // the IPv6 comparisons actually match; IPv6 checks apply only to literals,
    // so ordinary DNS names like fcc.gov / fda.gov are never rejected.
    const isSafeHost = (u: string): boolean => {
      let host = '';
      try {
        host = new URL(u).hostname.toLowerCase();
      } catch {
        return false;
      }
      const isIpv6Literal = host.startsWith('[') && host.endsWith(']');
      const bare = isIpv6Literal ? host.slice(1, -1) : host;
      if (
        host === 'localhost' ||
        host.endsWith('.localhost') ||
        /^(127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(host) ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
        /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./.test(host) ||
        (isIpv6Literal &&
          (bare === '::1' ||
            bare === '::' ||
            bare.startsWith('fe80:') ||
            /^f[cd][0-9a-f]{0,2}:/.test(bare)))
      ) {
        return false;
      }
      return true;
    };
    if (!isSafeHost(url)) return { ok: false, status: 0 };
    // Redirects are followed manually — every hop re-runs the host validation,
    // so a public URL can never bounce the privileged main process to a
    // loopback/private address (e.g. 302 Location: http://127.0.0.1:9200/).
    const MAX_REDIRECTS = 5;
    const fetchWithSafeRedirects = async (
      method: 'HEAD' | 'GET',
      startUrl: string
    ): Promise<Response | null> => {
      let current = startUrl;
      for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
        const res = await check(method, current);
        if (res.status >= 300 && res.status < 400) {
          const loc = res.headers.get('location');
          if (!loc) return res; // no Location header — treat as final response
          const next = new URL(loc, current).toString();
          if (!/^https?:\/\//i.test(next) || !isSafeHost(next)) {
            return null; // unsafe redirect target — refuse to follow
          }
          current = next;
          continue;
        }
        return res;
      }
      return null; // too many hops — unverifiable
    };
    const check = async (method: 'HEAD' | 'GET', target: string): Promise<Response> => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 8000);
      try {
        const res = await electron.net.fetch(target, {
          method,
          redirect: 'manual',
          signal: controller.signal,
          headers: {
            // Real browser UA — many sites reject bare HEAD/bot requests,
            // which used to misclassify valid links as dead.
            'User-Agent':
              'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            ...(method === 'GET' ? { Range: 'bytes=0-2047' } : {}),
          },
        });
        return res;
      } finally {
        clearTimeout(timer);
      }
    };
    try {
      let res = await fetchWithSafeRedirects('HEAD', url);
      // Many sites reject HEAD (405) or block bots (403/429) but serve GET
      // fine — retry with GET before declaring the link dead.
      if (res && [403, 405, 429, 500, 501, 502, 503].includes(res.status)) {
        res = await fetchWithSafeRedirects('GET', url);
      }
      // null = unsafe redirect / too many hops — conservatively keep the link.
      if (!res) return { ok: true, status: 0 };
      // Only explicit 404/410 = dead; anything else keeps the link.
      return { ok: ![404, 410].includes(res.status), status: res.status };
    } catch {
      // Network error / timeout — retry once via GET (transient failures
      // are common), then conservatively keep the link if still unverifiable.
      try {
        const res2 = await fetchWithSafeRedirects('GET', url);
        if (!res2) return { ok: true, status: 0 };
        return { ok: ![404, 410].includes(res2.status), status: res2.status };
      } catch {
        return { ok: true, status: 0 };
      }
    }
  });

  // -----------------------------------------------------------------------
  // Sessions
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.SESSIONS_LIST, async () => {
    const result = await bridge.sendSafe('sessions.list');
    if (result == null) return { sessions: [] };
    return result;
  });

  ipcMain.handle(IPC.SESSIONS_GET, async (_event, payload: unknown) => {
    const input = SessionGetInput.parse(payload);
    const params: Record<string, unknown> = { session_key: input.session_key };
    if (input.workspace) params.workspace = input.workspace;
    return bridge.sendSafe('sessions.get', params);
  });

  ipcMain.handle(IPC.SESSIONS_DELETE, async (_event, payload: unknown) => {
    const input = SessionDeleteInput.parse(payload);
    return bridge.send('sessions.delete', { session_key: input.session_key });
  });

  ipcMain.handle(IPC.SESSIONS_ARCHIVE, async (_event, payload: unknown) => {
    const input = SessionGetInput.parse(payload);
    return bridge.sendSafe('sessions.archive', { session_key: input.session_key });
  });

  ipcMain.handle(IPC.SESSIONS_UNARCHIVE, async (_event, payload: unknown) => {
    const input = SessionGetInput.parse(payload);
    return bridge.sendSafe('sessions.unarchive', { session_key: input.session_key });
  });

  ipcMain.handle(IPC.SESSIONS_LIST_ARCHIVED, async () => {
    return bridge.sendSafe('sessions.list_archived');
  });

  ipcMain.handle(IPC.SESSIONS_GET_TRACKED_FILES, async (_event, payload: unknown) => {
    const input = SessionGetInput.parse(payload);
    return bridge.sendSafe('sessions.get_tracked_files', { session_key: input.session_key });
  });

  ipcMain.handle(IPC.SESSIONS_CLEAR_TRACKED_FILES, async (_event, payload: unknown) => {
    const input = SessionGetInput.parse(payload);
    return bridge.send('sessions.clear_tracked_files', { session_key: input.session_key });
  });

  ipcMain.handle(IPC.SESSIONS_CLAIM_LEGACY, async (_event, payload: unknown) => {
    const input = SessionClaimLegacyInput.parse(payload);
    return bridge.send('sessions.claim_legacy', { session_key: input.session_key });
  });

  ipcMain.handle(IPC.SESSIONS_RENAME, async (_event, payload: unknown) => {
    const input = SessionRenameInput.parse(payload);
    return bridge.send('sessions.rename', {
      session_key: input.session_key,
      title: input.title,
    });
  });

  // -----------------------------------------------------------------------
  // Config
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.CONFIG_GET, async () => {
    const bridgeConfig = await bridge.sendSafe('config.get');
    return bridgeConfig ?? readLocalConfig();
  });

  ipcMain.handle(IPC.CONFIG_UPDATE, async (_event, payload: unknown) => {
    const input = ConfigUpdateInput.parse(payload);
    if (!bridge.isRunning()) {
      if (!isApprovalBypassUpdate(input.config)) {
        throw new Error('Bridge not running');
      }
      return writeLocalConfig(input.config);
    }
    try {
      // 比较并设置（#991）：expectModel 原样转发给桥下 config.update，
      // 后端在磁盘当前模型与期望不一致时跳过写入。
      return await bridge.send('config.update', {
        config: input.config,
        ...(input.expectModel !== undefined ? { expect_model: input.expectModel } : {}),
      });
    } catch (error) {
      if ((error as Error)?.message?.includes('Bridge not running')) {
        if (!isApprovalBypassUpdate(input.config)) {
          throw error;
        }
        return writeLocalConfig(input.config);
      }
      throw error;
    }
  });

  // -----------------------------------------------------------------------
  // Providers
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.PROVIDERS_LIST, async () => {
    return bridge.sendSafe('providers.list');
  });

  ipcMain.handle(IPC.PROVIDERS_TEST, async (_event, payload: unknown) => {
    const input = ProviderTestInput.parse(payload);
    return bridge.send('providers.test', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.PROVIDERS_UPDATE, async (_event, payload: unknown) => {
    const input = ProviderUpdateInput.parse(payload);
    return bridge.send('providers.update', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.PROVIDERS_ACTIVATE, async (_event, payload: unknown) => {
    const input = ProviderActivateInput.parse(payload);
    return bridge.send('providers.activate', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.PROVIDERS_DEACTIVATE, async (_event, payload: unknown) => {
    const input = ProviderDeactivateInput.parse(payload);
    return bridge.send('providers.deactivate', input as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // Models (model/list catalog — issue #788 常用模型预设)
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.MODEL_LIST, async () => {
    return bridge.sendSafe('model/list');
  });

  // -----------------------------------------------------------------------
  // Channels
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.CHANNELS_LIST, async () => {
    return bridge.sendSafe('channels.list');
  });

  ipcMain.handle(IPC.CHANNELS_UPDATE, async (_event, payload: unknown) => {
    const input = ChannelsUpdateInput.parse(payload);
    return bridge.send('channels.update', input as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // Python check — runs directly in main process, no bridge needed.
  // This must work BEFORE the bridge starts (e.g. during Setup Wizard).
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.PYTHON_CHECK, () => {
    const projectRoot = bridge.getProjectRoot();
    const issues: string[] = [];
    let pythonVersion = 'unknown';

    // Check for bundled miqi-bridge first (packaged / production environment).
    const bridgeExeName = process.platform === 'win32' ? 'miqi-bridge.exe' : 'miqi-bridge';
    const bundledBridge = join(process.resourcesPath, bridgeExeName);
    if (existsSync(bundledBridge)) {
      // Packaged mode: the bundled bridge existing proves Python + deps are
      // complete. Skip the cold-start `--check` spawnSync — spawning the
      // onefile binary (extraction + interpreter + imports) took 10-15s and
      // blocked the whole app startup because `server.py` does not handle
      // `--check` and the sync call only returned on timeout (#603).
      pythonVersion = 'bundled';
    } else {
      // Development environment: check system Python
      const candidates: string[][] = [];
      if (process.env['MIQI_PYTHON_PATH']) {
        candidates.push([process.env['MIQI_PYTHON_PATH']!]);
      }
      const hasUvLock =
        existsSync(join(projectRoot, 'uv.lock')) || existsSync(join(projectRoot, 'pyproject.toml'));
      if (hasUvLock) {
        candidates.push(['uv', 'run', 'python']);
      }
      const venvWin = join(projectRoot, '.venv', 'Scripts', 'python.exe');
      if (existsSync(venvWin)) candidates.push([venvWin]);
      const venvUnix = join(projectRoot, '.venv', 'bin', 'python');
      if (existsSync(venvUnix)) candidates.push([venvUnix]);
      candidates.push(['python3'], ['python']);

      let pythonCmd: string[] | null = null;
      for (const candidate of candidates) {
        try {
          const r = spawnSync(candidate[0], [...candidate.slice(1), '--version'], {
            timeout: 5000,
            encoding: 'utf8',
            windowsHide: true,
          });
          if (r.status === 0) {
            pythonCmd = candidate;
            const ver = (r.stdout || r.stderr || '').trim().replace(/^Python\s+/i, '');
            pythonVersion = ver;
            const parts = ver.split('.').map(Number);
            if (parts[0] < 3 || (parts[0] === 3 && (parts[1] ?? 0) < 11)) {
              issues.push(`Python ${ver} is too old (need >= 3.11)`);
            }
            break;
          }
        } catch {
          // try next
        }
      }

      if (!pythonCmd) {
        issues.push('Python not found. Install Python >= 3.11 or set MIQI_PYTHON_PATH.');
        pythonVersion = 'not found';
      } else {
        const checkScript = `
import importlib, sys
for m in ("pydantic", "httpx", "loguru"):
    try:
        importlib.import_module(m)
    except ImportError:
        print("MISSING:" + m)
`;
        try {
          const r = spawnSync(pythonCmd[0], [...pythonCmd.slice(1), '-c', checkScript], {
            timeout: 8000,
            encoding: 'utf8',
            windowsHide: true,
          });
          const out = (r.stdout || '').trim();
          for (const line of out.split('\n')) {
            if (line.startsWith('MISSING:')) {
              issues.push(`Missing dependency: ${line.slice(8)}`);
            }
          }
        } catch {
          issues.push('Could not check MiQroForge dependencies');
        }
      }
    }

    const configExists = existsSync(join(homedir(), '.miqi', 'config.json'));

    return {
      ok: issues.length === 0,
      python_version: pythonVersion,
      issues,
      config_exists: configExists,
    };
  });

  // -----------------------------------------------------------------------
  // WSL2 check — shared implementation used by WSL_CHECK IPC handler
  // and WSL_INSTALL_AND_PROVISION. Returns full WslCheckResult.
  // -----------------------------------------------------------------------
  function runWslCheckInternal(): WslCheckResult {
    const isWindows = process.platform === 'win32';

    if (!isWindows) {
      return {
        isWindows: false,
        installed: true,
        version: null,
        distros: [],
        defaultDistro: null,
        running: false,
        featureState: 'not-supported' as const,
        rebootRequired: false,
      } satisfies WslCheckResult;
    }

    let rebootRequired = false;

    // DISM Get-WindowsOptionalFeature requires elevation and always fails
    // inside the non-elevated app; read the feature states over WMI instead
    // (readable unelevated, reflects pending DISM changes immediately).
    const features = readFeatureStates();
    const featureWsl = features.featureWsl;
    const featureVmp = features.featureVmp;

    try {
      try {
        const rb = spawnSync(
          'powershell.exe',
          [
            '-NoProfile',
            '-Command',
            'Get-ItemProperty "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager" -Name PendingFileRenameOperations -ErrorAction SilentlyContinue | Select-Object -ExpandProperty PendingFileRenameOperations',
          ],
          { timeout: 8000, encoding: 'utf8', windowsHide: true }
        );
        if (rb.status === 0 && rb.stdout?.trim()) rebootRequired = true;
      } catch {
        /* ignore */
      }

      try {
        const cbs = spawnSync(
          'powershell.exe',
          [
            '-NoProfile',
            '-Command',
            'Test-Path "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending"',
          ],
          { timeout: 5000, encoding: 'utf8', windowsHide: true }
        );
        if (cbs.status === 0 && cbs.stdout?.trim() === 'True') rebootRequired = true;
      } catch {
        /* ignore */
      }
    } catch {
      /* ignore */
    }

    let featureState: WslCheckResult['featureState'] = 'not-supported';

    let installed = false;
    let version: string | null = null;
    let distros: string[] = [];
    let defaultDistro: string | null = null;
    let running = false;
    let initialized = false;

    try {
      const statusResult = spawnSync('wsl', ['--status'], {
        timeout: 8000,
        encoding: 'buffer',
        windowsHide: true,
      });
      if (statusResult.status === 0) {
        installed = true;
        let output = '';
        const buf = statusResult.stdout as Buffer | null;
        if (buf && buf.length > 1) {
          const hasBOM = buf[0] === 0xff && buf[1] === 0xfe;
          const nullRatio =
            buf.reduce((acc, b, i) => (i % 2 === 1 && b === 0 ? acc + 1 : acc), 0) /
            Math.floor(buf.length / 2);
          if (hasBOM || nullRatio > 0.3) {
            output = buf.toString('utf16le');
          } else {
            output = buf.toString('utf8');
          }
          output = output.replace(/^﻿/, '').replace(/\0/g, '');
        }
        const defaultMatch = output.match(/(?:默认分发|Default Distr?ibution)\s*[:：]\s*(.+)/i);
        if (defaultMatch) defaultDistro = defaultMatch[1].trim();
        const verMatch = output.match(/(?:默认版本|Default Version)\s*[:：]\s*(\d+)/i);
        if (verMatch) version = verMatch[1];
      }
    } catch {
      /* WSL not installed */
    }

    if (installed) {
      try {
        const listResult = spawnSync('wsl', ['--list', '--quiet'], {
          timeout: 8000,
          encoding: 'buffer',
          windowsHide: true,
        });
        if (listResult.status === 0) {
          const buf = listResult.stdout as Buffer | null;
          let raw = '';
          if (buf && buf.length > 1) {
            const hasBOM = buf[0] === 0xff && buf[1] === 0xfe;
            const nullRatio =
              buf.reduce((acc, b, i) => (i % 2 === 1 && b === 0 ? acc + 1 : acc), 0) /
              Math.floor(buf.length / 2);
            raw =
              hasBOM || nullRatio > 0.3
                ? buf.toString('utf16le').replace(/^﻿/, '').replace(/\0/g, '')
                : buf.toString('utf8').replace(/\0/g, '');
          }
          const lines = raw
            .split(/\r?\n/)
            .map((l) => l.trim())
            .filter(Boolean);
          // Keep only distros that can actually run bash: appliance distros
          // like docker-desktop would otherwise count as a usable distro and
          // block the wizard's "install Ubuntu" step.
          distros = lines.filter((d) => isBashCapableDistro(d));
          if (!defaultDistro && distros.length > 0) defaultDistro = distros[0];
        }
      } catch {
        /* ignore */
      }

      try {
        const psResult = spawnSync('wsl', ['--list', '--running'], {
          timeout: 8000,
          encoding: 'buffer',
          windowsHide: true,
        });
        if (psResult.status === 0) {
          const buf = psResult.stdout as Buffer | null;
          let raw = '';
          if (buf && buf.length > 1) {
            const hasBOM = buf[0] === 0xff && buf[1] === 0xfe;
            const nullRatio =
              buf.reduce((acc, b, i) => (i % 2 === 1 && b === 0 ? acc + 1 : acc), 0) /
              Math.floor(buf.length / 2);
            raw =
              hasBOM || nullRatio > 0.3
                ? buf.toString('utf16le').replace(/^﻿/, '').replace(/\0/g, '')
                : buf.toString('utf8').replace(/\0/g, '');
          }
          const runningLines = raw
            .split(/\r?\n/)
            .map((l) => l.trim())
            .filter(Boolean);
          running = runningLines.length > 1;
        }
      } catch {
        /* ignore */
      }

      if (distros.length > 0) {
        // Probe every usable distro for a non-root user (hasNonRootUser):
        // a distro that can still execute as root does not prove first-launch
        // user creation has completed, but any initialized distro proves the
        // platform is usable.
        initialized = distros.some((d) => hasNonRootUser(d));
      }
    }

    featureState = classifyWslFeatureState({
      isWindows: true,
      featureWsl,
      featureVmp,
      featureReadOk: features.ok,
      wslInstalled: installed,
      usableDistros: distros,
      initialized,
    });

    return {
      isWindows: true,
      installed,
      version,
      distros,
      defaultDistro,
      running,
      featureState,
      rebootRequired,
    } satisfies WslCheckResult;
  }

  // -----------------------------------------------------------------------
  // WSL2 check & install — Windows only, runs in main process.
  // Must work BEFORE the bridge starts (during Setup Wizard).
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.WSL_CHECK, () => runWslCheckInternal());

  ipcMain.handle(IPC.WSL_INSTALL, () => {
    if (process.platform !== 'win32') {
      return { launched: false, error: 'Not on Windows' };
    }
    try {
      const child = spawnSync(
        'powershell.exe',
        ['-Command', 'Start-Process wsl -ArgumentList "--install" -Verb RunAs'],
        { timeout: 15000, encoding: 'utf8', shell: false }
      );
      if (child.error) {
        return { launched: false, error: child.error.message };
      }
      return { launched: true };
    } catch (e: any) {
      return { launched: false, error: e?.message ?? String(e) };
    }
  });

  // -----------------------------------------------------------------------
  // WSL one-click install & provision (new in #361)
  // Simplified flow: wsl --install handles everything → reboot once →
  // auto-provision user. Sends progress events to renderer.
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.WSL_INSTALL_AND_PROVISION, async (_event) => {
    const sender = _event.sender;
    const safeSend = (channel: string, data: unknown) => {
      if (!sender.isDestroyed()) sender.send(channel, data);
    };

    if (process.platform !== 'win32') {
      return {
        success: false,
        phase: 'error',
        error: 'Not on Windows',
        errorCode: 'NOT_WINDOWS',
        nextStep: '此功能仅适用于 Windows 系统',
      } satisfies WslInstallAndProvisionResult;
    }

    // ── Persisted state path ──────────────────────────────────────────
    const statePath = join(homedir(), '.miqi', 'wsl_install_state.json');

    function readState(): { phase: string; at: number } | null {
      try {
        return JSON.parse(readFileSync(statePath, 'utf8'));
      } catch {
        return null;
      }
    }
    function writeState(phase: string) {
      try {
        mkdirSync(join(homedir(), '.miqi'), { recursive: true });
        writeFileSync(statePath, JSON.stringify({ phase, at: Date.now() }));
      } catch {
        /* best-effort */
      }
    }
    function clearState() {
      try {
        require('fs').unlinkSync(statePath);
      } catch {
        /* ignore */
      }
    }

    try {
      // ── Step 1: Check ───────────────────────────────────────────────
      safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
        phase: 'checking',
        message: '正在检测 WSL 状态...',
      } satisfies WslInstallProgress);

      const check = runWslCheckInternal();
      const saved = readState();
      safeSend(IPC_EVENTS.WSL_CHECK_UPDATED, {});

      // ── Step 2: not-enabled → DISM enable features ──────────────────
      if (check.featureState === 'not-enabled') {
        safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
          phase: 'enabling_features',
          message: '正在启用 Windows 可选功能 (WSL + 虚拟机平台)...',
        } satisfies WslInstallProgress);

        // The elevated process runs Enable-WindowsOptionalFeature and reports
        // its own output/exit code through the trampoline files: a declined
        // UAC prompt used to be indistinguishable from a DISM failure here.
        const r = runElevated(
          {
            powershell: [
              '$ErrorActionPreference = "Continue"',
              'Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart',
              'Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart',
            ].join('\r\n'),
          },
          120000
        );

        if (r.kind === 'cancelled') {
          safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
            phase: 'error',
            message: '启用 Windows 功能被取消：管理员权限请求被拒绝',
            error: 'ELEVATION_CANCELLED',
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'ELEVATION_CANCELLED',
            error: '启用 Windows 功能被取消',
            nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
          } satisfies WslInstallAndProvisionResult;
        }

        // Verification requires a successful read with the WSL feature on.
        // VirtualMachinePlatform is intentionally not required: on machines
        // with VBS/Core Isolation, WMI keeps VMP reported as Disabled while
        // it is functional (observed in live testing) — gating on it would
        // recreate the false-failure bug this step was fixed for.
        const featuresAfter = readFeatureStates();
        if (!featuresAfter.ok || !featuresAfter.featureWsl) {
          // A failed DISM cmdlet leaves the exit code at 0, so the captured
          // output is the only place the real reason appears — fall back to the
          // generic text only when the elevated run produced nothing at all.
          const produced = r.kind === 'unknown' || r.exitCode !== 0 || r.output.trim().length > 0;
          const detail = produced ? summarizeElevated(r) : '功能状态未变化';
          safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
            phase: 'error',
            message: `启用 Windows 功能失败: ${detail}`,
            error: detail,
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'FEATURE_ENABLE_FAILED',
            error: '无法启用 Windows 可选功能',
            nextStep: '以管理员身份打开 PowerShell 并运行: wsl --install',
          } satisfies WslInstallAndProvisionResult;
        }

        writeState('features_enabled');

        safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
          phase: 'enabling_features',
          rebootRequired: true,
          message: 'Windows 功能已启用。需要重启系统，重启后 MiQroForge 将自动继续安装。',
        } satisfies WslInstallProgress);

        return {
          success: true,
          phase: 'enabling_features',
          rebootRequired: true,
          nextStep: '请重启系统，重新打开 MiQroForge 后向导将自动继续',
        } satisfies WslInstallAndProvisionResult;
      }

      // ── Step 3: not-installed → wsl --install --no-distribution ─────
      if (check.featureState === 'not-installed') {
        safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
          phase: 'installing_wsl',
          message: '正在安装 WSL2 内核...',
        } satisfies WslInstallProgress);

        const r = runElevated(
          {
            command: {
              file: 'wsl.exe',
              args: ['--install', '--no-distribution', '--no-launch'],
            },
          },
          300000
        );

        // Only ask the system when the elevated run itself was inconclusive:
        // exit code 0 already proves the install, and a declined UAC prompt
        // proves nothing was attempted, so probing would just add latency.
        const kernelPresent =
          r.kind === 'failed' || r.kind === 'unknown' ? wslKernelPresent() : false;
        const outcome = classifyKernelInstall(r, kernelPresent);

        if (outcome.status === 'cancelled') {
          safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
            phase: 'error',
            message: 'WSL2 内核安装被取消：管理员权限请求被拒绝',
            error: 'ELEVATION_CANCELLED',
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'ELEVATION_CANCELLED',
            error: 'WSL2 内核安装被取消',
            nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
          } satisfies WslInstallAndProvisionResult;
        }

        if (outcome.status === 'failed') {
          safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
            phase: 'error',
            message: `WSL2 内核安装失败: ${outcome.detail}`,
            error: outcome.detail,
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'KERNEL_INSTALL_FAILED',
            error: `WSL2 内核安装失败: ${outcome.detail}`,
            nextStep: '以管理员身份打开 PowerShell 并运行: wsl --install --no-distribution',
          } satisfies WslInstallAndProvisionResult;
        }

        writeState('kernel_installed');

        safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
          phase: 'installing_wsl',
          rebootRequired: true,
          message: 'WSL2 内核安装完成。需要重启系统以继续。',
        } satisfies WslInstallProgress);

        return {
          success: true,
          phase: 'installing_wsl',
          rebootRequired: true,
          nextStep: '请重启系统，重新打开 MiQroForge 后向导将自动继续',
        } satisfies WslInstallAndProvisionResult;
      }

      // ── Step 4: no distro → install Ubuntu ───────────────────────────
      if (check.distros.length === 0 && check.featureState !== 'ready') {
        safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
          phase: 'installing_distro',
          message: '正在安装 Ubuntu 发行版（可能需要几分钟）...',
        } satisfies WslInstallProgress);

        const r = runElevated(
          { command: { file: 'wsl.exe', args: ['--install', '-d', 'Ubuntu', '--no-launch'] } },
          300000
        );

        if (r.kind === 'cancelled') {
          safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
            phase: 'error',
            message: 'Ubuntu 安装被取消：管理员权限请求被拒绝',
            error: 'ELEVATION_CANCELLED',
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'ELEVATION_CANCELLED',
            error: 'Ubuntu 发行版安装被取消',
            nextStep: '重新点击「一键安装 WSL2」，并在弹出的 UAC 窗口中点击「是」',
          } satisfies WslInstallAndProvisionResult;
        }

        const postCheck = runWslCheckInternal();
        if (postCheck.distros.length === 0) {
          // The command succeeded but no distro is registered yet: as with the
          // kernel step that means "installed, reboot pending", not a failure.
          // Only a non-zero exit code is an install failure.
          if (r.kind === 'ok') {
            safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
              phase: 'installing_distro',
              rebootRequired: true,
              message: 'Ubuntu 已安装，需要重启系统以继续。',
            } satisfies WslInstallProgress);
            return {
              success: true,
              phase: 'installing_distro',
              rebootRequired: true,
              nextStep: '请重启系统，重新打开 MiQroForge 后向导将自动继续',
            } satisfies WslInstallAndProvisionResult;
          }

          const detail = summarizeElevated(r);
          safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
            phase: 'error',
            message: `Ubuntu 安装失败: ${detail}`,
            error: detail,
          } satisfies WslInstallProgress);
          return {
            success: false,
            phase: 'error',
            errorCode: 'DISTRO_INSTALL_FAILED',
            error: `Ubuntu 发行版安装失败: ${detail}`,
            nextStep: '以管理员身份打开 PowerShell 并运行: wsl --install -d Ubuntu',
          } satisfies WslInstallAndProvisionResult;
        }
        check.distros = postCheck.distros;
        check.defaultDistro = postCheck.defaultDistro;
      }

      // ── Done ────────────────────────────────────────────────────────
      clearState();
      safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
        phase: 'complete',
        message: 'WSL2 安装配置完成！',
      } satisfies WslInstallProgress);
      safeSend(IPC_EVENTS.WSL_CHECK_UPDATED, {});

      return { success: true, phase: 'complete' } satisfies WslInstallAndProvisionResult;
    } catch (e: any) {
      safeSend(IPC_EVENTS.WSL_INSTALL_PROGRESS, {
        phase: 'error',
        message: `出错: ${e?.message ?? e}`,
        error: e?.message ?? String(e),
      } satisfies WslInstallProgress);
      return {
        success: false,
        phase: 'error',
        error: e?.message ?? String(e),
        errorCode: 'UNKNOWN',
        nextStep: 'https://learn.microsoft.com/windows/wsl/install',
      } satisfies WslInstallAndProvisionResult;
    }
  });

  // -----------------------------------------------------------------------
  // WSL stats — memory / CPU / disk usage (Windows only)
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.WSL_GET_STATS, (_event, distroName?: string) => {
    if (process.platform !== 'win32') {
      return {
        ok: false,
        error: 'Not on Windows',
        distro: '',
        memory: { total_mb: 0, used_mb: 0, free_mb: 0, used_pct: 0 },
        cpu: { usage_pct: 0, cores: 0 },
        disk: { total_gb: 0, used_gb: 0, free_gb: 0, used_pct: 0 },
        uptime_sec: 0,
      };
    }

    let targetDistro = distroName ?? '';
    if (!targetDistro) {
      try {
        const st = spawnSync('wsl', ['--status'], {
          timeout: 5000,
          encoding: 'buffer',
          windowsHide: true,
        });
        if (st.status === 0) {
          const buf = st.stdout as Buffer | null;
          let out = '';
          if (buf && buf.length > 1) {
            const hasBOM = buf[0] === 0xff && buf[1] === 0xfe;
            out =
              hasBOM ||
              buf.reduce(
                (a: number, b: number, i: number) => (i % 2 === 1 && b === 0 ? a + 1 : a),
                0
              ) /
                Math.floor(buf.length / 2) >
                0.3
                ? buf.toString('utf16le').replace(/^﻿/, '').replace(/\0/g, '')
                : buf.toString('utf8').replace(/\0/g, '');
          }
          const m = out.match(/(?:默认分发|Default Distr?ibution)\s*[:：]\s*(.+)/i);
          if (m) targetDistro = m[1].trim();
        }
      } catch {
        /* ignore */
      }

      if (!targetDistro) {
        try {
          const lr = spawnSync('wsl', ['--list', '--running', '--quiet'], {
            timeout: 5000,
            encoding: 'buffer',
            windowsHide: true,
          });
          if (lr.status === 0) {
            const buf = lr.stdout as Buffer | null;
            let out = '';
            if (buf && buf.length > 1) {
              const hasBOM = buf[0] === 0xff && buf[1] === 0xfe;
              out =
                hasBOM ||
                buf.reduce(
                  (a: number, b: number, i: number) => (i % 2 === 1 && b === 0 ? a + 1 : a),
                  0
                ) /
                  Math.floor(buf.length / 2) >
                  0.3
                  ? buf.toString('utf16le').replace(/^﻿/, '').replace(/\0/g, '')
                  : buf.toString('utf8').replace(/\0/g, '');
            }
            const lines = out
              .split(/\r?\n/)
              .map((l: string) => l.trim())
              .filter(Boolean);
            if (lines.length > 1) targetDistro = lines[1];
          }
        } catch {
          /* ignore */
        }
      }

      if (!targetDistro) {
        return {
          ok: false,
          error: 'No WSL distro found. Install a distro first.',
          distro: '',
          memory: { total_mb: 0, used_mb: 0, free_mb: 0, used_pct: 0 },
          cpu: { usage_pct: 0, cores: 0 },
          disk: { total_gb: 0, used_gb: 0, free_gb: 0, used_pct: 0 },
          uptime_sec: 0,
        };
      }
    }

    const runInWsl = (cmd: string): { stdout: string; stderr: string; status: number | null } => {
      const r = spawnSync('wsl.exe', ['-d', targetDistro, '--', 'bash', '-c', cmd], {
        timeout: 15000,
        encoding: 'buffer',
        windowsHide: true,
      });
      const decodeBuf = (buf: Buffer | string | null): string => {
        if (!buf || buf.length === 0) return '';
        if (typeof buf === 'string') return buf.trim();
        if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) {
          return buf.toString('utf16le').replace(/^﻿/, '').replace(/\0/g, '').trim();
        }
        const nullRatio =
          buf.reduce((a, b, i) => (i % 2 === 1 && b === 0 ? a + 1 : a), 0) /
          Math.floor(buf.length / 2);
        if (nullRatio > 0.3) {
          return buf.toString('utf16le').replace(/\0/g, '').trim();
        }
        return buf.toString('utf8').replace(/\0/g, '').trim();
      };
      return {
        stdout: decodeBuf(r.stdout as Buffer | null),
        stderr: decodeBuf(r.stderr as Buffer | null),
        status: r.status,
      };
    };

    const fastScript = [
      'free -m 2>/dev/null | awk \'/^Mem:/ {print "MEM:" $2 ":" $3 ":" $4 ":" $7}\'',
      'nproc 2>/dev/null | awk \'{print "CORES:" $1}\'',
      'df --output=size,used,avail --block-size=1M / 2>/dev/null | awk \'NR==2 {printf "DISK:%d:%d:%d\\n", $1, $2, $3}\'',
      'awk \'{print "UPTIME:" $1}\' /proc/uptime 2>/dev/null',
    ].join('; ');
    const fastResult = runInWsl(fastScript);

    let memory = { total_mb: 0, used_mb: 0, free_mb: 0, used_pct: 0 };
    let cores = 0;
    let disk = { total_gb: 0, used_gb: 0, free_gb: 0, used_pct: 0 };
    let uptimeSec = 0;

    if (fastResult.stdout) {
      const lines = fastResult.stdout.split('\n');
      const memLine = lines.find((l) => l.startsWith('MEM:'));
      if (memLine) {
        const parts = memLine.substring(4).split(':');
        const total = parseInt(parts[0], 10);
        const used = parseInt(parts[1], 10);
        if (!Number.isNaN(total) && total > 0) {
          memory = {
            total_mb: total,
            used_mb: Number.isNaN(parseInt(parts[1], 10)) ? 0 : parseInt(parts[1], 10),
            free_mb: Number.isNaN(parseInt(parts[2], 10)) ? 0 : parseInt(parts[2], 10),
            used_pct: Math.round((used / total) * 100),
          };
        }
      }
      const coresLine = lines.find((l) => l.startsWith('CORES:'));
      if (coresLine) {
        cores = parseInt(coresLine.substring(7), 10) || 0;
      }
      const diskLine = lines.find((l) => l.startsWith('DISK:'));
      if (diskLine) {
        const parts = diskLine.substring(5).split(':');
        const totalMb = parseInt(parts[0] || '0', 10);
        const usedMb = parseInt(parts[1] || '0', 10);
        const freeMb = parseInt(parts[2] || '0', 10);
        if (!Number.isNaN(totalMb) && totalMb > 0 && usedMb <= totalMb) {
          disk = {
            total_gb: Math.round((totalMb / 1024) * 10) / 10,
            used_gb: Math.round((usedMb / 1024) * 10) / 10,
            free_gb: Math.round((freeMb / 1024) * 10) / 10,
            used_pct: Math.round((usedMb / totalMb) * 100),
          };
        }
      }
      const uptimeLine = lines.find((l) => l.startsWith('UPTIME:'));
      if (uptimeLine) {
        uptimeSec = parseFloat(uptimeLine.substring(8)) || 0;
      }
    }

    if (memory.total_mb === 0) {
      const memRaw = runInWsl('cat /proc/meminfo');
      if (memRaw.stdout) {
        const mt = memRaw.stdout.match(/MemTotal:\s*(\d+)/);
        const mu = memRaw.stdout.match(/MemAvailable:\s*(\d+)/);
        if (mt?.[1]) {
          const totalKb = parseInt(mt[1], 10);
          const availKb = mu?.[1] ? parseInt(mu[1], 10) : totalKb;
          memory = {
            total_mb: Math.round(totalKb / 1024),
            used_mb: Math.round((totalKb - availKb) / 1024),
            free_mb: Math.round(availKb / 1024),
            used_pct: Math.round(((totalKb - availKb) / totalKb) * 100),
          };
        }
      }
    }

    if (cores === 0) {
      const cpuInfo = runInWsl('grep -c ^processor /proc/cpuinfo 2>/dev/null');
      if (cpuInfo.stdout) cores = parseInt(cpuInfo.stdout, 10) || 0;
    }

    if (disk.total_gb === 0) {
      const dfRaw = runInWsl('df --output=size,used,avail --block-size=1M / 2>/dev/null');
      if (dfRaw.stdout) {
        const lines2 = dfRaw.stdout.split('\n').filter(Boolean);
        if (lines2.length >= 2) {
          const vals = lines2[1].trim().split(/\s+/).map(Number);
          const t = vals[0],
            u = vals[1],
            f = vals[2];
          if (!Number.isNaN(t) && t > 0 && !Number.isNaN(u) && u <= t) {
            disk = {
              total_gb: Math.round((t / 1024) * 10) / 10,
              used_gb: Math.round((u / 1024) * 10) / 10,
              free_gb: Math.round(((f || 0) / 1024) * 10) / 10,
              used_pct: Math.round((u / t) * 100),
            };
          }
        }
      }
    }

    if (uptimeSec === 0) {
      const upRaw = runInWsl('cat /proc/uptime');
      if (upRaw.stdout) uptimeSec = parseFloat(upRaw.stdout.trim().split(/\s+/)[0]) || 0;
    }

    let cpuUsagePct = 0;
    const cpuScript = [
      "S1=$(awk 'NR==1 {print $2, $3, $4, $5, $6, $7, $8}' /proc/stat)",
      'sleep 0.5',
      "S2=$(awk 'NR==1 {print $2, $3, $4, $5, $6, $7, $8}' /proc/stat)",
      'set -- $S1; T1=$(( $2 + $3 + $4 + $5 + $6 + $7 + $8 )); I1=$5',
      'set -- $S2; T2=$(( $2 + $3 + $4 + $5 + $6 + $7 + $8 )); I2=$5',
      'TD=$(( T2 - T1 )); ID=$(( I2 - I1 ))',
      'if [ $TD -gt 0 ]; then echo $(( 100 * (TD - ID) / TD )); else echo "0"; fi',
    ].join('; ');
    const cpuResult = runInWsl(cpuScript);
    if (cpuResult.status === 0 && cpuResult.stdout) {
      const pct = parseInt(cpuResult.stdout.trim(), 10);
      if (!Number.isNaN(pct)) {
        cpuUsagePct = Math.min(Math.max(pct, 0), 100);
      }
    }

    return {
      ok: true,
      distro: targetDistro,
      memory,
      cpu: { usage_pct: cpuUsagePct, cores: Math.max(cores, 0) },
      disk,
      uptime_sec: Math.round(uptimeSec),
    } satisfies WslStatsResult;
  });

  ipcMain.handle(IPC.WSL_EXPORT_DISTRO, async (_, distroName: string) => {
    if (process.platform !== 'win32') {
      return { exported: false, distro: null, tarPath: null, error: 'Not on Windows' };
    }
    if (!distroName) {
      return { exported: false, distro: null, tarPath: null, error: 'No distro to export' };
    }
    try {
      const exportDir = process.env['LOCALAPPDATA'] || process.env['APPDATA'] || '';
      if (!exportDir) {
        return { exported: false, distro: null, tarPath: null, error: 'LOCALAPPDATA not found' };
      }
      const tarPath = join(exportDir, `${distroName}.tar`);
      const child = spawnSync(
        'wsl.exe',
        ['-d', distroName, '--export', distroName, '-t', tarPath],
        { timeout: 120000, encoding: 'utf8', windowsHide: true }
      );
      if (child.status !== 0) {
        return {
          exported: false,
          distro: null,
          tarPath: null,
          error: child.stderr || 'Export failed',
        };
      }
      return { exported: true, distro: distroName, tarPath, error: null };
    } catch (e: any) {
      return { exported: false, distro: null, tarPath: null, error: e?.message ?? String(e) };
    }
  });

  ipcMain.handle(
    IPC.WSL_IMPORT_DISTRO,
    async (_, options: { tarPath: string; distroName: string }) => {
      if (process.platform !== 'win32') {
        return { imported: false, distro: null, installLocation: null, error: 'Not on Windows' };
      }
      const { tarPath, distroName } = options;
      if (!tarPath) {
        return {
          imported: false,
          distro: null,
          installLocation: null,
          error: 'No tar file path provided',
        };
      }
      if (!distroName) {
        return {
          imported: false,
          distro: null,
          installLocation: null,
          error: 'No distro name provided',
        };
      }
      try {
        const installLocation = join(
          process.env['LOCALAPPDATA'] || process.env['APPDATA'] || '',
          'MiQi Sandbox'
        );
        const child = spawnSync(
          'wsl.exe',
          ['--import', distroName, installLocation, tarPath, '--version', '2'],
          { timeout: 120000, encoding: 'utf8', windowsHide: true }
        );
        if (child.status !== 0) {
          return {
            imported: false,
            distro: null,
            installLocation: null,
            error: child.stderr || 'Import failed',
          };
        }
        return { imported: true, distroName, installLocation, error: null };
      } catch (e: any) {
        return {
          imported: false,
          distro: null,
          installLocation: null,
          error: e?.message ?? String(e),
        };
      }
    }
  );

  ipcMain.handle(IPC.SANDBOX_SET_ENABLED, async (_event, enabled: boolean) => {
    const res = await bridge.send('sandbox.setEnabled', { enabled });
    const data = res as Record<string, unknown> | undefined;
    if (data?.enabled === true) {
      bridge.sandboxAvailable = data?.initializing === true ? false : true;
    } else if (data?.enabled === false) {
      bridge.sandboxAvailable = false;
    }
    bridge.emitState();
    return res;
  });

  // #854: allow_system_installs runtime toggle (no restart)
  ipcMain.handle(IPC.SANDBOX_SET_ALLOW_SYSTEM_INSTALLS, async (_event, enabled: boolean) => {
    return bridge.send('sandbox.setAllowSystemInstalls', { enabled });
  });

  ipcMain.handle(IPC.DIALOG_OPEN_FILE, async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openFile', 'openDirectory'],
    });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });

  ipcMain.handle(IPC.DIALOG_OPEN_DIRECTORY, async () => {
    const result = await dialog.showOpenDialog({
      properties: ['openDirectory'],
    });
    return result.canceled ? null : (result.filePaths[0] ?? null);
  });

  ipcMain.handle(IPC.SESSIONS_LIST_RECENT_WORKSPACES, async () => {
    const result = await bridge.sendSafe('sessions.list_recent_workspaces');
    if (result == null) return { workspaces: [] };
    return result;
  });

  // -----------------------------------------------------------------------
  // Approvals
  // -----------------------------------------------------------------------
  ipcMain.handle('approvals:list', async () => {
    return bridge.sendSafe('approvals.list');
  });

  ipcMain.handle('approvals:resolve', async (_event, payload: unknown) => {
    const p = payload as { approval_id: string; decision: string };
    return bridge.send('approvals.resolve', p as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // User input (issue #646: ask_user_confirm_card)
  // -----------------------------------------------------------------------
  ipcMain.handle('userInput:resolve', async (_event, payload: unknown) => {
    const p = payload as { input_id: string; choice_id: string; choice_label: string };
    return bridge.send('userInput.resolve', p as Record<string, unknown>);
  });

  ipcMain.handle('approvals:clear_permanent', async (_event, payload: unknown) => {
    const p = (payload ?? {}) as { pattern?: string };
    return bridge.send('approvals.clear_permanent', p as Record<string, unknown>);
  });

  ipcMain.handle('approvals:add_permanent', async (_event, payload: unknown) => {
    const p = payload as { pattern: string };
    return bridge.send('approvals.add_permanent', p as Record<string, unknown>);
  });

  ipcMain.handle('approvals:history', async (_event, payload: unknown) => {
    const p = (payload ?? {}) as { limit?: number };
    return bridge.sendSafe('approvals.history', p as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // Cron
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.CRON_LIST, async () => {
    return bridge.sendSafe('cron.list');
  });

  ipcMain.handle(IPC.CRON_CREATE, async (_event, payload: unknown) => {
    const input = CronCreateInput.parse(payload);
    return bridge.send('cron.create', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.CRON_UPDATE, async (_event, payload: unknown) => {
    const input = CronUpdateInput.parse(payload);
    return bridge.send('cron.update', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.CRON_DELETE, async (_event, payload: unknown) => {
    const input = CronDeleteInput.parse(payload);
    return bridge.send('cron.delete', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.CRON_TOGGLE, async (_event, payload: unknown) => {
    const input = CronToggleInput.parse(payload);
    return bridge.send('cron.toggle', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.CRON_RUN, async (_event, payload: unknown) => {
    const input = CronRunInput.parse(payload);
    return bridge.send('cron.run', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.CRON_RUNS, async (_event, payload: unknown) => {
    const input = CronRunsInput.parse(payload ?? {});
    return bridge.sendSafe('cron.runs', input as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // Memory
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.MEMORY_LIST, async () => {
    return bridge.sendSafe('memory.list');
  });

  ipcMain.handle(IPC.MEMORY_GET, async (_event, payload: unknown) => {
    const input = MemoryGetInput.parse(payload);
    return bridge.sendSafe('memory.get', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.MEMORY_UPDATE, async (_event, payload: unknown) => {
    const input = MemoryUpdateInput.parse(payload);
    return bridge.send('memory.update', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.MEMORY_LESSONS, async () => {
    return bridge.sendSafe('memory.lessons');
  });

  ipcMain.handle(IPC.MEMORY_LESSON_UNLEARN, async (_event, payload: unknown) => {
    const input = MemoryLessonUnlearnInput.parse(payload);
    return bridge.send('memory.lesson.unlearn', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.MEMORY_DELETE, async (_event, payload: unknown) => {
    const p = payload as { path: string };
    return bridge.send('memory.delete', p as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // Experience
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.EXPERIENCE_LIST, async (_event, payload: unknown) => {
    return bridge.sendSafe('experience:list', payload as Record<string, unknown>);
  });

  ipcMain.handle(IPC.EXPERIENCE_DELETE, async (_event, payload: unknown) => {
    return bridge.send('experience:delete', payload as Record<string, unknown>);
  });

  ipcMain.handle(IPC.EXPERIENCE_TOGGLE, async (_event, payload: unknown) => {
    return bridge.send('experience:toggle', payload as Record<string, unknown>);
  });

  ipcMain.handle(IPC.EXPERIENCE_SEARCH, async (_event, payload: unknown) => {
    return bridge.sendSafe('experience:search', payload as Record<string, unknown>);
  });

  // -----------------------------------------------------------------------
  // Skills
  // -----------------------------------------------------------------------
  ipcMain.handle(IPC.SKILLS_LIST, async () => {
    return bridge.sendSafe('skills.list');
  });

  ipcMain.handle(IPC.SKILLS_GET, async (_event, payload: unknown) => {
    const input = SkillsGetInput.parse(payload);
    return bridge.sendSafe('skills.get', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.SKILLS_OPEN_FOLDER, async (_event, payload: unknown) => {
    const p = payload as { name: string };
    return bridge.send('skills.open_folder', p as Record<string, unknown>);
  });

  ipcMain.handle(IPC.SKILLS_CREATE, async (_event, payload: unknown) => {
    const p = payload as { name: string; description?: string };
    return bridge.send('skills.create', p as Record<string, unknown>);
  });

  ipcMain.handle(IPC.SKILLS_UPLOAD, async (_event, payload: unknown) => {
    const p = payload as { name: string; content: string };
    return bridge.send('skills.upload', p as Record<string, unknown>);
  });

  ipcMain.handle(IPC.SKILLS_DELETE, async (_event, payload: unknown) => {
    const p = payload as { name: string };
    return bridge.send('skills.delete', p as Record<string, unknown>);
  });

  // -- Files (Workspace Editor) ------------------------------------------------
  ipcMain.handle(IPC.FILES_TREE, async () => {
    return bridge.sendSafe('files.tree');
  });

  ipcMain.handle(IPC.FILES_READ, async (_event, payload: unknown) => {
    const input = FilesReadInput.parse(payload);
    return bridge.sendSafe('files.read', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.FILES_WRITE, async (_event, payload: unknown) => {
    const input = FilesWriteInput.parse(payload);
    return bridge.send('files.write', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.FILES_DELETE, async (_event, payload: unknown) => {
    const p = payload as { path: string };
    return bridge.send('files.delete', p as Record<string, unknown>);
  });

  ipcMain.handle(IPC.FILES_DIFF, async (_event, payload: unknown) => {
    const p = payload as { path: string; session_key?: string };
    return bridge.sendSafe('files.diff', p as Record<string, unknown>);
  });

  ipcMain.handle(IPC.FILES_REVERT, async (_event, payload: unknown) => {
    const p = payload as { path: string; session_key?: string };
    return bridge.send('files.revert', p as Record<string, unknown>);
  });

  ipcMain.handle(IPC.FILES_ACCEPT, async (_event, payload: unknown) => {
    const p = payload as { path: string; session_key?: string };
    try {
      return await bridge.send('files.accept', p as Record<string, unknown>);
    } catch (err: any) {
      return { accepted: false, path: p.path, error: err?.message ?? String(err) };
    }
  });

  // #877: preview「下载/另存为」— native save dialog + write bytes.
  ipcMain.handle(IPC.FILES_SAVE_AS, async (event, payload: unknown) => {
    const input = FilesSaveAsInput.parse(payload);
    const win = electron.BrowserWindow.fromWebContents(event.sender);
    if (!win) return { saved: false, error: 'no window' };
    const safe = input.default_name.replace(/[\\/:*?"<>|]/g, '_').slice(-120) || 'download';
    const picked = await dialog.showSaveDialog(win, {
      title: '另存为',
      defaultPath: safe,
    });
    if (picked.canceled || !picked.filePath) {
      return { saved: false, canceled: true };
    }
    try {
      writeFileSync(picked.filePath, Buffer.from(input.data_base64, 'base64'));
      return { saved: true, path: picked.filePath };
    } catch (err) {
      return { saved: false, error: String(err) };
    }
  });

  // -- WSL helpers (async — must not block the Electron main thread) -----

  const execFileAsync = promisify(execFile);

  /** promisified spawn with stdin input — execFile's options don't accept
   *  `input`, but the WSL search probe feeds a bash script via stdin. */
  const spawnWithInput = (
    cmd: string,
    args: string[],
    opts: { input?: string; timeout?: number } = {}
  ): Promise<{ stdout: string }> =>
    new Promise((resolve, reject) => {
      const child = spawn(cmd, args, { windowsHide: true });
      let stdout = '';
      const timer = setTimeout(() => {
        child.kill();
        reject(new Error(`spawn ${cmd} timed out`));
      }, opts.timeout ?? 10_000);
      child.stdout?.on('data', (d: Buffer) => {
        stdout += d.toString('utf8');
      });
      // Keep the stderr pipe drained so a chatty probe can't deadlock the child.
      child.stderr?.on('data', () => {});
      child.on('error', (err) => {
        clearTimeout(timer);
        reject(err);
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        if (code === 0) resolve({ stdout });
        else reject(new Error(`spawn ${cmd} exited ${code}`));
      });
      if (opts.input) child.stdin.write(opts.input);
      child.stdin.end();
    });

  async function findFileInWsl(
    relPath: string
  ): Promise<{ wslAbsPath: string; distro: string } | null> {
    const execOpts = { timeout: 10000, encoding: 'utf8' as const, windowsHide: true };
    // List WSL distros
    let distros: string[] = [];
    try {
      const { stdout } = await execFileAsync('wsl.exe', ['--list', '--quiet'], execOpts);
      distros = stdout
        .split(/\r?\n/)
        .map((d: string) => d.trim())
        .filter(Boolean);
    } catch {
      return null;
    }

    // Pass relPath inline as a positional argument to the bash script so
    // WSL interop does not need to import it from the Windows environment.
    const escapedRelPath = shellEscape(relPath);
    const searchScript =
      `RP=$'${escapedRelPath}'\n` +
      `for d in /tmp/miqi-sandboxes/*/home/miqi/workspace/; do\n` +
      `  if [ -f "$d$RP" ]; then echo "$d$RP"; exit 0; fi\n` +
      `  for s in "$d"sessions/*/files/; do\n` +
      `    if [ -f "$s$RP" ]; then echo "$s$RP"; exit 0; fi\n` +
      `  done\n` +
      `done\n` +
      // Also search the WSL home workspace (where Python tools write files directly)
      `ws="$HOME/.miqi/workspace"\n` +
      `if [ -f "$ws/$RP" ]; then echo "$ws/$RP"; exit 0; fi\n` +
      `for s in "$ws"/sessions/*/files/; do\n` +
      `  if [ -f "$s/$RP" ]; then echo "$s/$RP"; exit 0; fi\n` +
      `done\n` +
      `exit 1\n`;

    for (const distro of distros) {
      try {
        const { stdout } = await spawnWithInput('wsl.exe', ['-d', distro, '--', 'bash'], {
          input: searchScript,
          timeout: execOpts.timeout,
        });
        if (stdout?.trim()) return { wslAbsPath: stdout.trim(), distro };
      } catch {
        continue;
      }
    }
    return null;
  }

  /** Escape a string for safe embedding in a single-quoted bash argument. */
  const shellEscape = (s: string) => s.replace(/'/g, "'\\''");

  async function copyFromWsl(
    wslAbsPath: string,
    distro: string,
    hostTarget: string
  ): Promise<boolean> {
    const wslTarget = hostTarget
      .replace(/^([A-Z]):/, (_, d: string) => `/mnt/${d.toLowerCase()}`)
      .replace(/\\/g, '/');
    const wslTargetDir = wslTarget.replace(/\/[^/]+$/, '');
    try {
      // Use $'...' quoting so embedded single quotes are safely escaped.
      const escapedDir = shellEscape(wslTargetDir);
      const escapedSrc = shellEscape(wslAbsPath);
      const escapedDst = shellEscape(wslTarget);
      await execFileAsync(
        'wsl.exe',
        [
          '-d',
          distro,
          '--',
          'bash',
          '-c',
          `mkdir -p $'${escapedDir}' && cp $'${escapedSrc}' $'${escapedDst}'`,
        ],
        { timeout: 10000, encoding: 'utf8', windowsHide: true }
      );
      return existsSync(hostTarget);
    } catch {
      return false;
    }
  }

  // -- Open file with system default application -------------------------
  ipcMain.handle(IPC.FILES_OPEN_EXTERNAL, async (_event, payload: unknown) => {
    const p = payload as { path: string };
    const raw = p.path;
    const absolutePath = resolveWorkspacePath(raw);

    // On Windows the file may live inside a WSL sandbox.
    const candidates: string[] = [absolutePath];

    if (process.platform === 'win32' && !existsSync(absolutePath)) {
      // Extract workspace-relative path for sandbox search
      const relPath = raw.replace(/\\/g, '/').replace(/^\/home\/miqi\/workspace\//, '');

      // Security: reject absolute or traversal-containing paths from the
      // renderer before they reach the WSL search script or the host copy.
      // An attacker could send "../../../.ssh/id_rsa" to exfiltrate files.
      const isSafe =
        !relPath.startsWith('/') &&
        !relPath.startsWith('\\') &&
        !/^[A-Za-z]:[/\\]/.test(relPath) &&
        !relPath.includes('..');
      if (!isSafe) {
        return { opened: false, path: raw, error: 'Unsafe path rejected' };
      }

      try {
        const found = await findFileInWsl(relPath);
        if (found) {
          const hostTarget = join(getWorkspacePath(), relPath);
          const copied = await copyFromWsl(found.wslAbsPath, found.distro, hostTarget);
          if (copied) candidates.push(hostTarget);
          candidates.push(`\\\\wsl$\\${found.distro}\\${found.wslAbsPath.replace(/\//g, '\\')}`);
        }
      } catch {
        // wsl.exe unavailable — stay with host workspace candidate only
      }
    }

    // Try each candidate until one works
    let opened = false;
    let lastError = '';
    for (const candidate of candidates) {
      try {
        if (!existsSync(candidate)) continue;
        // WSL UNC paths live inside the sandbox distro, not on the host — skip
        // the host-workspace canonical check (relPath was vetted above).
        const isWslUnc = candidate.startsWith('\\\\wsl$');
        if (!isWslUnc && !isWithinCanonicalWorkspace(candidate, getWorkspacePath())) {
          continue;
        }
        const error = await shell.openPath(candidate);
        if (!error) {
          opened = true;
          break;
        }
        lastError = error;
      } catch (e: any) {
        lastError = e?.message ?? String(e);
      }
    }

    if (!opened) {
      return {
        opened: false,
        path: raw,
        error: lastError || `File not found: ${raw}`,
      };
    }
    return { opened: true, path: raw };
  });

  // -- Open an HTML string in the system default browser -------------------
  // Write the content to a temp .html file (so relative CSS/scripts resolve
  // normally) and hand it to the OS default handler — the browser for .html.
  ipcMain.handle(IPC.HTML_OPEN_IN_BROWSER, async (_event, payload: unknown) => {
    const p = payload as { html: string };
    const html = typeof p?.html === 'string' ? p.html : '';
    // Unpredictable name + owner-only permissions: the file holds AI-generated
    // HTML and is written to the shared temp dir. Delete shortly after the
    // browser has had a chance to read it.
    const tmpPath = join(tmpdir(), `miqi-preview-${randomUUID()}.html`);
    try {
      writeFileSync(tmpPath, html, { encoding: 'utf8', mode: 0o600 });
      const error = await shell.openPath(tmpPath);
      setTimeout(() => {
        try {
          unlinkSync(tmpPath);
        } catch {
          /* already gone */
        }
      }, 60_000);
      return error ? { opened: false, path: tmpPath, error } : { opened: true, path: tmpPath };
    } catch (e: any) {
      return { opened: false, path: tmpPath, error: e?.message ?? String(e) };
    }
  });

  // -- Reveal file in system file manager (Explorer / Finder) ------------
  ipcMain.handle(IPC.FILES_OPEN_CONTAINING_FOLDER, async (_event, payload: unknown) => {
    const p = payload as { path: string };
    const raw = p.path;
    // Session metadata may store workspace as a string (Path str) —
    // resolve "Path('...')" wrapper to a plain path string before opening.
    const clean = raw.replace(/^Path\(['"]/, '').replace(/['"]\)$/, '');
    // Security: route through resolveWorkspacePath so the workspace-containment
    // check always applies.  A previous fast path here (isAbsolute(clean) &&
    // existsSync(clean) → showItemInFolder) skipped that check entirely, letting
    // the renderer reveal any host directory (security regression #955).
    const absolutePath = resolveWorkspacePath(clean);
    try {
      if (!existsSync(absolutePath)) {
        return { revealed: false, path: raw, error: `File not found: ${absolutePath}` };
      }
      // Follow symlinks/junctions so a link pointing outside the workspace can't
      // reveal a host directory through the lexical containment check (#955).
      if (!isWithinCanonicalWorkspace(absolutePath, getWorkspacePath())) {
        return { revealed: false, path: raw, error: `Path outside workspace: ${raw}` };
      }
      shell.showItemInFolder(absolutePath);
      return { revealed: true, path: raw };
    } catch (e: any) {
      return { revealed: false, path: raw, error: e?.message ?? String(e) };
    }
  });

  // #667: 直接下载（论文 PDF 等）——webContents.downloadURL 走 Electron 下载器
  // 按发起方 webContents 关联 will-download（session 是全局的，其他窗口/
  // 并发下载会串文件名），等 DownloadItem 的 done 事件后按真实结果返回，
  // 避免渲染层把取消/失败当作成功（CodeRabbit #668 review）。
  ipcMain.handle(IPC.DOWNLOADS_DOWNLOAD, async (event, payload: unknown) => {
    const p = payload as { url?: string; filename?: string };
    const url = p?.url ?? '';
    if (!/^https?:\/\//i.test(url)) return { ok: false, error: 'invalid url' };
    const win = electron.BrowserWindow.fromWebContents(event.sender);
    if (!win) return { ok: false, error: 'no window' };
    const safe = p.filename ? p.filename.replace(/[\\/:*?"<>|]/g, '_').slice(0, 120) : undefined;
    // #696: 保存位置由用户选择（保存对话框），不再固定 Downloads 目录
    const picked = await electron.dialog.showSaveDialog(win, {
      title: '保存文件',
      defaultPath: join(electron.app.getPath('downloads'), safe || 'download.pdf'),
      filters: [{ name: 'PDF', extensions: ['pdf'] }],
    });
    if (picked.canceled || !picked.filePath) {
      return { ok: false, error: 'cancelled' };
    }
    const session = win.webContents.session;
    return await new Promise<{ ok: boolean; error?: string; savePath?: string }>((resolve) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | null = null;
      const finish = (ok: boolean, error?: string) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        session.removeListener('will-download', onWillDownload);
        resolve(ok ? { ok, savePath: picked.filePath } : { ok, error });
      };
      const onWillDownload = (
        _e: Electron.Event,
        item: Electron.DownloadItem,
        wc: Electron.WebContents
      ) => {
        // Only the initiating webContents's downloads get our filename.
        if (wc !== win.webContents) return;
        try {
          item.setSavePath(picked.filePath);
        } catch {
          // fall back to default download path
        }
        item.once('done', (_ev, state) => {
          finish(state === 'completed', state === 'completed' ? undefined : `download ${state}`);
        });
      };
      session.on('will-download', onWillDownload);
      win.webContents.downloadURL(url);
      // Guard: if will-download never fires (e.g. the URL navigates instead
      // of downloading), don't leave the renderer hanging forever.
      timer = setTimeout(() => {
        finish(false, 'download did not start');
      }, 60_000);
    });
  });

  // -- Document parsing -----------------------------------------------------
  ipcMain.handle(IPC.DOCUMENTS_PARSE, async (_event, payload: unknown) => {
    return bridge.sendSafe('documents.parse', payload as Record<string, unknown>);
  });

  // -- MCP -----------------------------------------------------------------
  ipcMain.handle(IPC.MCP_LIST, async () => {
    return bridge.sendSafe('mcp.list');
  });

  ipcMain.handle(IPC.MCP_UPSERT, async (_event, payload: unknown) => {
    const input = McpUpsertInput.parse(payload);
    return bridge.send('mcp.upsert', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.MCP_DELETE, async (_event, payload: unknown) => {
    const input = McpDeleteInput.parse(payload);
    return bridge.send('mcp.delete', input as Record<string, unknown>);
  });

  ipcMain.handle(IPC.CONFIG_WRITE_INITIAL, (_event, payload: unknown) => {
    // #835 收口：provider 凭据与默认模型只允许经后端收口接口写入
    //（providers.activate / config.update）。此通道原先可绕过全部校验
    // 直写 provider_name/api_key/api_base/model 到 config.json（#929
    // review），现在只保留 workspace 初始化。
    const { workspace } = payload as {
      workspace?: string | null;
    };
    const configDir = getConfigDir();
    const configPath = getConfigPath();

    let existing: Record<string, unknown> = {};
    try {
      if (existsSync(configPath)) {
        existing = JSON.parse(readFileSync(configPath, 'utf8'));
      }
    } catch {
      // Start fresh
    }

    if (workspace) {
      const agents = (existing['agents'] as Record<string, unknown> | undefined) ?? {};
      const defaults = (agents['defaults'] as Record<string, unknown> | undefined) ?? {};
      defaults['workspace'] = workspace;
      agents['defaults'] = defaults;
      existing['agents'] = agents;
    }

    mkdirSync(configDir, { recursive: true });
    writeFileSync(configPath, JSON.stringify(existing, null, 2), 'utf8');

    return { saved: true, path: configPath };
  });

  // ---------------------------------------------------------------------------
  // Agents (Phase 2)
  // ---------------------------------------------------------------------------
  ipcMain.handle(IPC.AGENT_LIST, async (_event, payload: unknown) => {
    const p = (payload ?? {}) as { session_key?: string };
    return bridge.sendSafe('agent.list', { session_key: p.session_key ?? 'desktop:default' });
  });

  ipcMain.handle(IPC.AGENT_SPAWN, async (_event, payload: unknown) => {
    const input = AgentSpawnInput.parse(payload);
    // Callers may pass an explicit session_key (the Python bridge rejects
    // agent.spawn without a resolvable session — UNAUTHORIZED).
    return bridge.sendSafe('agent.spawn', {
      agent_type: input.agent_type,
      task: input.task,
      label: input.label,
      session_key: input.session_key ?? 'desktop:default',
    });
  });

  ipcMain.handle(IPC.AGENT_KILL, async (_event, payload: unknown) => {
    const { agent_id, session_key } = payload as {
      agent_id: string;
      session_key?: string;
    };
    return bridge.sendSafe('agent.kill', {
      agent_id,
      session_key: session_key ?? 'desktop:default',
    });
  });

  // ---------------------------------------------------------------------------
  // Plan (Phase 2)
  // ---------------------------------------------------------------------------
  ipcMain.handle(IPC.PLAN_GET, async (_event, payload: unknown) => {
    const { thread_id } = payload as { thread_id: string };
    return bridge.sendSafe('plan.get', { thread_id });
  });

  // ---------------------------------------------------------------------------
  // Permissions (Phase 3)
  // ---------------------------------------------------------------------------
  ipcMain.handle(IPC.PERMISSIONS_GET, async () => {
    return bridge.sendSafe('permissions.get');
  });

  ipcMain.handle(IPC.PERMISSIONS_UPDATE, async (_event, payload: unknown) => {
    const input = PermissionsUpdateInput.parse(payload);
    return bridge.sendSafe('permissions.update', { config: input });
  });

  ipcMain.handle(IPC.PERMISSIONS_PERMANENT_ADD, async (_event, payload: unknown) => {
    const { pattern } = payload as { pattern: string };
    return bridge.sendSafe('permissions.permanent.add', { pattern });
  });

  ipcMain.handle(IPC.PERMISSIONS_PERMANENT_REMOVE, async (_event, payload: unknown) => {
    const { pattern } = payload as { pattern: string };
    return bridge.sendSafe('permissions.permanent.remove', { pattern });
  });

  // ---------------------------------------------------------------------------
  // Plugins (Phase 4)
  // ---------------------------------------------------------------------------
  ipcMain.handle(IPC.PLUGINS_LIST, async () => {
    return bridge.sendSafe('plugins.list');
  });

  ipcMain.handle(IPC.PLUGINS_INSTALL, async (_event, payload: unknown) => {
    const { name } = payload as { name: string };
    return bridge.sendSafe('plugins.install', { name });
  });

  ipcMain.handle(IPC.PLUGINS_UNINSTALL, async (_event, payload: unknown) => {
    const { name } = payload as { name: string };
    return bridge.sendSafe('plugins.uninstall', { name });
  });

  ipcMain.handle(IPC.PLUGINS_TOGGLE, async (_event, payload: unknown) => {
    const { name, enabled } = payload as { name: string; enabled: boolean };
    return bridge.sendSafe('plugins.toggle', { name, enabled });
  });

  // -- Feedback --------------------------------------------------------------
  ipcMain.handle(IPC.FEEDBACK_SUBMIT, async (_event, payload: unknown) => {
    const input = FeedbackSubmitInput.parse(payload);
    return bridge.send('feedback:submit', input as unknown as Record<string, unknown>);
  });

  ipcMain.handle(IPC.FEEDBACK_LIST, async (_event, payload: unknown) => {
    return bridge.sendSafe('feedback:list', payload as Record<string, unknown>);
  });

  // -- Threads (Phase 36+) --------------------------------------------------
  // thread/start is a simple request-response method (not streaming).
  // Do NOT pass an onEvent callback — it would put the bridge in streaming
  // mode where the Promise only resolves on terminal events (final/error/
  // aborted), which thread/start never sends.
  ipcMain.handle(IPC.THREAD_START, async (_event, payload: unknown) => {
    const input = ThreadStartInput.parse(payload);
    return bridge.send('thread/start', {
      title: input.title,
      sessionKey: input.session_key,
      threadId: input.thread_id,
    });
  });

  ipcMain.handle(IPC.THREAD_LIST, async (_event, payload: unknown) => {
    const p = (payload ?? {}) as { session_key?: string };
    return bridge.sendSafe('thread/list', { sessionId: p.session_key });
  });

  ipcMain.handle(IPC.THREAD_READ, async (_event, payload: unknown) => {
    const input = ThreadReadInput.parse(payload);
    return bridge.sendSafe('thread/read', {
      threadId: input.thread_id,
      sessionId: input.session_key,
    });
  });

  ipcMain.handle(IPC.THREAD_NAME_SET, async (_event, payload: unknown) => {
    const input = ThreadNameSetInput.parse(payload);
    return bridge.send('thread/name/set', {
      threadId: input.thread_id,
      name: input.name,
      sessionId: input.session_key,
    });
  });

  // -- Turns (Phase 37+) ----------------------------------------------------
  ipcMain.handle(IPC.TURN_START, async (_event, payload: unknown) => {
    const input = TurnStartInput.parse(payload);
    const sender = _event.sender;
    const safeSend = (channel: string, data: unknown) => {
      if (!sender.isDestroyed()) sender.send(channel, data);
    };
    return bridge.send(
      'turn/start',
      {
        threadId: input.thread_id,
        input: [{ type: 'message', content: input.content }],
        model: input.model,
        effort: input.effort,
      },
      (type: string, data: unknown) => {
        if (type === 'error') {
          safeSend('chat:error', data);
        } else if (
          type === 'turn/completed' ||
          type === 'turn/started' ||
          type === 'item/started' ||
          type === 'item/completed'
        ) {
          safeSend(`turn:event`, data);
        } else if (type === 'item/commandExecution/outputDelta') {
          // Exec real-time delta — stream to chat:progress for inline
          // terminal output. Drop empty deltas.
          const d = data as Record<string, unknown> | undefined;
          if (d && typeof d.delta === 'string' && (d.delta as string).length > 0) {
            safeSend('chat:progress', {
              text: '',
              tool_hint: true,
              stream: d.stream,
              delta: d.delta,
              tool_call_id: d.itemId?.toString().split(':').pop(),
            });
          }
        } else if (type === 'approval_request') {
          safeSend('approval:request', data);
        } else if (type === 'approval_cleared') {
          safeSend('approval:cleared', data);
        } else if (type === 'user_input_requested') {
          safeSend('userInput:request', data);
        } else if (type === 'user_input_resolved') {
          safeSend('userInput:resolved', data);
        } else {
          safeSend('chat:progress', data);
        }
      }
    );
  });

  ipcMain.handle(IPC.TURN_INTERRUPT, async (_event, payload: unknown) => {
    const input = TurnInterruptInput.parse(payload);
    return bridge.send('turn/interrupt', {
      threadId: input.thread_id,
      turnId: input.turn_id,
    });
  });

  // MiQroForge 平台 OAuth2 登录 (issue #726) — 主进程本地处理，不依赖 bridge。
  registerQraftIpcHandlers();

  // 隐私协议拒绝退出 (#837)：macOS 上 window.close() 不终止应用，
  // 统一由主进程 app.quit() 收尾。
  ipcMain.handle(IPC.APP_QUIT, () => {
    app.quit();
    return { ok: true };
  });

  // 空会话回到欢迎态后把窗口带回前台（renderer 触发）。best-effort：窗口未聚焦
  // 则 restore/show/focus；仍不聚焦则 moveTop 重试。{ hard: true } 表示 renderer
  // 检测到 document.hasFocus()==false（window.confirm 模态关闭后页面焦点未交还）——
  // 此时窗口即便已 OS 聚焦，win.focus() 也不产生激活变化，须 blur→focus 逼
  // Chromium 重新下发页面焦点，否则键盘事件被吞、输入框点了没反应。
  ipcMain.handle(IPC.APP_FOCUS, (_event, opts) => {
    const win = electron.BrowserWindow.fromWebContents(_event.sender);
    if (!win) return { ok: false };
    const hard = !!opts && typeof opts === 'object' && (opts as { hard?: boolean }).hard === true;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
    if (hard && !win.isDestroyed()) {
      win.blur();
      win.focus();
      if (win.isMinimized()) win.restore();
    } else if (!win.isFocused() && !win.isDestroyed()) {
      win.moveTop();
      win.focus();
    }
    return { ok: true, focused: !win.isDestroyed() && win.isFocused() };
  });

  // 资产面板推开聊天区时把窗口加宽(extra≈面板宽):聊天列是 flex-1,新增宽度全归它,
  // 聊天区因此不挤压;关闭(extra=0)还原到开面板前宽度。逐次记录左右实际扩展量用于
  // 精确还原,最大化/全屏/不可调大小或屏幕已无剩余空间时跳过。记录以窗口为键,
  // 窗口销毁后自然归零(下次开面板以当时宽度为基线)。
  const panelExtraByWin = new WeakMap<
    BrowserWindow,
    { extra: number; left: number; right: number }
  >();
  ipcMain.handle(IPC.APP_PANEL_EXTRA, (event, raw: unknown) => {
    const win = electron.BrowserWindow.fromWebContents(event.sender);
    if (
      !win ||
      win.isDestroyed() ||
      win.isMaximized() ||
      win.isFullScreen() ||
      !win.isResizable()
    ) {
      return { ok: false, applied: 0, skipped: true };
    }
    const target = Math.max(
      0,
      Math.round(typeof raw === 'number' && Number.isFinite(raw) ? raw : 0)
    );
    const rec = panelExtraByWin.get(win) ?? { extra: 0, left: 0, right: 0 };
    let delta = target - rec.extra;
    if (delta !== 0) {
      const b = win.getBounds();
      const wa = electron.screen.getDisplayMatching(b).workArea;
      if (delta > 0) {
        // 优先向右扩(左缘不动),右缘到工作区边界后向左借位把窗口整体放中间可多补
        const growRight = Math.min(delta, Math.max(0, wa.x + wa.width - (b.x + b.width)));
        const growLeft = Math.min(delta - growRight, Math.max(0, b.x - wa.x));
        const grown = growRight + growLeft;
        if (grown > 0) {
          win.setBounds({ x: b.x - growLeft, y: b.y, width: b.width + grown, height: b.height });
          rec.right += growRight;
          rec.left += growLeft;
          rec.extra += grown;
        }
      } else {
        // 收窄:先还左借位再收右侧,总量不越过最小宽(minWidth)。绝不主动抹掉
        // 用户自己拉宽的窗口(仅收回本面板实际加宽的 px)。
        const maxRemove = Math.max(0, b.width - win.getMinimumSize()[0]);
        let remove = Math.min(-delta, rec.extra);
        const remLeft = Math.min(remove, rec.left, maxRemove);
        remove -= remLeft;
        const remRight = Math.min(remove, rec.right, maxRemove - remLeft);
        const removed = remLeft + remRight;
        if (removed > 0) {
          win.setBounds({ x: b.x + remLeft, y: b.y, width: b.width - removed, height: b.height });
          rec.left = Math.max(0, rec.left - remLeft);
          rec.right = Math.max(0, rec.right - remRight);
          rec.extra = Math.max(0, rec.extra - removed);
        }
      }
      panelExtraByWin.set(win, rec);
    }
    return { ok: true, applied: rec.extra, skipped: false };
  });
}
