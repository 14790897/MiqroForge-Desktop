/**
 * Issue #879 — 正文 [n] 脚注可点击 → 来源详情 端到端（真实来源）。
 *
 * 驱动方式：mock OpenAI（scripts/mock_citation_llm.py）两轮状态机——
 * 第 1 轮真实执行 web_search（auto 链 → 零配置 DDGS），第 2 轮把真实结果的
 * 标题 + URL 写成 `[n]` 脚注 + 文末「参考文献」。断言正文 [n] 可点击、点开
 * 来源详情弹窗展示真实题名 + 真实链接（非硬编码占位）。
 *
 * Run: cd apps/desktop && PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *      --config=playwright.config.ts --project=electron issue-879-citation-footnotes.spec.ts
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  LLM_TIMEOUT,
  sendMessage,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  APPS_DESKTOP,
} from './helpers/electron-setup';
import { postScreenshotToPr } from './helpers/pr-image-post';

// ── 真零配置搜索：清除本机环境变量里的搜索 key（回退 DDGS）──────────────
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

/** 启动 scripts/mock_citation_llm.py（stdlib only，port 0 由 OS 分配）。 */
async function startMockCitationLLM(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  let python = process.env.MIQI_PYTHON_PATH || 'python';
  const probe = spawnSync(python, ['-c', 'import sys; sys.exit(0)'], {
    encoding: 'utf8',
    timeout: 5000,
    windowsHide: true,
  });
  if (probe.status !== 0) python = 'python';

  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_citation_llm.py'), '0'], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });
  proc.on('error', (e) => console.log(`[test] mock citation server spawn error: ${e}`));

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
  });
  proc.on('exit', (code) => console.log(`[test] mock citation server exited: ${code}`));

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock citation server exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock citation server startup line not seen in 30s: ${stderrTail}`);
  }
  return { proc, mockUrl: readyUrl };
}

test.describe('Issue #879 [n] 脚注 → 来源详情', () => {
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
    const mock = await startMockCitationLLM();
    mockServer = mock.proc;

    const fixture = await launchElectronApp((config: any) => {
      // Point EVERY configured provider at the mock（provider resolution 由
      // agents.defaults.model 决定，fast 模式可能走 deepseek 等非 openai 路径）
      // —— mock 忽略 model 名/key。
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.mockUrl;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;

      // 显式旁路所有审批（camelCase + snake_case），否则 web_search 网络审批
      // 弹窗会阻塞真搜索。
      config.approvals = {
        ...(config.approvals ?? {}),
        bypassAll: true,
        bypass_all: true,
        bypassNetworkApproval: true,
        bypassToolConfirmation: true,
        bypassCommandApproval: true,
        bypassFileWriteApproval: true,
      };

      // 零配置搜索：清 key + provider=auto → DDGS（真搜索）。
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

  test('正文 [n] 脚注可点击，点开显示真实题名/链接', { timeout: LLM_TIMEOUT }, async () => {
    await createNewConversation(page);
    await sendMessage(page, 'MOF 造粒如何避免 BET 损失？');

    // 1. 等待 [n] 脚注渲染成可点击 citation（需要真搜索完成 + 参考文献到位）。
    //    正文 [1] 与参考文献列表的 [1] 都会 linkify → 取第一个（正文里的）。
    await expect(page.getByTestId('citation-ref-1').first()).toBeVisible({ timeout: 120_000 });

    // 2. 点击 [1] 脚注 → 来源详情弹窗。
    await page.getByTestId('citation-ref-1').first().click();

    // 3. 弹窗展示真实来源：题名字段 + 真实 http 链接（web 结果无作者/年份/DOI）。
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('参考文献 [1]')).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText('题名')).toBeVisible();
    const link = dialog.locator('a[href^="http"]').first();
    await expect(link).toBeVisible();
    const href = (await link.getAttribute('href')) ?? '';
    expect(href).toContain('http');

    // 4. 截图并上传到 PR。
    const shotPath = 'test-results/issue-879-citation-footnotes.png';
    await page.screenshot({ path: shotPath, fullPage: true });
    await postScreenshotToPr(shotPath, '✅ E2E 通过：正文 [n] 脚注可点击，点开显示真实题名/链接');
  });
});
