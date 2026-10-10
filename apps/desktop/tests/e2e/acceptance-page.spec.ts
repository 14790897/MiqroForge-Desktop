/**
 * 阶段 3 验收面板 E2E —— 跨平台、零外部依赖(不要求本机安装 SURE)。
 *
 * SURE_BIN 指向本 spec 生成的包装脚本(win: .cmd / posix: sh),包装内
 * `exec` mock CLI(scripts/mock_sure_cli.py,输出真实采集的报告 fixture)。
 * 覆盖全链路:设置页「验收(SURE)」入口 → 健康行(SURE 9.9.9-mock)→
 * 开始核查 →「运行中 + 已耗时」→ 四块结构化报告 →(第二组)取消态。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron acceptance-page
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { chmodSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  stopMockServer,
  waitForBridgeInitialized,
  waitForResponseComplete,
} from './helpers/electron-setup';
import { resolveMockPython, startMockServer } from './helpers/mock-server';
import { patchConfigForMock } from './helpers/mock-openai';

const REPO_ROOT = resolve(__dirname, '..', '..', '..', '..');
const MOCK_CLI = join(REPO_ROOT, 'scripts', 'mock_sure_cli.py');

/** 生成包装脚本:SURE_BIN 指向它,内部把 mock CLI 以"sure"身份执行。 */
function makeSureWrapper(dir: string, delayMs: number): string {
  const { command, args } = resolveMockPython();
  const argStr = args.map((a) => `"${a}"`).join(' ');
  if (process.platform === 'win32') {
    const wrapper = join(dir, 'sure.cmd');
    writeFileSync(
      wrapper,
      `@echo off\r\nset MOCK_SURE_DELAY_MS=${delayMs}\r\n"${command}" ${argStr} "${MOCK_CLI}" %*\r\n`
    );
    return wrapper;
  }
  const wrapper = join(dir, 'sure');
  writeFileSync(
    wrapper,
    `#!/bin/sh\nexport MOCK_SURE_DELAY_MS=${delayMs}\nexec "${command}" ${argStr} "${MOCK_CLI}" "$@"\n`
  );
  chmodSync(wrapper, 0o755);
  return wrapper;
}

async function openAcceptance(p: Page): Promise<void> {
  const settingsBtn = p.locator('[data-testid="nav-system-settings"]');
  await expect(settingsBtn).toBeVisible({ timeout: 15_000 });
  await settingsBtn.click();
  const tab = p.getByRole('tab', { name: /验收/ }).first();
  await expect(tab).toBeVisible({ timeout: 10_000 });
  await tab.click();
  await expect(p.getByRole('heading', { name: '验收(SURE)' })).toBeVisible({ timeout: 10_000 });
}

test.describe.serial('验收(SURE)面板 E2E · 健康/运行/报告', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let prevSureBin: string | undefined;
  const project = mkdtempSync(join(tmpdir(), 'sure-e2e-proj-'));

  test.beforeAll(async () => {
    const binDir = mkdtempSync(join(tmpdir(), 'sure-e2e-bin-'));
    prevSureBin = process.env.SURE_BIN;
    // 1.2s 延迟:让「运行中 + 已耗时」态可被观察
    process.env.SURE_BIN = makeSureWrapper(binDir, 1200);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page, 30);
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
    // #1273 评审:还原 SURE_BIN,避免同一 worker 上后续 spec 继承 mock 二进制
    // (如 sure-integration-gui 的守卫与默认条目探测都会读它)
    if (prevSureBin === undefined) delete process.env.SURE_BIN;
    else process.env.SURE_BIN = prevSureBin;
  });

  test('健康行 → 运行中 → 四块结构化报告', async () => {
    await openAcceptance(page);

    // 健康检查:mock 的 --version 输出
    await expect(page.getByText(/SURE 9\.9\.9-mock/)).toBeVisible({ timeout: 15_000 });

    await page.getByTestId('acceptance-project-input').fill(project);
    await page.getByTestId('acceptance-start').click();

    // 运行中(延迟窗口内)
    await expect(page.getByTestId('acceptance-running')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('核查中')).toBeVisible();
    await expect(page.getByText('已耗时')).toBeVisible();

    // 结构化报告(真实采集的 findings fixture:not_enough_checked + 5 findings + 5 未验证)
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('检查不足,无法判断')).toBeVisible();
    await expect(page.getByText('not_green · exit 1')).toBeVisible();
    await expect(page.getByText('发现的问题(5)')).toBeVisible();
    await expect(page.getByText('fake payment', { exact: false }).first()).toBeVisible();
    await expect(page.getByText('尚未验证的项目(5)')).toBeVisible();
    await expect(page.getByText('核查阶段')).toBeVisible();

    await page.screenshot({ path: 'test-results/acceptance-report.png', fullPage: true });
  });

  test('生成修复契约 → 复核对比(阶段 4;承接上一条的报告态)', async () => {
    // describe.serial:上一条用例结束时处于 check 报告态,操作行含「生成修复契约」
    await page.getByTestId('acceptance-repair').click();
    await expect(page.getByTestId('acceptance-running')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/生成修复契约中/)).toBeVisible();

    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('修复契约(5)')).toBeVisible();
    await expect(page.getByText('必须修复:').first()).toBeVisible();
    await expect(page.getByText('禁止走捷径:').first()).toBeVisible();
    await expect(page.getByText('fake payment', { exact: false }).first()).toBeVisible();
    await page.screenshot({ path: 'test-results/acceptance-repair.png', fullPage: true });

    // 复核:mock recheck fixture = still_open 5 / closed 0(「删标记但不跑检查 = 仍 open」语义)
    await page.getByTestId('acceptance-recheck').click();
    await expect(page.getByText(/复审对比中/)).toBeVisible({ timeout: 10_000 });
    // 对比区独有文案(避免子串撞上运行中卡片),确保截图是终态
    await expect(page.getByText(/发现项只在修复契约点名的检查/)).toBeVisible({
      timeout: 30_000,
    });
    await expect(
      page.getByText('project contains fake payment', { exact: false }).first()
    ).toBeVisible();
    await page.screenshot({ path: 'test-results/acceptance-recheck.png', fullPage: true });
  });
});

