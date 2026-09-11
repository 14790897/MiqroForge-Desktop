import { ChildProcess, spawn, execSync } from 'child_process';
import { EventEmitter } from 'events';
import { createInterface, Interface } from 'readline';
import { existsSync, watch } from 'fs';
import { join, extname } from 'path';
import { randomUUID } from 'crypto';
import { BrowserWindow } from 'electron';
import { createTypedAppClient } from '../shared/app-client';
import type { TypedAppClient } from '../shared/app-client';
import type { RuntimeState, RuntimeStatus } from '../shared/ipc';
import { IPC_EVENTS } from '../shared/ipc';
import { writeMainProcessLog } from './electron-log';

/** Strip ANSI escape codes (color/bold/reset) from log text. */
function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*m/g, '');
}

export interface BridgeRequest {
  id: string;
  method: string;
  params?: Record<string, unknown>;
}

interface BridgeResponse {
  id?: string | null;
  request_id?: string | null;
  result?: unknown;
  error?: string;
  code?: string;
  recoverable?: boolean;
  type?: string;
  event?: string;
  data?: unknown;
}

interface SendOptions {
  allowStarting?: boolean;
  timeoutMs?: number;
  timeoutMode?: 'total' | 'inactivity';
}

export interface NormalizedBridgeMessage {
  requestId: string | null;
  result?: unknown;
  error?: string;
  code?: string;
  recoverable?: boolean;
  eventType?: string;
  data?: unknown;
}

export interface InitializeParams {
  clientId: string;
  clientInfo: {
    name: string;
    title: string;
    version: string;
  };
  capabilities: {
    experimentalApi: boolean;
    optOutNotificationMethods: string[];
  };
}

/** Terminal event types that resolve/reject a streaming pending entry. */
const TERMINAL_EVENT_TYPES = new Set(['final', 'error', 'aborted']);
// Keep in sync with CHAT_DRAIN_IDLE_TIMEOUT_SECONDS in `miqi/bridge/loop.py`.
export const CHAT_BACKEND_DRAIN_TIMEOUT_MS = 600_000;
export const CHAT_SEND_TIMEOUT_MS = CHAT_BACKEND_DRAIN_TIMEOUT_MS + 120_000;

export function normalizeBridgeMessage(resp: BridgeResponse): NormalizedBridgeMessage {
  const requestId =
    typeof resp.id === 'string'
      ? resp.id
      : typeof resp.request_id === 'string'
        ? resp.request_id
        : null;

  const eventType =
    typeof resp.type === 'string'
      ? resp.type
      : typeof resp.event === 'string'
        ? resp.event
        : undefined;

  return {
    requestId,
    result: resp.result,
    error: resp.error,
    code: resp.code,
    recoverable: resp.recoverable,
    eventType,
    data: resp.data,
  };
}

export function buildInitializeParams(version: string): InitializeParams {
  return {
    clientId: 'miqi-desktop',
    clientInfo: {
      name: 'miqi_desktop',
      title: 'MiQroForge Desktop',
      version,
    },
    capabilities: {
      experimentalApi: true,
      optOutNotificationMethods: [],
    },
  };
}

