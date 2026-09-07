/**
 * MCP 服务器集成 E2E — 桌面端 MCP 全链路。
 *
 * 链路：beforeAll 把 stdio MCP 服务器（本地 FastMCP 测试服务器）写进
 * config.json → 会话创建时 RuntimeSession 连接 MCP 服务器、把其工具注册
 * 进 ToolRegistry → mock LLM 调用 mcp_e2emcp_e2e_echo → 编排器执行包装器
 * → MCP 子进程真实执行 → 工具结果回传 → mock 仅在收到真实标记时输出
 * MCP-E2E-PASS。
 *
 * 另有一条 UI 链路：设置页添加服务器（uimcp）→ 列表显示 + config.json
 * 持久化。两条链路互相独立（重试安全：每个用例单独跑也能通过）。
 *
 * 不依赖真实 LLM：scripts/mock_mcp.py 按工具调用序列确定性推进。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "MCP 服务器"
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { existsSync, readFileSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  APPS_DESKTOP,
} from './helpers/electron-setup';
import { postScreenshotToPr } from './helpers/pr-image-post';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
const SERVER_NAME = 'e2emcp';
const ECHO_MARKER = 'MCP_ECHO_RESULT_7f3a9c';
const SERVER_SCRIPT = join(REPO_ROOT, 'scripts', 'mock_mcp_server.py');

/**
 * Start scripts/mock_mcp.py on an ephemeral port and wait for its startup
 * line (the server prints the ACTUAL bound port after bind) — same pattern
 * as the confirm-card spec's mock_openai.py launcher.
 */
async function startMockLLM(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_mcp.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock-mcp] ${t.trim()}`);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-mcp-err] ${String(d).trim()}`);
  });
  proc.on('exit', (code) => console.log(`[test] mock-mcp server exited: ${code}`));

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock-mcp server exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock-mcp startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[test] mock-mcp LLM ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl };
}

/**
 * Resolve the interpreter that runs the MCP test server: it must import
 * the `mcp` SDK.  Prefer the same interpreters the bridge uses — an
 * explicit MIQI_PYTHON_PATH (if it has mcp) or the repo venv; fall back
 * to `uv run python` (CI bridge fallback).
 */
function resolveMcpCommand(): { command: string; args: string[] } {
  const candidates: string[] = [];
  if (process.env.MIQI_PYTHON_PATH) candidates.push(process.env.MIQI_PYTHON_PATH);
  const venvPy =
    process.platform === 'win32'
      ? join(REPO_ROOT, '.venv', 'Scripts', 'python.exe')
      : join(REPO_ROOT, '.venv', 'bin', 'python');
  candidates.push(venvPy);
  for (const py of candidates) {
    if (!existsSync(py)) continue;
    const probe = spawnSync(py, ['-c', 'import mcp'], {
      encoding: 'utf8',
      timeout: 15_000,
      windowsHide: true,
    });
    if (probe.status === 0) {
      console.log(`[test] MCP server interpreter: ${py}`);
      return { command: py, args: [] };
    }
  }
  console.log('[test] MCP server interpreter: uv run python (fallback)');
  return { command: 'uv', args: ['run', 'python'] };
}

