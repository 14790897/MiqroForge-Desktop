/**
 * #1257 登出中断在途回合 —— 真机用例。
 *
 * mock：`scripts/mock_hang.py`（POST 永不响应）→ 回合确定性地一直 in-flight，
 * 不依赖任何模型行为。
 *
 * 判别点：**登出后 bridge 日志里出现 `chat.abort` 的处理行** —— 说明中断真的
 * 发到了后端（app_server 的 chat.abort 处理会记「released turn lock」）。
 * 修复前 logout 只清本地凭据，后端那个回合会继续跑：界面一直停在「生成中」，
 * 会话还被 bridge 侧 turn lock 占着（新消息一律 TURN_IN_PROGRESS）。
 *
 * 平台判定登录失效后的自动退出登录走的是同一个 `logout()`，所以这条用例同时
 * 覆盖那条路径的中断行为。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { startMockServer } from './helpers/mock-server';

// 同 issue-981 系列：mock 类 spec 在 macOS CI 上连不上本地 listener。
const SKIP_MOCK_ON_MACOS_CI = process.platform === 'darwin' && !!process.env.CI;

/** 把所有 provider 指向 mock，并把默认模型钉到 deepseek —— 真实 API 永不被调用。 */
function patchProvidersToMock(config: any, mockUrl: string): void {
  const providers = config.providers ?? {};
  for (const [, p] of Object.entries(providers)) {
    if (p && typeof p === 'object') {
      (p as any).apiBase = mockUrl;
      if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
    }
  }
  config.agents = config.agents ?? {};
  config.agents.defaults = config.agents.defaults ?? {};
  config.agents.defaults.model = 'deepseek/deepseek-chat';
  const deepseek = (providers as any).deepseek ?? {};
  deepseek.apiBase = mockUrl;
  deepseek.apiKey = `${deepseek.apiKey ?? 'sk-mock-key'}`;
  (providers as any).deepseek = deepseek;
  config.providers = providers;
}

/**
 * 读数据根下所有 bridge 日志。
 *
 * 递归而不是写死 `<root>/workspace/logs`：登录后工作区按账号收口
 * （#1185），日志可能落在 `<root>/accounts/<sub>/workspace/logs`。
 */
function readBridgeLogs(root: string): string {
  const out: string[] = [];
  const walk = (dir: string, depth: number): void => {
    if (depth > 5) return;
    let names: string[] = [];
    try {
      names = readdirSync(dir);
    } catch {
      return;
    }
    for (const name of names) {
      const full = join(dir, name);
      try {
        if (statSync(full).isDirectory()) walk(full, depth + 1);
        else if (name.startsWith('bridge-') && name.endsWith('.log')) {
          out.push(readFileSync(full, 'utf8'));
        }
      } catch {
        /* 读不到就跳过：日志是收据，不是被测对象 */
      }
    }
  };
  walk(root, 0);
  return out.join('\n');
}

/**
 * 中断真的生效的收据：`miqi/bridge/loop.py` 的 release_turn_lock 每次被
 * chat.abort 调用都会记一行「chat.abort: released turn lock for session <id>」。
 *
 * 只匹配这一句而不是「chat.abort」：bridge 启动时会把已注册的方法名打进日志，
 * 那里面本来就有 chat.abort，拿它当收据会永远为真（实测踩到）。
 */
const ABORT_RECEIPT = 'released turn lock for session';

function abortReceipts(root: string): number {
  return (readBridgeLogs(root).match(/released turn lock for session/g) ?? []).length;
}

test.describe('#1257 登出中断在途回合', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;
  /** mock_hang 每收到一次 POST 打一行 —— 用来正向确认「回合真的在飞」。 */
  let mockStdout = '';
  const mockRequests = (): number =>
    (mockStdout.match(/\[mock-hang\] request received/g) ?? []).length;

  test.skip(SKIP_MOCK_ON_MACOS_CI, 'macOS CI cannot reach the local mock server');

  test.beforeAll(async () => {
    const mock = await startMockServer('mock_hang.py');
    mockServer = mock.proc;
    mockServer.stdout?.on('data', (d) => {
      mockStdout += String(d);
    });
    const fixture = await launchElectronApp((config: any) => {
      patchProvidersToMock(config, mock.mockUrl);
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
  }, 120_000);

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test('登出会把还在跑的回合中断给后端（不留在「生成中」）', async () => {
    test.setTimeout(240_000);

    await createNewConversation(page);
    await sendMessage(page, `登出中断：这条回合要一直挂着（${Date.now().toString(36).slice(-4)}）`);

    // 正向收据：请求真的到了 provider → 回合在飞（mock 永不响应，故不会结束）
    await expect
      .poll(mockRequests, {
        timeout: 60_000,
        message: '回合必须真的发到 provider，才谈得上「在途回合」',
      })
      .toBeGreaterThan(0);
    await expect(page.getByTitle('停止生成')).toBeVisible({ timeout: 30_000 });

    // 登出前：回合在飞（输入框右侧是「停止生成」）
    await page.screenshot({ path: 'test-results/issue-1257-before-logout.png', fullPage: true });

    // 登出前没有中断收据（有的话下面的断言就不是判别点）
    const before = abortReceipts(miqiHome);

    // 登出（平台判定失效后的自动退出走的是同一个 logout）
    await page.evaluate(async () => await (window as any).miqi.qraft.logout());

    // 判别点：中断真的到了后端并释放了 turn lock
    await expect
      .poll(() => abortReceipts(miqiHome), {
        timeout: 60_000,
        message: `登出后 bridge 必须收到 chat.abort 并释放 turn lock（${ABORT_RECEIPT} 应从 ${before} 增加；修复前后端回合会一直跑下去）`,
      })
      .toBeGreaterThan(before);

    // 登出后：回合不再处于「生成中」（输入框右侧回到发送键、可继续输入）
    await expect(page.getByTitle('停止生成')).toHaveCount(0, { timeout: 30_000 });
    await page.screenshot({ path: 'test-results/issue-1257-after-logout.png', fullPage: true });

    // 登出本身生效
    const status = await page.evaluate(async () => await (window as any).miqi.qraft.status());
    expect(status.loggedIn).toBe(false);
  });
});
