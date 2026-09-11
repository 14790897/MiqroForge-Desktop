/**
 * Bridge Chinese Error E2E — dirty API key + Chinese error regression test.
 *
 * Root cause of the reported crash: the user pasted an API key and typed a
 * Chinese note into the same field, so the stored key was
 * ``"sk-…  用这个"``. When the provider call built the
 * ``Authorization: Bearer …`` header, httpx raised
 * ``UnicodeEncodeError: 'ascii' codec can't encode characters`` — BEFORE any
 * HTTP request left the process. The task runner caught it and surfaced the
 * encoding crash instead of the real error.
 *
 * Fixes under test:
 *   1. miqi/config/schema.py — ProviderConfig.api_key sanitizer strips
 *      whitespace and non-ASCII annotation characters at config load, so a
 *      dirty key self-heals to the clean key.
 *   2. apps/desktop/src/main/bridge.ts — PYTHONIOENCODING=utf-8 on the bridge
 *      spawn (plus miqi/bridge/server.py's stdio reconfigure), so Chinese
 *      error text logged to stderr can never crash the bridge on ASCII
 *      locales and mask the real error.
 *
 * The spec injects the dirty key into the copied config (the real user
 * config is kept clean), points every provider at a local mock that always
 * answers 500 with a Chinese error body (scripts/mock_openai_error.py), then
 * sends a Chinese message and asserts the user-visible contract:
 *   - the request really reaches the mock (proves the dirty key was healed,
 *     otherwise httpx crashes before any HTTP call),
 *   - the bridge survives the turn (still 'running'),
 *   - the UI shows the friendly Chinese error, not a UnicodeEncodeError.
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron \
 *      -g "dirty API key"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/**
 * Friendly error the legacy runtime surfaces for TRANSIENT provider errors
 * (task_runner.py:1082 — a 500 from the mock classifies as transient and
 * gets this fixed, non-leaking message; the raw provider text is
 * intentionally NOT shown in the UI). The original crash surfaced
 * `UnicodeEncodeError: 'ascii' codec ...` instead of this message.
 */
const EXPECTED_ERROR_MESSAGE = '模型服务暂时不可用或过载，请稍后重试。';

/**
 * Distinctive marker inside scripts/mock_openai_error.py's ERROR_MESSAGE.
 * The UI never shows it (transient errors are deliberately sanitized), but
 * the raw Chinese provider error is logged by the bridge to stderr — the
 * exact log path the encoding bug used to crash on.
 */
const MOCK_ERROR_PHRASE = '模拟服务故障';

/**
 * Start scripts/mock_openai_error.py on an ephemeral port and wait for its
 * startup line (same pattern as confirm-card.spec.ts's startMockOpenAI).
 * Collects the mock's stdout lines so the test can prove the provider
 * call actually reached it.
 */
async function startMockErrorServer(): Promise<{
  proc: ChildProcess;
  mockUrl: string;
  mockLog: () => string[];
}> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_openai_error.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  const logLines: string[] = [];
  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock-err-server] ${t.trim()}`);
    logLines.push(t);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-err-server-err] ${String(d).trim()}`);
  });
  proc.on('exit', (code) => console.log(`[test] mock error server exited: ${code}`));

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock error server exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock error server startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[test] mock error server ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl, mockLog: () => logLines };
}

