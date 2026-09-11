/**
 * Cron Page E2E — 定时任务全链路（Issue #113）
 *
 * 覆盖（test.describe.serial，共享一次 Electron 实例）：
 *   1. 创建 at / every / cron 三种调度任务 → 列表显示正确
 *   2. 空 cron 表达式 → 前端校验拦截（不调用 IPC）
 *   3. toggle 启停 → 状态翻转（运行中 ↔ 已禁用）
 *   4. 手动 run → 「最近执行记录」出现该任务（UI 断言，不读 store）
 *   5. 重启恢复 → relaunch 后任务仍在、enabled 状态保留
 *   6. 删除 → 列表移除
 *
 * 设计要点（外部评审共识）：
 *   - 真实 IPC（window.miqi.cron.*），不 mock —— 覆盖 preload→IPC→Python 全链路
 *   - 不等待真实调度触发：at 时间戳设为 +10min，执行路径用手动 run 覆盖
 *   - 断言看 UI（最近执行记录/状态标签），不读后端 store 文件
 *   - 依赖 #764（设置页 cron tab 入口）；#765 合入后本分支 rebase 掉 cherry-pick
 *
 * Run:
 *   cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron cron-page.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  launchElectronApp,
  relaunchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

// at 调度时间戳：+10 分钟，确保测试期间调度器不会真触发（避免污染 runs 断言）
const TEN_MINUTES_MS = 10 * 60 * 1000;
// every 间隔：1 小时——测试期间绝不触发（60000ms 在慢 CI 上可能真跑）
const ONE_HOUR_MS = 60 * 60 * 1000;

test.describe.serial('Cron Page E2E (#113)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page, 30);
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  // ── helpers ────────────────────────────────────────────────────────────

  /** 打开系统设置 → 定时任务 tab。依赖 #764 的入口。 */
  async function openCronPage(p: Page) {
    const settingsBtn = p.locator('[data-testid="nav-system-settings"]');
    await expect(settingsBtn).toBeVisible({ timeout: 15_000 });
    await settingsBtn.click();
    // 设置页侧边栏出现「定时任务」tab 并点击（Radix Tabs 是 role=tab）
    const cronTab = p.getByRole('tab', { name: /定时任务/ }).first();
    await expect(cronTab).toBeVisible({ timeout: 10_000 });
    await cronTab.click();
    // CronPage 标题出现（说明已渲染）
    await expect(p.getByRole('heading', { name: '定时任务' })).toBeVisible({ timeout: 10_000 });
  }

  /** 打开「创建任务」弹窗（顶部按钮） */
  async function openCreateModal(p: Page) {
    await p.getByRole('button', { name: '创建任务' }).click();
    await expect(p.getByRole('heading', { name: '创建任务' })).toBeVisible({ timeout: 5_000 });
  }

  /** 提交创建表单 */
  async function submitCreate(p: Page) {
    // 弹窗底部提交按钮文案是「创建」（isEdit=false）
    await p.getByRole('button', { name: '创建', exact: true }).click();
  }

  /** 在列表中按名称找任务行（data-testid 定位，避免 role-name 歧义） */
  function jobRow(p: Page, name: string) {
    return p.locator(`[data-testid="cron-job-row-${name}"]`).first();
  }

  /** 任务行内的操作按钮（禁用/立即执行/删除） */
  function rowAction(p: Page, name: string, title: string) {
    return jobRow(p, name).locator(`button[title="${title}"]`);
  }

  /** 等待列表刷新后某任务出现 */
  async function expectJobVisible(p: Page, name: string) {
    await expect(jobRow(p, name)).toBeVisible({ timeout: 10_000 });
  }

  // ── 1. 创建三种调度任务 ────────────────────────────────────────────────

  test('创建 at / every / cron 三种调度任务并显示', async () => {
    await openCronPage(page);

    // at 任务：名称 + at 时间戳（+10min）
    await openCreateModal(page);
    await page.getByPlaceholder('例如：每日报告').fill('e2e-at-job');
    await page.getByRole('button', { name: 'at', exact: true }).click();
    const atInput = page.getByPlaceholder(String(Date.now() + 60000));
    // placeholder 是动态的（Date.now()+60000），用 label 定位数字输入
    await page
      .locator('input[type="number"]')
      .first()
      .fill(String(Date.now() + TEN_MINUTES_MS));
    await page.getByPlaceholder('任务触发时 Agent 应执行的操作…').fill('执行 at 任务');
    await submitCreate(page);
    await expectJobVisible(page, 'e2e-at-job');

    // every 任务：1 小时间隔（测试期间绝不触发）
    await openCreateModal(page);
    await page.getByPlaceholder('例如：每日报告').fill('e2e-every-job');
    await page.getByRole('button', { name: 'every', exact: true }).click();
    await page.getByPlaceholder('60000').fill(String(ONE_HOUR_MS));
    await page.getByPlaceholder('任务触发时 Agent 应执行的操作…').fill('执行 every 任务');
    await submitCreate(page);
    await expectJobVisible(page, 'e2e-every-job');

    // cron 表达式任务：0 9 * * *
    await openCreateModal(page);
    await page.getByPlaceholder('例如：每日报告').fill('e2e-cron-job');
    await page.getByRole('button', { name: 'cron', exact: true }).click();
    await page.getByPlaceholder('0 9 * * *').fill('0 9 * * *');
    await page.getByPlaceholder('UTC').fill('Asia/Shanghai');
    await page.getByPlaceholder('任务触发时 Agent 应执行的操作…').fill('执行 cron 任务');
    await submitCreate(page);
    await expectJobVisible(page, 'e2e-cron-job');

    // 三个任务都存在且状态为「运行中」（默认 enabled）
    await expect(jobRow(page, 'e2e-at-job')).toContainText('运行中', { timeout: 5_000 });
    await expect(jobRow(page, 'e2e-every-job')).toContainText('运行中', { timeout: 5_000 });
    await expect(jobRow(page, 'e2e-cron-job')).toContainText('运行中', { timeout: 5_000 });
  });

  // ── 2. 空 cron 表达式被前端拦截 ────────────────────────────────────────

  test('空 cron 表达式 → 前端校验拦截，不创建', async () => {
    await openCreateModal(page);
    await page.getByPlaceholder('例如：每日报告').fill('e2e-empty-expr');
    await page.getByRole('button', { name: 'cron', exact: true }).click();
    // 不填表达式，直接提交
    await submitCreate(page);
    // 弹窗应仍打开（创建失败），且列表无该任务
    await expect(page.getByRole('heading', { name: '创建任务' })).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('e2e-empty-expr')).not.toBeVisible({ timeout: 5_000 });
    // 关闭弹窗
    await page.getByRole('button', { name: '取消' }).click();
  });

  // ── 3. toggle 启停 ─────────────────────────────────────────────────────

  test('toggle 启停 → 状态在 运行中 ↔ 已禁用 间翻转', async () => {
    await expect(jobRow(page, 'e2e-every-job')).toBeVisible({ timeout: 10_000 });
    await expect(jobRow(page, 'e2e-every-job')).toContainText('运行中', { timeout: 5_000 });

    // 禁用（title=禁用 → 点击后状态变已禁用）
    await rowAction(page, 'e2e-every-job', '禁用').click();
    await expect(jobRow(page, 'e2e-every-job')).toContainText('已禁用', { timeout: 10_000 });

    // 重新启用（title=启用）
    await rowAction(page, 'e2e-every-job', '启用').click();
    await expect(jobRow(page, 'e2e-every-job')).toContainText('运行中', { timeout: 10_000 });
  });

  // ── 4. 手动 run → 最近执行记录 ─────────────────────────────────────────

  test('手动 run → 最近执行记录出现该任务', async () => {
    await expect(jobRow(page, 'e2e-at-job')).toBeVisible({ timeout: 10_000 });

    // 点击「立即执行」
    await rowAction(page, 'e2e-at-job', '立即执行').click();

    // 最近执行记录面板出现（runs.length > 0 才渲染），且面板内含该任务名
    const runsPanel = page.locator('div.rounded-xl', { hasText: '最近执行记录' }).first();
    await expect(runsPanel).toBeVisible({ timeout: 15_000 });
    await expect(runsPanel.getByText('e2e-at-job', { exact: true })).toBeVisible({
      timeout: 10_000,
    });
  });

  // ── 5. 重启恢复 ────────────────────────────────────────────────────────

  test('重启后任务仍在且 enabled 状态保留', async () => {
    // 先关闭第一个实例（避免同 MIQI_HOME 双实例抢锁/bridge），keepHome=true 保留持久化数据
    await closeElectronApp(electronApp, miqiHome, true);
    // relaunch 同一 miqiHome（持久化数据保留）
    const fixture2 = await relaunchElectronApp(miqiHome);
    electronApp = fixture2.electronApp;
    page = fixture2.page;
    await waitForBridgeInitialized(page, 30);

    await openCronPage(page);
    // 三个任务都还在
    await expectJobVisible(page, 'e2e-at-job');
    await expectJobVisible(page, 'e2e-every-job');
    await expectJobVisible(page, 'e2e-cron-job');
    // every 任务 enabled 状态保留（上一用例重新启用了）
    await expect(jobRow(page, 'e2e-every-job')).toContainText('运行中', { timeout: 10_000 });
  });

  // ── 6. 删除 ────────────────────────────────────────────────────────────

  test('删除任务 → 列表移除', async () => {
    await openCronPage(page);
    await expectJobVisible(page, 'e2e-at-job');

    // 点击删除
    await rowAction(page, 'e2e-at-job', '删除').click();
    // 列表刷新后任务消失
    await expect(jobRow(page, 'e2e-at-job')).not.toBeVisible({ timeout: 10_000 });
  });
});
