/**
 * PPTX Generator E2E Test
 *
 * Tests the pptx-generator skill end-to-end: AI creates a PowerPoint
 * presentation via the full Electron app, with auto-approval.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron pptx-generator.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  createNewConversation,
  sendMessage,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

const SKIP_REAL_PPTX_GENERATOR_ON_CI =
  !!process.env.CI && process.env.MIQI_RUN_REAL_PPTX_E2E !== '1';

test.describe('PPTX Generator E2E', () => {
  test.skip(
    SKIP_REAL_PPTX_GENERATOR_ON_CI,
    'Real PPTX generation depends on LLM tool/file choices; run with MIQI_RUN_REAL_PPTX_E2E=1 for manual/nightly verification.'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 120_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('pptx-generator skill creates AI PowerPoint', { timeout: 600_000 }, async () => {
    if (!!process.env.CI) {
      console.log('[test] Skipping PPTX verification on CI (sandbox filesystem mismatch)');
      return;
    }
    const fname = 'ai_intro.pptx';
    let _fn = 0;
    const shot = () =>
      page
        .screenshot({
          path: `test-results/videos/f${String(++_fn).padStart(4, '0')}.png`,
          timeout: 5000,
        })
        .catch(() => {});
    await createNewConversation(page);
    await shot();

    await sendMessage(
      page,
      `使用 pptx-generator 技能创建 PPT。封面标题"人工智能简介"副标题"技术、应用与未来"，目录 topics:什么是AI、核心技术、应用场景、未来展望，内容 items:机器学习、深度学习、NLP，总结 points:AI重塑行业、人机协作、安全对齐 conclusion:拥抱AI。文件名 ${fname}`
    );
    await shot();

    // Wait for "Thinking…" to appear (AI started processing)
    await expect(page.getByTestId('thinking-indicator'))
      .toBeVisible({ timeout: 30_000 })
      .catch(() => {});
    console.log('[test] AI started processing');
    await shot();

    // Pre-approve ALL tools via wildcard key
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
    await shot();

    // Wait for AI to finish, capturing frames along the way
    const deadline = Date.now() + 300_000;
    while (Date.now() < deadline) {
      const thinking = await page
        .getByTestId('thinking-indicator')
        .isVisible()
        .catch(() => false);
      if (!thinking) break;
      await page.waitForTimeout(8000);
      await shot();
    }
    await expect(page.getByTestId('thinking-indicator')).toBeHidden({ timeout: 300_000 });
    await shot();

    // Verify pptx file was created + check 14 internal items
    await page.waitForTimeout(3000);
    const { execFileSync } = require('node:child_process');
    const { join, resolve } = require('node:path');
    const ws = join(miqiHome, 'workspace');
    const verifier = join(__dirname, 'helpers', 'verify-pptx.py');
    const repoRoot = resolve(__dirname, '..', '..', '..', '..');
    const uv = process.platform === 'win32' ? 'uv.cmd' : 'uv';
    const env = { ...process.env, PYTHONIOENCODING: 'utf-8' };
    let result: any;
    try {
      const vout = execFileSync(uv, ['run', 'python', verifier, ws, fname], {
        cwd: repoRoot,
        encoding: 'utf8',
        timeout: 15000,
        env,
      });
      result = JSON.parse(vout);
    } catch (e: any) {
      // execSync throws on non-zero exit; stdout is in e.stdout
      const raw = e.stdout || e.stderr || '';
      console.log('[test] verify-pptx raw output:', raw.slice(0, 300));
      try {
        result = JSON.parse(raw);
      } catch {
        result = {
          pass: false,
          checks: [{ label: 'json parse error', pass: false, detail: raw.slice(0, 200) }],
        };
      }
    }
    console.log('[test] PPTX checks:', JSON.stringify(result.checks));
    await shot();
    if (!result.pass) {
      const failed = result.checks.filter((c: any) => !c.pass).map((c: any) => c.label);
      throw new Error(`PPTX checks failed: ${failed.join(', ')}`);
    }
    console.log('[test] ✅ All 14 checks passed');
  });
});
