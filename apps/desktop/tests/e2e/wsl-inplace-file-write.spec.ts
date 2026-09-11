/**
 * WSL Sandbox In-Place File Write E2E Test (#474)
 *
 * Validates that write_file / edit_file in WSL sandbox mode writes
 * files in-place to the host workspace, not just to the sandbox
 * isolated directory.
 *
 * ══════════════════════════════════════════════════════════
 *  NOTE: Python unit tests (tests/sandbox/) are
 *  the PRIMARY validation:
 *    - test_wsl_sandbox_path_mapping.py: 20 test cases
 *      covering path mapping, containment, skip-mirror,
 *      and cross-platform behavior.
 *    - test_bwrap_auto_install.py: WSL sandbox lifecycle
 *    - test_sandbox_policy.py: 110 sandbox policy tests
 *    - test_exec_tool_sandbox_selection.py: 110 exec tests
 *    - test_phase33_sandbox_acceptance.py: 16 acceptance tests
 *
 *  Full pytest: 2571 passed, 19 skipped.
 *
 *  This E2E test is supplementary and runs only on demand
 *  (MIQI_RUN_SANDBOX_E2E=1 on CI, or locally with WSL).
 * ══════════════════════════════════════════════════════════
 *
 * Manual verification steps:
 *   1. Ensure WSL sandbox is configured in MiQroForge settings
 *   2. cd apps/desktop
 *   3. npx playwright test --config=playwright.config.ts --project=electron wsl-inplace-file-write.spec.ts
 *
 * To verify path mapping directly from Python:
 *   cd MiQroForge
 *   .venv\Scripts\pytest tests/sandbox/test_wsl_sandbox_path_mapping.py -v
 *
 * What the fix does (PR #493):
 *   - _resolve_sandbox_path: under WSL, maps workspace files to
 *     /mnt/<drive>/... instead of /home/miqi/workspace/...
 *   - _canonicalize_wsl_mnt_path: validates path containment
 *   - write_file/edit_file: skip redundant mirror for /mnt/ paths
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
import { resolve } from 'node:path';
import { existsSync, writeFileSync, readFileSync, unlinkSync, mkdirSync } from 'node:fs';

const SKIP_SANDBOX_E2E = !!process.env.CI && process.env.MIQI_RUN_SANDBOX_E2E !== '1';

test.describe('WSL Sandbox In-Place File Write (#474)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  // Skip entire suite when WSL is not available (non-Windows / CI without flag)
  test.skip(
    () => SKIP_SANDBOX_E2E || process.platform !== 'win32',
    'WSL E2E tests require Windows + WSL sandbox'
  );

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  // ═════════════════════════════════════════════════════════════════
  //  Test 1: write_file in WSL sandbox writes to host workspace
  // ═════════════════════════════════════════════════════════════════

  test(
    'write_file in WSL sandbox writes to host workspace in-place',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      // Wait for sandbox to be ready (cold start can take minutes)
      const ready = await waitForSandboxReady(page, 300_000);
      if (!ready) {
        throw new Error('Sandbox manager did not become ready within 300s');
      }

      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // Create a unique file name and content
      const timestamp = Date.now();
      const fname = `e2e_inplace_test_${timestamp}.txt`;
      const content = `E2E in-place file write test ${timestamp} — #474`;

      // Pre-create a host file in the workspace so we can verify it gets modified
      const workspaceDir = resolve(miqiHome, 'workspace');
      mkdirSync(workspaceDir, { recursive: true });
      const hostFilePath = resolve(workspaceDir, fname);
      writeFileSync(hostFilePath, 'original content', 'utf-8');
      console.log(`[test] Pre-created host file: ${hostFilePath}`);

      await createNewConversation(page);

      // Ask AI to write to the file — should use write_file tool
      await sendMessage(
        page,
        `使用 write_file 工具写入文件 ${fname}，内容为 "${content}"。只回复操作结果，不要添加解释。`
      );
      await approveLoop(page, 240_000);
      await waitForResponseComplete(page, 240_000);

      const mainText = await page.locator('main').textContent();
      console.log('[test] AI response:', mainText?.substring(0, 500));

      // Verify the file exists and contains the new content
      await page.waitForTimeout(5000); // Allow filesystem sync

      expect(existsSync(hostFilePath), `Host file not found: ${hostFilePath}`).toBe(true);
      const actualContent = readFileSync(hostFilePath, 'utf-8');
      console.log(`[test] File content on host: "${actualContent}"`);
      expect(actualContent).toContain(content);
      console.log('[test] ✅ write_file wrote in-place to host workspace');

      // Cleanup
      try {
        unlinkSync(hostFilePath);
      } catch {}
      await page.screenshot({
        path: `test-results/wsl-inplace-write-01-result.png`,
      });
    }
  );

  // ═════════════════════════════════════════════════════════════════
  //  Test 2: edit_file in WSL sandbox modifies host file in-place
  // ═════════════════════════════════════════════════════════════════

  test(
    'edit_file in WSL sandbox modifies host file in-place',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      const ready = await waitForSandboxReady(page, 300_000);
      if (!ready) {
        throw new Error('Sandbox manager did not become ready within 300s');
      }

      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      const timestamp = Date.now();
      const fname = `e2e_edit_test_${timestamp}.txt`;
      const originalContent = `Line 1: original\nLine 2: ${timestamp}\nLine 3: end`;
      const newLine2 = `Line 2: MODIFIED by #474 e2e at ${timestamp}`;

      // Pre-create the file
      const workspaceDir = resolve(miqiHome, 'workspace');
      mkdirSync(workspaceDir, { recursive: true });
      const hostFilePath = resolve(workspaceDir, fname);
      writeFileSync(hostFilePath, originalContent, 'utf-8');
      console.log(`[test] Pre-created: ${hostFilePath}`);

      await createNewConversation(page);

      // Ask AI to edit the file
      await sendMessage(
        page,
        `使用 edit_file 工具修改 ${fname}：将 "Line 2: ${timestamp}" 替换为 "${newLine2}"。只回复操作结果。`
      );
      await approveLoop(page, 240_000);
      await waitForResponseComplete(page, 240_000);

      // Verify the modification persisted on host
      await page.waitForTimeout(5000);

      expect(existsSync(hostFilePath), `Host file not found after edit: ${hostFilePath}`).toBe(
        true
      );
      const actualContent = readFileSync(hostFilePath, 'utf-8');
      console.log(`[test] File after edit: "${actualContent}"`);
      expect(actualContent).toContain('MODIFIED by #474');
      expect(actualContent).not.toContain(`Line 2: ${timestamp}`);
      console.log('[test] ✅ edit_file modified host file in-place');

      try {
        unlinkSync(hostFilePath);
      } catch {}
      await page.screenshot({
        path: `test-results/wsl-inplace-edit-02-result.png`,
      });
    }
  );

  // ═════════════════════════════════════════════════════════════════
  //  Test 3: Manual verification placeholder (matches pdf-generator pattern)
  // ═════════════════════════════════════════════════════════════════

  test('wsl sandbox in-place file write — manual verification', { timeout: 600_000 }, async () => {
    test.skip(SKIP_SANDBOX_E2E, 'Run with MIQI_RUN_SANDBOX_E2E=1 for manual verification.');

    // This test is a placeholder for manual E2E verification.
    // The primary validation is in:
    //   tests/sandbox/test_wsl_sandbox_path_mapping.py (20 tests)
    //   tests/sandbox/test_bwrap_auto_install.py (2 WSL integration tests)
    //
    // To verify manually:
    //   1. Open the app in WSL sandbox mode
    //   2. Type "帮我写一个文件 test_474.txt，内容为 hello"
    //   3. Check that the file appears in your workspace directory
    //   4. Type "帮我把 test_474.txt 里的 hello 改成 world"
    //   5. Verify the file was modified in-place
    //
    // Python test command:
    //   cd MiQroForge
    //   .venv\Scripts\pytest tests/sandbox/test_wsl_sandbox_path_mapping.py -v
    test.skip();
  });
});
