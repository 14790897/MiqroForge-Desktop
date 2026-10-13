/**
 * 阶段 3-4 验收(SURE)面板 E2E —— 真实 SURE、真实 AI,零 mock。
 *
 * 前置:安装 SURE(查找链 SURE_BIN → PATH → 平台默认位置;CI 的
 * electron-e2e 会装官方 v0.1.2 的 Linux 产物)。找不到时如实 skip,
 * 绝不 mock 顶替。
 *
 * 测试项目是真实的:一个含 `# TODO` 的 py 文件会让确定性检查产出 1 条
 * 真实 finding(not_green),由此走通「检查 → 修复契约 → 交给 AI 真修 →
 * 自动复核」全链路。未配置 analysis provider 时(面板 spawn 的白名单
 * env 没有密钥通道),check 如实给出「检查不足 + 阶段 NOT CHECKED」的
 * 报告——面板如实渲染,断言围绕真实结构,不抠 fixture 数字。
 *
 * 已移除的两个场景(理由如实登记在 PR 描述):
 *   - 「低版本 SURE → 不兼容横幅」:构造低版本必须 mock 二进制(禁令);
 *     版本比较逻辑由 tests/runtime/test_sure_report.py 的 version_at_least
 *     四态单测覆盖。
 *   - 「运行中可取消」:无 provider 的真实检查在秒级内完成,不存在稳定的
 *     取消窗口;取消语义由 sure_task_runtime 的 pytest(进程树回收、取消
 *     后状态)覆盖。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron acceptance-page
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
  waitForResponseComplete,
} from './helpers/electron-setup';
import { findRealSureBin, runSureJson } from './helpers/sure-binary';
import { seedGatewayCredentials } from './helpers/real-ai';

/** 自建临时目录清理:Windows 下刚退出的子进程句柄可能瞬时占用,重试+容忍。 */
function cleanupDir(dir: string): void {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  } catch {
    // TEMP 里的残留由系统兜底;清理失败绝不连累用例结果
  }
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

const NO_SURE_SKIP = '未安装 SURE(查找链 SURE_BIN → PATH → 默认位置)——E2E 不 mock 外部依赖';

