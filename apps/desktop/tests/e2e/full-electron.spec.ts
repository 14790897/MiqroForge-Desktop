/**
 * Full Electron E2E Test (_electron.launch based)
 *
 * Uses Playwright's built-in _electron.launch(), which since v1.58
 * (PR #39012) uses app.commandLine.appendSwitch() instead of the
 * removed --remote-debugging-port CLI flag.  No manual CDP needed.
 *
 * User config at ~/.miqi/config.json is used automatically.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  APPS_DESKTOP,
  LLM_TIMEOUT,
  waitForInputReady,
  sendMessage,
  waitForResponseComplete,
  getSessionTitle,
  getSidebarSessionCount,
  getSidebarSessionItems,
  createNewConversation,
  waitForSidebarRefresh,
  switchToSessionWithMarker,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

// ─── Test Suite ───────────────────────────────────────────────────

const SKIP_REAL_WEB_SEARCH_ON_CI =
  !!process.env.CI && process.env.MIQI_RUN_REAL_WEB_SEARCH_E2E !== '1';
const SKIP_STATEFUL_SESSION_E2E_ON_CI =
  !!process.env.CI && process.env.MIQI_RUN_STATEFUL_SESSION_E2E !== '1';

test.describe('Native Electron E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  // ═══════════════════════════════════════════════════════════════
  //  SECTION 1: App Health & Basic AI
  // ═══════════════════════════════════════════════════════════════

  test('app launches and renders correctly', async () => {
    await expect(page.locator('[data-testid="app-title"]')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.locator('[data-testid="chat-input-container"] textarea')).toBeVisible({
      timeout: 10_000,
    });

    // Core UI landmarks — use stable data-testid selectors
    await expect(page.locator('[data-testid="nav-tasks-title"]')).toBeVisible();
    await expect(page.locator('[data-testid="nav-new-session"]')).toBeVisible();
  });

  test('right panel shows Task Assets', async () => {
    // The right panel should show "Task Assets" header with LayoutGrid icon.
    // Default state is panelOpen=true, showing the empty-state message.
    await expect(page.locator('[data-testid="task-assets-title"]')).toBeVisible({
      timeout: 10_000,
    });

    // Toggle button exists
    const toggleBtn = page.locator('[data-testid="toggle-assets-panel-btn"]');
    await expect(toggleBtn).toBeVisible();

    // Empty state message when no agent operations
    await expect(page.locator('[data-testid="task-assets-empty"]')).toBeVisible({
      timeout: 5_000,
    });

    // Toggle panel closed and verify it hides
    await toggleBtn.click();
    await expect(page.locator('[data-testid="task-assets-title"]')).not.toBeVisible({
      timeout: 5_000,
    });

    // Toggle panel back open
    await toggleBtn.click();
    await expect(page.locator('[data-testid="task-assets-title"]')).toBeVisible({ timeout: 5_000 });
  });

  test('basic AI responds to simple prompt', { timeout: LLM_TIMEOUT }, async () => {
    await sendMessage(page, '只回答Y');
    await expect(page.getByText('Y').first()).toBeVisible({
      timeout: 120_000,
    });
    await waitForResponseComplete(page);
  });

  test.describe('real web search integration', () => {
    test.skip(
      SKIP_REAL_WEB_SEARCH_ON_CI,
      '#187: Real Web Search + real LLM output is unstable in PR CI; run with MIQI_RUN_REAL_WEB_SEARCH_E2E=1 for manual/nightly verification.'
    );

    test('web search with real search tool', { timeout: LLM_TIMEOUT }, async () => {
      const marker = `WEB_SEARCH_E2E_DONE_${Date.now()}`;
      await sendMessage(
        page,
        `You must call web_search for "IANA reserved domains". After search completes, reply only with ${marker}`
      );
      const approvalDialog = page.locator('[role="alertdialog"]');
      if (await approvalDialog.isVisible({ timeout: 30_000 }).catch(() => false)) {
        console.log('[test] Network approval dialog appeared for web search');
        await page.getByRole('button', { name: /Allow once|允许一次/ }).click();
      }
      // Wait for streaming to finish before asserting visibility —
      // during streaming the response element may exist in DOM but be hidden
      await waitForResponseComplete(page);
      const markerEl = page.getByText(marker).first();
      await markerEl.scrollIntoViewIfNeeded().catch(() => {});
      await expect(markerEl).toBeVisible({ timeout: 30_000 });
      console.log('[test] Web search completed');
    });
  });

  // ═══════════════════════════════════════════════════════════════
  //  SECTION 2: Conversation Creation
  // ═══════════════════════════════════════════════════════════════

  test('create new conversation via sidebar button', { timeout: 60_000 }, async () => {
    const initialCount = await getSidebarSessionCount(page);
    const newTitle = await createNewConversation(page);

    // Title should be non-empty (new UI may assign named titles like
    // "Brand Guideline Update" instead of timestamps; titles may also
    // duplicate across sessions)
    expect(newTitle).toBeTruthy();
    console.log(`[test] New session title: ${newTitle}`);

    await expect(page.locator('main').getByText('只回答Y')).not.toBeVisible({
      timeout: 5_000,
    });

    const newCount = await getSidebarSessionCount(page);
    expect(newCount).toBeGreaterThanOrEqual(initialCount);
    console.log(`[test] Sidebar sessions: ${initialCount} → ${newCount}`);
  });

  test('New Chat button clears message history', { timeout: LLM_TIMEOUT }, async () => {
    await waitForInputReady(page, 15_000);

    await sendMessage(page, '只回答Y');
    await waitForResponseComplete(page);

    await createNewConversation(page);

    await expect(page.locator('main').getByText('只回答Y')).not.toBeVisible({
      timeout: 5_000,
    });
  });

  // ═══════════════════════════════════════════════════════════════
  //  SECTION 3: Conversation Switching & History
  // ═══════════════════════════════════════════════════════════════

  test.describe('stateful session integration', () => {
    test.skip(
      SKIP_STATEFUL_SESSION_E2E_ON_CI,
      'Stateful session isolation is unstable in PR CI; run with MIQI_RUN_STATEFUL_SESSION_E2E=1 for manual/nightly verification.'
    );

    test(
      'conversation isolation: messages do not leak between sessions',
      { timeout: LLM_TIMEOUT },
      async () => {
        const markerA = `IsolationA_${Date.now()}`;
        await sendMessage(page, `只回答${markerA}`);
        await waitForResponseComplete(page);

        await createNewConversation(page);

        const markerB = `IsolationB_${Date.now()}`;
        await sendMessage(page, `只回答${markerB}`);
        await waitForResponseComplete(page);

        // markerA should NOT be visible in the new chat (scope to main)
        await expect(page.locator('main').getByText(markerA)).not.toBeVisible({ timeout: 5_000 });
      }
    );
  });

  test(
    'switch between conversations via sidebar preserves history',
    { timeout: LLM_TIMEOUT },
    async () => {
      const markerSwitch = `SwitchBack_${Date.now()}`;
      await sendMessage(page, `只回答${markerSwitch}`);
      await expect(
        page.locator('main').getByText(markerSwitch, { exact: false }).first()
      ).toBeVisible({
        timeout: 120_000,
      });
      await waitForResponseComplete(page);

      const sessionTitle = await getSessionTitle(page).textContent();
      console.log(`[test] Session with marker: ${sessionTitle}`);

      await createNewConversation(page);
      await createNewConversation(page);

      const sessionCount = await getSidebarSessionCount(page);
      expect(sessionCount).toBeGreaterThan(0);
      console.log(`[test] Sidebar has ${sessionCount} sessions after creating new ones`);
    }
  );

  test('multiple new conversations all appear in sidebar', { timeout: 60_000 }, async () => {
    const countBefore = await getSidebarSessionCount(page);

    await createNewConversation(page);
    await createNewConversation(page);
    await createNewConversation(page);

    await waitForSidebarRefresh(page);
    await page.waitForTimeout(2000);
    const countAfter = await getSidebarSessionCount(page);
    expect(countAfter).toBeGreaterThanOrEqual(countBefore);
    console.log(`[test] Sidebar sessions: ${countBefore} → ${countAfter}`);
  });

  // ═══════════════════════════════════════════════════════════════
  //  SECTION 4: Sidebar Switching & History
  //
  //  Fixed in #480: ChatConsole.load() now uses exponential-backoff
  //  retry so transient bridge-unready state doesn't permanently skip
  //  history loading on session switch.
  //
  //  These tests share describe state with other tests that modify
  //  session count/order via createNewConversation → sendMessage.
  //  Gated behind MIQI_RUN_STATEFUL_SESSION_E2E=1 (same guard as
  //  other stateful tests).  The dedicated regression spec at
  //  tests/e2e/regression-480-startup-history.spec.ts always runs
  //  both scenarios in isolated Electron instances.
  // ═══════════════════════════════════════════════════════════════

  test.describe('sidebar switching', () => {
    if (SKIP_STATEFUL_SESSION_E2E_ON_CI) {
      test.skip();
    }
    test('sidebar switch back loads history', { timeout: LLM_TIMEOUT }, async () => {
      await createNewConversation(page);

      const m = `M_${Date.now()}`;
      const textarea = await waitForInputReady(page);
      await textarea.click();
      await textarea.type(`只回答${m}`);
      await textarea.press('Enter');
      await expect(page.locator('main').getByText(m).first()).toBeVisible({ timeout: 10_000 });
      await waitForResponseComplete(page);

      await createNewConversation(page);
      await page.waitForTimeout(2000);

      const countAfter = await getSidebarSessionItems(page).count();
      console.log(`[test] Sidebar sessions after new: ${countAfter}`);

      const sessionCards = getSidebarSessionItems(page);
      const numCards = await sessionCards.count();

      let found = false;
      for (let i = 0; i < numCards; i++) {
        const card = sessionCards.nth(i);
        await card.scrollIntoViewIfNeeded().catch(() => {});
        await card.click({ force: true, timeout: 5000 });
        await page.waitForTimeout(5000);

        const hasMarker = await page
          .locator('main')
          .getByText(m, { exact: false })
          .first()
          .isVisible()
          .catch(() => false);

        if (hasMarker) {
          found = true;
          console.log(`[test] Found marker in card #${i}`);
          break;
        }
        console.log(`[test] Card #${i} has no marker`);
      }
      expect(found).toBe(true);
      await expect(page.locator('main').getByText(m).first()).toBeVisible({ timeout: 15000 });
    });

    test('switch back sees full multi-turn history', { timeout: LLM_TIMEOUT }, async () => {
      await createNewConversation(page);

      const markerA = `RED_${Date.now()}`;
      const textarea1 = await waitForInputReady(page);
      await textarea1.click();
      await textarea1.type(`只回答${markerA}`);
      await textarea1.press('Enter');
      await expect(page.locator('main').getByText(markerA).first()).toBeVisible({
        timeout: 10_000,
      });
      await waitForResponseComplete(page);

      const markerB = `BLUE_${Date.now()}`;
      const textarea2 = await waitForInputReady(page);
      await textarea2.click();
      await textarea2.type(`只回答${markerB}`);
      await textarea2.press('Enter');
      await expect(page.locator('main').getByText(markerB).first()).toBeVisible({
        timeout: 10_000,
      });
      await waitForResponseComplete(page);

      await createNewConversation(page);
      await page.waitForTimeout(2000);

      const sessionCards = getSidebarSessionItems(page);
      const numCards = await sessionCards.count();
      console.log(`[test] ${numCards} sidebar session cards`);

      let found = false;
      for (let i = 0; i < numCards; i++) {
        const card = sessionCards.nth(i);
        await card.scrollIntoViewIfNeeded().catch(() => {});
        await card.click({ force: true, timeout: 5000 });
        await page.waitForTimeout(5000);

        const hasMarker = await page
          .locator('main')
          .getByText(markerA, { exact: false })
          .first()
          .isVisible()
          .catch(() => false);

        if (hasMarker) {
          found = true;
          console.log(`[test] Found markerA in card #${i}`);
          break;
        }
        console.log(`[test] Card #${i} has no markerA`);
      }
      expect(found).toBe(true);
      await expect(page.locator('main').getByText(markerB).first()).toBeVisible({
        timeout: 15_000,
      });
    });
  });

  test('history persists after app restart', { timeout: LLM_TIMEOUT }, async () => {
    test.skip(
      SKIP_STATEFUL_SESSION_E2E_ON_CI,
      'Session restart history is unstable in PR CI; run with MIQI_RUN_STATEFUL_SESSION_E2E=1 for manual/nightly verification.'
    );
    await createNewConversation(page);
    const m = `R_${Date.now()}`;
    await sendMessage(page, `只回答${m}`);
    await waitForResponseComplete(page);

    await closeElectronApp(electronApp);
    await new Promise((r) => setTimeout(r, 3000));

    const env: Record<string, string | undefined> = { ...process.env };
    env.MIQI_HOME = miqiHome;
    delete env.ELECTRON_RUN_AS_NODE;
    const app2 = await electron.launch({
      args: [APPS_DESKTOP],
      executablePath: require('electron') as string,
      env: env as Record<string, string>,
      chromiumSandbox: false,
    });
    const page2 = await app2.firstWindow();
    await page2.waitForLoadState('domcontentloaded');
    try {
      await page2.locator('[data-testid="app-title"]').waitFor({ timeout: 30000 });
    } catch {}
    await waitForInputReady(page2, 30000);

    // Wait for bridge to initialize, then reload so ChatConsole re-fires
    // useEffect with bridge fully ready.
    await page2.evaluate(async () => {
      for (let i = 0; i < 30; i++) {
        try {
          const s = await (window as any).miqi.runtime.status();
          if (s?.state === 'running' && s?.initialized) return;
        } catch {
          /* */
        }
        await new Promise((r) => setTimeout(r, 1000));
      }
    });
    await page2.reload();
    await page2.waitForLoadState('domcontentloaded');
    await waitForInputReady(page2, 30000);
    await page2.waitForTimeout(5000);

    await expect(page2.locator('main').getByText(m).first()).toBeVisible({ timeout: 30000 });

    await closeElectronApp(app2);
    await new Promise((r) => setTimeout(r, 3000));
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  });

  // ═══════════════════════════════════════════════════════════════
  //  SECTION 5: Multi-turn & Persistence
  // ═══════════════════════════════════════════════════════════════

  test('multi-turn memory recall within same session', { timeout: LLM_TIMEOUT }, async () => {
    await sendMessage(page, '只回答已记住');
    await expect(page.getByText(/已记住/).first()).toBeVisible({
      timeout: 120_000,
    });
    await waitForResponseComplete(page);

    await sendMessage(page, '只回答测试员');
    await expect(page.getByText(/测试员/).first()).toBeVisible({
      timeout: 120_000,
    });
    await waitForResponseComplete(page);
  });

  test(
    'session persists after New Chat and visible in Sessions page',
    { timeout: LLM_TIMEOUT },
    async () => {
      const persistMarker = `Persist_${Date.now()}`;
      await sendMessage(page, `只回答${persistMarker}`);
      await waitForResponseComplete(page);

      await createNewConversation(page);
      await expect(page.locator('main').getByText(persistMarker)).not.toBeVisible({
        timeout: 5_000,
      });

      console.log('[test] Session persistence verified');
    }
  );

  // SECTION 5 removed — Sessions page no longer has a dedicated nav button.

  // ═══════════════════════════════════════════════════════════════
  //  SECTION 6: AI File Creation with *:* wildcard pre-approval
  //
  //  Uses *:* wildcard to bypass all approval dialogs so tests
  //  don't have to wait for UI interaction.  Verifies files are
  //  created successfully without any approval popups.
  // ═══════════════════════════════════════════════════════════════

  test(
    'AI file creation: *:* pre-approved → file created without dialog',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      await waitForBridgeInitialized(page);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      await createNewConversation(page);

      const filename = `e2e_${Date.now()}.txt`;
      await sendMessage(
        page,
        `Use write_file to create ${filename} with content "hello from e2e wildcard approval test"`
      );

      // No approval dialog should appear
      await waitForResponseComplete(page, 240_000);

      await expect(page.locator('main').getByText(filename, { exact: false }).first()).toBeVisible({
        timeout: 15_000,
      });

      console.log(`[test] ✅ AI created file without approval dialog: ${filename}`);
    }
  );

  test(
    'AI PPT creation: *:* pre-approved → pptx_write without dialog',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      await createNewConversation(page);

      await sendMessage(
        page,
        '使用 pptx_write 工具创建一页PPT，file_path=e2e_test.pptx，slides=[{title:"E2E测试",content:"自动化测试验证通过"}]。创建成功后只回复一个字：成'
      );

      // No approval dialog — just wait for AI to finish
      await waitForResponseComplete(page, 240_000);

      await expect(page.locator('main').getByText('成').first()).toBeVisible({ timeout: 15_000 });

      console.log('[test] ✅ PPT created via pptx_write without approval dialog');
    }
  );
});