function findBridgeExecutable(projectRoot: string): {
  command: string;
  args: string[];
} {
  // If user set MIQI_PYTHON_PATH, use it directly with the script
  const envPath = process.env['MIQI_PYTHON_PATH'];
  if (envPath) {
    const bridgeScript = join(projectRoot, 'miqi', 'bridge', 'server.py');
    return { command: envPath, args: [bridgeScript] };
  }

  // If project has uv.lock, use uv run python (source mode, prefer over stale exe)
  if (existsSync(join(projectRoot, 'uv.lock')) || existsSync(join(projectRoot, 'pyproject.toml'))) {
    try {
      execSync('uv --version', { stdio: 'ignore', windowsHide: true });
      const bridgeScript = join(projectRoot, 'miqi', 'bridge', 'server.py');
      return { command: 'uv', args: ['run', 'python', bridgeScript] };
    } catch {
      // uv not available, fall through
    }
  }

  // Check for bundled miqi-bridge executable (packaged app)
  // In asar, __dirname is inside the archive, so use process.resourcesPath
  const bridgeExe = process.platform === 'win32' ? 'miqi-bridge.exe' : 'miqi-bridge';
  const bundledBridge = process.resourcesPath ? join(process.resourcesPath, bridgeExe) : null;
  if (bundledBridge && existsSync(bundledBridge)) {
    return { command: bundledBridge, args: [] };
  }

  // Try .venv
  const pythonCmd = process.platform === 'win32' ? 'python.exe' : 'python';
  const venvBin = process.platform === 'win32' ? 'Scripts' : 'bin';
  const venvPython = join(projectRoot, '.venv', venvBin, pythonCmd);
  if (existsSync(venvPython)) {
    const bridgeScript = join(projectRoot, 'miqi', 'bridge', 'server.py');
    return { command: venvPython, args: [bridgeScript] };
  }

  // Fallback
  const bridgeScript = join(projectRoot, 'miqi', 'bridge', 'server.py');
  return { command: 'python3', args: [bridgeScript] };
}

export class BridgeManager extends EventEmitter {
  private process: ChildProcess | null = null;
  private rl: Interface | null = null;
  private pending: Map<
    string,
    {
      resolve: (value: unknown) => void;
      reject: (reason: Error) => void;
      onEvent?: (type: string, data: unknown) => void;
      refreshTimeout?: () => void;
    }
  > = new Map();

  private state: RuntimeState = 'stopped';
  private logs: string[] = [];
  private maxLogs: number = 500;
  private projectRoot: string;
  private fileWatcher: ReturnType<typeof watch> | null = null;
  private hotReloadEnabled: boolean = false;
  private lastReloadTime: number = 0;
  private reloadCooldown: number = 1000; // 1 second cooldown between reloads
  private reloadTimer: ReturnType<typeof setTimeout> | null = null;
  private restartInProgress: boolean = false;
  private deferredRestart: boolean = false;
  private initialized: boolean = false;
  private clientId: string = 'miqi-desktop';
  private stoppingPromise: Promise<void> | null = null;
  private _sandboxAvailable: boolean = false;

  get sandboxAvailable(): boolean {
    return this._sandboxAvailable;
  }

  set sandboxAvailable(v: boolean) {
    this._sandboxAvailable = v;
  }

  constructor(projectRoot?: string) {
    super();
    // In dev: __dirname = apps/desktop/out/main → projectRoot is 4 levels up
    this.projectRoot = projectRoot || join(__dirname, '..', '..', '..', '..');
    // Hot reload is ON by default in dev mode for development convenience.
    this.hotReloadEnabled =
      process.env['NODE_ENV'] === 'development' ||
      process.env['ELECTRON_RENDERER_URL'] !== undefined;
  }

  /** Whether the bridge has completed the initialize/initialized handshake. */
  isInitialized(): boolean {
    return this.initialized;
  }

  getStatus(): RuntimeStatus {
    return {
      state: this.state,
      configured: this.state === 'running',
      sandbox_available: this.sandboxAvailable,
      error: this.state === 'error' ? 'Bridge process exited unexpectedly' : undefined,
    };
  }

  getProjectRoot(): string {
    return this.projectRoot;
  }

  getLogs(): string[] {
    return [...this.logs];
  }