test.describe.serial('验收(SURE)面板 E2E · 真实 SURE', () => {
  let electronApp: ElectronApplication | null = null;
  let page: Page;
  let sureBin: string | null = null;
  let project = '';
  let miqiHome = '';

  test.beforeAll(async () => {
    sureBin = findRealSureBin();
    if (!sureBin) return; // 各用例如实 skip;不启动 app

    // 真实测试项目:含 TODO 的 py 文件 → 确定性检查产出 1 条真实 finding,
    // 「交给 AI 修复」有真实素材可修(清理 TODO 后 recheck 将关闭该发现项)
    project = mkdtempSync(join(tmpdir(), 'sure-accept-'));
    writeFileSync(
      join(project, 'README.md'),
      '# acceptance probe\n\nA tiny project checked by the real SURE binary.\n'
    );
    writeFileSync(
      join(project, 'pay.py'),
      '# TODO: implement the payment flow, not finished yet\nraise NotImplementedError("payment")\n'
    );

    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    // 「交给 AI 修复」是真实子代理:本机经平台网关(复制真实凭据),
    // CI 走注入的 config.json provider(无凭据文件时静默跳过)
    seedGatewayCredentials(miqiHome);
    await waitForBridgeInitialized(page, 30);
    // 修复子代理挂靠一个真实聊天会话(空会话不落盘,spawn 需要真实会话)
    await createNewConversation(page);
    await sendMessage(page, '你好');
    await waitForResponseComplete(page);
  });

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp);
    if (miqiHome) cleanupDir(miqiHome);
    // 清理本轮真实 check/recheck 写进 SURE 记录库的会话(真实命令,尽力而为):
    // ① check/recheck 以测试项目为 project_root;② 桥接事件以会话工作区为 project_root
    if (sureBin) {
      for (const scope of [project, miqiHome ? join(miqiHome, 'workspace') : '']) {
        if (!scope) continue;
        const del = runSureJson(sureBin, ['history', 'delete', '--project', scope]);
        console.log(`[test] 清理记录(${scope}): outcome=${del?.outcome ?? '<无输出>'}`);
      }
    }
    if (project) cleanupDir(project);
  });

  test('健康行(真实版本)→ 真实检查报告(结构如实)', async () => {
    if (!sureBin) test.skip(true, NO_SURE_SKIP);
    await openAcceptance(page);
    // 真实二进制的版本(不绑死具体数字,CI 装的官方版与 fork 都应通过)
    await expect(page.getByText(/SURE \d+\.\d+\.\d+/)).toBeVisible({ timeout: 15_000 });

    await page.getByTestId('acceptance-project-input').fill(project);
    await page.getByTestId('acceptance-start').click();

    // 真实检查(无 provider 时秒级完成;给足余量)
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 120_000 });
    // 真实结论因 SURE 构建而异:官方 0.1.2 release 判「检查不足」(TODO 检查
    // 未能运行);fork 构建(main tip)能真跑该检查、判「需要关注」(not_ready)。
    // 断言不锁具体某一版,只要求如实呈现其一——绝不美化
    await expect(page.getByText(/检查不足,无法判断|需要关注/).first()).toBeVisible({
      timeout: 15_000,
    });
    // 真实的 1 条发现项(TODO/FIXME 确定性检查)
    await expect(page.getByText('发现的问题(1)')).toBeVisible({ timeout: 15_000 });
    // 逐阶段明细(真实 12 阶段记录)
    await expect(page.getByText('核查阶段')).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: 'test-results/acceptance-report.png', fullPage: true });
  });

  test('真实修复契约 → 真实复核对比(未修复 → still_open)', async () => {
    if (!sureBin) test.skip(true, NO_SURE_SKIP);
    // serial:承接上一条的真实检查报告态
    await page.getByTestId('acceptance-repair').click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('修复契约(1)')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('必须修复:').first()).toBeVisible();
    // 注:"禁止走捷径"段只在契约含 forbidden_shortcuts 时渲染(随 SURE 构建
    // 与内容而异,官方 0.1.2 的契约有、fork 的这份没有)——不锁它
    await page.screenshot({ path: 'test-results/acceptance-repair.png', fullPage: true });

    // 复核:尚未修复 → 真实对比应如实呈现 still_open(不关闭任何发现)
    await page.getByTestId('acceptance-recheck').click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/发现项只在修复契约点名的检查/)).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: 'test-results/acceptance-recheck.png', fullPage: true });
  });

  test('交给 AI 修复(真实子代理)→ 自动复核对比', async () => {
    if (!sureBin) test.skip(true, NO_SURE_SKIP);
    // serial:上一条结束时 envelope 是 recheck(对比态;该配置下检查"未能运行",
    // 结果如实呈现为「无法确认」,因此没有 still_open 入口)——点「重新核查」
    // 回到 check 报告态,生成契约,再交给 AI 修复
    await page.getByRole('button', { name: /重新核查/ }).click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('acceptance-repair')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('acceptance-repair').click();
    await expect(page.getByText('修复契约(1)')).toBeVisible({ timeout: 60_000 });
    await page.getByTestId('acceptance-fix').click();
    // 通知元素必须出现:真实子代理要么完成,要么如实报错(此配置下契约
    // 要求"允许 SURE 运行该检查",改代码的子代理可能做不到)——两种终态
    // 都接受,但绝不允许"卡住不明"
    await expect(page.getByTestId('acceptance-fix-notice')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('acceptance-fix-notice')).toContainText(
      /修复子代理已完成|修复子代理未成功完成/,
      { timeout: 300_000 }
    );
    // 子代理完成会自动触发复核;报错则手动复核——两条路径汇合到对比视图
    const recheckBtn = page.getByTestId('acceptance-recheck');
    if (await recheckBtn.isVisible().catch(() => false)) {
      await recheckBtn.click();
    }
    await expect(page.getByText(/发现项只在修复契约点名的检查/)).toBeVisible({ timeout: 300_000 });
    await page.screenshot({ path: 'test-results/acceptance-fix-flow.png', fullPage: true });
  });
});
