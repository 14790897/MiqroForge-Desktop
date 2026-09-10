/**
 * 诊断（临时）：getCompleteSvgBBox 对 mermaid 常见结构的覆盖——
 * ① foreignObject（htmlLabels 文本节点）② 嵌套 <svg> ③ filter 阴影。
 * 输出每类子元素的 getBBox 行为，定位"部分内容缺失"来源。
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

test.describe('bbox 盲区诊断', () => {
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

  test('foreignObject / 嵌套 svg 的 getBBox 行为 + 算法输出', async () => {
    const r = await page.evaluate(() => {
      const dbg = (
        window as unknown as {
          __miqiDiagramDebug?: { getCompleteSvgBBox: (s: SVGSVGElement) => unknown };
        }
      ).__miqiDiagramDebug!;
      const holder = document.createElement('div');
      holder.style.cssText =
        'position:fixed;left:-100000px;top:0;width:2000px;height:2000px;visibility:hidden';
      // ① foreignObject：文本框在 y=200（远超 100 高的 viewBox）
      holder.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">' +
        '<g transform="translate(10,10)"><rect x="0" y="0" width="40" height="30" fill="#eef"/></g>' +
        '<foreignObject x="10" y="200" width="60" height="40">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="font-size:12px">HTML label 内容</div>' +
        '</foreignObject>' +
        '</svg>';
      document.body.appendChild(holder);
      const svg = holder.querySelector('svg') as SVGSVGElement;
      const fo = svg.querySelector('foreignObject') as unknown as SVGGraphicsElement;
      let foBBox: unknown = null;
      try {
        const b = fo.getBBox();
        foBBox = { x: b.x, y: b.y, w: b.width, h: b.height };
      } catch (e) {
        foBBox = { err: String(e) };
      }
      const algo = dbg.getCompleteSvgBBox(svg);
      holder.remove();

      // ② 嵌套 svg（在 y=300 处）
      const holder2 = document.createElement('div');
      holder2.style.cssText = holder.style.cssText;
      holder2.innerHTML =
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">' +
        '<rect x="5" y="5" width="30" height="20"/>' +
        '<svg x="5" y="300" width="50" height="40"><rect x="0" y="0" width="50" height="40" fill="#fdd"/></svg>' +
        '</svg>';
      document.body.appendChild(holder2);
      const svg2 = holder2.querySelector('svg') as SVGSVGElement;
      const inner = svg2.querySelector('svg[width="50"]') as unknown as SVGGraphicsElement;
      let innerBBox: unknown = null;
      try {
        const b = inner.getBBox();
        innerBBox = { x: b.x, y: b.y, w: b.width, h: b.height };
      } catch (e) {
        innerBBox = { err: String(e) };
      }
      const algo2 = dbg.getCompleteSvgBBox(svg2);
      holder2.remove();

      return { foBBox, algo, innerBBox, algo2 };
    });
    const foB = r.foBBox as { x: number; y: number; w: number; h: number };
    expect(foB.y).toBe(200);
    const algo = r.algo as { x: number; y: number; width: number; height: number };
    // foreignObject 内容(y=240 底)必须被覆盖
    expect(algo.y + algo.height).toBeGreaterThanOrEqual(239);
    console.log('[bbox-diag] foreignObject=' + JSON.stringify(r.foBBox));
    console.log('[bbox-diag] algo(with FO)=' + JSON.stringify(r.algo));
    const algo2 = r.algo2 as { x: number; y: number; width: number; height: number };
    // 嵌套 svg 内容(translate 至 y=300..340)必须被覆盖
    expect(algo2.y + algo2.height).toBeGreaterThanOrEqual(339);
    console.log('[bbox-diag] innerSvg=' + JSON.stringify(r.innerBBox));
    console.log('[bbox-diag] algo(innerSvg)=' + JSON.stringify(r.algo2));
  });
});
