import { describe, expect, it } from 'vitest';
import { ASSET_PANEL_MIN_WIDTH, panelWindowMinWidth } from './layout';

const BASE = 900; // createWindow 的 minWidth

describe('panelWindowMinWidth', () => {
  it('面板关闭 → 就是基准最小宽度', () => {
    expect(panelWindowMinWidth(BASE, false, 1280)).toBe(900);
  });

  it('面板展开 → 基准 + 面板下限(缩窗时面板让位、输入框不被挤)', () => {
    expect(panelWindowMinWidth(BASE, true, 1280)).toBe(900 + ASSET_PANEL_MIN_WIDTH);
    expect(panelWindowMinWidth(BASE, true, 1100)).toBe(1100);
  });

  it('算出的 min 超过当前窗口宽时 clamp 到当前宽 —— 不把窗口钉死', () => {
    // 展开面板但窗口只有 950:若返回 1100 会让 Windows 无法再调整窗口(#1047 踩过)
    expect(panelWindowMinWidth(BASE, true, 950)).toBe(950);
    // 极端:窗口比基准还窄
    expect(panelWindowMinWidth(BASE, true, 700)).toBe(700);
    expect(panelWindowMinWidth(BASE, false, 850)).toBe(850);
  });

  it('开→关一轮后最小宽度回到基准(评审 Case 1 的纯逻辑部分)', () => {
    const opened = panelWindowMinWidth(BASE, true, 1180); // 展开:1100
    expect(opened).toBe(1100);
    const closed = panelWindowMinWidth(BASE, false, opened); // 关闭:回 900
    expect(closed).toBe(900);
  });
});
