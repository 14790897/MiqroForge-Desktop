/**
 * Regression for #977: 左侧对话栏无法拖动调整大小.
 *
 * usePanelResize 的 effect 依赖了 computeWidth——调用方传内联箭头函数
 * （每次渲染新身份），第一次 setWidth 重渲染即触发 cleanup，把
 * isResizing 重置为 false，后续 mousemove 全部被忽略：拖拽只生效第一步
 * 的几像素。真实鼠标事件密集，第一步位移常为 1-2px，用户感知即「拖不动」。
 *
 * 断言真实鼠标拖拽（多步 mousemove + mouseup）全程跟踪光标，并验证
 * MIN_WIDTH/MAX_WIDTH 钳制。
 */
import { test, expect } from '@playwright/test';
import { launchElectronApp, closeElectronApp } from './helpers/electron-setup';

const MIN_WIDTH = 180;
const MAX_WIDTH = 480;

/** 从分隔条当前中心点开始，分 stepCount 步拖到目标 x（步进间留出事件投递时间）。 */
async function dragHandle(page: import('@playwright/test').Page, handle: any, delta: number) {
  const rect = await handle.evaluate((el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  const steps = 10;
  await page.mouse.move(rect.x, rect.y);
  await page.mouse.down();
  for (let step = 1; step <= steps; step++) {
    await page.mouse.move(rect.x + (delta * step) / steps, rect.y);
    await page.waitForTimeout(30);
  }
  await page.mouse.up();
}

test('issue #977: sidebar drag-resize tracks the mouse', async () => {
  const { electronApp, page, miqiHome } = await launchElectronApp();

  const sidebar = page.locator('.sidebar-shell');
  await expect(sidebar).toBeVisible({ timeout: 30_000 });
  const handle = sidebar.locator('div.cursor-col-resize');

  const getWidth = () => sidebar.evaluate((el) => el.getBoundingClientRect().width);

  const before = await getWidth();
  expect(before).toBe(260); // defaultWidth

  // 右拖 150px：宽度必须跟随（原 bug 只生效第一步 ~13px）
  await dragHandle(page, handle, 150);
  const widened = await getWidth();
  expect(widened).toBeGreaterThan(before + 130);
  expect(widened).toBeLessThan(before + 170);

  // 左拖超量：钳制在 MIN_WIDTH（拖拽距离按当前宽度计算，鼠标不越出视口）
  await dragHandle(page, handle, -(widened - MIN_WIDTH + 40));
  expect(await getWidth()).toBe(MIN_WIDTH);

  // 右拖超量：钳制在 MAX_WIDTH
  await dragHandle(page, handle, MAX_WIDTH - MIN_WIDTH + 40);
  expect(await getWidth()).toBe(MAX_WIDTH);

  await closeElectronApp(electronApp, miqiHome);
});
