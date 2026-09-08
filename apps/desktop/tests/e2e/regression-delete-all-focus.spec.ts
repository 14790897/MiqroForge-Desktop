/**
 * Delete-last-conversation focus regression E2E.
 *
 * User report: deleting ALL conversations leaves the empty welcome with a
 * non-interactive input — clicking the textarea gives no caret and keyboard is
 * swallowed until a manual refresh. Root cause (manual repro, instrumented):
 * after the window.confirm modal of the delete closes, Chromium can fail to
 * hand PAGE focus back to the renderer, so document.hasFocus() stays false
 * even though the window is OS-focused and activeElement is the textarea;
 * real key events are only routed to an editable when the document has focus.
 * Fix: ChatConsole re-focuses the input on entering the empty welcome and, if
 * document.hasFocus() is still false ~420ms later, asks main for a hard
 * reactivation (win.blur()→focus()) that forces Chromium to re-deliver page
 * focus.
 *
 * What this spec can assert under automation (where hasFocus is never lost):
 * the delete-last→welcome transition still shows the composer textarea focused
 * and typable with no click — guarding the DOM half.  The OS page-focus half is
 * manual-only and not reproducible under Playwright.
 *
 * Run: npx playwright test --config=playwright.config.ts --project=electron -g "delete-all focus"
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  waitForInputReady,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
  createNewConversation,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** Start the deterministic plain-reply mock and wait for its bound URL. */