test.describe('Dirty API key + Chinese provider error', () => {
  // macOS CI cannot run this spec: the runner's undici fetch fails against
  // a local 127.0.0.1 listener and the spawned mock's stdout pipe never
  // delivers (both observed in macos-e2e). Same trimming strategy as
  // confirm-card.spec.ts (#710).
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;
  let mockLog: () => string[];
  // Main-process stdout/stderr — the bridge's own stderr is forwarded there
  // via console.log('[bridge] …') in src/main/bridge.ts.
  const mainProcessLines: string[] = [];

  test.beforeAll(async () => {
    const mock = await startMockErrorServer();
    mockServer = mock.proc;
    mockLog = mock.mockLog;
    const fixture = await launchElectronApp((config: any) => {
      // Point EVERY configured provider at the mock — provider resolution
      // depends on the model in agents.defaults, so patching a single
      // provider would leak real API calls in CI. The mock ignores model
      // names and API keys.
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.mockUrl;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      // Reproduce the reported bug on the ACTIVE provider: a key with a
      // Chinese annotation typed into the same field. Pin the default model
      // to deepseek so the dirty deepseek key is always the one exercised
      // (in CI agents.defaults may select siliconflow, which would make the
      // sanitizer path untested). The deepseek entry may not exist in the
      // copied config (CI only configures siliconflow), so create it with
      // BOTH fields — without apiBase the provider falls back to the real
      // api.deepseek.com default and the dirty key 401s there instead of
      // reaching the mock. The schema sanitizer must heal the key to the
      // clean value — otherwise httpx dies building the Authorization
      // header and the request below never reaches the mock.
      config.agents = config.agents ?? {};
      config.agents.defaults = config.agents.defaults ?? {};
      config.agents.defaults.model = 'deepseek/deepseek-chat';
      const deepseek = (providers as any).deepseek ?? {};
      deepseek.apiBase = mock.mockUrl;
      deepseek.apiKey = `${deepseek.apiKey ?? 'sk-mock-key'}  用这个`;
      (providers as any).deepseek = deepseek;
      config.providers = providers;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    const collect = (d: Buffer | string) => {
      for (const l of String(d).split(/\r?\n/)) if (l.trim()) mainProcessLines.push(l);
    };
    electronApp.process().stdout?.on('data', collect);
    electronApp.process().stderr?.on('data', collect);
  }, 180_000);

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'dirty key is healed, Chinese provider error surfaces, bridge survives',
    { timeout: LLM_TIMEOUT },
    async () => {
      await waitForBridgeInitialized(page, 30);

      const marker = `中文报错_${Date.now()}`;
      console.log(`[bridge-chinese-error] Sending: "${marker}"`);
      await sendMessage(page, `请帮我调用一次模型 ${marker}`);

      console.log('[bridge-chinese-error] Waiting for the error turn to finish…');
      await waitForResponseComplete(page);

      // The bridge must still be alive after logging the provider's Chinese
      // error message — pre-fix, the logger raised UnicodeEncodeError.
      const status = await page.evaluate(() => (window as any).miqi.runtime.status());
      expect(status?.state, 'bridge must survive the Chinese error turn').toBe('running');

      // Prove the provider call really reached the mock (otherwise the error
      // above could come from a config problem instead of the provider).
      const mockLines = mockLog().join('\n');
      console.log(
        `[bridge-chinese-error] mock saw ${mockLines.split('\n').length - 1} request line(s)`
      );
      if (!mockLines.includes('/v1/chat/completions')) {
        // Diagnostic: dump the tail of the main process (bridge stderr) log.
        console.log(
          '[bridge-chinese-error] main-process log tail:\n' + mainProcessLines.slice(-80).join('\n')
        );
      }
      expect(mockLines).toContain('/v1/chat/completions');

      // The raw Chinese provider error (mock's distinctive marker) must have
      // survived bridge logging — task_runner logs the full exception to
      // stderr, which the main process forwards to its own stdout. This is
      // the exact path that crashed with UnicodeEncodeError pre-fix.
      const mainProcessText = mainProcessLines.join('\n');
      console.log(`[bridge-chinese-error] main-process log has ${mainProcessLines.length} lines`);
      if (!mainProcessText.includes(MOCK_ERROR_PHRASE)) {
        console.log(
          '[bridge-chinese-error] main-process log tail:\n' + mainProcessLines.slice(-80).join('\n')
        );
      }
      expect(mainProcessText).toContain(MOCK_ERROR_PHRASE);

      // The UI intentionally shows the friendly generic error (transient
      // errors are sanitized by design) — never the encoding crash.
      const mainText = (await page.locator('main').textContent()) ?? '';
      console.log(`[bridge-chinese-error] main text after error turn: ${mainText.slice(-500)}`);
      expect(mainText).not.toContain('UnicodeEncodeError');
      expect(mainText).toContain(EXPECTED_ERROR_MESSAGE);
    }
  );
});
