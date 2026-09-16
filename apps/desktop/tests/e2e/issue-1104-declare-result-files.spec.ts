/**
 * Issue #1104 — 「结果文件」显式声明机制 + 「修改建议」不再截断到 3 行。
 *
 * 背景：面板的「结果/过程」分区此前是纯前端扩展名白名单，skill 产出的
 * `*_report.md` 必然落「过程文件」（用户 2026-09-16 反馈：红框报告文件
 * ZECKID_Na_report.md 应在「结果文件」栏）。本 PR 新增内置工具
 * `declare_result_files` 让 agent 显式声明交付物，面板按台账 `result: true`
 * 归入结果区；同时「修改建议」移除 `.slice(0, 3)` 截断并给分组标签加计数。
 *
 * 本 spec 用确定性 mock（fixtures/declare_result_mock.py）驱动一次真实
 * write_file × 5 + declare_result_files 调用，验证：
 *   ① 面板统计 = 1 个结果 / 4 个过程
 *   ② 结果区含报告卡片；过程区含其余 4 个
 *   ③「修改建议」显示 5 行全量（结果文件 (1) / 过程文件 (4)）
 *   ④ 磁盘台账：报告条目 result=true，其余条目无 result
 *   ⑤ IPC 读端（sessions.getTrackedFiles）返回 result 标记
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron --workers=1 issue-1104-declare-result-files.spec.ts
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

const COMPANION_SUFFIXES = ['_script.py', '_data.json', '_trace.log', '_notes.md'];

/** Start the deterministic declare_result mock and wait for its bound URL. */
async function startDeclareResultMock(): Promise<{ proc: ChildProcess; url: string }> {
  const python =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(
    python,
    [join(APPS_DESKTOP, 'tests', 'e2e', 'fixtures', 'declare_result_mock.py'), String(port)],
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
      throw new Error(`declare_result mock exited early: ${errTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!url) {
    proc.kill();
    throw new Error(`declare_result mock startup line not seen in 30s: ${errTail}`);
  }
  return { proc, url };
}

/** Session directory whose files/ holds *filename* (mirrors issue-983 spec). */
function findSessionDirWithFile(sessionsDir: string, filename: string): string | null {
  if (!existsSync(sessionsDir)) return null;
  for (const entry of readdirSync(sessionsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const sessionDir = join(sessionsDir, entry.name);
    if (existsSync(join(sessionDir, 'files', filename))) return sessionDir;
  }
  return null;
}

test.describe('Issue #1104 — declare_result_files 显式声明结果文件', () => {
  // Same localhost restriction as issue-983: macOS CI cannot reach 127.0.0.1.
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
    const m = await startDeclareResultMock();
    mock = m.proc;
    const fixture = await launchElectronApp((config: any) => {
      // 零配置机器（本机 providers 为空、走 qraft 登录）也要能跑：显式注入一个
      // openai provider 指向 mock，并把默认模型指到它。
      config.providers = config.providers ?? {};
      config.providers.openai = { apiKey: 'mock-key', apiBase: m.url };
      config.agents = {
        ...(config.agents ?? {}),
        defaults: { ...(config.agents?.defaults ?? {}), model: 'openai/gpt-4o-mini' },
      };
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
    '声明的报告进「结果文件」，其余进「过程文件」，修改建议不截断',
    { timeout: LLM_TIMEOUT },
    async () => {
      const tag = `r1104_${Date.now()}`;
      const report = `${tag}_report.md`;
      const companions = COMPANION_SUFFIXES.map((s) => `${tag}${s}`);

      await sendMessage(page, `Write the five files ${tag} and declare the report.`);

      // Turn end: wait for the mock's final text (unique per turn).
      await expect(
        page.locator('main').getByText(`declared ${report}`, { exact: false }).first()
      ).toBeVisible({ timeout: LLM_TIMEOUT });
      await waitForResponseComplete(page, LLM_TIMEOUT);

      const panel = page.getByTestId('task-assets-panel');
      await expect(panel).toBeVisible({ timeout: 10_000 });

      // ── ① 面板统计：1 个结果 / 4 个过程
      await expect(page.getByTestId('task-assets-stats')).toHaveText(
        /1 个结果 \/ 4 个过程/,
        { timeout: 30_000 }
      );

      // ── ② 结果区 / 过程区各就各位
      const resultSection = panel.getByTestId('asset-section-result');
      await expect(resultSection.getByText(report, { exact: false }).first()).toBeVisible({
        timeout: 15_000,
      });
      const processSection = panel.getByTestId('asset-section-process');
      // 过程区默认折叠与否取决于是否存在结果文件——只在确实不可见时点开
      // （盲点会把它反向关掉）
      const firstCompanion = processSection
        .getByText(companions[0], { exact: false })
        .first();
      if (!(await firstCompanion.isVisible().catch(() => false))) {
        await panel.getByTestId('asset-section-toggle-process').click();
      }
      // 会话目录里的 4 个文件没有「产物祖先目录」→ 不折叠，逐张显示
      for (const name of companions) {
        await expect(processSection.getByText(name, { exact: false }).first()).toBeVisible({
          timeout: 15_000,
        });
      }

      // ── ③「修改建议」全量展示（无 slice(0,3) 截断）+ 分组计数
      const changes = page.getByTestId('task-assets-changes');
      await expect(changes.getByText('结果文件 (1)')).toBeVisible({ timeout: 15_000 });
      await expect(changes.getByText('过程文件 (4)')).toBeVisible({ timeout: 15_000 });
      for (const name of [report, ...companions]) {
        await expect(changes.getByText(name, { exact: false }).first()).toBeVisible({
          timeout: 15_000,
        });
      }

      // ── ④ 磁盘台账：报告 result=true，其余无 result
      let sessionDir: string | null = null;
      const deadline = Date.now() + 30_000;
      while (!sessionDir && Date.now() < deadline) {
        sessionDir = findSessionDirWithFile(miqiSessionsDir, report);
        if (!sessionDir) await page.waitForTimeout(500);
      }
      expect(sessionDir, `artifact ${report} never appeared under ${miqiSessionsDir}/*/files/`)
        .not.toBeNull();
      const tracked = JSON.parse(
        readFileSync(join(sessionDir!, 'tracked_files.json'), 'utf-8')
      );
      const entries = Object.entries<any>(tracked.files ?? {});
      const reportEntry = entries.find(([k]) => k === report || k.endsWith(`/${report}`));
      expect(reportEntry, `report entry missing: ${JSON.stringify(Object.keys(tracked.files))}`)
        .toBeDefined();
      expect(reportEntry![1].result).toBe(true);
      for (const name of companions) {
        const e = entries.find(([k]) => k === name || k.endsWith(`/${name}`));
        expect(e, `companion entry missing: ${name}`).toBeDefined();
        expect(e![1].result).toBeUndefined();
      }

      // ── ⑤ IPC 读端（面板同回路）返回 result 标记
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
      const tf: any = await page.evaluate(
        (k) => (window as any).miqi.sessions.getTrackedFiles(k),
        sessionKey
      );
      const tfList: any[] = tf?.tracked_files ?? [];
      const ipcReport = tfList.find((f) => (f.path ?? '').endsWith(report));
      expect(ipcReport, `report not in IPC tracked list: ${JSON.stringify(tfList)}`).toBeDefined();
      expect(ipcReport.result).toBe(true);

      // 截图留给 PR（面板「结果/过程」分区）
      await panel.screenshot({
        path: join(APPS_DESKTOP, 'test-results', 'issue-1104-declare-panel.png'),
      });
      console.log('[test] ✅ #1104 声明机制与面板分区全部通过');
    }
  );
});
