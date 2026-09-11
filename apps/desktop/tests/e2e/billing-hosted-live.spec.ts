/**
 * 托管 slurm MCP 网关计费 live E2E（opt-in，需真实登录 + 可用 LLM 凭据；
 * CI 无凭据自动跳过）：
 *   真实登录 → 内置 miqroforge-slurm（http + insecure_http 默认放行）→
 *   真实 LLM 调用 mcp_miqroforge-slurm_submit_slurm_job 提交作业 →
 *   check_job_status 轮询到 RUNNING → Desktop 扣 10 积分 → 聊天区提示。
 *
 * 与 billing-live.spec.ts 的区别：后者走自部署本地回环服务器（127.0.0.1）
 * + 显式 Bearer header；本 spec 走 #1029 开启的内置托管网关（登录态注入
 * 共享 mcpGatewayKey，零配置）。覆盖「登录后调托管 slurm MCP + 计费」全链。
 *
 * Run（真实消耗：测试账号 10 积分 + 一个集群作业）：
 *   QRAFT_PHONE=… QRAFT_PASSWORD=… DEEPSEEK_API_KEY=… npx playwright test \
 *     --config=playwright.config.ts --project=electron tests/e2e/billing-hosted-live.spec.ts
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
  browserLogin,
  type ElectronFixture,
} from './helpers/electron-setup';

const HAS_CREDS =
  !!process.env.QRAFT_PHONE && !!process.env.QRAFT_PASSWORD && !!process.env.DEEPSEEK_API_KEY;

const describeFn = HAS_CREDS ? test.describe : test.describe.skip;

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

describeFn('托管 slurm MCP 网关计费 live E2E（opt-in）', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    // 清掉开发机 config 残留的 mcpServers（让内置默认 miqroforge-slurm 生效）。
    // 登录后平台会自动下发 AI 网关 encryptedApiKey（token.json 的 aiGateway 块），
    // 默认模型 deepseek-v4-flash 走网关即可回复 LLM；但网关真实 LLM 对「调用
    // submit_slurm_job」这类工具调用推理慢、方差大无法收敛（见记忆
    // slurm-mcp-billing-map），故这里注入官方 DeepSeek + deepseek-chat，让工具
    // 调用确定性收敛、测试快——不是「登录后无 key」。
    fixture = await launchElectronApp((config: any) => {
      if (config.tools && typeof config.tools === 'object') {
        delete config.tools.mcpServers;
        delete config.tools.mcp_servers;
      }
      config.providers = {
        ...(config.providers ?? {}),
        deepseek: { apiKey: process.env.DEEPSEEK_API_KEY },
      };
      config.agents = {
        ...(config.agents ?? {}),
        defaults: { ...(config.agents?.defaults ?? {}), model: 'deepseek/deepseek-chat' },
      };
      return config;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
  }, 180_000);

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
  });

  test(
    '真实登录 → 托管 slurm MCP 提交作业 → RUNNING 扣 10 积分 → 聊天区提示',
    { timeout: 360_000 },
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

      // 2. 新会话 + 预授权（避免审批卡住工具执行）
      await createNewConversation(page);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // 3. 指示模型用托管网关工具提交作业并轮询到 RUNNING
      //（工具注册名为 mcp_miqroforge-slurm_<tool>；只提交一次避免多作业噪音）
      await sendMessage(
        page,
        '使用 mcp_miqroforge-slurm_submit_slurm_job 工具提交作业（script 参数用 ' +
          '"#!/bin/bash\\nsleep 30\\nhostname"，保证轮询时作业仍在运行），只提交一次，不要重复提交。' +
          '然后用 mcp_miqroforge-slurm_check_job_status 轮询该作业，直到状态为 RUNNING 后，最后只回复 DONE_SLURM'
      );
      await approveLoop(page);

      // 4. RUNNING 扣费提示（10 积分）——出现即截图，作为证据
      await expect(page.getByText(/已扣 10 积分/).first()).toBeVisible({ timeout: 300_000 });
      await page.screenshot({
        path: 'test-results/slurm-billing-hosted-charge.png',
        fullPage: true,
      });

      // 5. 回合正常收尾
      await waitForResponseComplete(page, 300_000);
      await expect(
        page
          .getByTestId('chat-message-assistant')
          .getByText(/DONE_SLURM/)
          .first()
      ).toBeVisible({ timeout: 30_000 });

      await page.screenshot({ path: 'test-results/slurm-billing-hosted-live.png', fullPage: true });
    }
  );
});
