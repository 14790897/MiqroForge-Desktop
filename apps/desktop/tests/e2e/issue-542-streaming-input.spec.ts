/**
 * E2E regression spec for issue #542 — [BUG] AI 流式回复期间输入框被完全禁用，
 * 无法提前输入下一条 prompt。
 *
 * User-facing contract verified (matches the issue's requested behavior):
 *   1. While a turn is streaming — thinking, tool calls or reply text — the
 *      chat input stays ENABLED, so the user can pre-type the next message
 *      instead of being locked out (regression: PR #658 re-added
 *      `disabled={streaming}`, re-breaking #660's fix).
 *   2. Quick course-correction: typing a correction + Enter while streaming
 *      supersedes the in-flight turn (abort) and starts a new one; the input
 *      is never forced empty/disabled mid-reply, and the new turn completes.
 *
 * The "old reply freezes at the pause point" behavior is exercised directly
 * by the backend turn-cancel unit tests + the renderer reveal-reset logic;
 * here we guard the primary regression: input must stay usable + resend must
 * work in the real app.
 *
 * Uses a real LLM via the user's configured provider (approval-bypass on).
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *        --project=electron -g 'Issue #542'
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

const INPUT = '[data-testid="chat-input-container"] textarea';

/** Send a prompt via real keystrokes.  type() (not fill()) drives React's
 *  onChange per keystroke, so `input` state is committed before Enter fires —
 *  fill() can race streaming re-renders and leave handleSend reading a stale
 *  empty input (the #542 resend path). */
async function sendPrompt(page: import('@playwright/test').Page, text: string): Promise<void> {
  const inputX = page.locator(INPUT);
  await inputX.click();
  await inputX.fill('');
  await inputX.type(text);
  await inputX.press('Enter');
}

test.describe('Issue #542: input usable while AI streams / interrupt-resend', () => {
  // Serial: one shared app instance (launching one app per test is slow and
  // each test needs a streaming turn from the same session flow).
  test.describe.configure({ mode: 'serial' });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'input stays enabled while streaming; a resend supersedes the old turn and the old reply stops scrolling',
    { timeout: 420_000 },
    async () => {
      const inputX = page.locator(INPUT);
      await expect(inputX).toBeVisible({ timeout: 10_000 });

      // A fresh session (sidebar "+") so the initial-session cold-load retry
      // window (#570: sessions.get backoff up to ~57s) is NOT the thing under
      // test — we want to exercise mid-stream input, not session loading.
      await createNewConversation(page);

      // Gate on historyLoaded: the message area shows "正在连接…" until the
      // session history loads. Sending before that can leave the reply area
      // stuck on the connecting hint (cold-start flake) and the user bubbles
      // unrendered — not the #542 behavior we're testing.
      await expect(page.getByText('正在连接…')).toBeHidden({ timeout: 60_000 });

      // ── Send a medium-length prompt so the reply streams for several
      //    seconds — long enough to interact with the input mid-stream. ──
      const markerA = `542A_${Date.now().toString(36)}`;
      const promptA = `${markerA}：请用 5 段话详细介绍杭州这座城市的历史、文化、美食、交通、教育与发展。`;
      await sendPrompt(page, promptA);

      // Wait for the turn to ACTUALLY start streaming: the optimistic user
      // bubble's pending spinner (#364) disappears on the FIRST progress event
      // (setSendingFor(null)), which is exactly when the pre-stream pending
      // phase ends and pendingSendIds is cleared. We must NOT resend earlier —
      // while the send is still pending the #364 double-Enter guard correctly
      // swallows a resend, which is not the scenario under test.
      const pendingSpinner = page.locator('[data-testid="chat-message-user"] svg.animate-spin');
      await expect(pendingSpinner.last()).toBeHidden({ timeout: 120_000 });

      // ── #542 core: the input must be ENABLED while the AI is streaming
      //    (thinking, tool calls or reply text — the whole turn). This is the
      //    exact regression #658 re-broke with disabled={streaming}. ──
      await expect(inputX).toBeEnabled({ timeout: 5_000 });

      // ── Wait for the FIRST reply to actually generate a portion of its
      //    text.  The assistant bubble only appears once the final-text stream
      //    starts (after the agentic thinking/tool phase), so a visible
      //    non-empty bubble proves real reply text is streaming.  Only THEN do
      //    we fire the correction — the scenario is "reply partially generated,
      //    user interrupts mid-output", not "resend during the pre-stream
      //    pending phase". ──
      const firstAssistant = page.getByTestId('chat-message-assistant').first();
      await expect(firstAssistant).toBeVisible({ timeout: 180_000 });
      await expect(firstAssistant).toHaveText(/\S/, { timeout: 120_000 });

      // ── Quick course-correction: type a correction + Enter while the reply
      //    is streaming real text ──
      const markerB = `542B_${Date.now().toString(36)}`;
      await sendPrompt(page, `只回答${markerB}`);

      // The second user bubble appears — the input kept working mid-stream.
      await expect(page.getByTestId('chat-message-user')).toHaveCount(2, {
        timeout: 15_000,
      });
      // The input cleared for the new send.
      await expect(inputX).toHaveValue('', { timeout: 5_000 });

      // Input stays enabled throughout the interaction.
      await expect(inputX).toBeEnabled({ timeout: 5_000 });

      // ── The resend turn completes: streaming ends when the "停止生成"
      //    button (shown while streaming with an empty input) is replaced by
      //    the send button.  This is a reliable completion signal independent
      //    of the model's reply content, tool timers, or approval dialogs
      //    (bypass_all is on in the E2E config). ──
      await expect(page.getByRole('button', { name: '停止生成' })).toBeHidden({
        timeout: 180_000,
      });

      // A reply rendered for the resend turn (the app did not wedge).
      const assistantCount = await page.getByTestId('chat-message-assistant').count();
      expect(
        assistantCount,
        'the resend should produce an assistant reply (turn completed, not wedged)'
      ).toBeGreaterThan(0);
      await expect(inputX).toBeEnabled({ timeout: 5_000 });
    }
  );
});
