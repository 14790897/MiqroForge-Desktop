import { describe, it, expect } from 'vitest';
import {
  CrashRecoveryTracker,
  MAX_RELOADS_PER_WINDOW,
  RELOAD_WINDOW_MS,
  evaluateReloadBudget,
  reloadLogLine,
  reloadSkippedLogLine,
} from '../src/main/crashRecovery';

/**
 * #1035 渲染进程崩溃恢复：预算纯函数、日志行格式、主进程侧状态表。
 *
 * 恢复动作对用户完全不可见（2026-09 口径）：本模块只做预算记账与日志，
 * 不再有 notice / 在飞登记表（对应的 UI 通道已全部移除）。本文件只碰纯
 * 逻辑——本模块对 electron 只做 `import type`（编译期即抹除），import 它
 * 不会触发 `src/shared/electron.ts` 的 trampoline 校验。
 */

const T0 = 1_700_000_000_000; // 固定时间基准，避免依赖真实时钟

describe('evaluateReloadBudget — 10 分钟窗口 / 最多 3 次', () => {
  it('空历史：允许重载，序号从 1 起', () => {
    const budget = evaluateReloadBudget([], T0);
    expect(budget).toEqual({ allowed: true, attempt: 1, recent: [] });
  });

  it('0~3 次历史：预算内，attempt = 已有次数 + 1', () => {
    for (let n = 0; n < MAX_RELOADS_PER_WINDOW; n += 1) {
      const history = Array.from({ length: n }, (_, i) => T0 - (n - i) * 1000);
      const budget = evaluateReloadBudget(history, T0);
      expect(budget.allowed).toBe(true);
      expect(budget.attempt).toBe(n + 1);
      expect(budget.recent).toHaveLength(n);
    }
  });

  it('已达上限（3 次）：超预算，停止自动重载', () => {
    const history = [T0 - 3000, T0 - 2000, T0 - 1000];
    const budget = evaluateReloadBudget(history, T0);
    expect(budget.allowed).toBe(false);
    expect(budget.attempt).toBe(4); // 序号仍递增，便于日志排查
    expect(budget.recent).toHaveLength(MAX_RELOADS_PER_WINDOW);
  });

  it('超过上限（4 次）：仍然超预算', () => {
    const history = [T0 - 4000, T0 - 3000, T0 - 2000, T0 - 1000];
    const budget = evaluateReloadBudget(history, T0);
    expect(budget.allowed).toBe(false);
    expect(budget.recent).toHaveLength(4);
  });

  it('过期边界：年龄恰好等于窗口宽度的记录已过期（< 而非 <=）', () => {
    const exactlyAtEdge = T0 - RELOAD_WINDOW_MS;
    const budget = evaluateReloadBudget([exactlyAtEdge], T0);
    expect(budget.recent).toEqual([]);
    expect(budget.allowed).toBe(true);
    expect(budget.attempt).toBe(1);
  });

  it('过期边界内 1ms：仍算在窗口内', () => {
    const justInside = T0 - RELOAD_WINDOW_MS + 1;
    const budget = evaluateReloadBudget([justInside], T0);
    expect(budget.recent).toEqual([justInside]);
    expect(budget.attempt).toBe(2);
  });

  it('混合：只统计窗口内的记录，过期项被丢弃后预算自动恢复', () => {
    const history = [T0 - RELOAD_WINDOW_MS - 1, T0 - 2000, T0 - 1000];
    const budget = evaluateReloadBudget(history, T0);
    expect(budget.recent).toHaveLength(2);
    expect(budget.allowed).toBe(true);
    expect(budget.attempt).toBe(3);
  });
});

