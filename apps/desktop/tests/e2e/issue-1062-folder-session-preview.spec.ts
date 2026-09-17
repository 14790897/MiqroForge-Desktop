/**
 * #1062（补完）— 文件夹绑定会话的「预览 / 定位」要真的能用。
 *
 * #1062 前一半只把「工作区外路径」的静默失败改成可见报错。对文件夹绑定会话
 * 来说这是治错了地方：产物就在用户选的目录里，它并不是「工作区之外」——只是
 * 路径解析只认全局工作区（`~/.miqi/workspace`），于是绑定目录下的东西一律被
 * 判成越界，预览读不到、定位报「工作区之外」。
 *
 * 本 spec 跑真实用户路径：绑一个目录 → 让 agent 在里面写一个文件 → 断言这个
 * 文件可预览、可定位。三条断言在修复前都是红的：
 *
 *   1. `files.read(名, key)` 返回内容（预览读路径）——修复前为 null。
 *   2. `openContainingFolder(<绑定目录下的绝对路径>, key)` 的失败原因不得是
 *      「工作区之外」。用一个**不存在的**文件来问，这样既锁住「绑定根被接受
 *      为允许根」，又不会真的弹出文件管理器。
 *   3. 面板里点「预览」真的弹出预览窗且内容可见——这是用户实际看到的症状。
 *
 * Flow mirrors issue-1061-folder-session-assets.spec.ts: seed a workspace binding
 * so the picker's 最近使用 offers the folder → open the picker from the inline
 * 更换 button → chat with a real LLM.
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { postScreenshotToPr } from './helpers/pr-image-post';

// ── Helpers (kept local: each spec stays self-contained) ───────────────────

/** Register a binding the same way the frontend persists a workspace pick. */
async function seedWorkspace(page: Page, folderRoot: string): Promise<string> {
  await waitForBridgeInitialized(page);
  const seedKey = `e2e-1062-seed-${Date.now()}`;
  await page.evaluate(
    async ({ key, ws }) => {
      return await (window as any).miqi.sessions.get(key, { workspace: ws });
    },
    { key: seedKey, ws: folderRoot }
  );
  return seedKey;
}

/** Create a folder-bound session through the real UI: inline 更换 → picker. */
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
  await expect(page.getByTestId('inline-workspace-path')).toContainText(folderMarker, {
    timeout: 15_000,
  });
}

/** Resolve the folder session's key once its folder copy holds real messages. */
async function resolveFolderSessionKey(page: Page, folderMarker: string): Promise<string> {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const key: string | null = await page.evaluate(async (marker) => {
      const { sessions } = await (window as any).miqi.sessions.list();
      const hit = (sessions as any[]).find(
        (s) => typeof s?.workspace === 'string' && s.workspace.includes(marker)
      );
      return hit?.key ?? null;
    }, folderMarker);
    if (key) return key;
    await page.waitForTimeout(1000);
  }
  throw new Error(`no session bound to ${folderMarker} ever appeared in sessions.list()`);
}

// ── Test ───────────────────────────────────────────────────────────────────

test.describe('#1062 folder-bound session 预览/定位', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test('绑定目录下的产物可预览、可定位', async () => {
    test.setTimeout(LLM_TIMEOUT + 300_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const folderRoot = mkdtempSync(join(tmpdir(), 'miqi-1062-'));
    const folderMarker = 'miqi-1062';
    const filename = `q1062_${Date.now()}.md`;

    try {
      await seedWorkspace(page, folderRoot);
      await createFolderSessionViaPicker(page, folderMarker);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*'));

      await sendMessage(page, `Use write_file to create ${filename} with content "hello 1062"`);
      await waitForResponseComplete(page, LLM_TIMEOUT);

      const key = await resolveFolderSessionKey(page, folderMarker);

      // 前提：文件确实落在绑定目录里。否则下面的断言可能因为别的原因通过。
      //
      // 这里曾经在 CI 上因「没产出」而 skip（绕开当时还没修的账本分叉 bug）。
      // 那个 bug 已随 #1104 合入，且本用例要的是 write_file、不是 exec —— 跳过
      // 会把「绑定、agent 执行、路径解析」任何一处的新回归一起吞掉，所以去掉：
      // 没产出就是失败（#1103 review）。
      expect(
        existsSync(join(folderRoot, filename)),
        `agent output must land in the bound folder: ${join(folderRoot, filename)}`
      ).toBe(true);

      // 1. 预览的读路径。修复前 _validate_file_path 只认全局工作区，
      //    绑定目录下的相对名解析不到 → 抛 NOT_FOUND → 渲染层拿到 null。
      const read = await page.evaluate(
        async ({ p, k }) => await (window as any).miqi.files.read(p, k),
        { p: filename, k: key }
      );
      expect(
        String(read?.content ?? ''),
        `preview must read the bound-folder file, got: ${JSON.stringify(read)}`
      ).toContain('hello 1062');

      // 2. 定位的包含性校验。问一个绑定目录下**不存在**的文件：修复前这里会因
      //    「工作区之外」被拒；修复后绑定根是允许根，只会报「文件不存在」。
      //    用不存在的路径是为了不真的弹出文件管理器。
      const revealProbe = await page.evaluate(
        async ({ p, k }) => await (window as any).miqi.files.openContainingFolder(p, k),
        { p: join(folderRoot, 'definitely-not-here.txt'), k: key }
      );
      expect(String(revealProbe?.error ?? '')).toMatch(/not found/i);
      expect(
        String(revealProbe?.error ?? ''),
        'bound-folder path must not be refused as outside the workspace'
      ).not.toMatch(/outside workspace/i);

      // 3. 用户实际看到的症状：面板里点「预览」要真的弹出预览窗并显示内容。
      //    （「定位」按钮只对结果文件渲染，其包含性校验由上一步的 IPC 探针覆盖。）
      const panel = page.getByTestId('task-assets-panel');
      // 精确到**我们这个文件**的卡片：面板里可能还有别的 tracked 文件，取
      // `.first()` 可能点到别人身上，「预览显示 hello 1062」就成了假断言。
      const card = panel.getByTestId('tracked-file-card').filter({ hasText: filename }).first();
      await expect(card).toBeVisible({ timeout: 60_000 });
      const previewBtn = card.getByTestId('file-preview-btn');
      await expect(previewBtn).toBeVisible({ timeout: 15_000 });
      await previewBtn.click();

      // 断言预览窗**真的打开并显示内容**。「没有 error toast」不够——按钮彻底
      // 坏掉、点了什么都不发生，同样满足那个条件（#1103 review）。
      const preview = page.getByTestId('file-preview-modal');
      await expect(preview).toBeVisible({ timeout: 15_000 });
      await expect(preview).toContainText('hello 1062', { timeout: 15_000 });
      await expect(
        page.getByTestId('asset-error-toast'),
        '预览 a folder-bound file must not surface an error'
      ).toHaveCount(0);

      await page.screenshot({
        path: 'test-results/issue-1062-folder-session-preview.png',
        fullPage: true,
      });
      await postScreenshotToPr(
        'test-results/issue-1062-folder-session-preview.png',
        '✅ E2E 通过：文件夹绑定会话的「预览 / 定位」在绑定目录下可用'
      );
    } finally {
      await closeElectronApp(electronApp, miqiHome);
      rmSync(folderRoot, { recursive: true, force: true });
    }
  });
});
