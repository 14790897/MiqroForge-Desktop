/**
 * 编辑提交失败不得被静默丢弃。
 *
 * handleEdit 的 6 条早退原本都是裸 return（不返回任何东西），而两个提交点
 * 都是 fire-and-forget 后无条件 setEditing(false) —— 调用方分不清成败，用户
 * 刚写的文本无声消失、没有任何提示。其中最伤的一条是 sessions.truncate 失败：
 * 文本改了、后端删除历史没成功、编辑框关闭、改动凭空不见，用户只会以为「点了
 * 没反应」，然后发现自己写的东西没了。
 *
 * 本用例挑这条最容易确定触发的路径钉住新行为（其余 5 条早退共用同一个提交
 * 机制，钉住机制即钉住全部）：
 *   · 编辑框保持打开 —— 用户的字还在里面
 *   · 底栏下方出现原因行「删除历史失败，改动未提交」
 *   · 消息列表原地不动 —— 截断没成功，旧问答不得被删掉
 *
 * 它在改前的代码上是红的（编辑框照常关闭、无原因行），这才是有证伪能力的
 * 用例；同目录下 chat-edit-regenerate / issue-1072 里的「编辑失败」用例覆盖的
 * 是 rejected 那条路径 —— 那条改前就有可见反馈，两版代码上都是绿的，挡不住
 * 本次的回归。
 *
 * 触发方式与 issue-1072 同一手法：contextBridge 冻结了渲染层的 window.miqi.*，
 * page.evaluate 覆盖会被静默丢弃，只能改主进程的 IPC handler。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  sendMessage,
} from './helpers/electron-setup';
import { postScreenshotToPr } from './helpers/pr-image-post';

const REPO_ROOT = join(__dirname, '..', '..', '..', '..');
const SESSIONS_TRUNCATE = 'sessions:truncate';

/** describeEditFailure('truncate-failed') 的文案，逐字对齐。 */
const TRUNCATE_FAILED_TEXT = '删除历史失败，改动未提交';

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

/** 把 main 的 sessions:truncate 换成必抛 —— 模拟「后端删除历史失败」。 */
async function patchTruncateToFail(electronApp: ElectronApplication): Promise<void> {
  await electronApp.evaluate(
    async ({ ipcMain }, channel: string) => {
      ipcMain.removeHandler(channel);
      ipcMain.handle(channel, async () => {
        throw new Error('truncate failed (e2e)');
      });
    },
    SESSIONS_TRUNCATE
  );
}

/** hover 用户消息 → 编辑 → 提交(newText 覆盖编辑框内容)。 */
async function submitEdit(page: Page, newText: string): Promise<void> {
  await page.getByTestId('chat-message-user').first().hover();
  await page.getByTestId('edit-message-btn').first().click();
  const editor = page.getByTestId('edit-message-input');
  await expect(editor).toBeVisible({ timeout: 10_000 });
  await editor.fill(newText);
  await page.getByTestId('edit-message-submit').click();
}

/** 只匹配含该文本的消息气泡 —— 本机跑时应用可能带着开发者的历史会话，
 *  按 .first() 定位会打到别人的消息上。 */
function bubbleWith(page: Page, role: 'user' | 'assistant', text: string) {
  return page
    .getByTestId(role === 'user' ? 'chat-message-user' : 'chat-message-assistant')
    .filter({ hasText: text });
}

test.describe('编辑提交失败不得静默丢弃', () => {
  // macOS CI 的 undici fetch 到本地 127.0.0.1 会失败(与 chat-edit-regenerate
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
    // 新建会话:helper 会把开发者本机的 config.json 拷进临时 MIQI_HOME,应用可能
    // 直接落在开发者的历史会话上。新会话归属本次 client,编辑/提交路径才是我们
    // 要钉的那条(CI 上本来就是干净状态)。
    await createNewConversation(page);
  }, 240_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
  });

  test(
    'truncate 失败时编辑框保持打开并给出原因，旧问答原地不动',
    { timeout: 180_000 },
    async () => {
      // 一轮正常问答,拿到可编辑的用户气泡
      await sendMessage(page, 'MOCK_REPLY:第一版回答内容');
      await expect(bubbleWith(page, 'assistant', '第一版回答内容')).toHaveCount(1, {
        timeout: 90_000,
      });
      // 等 turn 完全结束(handleEdit 有 streaming 守卫)
      await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
        timeout: 90_000,
      });
      await page.waitForTimeout(1500);

      await patchTruncateToFail(electronApp);
      await submitEdit(page, '改过的提问文本');

      // 先等提交路径走到终态：改前是「编辑框被关掉」，改后是「出原因行」。
      // 不能直接断言 —— 刚点完提交、onEdit 的 await 还没落地时编辑框本来就是
      // 可见的，那样 edit-message-input.toBeVisible() 在改前的代码上也会当场
      // 通过（本次实测确实假绿过一次）。
      await page.waitForFunction(
        () =>
          !document.querySelector('[data-testid="edit-message-input"]') ||
          !!document.querySelector('[data-testid="edit-message-error"]'),
        null,
        { timeout: 30_000 }
      );

      // 关键断言一:编辑框没关 —— 改前这里已随 setEditing(false) 消失
      await expect(page.getByTestId('edit-message-input')).toBeVisible({ timeout: 5_000 });
      // 用户的字还在里面,没有被吃掉
      await expect(page.getByTestId('edit-message-input')).toHaveValue('改过的提问文本');
      // 关键断言二:原因行出现且文案正确
      await expect(page.getByTestId('edit-message-error')).toBeVisible({ timeout: 5_000 });
      await expect(page.getByTestId('edit-message-error')).toHaveText(TRUNCATE_FAILED_TEXT);
      // 关键断言三:截断没成功,旧问答不得被删掉
      await expect(bubbleWith(page, 'assistant', '第一版回答内容')).toHaveCount(1);

      const shot = 'test-results/edit-failure-not-silent.png';
      await page.screenshot({ path: shot, fullPage: true });
      await postScreenshotToPr(
        shot,
        '✅ truncate 失败:编辑框保持打开、原因行就位、旧问答原地不动'
      );
    }
  );
});
