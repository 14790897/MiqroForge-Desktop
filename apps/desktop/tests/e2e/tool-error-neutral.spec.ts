/**
 * E2E: 工具级失败（会话隔离 PermissionError）渲染为中性警示行，而非红色报错框。
 *
 * Regression coverage for issue #921.  A tool-level failure (ToolErrorEvent,
 * recoverable=True) is part of the agent's normal self-correction loop — the
 * model receives the failure result and adapts.  Rendering it with the red
 * danger bubble (ChatConsole role 'error') makes a routine self-correction
 * look like a system crash, indistinguishable from turn-level errors
 * (timeout / provider failure).
 *
 * Two tests cover the contract at different layers:
 *
 * Test A (real chain, WSL sandbox required): scripts/mock_cross_session_read.py
 *   drives the model to call read_file on ANOTHER session's files dir.  With
 *   the WSL sandbox active, _resolve_sandbox_path raises the session-isolation
 *   PermissionError BEFORE any try/except (the exact production path from the
 *   #921 report) — the orchestrator wraps it as ToolErrorEvent and the UI
 *   must render the sanitized message as a ⚠️ warning row, not a red bubble.
 *   Skipped on CI without MIQI_RUN_SANDBOX_E2E=1 (same as sandbox-exec.spec).
 *
 * Test B (rendering contract, sandbox-free): the frontend only registers its
 *   progress listeners while a turn is in flight, so the spec starts a real
 *   send against scripts/mock_hang.py (a provider mock that never responds,
 *   keeping the turn alive), then injects the ToolErrorEvent payload into
 *   the REAL renderer through the same IPC channel the bridge uses.
 *   Pre-fix, the same payload rendered as the red error bubble and both
 *   tests' danger-color assertion fails.
 *
 * Run:
 *   cd apps/desktop && npm run build && npx playwright test \
 *     --config=playwright.config.ts --project=electron \
 *     -g "session isolation tool error"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  waitForBridgeInitialized,
  waitForSandboxReady,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  getMiqiSessionsDir,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** Danger colors used by the red error bubble (dark + light theme). */
const DANGER_COLORS = ['rgb(255, 97, 97)', 'rgb(192, 64, 64)'];

const ISOLATION_PHRASE = '会话隔离禁止跨会话访问';

/** The exact ToolErrorEvent payload the backend sends for the session-
 *  isolation PermissionError (message shaped by _sanitize_exc_for_ui). */
const TOOL_ERROR_PAYLOAD = {
  event: 'ToolErrorEvent',
  data: {
    turn_id: 'e2e-turn',
    tool_name: 'read_file',
    tool_call_id: 'call_cross_read',
    message:
      'PermissionError: 路径位于其他会话的 files 目录内——会话隔离禁止跨会话访问。 不要重试或枚举 sessions[path]',
    recoverable: true,
  },
};

/** Start a mock provider server (script name under scripts/) on an
 *  ephemeral port and wait for its startup line. */
async function startMockServer(script: string): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', script), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock-${script}] ${t.trim()}`);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
  });
  proc.on('exit', (code) => console.log(`[test] mock ${script} exited: ${code}`));

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock ${script} exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock ${script} startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[test] mock ${script} ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl };
}

/** Point every configured provider at *mockUrl* and pin the default model
 *  to deepseek so the patched provider is always exercised — no real API
 *  call may ever leave the specs. */
function patchProvidersToMock(config: any, mockUrl: string): void {
  const providers = config.providers ?? {};
  for (const [name, p] of Object.entries(providers)) {
    if (p && typeof p === 'object') {
      (p as any).apiBase = mockUrl;
      if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
    }
  }
  config.agents = config.agents ?? {};
  config.agents.defaults = config.agents.defaults ?? {};
  config.agents.defaults.model = 'deepseek/deepseek-chat';
  const deepseek = (providers as any).deepseek ?? {};
  deepseek.apiBase = mockUrl;
  deepseek.apiKey = `${deepseek.apiKey ?? 'sk-mock-key'}`;
  (providers as any).deepseek = deepseek;
  config.providers = providers;
}

