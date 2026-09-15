/**
 * #1061 — folder-bound session: Task Assets + stub-less reads.
 *
 * Reported bug: a session bound to a non-default folder stops being reachable
 * from the UI — it vanishes from the sidebar (「暂无任务」) and its Task Assets
 * panel comes back empty, even though the conversation and the tracked files
 * are intact under the bound folder.
 *
 * Root cause is one shape, two read paths that never learned about the folder:
 * the write side mirrors the conversation and tracked_files.json under the
 * BOUND FOLDER root, while the read side scanned the app-home root, where the
 * folder session has only an empty stub.
 *
 * #1040 already covers switch-back and restart (see
 * issue-956-folder-session.spec.ts).  The two gaps left open, covered here:
 *
 *   1. Task Assets (#1061 proper).  sessions.get_tracked_files /
 *      clear_tracked_files still resolved the app-home root, so a folder
 *      session's 结果文件 panel was empty even though the entry was on disk
 *      under the folder.  #1040 fixed list/get/delete/archive but missed
 *      tracked files.
 *   2. Stub-less reads.  A session born inside a folder window may have no
 *      app-home stub at all.  With its runtime live we can read that runtime's
 *      own workspace root; without that seed the binding scan has nothing to
 *      go on and a bare sessions.get returns an empty conversation.
 *
 * Flow (real user path): seed a workspace binding so the picker's 最近使用
 * offers the folder → open the picker from the inline 更换 button → chat with
 * a real LLM.
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
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

// ── Helpers ────────────────────────────────────────────────────────────────

/** Register a binding the same way the frontend persists a workspace pick:
 *  sessions.get(key, { workspace }) writes the app-home stub + workspace
 *  metadata, which is what makes the folder show up in the picker's list. */
async function seedWorkspace(page: Page, folderRoot: string): Promise<string> {
  await waitForBridgeInitialized(page);
  const seedKey = `e2e-1061-seed-${Date.now()}`;
  await page.evaluate(
    async ({ key, ws }) => {
      return await (window as any).miqi.sessions.get(key, { workspace: ws });
    },
    { key: seedKey, ws: folderRoot }
  );
  return seedKey;
}

/** Create a folder-bound session through the real UI: inline 更换 →
 *  workspace picker → click the folder in 最近使用. */
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

/** Resolve the folder session's key from sessions.list().
 *
 *  Call this AFTER the first turn completes: the entry only carries a
 *  `workspace` field once the folder copy holds real messages. */
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

/** Session dir name under a sessions root — mirrors safe_filename() in
 *  miqi/utils/helpers.py, which get_session_dir() derives from the key. */
