import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { PassThrough } from 'stream';
import {
  buildInitializeParams,
  CHAT_BACKEND_DRAIN_TIMEOUT_MS,
  CHAT_SEND_TIMEOUT_MS,
  normalizeBridgeMessage,
} from './bridge';

// ============================================================
// Pure-function tests (unchanged)
// ============================================================

describe('normalizeBridgeMessage', () => {
  it('normalizes AppServer response request_id', () => {
    const msg = normalizeBridgeMessage({
      request_id: 'req-1',
      result: { ok: true },
    });

    expect(msg.requestId).toBe('req-1');
    expect(msg.result).toEqual({ ok: true });
    expect(msg.error).toBeUndefined();
  });

  it('normalizes transport response id', () => {
    const msg = normalizeBridgeMessage({
      id: 'req-2',
      result: { ok: true },
    });

    expect(msg.requestId).toBe('req-2');
  });

  it('normalizes AppServer error code', () => {
    const msg = normalizeBridgeMessage({
      request_id: 'req-3',
      error: 'Not initialized',
      code: 'NOT_INITIALIZED',
      recoverable: false,
    });

    expect(msg.requestId).toBe('req-3');
    expect(msg.error).toBe('Not initialized');
    expect(msg.code).toBe('NOT_INITIALIZED');
    expect(msg.recoverable).toBe(false);
  });

  it('normalizes legacy type events', () => {
    const msg = normalizeBridgeMessage({
      id: 'req-4',
      type: 'progress',
      data: { text: 'running' },
    });

    expect(msg.requestId).toBe('req-4');
    expect(msg.eventType).toBe('progress');
    expect(msg.data).toEqual({ text: 'running' });
  });

  it('normalizes AppServer event envelopes', () => {
    const msg = normalizeBridgeMessage({
      request_id: 'req-5',
      event: 'fs/changed',
      data: { watchId: 'watch-1' },
    });

    expect(msg.requestId).toBe('req-5');
    expect(msg.eventType).toBe('fs/changed');
    expect(msg.data).toEqual({ watchId: 'watch-1' });
  });
});

describe('buildInitializeParams', () => {
  it('builds the Desktop initialize payload', () => {
    const params = buildInitializeParams('0.1.0');

    expect(params.clientId).toBe('miqi-desktop');
    expect(params.clientInfo).toEqual({
      name: 'miqi_desktop',
      title: 'MiQroForge Desktop',
      version: '0.1.0',
    });
    expect(params.capabilities).toEqual({
      experimentalApi: true,
      optOutNotificationMethods: [],
    });
  });
});

// ============================================================
// Lifecycle tests — mock spawn, use real readline + PassThrough
// ============================================================

const { spawn: mockSpawn, execSync: mockExecSync } = vi.hoisted(() => ({
  spawn: vi.fn(),
  execSync: vi.fn(() => Buffer.from('')),
}));

vi.mock('child_process', async (importOriginal) => {
  const actual = await importOriginal<typeof import('child_process')>();
  return {
    ...actual,
    spawn: mockSpawn,
    execSync: mockExecSync,
  };
});

// Captured watcher close spies for cleanup assertions
const watcherCloses: Array<ReturnType<typeof vi.fn>> = [];
const watchCallbacks: Array<(eventType: string, filename: string | Buffer | null) => void> = [];

// Captured readline close spies — collected by the readline mock below
const rlCloseSpies: Array<ReturnType<typeof vi.fn>> = [];

// fs mocks: only return true for uv/pyproject to force the uv code path,
// and for miqi directory so file watcher starts
vi.mock('fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('fs')>();
  return {
    ...actual,
    existsSync: vi.fn((p: string) => {
      if (typeof p === 'string') {
        if (p.includes('uv.lock') || p.includes('pyproject.toml')) return true;
        if (p.includes('miqi')) return true;
      }
      return false;
    }),
    watch: vi.fn((_path: string, _options: any, callback: any) => {
      const closeFn = vi.fn();
      watcherCloses.push(closeFn);
      watchCallbacks.push(callback);
      return { close: closeFn };
    }),
  };
});