/** Resolve the REAL session key of the conversation just created/seeded
 *  (progress events filter on the key).  #774：空会话不落盘、不进
 *  sessions.list——直到首条真实消息写入才成为会话，故轮询等待列表出现会话。 */
async function resolveActiveSessionKey(page: Page): Promise<string> {
  let key = '';
  for (let attempt = 0; attempt < 30 && !key; attempt += 1) {
    key = await page.evaluate(async () => {
      const list = await (window as any).miqi.sessions.list();
      const sessions = (list?.sessions ?? []) as Array<{ key: string; created_at?: string }>;
      sessions.sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
      return sessions[sessions.length - 1]?.key ?? '';
    });
    if (!key) await page.waitForTimeout(1000);
  }
  expect(
    key,
    'session key must resolve (empty session persists only after the first message, #774)'
  ).not.toBe('');
  return key;
}

/** Assert no element carrying the isolation message renders in the danger
 *  color (pre-fix the message sat inside the red error bubble). */
async function assertNoDangerRendering(page: Page): Promise<void> {
  const dangerHits = await page.evaluate(
    ({ dangerColors, phrase }) => {
      const hits: string[] = [];
      for (const el of Array.from(document.querySelectorAll('div, span, p'))) {
        const text = (el as HTMLElement).textContent ?? '';
        if (!text.includes(phrase)) continue;
        const color = getComputedStyle(el).color;
        if ((dangerColors as string[]).includes(color)) hits.push(color);
      }
      return hits;
    },
    { dangerColors: DANGER_COLORS, phrase: ISOLATION_PHRASE }
  );
  expect(dangerHits, 'isolation message must not render in the danger color').toHaveLength(0);
}

const SKIP_SANDBOX_ON_CI = !!process.env.CI && process.env.MIQI_RUN_SANDBOX_E2E !== '1';
// macOS CI cannot run the mock-based specs: the runner's undici fetch fails
// against a local 127.0.0.1 listener and the spawned mock's stdout pipe
// never delivers (same trimming strategy as confirm-card.spec.ts #710).
const SKIP_MOCK_ON_MACOS_CI = process.platform === 'darwin' && !!process.env.CI;

// ── Test A: real chain with the WSL sandbox (production repro) ─────────

test.describe('Session isolation tool error rendering (real chain, sandbox)', () => {
  test.skip(SKIP_MOCK_ON_MACOS_CI || SKIP_SANDBOX_ON_CI, 'requires local mock + WSL sandbox');

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockServer('mock_cross_session_read.py');
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) => {
      patchProvidersToMock(config, mock.mockUrl);
      // Enable the WSL sandbox explicitly — the user's dev config has it
      // disabled, but the reported bug fires on the sandbox read path.
      config.tools = {
        ...config.tools,
        sandbox: { ...config.tools?.sandbox, enabled: true },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    await waitForBridgeInitialized(page);
    const ready = await waitForSandboxReady(page, 480_000);
    if (!ready) {
      throw new Error('Sandbox manager did not become ready within 480s');
    }
  }, 600_000);

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'cross-session read raises PermissionError → rendered as ⚠️ warning row',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      await createNewConversation(page);

      // The probe path lives under ANOTHER session's files dir — the
      // isolation check triggers on path RESOLUTION before any read.
      const probeDir = join(getMiqiSessionsDir(miqiHome), 'e2e_other_probe', 'files');
      mkdirSync(probeDir, { recursive: true });
      writeFileSync(join(probeDir, 'probe.txt'), 'cross-session probe');

      const message = [
        '请读取另一个会话的文件，测试会话隔离。',
        '__CROSS_READ_PATH_BEGIN__',
        join(probeDir, 'probe.txt'),
        '__CROSS_READ_PATH_END__',
      ].join('\n');
      await sendMessage(page, message);

      // The mock delays its final reply by 3s after the tool failure, so
      // the ⚠️ warning row must be asserted WHILE the turn is in flight —
      // once the turn completes the frontend rebuilds the timeline from
      // history and the transient row is gone.
      console.log('[tool-error-neutral] Waiting for the ⚠️ warning row mid-turn…');
      await expect
        .poll(
          () =>
            page.evaluate((phrase) => {
              const body = document.body?.textContent ?? '';
              return (
                body.includes('⚠️ PermissionError: 路径位于其他会话的 files 目录内') &&
                body.includes(phrase)
              );
            }, ISOLATION_PHRASE),
          { timeout: 60_000 }
        )
        .toBe(true);
      console.log('[tool-error-neutral] ✅ isolation message is visible mid-turn');

      // …and NOT in the danger color (pre-fix: red error bubble).
      await assertNoDangerRendering(page);
      console.log('[tool-error-neutral] ✅ no danger-colored element carries the message');

      // Screenshot for the PR (real production scenario, after the fix) —
      // taken while the row is still visible mid-turn.
      await page.screenshot({
        path: join(REPO_ROOT, 'docs', 'screenshots', 'tool-error-warning-after.png'),
      });
      console.log('[tool-error-neutral] ✅ screenshot saved to docs/screenshots/');

      console.log('[tool-error-neutral] Waiting for the turn to finish…');
      await waitForResponseComplete(page, 240_000);

      // The turn still completes — recoverable by design.
      const mainText = (await page.locator('main').textContent()) ?? '';
      expect(mainText).toContain('跨会话读取已被会话隔离策略拒绝，任务结束。');
      console.log('[tool-error-neutral] ✅ turn completed normally');
    }
  );
});

