/**
 * 托管 slurm MCP 网关 live E2E（opt-in，需真实登录 + 可用 LLM 凭据；
 * CI 无凭据自动跳过）：
 *   真实登录 → 内置 miqroforge-slurm（http + insecure_http 默认放行）→
 *   真实 LLM 在**自然提示词**下自主发现并调用 slurm MCP 提交作业 → 确认卡
 *   确认 → 作业提交并执行。
 *
 * 断言「模型能自主发现 mcp_miqroforge-slurm_* 工具并提交作业」（真实用户路径），
 * **不**断言扣分：计费当前仅在观测到 state=RUNNING 时触发，自然提示词下模型
 * 常提交快作业（PENDING→COMPLETED，从未观测到 RUNNING）→ 不扣分（已知计费漏洞，
 * 见 issue）。故本 spec 只断言 MCP 工具被发现并成功提交。
 *
 * 与 billing-live.spec.ts 的区别：后者走自部署本地回环服务器（127.0.0.1）
 * + 显式 Bearer header；本 spec 走 #1029 开启的内置托管网关（登录态注入
 * 共享 mcpGatewayKey，零配置）。
 *
 * Run（真实消耗：一个集群作业）：
 *   QRAFT_PHONE=… QRAFT_PASSWORD=… DEEPSEEK_API_KEY=… npx playwright test \
 *     --config=playwright.config.ts --project=electron tests/e2e/billing-hosted-live.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  sendMessage,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  browserLogin,
  type ElectronFixture,
} from './helpers/electron-setup';

const HAS_CREDS =
  !!process.env.QRAFT_PHONE && !!process.env.QRAFT_PASSWORD && !!process.env.DEEPSEEK_API_KEY;

const describeFn = HAS_CREDS ? test.describe : test.describe.skip;

/** 轮询处理审批弹窗与确认卡，直到 ready() 为真或超时。 */
async function driveUntilReady(page: Page, ready: () => Promise<boolean>, timeout = 300_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await ready()) return true;
    // 审批弹窗（工具执行授权）
    const allow = page
      .getByRole('button', { name: '持久允许' })
      .or(page.getByRole('button', { name: '永久允许' }));
    if (await allow.isVisible({ timeout: 300 }).catch(() => false)) {
      await allow
        .first()
        .click()
        .catch(() => {});
    }
    // 确认卡（ask_user_confirm_card）：点主按钮「确认提交」
    const primary = page.getByTestId('confirm-card-primary');
    if (await primary.isVisible({ timeout: 300 }).catch(() => false)) {
      await primary
        .first()
        .click()
        .catch(() => {});
    }
    await page.waitForTimeout(1500);
  }
  return ready();
}

describeFn('托管 slurm MCP 网关 live E2E（opt-in）', () => {
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
    '真实登录 → 自然提示词 → 模型自主发现并调用 slurm MCP 提交作业',
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

      // 2. 新会话 + 预授权
      await createNewConversation(page);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // 3. 自然提示词——不点名工具名，模型需自行发现 slurm MCP 并提交
      await sendMessage(page, '使用slurm提交任意一个任务');

      // 4. 驱动确认卡/审批，直到出现 submit_slurm_job 工具调用记录
      const submitted = await driveUntilReady(page, async () =>
        (await page
          .locator('main')
          .textContent()
          .catch(() => ''))!.includes('mcp_miqroforge-slurm_submit_slurm_job')
      );
      expect(submitted, '模型应自主发现并调用 mcp_miqroforge-slurm_submit_slurm_job').toBe(true);

      // 5. 无内容安全拦截 / 错误
      const text =
        (await page
          .locator('main')
          .textContent()
          .catch(() => '')) ?? '';
      expect(text).not.toContain('内容安全策略拦截');

      await page.screenshot({ path: 'test-results/slurm-mcp-hosted-live.png', fullPage: true });
    }
  );
});
