/**
 * Confirm Card E2E — REAL LLM path (issue #646 真机验证)。
 *
 * 与 confirm-card.spec.ts（mock 状态机）互补：本 spec 不 patch provider，
 * 使用配置中的真实模型（本地 deepseek / CI siliconflow），显式指令模型
 * 调用确认工具，验证真实模型 + 真实 HTTP 请求下卡片渲染、阻塞、用户选择
 * 回传、回合完成的完整链路：
 *
 *   A. ask_user_confirm_card → confirm-card（普通协作确认）
 *   B. request_action_confirmation → ActionCard（危险动作确认，#1071 C7 边界）
 *
 * 断言刻意收敛：真实模型回复文案不可控，只断言卡片出现、决议回传、
 * 回合正常收尾（有 assistant 回复且流式结束）。文件/大小/指纹等确定性字段
 * 由 mock 版 confirm-card.spec.ts 断言——真实模型可能改写参数，此处不重复押。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "real LLM"
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForResponseComplete,
  sendUntilDoneOrProviderDown,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
} from './helpers/electron-setup';

test.describe('Confirm Card (real LLM)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 真实 provider（不 patch 配置）——本地走 deepseek，CI 走 siliconflow
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    '真实模型调用 ask_user_confirm_card — 弹卡、点击确认、tool result 回传、回合完成',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      // 2026-08-28：卡并进工具链（Hermes 式）——断言页面级 + 回执
      const cardCount = () => page.getByTestId('confirm-card').count();

      // 显式指令模型调用工具（真实 HTTP 请求到 provider）。macos-e2e 上共享
      // CI key 限流会让回合报「模型服务暂时不可用」——重发一次；全失败则跳过
      // （卡片从未出现，fail 是噪音而非回归）。
      const cardAppeared = await sendUntilDoneOrProviderDown(
        page,
        '请立即调用 ask_user_confirm_card 工具弹出确认卡片：' +
          'title 用「确认执行方案？」，message 用「开始前需要你确认以下计划」。' +
          '调用后收到结果时直接回复 OK。',
        async () => (await cardCount()) > 0
      );
      test.skip(
        !cardAppeared,
        'no AI reply on every attempt (provider unavailable or too slow) — no confirm card to verify'
      );

      const confirmCard = page.getByTestId('confirm-card').first();
      await expect(confirmCard.getByText('确认执行方案？', { exact: true })).toBeVisible({
        timeout: 120_000,
      });
      await expect(confirmCard.getByTestId('confirm-run')).toBeVisible();

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-card.png`,
      });

      // 点击确认 → tool result 回传模型 → 模型继续完成回合
      await confirmCard.getByTestId('confirm-run').click();
      // 回执内定位（页面级会撞上真实模型输出里的同名文本——CI strict mode）
      await expect(
        page.locator('[data-receipt="true"]').getByText('已确认执行方案', { exact: true }).first()
      ).toBeVisible({
        timeout: 30_000,
      });

      await waitForResponseComplete(page, LLM_TIMEOUT);
      const assistantBubbles = page.getByTestId('chat-message-assistant');
      await expect(assistantBubbles.first()).toBeVisible({ timeout: 30_000 });

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-final.png`,
        fullPage: true,
      });
    }
  );

  test(
    '真实模型调用 request_action_confirmation — 弹 ActionCard、点击确认、回合完成',
    { timeout: LLM_TIMEOUT * 2 },
    async () => {
      // 上传/支付/破坏性删除等危险动作的唯一模型侧入口（#1071 C7 边界）：
      // 渲染为 ActionCard（testid=action-card），确认按钮仍是 confirm-run 体系。
      await createNewConversation(page);
      const actionCardCount = () => page.getByTestId('action-card').count();

      // 与上一条同款重试/跳过机制：CI 共享 key 限流时重发一次，全失败则跳过
      // （卡片从未出现，fail 是噪音而非回归）。
      const cardAppeared = await sendUntilDoneOrProviderDown(
        page,
        '请立即调用 request_action_confirmation 工具弹出动作确认卡片：' +
          'action 用「upload」，target 用「Qraft」，file_name 用「mof-report.json」，' +
          'description 用「上传 MOF-5 报告到 Qraft」。' +
          '调用后收到结果时直接回复 OK。',
        async () => (await actionCardCount()) > 0
      );
      test.skip(
        !cardAppeared,
        'no AI reply on every attempt (provider unavailable or too slow) — no action card to verify'
      );

      const actionCard = page.getByTestId('action-card').first();
      // action=upload → 标题行「☁ 上传：<target>」（ActionCard meta 渲染）
      await expect(actionCard.getByText('☁ 上传').first()).toBeVisible({ timeout: 120_000 });
      await expect(actionCard.getByText('Qraft').first()).toBeVisible();
      await expect(actionCard.getByTestId('confirm-run')).toBeVisible();

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-action.png`,
      });

      // 点击确认（「确认上传」）→ tool result 回传模型 → 模型继续完成回合
      await actionCard.getByTestId('confirm-run').click();

      // ActionCard 无回执态：决议后按设计卸载（ConfirmCardArea 对已决议的
      // action 卡 return null）——回合收尾后不得残留挂起卡。
      await expect(page.getByTestId('action-card')).toHaveCount(0, { timeout: 30_000 });

      await waitForResponseComplete(page, LLM_TIMEOUT);
      const assistantBubbles = page.getByTestId('chat-message-assistant');
      await expect(assistantBubbles.first()).toBeVisible({ timeout: 30_000 });

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-real-action-final.png`,
        fullPage: true,
      });
    }
  );
});
