/**
 * Issue #726 — MiQroForge 平台 OAuth2 登录设置页（smoke，mock bridge）。
 *
 * 覆盖：未登录时只展示浏览器登录（OAuth）入口、登录成功展示账号、
 * 登录失败错误提示、requiresRelogin 横幅、退出登录回到登录入口。
 */

import { expect, test } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

async function gotoQraftTab(
  page: import('@playwright/test').Page,
  opts?: Parameters<typeof buildMockBridgeScript>[0]
) {
  await page.addInitScript({ content: buildMockBridgeScript(opts) });
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page
    .getByRole('tab')
    .filter({ hasText: /MiQroForge/ })
    .click();
}

test.describe('Issue #726 MiQroForge 平台登录设置页', () => {
  test('未登录时只展示浏览器登录（OAuth）入口，手机号/密码表单与高级设置已隐藏', async ({
    page,
  }) => {
    await gotoQraftTab(page);

    // 浏览器登录入口（MiQroForge 授权页：用户在页面点击"同意"）
    await expect(page.getByTestId('qraft-browser-login-btn')).toBeVisible();
    // #1000：入口文案明确为「登录 MiQroForge 账号」
    await expect(page.getByTestId('qraft-browser-login-btn')).toContainText('登录 MiQroForge 账号');
    // 手机号/密码表单、提交按钮、环境选择与高级设置均不渲染
    await expect(page.getByTestId('qraft-phone-input')).toHaveCount(0);
    await expect(page.getByTestId('qraft-password-input')).toHaveCount(0);
    await expect(page.getByTestId('qraft-login-btn')).toHaveCount(0);
    await expect(page.getByRole('button', { name: '测试环境' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '生产环境' })).toHaveCount(0);
    await expect(page.getByText('高级设置（接入配置，默认按环境预填）')).toHaveCount(0);

    await page.screenshot({ path: 'test-results/issue-726/qraft-login-form.png', fullPage: true });
  });

  test('浏览器登录成功展示账号信息（mock 走 browserLogin IPC）', async ({ page }) => {
    await gotoQraftTab(page);

    await page.getByTestId('qraft-browser-login-btn').click();

    // 账号信息（nickname 来自 mock userinfo）
    await expect(page.getByText('MiQi测试').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('已登录')).toBeVisible();
    await expect(page.getByTestId('qraft-logout-btn')).toBeVisible();
  });

  test('浏览器登录被取消时展示中性提示而非错误', async ({ page }) => {
    await gotoQraftTab(page, {
      qraftLoginResult: {
        ok: false,
        code: 'LOGIN_CANCELLED',
        message: '已取消：登录窗口在完成授权前被关闭',
      },
    });

    await page.getByTestId('qraft-browser-login-btn').click();

    await expect(page.getByTestId('qraft-browser-notice')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('qraft-browser-notice')).toContainText('已取消');
    // 取消不是错误，不应出现红色错误框
    await expect(page.getByTestId('qraft-login-error')).toHaveCount(0);
  });

  test('登录成功展示账号信息（nickname/username/脱敏手机号）与退出按钮', async ({ page }) => {
    await gotoQraftTab(page);

    await page.getByTestId('qraft-browser-login-btn').click();

    // 账号信息（nickname 来自 mock userinfo）
    await expect(page.getByText('MiQi测试').first()).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('已登录')).toBeVisible();
    // 手机号脱敏展示（185****0000），不出现完整号码
    await expect(page.getByText(/185\*{4}0000/)).toBeVisible();
    await expect(page.getByText(/18500000000/)).toHaveCount(0);
    // token 到期 / 自动刷新时间与退出按钮
    await expect(page.getByText('access_token 到期：')).toBeVisible();
    await expect(page.getByText('计划自动刷新：')).toBeVisible();
    await expect(page.getByTestId('qraft-logout-btn')).toBeVisible();
    await expect(page.getByTestId('qraft-refresh-btn')).toBeVisible();

    await page.screenshot({ path: 'test-results/issue-726/qraft-logged-in.png', fullPage: true });
  });

  test('登录后展示积分余额（可用/累计获得/累计支出）', async ({ page }) => {
    await gotoQraftTab(page, {
      qraftPointsResult: {
        ok: true,
        points: { availablePoints: 270, heldPoints: 0, totalEarned: 300, totalSpent: 30 },
      },
    });

    await page.getByTestId('qraft-browser-login-btn').click();

    await expect(page.getByTestId('qraft-points-balance')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('qraft-points-value')).toHaveText('270');
    await expect(page.getByText('累计获得 300，累计支出 30')).toBeVisible();
    await expect(page.getByText('Slurm MCP 作业每次运行消耗 10 积分')).toBeVisible();

    await page.screenshot({
      path: 'test-results/issue-726/qraft-points-balance.png',
      fullPage: true,
    });
  });

  test('积分余额拉取失败展示错误提示', async ({ page }) => {
    await gotoQraftTab(page, {
      qraftStatus: {
        loggedIn: true,
        account: {
          phone: '18500000000',
          sub: '19',
          username: 'U-HKY4-GB4E',
          nickname: 'MiQi测试',
        },
        env: 'test',
        expiresAt: Date.now() + 7_199_000,
        refreshScheduledAt: Date.now() + 6_299_000,
      },
      qraftPointsResult: {
        ok: false,
        code: 'POINTS_FAILED',
        message: '查询积分余额失败：网络不可达',
      },
    });

    await expect(page.getByTestId('qraft-points-balance')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('查询积分余额失败：网络不可达')).toBeVisible();
  });

  test('登录失败展示错误提示与修复指引（IP 未加白示例）', async ({ page }) => {
    await gotoQraftTab(page, {
      qraftLoginResult: {
        ok: false,
        code: 'IP_NOT_WHITELISTED',
        message: '出口 IP 未加白，请联系 MiQroForge 管理员',
      },
    });

    await page.getByTestId('qraft-browser-login-btn').click();

    const errorBox = page.getByTestId('qraft-login-error');
    await expect(errorBox).toBeVisible({ timeout: 5000 });
    await expect(errorBox).toContainText('出口 IP 未加白');
  });

  test('登录态过期（requiresRelogin）时展示重新登录横幅', async ({ page }) => {
    await gotoQraftTab(page, {
      qraftStatus: {
        loggedIn: true,
        account: {
          phone: '18500000000',
          sub: '19',
          username: 'U-HKY4-GB4E',
          nickname: 'MiQi测试',
        },
        env: 'test',
        expiresAt: Date.now() - 60_000,
        refreshError: 'REFRESH_FAILED',
        requiresRelogin: true,
      },
    });

    await expect(page.getByTestId('qraft-relogin-banner')).toBeVisible();
    await expect(page.getByTestId('qraft-relogin-banner')).toContainText('登录已过期');
    // 已登录卡片与退出按钮仍可用，用户可退出后重新登录
    await expect(page.getByTestId('qraft-logout-btn')).toBeVisible();
  });

  test('退出登录清除状态并回到登录入口', async ({ page }) => {
    await gotoQraftTab(page, {
      qraftStatus: {
        loggedIn: true,
        account: {
          phone: '18500000000',
          sub: '19',
          username: 'U-HKY4-GB4E',
          nickname: 'MiQi测试',
        },
        env: 'test',
        expiresAt: Date.now() + 7_199_000,
        refreshScheduledAt: Date.now() + 6_299_000,
      },
    });

    await expect(page.getByTestId('qraft-logout-btn')).toBeVisible();
    await page.getByTestId('qraft-logout-btn').click();

    await expect(page.getByTestId('qraft-browser-login-btn')).toBeVisible({ timeout: 5000 });
  });
});
