/**
 * Plan Card E2E（#646-v2）— plan workstream → execution → dangerous action.
 *
 * The plan is part of the agent work stream. The user can execute the current
 * plan or adjust it inline; adjustment feedback is returned to the model which
 * produces a new plan before any mutation continues.
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
} from './helpers/electron-setup';
import { startMockOpenAI, patchConfigForMock } from './helpers/mock-openai';

async function launchWithMock() {
  const mock = await startMockOpenAI();
  const fixture = await launchElectronApp((config: any) =>
    patchConfigForMock(config, mock.mockUrl)
  );
  return { ...fixture, mockServer: mock.proc };
}

test.describe('Plan Card (#646-v2)', () => {
  // macOS CI cannot run mock-based specs: the runner cannot reach a local
  // 127.0.0.1 listener (see confirm-card.spec.ts / #710 trimming strategy).
  // The Linux electron-e2e job covers this spec in full.
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  test('计划工作流：当前方案执行 → ActionCard → 完成', { timeout: LLM_TIMEOUT }, async () => {
    const fixture = await launchWithMock();
    const electronApp: ElectronApplication = fixture.electronApp;
    const page: Page = fixture.page;

    try {
      await createNewConversation(page);
      await sendMessage(page, '计划：生成 MOF-5 实验报告并上传');

      const planCard = page.getByTestId('plan-card').first();
      await expect(planCard).toBeVisible({ timeout: 60_000 });
      // #1071 评审 P1（唯一性）：等待态只有兜底区一处渲染——本回合 mock 只回
      // ask_user_plan_confirm 工具调用（无文本 → 无 assistant 气泡），内联路径
      // 不可能有卡。合法多重性 = 1。（.first()/toHaveCount 并用：前者消歧义，
      // 后者把「恰好一张」写死，双渲染会被这里直接抓住。）
      await expect(page.getByTestId('plan-card')).toHaveCount(1);
      await expect(planCard.getByText('生成 MOF-5 实验报告')).toBeVisible();
      await expect(planCard.getByText('搜集论文资料')).toBeVisible();
      await expect(planCard.getByText('上传到 MiqroForge').first()).toBeVisible();
      await expect(planCard.getByText('网络')).toBeVisible();
      await expect(planCard.getByText('外部')).toBeVisible();
      await expect(planCard.getByTestId('plan-confirm')).toBeVisible();
      await expect(planCard.getByTestId('plan-modify')).toBeVisible();

      await page.screenshot({ path: 'test-results/plan-card-waiting.png' });
      await planCard.getByTestId('plan-confirm').click();

      const autoApprove = async () => {
        try {
          for (let i = 0; i < 60; i++) {
            const dialog = page.getByRole('alertdialog').first();
            if (await dialog.isVisible().catch(() => false)) {
              const allow = dialog.getByRole('button', { name: /允许一次|允许/ }).first();
              if (await allow.isVisible().catch(() => false)) await allow.click();
            }
            await page.waitForTimeout(500);
          }
        } catch {
          // App closed: nothing left to approve.
        }
      };
      const approveTask = autoApprove();

      const actionCard = page.getByTestId('action-card').first();
      await expect(actionCard).toBeVisible({ timeout: 60_000 });
      // #1071 评审 P1（唯一性）：ActionCard 出现时本回合仍无 assistant 文本气泡
      // （web_search / write_file / request_action_confirmation 都是纯工具调用
      // 回合），计划卡（已确认）+ 动作卡各 1 张，且各自只有兜底区一个实例。
      await expect(page.getByTestId('action-card')).toHaveCount(1);
      await expect(page.getByTestId('plan-card')).toHaveCount(1);
      await expect(actionCard.getByText('☁ 上传').first()).toBeVisible();
      await expect(actionCard.getByText('MiqroForge').first()).toBeVisible();
      await expect(actionCard.getByText('mof-report.json').first()).toBeVisible();
      await expect(actionCard.getByText(/23\.0 KB/)).toBeVisible();

      await page.screenshot({ path: 'test-results/action-card-upload.png' });
      await actionCard.getByRole('button', { name: '确认上传' }).click();
      await waitForResponseComplete(page, LLM_TIMEOUT);
      await expect(page.getByText(/已完成：MOF-5 实验报告/)).toBeVisible({ timeout: 30_000 });

      // #1071 评审 P1（双渲染回归锁）：回合收尾后 assistant 气泡带上本回合
      // turn_id，计划卡此时应**移入**消息内（inline-cards），兜底区不再留第二份。
      // 修复前这里的 page 级计数是 2（消息内 1 + 兜底区 1）——正是评审报的 P1。
      await expect(page.getByTestId('inline-cards').getByTestId('plan-card')).toHaveCount(1);
      await expect(page.getByTestId('confirm-card-area').getByTestId('plan-card')).toHaveCount(0);
      await expect(page.getByTestId('plan-card')).toHaveCount(1);

      await approveTask;
    } finally {
      await closeElectronApp(electronApp, fixture.miqiHome);
      fixture.mockServer.kill();
    }
  });

  test(
    '调整方案：内联输入意见 → Agent 重新规划 → 不执行旧方案',
    { timeout: LLM_TIMEOUT },
    async () => {
      const fixture = await launchWithMock();
      const electronApp: ElectronApplication = fixture.electronApp;
      const page: Page = fixture.page;

      try {
        await createNewConversation(page);
        await sendMessage(page, '计划：生成 MOF-5 实验报告并上传');

        const planCard = page.getByTestId('plan-card').first();
        await expect(planCard).toBeVisible({ timeout: 60_000 });
        await planCard.getByTestId('plan-modify').click();

        const adjustment = planCard.getByTestId('plan-adjustment-input');
        await expect(adjustment).toBeVisible();
        await adjustment.fill('不要上传 MiqroForge，先完成本地报告并增加成本对比步骤。');
        await planCard.getByTestId('plan-submit-adjustment').click();

        const revised = page.getByTestId('plan-card').last();
        await expect(revised).toBeVisible({ timeout: 60_000 });
        // #1071 评审 P1（唯一性 + 合法多重性）：这一阶段的合法多重性 = 2，不是 1——
        // 旧卡（state=modify，PlanCard 以「已调整」保留）+ 新卡（pending）同屏。
        // 两张都还没被消息内联（本回合依旧没有 assistant 文本气泡：改方案的答复
        // 由 ask_user_plan_confirm 的返回值回灌模型，不产生新消息），因此都在兜底区
        // 各占一个实例。写成 1 会掩盖双渲染，写成 2 才能同时锁住「不多不少」。
        await expect(page.getByTestId('plan-card')).toHaveCount(2);
        await expect(revised.getByText('生成 MOF-5 实验报告（修改版）')).toBeVisible();
        await expect(revised.getByText('对比合成成本')).toBeVisible();
        await expect(page.getByTestId('action-card')).toHaveCount(0);

        await revised.getByTestId('plan-cancel').click();
      } finally {
        await closeElectronApp(electronApp, fixture.miqiHome);
        fixture.mockServer.kill();
      }
    }
  );

  // ── 回归（2026-09-21 electron-e2e 抖动）──────────────────────────────
  // 一个**不关心计划卡**的 spec，只要提示词恰好让模型走到需要计划的工具调用，
  // 回合就会停在「等待你的决定」上：渲染进程的 `streaming` 仍是 true、标签照挂，
  // 于是 waitForResponseComplete 一路等到超时，报出来却是
  // 「回合在 120000ms 内没有结束（「进行中」标签一直没消失）」——从报错完全看不出
  // 和计划卡有关。当天多个无关分支上红的就是这个签名，失败快照里躺着同一张未确认
  // 的计划卡（模型在温度 1 下会不会走到这类工具调用是随机的，所以同一个 spec
  // 重试常常 8 秒就过）。修复后 waitForResponseComplete 在等待期间自己把卡点掉。
  test(
    '等待中的计划卡由 waitForResponseComplete 自动批准（不关心卡片的 spec 不再卡死）',
    { timeout: LLM_TIMEOUT },
    async () => {
      const fixture = await launchWithMock();
      const electronApp: ElectronApplication = fixture.electronApp;
      const page: Page = fixture.page;

      try {
        await createNewConversation(page);
        // mock 见用户消息里的「计划」二字就走 ask_user_plan_confirm → 一张等待态计划卡。
        await sendMessage(page, '计划：生成 MOF-5 实验报告并上传');

        const confirm = page.getByTestId('plan-confirm');
        await expect(confirm).toBeVisible({ timeout: 60_000 });

        // 关键：这里**不点**卡，交给 waitForResponseComplete。超时给得短是故意的
        // ——本用例只钉「卡被自动批准」这一步，不需要等整条链跑完（后面还有
        // web_search 等真实工具，本地没有 key 时跑不到底）。
        const waiting = waitForResponseComplete(page, 30_000).catch(() => {});
        await expect(confirm).toHaveCount(0, { timeout: 25_000 });
        await waiting;
      } finally {
        await closeElectronApp(electronApp, fixture.miqiHome);
        fixture.mockServer.kill();
      }
    }
  );
});
