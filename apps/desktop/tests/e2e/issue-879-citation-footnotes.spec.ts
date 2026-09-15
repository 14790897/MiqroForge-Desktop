/**
 * Issue #879 — 正文 [n] 脚注可点击 → 来源详情 端到端。
 *
 * #671 已让模型输出 `[n]` 脚注 + 文末「参考文献」列表；本 spec 验证前端
 * 解析这些参考文献、把正文 `[n]` 渲染成可点击脚注，点击后弹出「来源详情」
 * （题名/作者/年份/DOI）。驱动方式：mock OpenAI（scripts/mock_citation_llm.py）
 * 直接返回带 [1][2] + 参考文献的纯文本回复（无工具调用 / 无审批）。
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
    const mock = await startMockCitationLLM();
    mockServer = mock.proc;

    const fixture = await launchElectronApp((config: any) => {
      // Point EVERY configured provider at the mock（provider resolution 由
      // agents.defaults.model 决定，fast 模式可能走 deepseek 等非 openai 路径）
      // —— mock 忽略 model 名/key，见 regression-delete-all-focus.spec.ts。
      const providers = config.providers ?? {};
      for (const [name, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.mockUrl;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
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

  test('正文 [n] 脚注可点击，点开显示题名/作者/年份/DOI', { timeout: LLM_TIMEOUT }, async () => {
    await createNewConversation(page);
    await sendMessage(page, 'MOF 造粒如何避免 BET 损失？');

    // 1. 等待 [n] 脚注渲染成可点击 citation（需要全文 + 参考文献到位）。
    //    正文 [1] 与参考文献列表的 [1] 都会 linkify → 取第一个（正文里的）。
    await expect(page.getByTestId('citation-ref-1').first()).toBeVisible({ timeout: 90_000 });

    // 2. 点击 [1] 脚注 → 来源详情弹窗。
    await page.getByTestId('citation-ref-1').first().click();

    // 3. 弹窗内展示题名/作者/期刊/年份/DOI（scope 到 dialog 避免误匹配）。
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByText('参考文献 [1]')).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText('张三')).toBeVisible();
    await expect(dialog.getByText('MOF 造粒工艺综述')).toBeVisible();
    await expect(dialog.getByText('材料学报')).toBeVisible();
    await expect(dialog.getByText('10.1016/j.matt.2023.01.001')).toBeVisible();

    // 4. 截图并上传到 PR。
    const shotPath = 'test-results/issue-879-citation-footnotes.png';
    await page.screenshot({ path: shotPath, fullPage: true });
    await postScreenshotToPr(
      shotPath,
      '✅ E2E 通过：正文 [n] 脚注可点击，点开显示题名/作者/年份/DOI'
    );
  });
});
