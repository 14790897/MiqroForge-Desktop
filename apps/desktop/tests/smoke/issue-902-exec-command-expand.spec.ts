import { expect, test } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

/** Install the mock bridge (with privacy consent + a configured provider) and
 *  open the app so the chat composer is reachable. */
async function injectMockAndGoto(
  page: import('@playwright/test').Page,
  opts?: Parameters<typeof buildMockBridgeScript>[0]
) {
  // Privacy gate (#837) reads localStorage before the app mounts — pre-consent
  // so the chat screen is reachable without scrolling the agreement.
  await page.addInitScript({
    content: `localStorage.setItem('miqi:privacyConsentVersion', '1.0');`,
  });
  await page.addInitScript({
    content: buildMockBridgeScript({
      // A configured provider lets the send path pass its guard.
      providers: [{ id: 'openrouter', name: 'OpenRouter', configured: true }],
      activeModel: 'model-x',
      activeProvider: 'openrouter',
      ...opts,
    }),
  });
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
}

test.describe('Issue #902 exec command expand', () => {
  test('clicking an exec tool row expands to the full untruncated command', async ({ page }) => {
    // hangChatSend：mock 的 chat.send 挂起直到 terminal 事件，镜像真实桥接
    // （send promise 由 final/error/aborted 才 settle）。#918 改版后
    // ChatConsole 在 send settle 时立即退订本轮 progress 监听器——立即
    // resolve 会让发送后注入的事件被丢弃，工具行永远不渲染。
    await injectMockAndGoto(page, { hangChatSend: true });

    const textarea = page.getByPlaceholder('请输入消息或拖入文件...');
    await expect(textarea).toBeVisible({ timeout: 5000 });
    await textarea.fill('run a long command');
    await textarea.press('Enter');

    // Long command (> 60 chars) so the collapsed summary truncates and only
    // the expanded block reveals the full text.
    const longCommand =
      'echo "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron" | tr -d " " | wc -c';

    // 回合 in-flight 时监听器已注册，但 Enter 到注册之间仍有异步窗口
    // （网关/模型检查等）——在工具行出现前反复重试注入；同一 tool_call_id
    // 的重复注入只会更新既有行，不会叠行。
    const label = page.getByRole('button', { name: /执行命令/ });
    await expect
      .poll(
        async () => {
          await page.evaluate((cmd) => {
            (window as any).__miqiMock.progress({
              text: 'exec("echo alpha beta …")',
              tool_hint: true,
              tool_call_id: 'call_902_1',
              tool_args: { command: cmd },
            });
          }, longCommand);
          return label.isVisible();
        },
        { timeout: 10000 }
      )
      .toBe(true);

    // Collapsed summary is truncated — the full command is not on screen yet.
    await expect(page.getByText(longCommand)).toHaveCount(0);

    await label.click();
    await expect(page.getByText(longCommand)).toBeVisible();
  });
});
