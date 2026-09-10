/**
 * 登录入口显性化 E2E（issue #1000）。
 *
 * 覆盖「登录入口不再藏在设置页深处」的四条路径：
 *  1. 首屏（空会话欢迎区）登录卡片 + 顶栏一键登录 chip；
 *  2. 模型面板未登录拦截：一键浏览器登录按钮（原「去登录」仅跳设置页）；
 *  3. 发起会话拦截：未登录发送 → 拦截气泡直接给出登录按钮（不依赖
 *     平台网络——本地 providers 无凭据即命中该分支）；
 *  4. （真实登录，需 QRAFT_PHONE / QRAFT_PASSWORD）从首屏卡片完成
 *     OAuth 登录 → 卡片消失、顶栏出现账号 chip。
 *
 * 默认 MIQI_E2E 启动（隐私门绕过）→ 登录衔接页（login-step）由
 * privacy-consent.spec.ts 覆盖，本 spec 不重复操作共享 userData 的
 * 同意记录，避免与并行运行的隐私门 spec 相互干扰。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp, browserLogin } from './helpers/electron-setup';

const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';

test.describe('登录入口显性化 (#1000)', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp).catch(() => {});
  });

  test('首屏与顶栏：未登录时登录入口直接可见', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;

    // 首屏空会话欢迎区：登录卡片 + 一键登录按钮
    await expect(page.getByTestId('chat-hero-login-card')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('chat-hero-login-btn')).toContainText('登录 MiQroForge 账号');
    // 次级入口：查看平台账号（设置 → MiQroForge 平台）
    await expect(page.getByTestId('chat-hero-open-qraft')).toBeVisible();
    // 顶栏：一键登录 chip（不依赖会话是否为空）
    await expect(page.getByTestId('topbar-login-btn')).toContainText('登录 MiQroForge 账号');

    await page.screenshot({
      path: 'test-results/qraft-login-entry-hero.png',
      fullPage: true,
    });
  });

  test('模型面板未登录拦截：直接一键浏览器登录', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;

    // 设置 → 模型 tab：未登录时模型选择被登录门控替换，按钮直接发起 OAuth
    await page.getByText(/^(System Settings|系统设置)$/).click();
    await page.getByRole('tab', { name: /模型/ }).click();
    await expect(page.getByTestId('model-quickpanel-login-btn')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('model-quickpanel-login-btn')).toContainText(
      '登录 MiQroForge 账号'
    );
  });

  test('发起会话拦截：未登录发送给出登录引导气泡（含一键登录按钮）', async () => {
    // 去掉用户配置里的任何 provider 凭据：确保 providers.list 全部
    // configured=false，发送必走「未登录 → 登录引导」拦截分支。
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      for (const key of Object.keys(providers)) {
        const p = providers[key];
        if (p && typeof p === 'object') {
          delete p.apiKey;
          delete p.api_key;
          delete p.env_key;
        }
      }
    });
    electronApp = fixture.electronApp;
    page = fixture.page;

    await expect(page.getByTestId('chat-hero-login-card')).toBeVisible({ timeout: 120_000 });

    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await textarea.fill('帮我总结今天的工作');
    await page.evaluate(() => {
      const ta = document.querySelector<HTMLTextAreaElement>(
        '[data-testid="chat-input-container"] textarea'
      );
      if (!ta) throw new Error('textarea not found');
      ta.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: 'Enter',
          code: 'Enter',
          keyCode: 13,
          bubbles: true,
          cancelable: true,
        })
      );
    });

    // 拦截气泡：登录引导文案 + 一键登录按钮（不发起真实模型调用）
    await expect(page.getByTestId('chat-error-login-btn')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('chat-error-login-btn')).toContainText('登录 MiQroForge 账号');
    // 输入草稿被恢复，可登录后直接重发
    await expect(textarea).toHaveValue('帮我总结今天的工作');

    await page.screenshot({
      path: 'test-results/qraft-login-entry-chat-interception.png',
      fullPage: true,
    });
  });

  test('首屏卡片完成真实 OAuth 登录：卡片消失、顶栏出现账号 chip', async () => {
    test.skip(!PHONE || !PASSWORD, '需要 QRAFT_PHONE / QRAFT_PASSWORD 环境变量');
    test.setTimeout(300_000);

    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;

    await expect(page.getByTestId('chat-hero-login-card')).toBeVisible({ timeout: 120_000 });

    // 从首屏卡片直接发起浏览器登录（不经过设置页）
    const loginWin = await browserLogin(page, electronApp, PHONE, PASSWORD, {
      entryTestId: 'chat-hero-login-btn',
    });

    await expect(page.getByTestId('chat-hero-login-card')).toHaveCount(0, { timeout: 120_000 });
    await expect(page.getByTestId('topbar-account-chip')).toBeVisible({ timeout: 30_000 });
    expect(loginWin.isClosed()).toBe(true);

    await page.screenshot({
      path: 'test-results/qraft-login-entry-hero-logged-in.png',
      fullPage: true,
    });
  });
});
