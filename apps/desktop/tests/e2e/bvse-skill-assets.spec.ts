/**
 * #1104 — 真实 skill 产物完整性：exec 把 BVSE pipeline 写在用户目录（工作区外）
 * 时，「任务资产」面板必须列全产物，且 agent 声明的报告进「结果文件」。
 *
 * 背景（用户 2026-09-16 反馈）：「过程文件的显示也不全」——快照只覆盖 exec cwd，
 * 写到 ``--out-dir``（用户点名目录）的产物对面板不可见。修复后快照根 =
 * cwd + 用户点名目录（#821 ``_user_roots``）+ 命令里的 ``--out-dir``。
 *
 * 本 spec 用确定性 mock（fixtures/bvse_skill_mock.py）驱动一次**真实** pipeline
 * 运行（真计算、真产物，不是桩），再核对面板/台账完整性。
 *
 * 前置（本机/CI 无则 skip）：
 *   - BVSE_SKILL_DIR 默认 ~/.miqi/skills/bvse-mof-local-ssh（含 .venv 依赖）
 *   - BVSE_TEST_CIF 指向一个真实 MOF CIF（如 1499489..._freeONLY.cif）
 *
 * Run:
 *   cd apps/desktop && BVSE_TEST_CIF=<cif> npx playwright test \
 *     --config=playwright.config.ts --project=electron --workers=1 \
 *     bvse-skill-assets.spec.ts
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import {
  sendMessage,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  ensurePersistedSession,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

const SKILL_DIR =
  process.env.BVSE_SKILL_DIR ?? join(homedir(), '.miqi', 'skills', 'bvse-mof-local-ssh');
const SKILL_PY =
  process.platform === 'win32'
    ? join(SKILL_DIR, '.venv', 'Scripts', 'python.exe')
    : join(SKILL_DIR, '.venv', 'bin', 'python');
const TEST_CIF = process.env.BVSE_TEST_CIF ?? '';
/** 真实 pipeline 的允许时间（本机 freeONLY 结构 ≈15 min，留足余量） */
const PIPELINE_TIMEOUT = 30 * 60_000;

/** Recursive file listing (relative paths) under *root*. */
function listFiles(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string, prefix: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name);
      if (e.isDirectory()) walk(p, `${prefix}${e.name}/`);
      else out.push(`${prefix}${e.name}`);
    }
  };
  if (existsSync(root)) walk(root, '');
  return out;
}