// ── Test B: rendering contract via injected ToolErrorEvent (sandbox-free) ─

test.describe('Session isolation tool error rendering (injected event)', () => {
  test.skip(SKIP_MOCK_ON_MACOS_CI, 'macOS CI cannot reach the local mock server');

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockServer('mock_hang.py');
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) => {
      patchProvidersToMock(config, mock.mockUrl);
      // No sandbox needed — the frontend rendering contract is under test.
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    await waitForBridgeInitialized(page);
  });

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'ToolErrorEvent renders as a neutral warning row, not a red error bubble',
    { timeout: 120_000 },
    async () => {
      await createNewConversation(page);
      // #774：空会话不落盘、不进 sessions.list——先发首条真实消息让会话落盘
      // 并进入回合（前端仅在回合进行中注册 chat 监听，挂起 mock 保持回合存活），
      // 再从 sessions.list 解析 session key 用于注入 ToolErrorEvent。
      await sendMessage(page, `会话隔离渲染回归测试 ${Date.now()}`);
      await page.waitForTimeout(3000);
      const sessionKey = await resolveActiveSessionKey(page);
      console.log(`[tool-error-neutral] active session key: ${sessionKey}`);

      // Inject the events the backend would emit during a real turn, on the
      // same IPC channel the bridge forwards ('chat:progress'): first a
      // tool-call begin row (like ToolCallBeginEvent), then the
      // ToolErrorEvent itself.
      const injectProgress = (payload: Record<string, unknown>) =>
        electronApp.evaluate(({ BrowserWindow }, p) => {
          const win = BrowserWindow.getAllWindows().find(
            (w) => w.getTitle() === 'MiQroForge Desktop'
          );
          if (!win) throw new Error('main window not found');
          win.webContents.send('chat:progress', p);
        }, payload);

      await injectProgress({
        text: 'read_file 读取其他会话文件',
        tool_hint: true,
        tool_call_id: 'call_cross_read',
        session_key: sessionKey,
      });
      await injectProgress({ ...TOOL_ERROR_PAYLOAD, session_key: sessionKey });

      // 1. The full isolation message must reach the UI as a ⚠️ warning row
      //    (the row renders msg.content in full — pre-#921 it was a red
      //    error bubble; the truncated chain-label path cut the body off).
      const expectedRowText = `⚠️ ${TOOL_ERROR_PAYLOAD.data.message}`;
      await expect
        .poll(
          () =>
            page.evaluate(
              (fullText) =>
                Array.from(document.querySelectorAll('div, span')).some((el) =>
                  (el.textContent ?? '').includes(fullText)
                ),
              expectedRowText
            ),
          { timeout: 15_000 }
        )
        .toBe(true);
      console.log('[tool-error-neutral] ✅ warning row is visible with full message');

      // 2. …but NOT in the danger color.
      await assertNoDangerRendering(page);
      console.log('[tool-error-neutral] ✅ no danger-colored element carries the message');
    }
  );
});
