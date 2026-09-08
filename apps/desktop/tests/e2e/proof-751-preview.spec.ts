/**
 * Definitive proof for #751: reproduce the user's exact scenario — a tracked
 * file whose card passes a BARE filename to handlePreview — and assert the
 * preview modal renders the HTML in a sandboxed iframe.
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  launchElectronApp,
  closeElectronApp,
  waitForInputReady,
  ensurePersistedSession,
} from './helpers/electron-setup';

const OUT_DIR = join(__dirname, '../../test-results/proof-751');

const HTML_DOC = `<!doctype html>
<html lang="zh"><head><meta charset="utf-8"><title>销售仪表盘</title>
<style>body{font-family:system-ui,sans-serif;background:#f4f6fb;padding:24px}h1{color:#1f6feb}.card{background:#fff;border-radius:10px;padding:18px;margin-top:12px}</style>
</head><body><h1>销售仪表盘</h1><div class="card">本月销售额 ¥1,240,000（+18.6%）</div><canvas id="chart"></canvas>
<script>document.getElementById('chart').style.background='#16a34a'</script>
</body></html>`;

test('proof #751: file preview modal renders tracked html file', async () => {
  mkdirSync(OUT_DIR, { recursive: true });
  const fixture = await launchElectronApp();
  const electronApp = fixture.electronApp;
  const page = fixture.page;
  await waitForInputReady(page);

  const key = await ensurePersistedSession(page);

  // Write the file + a BARE-name tracked record (mimics local tool-hint
  // tracking: the panel card carries just "sales_dashboard.html").
  await electronApp.evaluate(
    (_e, args) => {
      const fs = (process as any).getBuiltinModule('node:fs');
      const path = (process as any).getBuiltinModule('node:path');
      const home = process.env.MIQI_HOME;
      const safe = args.key.replace(/:/g, '_');
      const dir = path.join(home, 'workspace', 'sessions', safe);
      const filesDir = path.join(dir, 'files');
      fs.mkdirSync(filesDir, { recursive: true });
      fs.writeFileSync(path.join(filesDir, 'sales_dashboard.html'), args.html, 'utf8');
      fs.writeFileSync(
        path.join(dir, 'tracked_files.json'),
        JSON.stringify({
          version: 1,
          files: {
            'sales_dashboard.html': {
              op: 'write',
              name: 'sales_dashboard.html',
              lastSeen: Date.now(),
            },
          },
        })
      );
    },
    { html: HTML_DOC, key }
  );

  // Reload so the panel re-reads tracked files.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await waitForInputReady(page, 60_000).catch(() => {});

  const previewBtn = page.locator('[data-testid="file-preview-btn"]').first();
  await expect(previewBtn).toBeVisible({ timeout: 20_000 });
  await previewBtn.click();
  await page.waitForTimeout(1200);

  // The modal must render a sandboxed iframe whose srcdoc contains the page.
  // sandbox="allow-scripts" (opaque origin) lets the page's own scripts run.
  const iframe = page.locator('iframe[sandbox="allow-scripts"]').last();
  await expect(iframe).toBeVisible({ timeout: 5000 });
  const srcdoc = (await iframe.getAttribute('srcdoc')) ?? '';
  expect(srcdoc).toContain('销售仪表盘');
  expect(srcdoc).toContain('</html>');
  expect(srcdoc).toContain('<script>');

  // Auto-fit: the short page must shrink the frame instead of leaving a tall
  // white void (the "extra white layer" below the preview).
  await expect
    .poll(
      async () => {
        const box = await iframe.boundingBox();
        return box ? Math.round(box.height) : -1;
      },
      { timeout: 10_000 }
    )
    .toBeLessThan(600);

  const shot = join(OUT_DIR, 'proof-751-preview-modal.png');
  await page.screenshot({ path: shot, timeout: 5000 });
  const box = await iframe.boundingBox();
  console.log(`[proof-751] iframe fitted height=${box?.height}px sandbox=allow-scripts`);

  await closeElectronApp(electronApp).catch(() => {});
});
