/**
 * Privacy consent gate E2E (issue #837) — 首次启动隐私协议确认门。
 *
 * 覆盖四条路径（全部禁用 MIQI_E2E 绕过，走真实确认门）：
 *  1. 拒绝并退出：首次启动展示协议 → 点「拒绝并退出」→ 应用退出；
 *  2. 同意进入：重启（同一 MIQI_HOME，同意未持久化）→ 门再次出现 →
 *     滚到底部并停留 → 「同意并继续」启用 → 点击 → 主界面加载；
 *  3. 同意持久化：page.reload() 重挂载 AppShell → 门不再出现（同
 *     readStoredConsent/门判定路径；不重启进程——CI 上 close 后
 *     relaunch 存在桥接端口残留，主界面长期不加载）；
 *  4. 应用内入口：设置 → 隐私协议 页可随时查阅协议文本。
 *
 * serial 模式：测试共享同一个 MIQI_HOME 的同意状态，必须按序执行
 * （playwright.config.ts 全局 fullyParallel）。
 *
 * 确定性说明：dev 模式下 userData 按 checkout 共享（main 的 ws-<hash>
 * setPath 覆盖 --user-data-dir），本 checkout 此前运行/重试留下的同意
 * 状态会让门被跳过——依赖门的测试先清记录、必要时重启一次。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "Privacy consent"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, relaunchElectronApp, closeElectronApp } from './helpers/electron-setup';

/** 清掉共享 userData 里的同意记录（幂等）。 */
async function clearStoredConsent(page: Page) {
  await page.evaluate(() => {
    try {
      localStorage.removeItem('miqi:privacyConsentVersion');
    } catch {
      /* ignore */
    }
  });
}

