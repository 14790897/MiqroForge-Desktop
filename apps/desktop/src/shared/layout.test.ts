import { describe, expect, it } from 'vitest';
import { ASSET_PANEL_MIN_WIDTH, clampMinToWindow, panelWindowMinWidth } from './layout';

const BASE = 900; // createWindow 的 minWidth

describe('panelWindowMinWidth(不变量目标,不做 clamp)', () => {
  it('面板关闭 → 基准最小宽度', () => {
    expect(panelWindowMinWidth(BASE, false)).toBe(900);
  });

  it('面板展开 → 基准 + 面板下限(聊天列保住与关闭时相同的宽度)', () => {
    expect(panelWindowMinWidth(BASE, true)).toBe(900 + ASSET_PANEL_MIN_WIDTH);
  });

  it('目标不随「当前窗口更窄」而缩水 —— 降级由 clampMinToWindow 显式处理', () => {
    // 旧实现会在窗口 950 时返回 950,悄悄放弃不变量(sijie-Z #1047 指出的 900~1099 区间)
    expect(panelWindowMinWidth(BASE, true)).toBe(1100);
  });

  it('开→关一轮后目标回到基准', () => {
    const opened = panelWindowMinWidth(BASE, true);
    expect(opened).toBe(1100);
    expect(panelWindowMinWidth(BASE, false)).toBe(900);
  });
});

describe('clampMinToWindow(仅在窗口确实撑不到目标时降级)', () => {
  it('窗口够宽 → 取目标', () => {
    expect(clampMinToWindow(1100, 1180)).toBe(1100);
  });

  it('窗口撑不到目标 → 取实际宽度(避免 min > 当前宽把窗口钉死)', () => {
    expect(clampMinToWindow(1100, 950)).toBe(950);
  });

  it('永不返回大于实际宽度的值', () => {
    for (const w of [700, 900, 1099, 1100, 1280]) {
      expect(clampMinToWindow(1100, w)).toBeLessThanOrEqual(w);
    }
  });
});
