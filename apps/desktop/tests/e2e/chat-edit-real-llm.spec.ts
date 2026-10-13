/**
 * 编辑并重新回答 — REAL LLM 路径。
 *
 * 与两条 mock 用例互补（chat-edit-failure-not-silent / chat-edit-pencil-disabled-
 * while-streaming）：mock 证明的是链路按假设的形状走，证明不了真实模型 + 真实
 * 请求下也成立。本 spec **不 patch provider**，打真实模型（本地走平台网关，
 * CI 走 siliconflow），钉两件事：
 *
 *   1. 真实**流式生成中** hover 用户气泡 —— 铅笔 disabled、tooltip 为
 *      「生成中，先停止再编辑」；回合结束后恢复可点、tooltip 变回
 *      「编辑并重新回答」。这条缺陷（窄化的 streaming prop 让用户气泡的
 *      `disabled` 恒为 false）只有在真实的长回复上才有足够长的窗口可观测。
 *   2. 真实模型下编辑提问 —— 旧问答被截断、新回答由真实模型重新生成。
 *      这条覆盖 handleEdit 改成返回 EditOutcome 之后的**成功路径**，防止
 *      「失败提示」改动把成功路径改坏。
 *
 * 断言收敛：真实模型文案不可控，故用「只回答 X」这类可控指令取结构信号
 * （回复里有没有 X），不断言整段措辞。
 *
 * 无守卫：electron-e2e（ubuntu 全量）会把 provider key 写进 app 的 config.json，
 * 本 spec 在 CI 上有真实执行者，因此不需要登记 CI-COVERAGE.md。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  sendMessage,
  sendUntilDoneOrProviderDown,
} from './helpers/electron-setup';

/** 生成中 / 可编辑的 tooltip，逐字对齐 ChatConsole 的铅笔。 */
const TITLE_STREAMING = '生成中，先停止再编辑';
const TITLE_EDITABLE = '编辑并重新回答';

/** 要一段足够长的回复 —— 流式窗口得长到能稳定观测。 */
const LONG_PROMPT = '请写一段大约 300 字的短文，介绍量子计算的基本原理。';

/** 「只回答 X」让真实模型的输出可控，断言才落得下来。 */
const FIRST_PROMPT = '请只回答：苹果';
const SECOND_PROMPT = '请只回答：香蕉';

test.describe.serial('编辑并重新回答 (real LLM)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 真实 provider（不 patch 配置）——本地走平台网关，CI 走 siliconflow
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 240_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    '真实生成中铅笔置灰且 tooltip 说真话，回合结束后恢复可点',
    { timeout: LLM_TIMEOUT },
    async () => {
      await createNewConversation(page);

      // sendMessage 只等用户气泡出现 + 输入框清空，不等回复 —— 它返回时回合已经
      // 在跑（desktop 在发出 chat.send 的那一刻就置 streaming=true，不等首个 token）。
      await sendMessage(page, LONG_PROMPT);

      // 不用 waitForTimeout 当同步点：以「停止生成」可见作为「确实在生成中」的信号。
      const streaming = await page
        .getByRole('button', { name: '停止生成' })
        .waitFor({ state: 'visible', timeout: 15_000 })
        .then(() => true)
        .catch(() => false);
      test.skip(!streaming, '回合结束得太快，没抓到「生成中」窗口 —— 无法验证生成中铅笔置灰');

      const bubble = page.getByTestId('chat-message-user').first();
      const pencil = page.getByTestId('edit-message-btn').first();
      await bubble.hover();

      // 关键断言：生成中铅笔必须真的禁用，且 tooltip 不得再假装可编辑。
      await expect(pencil).toBeDisabled({ timeout: 15_000 });
      await expect(pencil).toHaveAttribute('title', TITLE_STREAMING);

      // 证据截图：真实生成中的铅笔（置灰）与仍在流式的回答。
      // 用固定 ASCII 名：测试标题含 `:` / `,`，Windows 下非法，按标题拼路径会
      // 截出一个 0 字节的怪文件（而且 screenshot 不报错）。
      await page.screenshot({
        path: 'test-results/edit-pencil-disabled-real-llm.png',
        fullPage: true,
      });

      // 回合结束后铅笔恢复可点。
      await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
        timeout: LLM_TIMEOUT,
      });
      await expect(page.getByTestId('chat-message-assistant').first()).toBeVisible({
        timeout: 30_000,
      });
      await bubble.hover();
      await expect(pencil).toBeEnabled({ timeout: 15_000 });
      await expect(pencil).toHaveAttribute('title', TITLE_EDITABLE);
    }
  );

  test('真实模型下编辑提问：旧问答被截断，新回答重新生成', { timeout: LLM_TIMEOUT }, async () => {
    await createNewConversation(page);

    const replied = await sendUntilDoneOrProviderDown(
      page,
      FIRST_PROMPT,
      async () => (await page.getByTestId('chat-message-assistant').count()) > 0
    );
    test.skip(
      !replied,
      'no AI reply on every attempt (provider unavailable or too slow) — nothing to edit'
    );

    const assistant = page.getByTestId('chat-message-assistant').first();
    await expect(assistant).toContainText('苹果', { timeout: LLM_TIMEOUT });
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: LLM_TIMEOUT,
    });

    // hover 用户气泡 → 点铅笔（回合已结束，它必须是可点的）。
    const bubble = page.getByTestId('chat-message-user').first();
    const pencil = page.getByTestId('edit-message-btn').first();
    await bubble.hover();
    await expect(pencil).toBeEnabled({ timeout: 15_000 });
    await pencil.click();

    const editor = page.getByTestId('edit-message-input');
    await expect(editor).toBeVisible({ timeout: 10_000 });
    // 编辑器里是用户自己那句原话。
    await expect(editor).toHaveValue(/苹果/);

    // 改成另一个可控指令并提交 —— 走 handleEdit 的成功路径（截断 + 重发）。
    await editor.fill(SECOND_PROMPT);
    await page.getByTestId('edit-message-submit').click();

    // 断言：新回答由真实模型重新生成（含「香蕉」），旧回答不再在列表里。
    await expect(assistant).toContainText('香蕉', { timeout: LLM_TIMEOUT });
    await expect(assistant).not.toContainText('苹果');
    // 旧提问被替换，只剩一条用户消息。
    await expect(page.getByTestId('chat-message-user')).toHaveCount(1);
    await expect(page.getByTestId('chat-message-user').first()).toContainText('香蕉');

    await page.screenshot({ path: 'test-results/edit-regenerate-real-llm.png', fullPage: true });
  });
});