// readline mock: wraps createInterface to spy on close() calls
vi.mock('readline', async () => {
  const actual = await vi.importActual<typeof import('readline')>('readline');
  return {
    ...actual,
    createInterface: vi.fn((opts: any) => {
      const rl = actual.createInterface(opts);
      const origClose = actual.Interface.prototype.close.bind(rl) as () => void;
      const closeSpy = vi.fn(() => origClose());
      Object.defineProperty(rl, 'close', {
        value: closeSpy,
        writable: true,
        configurable: true,
      });
      rlCloseSpies.push(closeSpy);
      return rl;
    }),
  };
});

async function importBridgeManager() {
  const mod = await import('./bridge');
  return mod.BridgeManager;
}

function createMockProcess() {
  const stdout = new PassThrough();
  const stderr = new PassThrough();
  const proc = {
    stdin: { write: vi.fn(), end: vi.fn(), writable: true, destroyed: false },
    stdout,
    stderr,
    on: vi.fn(),
    once: vi.fn(),
    removeListener: vi.fn(),
    kill: vi.fn(),
    exitCode: null as number | null,
  };
  mockSpawn.mockReturnValue(proc);
  return proc;
}

/** Feed a JSON line into the bridge process stdout. */
function feedLine(proc: ReturnType<typeof createMockProcess>, obj: Record<string, unknown>) {
  proc.stdout.write(JSON.stringify(obj) + '\n');
}

/** Get the request ID from the last stdin write matching a method. */
function findRequestId(proc: ReturnType<typeof createMockProcess>, method: string): string | null {
  const write = proc.stdin.write as ReturnType<typeof vi.fn>;
  for (const call of write.mock.calls) {
    try {
      const r = JSON.parse(call[0] as string);
      if (r.method === method) return r.id;
    } catch {
      /* skip */
    }
  }
  return null;
}

/** Complete the full bridge startup sequence:
 *  spawn → ready handshake → 250ms exit check → initialize.
 *
 *  bridge.ts start() now waits for a {"type":"ready"} line before
 *  proceeding to initializeConnection().  This helper feeds that
 *  ready signal, waits for the 250ms alive-check, then feeds the
 *  initialize response.
 */
async function startBridge(
  proc: ReturnType<typeof createMockProcess>,
  bridge: InstanceType<Awaited<ReturnType<typeof importBridgeManager>>>,
  initResult: Record<string, unknown> = { clientId: 'test', serverInfo: { version: '1' } }
): Promise<void> {
  const startPromise = bridge.start();
  // Wait for spawn + readline + ready-handshake handlers to be set up
  await new Promise((r) => setTimeout(r, 300));
  // Resolve the ready handshake
  feedLine(proc, { type: 'ready' });
  // Wait for the 250ms exit-check + initializeConnection() to send initialize
  await new Promise((r) => setTimeout(r, 350));
  // Feed the initialize response
  const initId = findRequestId(proc, 'initialize');
  feedLine(proc, { id: initId, result: initResult });
  await startPromise;
}

beforeEach(() => {
  vi.clearAllMocks();
  watcherCloses.length = 0;
  watchCallbacks.length = 0;
  rlCloseSpies.length = 0;
});

afterEach(() => {
  // Restore env set by cleanup/hot-reload tests to avoid cross-test leakage
  delete process.env['ELECTRON_RENDERER_URL'];
});

