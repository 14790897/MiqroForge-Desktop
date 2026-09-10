/**
 * #843 流程图图库 UI E2E（真实 LLM 回合）：
 *   模型回合要求输出两个 mermaid 代码块（flowchart + sequenceDiagram），
 *   回答渲染为 QQ 图库卡（data-testid=diagram-card，按类型中文图名）→
 *   点击卡体打开浅色文件查看器（data-testid=diagram-viewer，顶部工具：
 *   适应窗口/1:1/旋转/复制/下载）→ 多图切换（计数 N/M）→ 放大 →
 *   Esc 关闭后卡片仍在。
 *
 * 平台守卫：回合经真实 provider（deepseek/moonshot），无需 WSL/bwrap；
 * 但回合依赖用户侧 provider key（CI 用 secret 注入，同 #854 real-llm spec）。
 *
 * Run: cd apps/desktop && npx electron-vite build &&
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *     --config=playwright.config.ts --project=electron \
 *     diagram-gallery-mermaid.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  waitForResponseComplete,
  waitForSandboxReady,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

// 平台守卫（CI 实测 macos-e2e）：本 spec 发送前等待沙箱就绪（WSL/bwrap
// 冷启动防回合互扰，Windows/Linux 实测有效）；macOS 无沙箱，等待必超时
// 失败——真 LLM 回合在 macos job 上无 provider key，整套跳过（#854 同款）。
test.skip(
  process.platform === 'darwin',
  'diagram gallery E2E requires WSL (Windows) or native bwrap (Linux) sandbox readiness'
);

test.describe('流程图图库 UI（真实 LLM，#843）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const proc = electronApp.process();
    (proc.stdout as any)?.on('data', (d: unknown) => {
      const s = String(d ?? '').trim();
      if (s) console.log('[bridge]', s.slice(0, 240));
    });

    // 沙箱先行就绪（回合首次初始化与 LLM 并发易互扰——#843 实测）
    const ready = await waitForSandboxReady(page, 180_000);
    expect(ready).toBe(true);
  }, 300_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    '回答渲染 mermaid 图库卡 → 浅色查看器（切换/放大/关闭）',
    { timeout: LLM_TIMEOUT + 90_000 },
    async () => {
      // 新会话，避开启动期会话态竞态（#854/#875 实测）
      await page
        .getByRole('button', { name: '新建会话' })
        .first()
        .click()
        .catch(() => undefined);
      await waitForInputReady(page, 120_000);

      const textarea = await waitForInputReady(page, 120_000);
      await textarea.fill(
        '这是 UI 验证场景：不要调用任何工具，也不要询问。请严格输出两个独立的 mermaid 图，' +
          '不要省略任何一个：1) 一个 flowchart TD（主题「溶剂热法合成 MOF 工艺流程」，4 个中文节点）；' +
          '2) 一个 sequenceDiagram（主题「活化与表征步骤」）。' +
          '每个图都放在自己的 ```mermaid 代码块里，代码块外写一两句中文说明。'
      );
      await textarea.press('Enter');
      await waitForResponseComplete(page, LLM_TIMEOUT);

      // 回合完成 → mermaid 块渲染为图库卡（流式期间显示源码、完成后成卡）
      const cards = page.getByTestId('diagram-card');
      await expect(cards.first()).toBeVisible({ timeout: 90_000 });
      const cardCount = await cards.count();
      console.log(`[mmd-e2e] cards=${cardCount}`);
      expect(cardCount).toBeGreaterThanOrEqual(1);

      // 类型识别图名（模型若给了两种图，名称应分别是流程图/时序图）
      if (cardCount >= 2) {
        const names = await cards.allTextContents();
        console.log(`[mmd-e2e] labels=${JSON.stringify(names)}`);
      }

      // 截图：对话流图库卡形态
      await page.screenshot({ path: 'test-results/mermaid-cards.png', timeout: 60_000 });

      // 点第二张卡（存在时）→ 查看器从该张打开（多图 N/M 计数）
      const openIndex = cardCount >= 2 ? 1 : 0;
      await cards.nth(openIndex).click();
      const viewer = page.getByTestId('diagram-viewer');
      await expect(viewer).toBeVisible({ timeout: 10_000 });
      await expect(viewer.getByRole('button', { name: '适应窗口' })).toBeVisible();
      // ③ 内容完整包含于 viewBox（mermaid 内容超 viewBox 被剪 → 90% 图；
      //    SvgBody mount 后按 getBBox 重写 viewBox 修复）
      const covered = await viewer.evaluate(() => {
        const svg = document.querySelector(
          '[data-testid="diagram-viewer"] svg[id^="mmd-"]'
        ) as SVGSVGElement | null;
        if (!svg) return { ok: false };
        const vb = svg.viewBox.baseVal;
        const b = svg.getBBox();
        return {
          ok:
            b.x >= vb.x - 0.5 &&
            b.y >= vb.y - 0.5 &&
            b.x + b.width <= vb.x + vb.width + 0.5 &&
            b.y + b.height <= vb.y + vb.height + 0.5,
          vb: vb.x + ',' + vb.y + ',' + vb.width + ',' + vb.height,
          bb: b.x + ',' + b.y + ',' + b.width + ',' + b.height,
        };
      });
      console.log('[mmd-e2e] viewBox coverage=' + JSON.stringify(covered));
      expect(covered.ok).toBe(true);

      // 诊断：鸟瞰缩略图真实渲染尺寸 vs 面板（裁切判定）
      {
        const mm = viewer.getByTestId('diagram-minimap');
        const mmRect = (await mm.boundingBox()) ?? { x: 0, y: 0, width: 0, height: 0 };
        const bgImg = await mm.evaluate((el) => getComputedStyle(el).backgroundImage);
        const svgRect = { width: bgImg.includes('data:image/svg+xml') ? 178 : 0, height: 0 };
        const outer = await mm.evaluate((el) => (el as HTMLElement).outerHTML.slice(0, 600));
        const overflow = await mm.evaluate((el) => {
          const e = el as HTMLElement;
          return { sw: e.scrollWidth, sh: e.scrollHeight, cw: e.clientWidth, ch: e.clientHeight };
        });
        console.log(
          '[mmd-e2e] minimap panel=' +
            JSON.stringify(mmRect) +
            ' svg=' +
            JSON.stringify(svgRect) +
            ' overflow=' +
            JSON.stringify(overflow) +
            ' outer=' +
            JSON.stringify(outer)
        );
      }
      await page.screenshot({ path: 'test-results/mermaid-viewer.png', timeout: 60_000 });

      if (cardCount >= 2) {
        await expect(viewer.getByText(`${openIndex + 1} / ${cardCount}`)).toBeVisible({
          timeout: 5_000,
        });
        // 上一张
        await viewer.getByRole('button', { name: '上一张' }).click();
        await expect(viewer.getByText('1 / 2').or(viewer.getByText('1 / 3'))).toBeVisible({
          timeout: 5_000,
        });
        // 胶片跳回：胶片按钮存在性断言 + 原生 click（Playwright 鼠标事件
        // 会被胶片外层 pointer-events-none 穿透——这里直接派发 DOM click，
        // 走 React onClick 同一条路径验证切换逻辑）
        const film2 = viewer.getByRole('button', { name: '查看第 2 张' });
        await expect(film2).toBeVisible({ timeout: 5_000 });
        await film2.evaluate((el) => (el as HTMLButtonElement).click());
        await expect(viewer.getByText('2 / 2').or(viewer.getByText('2 / 3'))).toBeVisible({
          timeout: 5_000,
        });
      }

      // 放大两次 → 百分比 > 100
      await viewer.getByRole('button', { name: '放大' }).click();
      await viewer.getByRole('button', { name: '放大' }).click();
      const pctText = await viewer.getByTestId('diagram-viewer-pct').textContent();
      const pct = Number.parseInt(pctText ?? '0', 10);
      console.log(`[mmd-e2e] zoom pct=${pctText}`);
      expect(pct).toBeGreaterThan(100);

      // ② 鸟瞰底图为整图 background-image（contain），非裁切内嵌 svg
      const hasBg = await viewer.getByTestId('diagram-minimap').evaluate((el) => {
        const bg = el.querySelector('div[style*="background-image"]');
        if (!bg) return false;
        const cs = getComputedStyle(bg);
        return cs.backgroundImage.includes('data:image/svg+xml') && cs.backgroundSize === 'contain';
      });
      expect(hasBg).toBe(true);
      // 鸟瞰面板特写大图（人眼直接验证完整图）
      await viewer
        .getByTestId('diagram-minimap')
        .screenshot({ path: 'test-results/minimap-closeup.png' });

      // 鸟瞰背景必须与主图修正后的 viewBox 一致（字符串路径同步完整版）
      const mmSync = await viewer.evaluate(() => {
        const main = document.querySelector(
          '[data-testid="diagram-viewer"] svg[id^="mmd-"]'
        ) as SVGSVGElement | null;
        const mainVb = main ? main.getAttribute('viewBox') : null;
        const bg = document.querySelector(
          '[data-testid="diagram-minimap"] div[style*="background-image"]'
        );
        const bgImg = bg ? getComputedStyle(bg).backgroundImage : '';
        const m = bgImg.match(/data:image\/svg\+xml,([^"\)]+)/);
        const dec = m ? decodeURIComponent(m[1]) : '';
        const vb = dec.match(/viewBox="([^"]+)"/);
        return { mainVb, bgVb: vb ? vb[1] : null, hasBg2: !!m };
      });
      console.log('[mmd-e2e] bird-sync=' + JSON.stringify(mmSync));
      expect(mmSync.hasBg2).toBe(true);
      await expect
        .poll(
          async () => {
            const vb = await viewer.evaluate(() => {
              const bg = document.querySelector(
                '[data-testid="diagram-minimap"] div[style*="background-image"]'
              );
              const bgImg = bg ? getComputedStyle(bg).backgroundImage : '';
              const m = bgImg.match(new RegExp('data:image/svg\\+xml,([^)]+)'));
              const dec = m ? decodeURIComponent(m[1]) : '';
              const vb = new RegExp('viewBox="([^"]*)"').exec(dec);
              return vb ? vb[1] : null;
            });
            return vb;
          },
          { timeout: 5_000, message: '鸟瞰背景未同步到修正后 viewBox' }
        )
        .toBe(mmSync.mainVb);
      // 适应窗口复位
      await viewer.getByRole('button', { name: '适应窗口' }).click();

      // Esc 关闭 → 对话中卡片仍在
      await page.keyboard.press('Escape');
      await expect(viewer).not.toBeVisible({ timeout: 5_000 });
      await expect(cards.first()).toBeVisible({ timeout: 5_000 });
    }
  );
});
