/**
 * #956 — folder-bound session persistence E2E.
 *
 * Repro of the reported bug: a session bound to a custom folder loses its
 * user/assistant messages after switching dialogs (only transient thinking
 * remains, send button stuck as "中断当前生成并发送"), and the session
 * vanishes from the sidebar entirely after a full app restart.
 *
 * Root cause (backend): the write side mirrors the conversation under the
 * bound folder root while sessions.get / sessions.list only read the
 * app-home root, where the folder session has just an empty stub.
 *
 * Flow (real user path):
 *   1. Seed a workspace binding via the bridge so the workspace picker's
 *      "最近使用" list offers the folder.
 *   2. Open the picker from the inline "更换" button and pick the folder —
 *      this creates the folder-bound session through the real
 *      pendingWorkspace → sessions.get(key, { workspace }) flow.
 *   3. Chat with a real LLM, then switch away/back (test 1) or fully
 *      restart the app on the same MIQI_HOME (test 2) and verify the
 *      history, the send button state, and no duplicate sidebar entry.
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdtempSync, existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  createNewConversation,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  relaunchElectronApp,
  switchToSessionWithMarker,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

const SIDEBAR = 'div.flex.flex-col.shrink-0.border-r';

/** Register a binding the same way the frontend persists a workspace pick —
 *  sessions.get(key, { workspace }) writes the app-home stub + metadata. */
async function seedWorkspace(page: Page, folderRoot: string) {
  await waitForBridgeInitialized(page);
  const seedKey = `e2e-956-seed-${Date.now()}`;
  await page.evaluate(
    async ({ key, ws }) => {
      return await (window as any).miqi.sessions.get(key, { workspace: ws });
    },
    { key: seedKey, ws: folderRoot }
  );
  return seedKey;
}

/** Create a folder-bound session through the real UI: inline "更换" →
 *  workspace picker → click the folder in "最近使用". */
async function createFolderSessionViaPicker(page: Page, folderMarker: string) {
  await page.getByTestId('inline-workspace-change-btn').click();
  await expect(page.getByTestId('workspace-picker-modal')).toBeVisible({
    timeout: 10_000,
  });
  const recentEntry = page
    .locator('[data-testid^="workspace-picker-recent-"]', { hasText: folderMarker })
    .first();
  await expect(recentEntry).toBeVisible({ timeout: 15_000 });
  await recentEntry.click();
  await waitForInputReady(page, 30_000);
  // The pill confirms the session resolved its workspace to the folder.
  await expect(page.getByTestId('inline-workspace-path')).toContainText(folderMarker, {
    timeout: 15_000,
  });
}

/** Assert the send button is in the normal "发送" state, not stuck generating. */
async function expectSendButtonNotStuck(page: Page) {
  await expect(page.getByLabel('发送')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByLabel('中断当前生成并发送')).toHaveCount(0);
  await expect(page.getByLabel('停止生成')).toHaveCount(0);
}

/** Assert the conversation is visible: question + reply, input sendable. */
async function expectConversationVisible(page: Page, marker: string, reply: string) {
  await expect(page.locator('main').getByText(marker, { exact: false }).first()).toBeVisible({
    timeout: 60_000,
  });
  await expect(page.locator('main').getByText(reply, { exact: false }).first()).toBeVisible({
    timeout: 60_000,
  });
  await expectSendButtonNotStuck(page);
}

test.describe('#956 folder-bound session', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test('switch-back keeps folder-session history and the input is sendable', async () => {
    test.setTimeout(LLM_TIMEOUT + 240_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const folderRoot = mkdtempSync(join(tmpdir(), 'miqi-956a-'));
    const folderMarker = 'miqi-956a';
    const question = 'Q956A-请只回复两个字：收到';

    try {
      await seedWorkspace(page, folderRoot);
      await createFolderSessionViaPicker(page, folderMarker);

      await sendMessage(page, question);
      await waitForResponseComplete(page, LLM_TIMEOUT);
      await expect(page.locator('main').getByText('收到', { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });

      // #956 invariant: the real conversation lives under the BOUND FOLDER,
      // while the app-home entry stays a binding-only stub.
      const folderSessionsDir = join(folderRoot, 'sessions');
      const convFiles: string[] = [];
      for (const entry of readdirSync(folderSessionsDir)) {
        const conv = join(folderSessionsDir, entry, 'conversation.jsonl');
        if (existsSync(conv)) convFiles.push(conv);
      }
      expect(convFiles.length).toBeGreaterThan(0);
      expect(readFileSync(convFiles[0], 'utf-8')).toContain('Q956A');

      // After the turn completes, the folder session must appear EXACTLY once
      // in the sidebar (the active registry + folder-scan must not duplicate it).
      await expect(
        page.locator(`${SIDEBAR} button.rounded-xl`, { hasText: 'Q956A' }).first()
      ).toBeVisible({ timeout: 60_000 });
      await expect(page.locator(`${SIDEBAR} button.rounded-xl`, { hasText: 'Q956A' })).toHaveCount(
        1
      );

      // Switch away and back — the reported bug lost the history here.
      await createNewConversation(page);
      const switched = await switchToSessionWithMarker(page, 'Q956A');
      expect(switched, 'folder session must be found in the sidebar').toBe(true);
      await expectConversationVisible(page, 'Q956A', '收到');

      await page.screenshot({
        path: 'test-results/issue-956-switch-back.png',
        fullPage: true,
      });
    } finally {
      await closeElectronApp(electronApp, miqiHome);
    }
  });

  test('folder session survives a full app restart with its history', async () => {
    test.setTimeout(LLM_TIMEOUT + 360_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const folderRoot = mkdtempSync(join(tmpdir(), 'miqi-956b-'));
    const folderMarker = 'miqi-956b';
    const question = 'Q956B-请只回复两个字：好的';

    try {
      await seedWorkspace(page, folderRoot);
      await createFolderSessionViaPicker(page, folderMarker);

      await sendMessage(page, question);
      await waitForResponseComplete(page, LLM_TIMEOUT);
      await expect(page.locator('main').getByText('好的', { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });

      // Full restart on the SAME MIQI_HOME.
      await closeElectronApp(electronApp, miqiHome, true);
      const relaunched = await relaunchElectronApp(miqiHome);
      electronApp = relaunched.electronApp;
      page = relaunched.page;

      // Pre-fix: the folder session vanished from the sidebar after restart.
      // Its title now comes from the folder copy's first user message.
      await expect(page.locator(SIDEBAR).getByText('Q956B', { exact: false }).first()).toBeVisible({
        timeout: 90_000,
      });
      await expect(page.locator(`${SIDEBAR} button.rounded-xl`, { hasText: 'Q956B' })).toHaveCount(
        1
      );

      const switched = await switchToSessionWithMarker(page, 'Q956B');
      expect(switched, 'folder session must be clickable after restart').toBe(true);
      await expectConversationVisible(page, 'Q956B', '好的');

      await page.screenshot({
        path: 'test-results/issue-956-restart.png',
        fullPage: true,
      });
    } finally {
      await closeElectronApp(electronApp, miqiHome);
    }
  });
});
