/**
 * Issue #1035 回归护栏 —— 一次停止只能留下**一条**「已停止。」
 *
 * 崩溃恢复给 ChatConsole 加的挂载期全局监听器会在「该会话没有 per-send 监听器」
 * 时接管 `chat:aborted`。而 handleAbort 的时序恰好会制造这个状态：它先退订并
 * 注销本轮 invocation，**然后**才 `await window.miqi.chat.abort(...)`。后端随后
 * 回来的 aborted 事件于是只剩全局监听器接得住——不拦住的话，界面上会同时出现
 * handleAbort 自己追加的那条和全局监听器补的那条，用户看到两条「已停止。」。
 * （实现侧的对策是 `localAbortSessionsRef`：本渲染层已经画过停止 UI 的会话，
 * 全局监听器不再重复接管。）
 *
 * mock 的 `chat.abort` 同步 `_fire('aborted')`（见 tests/smoke/mocks.ts），正好
 * 复现这条时序；默认（非挂起）的 `chat.send` 立即 resolve，所以点击停止时本轮
 * 监听器已经全部退订，走的正是「只剩全局监听器」那条路。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=smoke -g "1035"
 */
import { expect, test } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

/** 装 mock 桥（隐私同意 + 已配置 provider）并打开应用，让聊天输入框可达。 */
async function injectMockAndGoto(page: import('@playwright/test').Page) {
  // 隐私门（#837）在应用挂载前读 localStorage —— 先写同意版本，
  // 否则聊天界面根本到不了。
  await page.addInitScript({
    content: `localStorage.setItem('miqi:privacyConsentVersion', '2.0');`,
  });
  await page.addInitScript({
    content: buildMockBridgeScript({
      // 已配置的 provider 让发送路径通过它的前置校验。
      providers: [{ id: 'openrouter', name: 'OpenRouter', configured: true }],
      activeModel: 'model-x',
      activeProvider: 'openrouter',
    }),
  });
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
}

test.describe('Issue #1035 — 停止标记不重复', () => {
  test('点一次停止后「已停止。」恰好出现一次', async ({ page }) => {
    await injectMockAndGoto(page);

    const textarea = page.getByPlaceholder('请输入消息或拖入文件...');
    await expect(textarea).toBeVisible({ timeout: 5000 });
    await textarea.fill('长任务，稍后我会打断它');
    await textarea.press('Enter');

    // 生成中：Composer 的停止按钮（mock 不发 final，streaming 一直为真）。
    const stop = page.getByRole('button', { name: '停止生成' });
    await expect(stop).toBeVisible({ timeout: 10_000 });

    await stop.click();

    // 打断后 handleAbort 立刻画一条；后端回来的 aborted 事件若被全局监听器
    // 再接管一次，就会是两条。等一小段让两类监听器都有机会跑完。
    const marker = page.getByText('已停止。');
    await expect(marker).toHaveCount(1, { timeout: 10_000 });
    await page.waitForTimeout(500);
    await expect(marker, '停止标记不应被重复追加').toHaveCount(1);
  });
});
