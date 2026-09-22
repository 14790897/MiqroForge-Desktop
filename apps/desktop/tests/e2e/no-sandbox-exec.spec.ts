/**
 * No-Sandbox Host Exec E2E
 *
 * Regression coverage for #792 (PR #793): with the sandbox disabled,
 * the legacy orchestrator path (chat.send → TaskRunner →
 * SandboxPolicyEngine) used to select RESTRICTED + BLOCK_ALL network
 * policy for exec, so EVERY command was rejected with
 * "RESTRICTED 沙箱无法强制网络隔离…命令未执行" — blocking tasks that
 * needed network (e.g. the Qraft upload script hitting
 * test.forge.miqroera.com).
 *
 * After the fix, exec with no sandbox available selects NONE and runs
 * directly on the host — through Git Bash on Windows when installed —
 * without restrictions (network, cwd and path checks all bypassed; the
 * user runs without isolation by choice).
 *
 * The spec drives the real chat flow with the sandbox disabled via
 * patchConfig, asks the agent to run a marker command whose value is
 * generated at execution time (shell PID), auto-confirms the
 * ask_user_confirm_card the AI may raise for the networked curl, and
 * asserts the user-visible outcome: the execution-time markers appear
 * in the reply and the old fail-closed block message does not.
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron no-sandbox-exec.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { execSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { postScreenshotToPr } from './helpers/pr-image-post';
import {
  LLM_TIMEOUT,
  waitForBridgeInitialized,
  sendMessage,
  waitForResponseComplete,
  approveLoop,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

test.describe('No-Sandbox Host Exec E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp((config) => {
      // Deterministic no-sandbox path: exec resolves to RESTRICTED host
      // execution through SandboxPolicyEngine (bwrap_available=False).
      config.tools = {
        ...config.tools,
        sandbox: { ...config.tools?.sandbox, enabled: false },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test(
    'exec runs on host when sandbox is disabled (no RESTRICTED network block)',
    { timeout: 8 * 60_000 },
    async () => {
      test.setTimeout(8 * 60_000);

      // Git Bash is the whole point of this spec — without it the exec
      // falls back to cmd and the bash-only command below cannot run.
      // Mirror the product's find_git_bash(): known install locations
      // first, then PATH, rejecting the WSL entrypoint (System32) AND
      // Cygwin (uses /cygdrive/c paths, not the /c/ convention).
      if (process.platform === 'win32') {
        const gitBashAvailable = (): boolean => {
          const candidates = [
            'C:\\Program Files\\Git\\bin\\bash.exe',
            'C:\\Program Files (x86)\\Git\\bin\\bash.exe',
            'C:\\Program Files\\Git\\usr\\bin\\bash.exe',
            `${process.env.LOCALAPPDATA}\\Programs\\Git\\bin\\bash.exe`,
          ];
          if (candidates.some(existsSync)) return true;
          try {
            const where = execSync('where bash', {
              encoding: 'utf8',
              windowsHide: true,
            });
            return where
              .split(/\r?\n/)
              .map((l) => l.trim())
              .filter(Boolean)
              .some(
                (l) =>
                  !/^C:\\Windows\\System32\\bash\.exe$/i.test(l) &&
                  !/\\(?:cygwin|cygwin64)\\/i.test(l)
              );
          } catch {
            return false;
          }
        };
        if (!gitBashAvailable()) {
          test.skip(true, 'Git Bash not installed — cmd fallback path');
          return;
        }
      }

      // Both marker values are generated at EXECUTION time (shell PID
      // $$) — they cannot appear in the prompt or in an AI echo, so a
      // match in the reply proves the commands really ran.  POSIX-safe:
      // $RANDOM is a bashism (empty under /bin/sh on Linux CI) and
      // $(...) substitution is blocked by the exec safety guard — the AI
      // would split the command and drop the digits.  The second marker
      // is emitted only AFTER curl succeeds (--fail + &&), proving the
      // networked aspect of the original #792 scenario actually worked.
      const prompt =
        `必须使用 exec 工具执行下面这条命令，然后只回复命令的完整输出，` +
        `不要解释、不要改写：` +
        `echo NO_SANDBOX_EXEC_OK_$$ ` +
        `&& curl -sS --fail --max-time 10 -o /dev/null example.com ` +
        `&& echo NO_SANDBOX_NETWORK_OK_$$`;

      await sendMessage(page, prompt);
      await approveLoop(page, LLM_TIMEOUT);

      // Wait for the ACTUAL tool output.  The AI may first raise an
      // ask_user_confirm_card (networked exec) — auto-confirm it so the
      // turn continues.  With the old fail-closed policy the exec result
      // never appears, so the marker poll below still times out.
      // CI LLM providers are slow (#707) — a fixed 45s poll expired while
      // the exec was still in flight.  Activity-driven deadline: extend
      // while the UI keeps changing, capped at MAX_WAIT.
      const RUN_CAP = 8 * 60_000; // hard cap from test start
      const IDLE_DEADLINE = 3 * 60_000; // slow CI LLM stretches
      const runStart = Date.now();
      let idleDeadline = runStart + IDLE_DEADLINE;
      let text = '';
      let lastText = '';
      while (Date.now() - runStart < RUN_CAP && Date.now() < idleDeadline) {
        const primary = page.locator('[data-testid="confirm-card-primary"]');
        if (await primary.isVisible({ timeout: 400 }).catch(() => false)) {
          await primary.first().click();
          idleDeadline = Date.now() + IDLE_DEADLINE;
        } else {
          const choices = page.locator('[data-testid="confirm-card-choice"]');
          const count = await choices.count();
          let clicked = false;
          for (let i = 0; i < count; i++) {
            const c = choices.nth(i);
            const label = (await c.textContent()) ?? '';
            if (/取消/.test(label)) continue;
            if (await c.isVisible({ timeout: 400 }).catch(() => false)) {
              await c.click();
              idleDeadline = Date.now() + IDLE_DEADLINE;
              clicked = true;
              break;
            }
          }
          if (!clicked) {
            const confirmBtn = page.getByRole('button', { name: '确认执行' });
            if (await confirmBtn.isVisible({ timeout: 400 }).catch(() => false)) {
              await confirmBtn.click();
              idleDeadline = Date.now() + IDLE_DEADLINE;
            }
          }
        }
        text = (await page.locator('main').textContent()) ?? '';
        if (/NO_SANDBOX_EXEC_OK_\d+/.test(text) && /NO_SANDBOX_NETWORK_OK_\d+/.test(text)) {
          break;
        }
        if (text !== lastText) {
          lastText = text;
          idleDeadline = Date.now() + IDLE_DEADLINE;
        }
        await page.waitForTimeout(1500);
      }
      expect(text).toMatch(/NO_SANDBOX_EXEC_OK_\d+/);
      expect(text).toMatch(/NO_SANDBOX_NETWORK_OK_\d+/);

      await waitForResponseComplete(page, LLM_TIMEOUT);

      text = (await page.locator('main').textContent()) ?? '';

      // The old fail-closed block must not appear in the reply.
      expect(text).not.toContain('无法强制网络隔离');
      expect(text).not.toContain('命令未执行');

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });

      await postScreenshotToPr(
        `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        '✅ E2E 通过：无沙箱 exec 链路（Git Bash/cmd 回退）'
      );
    }
  );
});
