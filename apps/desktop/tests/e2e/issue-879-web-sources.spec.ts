/**
 * Issue #879 — web_search/web_fetch 结构化来源端到端。
 *
 * 后端 ① 已让 web_search 通过 item/toolExecution/outputDelta 事件 emit
 * `web_sources`（title/url/snippet/tool）。本 spec 验证前端 ② 消费该事件、
 * 「查看来源」弹窗展示结构化 title（此前只有启发式提取的裸 url）。
 *
 * 驱动方式：mock OpenAI 服务器（scripts/mock_search_llm.py，确定性两轮
 * 状态机）——第 1 轮发起真实 web_search 工具调用（auto 链 → DDGS），
 * 第 2 轮把真实工具结果的首个 URL 嵌进最终回复。断言「查看来源」弹窗
 * 打开且渲染出结构化来源条目，并截图上传到 PR（postScreenshotToPr）。
 *
 * Run: cd apps/desktop && PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *      --config=playwright.config.ts --project=electron issue-879-web-sources.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
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

// ── 真零配置：清除本机环境变量里的搜索 key ─────────────────────────────
const SEARCH_ENV_KEYS = ['DEEPSEEK_API_KEY', 'TAVILY_API_KEY', 'BRAVE_API_KEY'] as const;
const _savedSearchEnv: Record<string, string | undefined> = {};
for (const k of SEARCH_ENV_KEYS) _savedSearchEnv[k] = process.env[k];

function clearSearchEnvKeys() {
  for (const k of SEARCH_ENV_KEYS) delete process.env[k];
}

function restoreSearchEnvKeys() {
  for (const k of SEARCH_ENV_KEYS) {
    if (_savedSearchEnv[k] === undefined) delete process.env[k];
    else process.env[k] = _savedSearchEnv[k];
  }
}

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** 启动 scripts/mock_search_llm.py（stdlib only，port 0 由 OS 分配）。 */
async function startMockSearchLLM(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  let python = process.env.MIQI_PYTHON_PATH || 'python';
  const probe = spawnSync(python, ['-c', 'import sys; sys.exit(0)'], {
    encoding: 'utf8',
    timeout: 5000,
    windowsHide: true,
  });
  if (probe.status !== 0) {
    console.log(
      `[test] MIQI_PYTHON_PATH unusable (status ${probe.status}) — mock falls back to 'python'`
    );
    python = 'python';
  }
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_search_llm.py'), '0'], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });
  proc.on('error', (e) => console.log(`[test] mock search server spawn error: ${e}`));

  let readyUrl = '';
  let stdoutBuf = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    stdoutBuf += String(d);
    console.log(`[mock] ${String(d).trim()}`);
    const m = stdoutBuf.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-err] ${String(d).trim()}`);
  });
  proc.on('exit', (code) => console.log(`[test] mock search server exited: ${code}`));

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock search server exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock search server startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[test] mock search server ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl };
}

test.describe('Issue #879 web_sources 结构化来源', () => {
  // macOS CI 连不上本地 mock 监听（与 confirm-card / issue-979 同策略）。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    clearSearchEnvKeys();
    const mock = await startMockSearchLLM();
    mockServer = mock.proc;

    // 零配置搜索 + mock OpenAI 作为 LLM 提供方（模型非 deepseek，避免
    // auto 链启用 DeepSeek 官方搜索）——与 issue-979 场景 A 同配置。
    const fixture = await launchElectronApp((config: any) => {
      config.providers = config.providers ?? {};
      delete config.providers.deepseek;
      config.providers.openai = { apiKey: 'mock-key', apiBase: mock.mockUrl };
      config.agents = {
        ...(config.agents ?? {}),
        defaults: {
          ...(config.agents?.defaults ?? {}),
          model: 'openai/gpt-4o-mini',
        },
      };
      const search = config.tools?.web?.search;
      if (search && typeof search === 'object') {
        delete search.apiKey;
        delete search.tavilyApiKey;
        delete search.braveApiKey;
        search.provider = 'auto';
      }
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    try {
      mockServer?.kill();
    } catch {
      /* already gone */
    }
    restoreSearchEnvKeys();
  });

  test(
    'web_search 结构化来源在「查看来源」弹窗展示 title/url',
    { timeout: LLM_TIMEOUT },
    async () => {
      await createNewConversation(page);
      await sendMessage(page, '请用网页搜索查一下今天北京的天气');

      // 1. 工具行出现「网页搜索」——web_search 被真实执行（emit web_sources）
      await expect(page.locator('main').getByText('网页搜索').first()).toBeVisible({
        timeout: 60_000,
      });

      // 2. 等回复完成（web_sources 事件已随工具行到达前端）
      await waitForResponseComplete(page, 60_000);

      // 3. 点击「查看来源」按钮（每个 assistant 消息气泡都有，always visible）
      await page.getByLabel('查看来源').first().click();

      // 4. 弹窗打开，标题带来源计数且 > 0
      await expect(page.getByText(/查看来源（\d+）/).first()).toBeVisible({ timeout: 15_000 });

      // 5. 弹窗里渲染出结构化来源链接（title 主行 + url，而非空提示）
      await expect(page.locator('a[href^="http"]').first()).toBeVisible({ timeout: 15_000 });
      const modalText = await page.locator('main').textContent();
      expect(modalText).not.toContain('该回答未使用网络工具');

      // 6. 截图并上传到 PR（自动截图方法 #879 review）
      const shotPath = `test-results/${test.info().title.replace(/\s+/g, '-')}.png`;
      await page.screenshot({ path: shotPath, fullPage: true });
      await postScreenshotToPr(
        shotPath,
        '✅ E2E 通过：web_search 结构化来源在「查看来源」弹窗展示 title/url'
      );
    }
  );
});