async function startSkillMock(): Promise<{ proc: ChildProcess; url: string }> {
  const python =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(
    python,
    [join(APPS_DESKTOP, 'tests', 'e2e', 'fixtures', 'bvse_skill_mock.py'), String(port)],
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
      throw new Error(`bvse skill mock exited early: ${errTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!url) {
    proc.kill();
    throw new Error(`bvse skill mock startup line not seen in 30s: ${errTail}`);
  }
  return { proc, url };
}

test.describe('#1104 — 真实 skill 产物完整性（工作区外输出目录）', () => {
  // 本机 mock server 不可达（undici/loopback 限制）与缺依赖的环境直接跳过。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );
  test.skip(
    !existsSync(SKILL_PY) || !TEST_CIF || !existsSync(TEST_CIF),
    'BVSE skill venv 或 BVSE_TEST_CIF 缺失——本 spec 只在本机/具备依赖的环境运行'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mock: ChildProcess;

  test.beforeAll(async () => {
    const m = await startSkillMock();
    mock = m.proc;
    const fixture = await launchElectronApp((config: any) => {
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
    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mock?.kill();
  });

  test(
    'pipeline 写到用户目录 → 面板列全产物 + 报告进结果区',
    { timeout: PIPELINE_TIMEOUT + 300_000 },
    async () => {
      const outDir = join(miqiHome, 'user_output', 'bvse_run');
      const command = [
        `"${SKILL_PY}"`,
        `"${join(SKILL_DIR, 'scripts', 'pipeline.py')}"`,
        `"${TEST_CIF}"`,
        '--ion Na --executor direct --low-e-max 0.5 --skip-zeopp',
        `--out-dir "${outDir}"`,
      ].join(' ');

      await sendMessage(
        page,
        `Run the BVSE skill on this structure.\nCMD: ${command}\nDECLARE_GLOB: ${outDir}`
      );

      // ── 等真实 pipeline 完成（DONE.json 落盘是终态哨兵）
      const donePath = join(outDir, 'DONE.json');
      const deadline = Date.now() + PIPELINE_TIMEOUT;
      while (!existsSync(donePath) && Date.now() < deadline) {
        await page.waitForTimeout(3000);
      }
      expect(existsSync(donePath), `pipeline 未在 ${PIPELINE_TIMEOUT / 60000} 分钟内产出 DONE.json`)
        .toBe(true);
      // 让 mock 的声明轮与 UI 落定
      await expect(
        page.locator('main').getByText('declared report', { exact: false }).first()
      ).toBeVisible({ timeout: 120_000 });

      // ── 磁盘 ground truth
      const files = listFiles(outDir);
      expect(files.length, `产物文件数异常：${files.length}`).toBeGreaterThan(15);
      const report = files.find((f) => f.endsWith('_report.md'));
      expect(report, `未找到 *_report.md：${files.join(', ')}`).toBeTruthy();
      console.log(`[test] pipeline 产物 ${files.length} 个，报告=${report}`);

      // ── 台账完整性：目录里每个产物都要有条目（修复前只有零头）
      const sessionKey = await ensurePersistedSession(page);
      const tf: any = await page.evaluate(
        (k) => (window as any).miqi.sessions.getTrackedFiles(k),
        sessionKey
      );
      const trackedPaths: string[] = (tf?.tracked_files ?? []).map((f: any) =>
        String(f.path ?? '').replace(/\\/g, '/')
      );
      const missing = files.filter(
        (rel) => !trackedPaths.some((p) => p.endsWith(`/${rel}`) || p === rel)
      );
      expect(missing, `以下产物未进台账（显示不全）：${missing.join(', ')}`).toEqual([]);
      console.log(`[test] ✅ 台账完整：${files.length}/${files.length}`);

      // ── 面板：结果区含报告、过程区含其余产物
      const panel = page.getByTestId('task-assets-panel');
      await expect(panel).toBeVisible({ timeout: 15_000 });
      const resultSection = panel.getByTestId('asset-section-result');
      // 卡片对 >30 字符的文件名截断显示（TrackedFileCard），用前缀匹配
      const reportName = report!.split('/').pop()!;
      await expect(
        resultSection.getByText(reportName.slice(0, 20), { exact: false }).first()
      ).toBeVisible({ timeout: 20_000 });

      // 展开过程区并抽查关键产物（只在折叠时点开，盲点会反向关闭）
      const processSection = panel.getByTestId('asset-section-process');
      const firstProbe = processSection.getByText('summary.json', { exact: false }).first();
      if (!(await firstProbe.isVisible().catch(() => false))) {
        await panel.getByTestId('asset-section-toggle-process').click();
      }
      for (const probe of ['summary.json', '_bvse.cube']) {
        await expect(
          processSection.getByText(probe, { exact: false }).first()
        ).toBeVisible({ timeout: 20_000 });
      }

      // ── 批量目录折行：bvse_sites/ 20 个文件折成一行（默认收起，不铺开卡片）
      const groupRows = processSection.getByTestId('asset-dir-group');
      await expect
        .poll(async () => groupRows.count(), { timeout: 20_000 })
        .toBeGreaterThan(0);
      const sitesGroup = groupRows.filter({ hasText: 'bvse_sites/' });
      await expect(sitesGroup.first()).toBeVisible({ timeout: 10_000 });
      await expect(sitesGroup.first()).toContainText('个文件');
      // 收起状态下不渲染具体站点卡片
      await expect(processSection.getByText('Na_site01.cif', { exact: false })).toHaveCount(0);
      // 展开后可见
      await sitesGroup.first().click();
      await expect(
        processSection.getByText('Na_site01.cif', { exact: false }).first()
      ).toBeVisible({ timeout: 10_000 });

      // ── 声明的报告 result=true；其余条目无 result
      const reportEntry = (tf?.tracked_files ?? []).find((f: any) =>
        String(f.path ?? '').endsWith(report!)
      );
      expect(reportEntry?.result).toBe(true);
      const nonResult = (tf?.tracked_files ?? []).filter(
        (f: any) => !String(f.path ?? '').endsWith(report!)
      );
      expect(nonResult.every((f: any) => f.result !== true)).toBe(true);

      // 截图留给 PR：回到面板顶部、收起批量目录，展示「结果文件」区（真实报告）
      await sitesGroup.first().click().catch(() => {});
      await panel.evaluate((el) => {
        el.scrollTop = 0;
      });
      await page.waitForTimeout(400);
      await panel.screenshot({
        path: join(APPS_DESKTOP, 'test-results', 'issue-1104-bvse-panel.png'),
      });
      console.log('[test] ✅ 真实 skill 产物完整性 + 结果/过程分区通过');
    }
  );
});
