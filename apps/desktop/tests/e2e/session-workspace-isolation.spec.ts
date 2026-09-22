/**
 * E2E: Session workspace isolation for file writes.
 *
 * Regression coverage for the bug where write_file with an ABSOLUTE path
 * under the default workspace root (the directory the system prompt
 * advertises as the working directory) landed the file in the SHARED root
 * instead of the per-session workspace `<workspace>/sessions/<key>/files/`.
 *
 * Verifies:
 *   1. Agent writes with an absolute workspace-root path → file lands in
 *      sessions/<safe_key>/files/, NOT at the workspace root.
 *   2. The file still appears in the Task Assets panel.
 *   3. A second session starts with an empty Task Assets panel (isolation).
 *
 * The sandbox is disabled via patchConfig so the native (no-sandbox) path
 * is exercised deterministically — that path previously had NO session
 * isolation at all because the factory never wired the per-session dir.
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron session-workspace-isolation.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { join, resolve } from 'node:path';
import { existsSync, readdirSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  sendMessage,
  waitForResponseComplete,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
} from './helpers/electron-setup';

// ─── Helpers ──────────────────────────────────────────────────────────

/** Find the first session files dir containing *filename* under
 *  <miqiHome>/workspace/sessions/. Returns the full path or null. */
function findFileInSessionDirs(miqiHome: string, filename: string): string | null {
  const sessionsDir = join(miqiHome, 'workspace', 'sessions');
  if (!existsSync(sessionsDir)) return null;
  for (const entry of readdirSync(sessionsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const candidate = join(sessionsDir, entry.name, 'files', filename);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

async function waitForFileInPanel(page: Page, filename: string, timeout = 30_000) {
  const assetsPanel = page.getByTestId('task-assets-panel');
  const card = assetsPanel
    .locator('[class*="rounded"][class*="p-"]')
    .filter({ hasText: filename.slice(0, 20) })
    .first();
  try {
    await expect(card).toBeVisible({ timeout });
  } catch {
    const fallback = assetsPanel
      .locator('.rounded-lg.p-2\\.5')
      .filter({ hasText: filename.slice(0, 20) })
      .first();
    await expect(fallback).toBeVisible({ timeout });
    return fallback;
  }
  return card;
}

/** Dismiss stale Radix overlays that can swallow the first Enter (cold-start). */
async function dismissOverlays(page: Page) {
  await page.evaluate(() => {
    document.querySelectorAll('[data-radix-focus-guard]').forEach((e) => e.remove());
    document.querySelectorAll('[data-aria-hidden="true"]').forEach((e) => {
      if (e.classList.contains('fixed') && e.classList.contains('inset-0')) {
        (e as HTMLElement).style.display = 'none';
      }
    });
  });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
}

/** sendMessage with one retry — the first Enter of a cold app can race the
 *  optimistic-UI bubble mount (same class as workspace-selection flakes). */
async function sendMessageWithRetry(page: Page, text: string) {
  await dismissOverlays(page);
  for (let attempt = 0; ; attempt++) {
    try {
      await sendMessage(page, text);
      return;
    } catch {
      if (attempt >= 1) throw new Error('sendMessage failed after retries');
      console.log(`[test] send failed (attempt ${attempt + 1}) — retrying`);
      await page.waitForTimeout(1000);
      const textarea = page.locator('[data-testid="chat-input-container"] textarea');
      await textarea.fill('').catch(() => {});
      await dismissOverlays(page);
    }
  }
}

// ─── Suite ────────────────────────────────────────────────────────────

test.describe('Session Workspace Isolation E2E', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp((config) => {
      // Deterministic native path: no WSL sandbox in this spec.
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    await waitForBridgeInitialized(page);
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('absolute workspace-root write lands in the session workspace', async () => {
    test.setTimeout(LLM_TIMEOUT * 2);
    const marker = `E2E_WSISO_${Date.now()}`;
    const filename = `e2e_wsiso_${Date.now()}.md`;
    const workspaceRoot = resolve(join(miqiHome, 'workspace'));
    const absoluteTarget = join(workspaceRoot, filename);
    const content = `# ${marker}\n\nSession workspace isolation test.`;

    await createNewConversation(page);

    // Reproduce the original bug scenario: the system prompt advertises the
    // workspace ROOT as the working directory, so the model writes an
    // absolute root path.  The write must be redirected into the session's
    // isolated files dir.  Retry the send once — deepseek no-op turns (the
    // model replies without calling write_file) are a known flake class.
    const message =
      `必须调用 write_file 工具创建文件，path 参数必须是绝对路径 "${absoluteTarget}"，content="${content}"。` +
      `创建完成后只回复"完成"。`;
    let created = false;
    for (let attempt = 0; attempt < 2 && !created; attempt++) {
      await sendMessageWithRetry(page, message);
      await waitForResponseComplete(page, 240_000);
      created = findFileInSessionDirs(miqiHome, filename) !== null;
      if (!created) {
        console.log(`[test] ⚠️ write_file not executed (attempt ${attempt + 1}) — retrying send`);
      }
    }

    // The file must land in <workspace>/sessions/<key>/files/ (key derived
    // from the frontend session key "desktop:<ts>").
    await expect
      .poll(() => findFileInSessionDirs(miqiHome, filename), { timeout: 30_000 })
      .not.toBeNull();
    console.log(`[test] ✅ File "${filename}" found under sessions/*/files/`);

    // …and NOT at the shared workspace root.
    expect(
      existsSync(absoluteTarget),
      `file must not exist at workspace root: ${absoluteTarget}`
    ).toBe(false);
    console.log('[test] ✅ Workspace root is clean');

    // The Task Assets panel still shows the file (tracked_files bookkeeping
    // keeps pointing at the session-scoped location).
    await waitForFileInPanel(page, filename);
    console.log('[test] ✅ File appears in Task Assets panel');
  });

  test('a new session does not see the previous session files', async () => {
    test.setTimeout(LLM_TIMEOUT);

    // 直接切到新会话：其资产面板必须为空——不能泄漏上一条用例会话的
    // 追踪文件。隔离断言的对象就是会话 A 的文件，无需再走一轮 LLM 创建。
    await createNewConversation(page);
    await expect(page.locator('[data-testid="task-assets-empty"]')).toBeVisible({
      timeout: 15_000,
    });
    console.log('[test] ✅ New session shows empty Task Assets');
  });
});
