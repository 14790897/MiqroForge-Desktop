/**
 * E2E: AI calls the spawn tool via chat (Issue #246 core)
 *
 * This is the ONLY true end-to-end test for subagent spawn —
 * it drives the full user → LLM → tool-call → subagent → result render
 * pipeline.  The user types a prompt, the LLM decides to invoke the spawn
 * tool, the subagent runs, and the result card renders in chat.
 *
 * Bridge API integration tests (direct spawn/list/kill calls) live in
 * subagent-bridge-api.spec.ts.
 *
 * Issue #246 — Subagent subsystem needs end-to-end verification.
 * PR   #475 — This test.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron subagent-spawn.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  launchElectronApp,
  closeElectronApp,
  waitForInputReady,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

// ── Helpers ─────────────────────────────────────────────────────────

/** Ensure a session exists by sending a simple chat message and
 *  waiting for the response.  Agents require an initialized runtime session. */
async function ensureSession(page: Page): Promise<void> {
  const textarea = await waitForInputReady(page);
  await textarea.fill('回复 "ok"');
  await textarea.press('Enter');
  await expect(page.getByText('回复 "ok"').first()).toBeVisible({ timeout: 10_000 });

  // Wait for the thinking indicator to appear and then disappear.
  try {
    await expect(page.locator('[data-testid="thinking-indicator"]')).toBeVisible({
      timeout: 15_000,
    });
  } catch {
    /* may appear faster than we can catch */
  }
  try {
    await expect(page.locator('[data-testid="thinking-indicator"]')).toBeHidden({
      timeout: 60_000,
    });
  } catch {
    /* already hidden */
  }

  // Give the runtime a moment to fully settle after the turn completes.
  await page.waitForTimeout(2000);
}

// ── Tests ────────────────────────────────────────────────────────────

test.describe('Subagent Spawn E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page);

    // Verify the agents bridge API is present.
    const hasAgents = await page.evaluate(
      () => typeof (window as any).miqi?.agents?.spawn === 'function'
    );
    if (!hasAgents) {
      console.log('[test] agents API not available — skipping suite');
      test.skip();
    }
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
  });

  test.beforeEach(async () => {
    // Fresh conversation so previous subagent results don't leak.
    await page.keyboard.press('Control+N').catch(() => {});
    await page.waitForTimeout(800);
  });

  // ── Test: AI uses the spawn tool via chat (issue #246 core) ────
  //
  // Maintainer feedback on issue #246: "有这个工具，但是 ai 无法使用" —
  // the spawn tool is registered but the AI cannot actually call it.  This
  // test drives the REAL LLM tool-call path: the main agent must decide to
  // invoke the spawn tool, and the subagent result must render in chat.
  // If the tool-call path is broken, this test fails — which is exactly
  // what the issue is asking to verify.

  test('AI can call the spawn tool and the result renders', async () => {
    // 1. Prime the session.
    await ensureSession(page);

    // 2. Ask the AI to use the spawn tool — explicitly, so a refusal or
    //    silent fallback (running the command itself) is a real failure.
    const textarea = await waitForInputReady(page);
    const prompt =
      '请使用 spawn 工具创建一个 subagent 来执行命令 "echo hello-ai-spawn" 并报告输出。' +
      '这是对 spawn 工具的验证测试：你必须调用 spawn 工具，不要自己直接执行该命令。';
    await textarea.fill(prompt);
    await textarea.press('Enter');
    await expect(page.getByText(/请使用 spawn 工具/).first()).toBeVisible({ timeout: 10_000 });

    // 3. Wait for the subagent result card (rendered from chat:subagent_result).
    //    NOTE: the chat area accumulates messages across tests (Control+N does
    //    not clear it), so we cannot just check `includes('Subagent')` — the
    //    old cards from tests 1-4 would match.  Instead: wait until a NEW
    //    subagent card appears (card count increases) AND the newest ✅ card
    //    actually contains the task text (proof the subagent ran it, not just
    //    the main agent echoing the prompt).
    const countCards = (t: string) => (t.match(/(?:✅|❌) Subagent/g) || []).length;
    const initialCards = countCards(
      (await page
        .locator('main')
        .textContent()
        .catch(() => '')) || ''
    );
    const deadline = Date.now() + 180_000;
    let rendered = false;
    let lastText = '';
    while (Date.now() < deadline) {
      const mainText =
        (await page
          .locator('main')
          .textContent()
          .catch(() => '')) || '';
      lastText = mainText;
      if (countCards(mainText) > initialCards) {
        // New card appeared — extract the newest ✅ card and check its body.
        const lastIdx = mainText.lastIndexOf('✅ Subagent');
        const newestCard = lastIdx >= 0 ? mainText.slice(lastIdx) : '';
        if (newestCard.includes('hello-ai-spawn')) {
          rendered = true;
          break;
        }
      }
      await page.waitForTimeout(2000);
    }
    console.log('[test] ai-spawn: subagent result rendered:', rendered);
    // Print the rendered chat area so we can verify WHO ran the command:
    // the subagent card (role=subagent, from chat:subagent_result) vs any
    // main-agent tool calls in the same conversation.
    const mainTextFinal =
      (await page
        .locator('main')
        .textContent()
        .catch(() => '')) || '';
    console.log('[test] ai-spawn: main text tail:', mainTextFinal.slice(-800));
    expect(rendered).toBe(true);

    // 4. Let the MAIN agent's own turn finish before the test ends.  The
    //    subagent card renders while the main agent is still streaming its
    //    reply; leaving that request in flight made afterAll's
    //    closeElectronApp hang on the bridge child process (CI afterAll
    //    600s timeout + 300s worker force-kill).  Wait for the thinking
    //    indicator to disappear (tolerant — the UI may not show one).
    await page
      .getByText('Thinking…')
      .first()
      .waitFor({ state: 'hidden', timeout: 90_000 })
      .catch(() => {});
  });
});