function sessionDirName(key: string): string {
  return key.replace(/[:<>"/\\|?*]/g, '_');
}

/** True when any tracked_files.json under `sessionsRoot` mentions `filename`.
 *  Shape-agnostic on purpose: it only cares that the entry landed there. */
function trackedFilesMention(sessionsRoot: string, filename: string): boolean {
  if (!existsSync(sessionsRoot)) return false;
  return readdirSync(sessionsRoot).some((entry) => {
    const ledger = join(sessionsRoot, entry, 'tracked_files.json');
    return existsSync(ledger) && readFileSync(ledger, 'utf-8').includes(filename);
  });
}

/** Move a session dir OUT of the sessions root so the app sees no stub for it.
 *
 *  The destination matters: list_sessions() enumerates every
 *  "<dir>/conversation.jsonl" one level under the root, so a sibling rename
 *  inside the root would still be picked up, list_bound_workspaces would hand
 *  the folder back, and the binding scan would resolve the conversation on its
 *  own — i.e. the read would succeed even with the fallback removed, and this
 *  test would silently stop measuring anything.
 *
 *  There is deliberately no restore step: the fixed read path backfills a fresh
 *  stub at the original path, so renaming back would collide with it (EPERM on
 *  Windows) — and closeElectronApp() tears the whole temp MIQI_HOME down. */
function parkSessionDir(sessionsRoot: string, key: string): void {
  const dir = join(sessionsRoot, sessionDirName(key));
  expect(existsSync(dir), `app-home stub must exist at ${dir} before parking`).toBe(true);
  renameSync(dir, join(dirname(sessionsRoot), `e2e-parked-${sessionDirName(key)}`));
}

// ── Tests ──────────────────────────────────────────────────────────────────

test.describe('#1061 folder-bound session assets', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  // Surface failures in the PR automatically (inert unless MIQI_E2E_POST_IMG=1).
  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test('folder session → Task Assets resolves the bound folder, not the app-home stub', async () => {
    test.setTimeout(LLM_TIMEOUT + 300_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const folderRoot = mkdtempSync(join(tmpdir(), 'miqi-1061a-'));
    const folderMarker = 'miqi-1061a';
    const filename = `q1061_${Date.now()}.txt`;

    try {
      await seedWorkspace(page, folderRoot);
      await createFolderSessionViaPicker(page, folderMarker);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*'));

      await sendMessage(page, `Use write_file to create ${filename} with content "hello 1061"`);
      await waitForResponseComplete(page, LLM_TIMEOUT);

      const key = await resolveFolderSessionKey(page, folderMarker);

      // Write side: the entry lands under the BOUND FOLDER (custom workspaces
      // are never stripped back to the default root by _tracked_store_root).
      const folderSessions = join(folderRoot, 'sessions');
      expect(
        trackedFilesMention(folderSessions, filename),
        'tracked_files.json must be written under the bound folder'
      ).toBe(true);

      // Read side (#1061): pre-fix this read the app-home stub and came back
      // empty, which is what emptied the 结果文件 panel.
      const tracked = await page.evaluate(
        async (k) => await (window as any).miqi.sessions.getTrackedFiles(k),
        key
      );
      const paths: string[] = (tracked?.tracked_files ?? []).map((f: any) => String(f?.path ?? ''));
      expect(
        paths.some((p) => p.includes(filename)),
        `getTrackedFiles must surface ${filename}, got: ${JSON.stringify(paths)}`
      ).toBe(true);

      // The entry must NOT be in the app-home copy — otherwise the assertion
      // above would pass for the wrong reason.
      expect(
        trackedFilesMention(fixture.miqiSessionsDir, filename),
        'the app-home copy must stay free of the folder session entry'
      ).toBe(false);

      // The user-visible symptom: 任务资产 no longer empty.
      await expect(
        page.getByTestId('task-assets-panel').getByText(filename, { exact: false }).first()
      ).toBeVisible({ timeout: 60_000 });

      await page.screenshot({
        path: 'test-results/issue-1061-task-assets.png',
        fullPage: true,
      });
      await postScreenshotToPr(
        'test-results/issue-1061-task-assets.png',
        '✅ E2E 通过：文件夹绑定会话的「任务资产」从工区根读出，不再为空'
      );
    } finally {
      await closeElectronApp(electronApp, miqiHome);
      rmSync(folderRoot, { recursive: true, force: true });
    }
  });

  test('live runtime with no app-home stub still returns the real conversation', async () => {
    test.setTimeout(LLM_TIMEOUT + 300_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const folderRoot = mkdtempSync(join(tmpdir(), 'miqi-1061b-'));
    const folderMarker = 'miqi-1061b';
    const question = 'Q1061B-请只回复两个字：收到';

    try {
      const seedKey = await seedWorkspace(page, folderRoot);
      await createFolderSessionViaPicker(page, folderMarker);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*'));

      await sendMessage(page, question);
      await waitForResponseComplete(page, LLM_TIMEOUT);

      const key = await resolveFolderSessionKey(page, folderMarker);

      // Baseline: the live runtime is what the fallback reads from.  If the app
      // tore the runtime down, the stub-less read below would have nothing to
      // fall back on and this test would not measure the fix.
      const before = await page.evaluate(
        async (k) => await (window as any).miqi.sessions.get(k),
        key
      );
      expect(before?.status, 'session must still be running for this scenario').toBe('running');
      expect(before?.messages?.length).toBeGreaterThan(0);

      // Drop EVERY app-home pointer to this folder, so only the live runtime
      // can still say where the conversation lives:
      //   - the seed stub also declares the folder, and list_bound_workspaces
      //     would hand the folder straight back to the (unfixed) binding scan;
      //   - the session's own stub is the binding the fallback must replace.
      rmSync(join(fixture.miqiSessionsDir, sessionDirName(seedKey)), {
        recursive: true,
        force: true,
      });
      parkSessionDir(fixture.miqiSessionsDir, key);
      // Pre-fix: bare get had no binding to follow and returned an empty
      // conversation, so switching back showed only the transient thinking row.
      const after = await page.evaluate(
        async (k) => await (window as any).miqi.sessions.get(k),
        key
      );
      const contents: string[] = (after?.messages ?? []).map((m: any) => String(m?.content ?? ''));
      expect(
        contents.length,
        `stub-less read must return the real conversation, got: ${JSON.stringify(contents)}`
      ).toBeGreaterThan(0);
      expect(contents.join('\n')).toContain('Q1061B');
      expect(String(after?.workspace)).toContain(folderMarker);

      await page.screenshot({
        path: 'test-results/issue-1061-stubless-read.png',
        fullPage: true,
      });
      await postScreenshotToPr(
        'test-results/issue-1061-stubless-read.png',
        '✅ E2E 通过：app-home 无 stub 时，活跃 runtime 的工作区仍能读回真对话'
      );
    } finally {
      await closeElectronApp(electronApp, miqiHome);
      rmSync(folderRoot, { recursive: true, force: true });
    }
  });
});
