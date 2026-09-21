/**
 * MiQroForge 浏览器登录 E2E（真实平台，issue #726）。
 *
 * 完整链路：设置页点「浏览器登录」→ 主进程打开 MiQroForge 授权窗口（独立
 * partition）→ 未登录被 302 到平台登录页 → 填入测试账号登录 → 主进程
 * 检测到登录态 cookie 后把窗口带回授权流程 → 服务端 302 回调
 * redirect_uri?code → 主进程拦截 code → 换 token + userinfo → 应用内
 * 完成登录 → 退出登录清理。
 *
 * 走应用当前默认环境（#1142 起为生产 www.miqroforge.com，平台已把测试
 * 环境并入生产）。账号昵称由平台账号决定，断言不写死具体昵称。
 *
 * 凭据不写入仓库：需设置 QRAFT_PHONE / QRAFT_PASSWORD 环境变量，
 * 未设置时自动跳过（CI 默认跳过）。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, rmSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  browserLogin,
  type ElectronFixture,
} from './helpers/electron-setup';

const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';

const STORE_ENV = 'MIQI_QRAFT_STORE';

let storePath: string;

test.describe('MiQroForge 浏览器登录 E2E（真实平台）', () => {
  test.skip(!PHONE || !PASSWORD, '需要 QRAFT_PHONE / QRAFT_PASSWORD 环境变量');

  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    storePath = join(tmpdir(), `qraft-browser-e2e-${process.pid}.json`);
    process.env[STORE_ENV] = storePath;
    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
    if (existsSync(storePath)) rmSync(storePath, { force: true });
  });

  test('浏览器登录：MiQroForge 页面完成登录 → 自动回到授权 → 应用内完成登录', async () => {
    test.setTimeout(300_000);

    // 点浏览器登录 → 主进程打开 MiQroForge 授权窗口 → 填测试账号登录 →
    // 拦截回调 code 换 token → 设置页出现已登录账号信息（helper 全链路）
    const loginWin = await browserLogin(page, electronApp, PHONE, PASSWORD);

    // 账号信息区渲染「<昵称> 已登录」徽标（昵称由平台账号决定，不写死）；
    // 环境显示生产（#1142：默认登录环境已由 test 改为 prod）
    await expect(page.getByText(/\S+\s*已登录/).first()).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText(/环境\s*生产/)).toBeVisible();
    await expect(page.getByTestId('qraft-logout-btn')).toBeVisible();

    // 授权窗口已在完成时自动关闭
    expect(loginWin.isClosed()).toBe(true);

    await page.screenshot({
      path: 'test-results/qraft-browser-login-success.png',
      fullPage: true,
    });

    // 退出登录：回到登录入口，磁盘凭据清空
    await page.getByTestId('qraft-logout-btn').click();
    await expect(page.getByTestId('qraft-browser-login-btn')).toBeVisible({ timeout: 15_000 });
  });
});
