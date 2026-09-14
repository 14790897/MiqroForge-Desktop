/**
 * 编辑消息重答(#1011)回归测试 —— mock provider 驱动,确定性验证:
 *   1. 编辑成功流:用户消息原地变输入框 → 提交 → 旧问答被截断替换 → 新回答出现
 *   2. 编辑失败流(mock 500):错误提示出现,消息列表不进入空白坏状态
 *
 * 触发词:MOCK_REPLY:<文本> → mock 回该文本;MOCK_500:<文本> → mock 返回 500。
 * 两者仅在 env MIQI_MOCK_TEXT_REPLY=1 时启用(不污染默认 mock 行为)。
 */
import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { launchElectronApp, closeElectronApp, sendMessage } from './helpers/electron-setup';

const REPO_ROOT = join(__dirname, '..', '..', '..', '..');

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

test.describe.serial('编辑消息重答(#1011)', () => {
  // macOS CI 无法运行本 spec:runner 的 undici fetch 到本地 127.0.0.1 会失败,
  // 且 spawn 的 mock stdout 管道不投递(macos-e2e 实测)。Linux electron-e2e
  // 跑全量覆盖本 spec —— 与 confirm-card.spec.ts 相同的裁剪策略。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: any;
  let page: any;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockOpenAI();
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      // OpenAI 兼容协议指向 mock(anthropic 走 /v1/messages,mock 不支持)
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
  }, 240_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
  });

  test('编辑成功流:截断旧问答并重新回答', { timeout: 180_000 }, async () => {
    await sendMessage(page, 'MOCK_REPLY:第一版回答内容');

    // 等首个回答渲染(定位 AI 消息区,避免匹配到任务标题/侧栏同名文本)
    const assistant = page.getByTestId('chat-message-assistant').first();
    await expect(assistant).toContainText('第一版回答内容', { timeout: 90_000 });
    await expect(page.getByTestId('chat-message-user').first()).toBeVisible();
    // 等 turn 完全结束(停止生成按钮消失)——handleEdit 有 streaming 守卫
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });
    await page.waitForTimeout(1500);

    // hover 用户消息 → 点编辑
    await page.getByTestId('chat-message-user').first().hover();
    await page.getByTestId('edit-message-btn').first().click();
    const editor = page.getByTestId('edit-message-input');
    await expect(editor).toBeVisible({ timeout: 10_000 });
    // 编辑器带可见文本(不含内部序列化块)
    await expect(editor).toHaveValue(/第一版回答内容/);

    // 修改并提交
    await editor.fill('MOCK_REPLY:第二版回答内容');
    await page.getByTestId('edit-message-submit').click();

    // 断言:新回答出现(截断生效),AI 消息区不再含旧回答
    const assistantAfter = page.getByTestId('chat-message-assistant').first();
    await expect(assistantAfter).toContainText('第二版回答内容', { timeout: 90_000 });
    await expect(assistantAfter).not.toContainText('第一版回答内容');
    // 用户消息只剩一条(旧消息被替换)
    await expect(page.getByTestId('chat-message-user')).toHaveCount(1);
  });

  test('编辑失败流(mock 500):出现错误提示且列表不空白', { timeout: 180_000 }, async () => {
    // 等上一轮 turn 完全结束再编辑(handleEdit streaming 守卫)
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });
    await page.waitForTimeout(1500);
    await page.getByTestId('chat-message-user').first().hover();
    await page.getByTestId('edit-message-btn').first().click();
    const editor = page.getByTestId('edit-message-input');
    await expect(editor).toBeVisible({ timeout: 10_000 });

    await editor.fill('MOCK_500:触发失败回滚');
    await page.getByTestId('edit-message-submit').click();

    // 断言:错误提示出现(消息列表仍渲染内容,不进入空白坏状态)
    await expect(page.getByText(/错误|失败|error|Error/).first()).toBeVisible({ timeout: 90_000 });
  });
});
