/**
 * Repro/regression test for issue #570 — [ChatConsole] new session + send →
 * UI completely silent.
 *
 * The bug (fixed in this change):
 *   `ChatConsole.load()` retries `sessions.get` 10× with exponential backoff
 *   (500ms → 10s).  When the bridge is not running, `sessions.get` returns
 *   null every time.  Pre-fix, the retry window showed only a bare `Loader2`
 *   spinner (historyLoaded stays false, ChatConsole.tsx:4552) with no
 *   "正在连接…" text, and after exhaustion (ChatConsole.tsx:2270) the code
 *   only `console.warn`ed + `setHistoryLoaded(true)`, swapping the spinner for
 *   the blank empty state — no error bubble, the user saw silence.
 *
 * The fix: a "正在连接…" hint during the retry window, and an explicit
 * "会话加载失败…" error message after the retries are exhausted.
 *
 * This is a REGRESSION test — it asserts the DESIRED behaviour (connecting
 * hint + explicit error), so it FAILED on pre-fix code and passes now.
 *
 * Trigger: patch the main-process `sessions:get` IPC handler to return null
 * (via electronApp.evaluate), so every `window.miqi.sessions.get` invoke hits
 * the patched handler and returns null — the exact "backend unavailable" state
 * the retry loop guards against.  This is environment-independent (no reliance
 * on a specific broken python), so it runs identically on Windows dev machines
 * and Linux CI.  contextBridge freezes window.miqi.* in the renderer, so a
 * page.evaluate override would be silently dropped — patching ipcMain in the
 * main process is the working approach (same pattern as
 * workspace-file-read-edit.spec.ts for dialog:openDirectory).
 *
 * Note on reload: the app is launched with a HEALTHY bridge, so the session
 * loads normally at first.  The test patches sessions:get → null, then reloads
 * the renderer so ChatConsole remounts and runs a FRESH 10× retry under the
 * patched handler (which survives reload — ipcMain is in the main process).
 *
 * Run:
 *   cd apps/desktop
 *   npm run build
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *     npx playwright test --config=playwright.config.ts --project=electron \
 *       repro-570-silent-send --reporter=list
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

// The full retry window: 500+1000+2000+4000+8000+10000*5 ≈ 57s.
const RETRY_WINDOW_MS = 57_000;
const SESSIONS_GET = 'sessions:get';

test.describe('Repro #570: silent UI on session open with no bridge', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.afterAll(async () => {
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('unavailable backend → connecting hint during retry, explicit error after exhaustion', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;

    // Capture renderer console so we can PROVE the retry loop ran — it only
    // ever surfaces in the console, never in the UI (that's the bug).
    const consoleLines: string[] = [];
    page.on('console', (msg) => consoleLines.push(msg.text()));

    // ── Make the backend unavailable: patch the main-process IPC handler so
    //    `sessions.get` returns null.  contextBridge freezes window.miqi.* in
    //    the renderer, so this must be done in the main process (electronApp
    //    .evaluate) — ipcMain survives renderer reloads, so the patch stays
    //    active after the reload below. ──
    await electronApp.evaluate(async ({ ipcMain: ipc }, channel: string) => {
      ipc.removeHandler(channel);
      ipc.handle(channel, async () => null);
    }, SESSIONS_GET);

    // Reload the renderer so ChatConsole remounts and starts a FRESH 10×
    // retry under the patched handler (the initial mount already loaded the
    // session successfully with the healthy bridge).
    await page.reload();
    await page.waitForLoadState('domcontentloaded');
    await page
      .locator('[data-testid="chat-input-container"]')
      .waitFor({ state: 'attached', timeout: 15_000 });

    // ── Desired #1: during the ~57s retry window the UI should show a
    //    connecting/loading hint ("正在连接…").  Sample across the window —
    //    pre-fix code showed only a bare spinner with zero explanation. ──
    const samples: string[] = [];
    for (let i = 0; i < 8; i++) {
      samples.push((await page.locator('main').textContent()) ?? '');
      await page.waitForTimeout(3000);
    }
    expect(
      samples.join('\n'),
      'during the 10× retry the UI should show a connecting/loading explanation'
    ).toMatch(/连接|加载|重试|connecting|loading/i);

    // ── Desired #2: after the retries are exhausted the user should get an
    //    explicit error message (pre-fix code swapped the spinner for the
    //    blank empty state with no error at all). ──
    await page.waitForTimeout(RETRY_WINDOW_MS);
    const afterText = (await page.locator('main').textContent()) ?? '';
    expect(
      afterText,
      'after 10 exhausted retries the UI should show an explicit error message'
    ).toMatch(/加载失败|连接失败|重试失败|会话加载失败|出错|失败|error/i);

    // ── Desired #3: the error bubble is not a dead end — its "重试" button
    //    must trigger a fresh load (a new "Load attempt 1/10" console log),
    //    so a user (or the #480 slow-start path) can recover once the bridge
    //    comes back. ──
    const attemptsBefore = consoleLines.filter((l) =>
      /Load attempt \d+\/10 returned null/.test(l)
    ).length;
    const retryBtn = page.getByRole('button', { name: '重试' });
    await retryBtn.click({ timeout: 5_000 });
    await page.waitForTimeout(1000);
    const attemptsAfter = consoleLines.filter((l) =>
      /Load attempt \d+\/10 returned null/.test(l)
    ).length;
    expect(
      attemptsAfter,
      'clicking 重试 on the error bubble should trigger a fresh load() attempt'
    ).toBeGreaterThan(attemptsBefore);

    // The retry loop DID run (proven via console) and, thanks to the #570
    // fix, the UI now surfaced a connecting hint during the window and an
    // explicit error after it exhausted.
    const loadAttemptLines = consoleLines.filter((l) =>
      /Load attempt \d+\/10 returned null/.test(l)
    );
    expect(
      loadAttemptLines.length,
      'ChatConsole.load() retry loop ran (proven via console logs)'
    ).toBeGreaterThan(0);
    console.log(
      `[e2e] Retry loop ran ${loadAttemptLines.length} attempts before exhaustion — ` +
        `UI showed connecting hint + error after fix, 重试 button re-triggered load. ` +
        `First: ${loadAttemptLines[0]}`
    );
  });
});
