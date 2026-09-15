/**
 * E2E: 欢迎页「起点任务」三层渐进选择（issue #962）
 *
 * 覆盖 PR 新增的那条状态链——评审（issue #962）点名要补的就是它：
 *   模式 → L2 场景 → L3 任务 → 展开 → 提示词落进输入框 → 移除胶囊 → 切 L2 再切回
 *
 * 其中「移除 L3 胶囊要不要一并清掉输入框里的提示词」是评审 P2：胶囊 × 只清选择、
 * 不清文本的话，UI 说「没选任务」而输入框还留着整段 prompt，看着像 × 没生效。
 * 约定是——只有输入框里仍是该任务的原始提示词才清；用户改过就保留。
 *
 * 选择态按 title 记身份（不是下标），所以异步插入的「内置技能」项不会把已选场景
 * 挤到隔壁去（评审 P1）。title 的唯一性由单测 welcomeScenes.test.ts 守住。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron welcome-starters.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  launchElectronApp,
  closeElectronApp,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

const CHIPS = '[data-testid="welcome-starters"] button[aria-pressed]';
const COMPOSER = '[data-testid="chat-input-container"]';
const CARDS = '.starter-card';
const SCENE_PILL = 'button[title="移除这个子项目"]';
const TASK_PILL = 'button[title="移除这个任务"]';

test.describe('Welcome Starters E2E (#962)', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  const cards = () => page.locator(CARDS);
  const textarea = () => page.locator(`${COMPOSER} textarea`);
  const chip = (title: string) => page.locator(CHIPS).filter({ hasText: title }).first();
  const pressed = (title: string) =>
    chip(title)
      .getAttribute('aria-pressed')
      .then((v) => v === 'true');

  /**
   * 把欢迎页恢复成「代码任务 + 内置技能已选、没有展开任何 L3、输入框空」的干净状态。
   * 整份 spec 共用一个 app 实例，而 L2 chip 是 toggle —— 不重置的话下一个用例会
   * 把上一个用例选中/展开的东西点掉。
   */
  const resetStarters = async () => {
    await page
      .getByRole('button', { name: /代码任务/ })
      .first()
      .click();
    await expect(chip('内置技能')).toBeVisible({ timeout: 20_000 });

    // 先撤 L3 再撤 L2（撤 L2 会把 L3 一并清掉）
    if (await page.locator(TASK_PILL).count()) await page.locator(TASK_PILL).click();
    if (await page.locator(SCENE_PILL).count()) await page.locator(SCENE_PILL).click();
    if (!(await pressed('内置技能'))) await chip('内置技能').click();

    await expect(cards().first()).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(`${CARDS}[data-open]`)).toHaveCount(0);
  };

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page);
  }, 60_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
  });

  test('代码任务的 L2 里多一项「内置技能」，且只上架白名单里的技能', async () => {
    await resetStarters();
    console.log('[962] L2 chips =', JSON.stringify(await page.locator(CHIPS).allInnerTexts()));

    const titles = await cards().allInnerTexts();
    console.log('[962] 上架技能 =', JSON.stringify(titles.map((t) => t.split('\n')[0])));
    await expect(cards()).toHaveCount(6);
  });

  test('L3 是可展开卡片：同一时间只展开一张，再点收起', async () => {
    await resetStarters();

    await cards().nth(0).locator('button').click();
    await expect(cards().nth(0)).toHaveAttribute('data-open', '');
    await expect(page.locator(`${CARDS}[data-open]`)).toHaveCount(1);

    // 点第二张：手风琴，第一张自动收起
    await cards().nth(1).locator('button').click();
    await expect(cards().nth(1)).toHaveAttribute('data-open', '');
    await expect(page.locator(`${CARDS}[data-open]`)).toHaveCount(1);

    // 再点同一张：收起，一张都不剩
    await cards().nth(1).locator('button').click();
    await expect(page.locator(`${CARDS}[data-open]`)).toHaveCount(0);
  });

  test('点 L3 把提示词放进输入框；换一个任务是替换而不是累积', async () => {
    await resetStarters();

    await cards().nth(0).locator('button').click();
    const first = await textarea().inputValue();
    expect(first.trim().length).toBeGreaterThan(0);

    await cards().nth(2).locator('button').click();
    const second = await textarea().inputValue();
    expect(second).not.toBe(first);
    // 连点不会把两段提示词摞在一起
    expect(second).not.toContain(first);
  });

  test('移除 L3 胶囊会一并清掉未经修改的提示词', async () => {
    await resetStarters();
    await cards().nth(0).locator('button').click();
    expect((await textarea().inputValue()).trim().length).toBeGreaterThan(0);

    await page.locator(TASK_PILL).click();

    await expect(page.locator(TASK_PILL)).toHaveCount(0);
    await expect(page.locator(`${CARDS}[data-open]`)).toHaveCount(0);
    // 关键断言：输入框不能留着刚才那段 prompt
    await expect(textarea()).toHaveValue('');
  });

  test('用户手动改过提示词后，移除胶囊不误删编辑', async () => {
    await resetStarters();
    await cards().nth(0).locator('button').click();

    const edited = '我自己改过的内容，别删';
    await textarea().fill(edited);
    await page.locator(TASK_PILL).click();

    await expect(page.locator(TASK_PILL)).toHaveCount(0);
    await expect(textarea()).toHaveValue(edited);
  });

  test('移除 L2 胶囊：场景与任务两层选择一起清掉', async () => {
    await resetStarters();
    await cards().nth(0).locator('button').click();
    await expect(page.locator(TASK_PILL)).toHaveCount(1);

    await page.locator(SCENE_PILL).click();

    await expect(page.locator(SCENE_PILL)).toHaveCount(0);
    await expect(page.locator(TASK_PILL)).toHaveCount(0);
    await expect(cards()).toHaveCount(0);
    await expect(textarea()).toHaveValue('');
    expect(await pressed('内置技能')).toBe(false);
  });

  test('切换 L2 再切回来，选中态能恢复', async () => {
    await resetStarters();

    await chip('补测试').click();
    expect(await pressed('内置技能')).toBe(false);
    expect(await pressed('补测试')).toBe(true);
    await expect(page.locator(`${CARDS}[data-open]`)).toHaveCount(0);

    await chip('内置技能').click();
    expect(await pressed('内置技能')).toBe(true);
    await expect(cards()).toHaveCount(6);
  });

  test('输入法组字中按回车不会把半成品发出去', async () => {
    await resetStarters();

    // 真实 IME 没法在 e2e 里驱动，直接派发一个 isComposing=true 的回车——
    // Composer.handleKeyDown 读的就是 nativeEvent.isComposing。
    await textarea().click();
    await textarea().evaluate((el) => {
      const t = el as HTMLTextAreaElement;
      t.value = '我正在打拼音';
      t.dispatchEvent(new Event('input', { bubbles: true }));
      t.dispatchEvent(
        new KeyboardEvent('keydown', {
          key: 'Enter',
          bubbles: true,
          cancelable: true,
          isComposing: true,
        })
      );
    });
    await page.waitForTimeout(600);

    // 没发出去：还停在欢迎页（发过消息后欢迎块就没了）
    await expect(page.locator('[data-testid="chat-message-user"]')).toHaveCount(0);
    await expect(chip('内置技能')).toBeVisible();

    // 别把这段半成品留给后面的用例
    await textarea().fill('');
  });
});