test.describe.serial('验收(SURE)面板 E2E · 取消', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let prevSureBin: string | undefined;
  const project = mkdtempSync(join(tmpdir(), 'sure-e2e-proj-slow-'));

  test.beforeAll(async () => {
    const binDir = mkdtempSync(join(tmpdir(), 'sure-e2e-bin-slow-'));
    prevSureBin = process.env.SURE_BIN;
    // 30s 延迟:留足点击「取消核查」的窗口
    process.env.SURE_BIN = makeSureWrapper(binDir, 30_000);
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page, 30);
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
    // #1273 评审:还原 SURE_BIN(同上)
    if (prevSureBin === undefined) delete process.env.SURE_BIN;
    else process.env.SURE_BIN = prevSureBin;
  });

  test('运行中可取消 → 已取消态(取消不产出报告)', async () => {
    await openAcceptance(page);

    await page.getByTestId('acceptance-project-input').fill(project);
    await page.getByTestId('acceptance-start').click();
    await expect(page.getByTestId('acceptance-running')).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /取消核查/ }).click();
    await expect(page.getByTestId('acceptance-cancelled')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('acceptance-report')).not.toBeVisible();

    await page.screenshot({ path: 'test-results/acceptance-cancelled.png', fullPage: true });
  });
});

test.describe.serial('验收(SURE)面板 E2E · 交给 AI 修复(阶段 4)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let prevSureBin: string | undefined;
  let mockProc: ChildProcess | undefined;
  const project = mkdtempSync(join(tmpdir(), 'sure-e2e-fix-'));

  test.beforeAll(async () => {
    const binDir = mkdtempSync(join(tmpdir(), 'sure-e2e-bin-fix-'));
    prevSureBin = process.env.SURE_BIN;
    process.env.SURE_BIN = makeSureWrapper(binDir, 400);

    // 修复子代理是真实 agent(agent_jobs → turn_runner):用静态 LLM mock 收尾,
    // 零工具调用、不依赖任何真实模型;子代理工作区挂靠聊天会话
    const mock = await startMockServer('mock_llm_reply.py');
    mockProc = mock.proc;
    const fixture = await launchElectronApp((cfg) => patchConfigForMock(cfg, mock.mockUrl));
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page, 30);

    await createNewConversation(page);
    await sendMessage(page, '你好');
    await waitForResponseComplete(page);
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
    if (mockProc) stopMockServer(mockProc);
    // #1273 评审:还原 SURE_BIN(同上)
    if (prevSureBin === undefined) delete process.env.SURE_BIN;
    else process.env.SURE_BIN = prevSureBin;
  });

  test('交给 AI 修复 → 子代理完成 → 自动复核对比', async () => {
    await openAcceptance(page);
    await page.getByTestId('acceptance-project-input').fill(project);
    await page.getByTestId('acceptance-start').click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 30_000 });

    await page.getByTestId('acceptance-repair').click();
    await expect(page.getByText('修复契约(5)')).toBeVisible({ timeout: 30_000 });

    await page.getByTestId('acceptance-fix').click();
    // 通知元素必须出现;但子代理可能极快完成并把"已启动"立刻改写为"已完成"——
    // 只断言两态之一,不锁中间文案(否则与真实速度赛跑,已实测踩中)
    await expect(page.getByTestId('acceptance-fix-notice')).toBeVisible({ timeout: 15_000 });
    // 子代理(mock LLM,零工具调用)→ subagent_result(ok)→ 页面自动复核
    // (聊天区与子代理卡也有同款文案,断言收敛到本页通知元素)
    await expect(page.getByTestId('acceptance-fix-notice')).toContainText(
      /修复子代理已启动|修复子代理已完成/,
      { timeout: 150_000 }
    );
    // 等自动复核的最终对比视图(独有文案),截图定格在终态
    await expect(page.getByText(/发现项只在修复契约点名的检查/)).toBeVisible({
      timeout: 60_000,
    });
    await page.screenshot({ path: 'test-results/acceptance-fix-flow.png', fullPage: true });
  });
});
