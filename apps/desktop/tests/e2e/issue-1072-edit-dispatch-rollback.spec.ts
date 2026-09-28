/**
 * #1072 回归:编辑重答失败时,「是否回滚截断」必须由**请求是否真的派发**决定,
 * 而不是由「是否调用过 chat.send」推测。
 *
 * 两个场景在 UI 上都表现为「编辑提交后失败」,但正确行为相反:
 *   1. 确定未派发(bridge 未运行 / 参数构造失败 / 序列化失败)
 *      → 恢复截断前的完整列表 + 错误提示(main 以带标记的结果正常返回)
 *   2. 已派发但 IPC 失败(进程退出 / 热重载重启 / 超时)
 *      → 保留截断后的列表 —— 恢复旧列表会与已接收请求的后端状态分叉
 *
 * 触发方式:用 electronApp.evaluate 改写 main 进程的 chat:send handler。
 * contextBridge 冻结了渲染层的 window.miqi.*,page.evaluate 覆盖会被静默丢弃,
 * 只能改主进程(repro-570-silent-send.spec.ts 的既有手法)。
 *
 * 顺序敏感(describe.serial):场景二承接场景一恢复出来的列表,不复原 handler。
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
const CHAT_SEND = 'chat:send';

/** 编辑失败后用户可见的运行时错误文案(sanitizeUiMessage 把 bridge 侧失败统一成它)。 */
const RUNTIME_DOWN_TEXT = '运行时未启动或正在重启';

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

/**
 * 把 main 的 chat:send 换成受控返回:
 *  · 'not-dispatched' —— 模拟「请求从未写入 bridge 管道」,main 判为未派发
 *  · 'reject' —— 模拟「请求已写出后失败」(进程退出 / 重启 / 超时)
 */
async function patchChatSend(
  electronApp: ElectronApplication,
  mode: 'not-dispatched' | 'reject'
): Promise<void> {
  await electronApp.evaluate(
    async ({ ipcMain }, arg: { channel: string; mode: string }) => {
      ipcMain.removeHandler(arg.channel);
      ipcMain.handle(arg.channel, async () => {
        if (arg.mode === 'reject') {
          throw new Error('Bridge stopped — request cancelled');
        }
        return { __miqiChatNotDispatched: true, message: 'Bridge not running' };
      });
    },
    { channel: CHAT_SEND, mode }
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

/** 只匹配含该文本的消息气泡 —— 本机跑时应用可能带着开发者的历史会话,
 *  按 .first() 定位会打到别人的消息上。 */
function bubbleWith(page: Page, role: 'user' | 'assistant', text: string) {
  return page
    .getByTestId(role === 'user' ? 'chat-message-user' : 'chat-message-assistant')
    .filter({ hasText: text });
}

test.describe.serial('编辑重答失败的派发三态判定(#1072)', () => {
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
    // 直接落在开发者的历史会话上 —— 那种 legacy 会话的 sessions.truncate 会因归属
    // 校验失败,而 handleEdit 对截断失败是静默 return(编辑根本不发生)。新会话归属
    // 本次 client,截断/编辑路径才可复现(CI 上本来就是干净状态)。
    await createNewConversation(page);
  }, 240_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
  });

  test('场景一:确定未派发 → 恢复被截断的旧问答', { timeout: 180_000 }, async () => {
    // 一轮正常问答,拿到可编辑的消息
    await sendMessage(page, 'MOCK_REPLY:第一版回答内容');
    await expect(bubbleWith(page, 'assistant', '第一版回答内容')).toHaveCount(1, {
      timeout: 90_000,
    });
    // 等 turn 完全结束(handleEdit 有 streaming 守卫)
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });
    await page.waitForTimeout(1500);

    await patchChatSend(electronApp, 'not-dispatched');
    await submitEdit(page, 'MOCK_REPLY:第二版回答内容');

    // 错误提示(未派发的失败照旧提示用户)
    await expect(page.getByText(RUNTIME_DOWN_TEXT)).toBeVisible({ timeout: 60_000 });
    // 关键断言:被截掉的旧回答整组回来了,编辑后的新文本不得留下
    // —— 不恢复时这里恰好相反(旧回答消失、新气泡留着)
    await expect(bubbleWith(page, 'assistant', '第一版回答内容')).toHaveCount(1);
    await expect(bubbleWith(page, 'user', '第二版回答内容')).toHaveCount(0);
    // 还输入框:恢复必须把编辑后的文本放回 composer —— 气泡被回滚掉了,
    // 输入框是用户重发这段内容的唯一去处(setText 被删掉要能红)
    await expect(page.locator('[data-testid="chat-input-container"] textarea')).toHaveValue(
      'MOCK_REPLY:第二版回答内容'
    );

    // 证据截图(CI 默认贴到 PR,本地静默跳过) —— 见 helpers/pr-image-post
    const shot = 'test-results/issue-1072-not-dispatched-restored.png';
    await page.screenshot({ path: shot, fullPage: true });
    await postScreenshotToPr(
      shot,
      '✅ 确定未派发:旧问答被恢复到截断前,编辑后的新文本未留下,错误提示就位'
    );
  });

  test('场景二:已派发后 IPC 失败 → 保留截断后的列表', { timeout: 180_000 }, async () => {
    // 承接场景一:列表已被恢复成 [用户「第一版」, 回答「第一版」, 错误]
    await patchChatSend(electronApp, 'reject');
    await submitEdit(page, 'MOCK_REPLY:第三版回答内容');

    await expect(page.getByText(RUNTIME_DOWN_TEXT).last()).toBeVisible({ timeout: 60_000 });
    // 关键断言:不回滚 —— 编辑后的新消息留着,被截掉的旧回答不得复活
    await expect(bubbleWith(page, 'user', '第三版回答内容')).toHaveCount(1);
    await expect(bubbleWith(page, 'assistant', '第一版回答内容')).toHaveCount(0);

    const shot = 'test-results/issue-1072-dispatched-kept.png';
    await page.screenshot({ path: shot, fullPage: true });
    await postScreenshotToPr(
      shot,
      '✅ 已派发后 IPC 失败:截断后的列表保留(新消息在、旧回答不复活),错误提示就位'
    );
  });
});
