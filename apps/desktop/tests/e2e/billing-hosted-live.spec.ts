/**
 * 托管 slurm MCP 网关 live E2E（opt-in，需真实登录 + 可用 LLM 凭据；
 * CI 无凭据自动跳过）：
 *   真实登录 → 内置 miqroforge-slurm（http + insecure_http 默认放行）→
 *   真实 LLM 在**自然提示词**下自主发现并调用 slurm MCP 提交作业 → 确认卡
 *   确认 → 作业提交并执行。
 *
 * 断言「模型能自主发现 mcp_miqroforge-slurm_* 工具并提交作业」+「出现扣费提示」。
 * 计费在作业进入可扣费状态（RUNNING 或 COMPLETED）时触发——快作业从 PENDING
 * 直接到 COMPLETED 也能扣分（2026-09-11 修复：原先只认 RUNNING，快作业漏扣；
 * FAILED/TIMEOUT/CANCELLED 不计费）。
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
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
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

/** 读桥日志（<MIQI_HOME>/workspace/logs/bridge-*.log）拼接文本。 */
function readBridgeLog(miqiHome: string): string {
  const dir = join(miqiHome, 'workspace', 'logs');
  if (!existsSync(dir)) return '';
  return readdirSync(dir)
    .filter((f) => /^bridge-\d{4}-\d{2}-\d{2}\.log$/.test(f))
    .sort()
    .map((f) => readFileSync(join(dir, f), 'utf8'))
    .join('\n');
}

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

      // 4. 驱动确认卡/审批，直到扣费提示出现（作业进入可扣费状态：
      //    RUNNING 或已执行终态 COMPLETED/FAILED/TIMEOUT）
      const charged = await driveUntilReady(
        page,
        async () =>
          await page
            .getByText(/已扣 10 积分/)
            .first()
            .isVisible()
            .catch(() => false)
      );
      expect(charged, '应出现「已扣 10 积分」扣费提示').toBe(true);

      // 5. 模型自主发现并调用了 slurm MCP 提交作业
      const text =
        (await page
          .locator('main')
          .textContent()
          .catch(() => '')) ?? '';
      expect(text).toContain('mcp_miqroforge-slurm_submit_slurm_job');
      expect(text).not.toContain('内容安全策略拦截');

      // 6. 提交确实**成功**（不止工具名出现）：桥日志里 submit 工具返回
      //    success:true + 非空 job_id。失败/被拒的提交不会带这两项，也不会
      //    产生作业 → 上一步的扣费断言同样不会成立（双重兜底）。
      const log = readBridgeLog(fixture.miqiHome);
      expect(log, 'submit_slurm_job 应返回成功结果（success:true）').toMatch(
        /submit_slurm_job done \([^)]*\): result prefix='[^']*"success":\s*true/
      );
      expect(log, '提交结果应含非空 job_id').toMatch(/"job_id":\s*"\d+"/);

      await page.screenshot({
        path: 'test-results/slurm-billing-hosted-charge.png',
        fullPage: true,
      });
      await expect(page.getByText(/已扣 10 积分/).first()).toBeVisible({ timeout: 30_000 });
      await page.screenshot({ path: 'test-results/slurm-billing-hosted-live.png', fullPage: true });
    }
  );
});