  async start(): Promise<void> {
    if (this.stoppingPromise) {
      await this.stoppingPromise;
    }
    if (this.state === 'running' || this.state === 'starting') return;

    this.state = 'starting';
    this.emitState();

    const { command, args } = findBridgeExecutable(this.projectRoot);

    this.addLog(`Working directory: ${this.projectRoot}`);
    this.recordMainLog(
      'INFO',
      `Starting MiQroForge bridge: ${command} ${args.join(' ')}`,
      'bridge'
    );

    let startedProcess: ChildProcess | null = null;
    let startedReader: Interface | null = null;

    try {
      const bridgeProcess = spawn(command, args, {
        cwd: this.projectRoot,
        stdio: ['pipe', 'pipe', 'pipe'],
        // MIQI_PARENT_PID (#959): the bridge's parent-death watchdog watches
        // this PID. On Windows the direct parent may be a `uv` shim that
        // outlives Electron (and Windows never updates the parent PID), so
        // the bridge must watch the real launcher. When Electron is killed
        // hard (E2E force-kill / crash) the bridge hard-exits instead of
        // lingering as an orphan that pollutes later runs.
        env: {
          ...process.env,
          PYTHONUNBUFFERED: '1',
          PYTHONUTF8: '1',
          PYTHONIOENCODING: 'utf-8',
          MIQI_PARENT_PID: String(process.pid),
        },
        windowsHide: true,
      });
      this.process = bridgeProcess;
      startedProcess = bridgeProcess;

      const lineReader = createInterface({
        input: bridgeProcess.stdout!,
        crlfDelay: Infinity,
      });
      this.rl = lineReader;
      startedReader = lineReader;

      lineReader.on('line', (line: string) => {
        if (this.process !== bridgeProcess) return;
        try {
          const raw: BridgeResponse = JSON.parse(line);
          const resp = normalizeBridgeMessage(raw);

          if (resp.eventType) {
            if (resp.requestId) {
              const pending = this.pending.get(resp.requestId);
              // Terminal events resolve/reject the streaming promise
              if (pending && TERMINAL_EVENT_TYPES.has(resp.eventType)) {
                if (resp.eventType === 'error') {
                  // Error: single channel — only reject; do NOT call onEvent
                  const msg: string =
                    typeof resp.error === 'string'
                      ? resp.error
                      : resp.data &&
                          typeof resp.data === 'object' &&
                          'message' in resp.data &&
                          typeof (resp.data as Record<string, unknown>).message === 'string'
                        ? ((resp.data as Record<string, unknown>).message as string)
                        : typeof resp.data === 'string'
                          ? resp.data
                          : 'Stream error';
                  const err = new Error(msg);
                  if (resp.code) (err as Error & { code?: string }).code = resp.code;
                  pending.reject(err);
                } else {
                  // final / aborted: call onEvent then resolve.
                  // Wrap onEvent in try/catch so a thrown error in the
                  // callback does not skip pending.resolve (#331).
                  try {
                    pending.onEvent?.(resp.eventType, resp.data);
                  } catch (onEventErr) {
                    this.addLog(
                      `[Bridge] onEvent threw for terminal event ${resp.eventType}: ${onEventErr}`
                    );
                  } finally {
                    pending.resolve(resp.data);
                  }
                }
                // After resolving a terminal event, check if a deferred
                // restart was requested by the file watcher (#387).
                this._checkDeferredRestart();
                // Terminal: skip bridge-event
                return;
              }
              // Non-terminal events: call onEvent for tracked requests.
              // Skip bridge-event when a pending exists — onEvent already
              // delivers to the IPC handler. bridge-event is only for
              // global events without a tracked request (e.g. fs/changed).
              if (pending) {
                pending.refreshTimeout?.();
                pending.onEvent?.(resp.eventType, resp.data);
                return;
              }
            }
            this.emit('bridge-event', {
              requestId: resp.requestId,
              type: resp.eventType,
              data: resp.data,
            });

            // Track sandbox availability from the Python bridge.
            // sandbox.ready fires when _init_sandbox_manager() completes.
            if (resp.eventType === 'sandbox.ready') {
              const d = resp.data as Record<string, unknown> | undefined;
              this.sandboxAvailable = d?.initialized === true;
              this.emitState();
            }
            return;
          }

          if (!resp.requestId) {
            this.addLog(`[Bridge] Ignoring response without id/request_id: ${line}`);
            return;
          }

          const pending = this.pending.get(resp.requestId);
          if (!pending) {
            this.addLog(`[Bridge] No pending request for response ${resp.requestId}`);
            return;
          }

          if (resp.error) {
            const err = new Error(resp.code ? `${resp.error} (${resp.code})` : resp.error);
            (err as Error & { code?: string; recoverable?: boolean }).code = resp.code;
            (err as Error & { code?: string; recoverable?: boolean }).recoverable =
              resp.recoverable;
            this.addLog(
              `[Bridge] ${resp.requestId} failed: ${resp.error}` +
                (resp.code ? ` (${resp.code})` : '')
            );
            pending.reject(err);
          } else if (pending.onEvent) {
            // Streaming mode: notify via onEvent, keep pending alive for future events
            pending.refreshTimeout?.();
            pending.onEvent('response', resp.result);
          } else {
            pending.resolve(resp.result);
          }
        } catch (e) {
          const errMsg = e instanceof Error ? e.message : String(e);
          this.addLog(`[Bridge] Error processing stdout line: ${errMsg} — raw: ${line}`);
        }
      });

      bridgeProcess.stderr!.on('data', (data: Buffer) => {
        const rawText = data.toString().trim();
        if (rawText) {
          const text = stripAnsi(rawText);
          // Python libraries (loguru, warnings, etc.) often write normal
          // output to stderr — use INFO, not WARN, to avoid false alarms.
          console.log(`[bridge] ${text}`);
          this.recordMainLog('INFO', text, 'bridge');
        }
      });

      bridgeProcess.on('error', (err) => {
        if (this.process !== bridgeProcess) return;
        this.addLog(`Bridge process error: ${err.message}`);
        this.state = 'error';
        this.process = null;
        this.rl = null;
        this.initialized = false;
        this.clientId = 'miqi-desktop';
        this.emitState();
        // Reject all pending requests so callers don't hang
        for (const [id, entry] of this.pending) {
          entry.reject(new Error(`Bridge process error: ${err.message}`));
        }
        this.pending.clear();
      });

      // ── Ready handshake ────────────────────────────────────────────
      // Wait for the bridge to send {"type":"ready"} on stdout.
      // PyInstaller onefile exe first-run extraction to %TEMP% can take
      // 5-15+ seconds.  First-run WSL dependency auto-install (apt-get
      // install bubblewrap coreutils rsync) can take 60-120+ seconds.
      // Timeout: 180 s to match the Python-side apt-get timeout.
      // ───────────────────────────────────────────────────────────────
      await new Promise<void>((_resolve, _reject) => {
        let settled = false;

        const done = (err?: Error) => {
          if (settled) return;
          settled = true;
          clearTimeout(timeout);
          lineReader.removeListener('line', onReadyLine);
          bridgeProcess.removeListener('close', onClose);
          if (err) _reject(err);
          else _resolve();
        };

        const onReadyLine = (line: string) => {
          try {
            const msg = JSON.parse(line);
            if (msg.type === 'ready') {
              done();
            }
          } catch {
            // Non-JSON line (e.g. stray print) — ignore
          }
        };

        const onClose = (code: number | null) => {
          done(new Error(`Bridge process exited with code ${code} before ready signal`));
        };

        const timeout = setTimeout(() => {
          done(
            new Error(
              'Bridge did not send ready signal within 180 s (WSL dep install or PyInstaller extraction may be stuck)'
            )
          );
        }, 180_000);

        lineReader.on('line', onReadyLine);
        bridgeProcess.once('close', onClose);
      });

      // Bridge process is alive and accepting stdin.
      // Do NOT set state='running' yet — the renderer would start
      // making IPC calls before the initialize/initialized handshake
      // completes, and the bridge rejects them with NOT_INITIALIZED.
      this.addLog('Bridge ready');

      // Install the permanent request-response line handler
      // NOTE: The primary line handler (registered before the ready
      // handshake) already processes all responses and streaming events
      // for tracked requests.  This secondary handler ONLY forwards
      // orphan events (no pending request) to renderer windows so late
      // events are not dropped.  Do NOT process tracked responses/events
      // here — the primary handler already does that.
      lineReader.on('line', (line: string) => {
        if (this.process !== bridgeProcess) return;
        try {
          const resp: BridgeResponse = JSON.parse(line);
          const normalized = normalizeBridgeMessage(resp);
          const pending = normalized.requestId ? this.pending.get(normalized.requestId) : undefined;

          if (!pending && normalized.eventType && resp.data) {
            // Orphan event (e.g. subagent_result after main agent finished)
            // Forward to all renderer windows so late events are not dropped.
            // Use normalized.eventType (not raw resp.type) so events sent via
            // the "event" field are handled correctly — consistent with the
            // primary handler (#335).
            const eventKey = `CHAT_${normalized.eventType.toUpperCase()}`;
            const channel = IPC_EVENTS[eventKey as keyof typeof IPC_EVENTS];
            if (channel) {
              const allWindows = BrowserWindow.getAllWindows();
              for (const win of allWindows) {
                if (!win.isDestroyed()) {
                  win.webContents.send(channel, resp.data);
                }
              }
            }
          }
        } catch {
          // Non-JSON line from bridge (shouldn't happen — logs go to stderr)
        }
      });

      // Handle unexpected exit after ready
      bridgeProcess.on('close', (code) => {
        if (this.process !== bridgeProcess) return;
        if (this.state === 'stopping') return;
        this.addLog(`Bridge process exited with code ${code}`);
        this.state = code === 0 ? 'stopped' : 'error';
        this.process = null;
        this.rl = null;
        this.initialized = false;
        this.clientId = 'miqi-desktop';
        this.emitState();
        // Reject all pending requests
        for (const [id, pending] of this.pending) {
          pending.reject(new Error('Bridge process exited'));
          this.pending.delete(id);
        }
      });

      // Wait briefly and check if process is still alive
      await new Promise<void>((resolve, reject) => {
        setTimeout(() => {
          if (this.process !== bridgeProcess) {
            reject(new Error('Bridge process was replaced before initialization completed'));
          } else if (bridgeProcess.exitCode !== null && bridgeProcess.exitCode !== undefined) {
            reject(
              new Error(`Bridge process exited immediately with code ${bridgeProcess.exitCode}`)
            );
          } else {
            resolve();
          }
        }, 250);
      });

      await this.initializeConnection();
      // Only now is the bridge fully ready for IPC — the renderer
      // will see state='running' and may safely start making calls.
      this.state = 'running';
      this.emitState();
      this.startFileWatcher();
    } catch (err) {
      this.addLog(`Failed to start bridge: ${err}`);
      const isActiveStart = this.process === startedProcess;
      if (isActiveStart) {
        this.state = 'error';
        this.emitState();
      }

      // Cleanup on initialization failure
      if (isActiveStart) {
        this.stopFileWatcher();
      }
      if (startedReader) {
        startedReader.close();
      }
      if (this.rl === startedReader) {
        this.rl = null;
      }
      if (startedProcess && this.process === startedProcess) {
        startedProcess.stdin?.end();
        startedProcess.kill('SIGTERM');
        this.process = null;
      }
      if (isActiveStart) {
        this.initialized = false;
        this.clientId = 'miqi-desktop';
        // Reject all pending requests
        for (const [id, pending] of this.pending) {
          pending.reject(new Error('Bridge initialization failed'));
          this.pending.delete(id);
        }
      }
      throw err;
    }
  }

