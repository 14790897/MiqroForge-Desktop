/**
 * E2E: 系统包安装授权审计渲染（issue #935）
 *
 * 「允许并记住」把持久的、root 级的包管理能力交给模型，这是本仓库里最重的
 * 一次授权，必须留下可追溯的记录。本 spec 走真实链路验证「记录 → 接口 →
 * 界面」全段：
 *
 *   预置 <MIQI_HOME>/system_install_audit.jsonl（一条「允许并记住」+ 结果行、
 *   一条「拒绝」）→ 桥启动时 init_audit_file 载入 → approvals.history 合并
 *   返回并带 source 标记 → 审批页历史记录按来源渲染决策/归一化命令/结果。
 *
 * 刻意不依赖真实模型、WSL 或沙箱：弹卡那一段（record_authorization 的调用
 * 点、归一化命令的来源、persist/runtime 状态）由
 * tests/sandbox/test_system_install_routing.py 与
 * tests/agent/test_system_install_audit.py 覆盖，这里只钉住「存下来的行能被
 * 读出来并正确显示」——没有这一条，审计文件写对了用户也看不见。
 *
 * Run:
 *   cd apps/desktop && npm run build &&
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *     --config=playwright.config.ts --project=electron -g "935"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  launchElectronApp,
  relaunchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

const GRANTED_COMMAND = 'apt-get install -y texlive-xetex';
const DENIED_COMMAND = 'apt-get install -y evilpkg';

/** 授权行 + 结果行（同 grant_id）与一条拒绝，覆盖两种表决。 */
function seedRows(): string[] {
  const t = Date.now() / 1000;
  return [
    {
      id: 'e2e-auth-granted',
      kind: 'authorization',
      grant_id: 'e2e-grant-granted',
      timestamp: t - 60,
      session_key: 'e2e-client:e2e-session',
      thread_id: 'e2e-thread',
      turn_id: 'e2e-turn',
      decision: 'always',
      command: GRANTED_COMMAND,
      persist_failed: false,
      runtime_failed: false,
    },
    {
      id: 'e2e-result-granted',
      kind: 'result',
      grant_id: 'e2e-grant-granted',
      timestamp: t - 30,
      exit_code: 0,
      success: true,
      duration_ms: 12_300,
      reason: '',
    },
    {
      id: 'e2e-auth-denied',
      kind: 'authorization',
      grant_id: 'e2e-grant-denied',
      timestamp: t - 10,
      session_key: 'e2e-client:e2e-session',
      thread_id: 'e2e-thread',
      turn_id: 'e2e-turn',
      decision: 'deny',
      command: DENIED_COMMAND,
      persist_failed: false,
      runtime_failed: false,
    },
  ].map((row) => JSON.stringify(row));
}

test.describe('#935 系统包安装授权审计', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 审计文件必须在桥启动前就位——init_audit_file 只在桥启动时读一次。
    // 所以先冷启一次只为拿到本轮临时 MIQI_HOME，写好文件再重启；
    // keepHome=true 才能保住这个目录（默认 close 会连目录一起删）。
    const first = await launchElectronApp();
    miqiHome = first.miqiHome;
    await closeElectronApp(first.electronApp, miqiHome, true);
    writeFileSync(
      join(miqiHome, 'system_install_audit.jsonl'),
      `${seedRows().join('\n')}\n`,
      'utf-8'
    );

    const fixture = await relaunchElectronApp(miqiHome);
    electronApp = fixture.electronApp;
    page = fixture.page;
  }, 300_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('审计行出现在审批历史（来源/决策/归一化命令/结果）', async () => {
    await waitForBridgeInitialized(page);

    // 审批页挂在「设置 → 审批」tab 下
    await page.getByTestId('nav-system-settings').click();
    await page.getByRole('tab', { name: '审批' }).click();
    await page.getByRole('button', { name: '历史记录' }).click();

    // 两个来源区分开：预置的审计行必须带「系统包安装」标记，
    // 而不是被当普通危险命令历史吞掉。
    await expect(page.getByText('系统包安装').first()).toBeVisible({ timeout: 30_000 });

    // 「允许并记住」+ 归一化命令 + 结果
    await expect(page.getByText('永久允许').first()).toBeVisible();
    await expect(page.getByText(GRANTED_COMMAND)).toBeVisible();
    await expect(page.getByText('安装成功')).toBeVisible();

    // 拒绝也留痕（只记授权的话，「用户被问过没有、答了什么」无从查证）
    await expect(page.getByText('已拒绝').first()).toBeVisible();
    await expect(page.getByText(DENIED_COMMAND)).toBeVisible();

    // 截图留证据（PR 描述里的界面截图）
    await page.screenshot({ path: 'test-reports/issue-935-install-audit.png' });
  });

  test('展开授权行可见授权状态与耗时', async () => {
    await waitForBridgeInitialized(page);
    await page.getByTestId('nav-system-settings').click();
    await page.getByRole('tab', { name: '审批' }).click();
    await page.getByRole('button', { name: '历史记录' }).click();

    await page.getByText(GRANTED_COMMAND).click();

    await expect(page.getByText('结果：')).toBeVisible();
    await expect(page.getByText('耗时：')).toBeVisible();
    await expect(page.getByText('12.3s')).toBeVisible();
    // 预置行 persist/runtime 都正常 → 不显示「授权状态」告警行
    await expect(page.getByText('授权状态：')).toHaveCount(0);

    await page.screenshot({ path: 'test-reports/issue-935-install-audit-expanded.png' });
  });
});
