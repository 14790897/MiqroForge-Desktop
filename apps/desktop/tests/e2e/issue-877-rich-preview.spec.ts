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
 * Flake history (desktop-ci run 35064775546, XLSX case): the staged entry was
 * written to disk, the page reloaded, and the preview button asserted
 * visible within a fixed 20s — both the first attempt and the retry timed out
 * ("element(s) not found") while the same flow completes in ~12s on an idle
 * machine.  The panel paints only after the reload's restore round-trip
 * (`sessions.get` with its retry/backoff chain, then `getTrackedFiles`), so a
 * loaded CI runner can outrun any fixed sleep, and the raw timeout said
 * nothing about which half was slow.  The helper now polls the bridge for the
 * staged entry first and gives the render its own explicit budget.
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

/** Wait until the bridge actually serves the staged entry.
 *
 *  The panel paints from `sessions.getTrackedFiles()`, which the reload path
 *  only reaches after `sessions.get()` — itself retried up to 10× with
 *  backoff while the bridge warms up.  Under CI load (several Electron +
 *  Python bridge instances in parallel) that round-trip can outlast any fixed
 *  sleep, so poll the same bridge call the panel uses: the wait is then bound
 *  to the real state instead of to a guessed duration, and a genuinely
 *  missing entry stays distinguishable from a slow one. */
async function waitForTrackedFileServed(page: Page, key: string, name: string): Promise<void> {
  await expect
    .poll(
      async () =>
        page.evaluate(
          async (args) => {
            try {
              const res = await (window as any).miqi.sessions.getTrackedFiles(args.key);
              return ((res?.tracked_files ?? []) as any[]).some(
                (f) => f?.name === args.name || f?.path === args.name
              );
            } catch {
              return false;
            }
          },
          { key, name }
        ),
      { timeout: 60_000, intervals: [500, 1000, 2000] }
    )
    .toBe(true);
}

async function openFirstPreview(page: Page, key: string, name: string): Promise<void> {
  await waitForTrackedFileServed(page, key, name);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await waitForInputReady(page, 60_000).catch(() => {});
  const previewBtn = page.locator('[data-testid="file-preview-btn"]').first();
  try {
    // Generous but explicit: this waits for the reload's session restore to
    // complete (sessions.get retry chain + tracked-files fetch), not for a
    // speculative extra second of app latency.
    await expect(previewBtn).toBeVisible({ timeout: 60_000 });
  } catch (err) {
    // Dump the panel state so a future failure says *which* half broke —
    // data never served vs. served-but-not-rendered.
    const emptyState = await page
      .locator('[data-testid="task-assets-empty"]')
      .isVisible()
      .catch(() => false);
    const panelText = await page
      .locator('[data-testid="task-assets-panel"]')
      .textContent()
      .catch(() => null);
    console.log(
      `[test] preview button missing after reload — task-assets-empty=${emptyState}, ` +
        `panel="${(panelText ?? '<panel not found>').slice(0, 200)}"`
    );
    throw err;
  }
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
    await openFirstPreview(page, key, 'preview-877.xlsx');

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
    await openFirstPreview(page, key, 'preview-877.docx');

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
    await openFirstPreview(page, key, 'preview-877.pdf');

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