test.describe.serial('Privacy consent gate (#837)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('拒绝并退出：应用直接退出', { timeout: 240_000 }, async () => {
    // 不设 MIQI_E2E → 主进程不下发 --miqi-e2e → 渲染层展示真实确认门
    const fixture = await launchElectronApp(undefined, { noConsentBypass: true });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // 清掉历史运行残留的同意记录；若本次启动已跳过门（旧同意生效），
    // 重启一次让门按清理后的状态出现。
    await clearStoredConsent(page);
    if ((await page.getByTestId('privacy-consent-gate').count()) === 0) {
      await closeElectronApp(electronApp, miqiHome, true);
      const fresh = await relaunchElectronApp(miqiHome, { noConsentBypass: true });
      electronApp = fresh.electronApp;
      page = fresh.page;
    }

    await expect(page.getByTestId('privacy-consent-gate')).toBeVisible({ timeout: 60_000 });
    // 协议文本含版本号（中英文均显示 1.0）
    await expect(page.getByTestId('privacy-consent-text')).toContainText('1.0');

    const closed = electronApp.waitForEvent('close', { timeout: 20_000 }).catch(() => null);
    await page.getByTestId('privacy-consent-decline').click();

    // 拒绝走主进程 app.quit()（macOS 上 window.close 不终止应用）
    expect(await closed).not.toBeNull();
  });

  test('同意并继续：主界面加载', { timeout: 240_000 }, async () => {
    // 上一测试未同意（拒绝退出）→ 门再次出现
    const fixture = await relaunchElectronApp(miqiHome, { noConsentBypass: true });
    electronApp = fixture.electronApp;
    page = fixture.page;

    // 重试确定性：若上一次尝试在同意后失败（共享 userData 已存同意记录），
    // 门不会出现——先清记录，必要时再重启一次。
    await clearStoredConsent(page);
    if ((await page.getByTestId('privacy-consent-gate').count()) === 0) {
      await closeElectronApp(electronApp, miqiHome, true);
      const fresh = await relaunchElectronApp(miqiHome, { noConsentBypass: true });
      electronApp = fresh.electronApp;
      page = fresh.page;
    }

    await expect(page.getByTestId('privacy-consent-gate')).toBeVisible({ timeout: 60_000 });

    // 下拉到底并停留确认：滚动到底部前「同意并继续」必须禁用
    const agreeBtn = page.getByTestId('privacy-consent-agree');
    const scrollBox = page.getByTestId('privacy-consent-scroll');
    await expect(agreeBtn).toBeDisabled();

    // 回归：停留计时中滚动容器尺寸变化（窗口缩放/内容重排经 ResizeObserver
    // 触发 checkOverflow）应重置到底状态，不能靠旧计时放行同意
    // （CodeRabbit 评审场景）。直接缩小容器模拟——窗口级 resize 在 macOS CI
    // 上受屏幕分辨率钳制不可靠，而容器是 ResizeObserver 的直接观察对象。
    await scrollBox.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
    // 倒计时：滚到底后按钮显示剩余时间倒计时（如 "…(3.0s)"）并保持禁用
    await expect(agreeBtn).toContainText(/\(\d+\.\ds\)/);
    await expect(agreeBtn).toBeDisabled();
    // 倒计时显示中的截图（PR 证据）
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-consent-countdown.png`,
      fullPage: true,
    });
    await scrollBox.evaluate((el) => {
      el.style.maxHeight = '40vh'; // 缩小容器 → 溢出增多，此前「到底」失效
    });
    // 计时与倒计时一同被 resize 清除，按钮回到无倒计时的禁用态
    await expect(agreeBtn).not.toContainText('(');
    // 等过停留时长（3s）——计时已被 resize 清除，按钮保持禁用
    await page.waitForTimeout(3300);
    await expect(agreeBtn).toBeDisabled();

    // 恢复容器尺寸：scrollTop 被浏览器钳制回底部（不产生 scroll 事件），
    // 组件在尺寸变化回调里重新评估并重启停留计时 → 倒计时走完自动启用
    await scrollBox.evaluate((el) => {
      el.style.maxHeight = '';
    });
    await expect(agreeBtn).toBeEnabled({ timeout: 10_000 });
    // 倒计时归零后按钮恢复原文案
    await expect(agreeBtn).not.toContainText('(');

    // 确认门本身的截图（滚动到底、按钮已启用）
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-consent-gate.png`,
      fullPage: true,
    });

    await agreeBtn.click();

    // #1000：同意后直接衔接登录页（协议 → 登录一气呵成），入口不再藏在设置页。
    await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId('login-step-login-btn')).toBeVisible();
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-login-step.png`,
      fullPage: true,
    });

    // 暂不登录进入应用
    await page.getByTestId('login-step-skip').click();

    // CI 冷启动（bridge + python.check）较慢，给足时间
    await expect(page.getByTestId('app-title')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('privacy-consent-gate')).toHaveCount(0);
    // #1000：进入应用后首屏（空会话欢迎区）有登录卡片入口
    await expect(page.getByTestId('chat-hero-login-card')).toBeVisible({ timeout: 60_000 });
    // 同意版本已写入 localStorage
    const stored = await page.evaluate(() => localStorage.getItem('miqi:privacyConsentVersion'));
    expect(stored).toBe('1.0');

    // 实例保持运行，供测试 3/4 复用（避免 close 后 relaunch 的桥接端口残留）
  });

  test('同意持久化：重挂载后不再展示确认门', { timeout: 180_000 }, async () => {
    // 不重启进程：CI 上 close 后 relaunch 存在桥接端口残留，主界面长期
    // 不加载。page.reload() 重新执行渲染层入口，AppShell 重新挂载，
    // 走与冷启动完全相同的 readStoredConsent + 门判定路径。
    await page.reload();
    await expect(page.getByTestId('app-title')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('privacy-consent-gate')).toHaveCount(0);
    // #1000：登录衔接页只在同意动作后出现一次，重挂载（已有同意记录）不再出现
    await expect(page.getByTestId('login-step')).toHaveCount(0);
  });

  test('设置页可随时查阅隐私协议', { timeout: 90_000 }, async () => {
    // 沿用测试 3 的主界面实例
    await page.getByTestId('nav-system-settings').click();
    await expect(page.getByText('设置', { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole('tab', { name: /隐私协议/ }).click();
    await expect(page.getByTestId('settings-privacy-page')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('settings-privacy-text')).toContainText('1.0');
    // CI 的 navigator.language 为 en-US（默认英文）——切到中文验证切换与中文文本
    await page.getByRole('button', { name: '中文' }).click();
    await expect(page.getByTestId('settings-privacy-text')).toContainText('隐私协议');

    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-settings-privacy.png`,
      fullPage: true,
    });
  });
});