describe('日志行格式（期望行为 2：可检索）', () => {
  it('重载日志行含 renderer-reloaded / attempt / reason', () => {
    expect(reloadLogLine('oom', 1)).toBe('[main] renderer-reloaded: attempt=1 reason=oom');
    expect(reloadLogLine('crashed', 3)).toBe('[main] renderer-reloaded: attempt=3 reason=crashed');
  });

  it('超预算时没有重载，但同样留下可检索的痕迹', () => {
    const line = reloadSkippedLogLine('oom', MAX_RELOADS_PER_WINDOW);
    expect(line).toContain('[main] renderer-reload-skipped:');
    expect(line).toContain('reason=oom');
    expect(line).toContain(`reloadsInWindow=${MAX_RELOADS_PER_WINDOW}`);
    expect(line).toContain(`max=${MAX_RELOADS_PER_WINDOW}`);
  });
});

describe('CrashRecoveryTracker — 崩溃记账与预算', () => {
  it('onRendererCrash 只读（不记账）：连续读取序号不变', () => {
    const tracker = new CrashRecoveryTracker();
    expect(tracker.onRendererCrash(T0).allowed).toBe(true);
    expect(tracker.onRendererCrash(T0).attempt).toBe(1);
    // 再读一次仍是 1——判定不产生副作用，记账是显式的 recordReload
    expect(tracker.onRendererCrash(T0).attempt).toBe(1);
    expect(tracker.onRendererCrash(T0).recent).toEqual([]);
  });

  it('recordReload：记账后序号递增，返回本次序号（日志 attempt 取它）', () => {
    const tracker = new CrashRecoveryTracker();
    expect(tracker.recordReload(T0)).toBe(1);
    expect(tracker.recordReload(T0 + 1000)).toBe(2);
    expect(tracker.recordReload(T0 + 2000)).toBe(3);
    expect(tracker.onRendererCrash(T0 + 2000).recent).toHaveLength(3);
  });

  it('第 4 次崩溃超预算：allowed=false、不记账（recent 保持 3 条）', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.recordReload(T0);
    tracker.recordReload(T0 + 1000);
    tracker.recordReload(T0 + 2000);

    const fourth = tracker.onRendererCrash(T0 + 3000);
    expect(fourth.allowed).toBe(false);
    // 未记账 ⇒ 窗口内的记录仍是 3 条，跳过日志据此报数
    expect(fourth.recent).toHaveLength(MAX_RELOADS_PER_WINDOW);

    // 下一次仍超预算（没有因为"这次没记"而把预算用掉或重置）
    const fifth = tracker.onRendererCrash(T0 + 4000);
    expect(fifth.allowed).toBe(false);
    expect(fifth.recent).toHaveLength(MAX_RELOADS_PER_WINDOW);
  });

  it('窗口是滑动的：只滑出一部分时预算已恢复，序号接着窗口内剩余次数算', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.recordReload(T0);
    tracker.recordReload(T0 + 1000);
    tracker.recordReload(T0 + 2000);
    expect(tracker.onRendererCrash(T0 + 3000).allowed).toBe(false);

    // 只够让 T0 那条过期，T0+1000 / T0+2000 仍在窗口内
    const partial = tracker.onRendererCrash(T0 + RELOAD_WINDOW_MS + 1);
    expect(partial.allowed).toBe(true);
    expect(partial.recent).toHaveLength(2);
    expect(partial.attempt).toBe(3);
  });

  it('窗口整体滑过之后预算完全恢复，无需显式重置', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.recordReload(T0);
    tracker.recordReload(T0 + 1000);
    tracker.recordReload(T0 + 2000);
    expect(tracker.onRendererCrash(T0 + 3000).allowed).toBe(false);

    // 连最后一条（T0+2000）也过期
    const later = T0 + 2000 + RELOAD_WINDOW_MS + 1;
    const recovered = tracker.onRendererCrash(later);
    expect(recovered.allowed).toBe(true);
    expect(recovered.recent).toEqual([]);
    expect(recovered.attempt).toBe(1);
  });

  it('reset 清空预算历史', () => {
    const tracker = new CrashRecoveryTracker();
    tracker.recordReload(T0);
    tracker.recordReload(T0 + 1000);
    tracker.reset();
    expect(tracker.onRendererCrash(T0).recent).toEqual([]);
    expect(tracker.onRendererCrash(T0).attempt).toBe(1);
    expect(tracker.recordReload(T0)).toBe(1);
  });
});
