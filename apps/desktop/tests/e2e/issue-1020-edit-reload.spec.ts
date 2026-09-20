/**
 * 编辑重答后重载不再复活被截断的旧问答(#1020)回归测试。
 *
 * 场景:用户编辑消息重答,前端只截断渲染层;修复前 SessionManager(JSONL,重载读它)
 * 仍保留旧 turn,重载/重启后旧问答又出现。修复后编辑时先调 sessions.truncate 落盘,
 * 重载只看到新问答。
 *
 * 流程:发「第一版」→ 编辑成「第二版」→ 重启应用 → 断言「第一版」消失、「第二版」仍在。
 */
import { test, expect } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  launchElectronApp,
  relaunchElectronApp,
  closeElectronApp,
  sendMessage,
  waitForBridgeInitialized,
  waitForInputReady,
  userMessage,
} from './helpers/electron-setup';

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

test.describe.serial('编辑重答后重载不复活旧问答(#1020)', () => {
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
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
    mockServer?.kill();
  });

  test('编辑后重启,被截断的旧问答不再出现', { timeout: 180_000 }, async () => {
    await waitForBridgeInitialized(page);

    // 第一回合
    await sendMessage(page, 'MOCK_REPLY:第一版回答内容');
    const assistant = page.getByTestId('chat-message-assistant').first();
    await expect(assistant).toContainText('第一版回答内容', { timeout: 90_000 });
    // 等 turn 完全结束(停止生成按钮消失)——handleEdit 有 streaming 守卫
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });
    await page.waitForTimeout(1500);

    // 编辑 → 改成第二版 → 重答
    await page.getByTestId('chat-message-user').first().hover();
    await page.getByTestId('edit-message-btn').first().click();
    const editor = page.getByTestId('edit-message-input');
    await expect(editor).toBeVisible({ timeout: 10_000 });
    await editor.fill('MOCK_REPLY:第二版回答内容');
    await page.getByTestId('edit-message-submit').click();

    const assistantAfter = page.getByTestId('chat-message-assistant').first();
    await expect(assistantAfter).toContainText('第二版回答内容', { timeout: 90_000 });
    await expect(assistantAfter).not.toContainText('第一版回答内容');
    await expect(page.getByTestId('chat-message-user')).toHaveCount(1);
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });

    // 重启(保留数据) → 从 SessionManager 重载
    await closeElectronApp(electronApp); // 不带 miqiHome → 保留数据
    await new Promise((r) => setTimeout(r, 3000));
    const fixture2 = await relaunchElectronApp(miqiHome);
    electronApp = fixture2.electronApp;
    page = fixture2.page;
    await waitForInputReady(page, 60_000);

    // 断言:新问答仍在,被截断的旧问答不再复活
    await expect(userMessage(page, 'MOCK_REPLY:第二版回答内容')).toBeVisible({ timeout: 240_000 });
    await expect(page.getByTestId('chat-message-assistant').first()).toContainText(
      '第二版回答内容',
      { timeout: 90_000 }
    );
    await expect(page.getByTestId('chat-message-assistant').first()).not.toContainText(
      '第一版回答内容'
    );
    // 用户消息只剩编辑后的那一条
    await expect(page.getByTestId('chat-message-user')).toHaveCount(1);
    await expect(page.getByTestId('chat-message-user').first()).not.toContainText('第一版回答内容');
  });
});
