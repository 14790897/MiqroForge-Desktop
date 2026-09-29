/**
 * E2E（真实模型 · 真子智能体 · 零注入）：#981 —— 在子任务 tab 发消息不得中断主任务
 *
 * 与 `issue-981-task-parallel.spec.ts` 的分工：
 *   - 那一份用 mock provider + 注入 delta 换取**确定性**（主回合必然一直在飞），
 *     作为回归判据；
 *   - 这一份**什么都不 mock、什么都不注入**：主任务与子任务的回复都来自真实模型
 *     （本机配置的 provider），子线程 tab 由模型自己调用 `spawn` 工具产生
 *     （SpawnTool → AgentControl.spawn → sub_agent_spawned → 本 PR 的接线 →
 *     `agent:spawned` → 渲染层建 tab），界面上的每一个字都是真的。
 *
 * 也就是说：**用户按下面的步骤自己就能复现**——
 *   1. 跟模型说「用 spawn 起一个子智能体去做 X」（或任何会让它调 spawn 的任务）；
 *   2. 等 tab 栏出现第二个 tab（子任务）；主任务这时还在生成；
 *   3. 切到子任务 tab 发一条消息；
 *   4. 回到主任务 tab：它的回复应继续正常生成完，**不应**出现「已停止。」/
 *      TURN_IN_PROGRESS —— 修复前这里会把主任务 abort 掉。
 *
 * 不确定性（真模型的代价，按仓库 real-LLM 用例的既有口径处理）：
 *   - provider 不可用（限流/过载）→ skip；
 *   - 模型没调 spawn、tab 一直不出现 → skip（这一步测不到东西）；
 *   - 主回合在切走之前就结束了 → skip（覆盖不到「生成中被切走」）。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  APPS_DESKTOP,
  LLM_TIMEOUT,
  PROVIDER_UNAVAILABLE_TEXT,
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

/** TURN_IN_PROGRESS 的用户可见文案（sanitizeUiMessage.ts:47）。 */
const TURN_IN_PROGRESS_TEXT = '上一个任务还在进行中';
/** 前端在 abort 时往回合里写的标记（ChatConsole.tsx）。 */
const STOPPED_TEXT = '已停止。';

const SHOT_DIR = join(APPS_DESKTOP, 'test-reports', 'issue981-real');

/** 会话级「生成中」标志：流式期间发送按钮换成停止按钮（Composer.tsx）。 */
const streamingStop = (page: Page) => page.getByLabel('停止生成');

/** 最后一条助手气泡的正文。 */
const lastAssistantText = (page: Page): Promise<string> =>
  page
    .getByTestId('chat-message-assistant')
    .last()
    .textContent()
    .then((t) => t ?? '')
    .catch(() => '');

/**
 * 最长的那条助手回复。
 *
 * ⚠️ 消息列表是**按会话共享的**：切到子任务 tab 发消息后，子任务那条（短）回复会
 * 排在主任务回复后面，`last()` 量到的是它，不是主任务。所以量主任务必须按「最长
 * 的那条」——主任务的产出是一篇散文，子任务只是一句话，两者长度差着一个量级。
 */
const longestAssistantText = (page: Page): Promise<string> =>
  page
    .getByTestId('chat-message-assistant')
    .allTextContents()
    .then((all) => all.reduce((a, b) => (b.length > a.length ? b : a), ''))
    .catch(() => '');

