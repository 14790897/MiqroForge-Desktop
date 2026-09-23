/**
 * AI 网关真实账号 live E2E（opt-in，默认跳过，不入 CI 常规执行）。
 *
 * 与 billing-live.spec.ts 同策略：凭据仅经环境变量注入，登录态、密钥
 * 均落在 launchElectronApp 的临时 MIQI_HOME，测试结束随临时目录清理。
 *
 * 用法：
 *   QRAFT_LIVE=1 QRAFT_PHONE=<测试账号> QRAFT_PASSWORD=<密码> \
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *   npx playwright test --config=playwright.config.ts --project=electron ai-gateway-live.spec.ts
 *
 * 覆盖真实全链路（issue #922 / #1172 / PR #946）：
 *   真实 Electron 应用 → QraftPage 浏览器登录（OAuth）→ /oauth2/userinfo 下发
 *   encryptedApiKey（网关状态行"可用"+ 配置版本）→ 默认模型自动就绪（#1172）
 *   → 聊天发消息 → 主进程写 token.json → Python make_provider 路由
 *   AnthropicProvider → 平台 AI 网关真实回复。
 *
 * 关键前提：本用例**不拷贝**开发者本机的 ~/.forge/config.json（noUserConfig），
 * 以复现 #1172 的「全新安装」状态 —— MIQI_HOME 里没有用户 provider 凭据、也没
 * 有显式配置过 agents.defaults.model（config.get 带出的是 schema 默认值）。
 */

import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { readFileSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  browserLogin,
  createNewConversation,
  sendMessage,
  type ElectronFixture,
} from './helpers/electron-setup';

const LIVE = process.env.QRAFT_LIVE === '1';
const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';
const READY = LIVE && PHONE !== '' && PASSWORD !== '';
const LLM_TIMEOUT = 300_000;

const describeFn = READY ? test.describe : test.describe.skip;

describeFn('AI 网关真实账号 live E2E (opt-in)', () => {
  let fixture: ElectronFixture;

  test.beforeAll(async () => {
    // 全新安装：不拷贝本机 provider 配置（#1172 的复现前提）。
    fixture = await launchElectronApp(undefined, { noUserConfig: true });
  }, 180_000);

  test.afterAll(async () => {
    if (fixture?.electronApp) await closeElectronApp(fixture.electronApp, fixture.miqiHome);
  });

  test(
    '全新安装真实登录 → 默认模型自动就绪 → 消息经网关真实回复（#1172）',
    { timeout: LLM_TIMEOUT },
    async () => {
      const page = fixture.page;

      // 1. 设置页真实登录（幂等：dev userData 可能残留上次登录态）
      const loggedInBadge = page.getByText('已登录');
      if (!(await loggedInBadge.isVisible({ timeout: 5000 }).catch(() => false))) {
        await browserLogin(page, fixture.electronApp, PHONE, PASSWORD);
      }
      await expect(loggedInBadge).toBeVisible({ timeout: 90_000 });

      // 2. 网关状态：真实 userinfo 下发 encryptedApiKey → 状态行"可用" + 配置版本
      await expect(page.getByTestId('qraft-ai-gateway')).toBeVisible({ timeout: 30_000 });
      await expect(page.getByTestId('qraft-ai-gateway-status')).toHaveText('可用');
      await expect(page.getByTestId('qraft-ai-gateway')).toContainText('配置版本 v1');

      // 2.5 右上角必须是真实登录账号（不是预置的假账号/E2E 占位）
      const accountName = await page.evaluate(async () => {
        const s = await (window as any).miqi.qraft.status();
        return s?.account?.nickname || s?.account?.username || '';
      });
      expect(accountName).not.toBe('');
      await expect(page.getByTestId('topbar-account-chip')).toHaveText(accountName);

      // 3. 默认模型自动就绪（#1172）：全新安装下 config.json 里原本只有 schema
      //    默认值（config.get 会把它带出来，永远非空），登录 + 网关 active 后
      //    应自动落盘为网关模型 —— 用户无需去「设置 → 模型」手动选择。
      const configPath = join(fixture.miqiHome, 'config.json');
      await expect
        .poll(
          () => {
            try {
              return JSON.parse(readFileSync(configPath, 'utf8')).agents?.defaults?.model ?? '';
            } catch {
              return '';
            }
          },
          { timeout: 120_000 }
        )
        .toBe('deepseek/deepseek-v4-flash');

      // 4. 新会话发消息（默认模型 deepseek/deepseek-v4-flash 即网关模型）
      await createNewConversation(page);
      await sendMessage(page, 'ping');

      // 5. 真实回复经网关流式渲染（assistant 气泡出现 pong）
      await expect(
        page.getByTestId('chat-message-assistant').getByText(/pong/i).first()
      ).toBeVisible({ timeout: 180_000 });

      await page.screenshot({
        path: 'test-results/ai-gateway-live-reply.png',
        fullPage: true,
      });

      // 6. 模型 tab 复核：全程没有任何手动选择，「当前默认模型」已是网关模型
      await page.getByText(/^(System Settings|系统设置)$/).click();
      await page.getByRole('tab', { name: '模型' }).click();
      await expect(page.getByTestId('providers-active-model')).toHaveText(
        '当前默认模型：deepseek/deepseek-v4-flash',
        { timeout: 60_000 }
      );
      await page.screenshot({
        path: 'test-results/ai-gateway-live-autoready.png',
        fullPage: true,
      });
    }
  );
});
