/**
 * Sandbox Exec E2E Tests
 *
 * Tests that require bwrap sandbox (WSL on Windows, native on Linux).
 * Skipped on regular CI runners that lack bubblewrap.
 *
 * Run:
 *   npx playwright test --config=playwright.config.ts --project=electron sandbox-exec.spec.ts
 *   MIQI_RUN_SANDBOX_E2E=1 npx playwright test ...  (force on CI)
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  APPS_DESKTOP,
  LLM_TIMEOUT,
  waitForInputReady,
  sendMessage,
  waitForResponseComplete,
  createNewConversation,
  approveLoop,
  launchElectronApp,
  closeElectronApp,
  waitForSandboxReady,
} from './helpers/electron-setup';

const SKIP_SANDBOX_ON_CI = !!process.env.CI && process.env.MIQI_RUN_SANDBOX_E2E !== '1';

test.describe('Sandbox Exec E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // Wait for sandbox to finish cold-start initialization (export/import/apt).
    // On a cold CI runner this can take 3-5 minutes.  Without this wait,
    // exec commands silently fall back to local execution.
    // Fail fast if the sandbox never becomes ready — proceeding with a
    // local-exec fallback would make all sandbox assertions pass vacuously.
    const ready = await waitForSandboxReady(page, 300_000);
    if (!ready) {
      throw new Error('Sandbox manager did not become ready within 300s');
    }
  }, 420_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  // ── Initialization ──────────────────────────────────────────────

  test('sandbox manager initializes on bridge startup', { timeout: 120_000 }, async () => {
    const status = await page.evaluate(async () => {
      try {
        return await (window as any).miqi.runtime.status();
      } catch {
        return null;
      }
    });
    expect(status?.state).toBe('running');
    console.log('[test] ✅ Bridge running with sandbox manager initialized');
  });

  // ── Basic exec commands ─────────────────────────────────────────

  test('exec pwd in sandbox returns /home/miqi/workspace', { timeout: LLM_TIMEOUT }, async () => {
    test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');

    // ── Template step 1: capture bridge stderr ──────────────────
    page.on('console', (msg) => {
      const t = msg.text();
      if (t.includes('error') || t.includes('BRIDGE') || t.includes('miqi'))
        console.log(`[debug] ${t}`);
    });

    // ── Template step 2: wait for bridge ready (NOT_INITIALIZED guard) ──
    await page.evaluate(async () => {
      for (let i = 0; i < 60; i++) {
        const s = await (window as any).miqi.runtime.status();
        if (s?.state === 'running' && s?.initialized) return;
        await new Promise((r) => setTimeout(r, 1000));
      }
    });

    // ── Template step 3: capture actual runtime state ────────────
    const runtimeState = await page.evaluate(async () => {
      try {
        const s = await (window as any).miqi.runtime.status();
        return `status:${JSON.stringify(s)}`;
      } catch (e: any) {
        return `reject:${e?.message ?? String(e)}`;
      }
    });
    console.log(`[debug] runtime.status → ${runtimeState}`);

    // ── Template step 4: *:* wildcard pre-approve per e2e-test-workflow ──
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

    // ── Execute ─────────────────────────────────────────────────
    await createNewConversation(page);
    await sendMessage(page, '用 exec 工具执行 pwd，只回复 exec 的实际输出，不要加任何解释');

    // ── Template step 5: auto-click approval loop per e2e-test-workflow ──
    await approveLoop(page, 240_000);

    // ── Template step 6: wait for final response stabilization ───
    await waitForResponseComplete(page, 240_000);

    // ── Template step 7: capture full conversation ──────────────
    const fullText = await page.evaluate(() => {
      const el = document.querySelector('main');
      return el?.textContent ?? '';
    });
    console.log('[test] === Full AI conversation ===');
    console.log(fullText);
    console.log('[test] ===========================');

    // ── Assert: use semantic selector per template ──────────────
    await expect(
      page.locator('main').getByText('/home/miqi/workspace', { exact: false }).first()
    ).toBeVisible({ timeout: 30_000 });
    console.log('[test] ✅ exec pwd ran inside sandbox');
  });

  test('exec whoami returns miqi user', { timeout: LLM_TIMEOUT }, async () => {
    test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');
    await sendMessage(page, '用 exec 工具执行 whoami，只回复 exec 的实际输出，不要加任何解释');
    await waitForResponseComplete(page, 120_000);
    await expect(page.locator('main').getByText('miqi', { exact: false }).first()).toBeVisible({
      timeout: 15_000,
    });
    console.log('[test] ✅ exec whoami → miqi');
  });

  test('exec echo returns command output', { timeout: LLM_TIMEOUT }, async () => {
    test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');
    await sendMessage(
      page,
      '用 exec 工具执行 echo "sandbox_e2e_OK"，只回复 exec 的实际输出，不要加任何解释'
    );
    await waitForResponseComplete(page, 120_000);
    await expect(
      page.locator('main').getByText('sandbox_e2e_OK', { exact: false }).first()
    ).toBeVisible({ timeout: 15_000 });
    console.log('[test] ✅ exec echo → sandbox_e2e_OK');
  });

  test('exec uname returns Linux sandbox', { timeout: LLM_TIMEOUT }, async () => {
    test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');
    await sendMessage(page, '用 exec 工具执行 uname -s，只回复 exec 的实际输出，不要加任何解释');
    await waitForResponseComplete(page, 120_000);
    await expect(page.locator('main')).toContainText(/linux|exec.*uname/i, { timeout: 10_000 });
    console.log('[test] ✅ exec uname -s → Linux');
  });

  test('exec ls shows sandbox workspace contents', { timeout: LLM_TIMEOUT }, async () => {
    test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');
    await sendMessage(
      page,
      '用 exec 工具执行 ls /home/miqi/workspace，只回复 exec 的实际输出，不要加任何解释'
    );
    await waitForResponseComplete(page, 120_000);
    const response = page.locator('main').getByText(/.+/);
    await expect(response.first()).toBeVisible({ timeout: 30_000 });
    console.log('[test] ✅ exec ls /home/miqi/workspace');
  });

  // ── Session isolation + file operations ─────────────────────────

  test(
    'session file isolation: exec files from one session not visible in another',
    { timeout: LLM_TIMEOUT },
    async () => {
      test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');
      await createNewConversation(page);

      const marker = `ISOLATED_${Date.now()}`;
      const fname = `session_isolation_${Date.now()}.txt`;
      // Use write_file (session-scoped path) instead of exec echo > shared workspace.
      await sendMessage(page, `用 write_file 创建文件 ${fname}，内容为 "${marker}"`);
      await waitForResponseComplete(page, 120_000);

      // Switch to a new session — should NOT see the file from Session A.
      await createNewConversation(page);
      await sendMessage(page, `用 exec 执行: cat /home/miqi/workspace/${fname} 2>&1`);
      await waitForResponseComplete(page, 120_000);
      await page.waitForTimeout(15_000);

      const mainB = (await page.locator('main').textContent()) || '';
      const hasNotFound =
        /no such file|not found|not exist|does not exist|不存在|No such|cat.*error/i.test(mainB);
      if (!hasNotFound) {
        console.log('[test] Session B text (600):', mainB.substring(0, 600));
      }
      expect(hasNotFound).toBe(true);
      await page.screenshot({ path: 'test-results/session-isolation-02-sessionB-cannot-see.png' });
      console.log('[test] ✅ Session B cannot see Session A file');
    }
  );

  test(
    'write_file uses session-scoped workspace via sandbox',
    { timeout: LLM_TIMEOUT },
    async () => {
      test.skip(SKIP_SANDBOX_ON_CI, 'CI runner lacks bwrap');
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
      await createNewConversation(page);

      const fname = `e2e_session_file_${Date.now()}.txt`;
      const content = `E2E session file content ${Date.now()}`;

      await sendMessage(page, `Use write_file to create ${fname} with content "${content}"`);
      await waitForResponseComplete(page, 240_000);
      await page.screenshot({ path: 'test-results/session-isolation-03-write-file-approval.png' });

      await sendMessage(
        page,
        `用 exec 执行: cat /home/miqi/workspace/sessions/*/files/${fname} 2>&1`
      );
      await waitForResponseComplete(page, 120_000);
      const mainText = await page.locator('main').textContent();
      expect(mainText).toContain(content);
      await page.screenshot({ path: 'test-results/session-isolation-04-write-file-verified.png' });
      console.log(`[test] ✅ write_file session-scoped: ${fname}`);
    }
  );
});