async function startPlainMock(): Promise<{ proc: ChildProcess; url: string }> {
  // Windows venv 用 Scripts/python.exe,posix 用 bin/python。CI electron-e2e
  // 在 ubuntu 上跑——写死 Windows 路径 spawn ENOENT,mock 起不来、seed 全挂。
  const python =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(
    python,
    [join(APPS_DESKTOP, 'tests', 'e2e', 'fixtures', 'plain_reply_mock.py'), String(port)],
    {
      cwd: REPO_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      windowsHide: true,
    }
  );
  let url = '';
  let errTail = '';
  proc.stdout?.on('data', (d) => {
    const m = String(d).match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) url = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => (errTail = (errTail + String(d)).slice(-2000)));
  const deadline = Date.now() + 30_000;
  while (!url && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      proc.kill();
      throw new Error(`plain mock exited early: ${errTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!url) {
    proc.kill();
    throw new Error(`plain mock startup line not seen in 30s: ${errTail}`);
  }
  return { proc, url };
}

test.describe('Delete-all focus regression', () => {
  // Same localhost restriction as confirm-card.spec: the macOS CI runner's
  // undici fetch cannot reach a local 127.0.0.1 listener.
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mock: ChildProcess;

  test.beforeAll(async () => {
    const m = await startPlainMock();
    mock = m.proc;
    const fixture = await launchElectronApp((config: any) => {
      // Point EVERY configured provider at the mock (provider resolution is
      // driven by agents.defaults.model) — mock ignores model names/keys.
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = m.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mock?.kill();
  });

  function sidebarItems() {
    return page.locator('div.flex.flex-col.shrink-0.border-r').first().locator('button.rounded-xl');
  }

  function composerTextarea() {
    return page.locator('[data-testid="chat-input-container"] textarea').first();
  }

  test(
    'seed one conversation, delete it, land on welcome with a focused, typable input',
    { timeout: 300_000 },
    async () => {
      // ── Seed a real conversation (plain reply from the mock) so a sidebar
      //    card exists to delete.  Fresh launches list nothing until the first
      //    message is persisted.  A cold start spins up the agent thread in the
      //    background ("正在连接…" = history not loaded yet); firing a send
      //    before that thread is ready drops the message (optimistic bubble
      //    rolls back), so wait for the loading state to clear first, then
      //    retry the seed a couple of times for provider warm-up.
      const seedText = '请回复一句话';
      const userBubbles = page.getByTestId('chat-message-user');
      for (let attempt = 0; attempt < 3; attempt++) {
        const connecting = page.locator('main').getByText('正在连接…');
        await connecting.waitFor({ state: 'hidden', timeout: 120_000 }).catch(() => {
          console.log(`[test] ⚠️ seed #${attempt + 1}: "正在连接…" never cleared — sending anyway`);
        });
        const textarea = await waitForInputReady(page, 60_000);
        const before = await userBubbles.count();
        // fill() + Enter submits in this app (cf. kwp-commands / reasoning-mode /
        // execution-policy specs); type() races the cold-start runtime handshake.
        await textarea.fill(seedText);
        await textarea.press('Enter');
        try {
          await expect(userBubbles).toHaveCount(before + 1, { timeout: 60_000 });
          break;
        } catch {
          console.log(`[test] ⚠️ seed send #${attempt + 1} produced no bubble — retrying`);
          await page.waitForTimeout(2000);
        }
      }
      await expect(userBubbles).toHaveCount(1, { timeout: 10_000 });
      await waitForResponseComplete(page, 60_000);
      await expect
        .poll(async () => sidebarItems().count(), { timeout: 30_000 })
        .toBeGreaterThanOrEqual(1);

      // ── Delete the (last/only) conversation via sidebar context menu.
      //    Auto-accept the window.confirm.
      // Auto-accept the window.confirm. once()——shared Page 上两个 test 各注册
      // 监听器,page.on 会累积:后注册的对同一 dialog 重复 accept 报错(CodeRabbit)。
      page.once('dialog', async (d) => d.accept());
      await sidebarItems().first().click({ button: 'right' });
      await page.locator('div.rounded-lg.shadow-lg button', { hasText: '删除对话' }).click();

      // Empty welcome: sidebar emptied and the composer is present.
      await expect.poll(async () => sidebarItems().count(), { timeout: 30_000 }).toBe(0);
      await expect(page.locator('[data-testid="chat-input-container"]')).toBeVisible();

      // Focus must be on the composer textarea on the empty welcome.
      await expect
        .poll(
          async () =>
            page.evaluate(() => {
              const ae = document.activeElement as HTMLElement | null;
              return {
                hasFocus: document.hasFocus(),
                inComposer: !!ae?.closest('[data-testid="chat-input-container"]'),
                tag: ae?.tagName ?? null,
              };
            }),
          { timeout: 10_000 }
        )
        .toMatchObject({ hasFocus: true, inComposer: true, tag: 'TEXTAREA' });

      // Typing must land with NO click: send text and confirm the input holds it.
      await page.keyboard.type('回归验证：删除后可直接输入');
      await expect(composerTextarea()).toHaveValue('回归验证：删除后可直接输入');
      console.log('[test] ✅ delete-all → welcome: input focused and typable without a click');

      // Per e2e-test-workflow skill: every run must end with a visual proof.
      await page.screenshot({
        path: `test-results/delete-all-focus-${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });
    }
  );

  test(
    'seed a replied conversation, open a new empty one, delete the replied one via sidebar — welcome stays typable',
    { timeout: 300_000 },
    async () => {
      // Repro from the field: current session is a fresh EMPTY conversation, the
      // deleted one (which HAS a reply) is NOT current. Deleting it goes through
      // window.confirm but triggers no session switch, so the entry-focus effect
      // never re-runs → only the delete→focus-regrant path can restore typing.
      // Under Playwright the OS activation is never actually lost, so this guards
      // the DOM/flow half (the OS half stays manual-only, see header note).
      const seedText = '请回复一句话';
      const userBubbles = page.getByTestId('chat-message-user');
      for (let attempt = 0; attempt < 3; attempt++) {
        const connecting = page.locator('main').getByText('正在连接…');
        await connecting.waitFor({ state: 'hidden', timeout: 120_000 }).catch(() => {});
        const textarea = await waitForInputReady(page, 60_000);
        const before = await userBubbles.count();
        await textarea.fill(seedText);
        await textarea.press('Enter');
        try {
          await expect(userBubbles).toHaveCount(before + 1, { timeout: 60_000 });
          break;
        } catch {
          await page.waitForTimeout(2000);
        }
      }
      await expect(userBubbles).toHaveCount(1, { timeout: 10_000 });
      await waitForResponseComplete(page, 60_000);
      await expect
        .poll(async () => sidebarItems().count(), { timeout: 30_000 })
        .toBeGreaterThanOrEqual(1);

      // Open a NEW empty conversation (current). Empty sessions are ephemeral, so
      // the sidebar still lists only the replied conversation A.
      await createNewConversation(page);
      // The inline workspace selector renders only once historyLoaded && the
      // active session is empty — i.e. the empty welcome is actually ready.
      // Deleting before that would fire the focus-regrant into a session whose
      // history is still loading (CodeRabbit).
      await expect(page.getByTestId('inline-workspace-selector')).toBeVisible({
        timeout: 30_000,
      });
      await expect
        .poll(async () => sidebarItems().count(), { timeout: 30_000 })
        .toBeGreaterThanOrEqual(1);

      // Delete A (not the current empty session) via the sidebar context menu.
      // Auto-accept the window.confirm. once()——shared Page 上两个 test 各注册
      // 监听器,page.on 会累积:后注册的对同一 dialog 重复 accept 报错(CodeRabbit)。
      page.once('dialog', async (d) => d.accept());
      await sidebarItems().first().click({ button: 'right' });
      await page.locator('div.rounded-lg.shadow-lg button', { hasText: '删除对话' }).click();

      // Sidebar emptied; the empty welcome composer is present.
      await expect.poll(async () => sidebarItems().count(), { timeout: 30_000 }).toBe(0);
      await expect(page.locator('[data-testid="chat-input-container"]')).toBeVisible();

      // Composer must be focused and typable with NO click.
      await expect
        .poll(
          async () =>
            page.evaluate(() => {
              const ae = document.activeElement as HTMLElement | null;
              return {
                hasFocus: document.hasFocus(),
                inComposer: !!ae?.closest('[data-testid="chat-input-container"]'),
                tag: ae?.tagName ?? null,
              };
            }),
          { timeout: 10_000 }
        )
        .toMatchObject({ hasFocus: true, inComposer: true, tag: 'TEXTAREA' });

      await page.keyboard.type('回归验证：删除非当前对话后可直接输入');
      await expect(composerTextarea()).toHaveValue('回归验证：删除非当前对话后可直接输入');
      console.log(
        '[test] ✅ delete non-current → welcome: input focused and typable without a click'
      );

      await page.screenshot({
        path: `test-results/delete-noncurrent-focus-${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });
    }
  );
});
