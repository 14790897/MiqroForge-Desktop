/**
 * Slurm MCP 计费真实链路 E2E（opt-in，需凭据；CI 无凭据自动跳过）：
 *   真实 OAuth2 登录（设置页 MiQroForge 平台）→ 新会话发消息 →
 *   真实 LLM 调用 slurm MCP submit_slurm_job 提交作业 → check_job_status
 *   轮询到 state=RUNNING → Python 发 slurm_job_running 事件 → Desktop
 *   扣 10 积分 → 聊天区出现扣费提示。
 *
 * 运行（会真实消耗：集群一次 hostname 作业 + 测试账号 10 积分）：
 *   QRAFT_PHONE=… QRAFT_PASSWORD=… SLURM_MCP_KEY=… npx playwright test \\
 *     --config=playwright.config.ts --project=electron tests/e2e/billing-live.spec.ts
 * 依赖：真实平台可达、本地 slurm MCP 运行中（SLURM_MCP_URL）、本地
 * provider 配置（launchElectronApp 复制用户配置）。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const SLURM_MCP_URL = process.env.SLURM_MCP_URL ?? 'http://127.0.0.1:9000/mcp';
const SLURM_MCP_KEY = process.env.SLURM_MCP_KEY ?? '';

async function approveLoop(page: Page, timeout = 180_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const btn = page
      .getByRole('button', { name: '持久允许' })
      .or(page.getByRole('button', { name: '永久允许' }));
    if (await btn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await btn.click();
    }
    const thinking = await page
      .getByText('Thinking…')
      .isVisible()
      .catch(() => false);
    if (!thinking) break;
    await page.waitForTimeout(1000);
  }
}

const HAS_CREDS = !!process.env.QRAFT_PHONE && !!process.env.QRAFT_PASSWORD && !!SLURM_MCP_KEY;

const describeFn = HAS_CREDS ? test.describe : test.describe.skip;

describeFn('Billing live E2E — slurm MCP RUNNING 扣分 (opt-in)', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    // 把本地 slurm MCP 注入临时配置：URL + Bearer API Key。
    // config.json 采用 camelCase 键（mcpServers）——蛇形键会被别名解析
    // 遮蔽（用户配置里两者并存且均为空），两个键都要写。
    fixture = await launchElectronApp((config: Record<string, any>) => {
      config.tools = config.tools || {};
      const slurm = {
        url: SLURM_MCP_URL,
        headers: { Authorization: `Bearer ${SLURM_MCP_KEY}` },
        tool_timeout: 90,
        toolTimeout: 90,
        description:
          'SLURM cluster job management: submit_slurm_job, check_job_status, cancel_slurm_job, list_partitions, get_job_output',
      };
      config.tools.mcpServers = { slurm };
      config.tools.mcp_servers = { slurm };
      return config;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
  }, 180_000);

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
  });

  test(
    '真实登录 → slurm MCP 提交作业 → RUNNING 扣 10 积分 → 聊天区提示',
    { timeout: 360_000 },
    async () => {
      // 1. 设置页真实登录（dev userData 可能残留上次运行的登录态，幂等处理）
      await page.getByText(/^(System Settings|系统设置)$/).click();
      await page
        .getByRole('tab')
        .filter({ hasText: /MiQroForge/ })
        .first()
        .click();
      const loggedInBadge = page.getByText('已登录');
      if (!(await loggedInBadge.isVisible({ timeout: 5000 }).catch(() => false))) {
        await page.getByTestId('qraft-phone-input').fill(process.env.QRAFT_PHONE!);
        await page.getByTestId('qraft-password-input').fill(process.env.QRAFT_PASSWORD!);
        await page.getByTestId('qraft-login-btn').click();
      }
      await expect(loggedInBadge).toBeVisible({ timeout: 90_000 });

      // 2. 新会话 + 预授权（避免审批卡住工具执行）
      await createNewConversation(page);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // 3. 指示模型用 slurm MCP 提交作业并轮询到 RUNNING
      //（工具注册名为 mcp_slurm_<tool>，必须用注册名调用；明确只提交一次，
      // 避免模型对作业 ID 提取失败时重复提交造成多作业噪音）
      await sendMessage(
        page,
        '使用 mcp_slurm_submit_slurm_job 工具提交作业（script 参数用 "#!/bin/bash\\nsleep 30\\nhostname"，' +
          '保证轮询时作业仍在运行），只提交一次，不要重复提交。' +
          '然后用 mcp_slurm_check_job_status 轮询该作业，直到状态为 RUNNING 后，最后只回复 DONE_SLURM'
      );
      await approveLoop(page);

      // 4. RUNNING 扣费提示（10 积分）——出现即截图，作为 PR 证据
      await expect(page.getByText(/已扣 10 积分/).first()).toBeVisible({ timeout: 300_000 });
      await page.screenshot({ path: 'test-results/slurm-billing-charge.png', fullPage: true });

      // 5. 回合正常收尾
      await waitForResponseComplete(page, 300_000);
      await expect(
        page
          .getByTestId('chat-message-assistant')
          .getByText(/DONE_SLURM/)
          .first()
      ).toBeVisible({ timeout: 30_000 });

      await page.screenshot({ path: 'test-results/slurm-billing-live.png', fullPage: true });
    }
  );
});
