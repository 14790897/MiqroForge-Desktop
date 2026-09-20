/**
 * Sidebar 搜索 与 离开聊天后的返回入口 —— 本 PR(#1151 / issue #1150) 新增功能
 * 的专用 E2E。回归套件（delete-all-focus / regression-480 / streaming-isolation）
 * 只覆盖既有行为，锁定不了这两个新入口的状态机。
 *
 * 重点锁定「关闭搜索时清空 query」—— 曾经的缺陷是：点搜索图标关闭只隐藏输入框、
 * query 仍生效，列表停在被过滤的状态而界面上没有任何解释。下面的
 * 「点图标关闭 → 列表恢复完整」断言就是防它回归的。
 *
 * 用 plain_reply_mock 提供确定性回复，不依赖真实 LLM。
 *
 * Run:
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test --config=playwright.config.ts \
 *     --project=electron tests/e2e/sidebar-search-back-entry.spec.ts --workers=1
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  createNewConversation,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** Deterministic plain-reply mock（与 regression-delete-all-focus 同款）。 */
async function startPlainMock(): Promise<{ proc: ChildProcess; url: string }> {
  // Windows venv 用 Scripts/python.exe，posix 用 bin/python。
  const python =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(
    python,
    [join(APPS_DESKTOP, 'tests', 'e2e', 'fixtures', 'plain_reply_mock.py'), String(port)],
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
      throw new Error(`plain mock exited early: ${errTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!url) {
    proc.kill();
    throw new Error(`plain mock startup line not seen in 30s: ${errTail}`);
  }
  return { proc, url };
}

test.describe('Sidebar 搜索与返回入口 (#1150)', () => {
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mock: ChildProcess;

  /** 侧栏会话卡：按语义标识定位，不依赖样式类名。 */
  const sessionItems = () => page.locator('[data-testid="session-item"]');
  const searchInput = () => page.locator('input[aria-label="搜索会话"]');
  const searchToggle = () => page.getByTestId('nav-session-search');

  /** 造 N 条已落盘的会话（首条消息写入即持久化，回复完成才进列表）。 */
  async function seedSessions(prompts: string[]) {
    for (const p of prompts) {
      await createNewConversation(page);
      await sendMessage(page, p);
      await waitForResponseComplete(page, 90_000);
    }
    await expect(sessionItems()).toHaveCount(prompts.length, { timeout: 30_000 });
  }

  test.beforeAll(async () => {
    const m = await startPlainMock();
    mock = m.proc;
    const fixture = await launchElectronApp((config: any) => {
      // 把所有已配置的 provider 指向 mock（provider 由 agents.defaults.model 解析，
      // mock 忽略模型名与 key）。
      const providers = config.providers ?? {};
      for (const [, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = m.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
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

  test('搜索按标题过滤；点图标关闭会清空 query 并恢复完整列表', { timeout: 300_000 }, async () => {
    await seedSessions([
      '把侧栏的会话列表压成单行',
      '给会话列表加一个搜索入口',
      '压缩提问气泡和回复之间的间距',
    ]);
    const total = await sessionItems().count();
    expect(total).toBe(3);

    // 打开搜索：输入框出现且自动聚焦
    await searchToggle().click();
    await expect(searchInput()).toBeVisible();
    await expect(searchInput()).toBeFocused();
    await expect(searchToggle()).toHaveAttribute('aria-expanded', 'true');

    // 按标题过滤：只有「加一个搜索入口」那条命中
    await searchInput().fill('搜索');
    await expect(sessionItems()).toHaveCount(1, { timeout: 10_000 });

    // ★ 点图标关闭：query 必须一并清空，列表恢复完整
    //   （修复前这里会停在 1 条，而输入框已经藏起来了）
    await searchToggle().click();
    await expect(searchInput()).toHaveCount(0);
    await expect(searchToggle()).toHaveAttribute('aria-expanded', 'false');
    await expect(sessionItems()).toHaveCount(total);
  });

  test('Esc 清空 query 并收起；无结果时给出提示', { timeout: 180_000 }, async () => {
    const total = await sessionItems().count();

    await searchToggle().click();
    await searchInput().fill('zzz-不存在的关键词');
    await expect(sessionItems()).toHaveCount(0);
    await expect(page.getByText('没有匹配的会话')).toBeVisible();

    await searchInput().press('Escape');
    await expect(searchInput()).toHaveCount(0);
    await expect(sessionItems()).toHaveCount(total);

    // 重新打开时应是空 query（不复用上次的关键词）
    await searchToggle().click();
    await expect(searchInput()).toHaveValue('');
    await expect(sessionItems()).toHaveCount(total);
    await searchInput().press('Escape');
  });

  test('离开聊天页后底部出现「返回任务」，点击回到聊天', { timeout: 180_000 }, async () => {
    const backBtn = page.getByTestId('nav-back-to-tasks');
    const settingsBtn = page.getByTestId('nav-system-settings');

    // 聊天态：只有版本号，没有返回入口
    await expect(backBtn).toHaveCount(0);
    await expect(settingsBtn).toBeVisible();

    await settingsBtn.click();
    // 设置页：返回入口出现，且「系统设置」仍在（没有把回程做成开关）
    await expect(backBtn).toBeVisible({ timeout: 15_000 });
    await expect(settingsBtn).toBeVisible();

    await backBtn.click();
    // 回到聊天：返回入口消失，输入框可用
    await expect(backBtn).toHaveCount(0);
    await expect(page.locator('[data-testid="chat-input-container"] textarea')).toBeVisible({
      timeout: 15_000,
    });
  });
});