test.describe('#981 真实模型 · 真子智能体（零注入）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 不 patch 配置：走本机/CI 配好的真实 provider。
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
    // 工具审批预授权：`spawn` 走审批门，E2E 里没人点卡 → 超时按「用户已拒绝」处理，
    // 子智能体根本起不来（本机实测过一次：模型自己回报「Approval timeout」）。
    // 同 full-electron / billing-live / global-prompt-skill-rule 等用例的既有写法。
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
    console.log('[e2e981-real] bridge initialized');
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    '真模型调 spawn 起子智能体 → 子任务 tab 发消息 → 主任务不被中断',
    { timeout: LLM_TIMEOUT * 3 },
    async () => {
      mkdirSync(SHOT_DIR, { recursive: true });

      // 让模型先起子智能体、再自己去写一段长文：这样「切到子 tab 发消息」时主任务
      // 大概率还在生成，才能覆盖到「生成中被切走」这一步。
      // 指令写得硬一些（第一件事就是调 spawn、调完再写），因为这一步依赖模型
      // 真的选择调用工具 —— 它不调就只能 skip（见下）。
      const MAIN_PROMPT =
        '第一件事：立刻调用 spawn 工具起一个子智能体，task 用「整理一份本周待办清单草稿」，label 用「待办整理」。' +
        '在 spawn 返回之前不要开始写正文。' +
        'spawn 返回之后，你再在这边写一篇 600 字左右的散文《城市里的四季》作为主任务的产出。';
      const SUB_PROMPT = '用一句话概括秋天的特征。';

      // 必须先开一个新会话：本机 e2e 有「落在开发者历史会话上」的已知污染，模型会
      // 读到之前几轮测试留下的上下文并据此改变行为（本机实测过一次：它认为
      // 「这条指令我在上一步已经完整执行过了」而拒绝再 spawn）。新会话同时让下面
      // 的判据干净（不会混进历史里的「任务被中断」卡片）。
      await createNewConversation(page);

      await sendMessage(page, MAIN_PROMPT);

      // 1) 会话真的在生成（真实模型在回）
      await expect(streamingStop(page), '主任务必须真的开始生成').toBeVisible({
        timeout: LLM_TIMEOUT,
      });
      await page.screenshot({ path: join(SHOT_DIR, '1-main-streaming-real.png') });

      if ((await page.getByText(PROVIDER_UNAVAILABLE_TEXT).count()) > 0) {
        test.skip(true, 'provider 不可用（限流/过载），本回合没有真实回复');
      }

      // 2) 模型自己调 spawn → 真实 tab 出现（接线前这里永远等不到）
      const tabs = page.getByTestId('chat-thread-tab');
      let appeared = false;
      try {
        await expect(tabs).toHaveCount(2, { timeout: LLM_TIMEOUT });
        appeared = true;
      } catch {
        appeared = false;
      }
      if (!appeared) {
        // 诊断：模型到底做了什么（是没调 spawn，还是调了报错）—— 连同截图留证。
        await page.screenshot({ path: join(SHOT_DIR, '2b-no-tab.png') });
        const said = (await lastAssistantText(page)).slice(0, 500);
        const body = await page.evaluate(() => document.body.innerText.slice(0, 300));
        console.log(`[e2e981-real] tab 未出现。模型最后一段回复: ${said}`);
        console.log(`[e2e981-real] 页面片段: ${body}`);
        test.skip(
          true,
          `模型这一轮没有调用 spawn（或调用失败），子任务 tab 未出现。模型原话: ${said.slice(0, 120)}`
        );
      }
      await page.screenshot({ path: join(SHOT_DIR, '2-two-tabs-real.png') });

      // 3) 主回合必须还在生成，否则覆盖不到「生成中被切走」
      if ((await streamingStop(page).count()) === 0) {
        test.skip(true, '主回合在切 tab 之前就结束了，无法覆盖「生成中被切走」');
      }

      const subTab = page.locator('[data-testid="chat-thread-tab"]:not([data-thread-id="main"])');
      // 切走之前主任务回复的长度：它此刻在流式，所以回来时必须**继续增长**——
      // 被 abort 的话生成会停在切走那一刻。绝对字数不可靠（真模型不保证写满），
      // 增长与否才是「有没有被掐断」的判据。
      const beforeSwitch = (await longestAssistantText(page)).length;
      console.log(`[e2e981-real] 切走前主任务回复长度: ${beforeSwitch}`);
      await subTab.first().click();
      await expect(subTab.first()).toHaveAttribute('data-active', 'true');

      // 4) 在子任务 tab 发一条真实消息
      await sendMessage(page, SUB_PROMPT);
      await page.waitForTimeout(4000);
      await page.screenshot({ path: join(SHOT_DIR, '3-sub-task-sent-real.png') });

      // 5) 断言：主任务没有被中断
      await expect(
        page.getByText(TURN_IN_PROGRESS_TEXT),
        '子任务 tab 发消息不得被 TURN_IN_PROGRESS 拒绝'
      ).toHaveCount(0);
      await expect(
        page.getByText(STOPPED_TEXT),
        '主任务不得被 abort（出现「已停止。」即说明被切走时中断了）'
      ).toHaveCount(0);

      // 6) 回主 tab：主任务应正常收尾，且是一份完整长文（被中断会停在中途）
      const mainTab = page.locator('[data-testid="chat-thread-tab"][data-thread-id="main"]');
      await mainTab.click();
      await expect(mainTab).toHaveAttribute('data-active', 'true');
      await expect(streamingStop(page), '主任务应正常收尾（不被中断）').toHaveCount(0, {
        timeout: LLM_TIMEOUT,
      });
      const mainReply = await longestAssistantText(page);
      await page.screenshot({ path: join(SHOT_DIR, '4-back-on-main-real.png') });
      expect(
        mainReply.length,
        `主任务被切走后必须继续生成完（切走时 ${beforeSwitch} 字，最终 ${mainReply.length} 字）——` +
          ' 若被 abort，长度会停在切走那一刻'
      ).toBeGreaterThan(beforeSwitch);

      console.log(`[e2e981-real] screenshots -> ${SHOT_DIR}`);
    }
  );
});
