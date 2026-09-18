import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  CrashRecoveryRegistry,
  CrashRecoveryTracker,
  MAX_RELOADS_PER_WINDOW,
  RELOAD_WINDOW_MS,
  crashRecovery,
  evaluateReloadBudget,
  handleRendererCrash,
  reloadLogLine,
  reloadSkippedLogLine,
} from '../src/main/crashRecovery';
import type { CrashRecoverableWindow } from '../src/main/crashRecovery';

/**
 * #1035 渲染进程崩溃恢复：预算纯函数、日志行格式、主进程侧状态表。
 *
 * 恢复动作对用户完全不可见（2026-09 口径）：本模块只做预算记账与日志，
 * 不再有 notice / 在飞登记表（对应的 UI 通道已全部移除）。本文件只碰纯
 * 逻辑——本模块不 import electron（窗口只用结构子集 `CrashRecoverableWindow`
 * 的假实现），import 它不会触发 `src/shared/electron.ts` 的 trampoline 校验。
 */

const T0 = 1_700_000_000_000; // 固定时间基准，避免依赖真实时钟

/**
 * 假窗口：`CrashRecoverableWindow` 的最小实现。
 *
 * id 自增且跨用例唯一——`handleRendererCrash` 写的是模块级单例
 * `crashRecovery`，各用例用不同 id 才不会互相借预算。
 */
let nextWindowId = 1000;

function createFakeWindow(options: { destroyed?: boolean; webContentsDestroyed?: boolean } = {}) {
  const id = nextWindowId++;
  const state = {
    destroyed: options.destroyed ?? false,
    webContentsDestroyed: options.webContentsDestroyed ?? false,
    reloads: 0,
  };
  const closedListeners: Array<() => void> = [];
  const win = {
    id,
    isDestroyed: () => state.destroyed,
    once: (event: 'closed', listener: () => void) => {
      if (event === 'closed') closedListeners.push(listener);
    },
    webContents: {
      isDestroyed: () => state.webContentsDestroyed,
      reload: () => {
        state.reloads += 1;
      },
    },
  } satisfies CrashRecoverableWindow;

  return {
    id,
    win,
    /** 窗口销毁（Electron 的 `closed` 事件）。 */
    close: () => {
      state.destroyed = true;
      closedListeners.splice(0).forEach((listener) => listener());
    },
    reloads: () => state.reloads,
  };
}

/** 捕获 crashRecovery 打出的可检索日志。 */
function captureCrashLogs() {
  const logs: string[] = [];
  const warns: string[] = [];
  const text = (args: unknown[]) => args.map((a) => String(a)).join(' ');
  vi.spyOn(console, 'log').mockImplementation((...args: unknown[]) => {
    logs.push(text(args));
  });
  vi.spyOn(console, 'warn').mockImplementation((...args: unknown[]) => {
    warns.push(text(args));
  });
  return { logs, warns };
}

afterEach(() => {
  vi.restoreAllMocks();
});

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

describe('CrashRecoveryRegistry — 预算按 BrowserWindow 分表（#1035 复审）', () => {
  it('同一窗口复用同一个 tracker；不同窗口各自一份', () => {
    const registry = new CrashRecoveryRegistry();
    const a = createFakeWindow();
    const b = createFakeWindow();

    const trackerA = registry.trackerFor(a.win);
    expect(registry.trackerFor(a.win)).toBe(trackerA);
    expect(registry.trackerFor(b.win)).not.toBe(trackerA);
    expect(registry.size).toBe(2);
  });

  it('窗口 closed 后条目被清理：不会把旧预算留给复用同一 id 的新窗口', () => {
    const registry = new CrashRecoveryRegistry();
    const a = createFakeWindow();
    const tracker = registry.trackerFor(a.win);
    tracker.recordReload(T0);
    expect(registry.size).toBe(1);

    a.close();
    expect(registry.size).toBe(0);

    const revived = registry.trackerFor(a.win);
    expect(revived).not.toBe(tracker);
    expect(revived.onRendererCrash(T0).attempt).toBe(1);
    expect(revived.onRendererCrash(T0).recent).toEqual([]);
  });

  it('forget 只丢指定窗口的记录，其余窗口不受影响', () => {
    const registry = new CrashRecoveryRegistry();
    const a = createFakeWindow();
    const b = createFakeWindow();
    registry.trackerFor(a.win).recordReload(T0);
    registry.trackerFor(b.win).recordReload(T0);

    registry.forget(a.id);
    expect(registry.size).toBe(1);
    expect(registry.trackerFor(a.win).onRendererCrash(T0).attempt).toBe(1);
    expect(registry.trackerFor(b.win).onRendererCrash(T0).attempt).toBe(2);
  });
});