test.describe('MCP 服务器集成', () => {
  // macOS CI cannot run this spec: the runner's undici fetch fails against
  // a local 127.0.0.1 listener and the spawned mock's stdout pipe never
  // delivers — same trimming strategy as the confirm-card spec (#710).
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;
  let mcpCommand: { command: string; args: string[] };

  test.beforeAll(async () => {
    const mock = await startMockLLM();
    mockServer = mock.proc;
    mcpCommand = resolveMcpCommand();
    // Point EVERY configured provider at the mock — provider resolution
    // depends on the model in agents.defaults, so patching a single provider
    // would leak real API calls.  Preload the runtime-test MCP server
    // (config.json persists camelCase keys: tools.mcpServers) and drop any
    // user-configured servers so the run is deterministic.
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.mockUrl;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
      config.tools = {
        ...(config.tools ?? {}),
        mcpServers: {
          [SERVER_NAME]: {
            command: mcpCommand.command,
            args: [...mcpCommand.args, SERVER_SCRIPT],
            toolTimeout: 30,
            description: 'E2E 测试 MCP 服务器',
            lazy: false,
          },
        },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
  });

  // 失败时把 Playwright 自动截图贴到 PR 评论（CI 默认开启，本地 MQI_E2E_POST_IMG=1）
  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  test(
    '设置页添加 stdio MCP 服务器 → 列表显示并持久化到 config.json',
    { timeout: 120_000 },
    async () => {
      // ── 打开设置 → MCP 服务 tab ──
      const settingsBtn = page.locator('[data-testid="nav-system-settings"]');
      await expect(settingsBtn).toBeVisible({ timeout: 15_000 });
      await settingsBtn.click();
      const mcpTab = page.getByRole('tab', { name: /MCP 服务/ }).first();
      await expect(mcpTab).toBeVisible({ timeout: 10_000 });
      await mcpTab.click();
      await expect(page.getByRole('heading', { name: 'MCP 服务器' })).toBeVisible({
        timeout: 10_000,
      });

      // 预置的 e2emcp（beforeAll 写入 config.json）已出现在列表里
      await expect(page.getByText(SERVER_NAME, { exact: true })).toBeVisible({
        timeout: 15_000,
      });

      // ── 添加服务器弹窗 ──
      await page.getByRole('button', { name: '添加服务器' }).click();
      await expect(page.getByRole('heading', { name: '添加 MCP 服务器' })).toBeVisible();

      const uiName = 'uimcp';
      await page.getByPlaceholder('my-mcp-server').fill(uiName);
      await page.getByPlaceholder('npx').fill(mcpCommand.command);
      const argsStr =
        mcpCommand.args.length > 0 ? [...mcpCommand.args, SERVER_SCRIPT].join(', ') : SERVER_SCRIPT;
      await page.getByPlaceholder('-y, @modelcontextprotocol/server-filesystem').fill(argsStr);
      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-modal.png`,
      });
      await page.getByRole('button', { name: '保存', exact: true }).click();

      // ── 列表出现新服务器卡片（stdio 徽标） ──
      await expect(page.getByText(uiName, { exact: true })).toBeVisible({
        timeout: 15_000,
      });
      await expect(page.getByText('stdio', { exact: true })).toHaveCount(2);
      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-list.png`,
        fullPage: true,
      });
      await postScreenshotToPr(
        `test-results/${test.info().title.replace(/\s+/g, '-')}-list.png`,
        '✅ E2E 通过：设置页添加 MCP 服务器（uimcp）→ 列表显示 + config.json 持久化'
      );

      // ── config.json 持久化（文件存 camelCase 键：tools.mcpServers） ──
      const raw = readFileSync(join(miqiHome, 'config.json'), 'utf-8');
      const saved = JSON.parse(raw);
      const entry = saved?.tools?.mcpServers?.[uiName];
      expect(entry, 'config.json 应包含 tools.mcpServers.uimcp').toBeTruthy();
      expect(entry.command).toBe(mcpCommand.command);
      const savedArgs = entry.args ?? [];
      expect(savedArgs).toContain(SERVER_SCRIPT);
    }
  );

  test(
    '新建会话 → 模型调用 MCP 工具 → 最终回复含工具返回值标记',
    { timeout: LLM_TIMEOUT },
    async () => {
      // ── 新建会话（离开设置页）→ 新 RuntimeSession 在 start() 时连接 MCP ──
      await createNewConversation(page);
      await expect(page.locator('[data-testid="chat-input-container"]')).toBeVisible();

      // #952 回归守卫：E2E 应用不得继承开发机的平台登录态。开发模式
      // userData 按仓库隔离（index.ts），若 launchElectronApp 未隔离 qraft
      // store，登录态会恢复 → 真实网关凭据同步进临时 workspace 的
      // .qraft/token.json → 模型调用走真实网关、mock 收不到请求。
      const qraftState = await page.evaluate(async () => {
        try {
          return await (window as any).miqi.qraft.status();
        } catch {
          return null;
        }
      });
      expect(
        qraftState?.loggedIn,
        'E2E app must not inherit the developer machine platform login (see #952)'
      ).toBeFalsy();

      // ── 发送任务 → mock 第一轮即调用 mcp_e2emcp_e2e_echo ──
      await sendMessage(page, 'MCP 测试：请调用 MCP 工具并返回结果');

      await waitForResponseComplete(page, LLM_TIMEOUT);
      await expect(page.locator('main')).toContainText('MCP-E2E-PASS', {
        timeout: 30_000,
      });
      await expect(page.locator('main')).toContainText(ECHO_MARKER, {
        timeout: 30_000,
      });

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}-final.png`,
        fullPage: true,
      });
      await postScreenshotToPr(
        `test-results/${test.info().title.replace(/\s+/g, '-')}-final.png`,
        '✅ E2E 通过：新会话中模型调用 mcp_e2emcp_e2e_echo，真实 MCP 子进程返回标记，AI 回复 MCP-E2E-PASS'
      );
    }
  );
});
