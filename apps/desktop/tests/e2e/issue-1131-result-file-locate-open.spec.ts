/**
 * Issue #1131 — 文档工具交付物在「任务资产 → 结果文件」里的
 * 「定位」/「系统应用打开」必须真的能用。
 *
 * 复现的是 issue 里的真实链路：`create_pdf` 产出 PDF → `declare_result_files`
 * 用**工作区基准相对形式**（`sessions/<key>/files/<name>`）声明它 —— 也就是
 * 真实 agent 当时发的形态。修复前该路径会在会话 files 根下再叠一层前缀，
 * 台账因此多出一条重复前缀条目（与既有裸名条目 canonical 形态不同，去重匹配
 * 不上），面板展示它 → 「定位」报 `File not found: …\sessions\…\files\sessions\…`。
 *
 * 修完前缀重复还不够：文档工具相对**会话 files 根**记账，台账里存的是裸文件名，
 * 而 `sessions.workspace` 对非文件夹绑定的默认工作区会话返回 null，主进程于是把
 * 裸名锚到全局工作区根 → 「定位」照样 File not found（这次报的是
 * `<workspace>\<裸名>`）。渲染层因此要为「定位」补会话相对候选，与「预览」
 * 「下载」既有的候选回退一致。
 *
 * 「系统应用打开」是另一处：PDF 预览分支只存 `pdfUrl` 不存字节 → 按钮跳过
 * openBytes，退回 openExternal(裸名) 而找不到文件。
 *
 * 断言（都在修复前为红）：
 *   ① 台账：pdf 只有一条有效条目且 result=true，且不存在重复前缀条目
 *   ② 面板读端（getTrackedFiles，与面板同源）能看到该产物
 *   ③ 点「预览」→ PDF iframe 渲染出来（真的是 PDF 视图，不再回退成纯文本）
 *   ④ 点「系统应用打开」→ 出现新的 `miqi-open-*.pdf` 临时文件且内容以 %PDF 开头
 *      —— 证明走了 openBytes（把字节交给系统）。修复前 dataBase64 缺失，
 *      openBytes 根本不会被调用，因此**不会有**该临时文件。
 *      不直接断言「没有错误提示」：无头 CI 上 shell.openPath 失败会弹提示，
 *      那是环境差异不是本 issue 的缺陷。
 *
 * ⑤ 定位用**真实点击**触发（issue 报的就是这个按钮）。它会真的打开文件管理器，
 * 这是该功能正常的预期表现；失败时渲染层弹可见提示，故断言「无错误提示」。
 * 候选回退未加时该断言会红（实测：`定位失败：File not found: <workspace>\<裸名>`）。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron --workers=1 issue-1131-result-file-locate-open.spec.ts
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  ensurePersistedSession,
  stopMockServer,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

// ─── Mock ─────────────────────────────────────────────────────────────

async function startCreatePdfMock(): Promise<{ proc: ChildProcess; url: string }> {
  const python =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
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

// ─── Helpers ──────────────────────────────────────────────────────────

/** Mirror of `session_files_dir_key` — see issue-1104 spec for the rationale. */
function sessionFilesDirKey(sessionKey: string): string {
  const parts = sessionKey.split(':');
  const kept = parts.length >= 3 ? parts.slice(1) : parts;
  return kept
    .join('_')
    .replace(/[<>:"/\\|?*]/g, '_')
    .trim();
}

function findSessionDirWithFile(sessionsDir: string, filename: string): string | null {
  if (!existsSync(sessionsDir)) return null;
  for (const entry of readdirSync(sessionsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const sessionDir = join(sessionsDir, entry.name);
    if (existsSync(join(sessionDir, 'files', filename))) return sessionDir;
  }
  return null;
}

/** `miqi-open-*` temp files written by the main process on openBytes. */
function openBytesTempFiles(): string[] {
  const dir = tmpdir();
  if (!existsSync(dir)) return [];
  return readdirSync(dir).filter((f) => f.startsWith('miqi-open-'));
}

// ─── Suite ────────────────────────────────────────────────────────────

test.describe('Issue #1131 — 结果文件的「定位」「系统应用打开」', () => {
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
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = m.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    miqiSessionsDir = fixture.miqiSessionsDir;
    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
      console.log('[test] #1131 afterAll: app closed');
    } finally {
      // Always stop the mock, even when the close above throws: a child left
      // running keeps this Playwright worker's event loop alive, and the
      // resulting `worker-N process did not exit` force-kill fails the whole
      // job even when every test passed.
      await stopMockServer(mock, 'create_pdf mock');
      console.log('[test] #1131 afterAll: done');
    }
  });

  test(
    'create_pdf 交付物：台账无重复前缀、可预览、可定位、可用系统应用打开',
    { timeout: LLM_TIMEOUT },
    async () => {
      // ≤30 chars: TrackedFileCard truncates longer names in the DOM.
      const filename = `p1131_${Date.now()}.pdf`;

      // The declared path needs the real session key, so make sure a session
      // exists (the helper seeds one if the fresh app has none) before sending.
      const sessionKey = await ensurePersistedSession(page);
      const declaredAs = `sessions/${sessionFilesDirKey(sessionKey)}/files/${filename}`;
      console.log(`[test] session key = ${sessionKey}`);

      // Each tool call needs an approval round-trip (~11s each locally);
      // without a standing grant the turn strands mid-way. See e2e-test-workflow.
      await page.evaluate(async () => {
        await (window as any).miqi.approvals.addPermanent('*:*');
      });

      // One turn, exactly like the issue's repro: create the PDF, then declare
      // it with the workspace-base-relative form the real agent emitted.
      await sendMessage(
        page,
        `Please use create_pdf to create a PDF named ${filename}. ` +
          `Then declare it as a result file. DECLARE_AS=${declaredAs}`
      );

      await expect(
        page.locator('main').getByText(`created ${filename}`, { exact: false }).first()
      ).toBeVisible({ timeout: LLM_TIMEOUT });
      await waitForResponseComplete(page, LLM_TIMEOUT);

      // ── ① 台账：pdf 只有一条有效条目、result=true，且无重复前缀条目
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

      const tracked = JSON.parse(readFileSync(join(sessionDir!, 'tracked_files.json'), 'utf-8'));
      const keys: string[] = Object.keys(tracked.files ?? {});
      console.log(`[test] ledger keys = ${JSON.stringify(keys)}`);

      const doubled = keys.filter((k) =>
        /sessions\/[^/]+\/files\/sessions\/[^/]+\/files\//.test(k.replace(/\\/g, '/'))
      );
      expect(doubled, `重复前缀台账条目：${JSON.stringify(doubled)}`).toEqual([]);

      const pdfEntry = keys.find((k) => {
        const norm = k.replace(/\\/g, '/');
        return norm === filename || norm.endsWith(`/${filename}`);
      });
      expect(pdfEntry, `pdf entry missing: ${JSON.stringify(keys)}`).toBeDefined();
      expect(tracked.files[pdfEntry!].result).toBe(true);
      console.log('[test] ✅ ① 台账只有正确路径的 pdf 条目且 result=true');

      // ── ② 面板读端（sessions.getTrackedFiles，与面板同源）能看到该产物。
      //    它给出的 path 是**裸文件名**：文档工具相对会话 files 根记账。
      //    注意裸名在 bridge 侧解析不到（`sessions.workspace` 对默认工作区会话
      //    返回 null → 锚到全局工作区根），所以「预览」靠候选路径绕过、「定位」
      //    需要渲染层的候选回退 —— 后者由 ⑤ 真实点击验证。
      const tf: any = await page.evaluate(
        (k) => (window as any).miqi.sessions.getTrackedFiles(k),
        sessionKey
      );
      const tfList: any[] = tf?.tracked_files ?? [];
      const panelEntry = tfList.find(
        (f) =>
          String(f?.path ?? '')
            .replace(/\\/g, '/')
            .endsWith(`/${filename}`) || f?.name === filename
      );
      expect(panelEntry, `getTrackedFiles 未返回该产物：${JSON.stringify(tfList)}`).toBeDefined();
      const panelPath = String(panelEntry!.path ?? panelEntry!.name ?? '');
      console.log(`[test] ✅ ② 面板读端拿到产物（path=${panelPath}）`);

      // ── 面板结果区出现该卡片
      const panel = page.getByTestId('task-assets-panel');
      const card = panel.locator('.rounded-lg.p-2\\.5', { hasText: filename }).last();
      await expect(card).toBeVisible({ timeout: 60_000 });

      // ── ③ 预览：PDF iframe 渲染出来（回归保护）
      await card.getByRole('button', { name: '预览' }).click();
      const pdfFrame = page.locator('iframe[src^="blob:"]').last();
      await expect(pdfFrame).toBeVisible({ timeout: 20_000 });
      // Let Chromium's built-in PDF viewer paint before capturing evidence —
      // a screenshot taken the instant the iframe mounts shows a blank frame.
      await page.waitForTimeout(3000);
      await page.screenshot({
        path: `test-results/issue-1131-preview-${filename}.png`,
      });
      console.log('[test] ✅ ③ 「预览」渲染出 PDF iframe');

      // ── ④ 系统应用打开：必须走 openBytes（主进程写 miqi-open-*.pdf 临时文件）
      const before = new Set(openBytesTempFiles());
      await page.getByRole('button', { name: '系统应用打开' }).click();
      await expect
        .poll(() => openBytesTempFiles().filter((f) => !before.has(f)).length, {
          timeout: 20_000,
        })
        .toBeGreaterThan(0);
      const created = openBytesTempFiles().filter((f) => !before.has(f));
      const bytes = readFileSync(join(tmpdir(), created[0]));
      console.log(`[test] openBytes temp file = ${created[0]} (${bytes.length} bytes)`);
      expect(bytes.length).toBeGreaterThan(0);
      expect(bytes.subarray(0, 4).toString('latin1')).toBe('%PDF');
      console.log('[test] ✅ ④ 「系统应用打开」把 PDF 字节交给了系统（openBytes）');

      // Close the preview modal before clicking 定位 (the overlay covers the panel).
      await page.keyboard.press('Escape');
      await page.waitForTimeout(500);

      // ── ⑤ 定位：真实点击。成功时打开文件管理器；失败时渲染层弹可见提示。
      await expect(page.getByTestId('asset-error-toast')).toHaveCount(0);
      await card.getByRole('button', { name: '定位' }).click();
      // The toast auto-clears after 4s (notifyAssetError), so poll instead of
      // sampling once — a single sample can miss a toast that already expired.
      let toastText = '';
      for (let i = 0; i < 20; i++) {
        if ((await page.getByTestId('asset-error-toast').count()) > 0) {
          toastText = (await page.getByTestId('asset-error-toast').first().textContent()) ?? '';
          break;
        }
        await page.waitForTimeout(300);
      }
      expect(toastText, `「定位」失败并弹出了提示：${toastText}`).toBe('');
      console.log('[test] ✅ ⑤ 「定位」未报错（修复前会弹 定位失败：File not found）');

      await page.screenshot({
        path: `test-results/issue-1131-${filename}.png`,
        fullPage: true,
      });
    }
  );
});
