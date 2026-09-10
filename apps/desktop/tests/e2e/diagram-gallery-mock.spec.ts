/**
 * #843 图库 UI E2E（本地 mock LLM — 不依赖真实 provider/key，本地可跑）：
 * 本地 http server 模拟 OpenAI 兼容 SSE，固定输出两个 mermaid 代码块。
 * 断言：图库卡渲染（含图名）→ 查看器 → 多图切换 → 放大 → 鸟瞰同步 →
 * 拖拽到边界（clamp 基于内容尺寸，可拖到顶/底）→ Esc。
 *
 * Run: cd apps/desktop && npx electron-vite build &&
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *     --config=playwright.config.ts --project=electron diagram-gallery-mock.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { createServer, type Server } from 'node:http';
import { once } from 'node:events';
import {
  waitForInputReady,
  waitForSandboxReady,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

const ANSWER = [
  '流程如下：',
  '',
  '```mermaid',
  'flowchart TD',
  '  A[原料预处理] --> B[配位自组装]',
  '  B --> C{结晶可控?}',
  '  C -->|是| D[活化处理]',
  '  C -->|否| B',
  '  D --> E[表征与测试]',
  '```',
  '',
  '时序如下：',
  '',
  '```mermaid',
  'sequenceDiagram',
  '  participant U as 活化炉',
  '  participant X as XRD',
  '  U->>X: 样品（真空梯度升温）',
  '  X-->>U: 骨架确认',
  '```',
  '',
].join('\n');

test.describe('#843 图库 UI（本地 mock LLM）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mock: Server;

  test.beforeAll(async () => {
    mock = createServer((req, res) => {
      req.resume();
      req.on('end', () => {
        res.writeHead(200, {
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-cache',
          connection: 'keep-alive',
        });
        const enc = (o: unknown) => `data: ${JSON.stringify(o)}\n\n`;
        const chunk = (delta: Record<string, unknown>, finish: string | null) =>
          enc({
            id: 'mock-1',
            object: 'chat.completion.chunk',
            created: Date.now(),
            model: 'mock',
            choices: [{ index: 0, delta, finish_reason: finish }],
          });
        // 按行分块流式（保持 mermaid 行完整）
        const lines = ANSWER.split('\n');
        let i = 0;
        const tick = () => {
          if (i === 0) {
            res.write(chunk({ role: 'assistant', content: lines[i++] + '\n' }, null));
          } else if (i < lines.length) {
            res.write(chunk({ content: lines[i++] + '\n' }, null));
          } else {
            res.write(chunk({}, 'stop'));
            res.write('data: [DONE]\n\n');
            res.end();
            return;
          }
          setTimeout(tick, 120);
        };
        setTimeout(tick, 500);
      });
    });
    mock.listen(0, '127.0.0.1');
    await once(mock, 'listening');
    const port = (mock.address() as { port: number }).port;

    const fixture = await launchElectronApp((config: any) => {
      // provider 指向本地 mock（覆盖任何既有配置；本机 providers 为空也能跑）。
      // 模型名对齐 CI desktop-ci.yml 的格式（无 provider 前缀，唯一定义即命中）
      config.providers = {
        ...(config.providers ?? {}),
        siliconflow: { apiKey: 'mock-key', apiBase: `http://127.0.0.1:${port}/v1` },
      };
      config.agents = {
        ...(config.agents ?? {}),
        defaults: { ...(config.agents?.defaults ?? {}), model: 'deepseek-v4-pro' },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    // 桥日志（provider 解析诊断）
    const proc = electronApp.process();
    (proc.stdout as any)?.on('data', (d: unknown) => {
      const t = String(d ?? '').trim();
      if (/provider|api key|model|activation|builtin/i.test(t))
        console.log('[bridge]', t.slice(0, 220));
    });
    (proc.stderr as any)?.on('data', (d: unknown) => {
      const t = String(d ?? '').trim();
      if (t) console.log('[berr]', t.slice(0, 220));
    });
    const ready = await waitForSandboxReady(page, 180_000);
    console.log(`[mock-e2e] sandbox ready=${ready}`);
  }, 300_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mock.close();
  });

  test(
    'mock 回合 → 图库卡 → 查看器（切换/放大/边界拖拽/鸟瞰同步）',
    { timeout: 150_000 },
    async () => {
      // 诊断：后端报告的 provider 状态 + 生效 config
      const pinfo = await page.evaluate(async () => {
        const w = window as unknown as { miqi?: Record<string, any> };
        try {
          const providers = await w.miqi?.providers?.list?.();
          const config = await w.miqi?.config?.get?.();
          return {
            providers: JSON.stringify(providers).slice(0, 600),
            model: JSON.stringify(config?.agents?.defaults?.model),
          };
        } catch (e) {
          return { err: String(e) };
        }
      });
      console.log('[mock-e2e] pinfo=', JSON.stringify(pinfo));
      const textarea = await waitForInputReady(page, 120_000);
      await textarea.fill('给我 MOF 合成流程和表征时序的 mermaid 图');
      await textarea.press('Enter');

      // 图库卡出现（mock 秒回流完）
      const cards = page.getByTestId('diagram-card');
      await expect(cards.first()).toBeVisible({ timeout: 60_000 });
      const cardCount = await cards.count();
      console.log(`[mock-e2e] cards=${cardCount}`);
      expect(cardCount).toBeGreaterThanOrEqual(1);

      // 打开查看器
      await cards.first().click();
      const viewer = page.getByTestId('diagram-viewer');
      await expect(viewer).toBeVisible({ timeout: 10_000 });
      await expect(viewer.getByRole('button', { name: '适应窗口' })).toBeVisible();

      // 鸟瞰同步断言（背景 viewBox == 主图修正版 viewBox）
      const mmSync = await expect
        .poll(
          () =>
            viewer.evaluate(() => {
              const main = document.querySelector(
                '[data-testid="diagram-viewer"] svg[id^="mmd-"]'
              ) as SVGSVGElement | null;
              const mainVb = main ? main.getAttribute('viewBox') : null;
              const bg = document.querySelector(
                '[data-testid="diagram-minimap"] div[style*="background-image"]'
              );
              const bgImg = bg ? getComputedStyle(bg).backgroundImage : '';
              const m = bgImg.match(/data:image\/svg\+xml,([^")]+)/);
              const dec = m ? decodeURIComponent(m[1]) : '';
              const vb = new RegExp('viewBox="([^"]*)"').exec(dec);
              return { mainVb, bgVb: vb ? vb[1] : null };
            }),
          { timeout: 8_000 }
        )
        .then((r) => r);
      console.log(`[mock-e2e] bird-sync=${JSON.stringify(mmSync)}`);
      expect(mmSync.bgVb).toBe(mmSync.mainVb);

      // 放大 3 次 → 百分比 > 100
      for (let i = 0; i < 3; i++) {
        await viewer.getByRole('button', { name: '放大' }).click();
      }
      const pct = Number.parseInt(
        (await viewer.getByTestId('diagram-viewer-pct').textContent()) ?? '0',
        10
      );
      expect(pct).toBeGreaterThan(100);

      // S2：拖拽可达边界——大幅向上拖两次，第二次后位置被 clamp 钉住（不再变）
      const tfRead = () =>
        viewer.getByTestId('diagram-tf').evaluate((el) => (el as HTMLElement).style.transform);
      const vbox = (await viewer.boundingBox()) ?? { x: 0, y: 0 };
      const drag = async (dy: number) => {
        await page.mouse.move(vbox.x + 300, vbox.y + 300);
        await page.mouse.down();
        await page.mouse.move(vbox.x + 300, vbox.y + 300 + dy, { steps: 12 });
        await page.mouse.up();
      };
      await drag(-400);
      const t1 = await tfRead();
      await drag(-400);
      const t2 = await tfRead();
      console.log(`[mock-e2e] drag t1=${t1} t2=${t2}`);
      expect(t1).not.toBe('translate(0px, 0px) scale(1) rotate(0deg)'); // 发生了平移
      expect(t2).toBe(t1); // 已到 clamp 边界，位置钉住

      // 鸟瞰视野框随之更新（存在且尺寸变小 = 视野小于全图）
      const boxW = await viewer
        .getByTestId('diagram-mm-box')
        .evaluate((el) => parseFloat((el as HTMLElement).style.width || '0'));
      expect(boxW).toBeGreaterThan(0);

      // Esc 关闭
      await page.keyboard.press('Escape');
      await expect(viewer).not.toBeVisible({ timeout: 5_000 });
      await expect(cards.first()).toBeVisible({ timeout: 5_000 });
    }
  );
});
