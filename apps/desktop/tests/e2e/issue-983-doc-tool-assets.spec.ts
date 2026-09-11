/**
 * Issue #983 — 文档工具（create_pdf）产物必须落进「本会话」的 tracked 存储，
 * 不能落成 <sessionDir>/files/sessions/<key>/tracked_files.json 这条孤儿路径。
 *
 * 背景：create_pdf/docx/pptx/xlsx 注册时的 workspace 是 <ws>/sessions/<key>/files，
 * 而 _persist_tracked_file 直接把它当存储根喂给 SessionManager → 条目写到
 * <files>/sessions/<key>/tracked_files.json，资产面板永远读不到。#1003 把存储根
 * 剥回 <ws>（_tracked_store_root）+ store key 与目录名派生同源（_session_files_dir_key）。
 *
 * 本 spec 用本地确定性 mock（fixtures/create_pdf_mock.py）驱动一次真实
 * create_pdf 工具调用，再分别从磁盘与 IPC 两条读端验证落点。
 *
 * 断言分层（判别性只认 ④-a/④-b/④-c）：
 *   ④-a canonical：<sessionDir>/tracked_files.json 存在且 files 键含该文件
 *   ④-b 孤儿：<sessionDir>/files 下递归找不到任何 tracked_files.json
 *   ④-c 面板读端同源：sessions.getTrackedFiles(真实 IPC) 返回该文件
 *   ①  smoke：资产面板出现卡片 —— ChatConsole 对 create_pdf 无条件 trackFile
 *      （_FILE_WRITE_TOOLS），未修复的 develop 上也会绿，故只作 smoke，不作证据。
 *
 * 前提：本 spec 仅在 session_workspace_enabled=true 下有效（关掉后产物落
 * <ws>/<file>、findSessionDirWithFile 返回 null → 假红）。本机 ~/.miqi/config.json
 * 为 true；CI 未设 → 默认 True（miqi/config/schema.py:237）。下方有显式 guard。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron --workers=1 issue-983-doc-tool-assets.spec.ts
 *      (--workers=1: one Electron app + one Python mock per run — no reason to
 *      contend for CPU with sibling specs, and it keeps the mock port single.)
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  ensurePersistedSession,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

// ─── Mock ─────────────────────────────────────────────────────────────

/** Start the deterministic create_pdf mock and wait for its bound URL. */
async function startCreatePdfMock(): Promise<{ proc: ChildProcess; url: string }> {
  // Windows venv 用 Scripts/python.exe，posix 用 bin/python（同 regression-delete-all-focus）。
  const python =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
  // 随机端口：避免 workers 并发碰撞（同 regression-delete-all-focus:47）。
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(
    python,
    [join(APPS_DESKTOP, 'tests', 'e2e', 'fixtures', 'create_pdf_mock.py'), String(port)],
    {
      cwd: REPO_ROOT,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      windowsHide: true,
    }
  );
  let url = '';
  let errTail = '';
  proc.stdout?.on('data', (d) => {
    const m = String(d).match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) url = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => (errTail = (errTail + String(d)).slice(-2000)));
  const deadline = Date.now() + 30_000;
  while (!url && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      proc.kill();
      throw new Error(`create_pdf mock exited early: ${errTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!url) {
    proc.kill();
    throw new Error(`create_pdf mock startup line not seen in 30s: ${errTail}`);
  }
  return { proc, url };
}

// ─── Disk helpers ─────────────────────────────────────────────────────

/**
 * First session directory under *sessionsDir* whose files/ holds *filename*.
 *
 * Returns the SESSION DIRECTORY (`<sessionsDir>/<key>`), NOT the file path —
 * ④-b joins 'files' onto it, and returning the file path would make that
 * join a nonexistent dir (always empty ⇒ ④-b green even on unfixed develop).
 * The sibling helper findFileInSessionDirs (delivery-path-truth.spec.ts)
 * returns a file path and must NOT be copied here.
 */
function findSessionDirWithFile(sessionsDir: string, filename: string): string | null {
  if (!existsSync(sessionsDir)) return null;
  for (const entry of readdirSync(sessionsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const sessionDir = join(sessionsDir, entry.name);
    if (existsSync(join(sessionDir, 'files', filename))) return sessionDir;
  }
  return null;
}

/** Every tracked_files.json under *root* (recursive) — the orphan hunt. */
function findTrackedFilesJson(root: string): string[] {
  const found: string[] = [];
  if (!existsSync(root)) return found;
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const p = join(root, entry.name);
    if (entry.isDirectory()) found.push(...findTrackedFilesJson(p));
    else if (entry.name === 'tracked_files.json') found.push(p);
  }
  return found;
}

// ─── Suite ────────────────────────────────────────────────────────────

test.describe('Issue #983 — doc-tool artifacts land in the session tracked store', () => {
  // Same localhost restriction as confirm-card.spec: the macOS CI runner's
  // undici fetch cannot reach a local 127.0.0.1 listener.
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let miqiSessionsDir: string;
  let mock: ChildProcess;

  test.beforeAll(async () => {
    const m = await startCreatePdfMock();
    mock = m.proc;
    const fixture = await launchElectronApp((config: any) => {
      // Point EVERY configured provider at the mock (provider resolution is
      // driven by agents.defaults.model) — the mock ignores model names/keys.
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = m.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
      // Deterministic native paths: no WSL sandbox in this spec.
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    miqiSessionsDir = fixture.miqiSessionsDir;
    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mock?.kill();
  });

  test(
    'create_pdf 产物落 canonical tracked 存储、无孤儿存储、面板读端同源',
    { timeout: LLM_TIMEOUT },
    async () => {
      // Precondition guard: with per-session workspace isolation OFF the
      // artifact lands at <ws>/<file> and every disk assertion below becomes
      // a false red. Skip loudly instead of blaming #983.
      const wsEnabled = await page.evaluate(async () => {
        const c: any = await (window as any).miqi.config.get();
        const sessions = c?.agents?.sessions ?? c?.agents?.defaults?.sessionConfig;
        return sessions?.sessionWorkspaceEnabled ?? sessions?.session_workspace_enabled ?? null;
      });
      test.skip(
        wsEnabled === false,
        'session_workspace_enabled=false — this spec only covers the session-workspace layout'
      );

      // ≤30 chars on purpose: TrackedFileCard.tsx:73 renders
      // `name.length > 30 ? slice(0,28)+'…' : name`, so a longer filename
      // never appears verbatim in the panel DOM and the smoke card
      // assertion below could never match.
      const filename = `p983_${Date.now()}.pdf`;
      // ASCII-only turn: the mock keys off the *.pdf filename alone, and CI
      // runners may lack a CJK font — nothing here may depend on CJK glyphs.
      const userText = `Please use create_pdf to create a PDF named ${filename}.`;

      await sendMessage(page, userText);

      // Session key from the live list. ensurePersistedSession() is the
      // documented accessor (#774: list[0] is not necessarily the CURRENT
      // session, empty sessions never persist). Wait for the user message to
      // persist FIRST so the helper cannot fall back to seeding a second
      // message — that seed would race this turn and flip the mock to text.
      await expect
        .poll(
          async () =>
            page.evaluate(async () => {
              const all = await (window as any).miqi.sessions.list();
              return (all?.sessions ?? []).length;
            }),
          { timeout: 60_000 }
        )
        .toBeGreaterThan(0);
      const sessionKey = await ensurePersistedSession(page);
      console.log(`[test] session key = ${sessionKey}`);

      // Turn end: the panel's backend refresh hangs off the turn-end handler.
      // Wait for the mock's final text (unique per turn) before the generic
      // settle, so a still-running reportlab call cannot look "stable".
      await expect(
        page.locator('main').getByText(`created ${filename}`, { exact: false }).first()
      ).toBeVisible({ timeout: LLM_TIMEOUT });
      await waitForResponseComplete(page, LLM_TIMEOUT);

      // ── ④-a canonical: session dir holds the artifact AND its tracked store.
      //    (develop: artifact yes, tracked_files.json is one level deeper → red)
      let sessionDir: string | null = null;
      const deadline = Date.now() + 60_000;
      while (!sessionDir && Date.now() < deadline) {
        sessionDir = findSessionDirWithFile(miqiSessionsDir, filename);
        if (!sessionDir) await page.waitForTimeout(500);
      }
      expect(
        sessionDir,
        `artifact ${filename} never appeared under ${miqiSessionsDir}/*/files/`
      ).not.toBeNull();
      console.log(`[test] session dir = ${sessionDir}`);

      const trackedPath = join(sessionDir!, 'tracked_files.json');
      expect(existsSync(trackedPath), `canonical tracked store missing: ${trackedPath}`).toBe(true);
      const tracked = JSON.parse(readFileSync(trackedPath, 'utf-8'));
      const trackedKeys = Object.keys(tracked.files ?? {});
      expect(
        trackedKeys.some((k) => k === filename || k.endsWith(`/${filename}`)),
        `canonical store keys = ${JSON.stringify(trackedKeys)}`
      ).toBe(true);
      console.log('[test] ✅ ④-a canonical tracked store contains the artifact');

      // ── ④-b no orphan store under the session's files/ (develop writes
      //    <files>/sessions/<key>/tracked_files.json → red).
      const orphans = findTrackedFilesJson(join(sessionDir!, 'files'));
      expect(orphans, `orphan tracked store(s): ${orphans.join(', ')}`).toEqual([]);
      console.log('[test] ✅ ④-b no orphan tracked store under <sessionDir>/files');

      // ── ④-c panel read path (real IPC → bridge → SessionManager.load_tracked_files
      //    with ownership check): the same store the panel reads.
      const tf: any = await page.evaluate(
        (k) => (window as any).miqi.sessions.getTrackedFiles(k),
        sessionKey
      );
      const tfList: any[] = tf?.tracked_files ?? [];
      expect(
        tfList.some(
          (f) =>
            f?.name === filename ||
            f?.path === filename ||
            String(f?.path ?? '').endsWith(`/${filename}`)
        ),
        `getTrackedFiles returned ${JSON.stringify(tfList)}`
      ).toBe(true);
      console.log('[test] ✅ ④-c sessions.getTrackedFiles sees the artifact');

      // ── ① smoke only (green on unfixed develop too — NOT #983 evidence).
      const card = page
        .getByTestId('task-assets-panel')
        .locator('.rounded-lg.p-2\\.5', { hasText: filename })
        .last();
      await expect(card).toBeVisible({ timeout: 60_000 });
      console.log('[test] ✅ ① smoke: asset panel card rendered');

      // Per e2e-test-workflow skill: every run ends with a visual proof.
      await page.screenshot({
        path: `test-results/issue-983-doc-tool-assets-${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });
    }
  );
});
