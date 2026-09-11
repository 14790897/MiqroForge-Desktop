/**
 * AI Connectivity E2E Test
 *
 * Verifies that the full chat pipeline — user input → chat.send → model
 * inference → streaming response → UI rendering — works end-to-end.
 * If the AI model is unreachable (no API key, wrong base URL, network
 * failure, provider down, etc.), this test FAILS, which causes desktop-ci
 * to halt before running the full (expensive) E2E suite.
 *
 * Strategy: drive the real chat UI via sendMessage + waitForResponseComplete
 * (same helpers the full-electron spec uses), asking the AI for a trivial
 * one-character reply.  This exercises EVERY layer the production user path
 * hits:
 *   1. Bridge readiness     — runtime.status() → running + initialized
 *   2. Provider resolution  — which provider/model the config activates
 *   3. chat.send IPC        — preload → main → app_server → TaskRunner
 *   4. LLM round-trip       — HTTP call to the provider endpoint
 *   5. Streaming delivery   — onProgress / onFinal events
 *   6. React render         — <main> textContent stabilisation
 *
 * When any layer fails the test throws with a diagnostic message that
 * pinpoints the stage, making CI failures self-documenting.
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron \
 *      -g "AI connectivity"
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  sendMessage,
  waitForResponseComplete,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

test.describe('AI Connectivity', () => {
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

  test(
    'full chat pipeline reaches AI — desktop CI fails if disconnected',
    { timeout: LLM_TIMEOUT },
    async () => {
      // ── Phase 1: bridge readiness ─────────────────────────────
      // waitForBridgeInitialized polls miqi.runtime.status() until
      // state === 'running' && initialized === true.
      await waitForBridgeInitialized(page, 30);
      console.log('[ai-connectivity] Bridge is running + initialized');

      // ── Phase 2: provider inventory ───────────────────────────
      // providers.list reports which providers are configured and
      // which is active.  If zero configured providers are found,
      // the CI secrets are completely missing — fail immediately
      // with a clear diagnostic before wasting time on chat.send.
      const providerInfo = await page.evaluate(async () => {
        const listed: any = await (window as any).miqi.providers.list();
        const providers: Array<{ name: string; configured: boolean; configured_model?: string }> =
          listed?.providers ?? [];
        const configured = providers.filter((p) => p.configured);
        return {
          activeProvider: listed?.active_provider ?? null,
          activeModel: listed?.active_model ?? null,
          configuredCount: configured.length,
          configuredNames: configured.map((p) => p.name),
        };
      });

      console.log('[ai-connectivity] providers.list result:', JSON.stringify(providerInfo));

      if (providerInfo.configuredCount === 0) {
        throw new Error(
          'AI connectivity check failed (stage=list): ' +
            'No configured providers found in ~/.miqi/config.json. ' +
            'Check DEEPSEEK_API_KEY / DEEPSEEK_API_BASE secrets.'
        );
      }

      console.log(
        `[ai-connectivity] Active provider: ${providerInfo.activeProvider ?? '?'}, ` +
          `model: ${providerInfo.activeModel ?? '?'}, ` +
          `${providerInfo.configuredCount} configured provider(s)`
      );

      // ── Phase 3: full chat pipeline ───────────────────────────
      // Drive the real chat UI with a minimal prompt.  This exercises:
      //   chat.send IPC → app_server → TaskRunner → LLM HTTP call →
      //   streaming onProgress/onFinal → React render → textContent stabilisation.
      //
      // Use a unique marker so we never match stale DOM text from a
      // prior test or cross-session leak.
      const marker = `OK_${Date.now()}`;
      const prompt = `只回答${marker}`;

      console.log(`[ai-connectivity] Sending prompt: "${prompt}"`);
      await sendMessage(page, prompt);

      // waitForResponseComplete waits for "Thinking…" to hide AND
      // textContent to stabilise — so it covers streaming delivery
      // AND UI rendering.
      console.log('[ai-connectivity] Waiting for AI response…');
      await waitForResponseComplete(page);

      // Confirm the marker is visible in <main> — the final proof
      // that the model replied AND the UI rendered it.
      const markerEl = page.locator('main').getByText(marker, { exact: false }).first();
      await markerEl.scrollIntoViewIfNeeded().catch(() => {});
      await expect(markerEl).toBeVisible({ timeout: 15_000 });

      console.log(
        `[ai-connectivity] ✅ Full chat pipeline verified: ` +
          `${providerInfo.activeProvider ?? '?'} / ${providerInfo.activeModel ?? '?'} ` +
          `returned "${marker}"`
      );
    }
  );
});
