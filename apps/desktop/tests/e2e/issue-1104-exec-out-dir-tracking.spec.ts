/**
 * #1104 — exec 产物追踪覆盖工作区外的输出目录（快，不依赖真实 skill）。
 *
 * 用户反馈 2026-09-16：「过程文件的显示也不全」——skill 把产物写到
 * ``--out-dir`` 指向的用户目录时，面板只显示零头。根因两处：
 *   ① 快照只覆盖 exec cwd（#1104 修复：cwd + 用户点名目录 + 命令声明的 out-dir）；
 *   ② out-dir 运行时才创建，被 ``is_dir()`` 过滤掉 → 整棵产物树 diff 不到
 *      （#1104 修复：不存在的根用空快照兜底）。
 *
 * 本 spec 用确定性 mock 驱动一次真实 exec：命令写入工作区外的 ``user_out/``
 * （运行前不存在），断言台账 + 面板都能看到全部产物，且目录级聚合生效。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron --workers=1 issue-1104-exec-out-dir-tracking.spec.ts
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { existsSync, readdirSync } from 'node:fs';
import {
  sendMessage,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  ensurePersistedSession,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
const OUT_FILES = ['alpha.txt', 'beta.txt', 'gamma.txt', 'delta.txt', 'epsilon.txt'];

async function startMock(): Promise<{ proc: ChildProcess; url: string }> {
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
      throw new Error(`mock exited early: ${errTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!url) {
    proc.kill();
    throw new Error(`mock startup line not seen in 30s: ${errTail}`);
  }
  return { proc, url };
}

test.describe('#1104 — exec 追踪工作区外产物目录', () => {
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mock: ChildProcess;

  test.beforeAll(async () => {
    const m = await startMock();
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
    '流水线写到运行前不存在的 out-dir → 产物全部进台账与面板',
    { timeout: 300_000 },
    async () => {
      const outDir = join(miqiHome, 'user_out');
      expect(existsSync(outDir), 'out-dir 必须运行前不存在，才覆盖「空快照兜底」路径').toBe(false);

      const py = join(
        REPO_ROOT,
        '.venv',
        process.platform === 'win32' ? 'Scripts' : 'bin',
        process.platform === 'win32' ? 'python.exe' : 'python'
      );
      const files = OUT_FILES.map((n) => `'${n}'`).join(', ');
      const cmd =
        `"${py}" -c "import pathlib; d=pathlib.Path(r'${outDir}'); ` +
        `d.mkdir(parents=True, exist_ok=True); ` +
        `[(d / n).write_text('x') for n in [${files}]]" ` +
        `--out-dir "${outDir}"`;

      await sendMessage(page, `Run it.\nCMD: ${cmd}\nDECLARE_GLOB: ${outDir}`);
      await expect(
        page.locator('main').getByText('no report found', { exact: false }).first()
      ).toBeVisible({ timeout: 180_000 });

      // 磁盘现状
      expect(readdirSync(outDir).sort()).toEqual([...OUT_FILES].sort());

      // ── 台账（IPC 回路）：5 个产物全部在，且 op=write
      const sessionKey = await ensurePersistedSession(page);
      const tf: any = await page.evaluate(
        (k) => (window as any).miqi.sessions.getTrackedFiles(k),
        sessionKey
      );
      const tracked: any[] = tf?.tracked_files ?? [];
      for (const name of OUT_FILES) {
        const entry = tracked.find((f: any) => String(f.path ?? '').endsWith(`/${name}`));
        expect(
          entry,
          `未进台账：${name}（现有 ${JSON.stringify(tracked.map((t) => t.name))}）`
        ).toBeDefined();
        expect(entry.op).toBe('write');
      }

      // ── 面板：5 张卡片逐张可见（out-dir 是交付根，没有产物祖先 → 不折叠）
      const panel = page.getByTestId('task-assets-panel');
      await expect(panel).toBeVisible({ timeout: 15_000 });
      const processSection = panel.getByTestId('asset-section-process');
      const firstFile = processSection.getByText(OUT_FILES[0], { exact: false }).first();
      if (!(await firstFile.isVisible().catch(() => false))) {
        await panel.getByTestId('asset-section-toggle-process').click();
      }
      for (const name of OUT_FILES) {
        await expect(processSection.getByText(name, { exact: false }).first()).toBeVisible({
          timeout: 15_000,
        });
      }
      console.log('[test] ✅ 工作区外产物目录追踪 + 面板完整性通过');
    }
  );
});
