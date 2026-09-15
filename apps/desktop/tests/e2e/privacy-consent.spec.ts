/**
 * 法律文件确认门 E2E (issue #837 / #1068) — 首次启动法律文件确认与设置页查阅。
 *
 * 覆盖五条路径（全部禁用 MIQI_E2E 绕过，走真实确认门）：
 *  1. 拒绝并退出：首次启动展示《温馨提示》→ 点「不同意，退出」→ 应用退出；
 *  2. 同意进入：重启（同一 MIQI_HOME，同意未持久化）→ 门再次出现 →
 *     文内《隐私政策》链接可查看全文 → 倒计时结束后「同意」启用 → 点击 →
 *     主界面加载；
 *  3. 同意持久化：page.reload() 重挂载 AppShell → 门不再出现（同
 *     readConsentVersion/门判定路径；不重启进程——CI 上 close 后
 *     relaunch 存在桥接端口残留，主界面长期不加载）；
 *  4. 缓存丢失兜底（#1071）：清掉 localStorage 缓存后重挂载 → 门不出现，
 *     同意状态由主进程 userData 文件兜底并回填缓存；
 *  5. 应用内入口：设置 → 法律文件 页可查阅五份文件全文。
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

/** 清掉同意记录（localStorage 缓存 + 主进程权威存储，幂等）。 */
async function clearStoredConsent(page: Page) {
  await page.evaluate(async () => {
    try {
      localStorage.removeItem('miqi:privacyConsentVersion');
    } catch {
      /* ignore */
    }
    try {
      await (window as any).miqi?.privacy?.setConsent(null);
    } catch {
      /* ignore */
    }
  });
}

/** 只清 localStorage 缓存，保留主进程权威存储（#1071 回归用）。 */
async function clearCachedConsentOnly(page: Page) {
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
    // 弹窗内容为律师定稿的《温馨提示》（#1068），版本号随确认门展示
    await expect(page.getByTestId('privacy-consent-text')).toContainText(
      '欢迎您使用MiQroForge DeskTop！'
    );
    await expect(page.getByTestId('privacy-consent-gate')).toContainText('温馨提示');
    await expect(page.getByTestId('privacy-consent-gate')).toContainText('2.0');

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

    // 计时设计沿用 #837：倒计时走完前「同意」保持禁用并显示剩余时间
    const agreeBtn = page.getByTestId('privacy-consent-agree');
    await expect(agreeBtn).toBeDisabled();
    await expect(agreeBtn).toContainText(/\(\d+\.\ds\)/);

    // 确认门截图（倒计时进行中）
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-consent-countdown.png`,
      fullPage: true,
    });

    // 文内《隐私政策》链接打开全文弹窗（律师设计稿的【请插入超链接】落地）
    await page.getByTestId('privacy-consent-doc-privacy').click();
    await expect(page.getByTestId('privacy-consent-doc-content')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('privacy-consent-doc-content')).toContainText(
      'MiQroForge DeskTop隐私政策'
    );
    await expect(page.getByTestId('privacy-consent-doc-content')).toContainText('2026年9月28日');
    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-consent-doc-dialog.png`,
      fullPage: true,
    });
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('privacy-consent-doc-content')).toHaveCount(0);

    // 倒计时结束（3s）后按钮启用，剩余时间文案消失
    await expect(agreeBtn).toBeEnabled({ timeout: 10_000 });
    await expect(agreeBtn).not.toContainText('(');

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
    // 同意版本已写入 localStorage（#1068 起为律师版 2.0）
    const stored = await page.evaluate(() => localStorage.getItem('miqi:privacyConsentVersion'));
    expect(stored).toBe('2.0');

    // 实例保持运行，供测试 3/4 复用（避免 close 后 relaunch 的桥接端口残留）
  });

  test('同意持久化：重挂载后不再展示确认门', { timeout: 180_000 }, async () => {
    // 不重启进程：CI 上 close 后 relaunch 存在桥接端口残留，主界面长期
    // 不加载。page.reload() 重新执行渲染层入口，AppShell 重新挂载，
    // 走与冷启动完全相同的 readConsentVersion + 门判定路径。
    await page.reload();
    await expect(page.getByTestId('app-title')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('privacy-consent-gate')).toHaveCount(0);
    // #1000：登录衔接页只在同意动作后出现一次，重挂载（已有同意记录）不再出现
    await expect(page.getByTestId('login-step')).toHaveCount(0);
  });

  test('同意不依赖 localStorage：缓存清空后仍不弹门（#1071）', { timeout: 180_000 }, async () => {
    // 用户反馈「每次打开都强制看协议」：双开时第二个实例的 Chromium 存储
    // 退化成内存，localStorage 既读不到也写不进。同意版本现在由主进程
    // userData 文件兜底，这里模拟「缓存丢失」验证兜底路径。
    await page.reload();
    await expect(page.getByTestId('app-title')).toBeVisible({ timeout: 120_000 });

    await clearCachedConsentOnly(page);
    await page.reload();

    // 纯 localStorage 丢失不再触发确认门，且缓存被权威存储回填
    await expect(page.getByTestId('app-title')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('privacy-consent-gate')).toHaveCount(0);
    const refilled = await page.evaluate(() => localStorage.getItem('miqi:privacyConsentVersion'));
    expect(refilled).toBe('2.0');
    // preload 同步读到的权威存储值也应一致
    const durable = await page.evaluate(
      () => (window as any).miqi?.privacy?.initialConsentVersion ?? null
    );
    expect(durable).toBe('2.0');
  });

  test('设置页可查阅五份法律文件', { timeout: 90_000 }, async () => {
    // 沿用测试 3 的主界面实例
    await page.getByTestId('nav-system-settings').click();
    await expect(page.getByText('设置', { exact: true }).first()).toBeVisible({
      timeout: 30_000,
    });

    // 律师设计稿：侧边栏「隐私协议」更名为「法律文件」
    await page.getByRole('tab', { name: /法律文件/ }).click();
    await expect(page.getByTestId('settings-legal-page')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId('legal-version-badge')).toContainText('2.0');
    // 默认展示《用户协议》（律师版，生效 2026-09-28）
    await expect(page.getByTestId('legal-doc-content')).toContainText('用户服务协议');
    await expect(page.getByTestId('legal-doc-content')).toContainText('2026年9月28日');

    // 逐份切换：目录五项均可见且可打开对应全文
    const cases: Array<[string, string]> = [
      ['privacy', 'MiQroForge DeskTop隐私政策'],
      ['privacy-summary', '隐私政策摘要'],
      ['data-collection', '个人信息收集清单'],
      ['data-sharing', '第三方服务清单'],
    ];
    for (const [id, marker] of cases) {
      await page.getByTestId(`legal-nav-${id}`).click();
      await expect(page.getByTestId('legal-doc-content')).toContainText(marker);
    }

    await page.screenshot({
      path: `test-results/${test.info().title.replace(/\s+/g, '-')}-settings-legal.png`,
      fullPage: true,
    });
  });
});
