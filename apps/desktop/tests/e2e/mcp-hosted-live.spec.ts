/**
 * Hosted slurm MCP 网关 live E2E（opt-in，需真实登录凭据；CI 无凭据跳过）：
 *   真实登录 → 启动会话 → 内置 miqroforge-slurm（http + insecure_http 默认
 *   放行 + 登录态注入 mcpGatewayKey）连上平台托管网关，注册 9 个工具。
 *
 * 断言方式：读 bridge 日志（workspace/logs/bridge-*.log）里的两条确定性
 * 标记——「登录态注入网关凭据」与「connected, 9 tools registered」。会话
 * 是懒加载（首条消息/首个 thread 才 RuntimeSession.start → _connect_mcp），
 * 故直接用 threads.start 触发，不依赖 LLM、不消耗积分、不提交集群作业，
 * 是 #1029 改动（http 放行 + 凭据注入）的最小闭环。
 *
 * Run（无真实资源消耗：仅登录 + 连接）：
 *   QRAFT_PHONE=… QRAFT_PASSWORD=… npx playwright test \
 *     --config=playwright.config.ts --project=electron tests/e2e/mcp-hosted-live.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  launchElectronApp,
  closeElectronApp,
  browserLogin,
  type ElectronFixture,
} from './helpers/electron-setup';

const HAS_CREDS = !!process.env.QRAFT_PHONE && !!process.env.QRAFT_PASSWORD;

const describeFn = HAS_CREDS ? test.describe : test.describe.skip;

/** 读全部 bridge 日志（workspace/logs/bridge-YYYY-MM-DD.log），跨文件拼接。 */
function allBridgeLogs(miqiHome: string): string {
  const logDir = join(miqiHome, 'workspace', 'logs');
  if (!existsSync(logDir)) return '';
  return readdirSync(logDir)
    .filter((f) => /^bridge-\d{4}-\d{2}-\d{2}\.log$/.test(f))
    .sort()
    .map((f) => readFileSync(join(logDir, f), 'utf8'))
    .join('\n');
}

describeFn('Hosted slurm MCP 网关 live E2E（opt-in）', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    // 清掉用户真实 config 里残留的 mcpServers/mcp_servers（开发机 config
    // 常带本地 slurmmcp 回环服务器），让内置默认 miqroforge-slurm 生效——
    // 这正是被测对象。两个键都要删（camelCase 键清不掉会遮蔽默认）。
    fixture = await launchElectronApp((config: any) => {
      if (config.tools && typeof config.tools === 'object') {
        delete config.tools.mcpServers;
        delete config.tools.mcp_servers;
      }
      return config;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
  }, 180_000);

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
  });

  test(
    '真实登录 → 启动会话 → 内置 slurm 网关注入凭据并注册工具',
    { timeout: 180_000 },
    async () => {
      // 1. 设置页真实登录（幂等：dev userData 可能残留上次登录态）
      const loggedInBadge = page.getByText('已登录');
      if (!(await loggedInBadge.isVisible({ timeout: 5000 }).catch(() => false))) {
        await browserLogin(
          page,
          electronApp,
          process.env.QRAFT_PHONE!,
          process.env.QRAFT_PASSWORD!
        );
      }
      await expect(loggedInBadge).toBeVisible({ timeout: 90_000 });

      // 2. 启动会话（懒加载：首个 thread 才 RuntimeSession.start → _connect_mcp）
      await page
        .evaluate(() =>
          (window as any).miqi.threads.start({
            session_key: 'live-hosted-mcp',
            title: 'live-hosted-mcp',
          })
        )
        .catch((e: any) => {
          throw new Error(`threads.start failed: ${String(e)}`);
        });

      // 3. 轮询 bridge 日志：连接是异步后台任务，等待两条确定性标记出现
      const deadline = Date.now() + 60_000;
      let log = '';
      while (Date.now() < deadline) {
        log = allBridgeLogs(fixture.miqiHome);
        if (log.includes('登录态注入网关凭据') && log.includes('connected, 9 tools registered')) {
          break;
        }
        await page.waitForTimeout(1000);
      }

      const mcpLines = log
        .split('\n')
        .filter((l) => /mcp|slurm|非回环|注入|connected|make_provider|No API/i.test(l))
        .join('\n');
      console.log(`[test] MCP-related bridge log lines:\n${mcpLines || '(none)'}`);

      expect(log, 'bridge 日志应包含登录态注入网关凭据').toContain('登录态注入网关凭据');
      expect(log).toContain('connected, 9 tools registered');
    }
  );
});
