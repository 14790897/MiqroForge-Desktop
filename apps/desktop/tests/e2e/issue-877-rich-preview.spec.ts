/**
 * E2E for issue #877 — rich in-app preview for XLSX / DOCX / PDF.
 *
 * Three tracked-file scenarios, one per format, all through the same
 * Task Assets → preview modal path:
 *   1. XLSX → spreadsheet table render (cells + sheet tabs + merged cell)
 *   2. DOCX → rich document render (heading + table cells)
 *   3. PDF  → paginated iframe blob render (Chromium PDF viewer)
 *
 * Fixtures live in ./fixtures as base64 files generated once with the
 * backend's own libraries (openpyxl / python-docx / hand-built PDF).
 *
 * Run:
 *   cd apps/desktop
 *   npm run build && npx playwright test --config=playwright.config.ts --project=electron issue-877-rich-preview.spec.ts
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  launchElectronApp,
  closeElectronApp,
  waitForInputReady,
  ensurePersistedSession,
} from './helpers/electron-setup';

const FIXTURES_DIR = join(__dirname, 'fixtures');
const OUT_DIR = join(__dirname, '../../test-results/issue-877');

function fixtureBase64(name: string): string {
  return readFileSync(join(FIXTURES_DIR, name), 'utf8').trim();
}

/** Stage a tracked file inside the first session's files dir (mirrors
 *  proof-751: bare-name tracked record + file on disk). */
async function stageTrackedFile(
  electronApp: ElectronApplication,
  key: string,
  fileName: string,
  base64: string
): Promise<void> {
  await electronApp.evaluate(
    (_e, args) => {
      const fs = (process as any).getBuiltinModule('node:fs');
      const path = (process as any).getBuiltinModule('node:path');
      const home = process.env.MIQI_HOME;
      const safe = args.key.replace(/:/g, '_');
      const dir = path.join(home, 'workspace', 'sessions', safe);
      const filesDir = path.join(dir, 'files');
      fs.mkdirSync(filesDir, { recursive: true });
      fs.writeFileSync(path.join(filesDir, args.fileName), Buffer.from(args.base64, 'base64'));
      fs.writeFileSync(
        path.join(dir, 'tracked_files.json'),
        JSON.stringify({
          version: 1,
          files: {
            [args.fileName]: {
              op: 'write',
              name: args.fileName,
              lastSeen: Date.now(),
            },
          },
        })
      );
    },
    { key, fileName, base64 }
  );
}

async function openFirstPreview(page: Page): Promise<void> {
  await page.reload({ waitUntil: 'domcontentloaded' });
  await waitForInputReady(page, 60_000).catch(() => {});
  const previewBtn = page.locator('[data-testid="file-preview-btn"]').first();
  await expect(previewBtn).toBeVisible({ timeout: 20_000 });
  await previewBtn.click();
  await page.waitForTimeout(1500);
}

test.describe('issue #877 rich preview', () => {
  test('XLSX preview renders a spreadsheet table with sheet tabs', async () => {
    mkdirSync(OUT_DIR, { recursive: true });
    const fixture = await launchElectronApp();
    const electronApp = fixture.electronApp;
    const page = fixture.page;
    await waitForInputReady(page);

    const key = await ensurePersistedSession(page);

    await stageTrackedFile(
      electronApp,
      key,
      'preview-877.xlsx',
      fixtureBase64('preview-877.xlsx.b64')
    );
    await openFirstPreview(page);

    // Table cells from sheet 1 (including the merged-cell anchor).  First
    // assertion gets a generous timeout — the backend's first openpyxl import
    // can take ~30s on Windows runners.
    await expect(page.getByText('参数', { exact: true }).first()).toBeVisible({ timeout: 45_000 });
    await expect(page.getByText('温度', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('300', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('合并区标题', { exact: true }).first()).toBeVisible();

    // Sheet tab switch → sheet 2 content
    await page.getByRole('button', { name: '数据' }).click();
    await expect(page.getByText('2.5', { exact: true }).first()).toBeVisible({ timeout: 5_000 });

    // Unified toolbar: 下载/另存为 + 系统应用打开
    await expect(page.getByText('下载/另存为')).toBeVisible();
    await expect(page.getByText('系统应用打开')).toBeVisible();

    await page.screenshot({
      path: join(OUT_DIR, 'issue-877-xlsx-preview.png'),
      timeout: 5000,
    });
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('DOCX preview renders headings and table structure', async () => {
    mkdirSync(OUT_DIR, { recursive: true });
    const fixture = await launchElectronApp();
    const electronApp = fixture.electronApp;
    const page = fixture.page;
    await waitForInputReady(page);

    const key = await ensurePersistedSession(page);

    await stageTrackedFile(
      electronApp,
      key,
      'preview-877.docx',
      fixtureBase64('preview-877.docx.b64')
    );
    await openFirstPreview(page);

    await expect(page.getByText('实验结果', { exact: true }).first()).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText('本报告记录合成参数。', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('样品', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('MOF-5', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('87%', { exact: true }).first()).toBeVisible();

    await page.screenshot({
      path: join(OUT_DIR, 'issue-877-docx-preview.png'),
      timeout: 5000,
    });
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('PDF preview renders a paginated iframe blob', async () => {
    mkdirSync(OUT_DIR, { recursive: true });
    const fixture = await launchElectronApp();
    const electronApp = fixture.electronApp;
    const page = fixture.page;
    await waitForInputReady(page);

    const key = await ensurePersistedSession(page);

    await stageTrackedFile(
      electronApp,
      key,
      'preview-877.pdf',
      fixtureBase64('preview-877.pdf.b64')
    );
    await openFirstPreview(page);

    // The modal body hosts a blob iframe for Chromium's PDF viewer.
    const pdfFrame = page.locator('iframe[src^="blob:"]').last();
    await expect(pdfFrame).toBeVisible({ timeout: 10_000 });

    await page.screenshot({
      path: join(OUT_DIR, 'issue-877-pdf-preview.png'),
      timeout: 5000,
    });
    await closeElectronApp(electronApp).catch(() => {});
  });
});