  async stop(): Promise<void> {
    if (this.stoppingPromise) {
      await this.stoppingPromise;
      return;
    }
    if (!this.process) return;

    this.state = 'stopping';
    this.emitState();

    // Stop file watcher
    this.stopFileWatcher();

    this.initialized = false;
    this.clientId = 'miqi-desktop';

    const proc = this.process;
    this.stoppingPromise = new Promise<void>((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(forceKillTimer);
        clearTimeout(resolveTimer);
        for (const [id, entry] of this.pending) {
          entry.reject(new Error('Bridge stopped — request cancelled'));
          this.pending.delete(id);
        }
        proc.removeListener('close', done);
        proc.removeListener('exit', done);
        this.stoppingPromise = null;
        if (this.process === proc) {
          this.process = null;
          this.rl = null;
          this.state = 'stopped';
          this.emitState();
        }
        resolve();
      };

      const forceKillTimer = setTimeout(() => {
        if (this.process === proc) {
          proc.kill('SIGKILL');
        }
      }, 5000);

      const resolveTimer = setTimeout(done, 5500);

      proc.once('close', done);
      proc.once('exit', done);

      proc.stdin?.end();
      proc.kill('SIGTERM');
    });

    this.recordMainLog('INFO', 'Bridge stopping', 'bridge');
    await this.stoppingPromise;
  }

  private startFileWatcher(): void {
    if (!this.hotReloadEnabled || this.fileWatcher) return;

    const miqiDir = join(this.projectRoot, 'miqi');
    if (!existsSync(miqiDir)) {
      this.addLog(`[Hot Reload] Skipping watcher - miqi directory not found: ${miqiDir}`);
      return;
    }

    this.addLog('[Hot Reload] File watcher enabled for Python backend');

    this.fileWatcher = watch(miqiDir, { recursive: true }, (_fileEventType, filename) => {
      if (!filename) return;

      // Only watch Python files
      const ext = extname(filename);
      if (ext !== '.py') return;

      // Skip pycache and __pycache__ directories
      if (filename.includes('__pycache__') || filename.endsWith('.pyc')) return;

      this.handleFileChange(filename);
    });
  }

  private stopFileWatcher(): void {
    if (this.reloadTimer) {
      clearTimeout(this.reloadTimer);
      this.reloadTimer = null;
    }
    if (this.fileWatcher) {
      this.fileWatcher.close();
      this.fileWatcher = null;
      this.addLog('[Hot Reload] File watcher stopped');
    }
  }

  private handleFileChange(filename: string): void {
    const now = Date.now();
    if (now - this.lastReloadTime < this.reloadCooldown) {
      return;
    }

    this.lastReloadTime = now;
    this.addLog(`[Hot Reload] Detected change in: ${filename}`);

    if (this.state !== 'running' || !this.initialized || this.restartInProgress) {
      this.addLog(`[Hot Reload] Ignoring change while bridge is ${this.state}`);
      return;
    }

    // Defer restart if there are active requests — avoid killing sessions.
    // The restart will be triggered when the last pending request completes.
    if (this.pending.size > 0) {
      this.addLog(`[Hot Reload] Deferring restart due to ${this.pending.size} pending request(s)`);
      this.deferredRestart = true;
      return;
    }

    if (this.reloadTimer) {
      clearTimeout(this.reloadTimer);
    }

    // Schedule restart after a short delay to allow multiple files to change
    this.reloadTimer = setTimeout(() => {
      this.reloadTimer = null;
      this.restart().catch((err) => {
        this.addLog(`[Hot Reload] Restart error: ${err}`);
      });
    }, 500);
  }

  /** Trigger a deferred restart after pending requests complete (#387). */
  private _checkDeferredRestart(): void {
    if (!this.deferredRestart) return;
    // Only fire when all pending requests have settled
    if (this.pending.size > 0) return;
    this.deferredRestart = false;
    this.addLog('[Hot Reload] Triggering deferred restart after pending requests completed');
    // Use handleFileChange's debounce + restart flow
    this.handleFileChange('(deferred)');
  }

  private async restart(): Promise<void> {
    if (this.restartInProgress || this.state !== 'running' || !this.process) return;
    this.restartInProgress = true;

    this.addLog('[Hot Reload] Restarting bridge due to code changes...');
    this.stopFileWatcher();

    // Reject all pending requests so callers don't hang forever
    for (const [id, entry] of this.pending) {
      entry.reject(new Error('Bridge restarted — request cancelled'));
    }
    this.pending.clear();

    const oldProcess = this.process;

    // Stop current process
    this.state = 'stopping';
    this.emitState();
    oldProcess.stdin?.end();
    oldProcess.kill('SIGTERM');

    // Wait for the old process to finish its own cleanup before starting a
    // replacement. Starting too early can race sandbox state cleanup on Windows.
    await new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        oldProcess.removeListener?.('close', finish);
        resolve();
      };
      const timeout = setTimeout(finish, 5000);
      oldProcess.once?.('close', finish);
    });

    if (this.process === oldProcess) {
      this.process = null;
      this.rl = null;
    }
    this.initialized = false;
    this.clientId = 'miqi-desktop';
    this.state = 'stopped';

    // Restart
    try {
      await this.start();
      this.addLog('[Hot Reload] Bridge restarted successfully');
    } catch (err) {
      this.addLog(`[Hot Reload] Failed to restart bridge: ${err}`);
    } finally {
      this.restartInProgress = false;
    }
  }

  isRunning(): boolean {
    return this.process !== null && this.state === 'running';
  }

  async sendSafe(
    method: string,
    params?: Record<string, unknown>,
    onEvent?: (type: string, data: unknown) => void
  ): Promise<unknown> {
    if (!this.isRunning()) return null;
    try {
      return await this.send(method, params, onEvent);
    } catch (e: any) {
      const errMsg = e?.message ?? String(e);
      this.recordMainLog('WARN', `sendSafe ${method} failed: ${errMsg}`, 'bridge');
      return null;
    }
  }

  /** Like sendSafe but returns structured error info so the UI can display it.
   *  Use for pages that need to show failure reasons, not just blank states. */
  async sendSafeWithError(
    method: string,
    params?: Record<string, unknown>
  ): Promise<{ ok: true; value: unknown } | { ok: false; error: string; code?: string }> {
    if (!this.isRunning()) return { ok: false, error: 'Bridge not running' };
    try {
      const value = await this.send(method, params);
      return { ok: true, value };
    } catch (e: any) {
      const msg = e?.message ?? String(e ?? 'Unknown bridge error');
      this.recordMainLog('WARN', `sendSafeWithError ${method} failed: ${msg}`, 'bridge');
      return { ok: false, error: msg, code: e?.code };
    }
  }

  app(): TypedAppClient {
    return createTypedAppClient((method, params, onEvent) => this.send(method, params, onEvent));
  }

  async send(
    method: string,
    params?: Record<string, unknown>,
    onEvent?: (type: string, data: unknown) => void
  ): Promise<unknown> {
    return this.sendRequest(method, params, onEvent);
  }

  private async sendRequest(
    method: string,
    params?: Record<string, unknown>,
    onEvent?: (type: string, data: unknown) => void,
    options: SendOptions = {}
  ): Promise<unknown> {
    const canSend =
      this.isRunning() ||
      (options.allowStarting === true && this.process !== null && this.state === 'starting');

    if (!canSend) {
      throw new Error('Bridge not running');
    }

    const id = randomUUID();
    const request: BridgeRequest = { id, method, params };
    // Methods that may be slow during first-time auto-export/install (2-5 minutes)
    // or while the sandbox is initializing in the background.
    // When a request hits SLOW_METHODS, it gets the extended CHAT_SEND_TIMEOUT_MS
    // instead of the default 30s IPC timeout to avoid sendSafe swallowing
    // failures.  The Python side (#266) already dispatches these concurrently,
    // so a slow method no longer blocks fast ones.
    const SLOW_METHODS = new Set([
      'chat.send',
      'config.get',
      'plugins.list',
      'sandbox.setEnabled',
      'sessions.archive',
      'sessions.get',
      'sessions.get_tracked_files',
      'sessions.list',
      'sessions.list_archived',
      'sessions.unarchive',
      'thread/start',
    ]);
    const timeoutMs =
      options.timeoutMs ?? (SLOW_METHODS.has(method) ? CHAT_SEND_TIMEOUT_MS : 30_000);
    const timeoutMode = options.timeoutMode ?? (method === 'chat.send' ? 'inactivity' : 'total');
    const startMs = Date.now();

    const logSlow = () => {
      // chat.send / turn/start are streaming long-lived requests — slow duration is expected
      if (method === 'chat.send' || method === 'turn/start') return;
      const duration = Date.now() - startMs;
      if (duration > 1000) {
        this.recordMainLog('INFO', `IPC ${method} took ${duration}ms`, 'bridge');
      }
    };

    return new Promise((resolve, reject) => {
      let timeout: ReturnType<typeof setTimeout> | undefined;
      const timeoutMessage =
        timeoutMode === 'inactivity'
          ? `Request ${method} timed out after ${timeoutMs}ms without bridge events`
          : `Request ${method} timed out`;
      const armTimeout = () => {
        timeout = setTimeout(() => {
          this.pending.delete(id);
          logSlow();
          reject(new Error(timeoutMessage));
        }, timeoutMs);
      };
      const refreshTimeout = () => {
        if (timeoutMode !== 'inactivity') return;
        clearTimeout(timeout);
        armTimeout();
      };
      armTimeout();

      this.pending.set(id, {
        resolve: (value: unknown) => {
          clearTimeout(timeout);
          this.pending.delete(id);
          logSlow();
          resolve(value);
        },
        reject: (err: Error) => {
          clearTimeout(timeout);
          this.pending.delete(id);
          logSlow();
          reject(err);
        },
        onEvent,
        refreshTimeout,
      });

      const stdin = this.process!.stdin!;
      if (!stdin.writable || stdin.destroyed) {
        clearTimeout(timeout);
        this.pending.delete(id);
        reject(new Error('Bridge not running'));
        return;
      }
      try {
        stdin.write(JSON.stringify(request) + '\n', (err) => {
          if (err) {
            clearTimeout(timeout);
            this.pending.delete(id);
            reject(err);
          }
        });
      } catch (err) {
        clearTimeout(timeout);
        this.pending.delete(id);
        reject(err instanceof Error ? err : new Error(String(err)));
      }
    });
  }

  private writeNotification(method: string, params: Record<string, unknown> = {}): void {
    if (!this.process?.stdin) return;
    this.process.stdin.write(JSON.stringify({ method, params }) + '\n');
  }

  private async initializeConnection(): Promise<void> {
    const version = '0.1.0';
    const result = await this.sendRequest(
      'initialize',
      buildInitializeParams(version) as unknown as Record<string, unknown>,
      undefined,
      { allowStarting: true, timeoutMs: 15_000 }
    );

    const init = result as { clientId?: string; serverInfo?: { version?: string } } | null;
    this.clientId = init?.clientId ?? 'miqi-desktop';
    this.initialized = true;
    this.addLog(
      `Bridge initialized as ${this.clientId}` +
        (init?.serverInfo?.version ? ` against server ${init.serverInfo.version}` : '')
    );
    this.writeNotification('initialized');
  }

  private addLog(message: string): void {
    this.logs.push(`[${new Date().toISOString()}] ${message}`);
    if (this.logs.length > this.maxLogs) {
      this.logs = this.logs.slice(-this.maxLogs);
    }
    this.emit('log', message);
  }

  recordMainLog(level: string, message: string, source?: string): void {
    writeMainProcessLog(level, message, this.projectRoot, source);
  }

  emitState(): void {
    this.emit('state', this.getStatus());
  }
}
