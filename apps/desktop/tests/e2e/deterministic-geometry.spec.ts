/**
 * #843 几何确定性 E2E（外部分析建议：不赌 LLM 输出，用固定 fixture 锁死）：
 * - getCompleteSvgBBox 对「负坐标 + transform + stroke」的 fixture 必须给出
 *   覆盖全部视觉内容（含 stroke 与 group transform）的完整 bbox；
 * - 拒绝 root.getBBox 级别的漏算（stroke 10 → 内容应外扩 5px）。
 * 通过 window.__miqiDiagramDebug（DiagramViewer 暴露，无副作用）测量。
 *
 * Run: cd apps/desktop && PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *   --config=playwright.config.ts --project=electron deterministic-geometry.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.describe('#843 几何确定性（fixture）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 300_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('getCompleteSvgBBox 覆盖负坐标 + group transform + stroke', async () => {
    const r = await page.evaluate(() => {
      const dbg = (
        window as unknown as {
          __miqiDiagramDebug?: {
            getCompleteSvgBBox: (
              s: SVGSVGElement
            ) => { x: number; y: number; width: number; height: number } | null;
          };
        }
      ).__miqiDiagramDebug;
      if (!dbg) return { err: 'debug hook missing' };
      const holder = document.createElement('div');
      holder.style.cssText =
        'position:fixed;left:-100000px;top:0;width:2000px;height:2000px;visibility:hidden';
      holder.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">' +
        '<g transform="translate(-30,-20)">' +
        '<rect x="0" y="0" width="50" height="40" stroke="black" stroke-width="10" fill="none"/>' +
        '</g></svg>';
      document.body.appendChild(holder);
      const svg = holder.querySelector('svg') as SVGSVGElement;
      let bbox = null;
      try {
        bbox = dbg.getCompleteSvgBBox(svg);
      } finally {
        holder.remove();
      }
      return { bbox };
    });
    console.log('[geo-e2e] bbox=' + JSON.stringify(r));
    expect(r.err).toBeUndefined();
    const b = r.bbox!;
    // rect 0..50/0..40 + stroke 10(±5) → -5..55/-5..45;g translate(-30,-20) → -35..25/-25..25
    expect(b.x).toBeLessThanOrEqual(-34);
    expect(b.y).toBeLessThanOrEqual(-24);
    expect(b.x + b.width).toBeGreaterThanOrEqual(24);
    expect(b.y + b.height).toBeGreaterThanOrEqual(24);
    // 关键：stroke 必须计入（无 stroke 时宽=50，计入后=60）
    expect(b.width).toBeGreaterThanOrEqual(59);
    expect(b.height).toBeGreaterThanOrEqual(49);
  });

  test('getCompleteSvgBBox 跳过 defs 内容（不把模板算进可见区）', async () => {
    const r = await page.evaluate(() => {
      const dbg = (
        window as unknown as {
          __miqiDiagramDebug?: {
            getCompleteSvgBBox: (
              s: SVGSVGElement
            ) => { x: number; y: number; width: number; height: number } | null;
          };
        }
      ).__miqiDiagramDebug!;
      const holder = document.createElement('div');
      holder.style.cssText =
        'position:fixed;left:-100000px;top:0;width:2000px;height:2000px;visibility:hidden';
      holder.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">' +
        '<defs><rect x="500" y="500" width="50" height="50"/></defs>' +
        '<rect x="10" y="10" width="30" height="20"/>' +
        '</svg>';
      document.body.appendChild(holder);
      const svg = holder.querySelector('svg') as SVGSVGElement;
      const bbox = dbg.getCompleteSvgBBox(svg);
      holder.remove();
      return { bbox };
    });
    const b = r.bbox!;
    expect(b).not.toBeNull();
    // defs 中的 500,500 矩形不得参与 bbox
    expect(b.x + b.width).toBeLessThan(100);
    expect(b.y + b.height).toBeLessThan(100);
  });
});
