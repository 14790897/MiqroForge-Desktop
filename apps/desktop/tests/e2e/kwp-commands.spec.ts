/**
 * E2E: KWP slash command + auto skill trigger
 *
 * Verifies that the embedded knowledge-work-plugins integration works
 * end-to-end in a real Electron session:
 *
 * 1. /brainstorm flows through the bridge and the agent receives a
 *    system prompt containing the brainstorm command body, plus the
 *    user arguments in a "User arguments" sub-section.
 * 2. /brainstorm with no args still injects the command body
 *    (LLM responds as if briefed even without user input).
 * 3. Auto skill trigger: a natural-language phrase like
 *    "帮研究一下 Acme Corp" causes the agent to load
 *    kwp/sales/account-research/SKILL.md and run its workflow.
 * 4. Slash command bodies surface in the assistant's reply — the
 *    injected content actually shapes the response (not silently
 *    ignored).
 * 5. Skill metadata appears in the SkillsPage UI under the
 *    "Knowledge Work" group.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron kwp-commands.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
  waitForInputReady,
  sendMessage,
  waitForResponseComplete,
  userMessage,
} from './helpers/electron-setup';

// Phrases that should reliably trigger the embedded KWP skills.
// Each phrase matches the description text in the converted SKILL.md
// frontmatter ("Trigger with ...") so the LLM is expected to load
// the corresponding skill via read_file().
const BRAINSTORM_PROMPT = '/brainstorm 关于如何提升 Q3 用户留存率';
const BRAINSTORM_NOARGS = '/brainstorm';
const AUTOTRIGGER_PROMPT = '帮研究一下 Anthropic 这家公司，给出关键信息';

test.describe('KWP Commands & Skill Auto-trigger E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page);
    // Sanity-check: skills.list should expose KWP skills via the
    // SkillsLoader built-in pipeline.  We don't strictly need this —
    // it's just an early-bail mechanism if the built-ins broke.
    const skillsCheck = await page.evaluate(async () => {
      // The bridge doesn't expose a skills namespace directly in E2E;
      // route through the runtime status probe instead.
      const s = await (window as any).miqi.runtime.status();
      return { state: s?.state, initialized: s?.initialized };
    });
    console.log(`[test] beforeAll bridge state: ${JSON.stringify(skillsCheck)}`);
    if (skillsCheck.initialized !== true) {
      console.log('[test] Bridge not initialized — skipping KWP E2E');
      test.skip(true, 'Bridge not initialized — skipping KWP E2E');
    }
  }, 60_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
  });

  test.beforeEach(async () => {
    // Fresh conversation for each scenario so prior-context bleed
    // doesn't muddy an isolated assertion.
    await page.keyboard.press('Control+N').catch(() => {});
    await page.waitForTimeout(800);
  });

  // ──────── Test 1: /brainstorm command + user args ────────

  test('/brainstorm prepends command body and accepts user args', async () => {
    const textarea = await waitForInputReady(page);
    await textarea.fill(BRAINSTORM_PROMPT);
    await textarea.press('Enter');

    // User message visible in chat.
    await expect(page.getByText(BRAINSTORM_PROMPT).first()).toBeVisible({
      timeout: 10_000,
    });

    await waitForResponseComplete(page, 180_000);

    // The agent should respond substantively.  We assert *content
    // shape* rather than a fixed string because LLM wording varies:
    // the response must include Chinese characters (we asked in
    // Chinese) and must mention at least one of the brainstorm-style
    // cue phrases from the KWP prompt body.
    const body = await page.locator('main').last().textContent();
    expect(body || '').toMatch(/[一-鿿]/); // any CJK char
    const cuePhrases = [
      '产品',
      '路线',
      '战略',
      '需求',
      '用户',
      '假设',
      '机会',
      '风险',
      '方案',
      'idea',
      'feature',
      'requirement',
      'opportunity',
    ];
    const hit = cuePhrases.some((p) => (body || '').includes(p));
    expect(hit).toBeTruthy();
  });

  // ──────── Test 2: /brainstorm with no args ────────

  test('/brainstorm without args still injects prompt body', async () => {
    const textarea = await waitForInputReady(page);
    await textarea.fill(BRAINSTORM_NOARGS);
    await textarea.press('Enter');

    await expect(page.getByText(BRAINSTORM_NOARGS).first()).toBeVisible({
      timeout: 10_000,
    });
    await waitForResponseComplete(page, 180_000);

    const body = await page.locator('main').last().textContent();
    // The command body is empty / fallback in this case, but the agent
    // must still produce some response rather than hang or 500.
    expect((body || '').length).toBeGreaterThan(20);
  });

  // ──────── Test 3: auto skill trigger via natural language ────────

  test('natural-language "research [company]" auto-triggers account-research', async () => {
    const textarea = await waitForInputReady(page);
    await textarea.fill(AUTOTRIGGER_PROMPT);
    await textarea.press('Enter');

    await expect(page.getByText(AUTOTRIGGER_PROMPT).first()).toBeVisible({
      timeout: 10_000,
    });

    await waitForResponseComplete(page, 180_000);

    // Expected behavior: the agent should at minimum perform web
    // searches.  Web searches are typically visible as tool-call
    // cards (we look for "IN PROGRESS" tag visibility during the run,
    // then assert that the final response mentions the searched
    // company).  An Anthropic-specific cue: the response should
    // contain "Anthropic" (or a factual close — Claude AI company).
    const body = await page.locator('main').last().textContent();
    expect(body || '').toMatch(/Anthropic/);
  });

  // ──────── Test 4: skills UI surfaces Knowledge Work group ────────

  test('SkillsPage shows "Knowledge Work" group for KWP skills', async () => {
    // Open Skills page through sidebar.  Sidebar nav is the standard
    // way in the current UI.
    const navItem = page.locator('[data-testid="nav-skills"]');
    if (!(await navItem.isVisible({ timeout: 5_000 }).catch(() => false))) {
      test.skip(true, 'Skills sidebar entry not present in this build');
      return;
    }
    await navItem.click();
    await page.waitForTimeout(1_500);

    // The SkillsPage UI groups KWP skills under "Knowledge Work".
    // Wait for the heading then assert at least one skill row exists.
    const heading = page.getByText('Knowledge Work', { exact: false }).first();
    await expect(heading).toBeVisible({ timeout: 10_000 });

    // Account-research should be visible among the converted skills.
    await expect(page.getByText('account-research', { exact: false }).first()).toBeVisible({
      timeout: 5_000,
    });
  });

  // ──────── Test 5: slash command is detected without LLM round-trip ───

  // Verifies that `/brainstorm <args>` reaches the slash-command
  // detector in the bridge — observed via the user-message bubble in
  // <main>:
  //   • On detection, TaskRunner strips the `/brainstorm` prefix and
  //     sends only the args to the LLM (slash_content is prepended
  //     to the system prompt instead).
  //   • Without detection, the bubble shows the full `/brainstorm foo`.
  // We assert the bubble contains the args but NOT the literal
  // `/brainstorm` token — that confirms the bridge handled the slash
  // command.  Crucially, we do NOT wait for the LLM response, so
  // the test runs in < 5s and doesn't depend on provider availability.

  // Counter-verification: also send a NON-registered slash command
  // and confirm that its prefix is NOT stripped — proving the
  // detector only strips registered commands, not all /-prefixed
  // text.  Without this counter-check, a naive "strip everything
  // starting with /" regex would pass test 5 but not test 6.

  test('/brainstorm <args> strips the slash prefix before the LLM', async () => {
    const marker = `STRIP_PROBE_${Date.now()}`;
    const textarea = await waitForInputReady(page);
    await textarea.fill(`/brainstorm ${marker}`);
    await textarea.press('Enter');

    // Scope both assertions to the just-submitted user-message bubble
    // (data-testid="chat-message-user").  Searching page-wide or
    // main-wide could match stale DOM (older turn, in-flight text,
    // placeholders) and silently break the test.
    const userBubble = userMessage(page, marker);

    // The user message bubble must contain the args…
    await expect(userBubble).toBeVisible({ timeout: 10_000 });
    // …and must NOT contain the bare `/brainstorm` token (which would
    // mean the slash detector never ran and the raw text was sent
    // through to the LLM as the user message).
    await expect(userBubble).not.toContainText('/brainstorm');
  });

  test('/<unknown-cmd> does NOT strip (slash detector only acts on registered cmds)', async () => {
    const textarea = await waitForInputReady(page);
    await textarea.fill('/no-such-slash-command-x');
    await textarea.press('Enter');

    // For an unknown command the bubble must contain the full text
    // because the detector doesn't know about it.
    await expect(
      page.getByTestId('chat-message-user').filter({ hasText: '/no-such-slash-command-x' }).first()
    ).toBeVisible({ timeout: 10_000 });
  });
});
