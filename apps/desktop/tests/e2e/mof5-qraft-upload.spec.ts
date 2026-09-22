/**
 * MOF-5 Qraft Upload E2E — real user scenario, sandbox OFF.
 *
 * Full acceptance test of the no-sandbox exec chain (#793 NONE policy,
 * #796 accurate environment description, #801 Git Bash exec): run the
 * real user task — 生成 MOF-5 市场合成价格报告并确认执行，使用
 * qraft-workflowspec-export 技能上传到 forge — with the sandbox
 * disabled, and assert the upload succeeds from the AI's user-visible
 * reply.
 *
 * The spec points the app at the user's REAL workspace
 * (C:\Users\Intership003\.miqi\workspace): the qraft token lives in
 * .qraft/token.json there, and the e2e temp home would have neither
 * token nor prior artifacts.  The qraft-workflowspec-export skill
 * itself ships with the app under miqi/skills/.
 *
 * Windows-only: the workspace path and Git Bash exec are Windows
 * specifics (CI electron-e2e/macos-e2e skip it, like bwrap-dependent
 * specs do).
 *
 * Prerequisites:
 *   - Windows with Git Bash installed (or Git for Windows)
 *   - A VALID Qraft login in the real workspace (`.qraft/token.json`) —
 *     an expired token fails the upload step with NOT_LOGGED_IN, which
 *     is the expected failure mode and surfaces in the AI reply (the
 *     exec chain itself is already proven by that point: report
 *     generation, skill loading, script execution all use Git Bash).
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron mof5-qraft-upload.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { postScreenshotToPr } from './helpers/pr-image-post';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
} from './helpers/electron-setup';

// Opt-in credential-bound scenario: read the workspace from the
// environment, fall back to the dev default.  The spec SKIPS when the
// Qraft token is missing or about to expire — CI workers without a
// fresh login must not fail on this.
const REAL_WORKSPACE =
  process.env.MIQI_E2E_WORKSPACE ?? 'C:\\Users\\Intership003\\.miqi\\workspace';

function qraftTokenValid(): boolean {
  const tokenPath = join(REAL_WORKSPACE, '.qraft', 'token.json');
  if (!existsSync(tokenPath)) return false;
  try {
    const d = JSON.parse(readFileSync(tokenPath, 'utf-8'));
    const exp = d.expiresAt ?? d.expires_at ?? d.exp;
    if (typeof exp !== 'number') return false;
    // The full flow takes 20-40 min — require at least 30 min left.
    return exp - Date.now() > 30 * 60_000;
  } catch {
    return false;
  }
}

test.describe('MOF-5 Qraft Upload E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    test.skip(process.platform !== 'win32', 'Windows-only real-scenario spec');
    test.skip(
      !qraftTokenValid(),
      'Opt-in: requires a valid Qraft login in ' +
        `${join(REAL_WORKSPACE, '.qraft', 'token.json')} (set MIQI_E2E_WORKSPACE)`
    );

    const fixture = await launchElectronApp((config) => {
      // Sandbox OFF — this is the whole point of the regression suite.
      config.tools = {
        ...config.tools,
        sandbox: { ...config.tools?.sandbox, enabled: false },
        // The skill's validate/upload scripts take >30s (12KB JSON +
        // PDF sha256) — the user's default exec timeout kills them.
        exec: { ...config.tools?.exec, timeout: 120 },
      };
      // Real workspace: qraft token + existing MOF-5 session artifacts.
      config.agents = {
        ...config.agents,
        defaults: { ...config.agents?.defaults, workspace: REAL_WORKSPACE },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test.afterEach(async () => {
    // Surface evidence into the PR: success screenshots are posted by the
    // test body; failures post the auto-captured failure screenshot here.
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test(
    'MOF-5 price report generation + qraft-workflowspec-export upload succeeds without sandbox',
    { timeout: 45 * 60_000 },
    async () => {
      test.setTimeout(45 * 60_000);

      await createNewConversation(page);

      // Blank (empty / whitespace-only) overrides count as unset.
      const prompt =
        (process.env.MIQI_E2E_PROMPT ?? '').trim() ||
        '帮我生成 MOF-5 市场合成价格报告并确认执行，' +
          '使用 qraft-workflowspec-export 技能上传到 forge';

      await sendMessage(page, prompt);

      // The skill flow requires user confirmations (方案清单、上传确认)
      // via ask_user_confirm_card — auto-confirm them while waiting for
      // the final user-visible outcome: a successful upload.  The deadline
      // is activity-driven: deep-thinking models can spend many minutes
      // reasoning before the first tool call, so keep extending it while
      // the UI keeps changing (streaming/tool results), capped at MAX_WAIT.
      // Idle-deadline model: activity (text change / confirm click)
      // extends the idle deadline; a hard cap from test start bounds the
      // whole run.  A silent-but-active turn is never cut at a fixed
      // deadline, and a truly dead turn fails fast at the idle deadline.
      const RUN_CAP = 40 * 60_000; // hard cap from test start
      const IDLE_DEADLINE = 10 * 60_000; // long silent model-thinking stretches
      const runStart = Date.now();
      let idleDeadline = runStart + IDLE_DEADLINE;
      let text = '';
      let lastText = '';
      while (Date.now() - runStart < RUN_CAP && Date.now() < idleDeadline) {
        // Prefer the card's primary choice (may be a custom label like
        // "PDF 报告" — label-matching alone would let the card time out).
        const primary = page.locator('[data-testid="confirm-card-primary"]');
        if (await primary.isVisible({ timeout: 300 }).catch(() => false)) {
          await primary.first().click();
          console.log('[test] Auto-confirmed card: (primary choice)');
          idleDeadline = Date.now() + IDLE_DEADLINE;
        } else {
          // Cards whose options are all custom (e.g. upload-conflict:
          // "作为新版本上传") have no accent primary — click the first
          // non-cancel choice to keep the flow moving.
          const choices = page.locator('[data-testid="confirm-card-choice"]');
          const count = await choices.count();
          let clicked = false;
          for (let i = 0; i < count; i++) {
            const c = choices.nth(i);
            const label = (await c.textContent()) ?? '';
            if (/取消/.test(label)) continue;
            if (await c.isVisible({ timeout: 300 }).catch(() => false)) {
              await c.click();
              console.log(`[test] Auto-confirmed card: (choice ${label.trim().slice(0, 20)})`);
              idleDeadline = Date.now() + IDLE_DEADLINE;
              clicked = true;
              break;
            }
          }
          if (!clicked) {
            for (const name of ['确认上传', '确认执行', '确认', '继续执行']) {
              const btn = page.getByRole('button', { name, exact: false });
              if (await btn.isVisible({ timeout: 300 }).catch(() => false)) {
                await btn.click();
                console.log(`[test] Auto-confirmed card: ${name}`);
                idleDeadline = Date.now() + IDLE_DEADLINE;
                break;
              }
            }
          }
        }
        text = (await page.locator('main').textContent()) ?? '';
        if (/上传成功|HTTP\s*200/.test(text)) break;
        if (text !== lastText) {
          lastText = text;
          idleDeadline = Date.now() + IDLE_DEADLINE;
        }
        await page.waitForTimeout(1500);
      }

      // User-visible outcome: the platform upload succeeded.
      expect(text).toMatch(/上传成功|HTTP\s*200/);

      await waitForResponseComplete(page, LLM_TIMEOUT);
      text = (await page.locator('main').textContent()) ?? '';
      expect(text).toMatch(/上传成功|HTTP\s*200/);

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });

      await postScreenshotToPr(
        `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        '✅ E2E 验收通过：MOF-5 报告生成 + qraft-workflowspec-export 上传'
      );
    }
  );
});