describe('handleRendererCrash — 每个窗口独立预算（#1035 复审）', () => {
  it('两个窗口的预算互不影响：一个耗尽，另一个照常重载', () => {
    const { logs, warns } = captureCrashLogs();
    const a = createFakeWindow();
    const b = createFakeWindow();

    for (let i = 0; i < MAX_RELOADS_PER_WINDOW; i += 1) {
      handleRendererCrash(a.win, 'oom', 1, T0 + i);
    }
    expect(a.reloads()).toBe(MAX_RELOADS_PER_WINDOW);
    expect(logs).toEqual([
      reloadLogLine('oom', 1),
      reloadLogLine('oom', 2),
      reloadLogLine('oom', 3),
    ]);

    // a 的第 4 次崩溃：超预算，静默跳过（不重载、不弹任何东西）
    handleRendererCrash(a.win, 'oom', 1, T0 + 10);
    expect(a.reloads()).toBe(MAX_RELOADS_PER_WINDOW);
    expect(warns).toEqual([reloadSkippedLogLine('oom', MAX_RELOADS_PER_WINDOW)]);

    // b 是另一个窗口：同一时刻的首次崩溃不受 a 耗尽的影响
    handleRendererCrash(b.win, 'crashed', 1, T0 + 11);
    expect(b.reloads()).toBe(1);
    expect(logs.at(-1)).toBe(reloadLogLine('crashed', 1));
  });

  it('旧窗口耗尽并销毁后，activate 重建的新窗口仍能重载', () => {
    const { logs, warns } = captureCrashLogs();
    const sizeBefore = crashRecovery.size;

    const closed = createFakeWindow();
    for (let i = 0; i < MAX_RELOADS_PER_WINDOW + 1; i += 1) {
      handleRendererCrash(closed.win, 'oom', 1, T0 + i);
    }
    expect(closed.reloads()).toBe(MAX_RELOADS_PER_WINDOW);
    expect(crashRecovery.size).toBe(sizeBefore + 1);

    // macOS：关闭最后一个窗口不退出应用 → activate → 新建 BrowserWindow
    closed.close();
    expect(crashRecovery.size).toBe(sizeBefore);

    const recreated = createFakeWindow();
    handleRendererCrash(recreated.win, 'oom', 1, T0 + 100);
    expect(recreated.reloads()).toBe(1);
    expect(logs.at(-1)).toBe(reloadLogLine('oom', 1));
    expect(warns).toEqual([reloadSkippedLogLine('oom', MAX_RELOADS_PER_WINDOW)]);
  });

  it('窗口已销毁：不重载，也不建条目（销毁后不会再发 closed，建了就是残留）', () => {
    const destroyed = createFakeWindow({ destroyed: true });
    const sizeBefore = crashRecovery.size;

    handleRendererCrash(destroyed.win, 'oom', 1, T0);

    expect(destroyed.reloads()).toBe(0);
    expect(crashRecovery.size).toBe(sizeBefore);
  });

  it('webContents 已销毁：不重载，且不消耗该窗口的预算', () => {
    const { logs } = captureCrashLogs();
    const half = createFakeWindow({ webContentsDestroyed: true });

    handleRendererCrash(half.win, 'oom', 1, T0);

    expect(half.reloads()).toBe(0);
    // 判定通过但没记成重载 ⇒ 没有 renderer-reloaded 行；预算未被用掉
    expect(logs).toEqual([]);
    expect(crashRecovery.trackerFor(half.win).onRendererCrash(T0).attempt).toBe(1);
  });

  it('null 窗口：静默返回（主窗口尚未创建时无从归属）', () => {
    expect(() => handleRendererCrash(null, 'oom', 1, T0)).not.toThrow();
  });
});
