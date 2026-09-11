/**
 * PDF Read E2E — issue #805 regression
 *
 * Regression coverage for #805 (PR #840): `pdf_read` 无论传 `file_path`
 * 还是 `filename` 都必现报「必须提供 file_path」，PDF 读取通道不可用。
 * 根因是 orchestrator 的 `_normalize_tool_args` 别名表把 `file_path`
 * 无差别改写为 `path`，而 `PdfReadTool.execute()` 只读 `file_path`
 * （修复：归一化改为 schema 感知 + `filename→file_path` 映射 +
 * 归一化前移到 validate_params 之前）。
 *
 * The spec drives the real chat flow: a marker PDF is written into the
 * session workspace, the agent is asked to read it via the `pdf_read`
 * tool, and the user-visible outcome is asserted — the marker text
 * extracted by pypdfium2 must appear in the model's reply.
 *
 * Run:
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron pdf-read.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  waitForBridgeInitialized,
  sendMessage,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

const MARKER = 'E2E_PDF_READ_MARKER_805';

/** Hand-crafted minimal PDF whose text layer (Helvetica Tj) is extractable
 *  by pypdfium2 — no OCR dependency on CI. Verified locally with
 *  pypdfium2.get_textpage(). */
function makeMarkerPdf(): Buffer {
  const stream = `BT /F1 24 Tf 72 720 Td (${MARKER}) Tj ET`;
  return Buffer.from(
    '%PDF-1.4\n' +
      '1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n' +
      '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' +
      '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R' +
      '/Resources<</Font<</F1 5 0 R>>>>>>endobj\n' +
      `4 0 obj<</Length ${Buffer.byteLength(stream)}>>stream\n${stream}\nendstream\nendobj\n` +
      '5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n' +
      'xref\n0 6\n' +
      '0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n' +
      '0000000115 00000 n \n0000000214 00000 n \n0000000270 00000 n \n' +
      'trailer<</Size 6/Root 1 0 R>>\nstartxref\n330\n%%EOF\n',
    'utf-8'
  );
}

test.describe('PDF Read E2E (issue #805)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let pdfPath: string;

  test.beforeAll(async () => {
    // 与 CI 环境对齐：CI 的 config.json 使用 app_server runtime；本地用户配置
    // 可能是 legacy，导致 agent 执行路径与 CI 不一致。显式 patch 保证
    // spec 在本地/CI 行为一致（issue #805 修复作用于两条路径的公共
    // orchestrator 归一化层，app_server 路径同样覆盖）。
    const fixture = await launchElectronApp((config) => {
      config.agents = {
        ...config.agents,
        defaults: { ...config.agents?.defaults, runtime: 'app_server' },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // 预置含标记文本的 PDF 到工作区根目录（pdf_read 的 workspace 相对路径基座）
    const workspaceDir = join(miqiHome, 'workspace');
    mkdirSync(workspaceDir, { recursive: true });
    const abs = join(workspaceDir, 'e2e-read-test.pdf');
    writeFileSync(abs, makeMarkerPdf());
    // 转正斜杠：Windows 反斜杠在提示词/LLM 传参中易被转义
    pdfPath = abs.split('\\').join('/');

    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('pdf_read 读取工作区 PDF 并返回标记文本', { timeout: 8 * 60_000 }, async () => {
    await sendMessage(
      page,
      `请调用 pdf_read 工具读取文件 ${pdfPath}，告诉我文件里包含的标记文本是什么，原样输出。`
    );
    // agent 可能对 pdf_read/skill_manage 调用发起审批弹窗（app_server
    // 路径 bypass_all 不总是生效）：持续点击"永久允许"，同时轮询
    // assistant 气泡直到包含标记文本。不依赖 waitForResponseComplete
    // / approveLoop——它们会在 agent 长时间思考（文本无变化）时提前返回。
    const deadline = Date.now() + 7 * 60_000;
    while (Date.now() < deadline) {
      const approveBtn = page.getByTestId('approval-allow-permanent');
      if (await approveBtn.isVisible({ timeout: 500 }).catch(() => false)) {
        await approveBtn.click();
      }
      const reply = page.locator('[data-testid="chat-message-assistant"]').last();
      if ((await reply.count()) > 0) {
        const txt = await reply.textContent().catch(() => '');
        if (txt && txt.includes(MARKER)) break;
      }
      await page.waitForTimeout(1000);
    }
    await expect(page.locator('[data-testid="chat-message-assistant"]').last()).toContainText(
      MARKER,
      { timeout: 10_000 }
    );
  });
});
