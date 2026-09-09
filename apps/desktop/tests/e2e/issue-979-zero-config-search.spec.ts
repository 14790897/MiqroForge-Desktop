/**
 * Issue #979 — 搜索 auto 链兜底 E2E。
 *
 * 场景 A（零配置）：清空全部搜索配置（无 Tavily/Brave key、无 DeepSeek
 * 官方 key、环境变量无搜索 key）时，agent 调用 web_search 走 auto 链
 * 回落 DDGS 并真实返回结果。
 *
 * 场景 B（DeepSeek 配置了但搜索不可用）：LLM 提供方为 DeepSeek（模型
 * deepseek/deepseek-v4-flash）但 apiBase 是中转站（无 /responses 端点，
 * 不支持官方联网搜索）→ auto 链跳过 DeepSeek 搜索、自动切换 DDGS 返回
 * 真实结果（#979：DeepSeek 配置不能搜索也要自动切换）。
 *
 * 驱动方式：mock OpenAI 服务器（scripts/mock_search_llm.py，确定性两轮
 * 状态机）作为 LLM 提供方 —— 第 1 轮发起真实 web_search 工具调用（经
 * 应用运行时真实执行，auto 链 → DDGS 真实网络请求），第 2 轮把真实工具
 * 结果中的首个 URL 嵌进最终回复（SEARCH_OK|{url}）。断言最终回复携带
 * 真实 URL，即证明 auto 链在应用内端到端可用。
 *
 * 场景 A 刻意不用 DeepSeek 作为 LLM 提供方：DeepSeek 模型 + 官方 base
 * 会让 auto 链自动启用 DeepSeek 官方搜索（#844 设计），测不到纯 DDGS
 * 兜底路径。
 *
 * Run: cd apps/desktop && PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *      --config=playwright.config.ts --project=electron issue-979-zero-config-search.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
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

// ── 真零配置：清除本机环境变量里的搜索 key ─────────────────────────────
// WebSearchTool 构造时会把 DEEPSEEK_API_KEY/TAVILY_API_KEY/BRAVE_API_KEY
// 环境变量当兜底配置（web.py）——不删掉它们，零配置就不成立。
for (const k of ['DEEPSEEK_API_KEY', 'TAVILY_API_KEY', 'BRAVE_API_KEY']) {
  delete process.env[k];
}

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** 启动 scripts/mock_search_llm.py（stdlib only，ephemeral 端口）。 */
async function startMockSearchLLM(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_search_llm.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock] ${t.trim()}`);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
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

/** 断言零配置/DeepSeek 不可用场景下 web_search 经 DDGS 真实返回结果。 */
async function expectSearchOkViaDdgs(page: Page) {
  // 1. 工具行出现「网页搜索」——web_search 被真实执行（非错误短路上报）
  await expect(page.locator('main').getByText('网页搜索').first()).toBeVisible({
    timeout: 60_000,
  });

  // 2. mock 把真实工具结果的首个 URL 嵌进最终回复：DDGS 兜底返回了真实结果
  await expect(
    page
      .getByTestId('chat-message-assistant')
      .getByText(/SEARCH_OK\|https?:\/\//)
      .first()
  ).toBeVisible({ timeout: 180_000 });

  // 3. 失败路径不得出现（auto 链不应报网络/限流/余额错误而中断）
  await waitForResponseComplete(page, 60_000);
  const mainText = await page.locator('main').textContent();
  expect(mainText).not.toContain('网络搜索失败');
  expect(mainText).not.toContain('SEARCH_FAILED');
  expect(mainText).not.toContain('余额不足');
}

test.describe('Issue #979 场景 A：零配置搜索 DDGS 兜底', () => {
  // macOS CI 连不上本地 mock 监听（与 confirm-card 同策略，见该 spec 注释）。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockSearchLLM();
    mockServer = mock.proc;

    // 零配置搜索 + mock OpenAI 作为 LLM 提供方：
    //  - providers.deepseek 必须不存在（否则 auto 链启用 DeepSeek 官方搜索）
    //  - 模型用 openai/gpt-4o-mini（非 deepseek 模型 → 不启用对应模型搜索）
    //  - tools.web.search 的 key 全部清除，provider 保持 auto
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
  });

  test(
    '零配置发起 web_search → auto 链回落 DDGS → 真实结果返回',
    { timeout: LLM_TIMEOUT },
    async () => {
      await createNewConversation(page);
      await sendMessage(page, '请用网页搜索查一下今天北京的天气');

      await expectSearchOkViaDdgs(page);

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });
    }
  );
});

test.describe('Issue #979 场景 B：DeepSeek 中转站 base（搜索不可用）自动切换 DDGS', () => {
  // macOS CI 连不上本地 mock 监听（与 confirm-card 同策略，见该 spec 注释）。
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockSearchLLM();
    mockServer = mock.proc;

    // DeepSeek 作为 LLM 提供方（模型 deepseek/deepseek-v4-flash），但
    // apiBase 指向 mock（等价中转站：无 /responses 端点，_is_official_
    // deepseek_base 判定失败）→ auto 链跳过 DeepSeek 官方搜索，自动
    // 切换 DDGS。LLM 请求本身经 mock 正常驱动 web_search 工具调用。
    const fixture = await launchElectronApp((config: any) => {
      config.providers = config.providers ?? {};
      config.providers.deepseek = { apiKey: 'mock-key', apiBase: mock.mockUrl };
      config.agents = {
        ...(config.agents ?? {}),
        defaults: {
          ...(config.agents?.defaults ?? {}),
          model: 'deepseek/deepseek-v4-flash',
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
  });

  test(
    'DeepSeek 中转站 base → 官方搜索不可用 → 自动切换 DDGS 返回真实结果',
    { timeout: LLM_TIMEOUT },
    async () => {
      await createNewConversation(page);
      await sendMessage(page, '请用网页搜索查一下今天北京的天气');

      await expectSearchOkViaDdgs(page);

      await page.screenshot({
        path: `test-results/${test.info().title.replace(/\s+/g, '-')}.png`,
        fullPage: true,
      });
    }
  );
});
