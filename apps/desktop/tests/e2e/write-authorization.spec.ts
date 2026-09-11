/**
 * E2E — issue #864 写授权卡（write_file 写入 workspace 外目录）。
 *
 * 用本地 mock OpenAI 服务器（scripts/mock_openai.py，确定性状态机）驱动，
 * 不依赖真实 LLM 行为，也不需要 WSL 沙箱（走 native 路径，`boundary_enforced`
 * 仅当 `restrict_to_workspace` 开启或 WSL 沙箱激活时才弹卡）。本 spec 在
 * launchElectronApp 里 patch `tools.restrictToWorkspace: true` 打开写白名单，
 * 这样 write_file 写 workspace 外路径才会命中授权卡。
 *
 * 链路：
 *   1. mock 第一轮返回 write_file 工具调用，target 是 workspace 外的临时目录，
 *      并带 authorize_paths 声明 → 桌面弹写授权卡（允许本次/本目录不再询问/拒绝）
 *   2. 用户点「允许本次」→ 写放行 → 产物落在 workspace 外目录
 *   3. 断言 host 文件存在且内容正确
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron \
 *      write-authorization.spec.ts --workers=1
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, readFileSync, mkdtempSync, rmSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  sendMessage,
  launchElectronApp,
  closeElectronApp,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

async function startMockOpenAI(outDir: string): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_openai.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1', MIQI_AUTH_OUT_DIR: outDir },
    windowsHide: true,
  });

  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock] ${t.trim()}`);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-err] ${String(d).trim()}`);
  });
  proc.on('exit', (code) => console.log(`[test] mock server exited: ${code}`));

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock OpenAI server exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock OpenAI server startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[test] mock OpenAI server ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl };
}

test.describe('Write Authorization Card (#864)', () => {
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;
  let outDir: string;

  test.beforeAll(async () => {
    // 写授权卡依赖一个"写白名单"边界。native 路径 + restrictToWorkspace=true
    // 才会触发 _resolve_path 的 shared_roots 检查，进而命中授权卡。
    outDir = mkdtempSync(join(tmpdir(), 'miqi-e2e-auth-'));
    console.log(`[test] auth out dir: ${outDir}`);
    const mock = await startMockOpenAI(outDir);
    mockServer = mock.proc;

    const fixture = await launchElectronApp(
      (config: any) => {
        const providers = config.providers ?? {};
        for (const [name, p] of Object.entries(providers)) {
          if (p && typeof p === 'object') {
            (p as any).apiBase = mock.mockUrl;
            if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
          }
        }
        config.providers = providers;
        const tools = config.tools ?? {};
        config.tools = { ...tools, restrictToWorkspace: true };
      },
      { bypassAll: false }
    );
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
    try {
      rmSync(outDir, { recursive: true, force: true });
    } catch {}
  });

  test(
    'write_file 写 workspace 外目录 → 弹写授权卡 → 允许本次 → 写入成功',
    { timeout: LLM_TIMEOUT },
    async () => {
      const cardArea = page.getByTestId('confirm-card-area');
      const resolvedArea = page.getByTestId('confirm-card-resolved');

      // 跳过 PermissionEngine 的通用「文件操作审批」dialog（legacy 路径会在
      // write_file 进入 tool.execute 之前先弹它），这样本测试能精确断言到
      // 我们 issue #864 的「授权写入工作区外目录」卡。`*:*` permanent 只影响
      // PermissionEngine 的审批，不影响 tool.execute 内的授权卡（它读
      // config.approvals 的 bypass 开关，与 permanent allowlist 无关）。
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      await sendMessage(page, '写授权测试');

      // 写授权卡弹出（title 固定为「授权写入工作区外目录」）
      await expect(cardArea).toBeVisible({ timeout: 60_000 });
      await expect(cardArea.getByText('授权写入工作区外目录')).toBeVisible();
      await expect(cardArea.getByRole('button', { name: '允许本次' })).toBeVisible();
      await expect(cardArea.getByRole('button', { name: '本目录不再询问' })).toBeVisible();
      await expect(cardArea.getByRole('button', { name: '拒绝' })).toBeVisible();

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-card.png`,
      });

      // 点「允许本次」→ 写放行
      await cardArea.getByRole('button', { name: '允许本次' }).click();
      await expect(resolvedArea.getByText(/授权写入工作区外目录/)).toBeVisible({
        timeout: 30_000,
      });

      // 产物落在 workspace 外目录
      const target = join(outDir, 'auth_probe.txt');
      await expect.poll(async () => existsSync(target), { timeout: 30_000 }).toBe(true);
      const content = readFileSync(target, 'utf-8');
      expect(content).toContain('authorization-card-e2e-probe');
      console.log(`[test] ✅ 写授权卡放行，产物落在 workspace 外目录`);

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-final.png`,
        fullPage: true,
        timeout: 60_000,
      });
    }
  );
});

test.describe('Write Authorization Bypass (#864)', () => {
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;
  let outDir: string;

  test.beforeAll(async () => {
    outDir = mkdtempSync(join(tmpdir(), 'miqi-e2e-auth-bypass-'));
    console.log(`[test] bypass auth out dir: ${outDir}`);
    const mock = await startMockOpenAI(outDir);
    mockServer = mock.proc;

    // bypassAll 默认 true（electron-setup 的默认行为）——写授权卡应被跳过。
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.mockUrl;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
      const tools = config.tools ?? {};
      config.tools = { ...tools, restrictToWorkspace: true };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
    try {
      rmSync(outDir, { recursive: true, force: true });
    } catch {}
  });

  test(
    'approvals.bypass_all=true 时写 workspace 外目录不弹授权卡直接写入',
    { timeout: LLM_TIMEOUT },
    async () => {
      const cardArea = page.getByTestId('confirm-card-area');
      await sendMessage(page, '写授权测试');

      const target = join(outDir, 'auth_probe.txt');
      await expect.poll(async () => existsSync(target), { timeout: 30_000 }).toBe(true);
      const content = readFileSync(target, 'utf-8');
      expect(content).toContain('authorization-card-e2e-probe');

      await expect(cardArea).toBeHidden();
      console.log(`[test] ✅ bypass 下写 workspace 外目录无需授权卡`);
    }
  );
});
