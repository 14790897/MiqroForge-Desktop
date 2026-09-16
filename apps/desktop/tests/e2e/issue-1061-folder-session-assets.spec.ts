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

/** Every key in every tracked_files.json under `sessionsRoot`, prefixed with the
 *  session dir it came from. Only used to make assertion failures say WHERE an
 *  entry landed, instead of just "not here". */
function trackedLedgerKeys(sessionsRoot: string): string[] {
  if (!existsSync(sessionsRoot)) return [];
  const out: string[] = [];
  for (const entry of readdirSync(sessionsRoot)) {
    const ledger = join(sessionsRoot, entry, 'tracked_files.json');
    if (!existsSync(ledger)) continue;
    try {
      const files = JSON.parse(readFileSync(ledger, 'utf-8'))?.files ?? {};
      out.push(...Object.keys(files).map((k) => `${entry}:${k}`));
    } catch {
      out.push(`${entry}:<unparseable>`);
    }
  }
  return out;
}

/** Move a session dir OUT of the sessions root so the app sees no stub for it. *
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

// ── #1096: exec 产物与文档产物必须写进同一份账本 ───────────────────────────
//
// 用户报的场景：让 agent 写两个文件，再「合成一个」第三个，第三个在「任务资产」
// 里根本不出现，而它在磁盘上确实存在。
//
// 根因是两个写入口分叉：文档工具按会话工作区（绑定根）落账、key 是相对路径；
// exec 产物追踪写死 `SessionManager(_get_workspace_path())`（app-home），key
// 是绝对路径。读端 `_find_ledger_root` 的规则是「哪份账本已有条目哪份说了算」，
// 绑定根那份先被文档工具占了，app-home 那份永远读不到 —— 合并产物就此消失。
//
// 这条测试走真实用户路径复现：先 write_file 两个输入（文档工具侧），再用 exec
// 合成第三个。断言三个都在账本里、合成产物在**绑定根那份**账本里、且**不在**
// app-home 那份里。修复前最后一条为真（bug 就在那），倒数第二条为假。

test.describe('#1096 exec 产物与文档产物同账本', () => {
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

  test('「两个输入 + exec 合成第三个」后，合成产物在绑定根账本且面板可见', async () => {
    test.setTimeout(LLM_TIMEOUT + 300_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const folderRoot = mkdtempSync(join(tmpdir(), 'miqi-1096-'));
    const folderMarker = 'miqi-1096';
    const stamp = String(Date.now()).slice(-6);
    const in1 = `in1_${stamp}.txt`;
    const in2 = `in2_${stamp}.txt`;
    // 放在绑定根的子目录里：账本存的是相对 key（`sub/m…html`），工具消息报的是
    // 绝对路径，两边都带目录 —— 只有按路径段后缀折叠才认得出是同一个文件，
    // 旧的「有一边是裸文件名就按 basename 合并」在这里兜不住。用户那台机器上
    // 踩的正是这个形态（`冷笑话/冷笑话合集.pdf`）。
    const merged = `sub/m${stamp}.html`;
    const mergedName = `m${stamp}.html`;

    try {
      await seedWorkspace(page, folderRoot);
      await createFolderSessionViaPicker(page, folderMarker);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*'));

      // 文档工具侧：两个输入。
      await sendMessage(
        page,
        `Use write_file to create ${in1} with content "one" and ${in2} with content "two"`
      );
      await waitForResponseComplete(page, LLM_TIMEOUT);
      // exec 侧：合成第三个。措辞上明确排除 write_file，否则测不到 exec 追踪。
      await sendMessage(
        page,
        `只准使用 exec 工具：执行一条 shell 命令，把当前目录下的 ${in1} 和 ${in2} ` +
          `合并写入 ${merged}（sub 子目录不存在就先创建）。不要使用 write_file。`
      );
      await waitForResponseComplete(page, LLM_TIMEOUT);

      const key = await resolveFolderSessionKey(page, folderMarker);

      // 前提：合成产物确实落在绑定根，否则下面的断言可能因为别的原因通过。
      expect(
        existsSync(join(folderRoot, merged)),
        `exec must actually create ${join(folderRoot, merged)}`
      ).toBe(true);

      const folderSessions = join(folderRoot, 'sessions');
      // 诊断：失败时把两份账本的 key 全带出来。踩过一次坑——只看
      // getTrackedFiles 的返回，分不清产物是「根本没被记」还是「记到了另一份
      // 账本」，而这两者的修法完全不同。
      const diag =
        `[folder=${JSON.stringify(trackedLedgerKeys(folderSessions))} ` +
        `home=${JSON.stringify(trackedLedgerKeys(fixture.miqiSessionsDir))}]`;
      // 三个条目都在账本里（读取侧 #1061 已保证读得到绑定根那份）。
      const tracked = await page.evaluate(
        async (k) => await (window as any).miqi.sessions.getTrackedFiles(k),
        key
      );
      const paths: string[] = (tracked?.tracked_files ?? []).map((f: any) => String(f?.path ?? ''));
      for (const name of [in1, in2, merged]) {
        expect(
          paths.some((p) => p.includes(name)),
          `${name} must be in the bound-folder ledger ${diag}, got: ${JSON.stringify(paths)}`
        ).toBe(true);
      }

      // #1096 的核心：exec 产物写在绑定根那份账本里，key 是绑定根相对路径。
      expect(
        trackedFilesMention(folderSessions, mergedName),
        'exec output must be persisted under the BOUND FOLDER ledger'
      ).toBe(true);
      // 修复前它写在这里（绝对路径），读端永远读不到 —— 让这条测试真的在测这个 bug。
      expect(
        trackedFilesMention(fixture.miqiSessionsDir, mergedName),
        'exec output must NOT be written to the app-home ledger'
      ).toBe(false);

      // 用户实际看到的症状：合成产物在「结果文件」里出现，且只出现一次。
      // 范围必须限定在结果文件区块内：面板底部的「修改建议」区块（#607）会把
      // write/edit 的文件再列一遍，它按设计就会让同一个名字在面板里出现两次，
      // 不能算作重复条目。
      const results = page.getByTestId('asset-section-result');
      const row = results.getByText(mergedName, { exact: false });
      await expect(row.first()).toBeVisible({ timeout: 60_000 });
      expect(
        await row.count(),
        `结果文件 must list ${mergedName} exactly once (no relative/absolute duplicate)`
      ).toBe(1);

      await page.screenshot({
        path: 'test-results/issue-1096-merged-asset.png',
        fullPage: true,
      });
      await postScreenshotToPr(
        'test-results/issue-1096-merged-asset.png',
        '✅ E2E 通过：exec 合成产物与文档产物同账本，面板不丢条也不重复'
      );
    } finally {
      await closeElectronApp(electronApp, miqiHome);
      rmSync(folderRoot, { recursive: true, force: true });
    }
  });
});
