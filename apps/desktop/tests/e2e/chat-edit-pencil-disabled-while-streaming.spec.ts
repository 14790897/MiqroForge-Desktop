/**
 * 生成中铅笔必须真的置灰。
 *
 * 铅笔写的是 `disabled={streaming}`，但 MessageBubble 收到的 `streaming` prop
 * 在渲染点被窄化过：
 *
 *     streaming={streaming && i === lastAssistantIdx && assistantTailActive}
 *
 * `lastAssistantIdx` 按定义指向最后一个 assistant 分组，而铅笔挂在**用户**
 * 气泡上 —— 用户气泡的 `i` 永远不可能等于它，于是这个 prop 对用户气泡恒为
 * false，按钮永远不禁用、tooltip 永远说「编辑并重新回答」。
 *
 * 这不是纯显示问题：它正是「编辑提交被静默丢弃」长期没人发现的原因 —— 界面
 * 明确告诉用户可以改，用户点了，改动被 handleEdit 的 streaming 守卫丢掉。
 *
 * 本用例钉住修好后的行为：
 *   · 生成中 hover 用户气泡 —— 铅笔 disabled、tooltip 为「生成中，先停止再编辑」
 *   · 回合结束后 —— 铅笔恢复可点、tooltip 变回「编辑并重新回答」
 *
 * 改前的代码上它是红的（铅笔始终可点、tooltip 始终是后半句）。
 *
 * 「生成中」这个窗口靠 mock 的 `MOCK_SLOW:<ms>:` 标记造出来：desktop 在发出
 * chat.send 的那一刻就把 streaming 置为 true（ChatConsole.tsx，不等第一个
 * token），所以 mock 响应前睡的那几秒里，界面确实处于生成中。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { createNewConversation, launchElectronApp, closeElectronApp, sendMessage } from './helpers/electron-setup';
import { postScreenshotToPr } from './helpers/pr-image-post';

const REPO_ROOT = join(__dirname, '..', '..', '..', '..');

/** 生成中的 tooltip / 可编辑时的 tooltip，逐字对齐 ChatConsole 的 pencil。 */
const TITLE_STREAMING = '生成中，先停止再编辑';
const TITLE_EDITABLE = '编辑并重新回答';

/** mock 响应前先睡这么久 —— 给用例留出稳定的「生成中」窗口。 */
const SLOW_MS = 8000;

async function startMockOpenAI(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_openai.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1', MIQI_MOCK_TEXT_REPLY: '1' },
    windowsHide: true,
  });
  let readyUrl = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock] ${t.trim()}`);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => console.log(`[mock-err] ${String(d).trim()}`));
  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) throw new Error(`mock exited early: ${proc.exitCode}`);
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error('mock startup line not seen in 30s');
  }
  return { proc, mockUrl: readyUrl };
}

test.describe('生成中铅笔置灰', () => {
  // macOS CI 的 undici fetch 到本地 127.0.0.1 会失败(与其它本地 mock spec
  // 相同的裁剪策略),Linux electron-e2e 全量覆盖本 spec。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockOpenAI();
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      config.agents = {
        ...(config.agents || {}),
        defaults: { ...(config.agents?.defaults || {}), model: 'deepseek/deepseek-chat' },
      };
      providers.deepseek = {
        ...(providers.deepseek || {}),
        apiBase: mock.mockUrl,
        apiKey: 'mock-key',
      };
      config.providers = providers;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await createNewConversation(page);
  }, 240_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
  });

  test(
    '生成中铅笔置灰且 tooltip 说真话，回合结束后恢复可点',
    { timeout: 180_000 },
    async () => {
      // sendMessage 只等用户气泡出现+输入框清空,不等回复 —— 它返回时回合
      // 已经在跑,而 mock 还要 SLOW_MS 才回,这就是我们要的「生成中」窗口。
      await sendMessage(page, `MOCK_SLOW:${SLOW_MS}:慢回复内容`);

      const bubble = page.getByTestId('chat-message-user').first();
      const pencil = page.getByTestId('edit-message-btn').first();
      await bubble.hover();

      // 关键断言:生成中铅笔必须禁用,且 tooltip 不得再假装可编辑
      await expect(pencil).toBeDisabled({ timeout: 10_000 });
      await expect(pencil).toHaveAttribute('title', TITLE_STREAMING);

      // 证据截图:生成中的铅笔(置灰)与仍在生成的回答
      const shot = 'test-results/edit-pencil-disabled.png';
      await page.screenshot({ path: shot, fullPage: true });
      await postScreenshotToPr(shot, '✅ 生成中:铅笔置灰、tooltip 为「生成中，先停止再编辑」');

      // 回合结束(「停止生成」按钮消失)后,铅笔恢复可点
      await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
        timeout: 90_000,
      });
      await expect(page.getByTestId('chat-message-assistant')).toHaveCount(1, {
        timeout: 90_000,
      });
      await bubble.hover();
      await expect(pencil).toBeEnabled({ timeout: 10_000 });
      await expect(pencil).toHaveAttribute('title', TITLE_EDITABLE);
    }
  );
});
