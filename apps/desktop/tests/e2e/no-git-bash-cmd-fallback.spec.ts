/**
 * No-Git-Bash cmd-fallback E2E (#865)
 *
 * Regression coverage for a Windows host that has NEITHER Git Bash NOR an
 * enabled WSL: `find_git_bash()` returns None, so exec falls back to
 * Windows cmd.  The environment description must warn the AI not to run
 * bash/wsl commands — PATH resolves `bash` to System32\bash.exe (the WSL
 * entrypoint stub), which errors with `EXECUTABLE NOT FOUND` /
 * `WSL not installed`.
 *
 * We force that environment deterministically with MIQI_FORCE_CMD_EXEC=1
 * (`find_git_bash()` short-circuits to None even when Git Bash is
 * installed) plus sandbox disabled, then ask the AI to run a cmd-only
 * marker `echo NO_GITBASH_CMD_OK_%RANDOM%`.  `%RANDOM%` expands to digits
 * under cmd but stays literal under bash, so matching `_\d+` proves the
 * cmd path was taken — and the absence of the WSL-stub errors proves the
 * original failure is gone.
 *
 * Windows-only: `%RANDOM%` is a cmd-ism.  On macOS/Linux the suite is
 * skipped (exec uses bash there, and the marker never expands).
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron no-git-bash-cmd-fallback.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
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

// Capture the prior value at module load so afterAll can restore it —
// module-level process.env mutation otherwise leaks into sibling specs
// that share this worker process.
const CMD_EXEC_ENV = 'MIQI_FORCE_CMD_EXEC';
const priorCmdExec = process.env[CMD_EXEC_ENV];

test.describe('No-Git-Bash cmd fallback E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    test.skip(process.platform !== 'win32', 'cmd %RANDOM% marker is Windows-only');

    // Force the cmd fallback regardless of Git Bash being installed.  Must
    // be set before launchElectronApp() snapshots process.env into the
    // Electron/bridge child.
    process.env[CMD_EXEC_ENV] = '1';

    const fixture = await launchElectronApp((config) => {
      config.tools = {
        ...config.tools,
        sandbox: { ...config.tools?.sandbox, enabled: false },
      };
      // Surface the raw exec stdout in the chat timeline (inline exec
      // output) so the assertion below can target the tool's own output
      // rather than only the model's echoed reply.
      config.desktop = {
        ...config.desktop,
        ui: { ...config.desktop?.ui, inlineExecOutput: true },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } finally {
      if (priorCmdExec === undefined) {
        delete process.env[CMD_EXEC_ENV];
      } else {
        process.env[CMD_EXEC_ENV] = priorCmdExec;
      }
    }
  });

  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test(
    'exec runs in cmd and no WSL-stub error when Git Bash is absent',
    { timeout: 8 * 60_000 },
    async () => {
      test.setTimeout(8 * 60_000);

      // `%RANDOM%` is cmd-only: cmd expands it to digits, bash keeps it
      // literal.  A `_\d+` match proves the command actually ran under
      // cmd — the deterministic proof that MIQI_FORCE_CMD_EXEC took the
      // cmd fallback rather than Git Bash.
      const prompt =
        `必须使用 exec 工具执行下面这条命令，然后只回复命令的完整输出，` +
        `不要解释、不要改写：` +
        `echo NO_GITBASH_CMD_OK_%RANDOM%`;

      await sendMessage(page, prompt);
      await approveLoop(page, LLM_TIMEOUT);

      const RUN_CAP = 8 * 60_000;
      const IDLE_DEADLINE = 3 * 60_000;
      const runStart = Date.now();
      let idleDeadline = runStart + IDLE_DEADLINE;
      let text = '';
      let lastText = '';
      while (Date.now() - runStart < RUN_CAP && Date.now() < idleDeadline) {
        // Auto-confirm any approval dialog (networked exec / confirm card).
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
        if (/NO_GITBASH_CMD_OK_\d+/.test(text)) break;
        if (text !== lastText) {
          lastText = text;
          idleDeadline = Date.now() + IDLE_DEADLINE;
        }
        await page.waitForTimeout(1500);
      }

      // Prove the exec TOOL actually ran — the raw stdout is rendered in the
      // inline exec-output <pre> (green block), which only populates from
      // exec_output_delta events on real execution, never from the model's
      // final text.  This guards against a model fabricating the marker.
      const execOutput = page
        .locator('pre.whitespace-pre-wrap')
        .filter({ hasText: /NO_GITBASH_CMD_OK_\d+/ });
      await expect(execOutput.first()).toBeVisible({ timeout: 10_000 });

      // cmd executed the marker and expanded %RANDOM% to digits.
      expect(text).toMatch(/NO_GITBASH_CMD_OK_\d+/);

      await waitForResponseComplete(page, LLM_TIMEOUT);
      text = (await page.locator('main').textContent()) ?? '';

      // The original failure mode — PATH → System32\bash.exe WSL stub —
      // must not appear.
      expect(text).not.toContain('EXECUTABLE NOT FOUND');
      expect(text).not.toContain('子系统未安装');

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });

      await postScreenshotToPr(
        `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        '✅ E2E 通过：无 Git Bash 时 cmd 回退链路（无 WSL 桩报错）'
      );
    }
  );
});
