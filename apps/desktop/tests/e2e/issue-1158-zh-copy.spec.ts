/**
 * Issue #1158 — 中文界面残留英文文案（模型页 Refresh 按钮）。
 *
 * 三处病灶里只有这一处是**稳定可见**的静态文案，适合用真实渲染的 e2e 断言并留截图：
 *   - 模型页右上角「Refresh」按钮 ← 本 spec
 *   - 启动屏「Loading MiQroForge…」与 diff 弹窗「Loading diff...」都是瞬态（分别只
 *     在环境探测完成前、一次 IPC 往返期间出现），e2e 要抓住它们只能靠 sleep 赌时序。
 *     那两处连同本处一起由 `tests/zhCopyResidualEnglish.test.ts` 的源码文本锁覆盖。
 *
 * 本 spec 不依赖 bridge / LLM：
 *   - 「刷新」按钮在 `editTarget` 为空的离线态也照样渲染（它在条件块之外），
 *     所以不需要 providers.list() 成功，也不需要「编辑当前模型」按钮存在；
 *   - 反过来这也让断言比「找不到 Refresh」更强：只认模型页页头里那个按钮。
 *
 * Run: cd apps/desktop && PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "1158"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.describe('Issue #1158 — 中文界面不残留英文文案', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string | undefined;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('模型页右上角刷新按钮显示「刷新」而不是 Refresh', { timeout: 120_000 }, async () => {
    // ── 打开 设置 → 模型 ──
    const settingsBtn = page.locator('[data-testid="nav-system-settings"]');
    await expect(settingsBtn).toBeVisible({ timeout: 60_000 });
    await settingsBtn.click();

    // 导航项的可访问名 = label + description（「模型 Provider 与 API Key」），
    // 用 ^模型 锚定，避免匹配到关键词里含「模型」的其他项。
    const modelTab = page.getByRole('tab', { name: /^模型/ });
    await expect(modelTab).toBeVisible({ timeout: 30_000 });
    await modelTab.click();

    // 模型页页头渲染完成的标志（标题 + 当前默认模型行）
    await expect(page.getByTestId('providers-active-model')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('heading', { name: '模型', exact: true })).toBeVisible();

    // ── 修复前：这里是裸英文「Refresh」，与相邻的中文按钮并列 ──
    const refreshBtn = page.getByRole('button', { name: '刷新', exact: true });
    await expect(refreshBtn).toBeVisible();

    // 模型页头部有且只有这一个「刷新」按钮（页面无界面变更时也不该多出来）
    await expect(refreshBtn).toHaveCount(1);

    // 反向断言：整页不再有可访问名为 Refresh 的按钮。切页签时 Radix 会卸载
    // 非活动 Tabs.Content（全仓库无 forceMount），所以此刻页面上残留的
    // 「已归档对话」刷新按钮（title="刷新" + RefreshCw 图标）不在 DOM 里。
    await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toHaveCount(0);

    // 按钮确实挂在模型页页头右侧（与「模型」标题同处一个 justify-between 行），
    // 而不是页面别处的同名按钮 —— 用几何对齐代替类名/结构耦合。
    const headingBox = await page.getByRole('heading', { name: '模型', exact: true }).boundingBox();
    const refreshBox = await refreshBtn.boundingBox();
    expect(headingBox, '找不到「模型」标题的包围盒').not.toBeNull();
    expect(refreshBox, '找不到「刷新」按钮的包围盒').not.toBeNull();
    if (headingBox && refreshBox) {
      // 页头行高约 4rem：按钮中心应落在标题所在的这条横带内
      const headingCenterY = headingBox.y + headingBox.height / 2;
      const refreshCenterY = refreshBox.y + refreshBox.height / 2;
      expect(Math.abs(refreshCenterY - headingCenterY)).toBeLessThan(40);
      // 且位于标题右侧（右上角）
      expect(refreshBox.x).toBeGreaterThan(headingBox.x + headingBox.width);
    }

    // ── 证据截图 ──
    await page.screenshot({
      path: 'test-results/issue-1158-模型页-刷新按钮.png',
      fullPage: true,
    });

    // 点一下：文案不因重新拉取而改变，也证明它就是那个 load 按钮
    await refreshBtn.click();
    await expect(refreshBtn).toBeVisible();
    await expect(refreshBtn).toHaveText('刷新');
  });
});
