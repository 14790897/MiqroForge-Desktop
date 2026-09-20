/**
 * 编辑重答上下文截断(#1146)回归测试 —— mock provider 驱动,确定性验证:
 *   编辑第 2 回合重答后,新回合的模型上下文(mock 回显)不再含被替换的旧回合,
 *   只保留编辑点之前的回合 —— 击穿「前端只截渲染层、后端上下文 stale」的假象。
 *
 * 触发词(仅在 env MIQI_MOCK_TEXT_REPLY=1 时启用,不污染默认 mock 行为):
 *   MOCK_REPLY:<文本>   → mock 回该文本
 *   MOCK_ECHO_CTX:<文本> → mock 回显本请求的完整 messages 摘要(CTX:user:... | assistant:...)
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

test.describe.serial('编辑重答上下文截断(#1146)', () => {
  // macOS CI 无法运行本 spec:runner 的 undici fetch 到本地 127.0.0.1 会失败,
  // 与 chat-edit-regenerate.spec.ts 相同的裁剪策略(Linux electron-e2e 跑全量覆盖)。
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

  test('编辑第 2 回合重答:模型上下文只保留第 1 回合', { timeout: 180_000 }, async () => {
    // 第 1 回合
    await sendMessage(page, 'MOCK_REPLY:第一回合答案');
    await expect(page.getByTestId('chat-message-assistant').first()).toContainText(
      '第一回合答案',
      { timeout: 90_000 }
    );
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });
    await page.waitForTimeout(1500);

    // 第 2 回合
    await sendMessage(page, 'MOCK_REPLY:第二回合答案');
    await expect(page.getByTestId('chat-message-assistant').last()).toContainText(
      '第二回合答案',
      { timeout: 90_000 }
    );
    await expect(page.getByRole('button', { name: '停止生成' })).toHaveCount(0, {
      timeout: 90_000,
    });
    await page.waitForTimeout(1500);

    // 编辑第 2 回合用户消息 → 触发 mock 回显完整上下文
    const secondUser = page.getByTestId('chat-message-user').nth(1);
    await secondUser.hover();
    await secondUser.getByTestId('edit-message-btn').click();
    const editor = page.getByTestId('edit-message-input');
    await expect(editor).toBeVisible({ timeout: 10_000 });
    await editor.fill('MOCK_ECHO_CTX:探针');
    await page.getByTestId('edit-message-submit').click();

    // 回显的上下文:含第 1 回合,不含被替换的第 2 回合。
    const echo = page.getByTestId('chat-message-assistant').last();
    await expect(echo).toContainText('CTX:', { timeout: 90_000 });
    await expect(echo).toContainText('第一回合答案');
    await expect(echo).not.toContainText('第二回合答案');
  });
});
