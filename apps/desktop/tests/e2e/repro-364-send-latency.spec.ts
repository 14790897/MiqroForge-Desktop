/**
 * Regression spec for issue #364 — [ChatConsole] shows the optimistic user
 * bubble and clears the input IMMEDIATELY on Enter, even when the provider
 * check is slow.
 *
 * Pre-fix behaviour: `handleSend` awaited `window.miqi.providers.list()` FIRST,
 * and only inserted the user message / cleared the input AFTER that IPC
 * round-trip resolved.  On a cold start the bridge queues providers.list behind
 * its init (PyInstaller extract 5-15s + WSL deps + sandbox) — so the UI sat
 * silent for seconds after Enter, making the user press Enter again →
 * duplicate messages/tasks.
 *
 * Post-fix expectation (guarded here):
 *   - the user bubble appears immediately (not blocked by the slow check)
 *   - the input box clears immediately
 *   - a second send fired while the check is still pending is swallowed by the
 *     per-session pending guard → exactly ONE user bubble in the end
 *   - a pending spinner shows on the optimistic bubble while it waits
 *
 * Trigger: patch the main-process `providers:list` handler to DELAY 5s then
 * return a healthy configured provider.
 *
 * Run:
 *   cd apps/desktop
 *   npm run build
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *     npx playwright test --config=playwright.config.ts --project=electron \
 *       repro-364-send-latency --reporter=list
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

const PROVIDERS_LIST = 'providers:list';
const PROVIDER_DELAY_MS = 5_000;

test.describe('#364: optimistic send shows user bubble immediately while providers:list is slow', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.afterAll(async () => {
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('shows the user bubble and clears the input immediately while providers:list is slow', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;

    // ── Slow the providers:list handler (main process) — simulate a cold
    //    bridge that queues this IPC behind its init work. ──
    await electronApp.evaluate(
      async ({ ipcMain: ipc }, args: any) => {
        ipc.removeHandler(args.channel);
        ipc.handle(args.channel, async () => {
          await new Promise((r) => setTimeout(r, args.delay));
          return {
            providers: [
              {
                name: 'openai',
                display_name: 'OpenAI',
                env_key: 'OPENAI_API_KEY',
                provider_type: 'openai',
                is_gateway: false,
                is_local: false,
                default_api_base: 'https://api.openai.com/v1',
                configured: true,
                api_base: null,
              },
            ],
          };
        });
      },
      { channel: PROVIDERS_LIST, delay: PROVIDER_DELAY_MS }
    );

    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await textarea.fill('repro-364 消息');
    const t0 = Date.now();

    // ── Double-send while providers:list is STILL pending — the #364
    //    scenario where the user, seeing no response, sends again.  The first
    //    Enter starts the optimistic send (input cleared, pending guard set).
    //    Fire a second Enter keydown in the same synchronous tick so it reaches
    //    the send path again; the send flow must swallow it (empty-input check
    //    or per-session pending guard) instead of spawning a duplicate bubble. ──
    await page.evaluate(() => {
      const ta = document.querySelector<HTMLTextAreaElement>(
        '[data-testid="chat-input-container"] textarea'
      );
      if (!ta) throw new Error('textarea not found');
      for (let i = 0; i < 2; i++) {
        ta.dispatchEvent(
          new KeyboardEvent('keydown', {
            key: 'Enter',
            code: 'Enter',
            keyCode: 13,
            bubbles: true,
            cancelable: true,
          })
        );
      }
    });

    // Sample right after Enter — the optimistic UI must have committed
    // already: input cleared, user bubble visible, pending spinner showing —
    // while providers:list is still pending.  (Polling, not a fixed sleep:
    // a slow CI runner must not turn a rendering hiccup into a false fail —
    // the *time* budget below still proves optimistic rendering.)
    await page.waitForTimeout(500);
    const inputVal = await textarea.inputValue();
    let bubbleVisibleEarly = false;
    try {
      await page.getByTestId('chat-message-user').last().waitFor({
        state: 'visible',
        timeout: 5_000,
      });
      bubbleVisibleEarly = true;
    } catch {
      bubbleVisibleEarly = false;
    }
    const tEarly = Date.now() - t0;
    const bubbleCountEarly = await page.getByTestId('chat-message-user').count();
    // The optimistic user bubble should show a pending spinner while the send
    // is still waiting on the slow provider check (poll up to 5s).
    let pendingSpinnerVisible = false;
    try {
      await page
        .locator('[data-testid="chat-message-user"] svg.animate-spin')
        .last()
        .waitFor({ state: 'visible', timeout: 5_000 });
      pendingSpinnerVisible = true;
    } catch {
      pendingSpinnerVisible = false;
    }

    // Wait until the (eventually-appearing) user bubble shows, or timeout.
    let tBubble = -1;
    try {
      await page
        .getByTestId('chat-message-user')
        .last()
        .waitFor({
          state: 'visible',
          timeout: PROVIDER_DELAY_MS + 5_000,
        });
      tBubble = Date.now() - t0;
    } catch {
      tBubble = -1;
    }

    const stillFilled = inputVal.trim().length > 0;

    // After providers:list resolves and the single turn settles, exactly ONE
    // user bubble must remain — the duplicate send was swallowed by the guard.
    // Poll up to 20s instead of a fixed sleep: a slow runner must only fail on
    // a REAL duplicate, not on "the turn hasn't settled yet".
    let bubbleCountFinal = -1;
    await expect
      .poll(
        async () => {
          bubbleCountFinal = await page.getByTestId('chat-message-user').count();
          return bubbleCountFinal;
        },
        { timeout: 20_000, intervals: [500, 1_000, 1_500, 2_000] }
      )
      .toBe(1);

    console.log(
      `\n[repro-364] 500ms after Enter: input still filled=${stillFilled}, ` +
        `user bubble visible=${bubbleVisibleEarly} (t=${tEarly}ms), ` +
        `bubble count early=${bubbleCountEarly}, pending spinner=${pendingSpinnerVisible}`
    );
    console.log(
      `[repro-364] user bubble appeared after ${tBubble}ms (providers:list delayed ${PROVIDER_DELAY_MS}ms)`
    );
    console.log(
      `[repro-364] 2× send → final user bubble count = ${bubbleCountFinal} ` + `(duplicate if > 1)`
    );

    // Post-fix expectations: the optimistic bubble appears immediately, the
    // input clears immediately, and the duplicate send never creates a second
    // bubble.
    expect(
      bubbleVisibleEarly,
      'user bubble should appear immediately on Enter (optimistic UI), ' +
        'not wait for providers:list'
    ).toBe(true);
    expect(stillFilled, 'input box should clear immediately on Enter').toBe(false);
    expect(
      pendingSpinnerVisible,
      'optimistic user bubble should show a pending spinner while waiting'
    ).toBe(true);
    expect(
      bubbleCountFinal,
      `double send must produce exactly one user bubble, got ${bubbleCountFinal}`
    ).toBe(1);
    expect(
      tBubble,
      `user bubble appeared at ${tBubble}ms — should be ~0, not blocked by the ` +
        `${PROVIDER_DELAY_MS}ms providers:list delay`
    ).toBeLessThan(PROVIDER_DELAY_MS);
  });
});