describe('BridgeManager lifecycle', () => {
  it('completes initialize handshake and sends initialized notification', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'alpha-1', serverInfo: { version: '0.2.0' } });

    expect(bridge.isInitialized()).toBe(true);
    expect(bridge.isRunning()).toBe(true);
    expect(bridge.getStatus().state).toBe('running');

    // Should have sent the initialized notification
    const writes = proc.stdin.write as ReturnType<typeof vi.fn>;
    const initNotification = writes.mock.calls
      .map((c: any[]) => c[0])
      .find((s: string) => s.includes('"method":"initialized"'));
    expect(initNotification).toBeTruthy();
  }, 10_000);

  it('rejects initialize when process exits immediately', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    proc.exitCode = 1;

    const bridge = new BridgeManager('/fake/root');
    const startPromise = bridge.start();

    // Wait for ready-handshake handlers to be set up
    await new Promise((r) => setTimeout(r, 300));

    // Feed ready signal — ready handshake resolves, then the 250ms
    // exit check will reject because exitCode=1
    feedLine(proc, { type: 'ready' });

    await expect(startPromise).rejects.toThrow(/exited immediately/);
    expect(bridge.getStatus().state).toBe('error');
  });

  it('cleans up all resources on initialize failure', async () => {
    // Enable hot reload so the file watcher is started
    process.env['ELECTRON_RENDERER_URL'] = 'test';
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    const startPromise = bridge.start();
    await new Promise((r) => setTimeout(r, 300));
    feedLine(proc, { type: 'ready' });
    await new Promise((r) => setTimeout(r, 350));

    const initId = findRequestId(proc, 'initialize');
    feedLine(proc, {
      id: initId,
      error: 'Internal error',
      code: 'INTERNAL',
    });

    await expect(startPromise).rejects.toThrow(/Internal error/);

    // 1. readline.close() spy was called (captured by module-level mock)
    expect(rlCloseSpies.length).toBeGreaterThan(0);
    for (const spy of rlCloseSpies) {
      expect(spy).toHaveBeenCalled();
    }
    // 2. watcher is not started until initialization succeeds
    expect(watcherCloses.length).toBe(0);
    // 3. rl and fileWatcher set to null after cleanup
    expect((bridge as any).rl).toBeNull();
    expect((bridge as any).fileWatcher).toBeNull();
    // 4. stdin.end called
    expect(proc.stdin.end).toHaveBeenCalled();
    // 5. process.kill called with SIGTERM
    expect(proc.kill).toHaveBeenCalledWith('SIGTERM');
    // 6. pending cleared (size 0 after cleanup)
    expect((bridge as any).pending.size).toBe(0);
  }, 10_000);

  it('ignores late close events from a previous bridge process after restart', async () => {
    const BridgeManager = await importBridgeManager();
    const firstProc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(firstProc, bridge, { clientId: 'first', serverInfo: { version: '1' } });
    const oldCloseHandler = firstProc.on.mock.calls.find((call) => call[0] === 'close')?.[1] as
      ((code: number | null) => void) | undefined;
    expect(oldCloseHandler).toBeTypeOf('function');

    const stopPromise = bridge.stop();
    await new Promise((r) => setTimeout(r, 10));
    const stopExitHandler = [...firstProc.once.mock.calls]
      .reverse()
      .find((call) => call[0] === 'exit')?.[1] as (() => void) | undefined;
    expect(stopExitHandler).toBeTypeOf('function');
    stopExitHandler?.();
    await stopPromise;

    const secondProc = createMockProcess();
    await startBridge(secondProc, bridge, { clientId: 'second', serverInfo: { version: '1' } });
    expect(bridge.isRunning()).toBe(true);

    oldCloseHandler?.(0);

    expect(bridge.isRunning()).toBe(true);
    expect((bridge as any).process).toBe(secondProc);
    expect((bridge as any).rl).not.toBeNull();
  }, 10_000);

  it('does not mark an intentional stop as an error when the persistent close handler runs first', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'stop-close', serverInfo: { version: '1' } });
    const persistentCloseHandler = proc.on.mock.calls.find((call) => call[0] === 'close')?.[1] as
      ((code: number | null) => void) | undefined;
    expect(persistentCloseHandler).toBeTypeOf('function');

    const stopPromise = bridge.stop();
    await new Promise((r) => setTimeout(r, 10));

    persistentCloseHandler?.(null);
    expect(bridge.getStatus().state).toBe('stopping');

    const stopCloseHandler = [...proc.once.mock.calls]
      .reverse()
      .find((call) => call[0] === 'close')?.[1] as (() => void) | undefined;
    expect(stopCloseHandler).toBeTypeOf('function');
    stopCloseHandler?.();
    await stopPromise;

    expect(bridge.getStatus().state).toBe('stopped');
    expect(bridge.isRunning()).toBe(false);
  }, 10_000);

  it('routes streaming accepted→progress→final without early resolve', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    // Complete initialize
    await startBridge(proc, bridge, { clientId: 'ac', serverInfo: { version: '1' } });

    // Send a streaming request
    const events: { type: string; data: unknown }[] = [];
    let resolved = false;
    let rejected = false;
    const sendPromise = bridge
      .send('chat.send', { message: 'hello' }, (type, data) => {
        events.push({ type, data });
      })
      .then((v) => {
        resolved = true;
        return v;
      })
      .catch(() => {
        rejected = true;
      });

    await new Promise((r) => setTimeout(r, 10));
    const reqId = findRequestId(proc, 'chat.send');

    // 1) accepted response → onEvent('response', ...), promise NOT resolved
    feedLine(proc, { id: reqId, result: { status: 'accepted', taskId: 'task-1' } });
    await new Promise((r) => setTimeout(r, 10));

    expect(events.length).toBe(1);
    expect(events[0].type).toBe('response');
    expect((events[0].data as any).status).toBe('accepted');
    expect(resolved).toBe(false);
    expect(rejected).toBe(false);

    // 2) progress event
    feedLine(proc, { id: reqId, type: 'progress', data: { text: 'thinking...' } });
    await new Promise((r) => setTimeout(r, 10));
    expect(events.length).toBe(2);
    expect(events[1].type).toBe('progress');
    expect(resolved).toBe(false);

    // 3) final event → promise resolves
    feedLine(proc, { id: reqId, type: 'final', data: { text: 'done!' } });
    const result = await sendPromise;
    expect(resolved).toBe(true);
    expect(result).toEqual({ text: 'done!' });
    expect(events.length).toBe(3);
    expect(events[2].type).toBe('final');
  }, 10_000);

  it('rejects streaming promise on error event with data.message envelope', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'err-test', serverInfo: { version: '1' } });

    const events: string[] = [];
    const sendPromise = bridge.send('chat.send', { message: 'hi' }, (type) => {
      events.push(type);
    });

    await new Promise((r) => setTimeout(r, 10));
    const reqId = findRequestId(proc, 'chat.send');

    // accepted
    feedLine(proc, { id: reqId, result: { status: 'accepted' } });
    await new Promise((r) => setTimeout(r, 10));
    expect(events).toContain('response');

    // error event with data.message envelope (real bridge format)
    feedLine(proc, { id: reqId, type: 'error', data: { message: 'Something broke' } });
    await expect(sendPromise).rejects.toThrow('Something broke');
  }, 10_000);

  it('terminal error does not call onEvent (single channel — only promise rejection)', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'sc-test', serverInfo: { version: '1' } });

    // Track all onEvent calls
    const onEventCalls: Array<{ type: string; data: unknown }> = [];
    const sendPromise = bridge.send('chat.send', { message: 'test' }, (type, data) => {
      onEventCalls.push({ type, data });
    });

    await new Promise((r) => setTimeout(r, 10));
    const reqId = findRequestId(proc, 'chat.send');

    // 1) progress event → onEvent called (non-terminal)
    feedLine(proc, { id: reqId, type: 'progress', data: { text: 'thinking...' } });
    await new Promise((r) => setTimeout(r, 10));
    expect(onEventCalls.length).toBeGreaterThanOrEqual(1);
    const progressCount = onEventCalls.length;

    // 2) terminal error event → onEvent NOT called (single channel), only reject
    feedLine(proc, { id: reqId, type: 'error', data: { message: 'Something broke' } });
    await expect(sendPromise).rejects.toThrow('Something broke');
    // onEvent count unchanged — error delivered only through promise rejection
    expect(onEventCalls.length).toBe(progressCount);
  }, 10_000);

  it('rejects all pending on process exit', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'exit-test', serverInfo: { version: '1' } });

    // Start a streaming request
    const events: string[] = [];
    const sendPromise = bridge.send('chat.send', { message: 'test' }, (type) => {
      events.push(type);
    });

    await new Promise((r) => setTimeout(r, 10));
    const reqId = findRequestId(proc, 'chat.send');

    // accepted
    feedLine(proc, { id: reqId, result: { status: 'accepted' } });
    await new Promise((r) => setTimeout(r, 10));
    expect(events).toContain('response');

    // Simulate process exit → should reject pending
    const closeHandler = proc.on.mock.calls.find((c: any[]) => c[0] === 'close')?.[1] as (
      code: number
    ) => void;
    expect(closeHandler).toBeTruthy();
    closeHandler(1);

    await expect(sendPromise).rejects.toThrow('Bridge process exited');
  }, 10_000);

  it('handles aborted terminal event', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'abort-test', serverInfo: { version: '1' } });

    const events: string[] = [];
    const sendPromise = bridge.send('chat.send', { message: 'abort-me' }, (type) => {
      events.push(type);
    });

    await new Promise((r) => setTimeout(r, 10));
    const reqId = findRequestId(proc, 'chat.send');

    // accepted
    feedLine(proc, { id: reqId, result: { status: 'accepted' } });
    await new Promise((r) => setTimeout(r, 10));

    // aborted event → promise resolves
    feedLine(proc, { id: reqId, type: 'aborted', data: { reason: 'user cancelled' } });
    const result = await sendPromise;
    expect(result).toEqual({ reason: 'user cancelled' });
  }, 10_000);

  describe('BridgeManager typed app client', () => {
    it('exposes a typed app client backed by send()', async () => {
      const BridgeManager = await importBridgeManager();
      const bridge = new BridgeManager('/fake/root');
      const sendSpy = vi.spyOn(bridge, 'send').mockResolvedValue({ dataBase64: 'aGVsbG8=' });

      const app = bridge.app();
      const result = await app.request('fs/readFile', {
        path: 'C:/repo/file.txt',
      });

      expect(result).toEqual({ dataBase64: 'aGVsbG8=' });
      expect(sendSpy).toHaveBeenCalledWith('fs/readFile', { path: 'C:/repo/file.txt' }, undefined);
    });
  });

  it('times out streaming request with no terminal event', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'timeout-test', serverInfo: { version: '1' } });

    // Use a custom timeout
    const origSendRequest = (bridge as any).sendRequest.bind(bridge);
    const sendPromise = origSendRequest('chat.send', { message: 'test' }, (() => {}) as any, {
      timeoutMs: 100,
    });

    await new Promise((r) => setTimeout(r, 10));
    const reqId = findRequestId(proc, 'chat.send');

    // accepted — keeps pending alive
    feedLine(proc, { id: reqId, result: { status: 'accepted' } });
    await new Promise((r) => setTimeout(r, 10));

    // Wait for timeout
    await expect(sendPromise).rejects.toThrow(/timed out/);
  }, 10_000);

  it('refreshes chat.send inactivity timeout when progress arrives', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, {
      clientId: 'progress-timeout-test',
      serverInfo: { version: '1' },
    });

    vi.useFakeTimers();
    try {
      let settled = false;
      const origSendRequest = (bridge as any).sendRequest.bind(bridge);
      const sendPromise = origSendRequest('chat.send', { message: 'test' }, (() => {}) as any, {
        timeoutMs: 100,
      });
      sendPromise.then(
        () => {
          settled = true;
        },
        () => {
          settled = true;
        }
      );

      await vi.advanceTimersByTimeAsync(10);
      const reqId = findRequestId(proc, 'chat.send');

      feedLine(proc, { id: reqId, result: { status: 'accepted' } });
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(90);
      expect(settled).toBe(false);

      feedLine(proc, { id: reqId, type: 'progress', data: { text: 'still running' } });
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(99);
      expect(settled).toBe(false);

      feedLine(proc, { id: reqId, type: 'final', data: { text: 'done' } });
      await Promise.resolve();
      await expect(sendPromise).resolves.toEqual({ text: 'done' });
    } finally {
      vi.useRealTimers();
      proc.stdout.destroy();
      proc.stderr.destroy();
    }
  }, 10_000);

  it('keeps chat.send client timeout later than the backend drain timeout', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, {
      clientId: 'default-timeout-test',
      serverInfo: { version: '1' },
    });

    vi.useFakeTimers();
    try {
      let settled = false;
      const sendPromise = bridge.send('chat.send', { message: 'test' }, (() => {}) as any);
      sendPromise.then(
        () => {
          settled = true;
        },
        () => {
          settled = true;
        }
      );

      await vi.advanceTimersByTimeAsync(CHAT_BACKEND_DRAIN_TIMEOUT_MS + 1);
      expect(settled).toBe(false);

      await vi.advanceTimersByTimeAsync(CHAT_SEND_TIMEOUT_MS - CHAT_BACKEND_DRAIN_TIMEOUT_MS - 1);
      await expect(sendPromise).rejects.toThrow(/Request chat\.send timed out/);
    } finally {
      vi.useRealTimers();
      proc.stdout.destroy();
      proc.stderr.destroy();
    }
  }, 10_000);

  it('hot reload waits for old bridge close before spawning replacement', async () => {
    process.env['ELECTRON_RENDERER_URL'] = 'test';
    const BridgeManager = await importBridgeManager();
    const firstProc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(firstProc, bridge, {
      clientId: 'reload-test',
      serverInfo: { version: '1' },
    });
    expect(watchCallbacks.length).toBe(1);
    expect(mockSpawn).toHaveBeenCalledTimes(1);

    const secondProc = createMockProcess();
    const restartPromise = (bridge as any).restart() as Promise<void>;
    await new Promise((r) => setTimeout(r, 50));

    expect(firstProc.stdin.end).toHaveBeenCalled();
    expect(firstProc.kill).toHaveBeenCalledWith('SIGTERM');
    expect(watcherCloses[0]).toHaveBeenCalled();
    expect(mockSpawn).toHaveBeenCalledTimes(1);

    const closeHandlers = firstProc.once.mock.calls.filter((c: any[]) => c[0] === 'close');
    const restartClose = closeHandlers[closeHandlers.length - 1]?.[1] as () => void;
    expect(restartClose).toBeTruthy();
    restartClose();

    await new Promise((r) => setTimeout(r, 300));
    expect(mockSpawn).toHaveBeenCalledTimes(2);
    feedLine(secondProc, { type: 'ready' });
    await new Promise((r) => setTimeout(r, 350));
    const initId = findRequestId(secondProc, 'initialize');
    feedLine(secondProc, {
      id: initId,
      result: { clientId: 'reload-test', serverInfo: { version: '2' } },
    });

    await restartPromise;
    expect(bridge.isRunning()).toBe(true);
    expect(bridge.isInitialized()).toBe(true);
    expect(watchCallbacks.length).toBe(2);
  }, 10_000);

  it('tracks sandboxAvailable from sandbox.ready bridge event', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'sandbox-ready', serverInfo: { version: '1' } });

    expect(bridge.sandboxAvailable).toBe(false);

    feedLine(proc, { request_id: 'ev-sr-1', event: 'sandbox.ready', data: { initialized: true } });
    await new Promise((r) => setTimeout(r, 50));

    expect(bridge.sandboxAvailable).toBe(true);
  }, 10_000);

  it('sandbox.ready with initialized=false keeps sandboxAvailable false', async () => {
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge, { clientId: 'sandbox-fail', serverInfo: { version: '1' } });

    bridge.sandboxAvailable = true;

    feedLine(proc, {
      request_id: 'ev-sr-2',
      event: 'sandbox.ready',
      data: { initialized: false, error: 'bwrap not found' },
    });
    await new Promise((r) => setTimeout(r, 50));

    expect(bridge.sandboxAvailable).toBe(false);
  }, 10_000);

  it('starts hot reload watcher by default in dev renderer mode', async () => {
    process.env['ELECTRON_RENDERER_URL'] = 'test';
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge);

    expect((bridge as any).hotReloadEnabled).toBe(true);
  });

  it('skips hot reload restart when there are pending requests', async () => {
    process.env['ELECTRON_RENDERER_URL'] = 'test';
    const BridgeManager = await importBridgeManager();
    const proc = createMockProcess();
    const bridge = new BridgeManager('/fake/root');

    await startBridge(proc, bridge);
    expect((bridge as any).hotReloadEnabled).toBe(true);

    // Inject a pending request to simulate an active session
    (bridge as any).pending.set('req-1', {
      resolve: () => {},
      reject: () => {},
      method: 'chat.send',
      timestamp: Date.now(),
    });

    // Trigger file change — should skip restart because pending > 0
    const logs: string[] = [];
    const origAddLog = (bridge as any).addLog.bind(bridge);
    (bridge as any).addLog = (msg: string) => {
      logs.push(msg);
      origAddLog(msg);
    };

    (bridge as any).handleFileChange('test.py');

    // Should have deferred restart (not skipped silently — #387)
    expect(logs.some((l: string) => l.includes('Deferring restart'))).toBe(true);
    expect((bridge as any).deferredRestart).toBe(true);
  });
});

describe('BridgeManager sandbox tracking', () => {
  it('defaults sandboxAvailable to false', async () => {
    const BridgeManager = await importBridgeManager();
    const bridge = new BridgeManager('/fake/root');
    expect(bridge.sandboxAvailable).toBe(false);
  });

  it('getStatus reflects sandboxAvailable', async () => {
    const BridgeManager = await importBridgeManager();
    const bridge = new BridgeManager('/fake/root');
    bridge.sandboxAvailable = true;
    expect(bridge.getStatus().sandbox_available).toBe(true);

    bridge.sandboxAvailable = false;
    expect(bridge.getStatus().sandbox_available).toBe(false);
  });

  it('emitState is callable so ipc layer can notify renderer', async () => {
    const BridgeManager = await importBridgeManager();
    const bridge = new BridgeManager('/fake/root');
    expect(typeof bridge.emitState).toBe('function');
    expect(() => bridge.emitState()).not.toThrow();
  });
});
