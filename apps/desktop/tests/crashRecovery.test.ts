import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  CrashRecoveryRegistry,
  CrashRecoveryTracker,
  MAX_RELOADS_PER_WINDOW,
  RELOAD_WINDOW_MS,
  crashRecovery,
  evaluateReloadBudget,
  handleRendererCrash,
  reloadFailedLogLine,
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
 * 假窗口：`CrashRecoverableWindow` 的最小实现（含 close/closed 时序）。
 *
 * id 自增且跨用例唯一——`handleRendererCrash` 写的是模块级单例
 * `crashRecovery`，各用例用不同 id 才不会互相借预算。
 */
let nextWindowId = 1000;

function createFakeWindow(
  options: {
    destroyed?: boolean;
    webContentsDestroyed?: boolean;
    /** 指定 id：模拟 Electron 复用 `BrowserWindow.id` 重建窗口。 */
    id?: number;
    /** `reload()` 抛出的错误消息（模拟窗口正好在重载途中被销毁）。 */
    reloadThrows?: string;
  } = {}
) {
  const id = options.id ?? nextWindowId++;
  const state = {
    destroyed: options.destroyed ?? false,
    webContentsDestroyed: options.webContentsDestroyed ?? false,
    reloads: 0,
  };
  const listeners: Record<'close' | 'closed', Array<() => void>> = { close: [], closed: [] };
  // 累计登记数（fire 会清空数组，所以另记一份）：用来断言 watch 的幂等性
  const registered: Record<'close' | 'closed', number> = { close: 0, closed: 0 };
  const win = {
    id,
    isDestroyed: () => state.destroyed,
    once: (event: 'close' | 'closed', listener: () => void) => {
      registered[event] += 1;
      listeners[event].push(listener);
    },
    webContents: {
      isDestroyed: () => state.webContentsDestroyed,
      reload: () => {
        if (options.reloadThrows) throw new Error(options.reloadThrows);
        state.reloads += 1;
      },
    },
  } satisfies CrashRecoverableWindow;

  const fire = (event: 'close' | 'closed') => {
    listeners[event].splice(0).forEach((listener) => listener());
  };

  return {
    id,
    win,
    /** 窗口开始关闭、尚未销毁（Electron 的 `close` 事件）。 */
    beginClose: () => fire('close'),
    /** 窗口关闭全流程：`close` → 销毁（含 webContents）→ `closed`。 */
    close: () => {
      fire('close');
      state.destroyed = true;
      state.webContentsDestroyed = true;
      fire('closed');
    },
    /** 累计登记过的生命周期监听数（断言 watch / trackerFor 的幂等性）。 */
    registered: () => ({ ...registered }),
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

  it('重载失败的行带 reason/attempt 与错误详情，且与「超预算」区分开（#1035 复审 P2）', () => {
    const line = reloadFailedLogLine('oom', 2, new Error('Object has been destroyed'));
    expect(line).toBe(
      '[main] renderer-reload-failed: attempt=2 reason=oom error=Object has been destroyed'
    );
    // 不是 skipped：一条是"没做动作"，一条是"做了但失败"，排查方向不同
    expect(line).not.toContain('renderer-reload-skipped');
    // 非 Error 的抛出物也要留下可读文本，不能变成 "undefined"
    expect(reloadFailedLogLine('crashed', 1, 'boom')).toContain('error=boom');
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

  it('canRecover：活着的窗口 true；close 后 false；closed 清掉标记', () => {
    const registry = new CrashRecoveryRegistry();
    const a = createFakeWindow();
    registry.watch(a.win);
    expect(registry.canRecover(a.win)).toBe(true);

    a.beginClose();
    expect(registry.canRecover(a.win), 'close 已发、尚未销毁 = 不可恢复').toBe(false);

    a.close();
    // 窗口确实没了：仍然是不可恢复（这回是 isDestroyed 拦下的）
    expect(registry.canRecover(a.win)).toBe(false);
    // 而且 closing 标记也不能留在表里——否则复用同一 id 的新窗口会被连坐
    const revived = createFakeWindow({ id: a.id });
    registry.watch(revived.win);
    expect(registry.canRecover(revived.win), '同 id 重建的窗口不得继承上一个的 closing').toBe(true);
  });

  it('watch 幂等：重复登记不会叠加 close/closed 监听', () => {
    const registry = new CrashRecoveryRegistry();
    const a = createFakeWindow();
    registry.watch(a.win);
    registry.watch(a.win); // 第二次起应是 no-op
    registry.watch(a.win);

    // 幂等是 watch 自己的职责：监听器只在首次登记（trackerFor 内部也会调它，
    // 窗口创建时 index.ts 已经调过一次，重复登记是常态而不是异常）
    expect(a.registered()).toEqual({ close: 1, closed: 1 });

    a.beginClose();
    expect(registry.canRecover(a.win)).toBe(false);
    a.close();
    expect(registry.size).toBe(0);
  });

  it('trackerFor 自己也会登记生命周期（直接调用方不至于没有 close/closed 记录）', () => {
    const registry = new CrashRecoveryRegistry();
    const a = createFakeWindow();

    registry.trackerFor(a.win); // 不走 watch 的调用方
    a.beginClose();
    expect(registry.canRecover(a.win)).toBe(false);
    a.close();
    expect(registry.size).toBe(0);
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

  it('窗口已销毁：不重载、不建条目、不打任何 reloaded/skipped 日志', () => {
    const { logs, warns } = captureCrashLogs();
    const sizeBefore = crashRecovery.size;
    const destroyed = createFakeWindow({ destroyed: true });

    handleRendererCrash(destroyed.win, 'oom', 1, T0);

    expect(destroyed.reloads()).toBe(0);
    // 已销毁的窗口不会再发 `closed`，建了条目就是永久残留
    expect(crashRecovery.size).toBe(sizeBefore);
    // 日志里不许出现"已重载/已跳过"——恢复动作根本没轮到执行，那是误导
    expect(logs).toEqual([]);
    expect(warns).toEqual([]);
  });

  it('webContents 已销毁：不重载、不建条目、不消耗预算', () => {
    const { logs, warns } = captureCrashLogs();
    const sizeBefore = crashRecovery.size;
    const half = createFakeWindow({ webContentsDestroyed: true });

    handleRendererCrash(half.win, 'oom', 1, T0);

    expect(half.reloads()).toBe(0);
    expect(crashRecovery.size).toBe(sizeBefore);
    expect(logs).toEqual([]);
    expect(warns).toEqual([]);
    // 预算没被用掉：这个窗口之后（webContents 活过来）的首次崩溃仍是 attempt=1
    const revived = crashRecovery.trackerFor(half.win);
    expect(revived.onRendererCrash(T0).attempt).toBe(1);
    expect(revived.onRendererCrash(T0).recent).toEqual([]);
  });

  it('null 窗口：静默返回（主窗口尚未创建时无从归属）', () => {
    expect(() => handleRendererCrash(null, 'oom', 1, T0)).not.toThrow();
  });

  it('reload 抛异常：不谎报已重载，改打 renderer-reload-failed（#1035 复审 P2）', () => {
    const { logs, warns } = captureCrashLogs();
    const flaky = createFakeWindow({ reloadThrows: 'Object has been destroyed' });

    expect(() => handleRendererCrash(flaky.win, 'oom', 1, T0)).not.toThrow();

    expect(logs, 'reload 没成功就不该有 renderer-reloaded 行').toEqual([]);
    expect(warns).toEqual([reloadFailedLogLine('oom', 1, new Error('Object has been destroyed'))]);
    expect(warns[0]).toContain('[main] renderer-reload-failed:');
    expect(warns[0]).toContain('reason=oom');
    expect(warns[0]).toContain('error=Object has been destroyed');
    // 窗口还活着（只是这次重载失败）：再崩一次序号递增，说明预算按"尝试过的
    // 动作"算——否则反复失败的窗口会无限刷日志行。
    expect(crashRecovery.trackerFor(flaky.win).onRendererCrash(T0 + 1).attempt).toBe(2);
  });
});

/**
 * #1035 复审 P1：`close` 与 `render-process-gone` 的竞态。
 *
 * Electron 的窗口关闭是 `close`（开始关、尚未销毁）→ 销毁 → `closed` 三步，
 * 中间这个窗口 `isDestroyed()` 还是 false。崩溃事件正好落在这个缝里时，旧实现
 * 会照着"窗口没销毁"去做恢复动作：给一个正在拆的窗口续命（用户看到关不掉的
 * 窗口），reload 本身也可能在拆到一半时抛异常。这里用假窗口把三种时序都摆出来。
 */
describe('生命周期守卫 — close / destroyed 与 crash 的竞态（#1035 复审 P1）', () => {
  it('已进入关闭流程（close 已发、尚未 destroyed）：不重载、不建条目、不打日志', () => {
    const { logs, warns } = captureCrashLogs();
    const sizeBefore = crashRecovery.size;
    const win = createFakeWindow();
    crashRecovery.watch(win.win); // index.ts 在窗口创建时就会登记
    win.beginClose();

    // 前提：此时窗口还没销毁——旧实现只看 isDestroyed()，这一条会漏过去
    expect(win.win.isDestroyed()).toBe(false);
    expect(win.win.webContents.isDestroyed()).toBe(false);

    handleRendererCrash(win.win, 'oom', 1, T0);

    expect(win.reloads()).toBe(0);
    expect(crashRecovery.size).toBe(sizeBefore);
    expect(logs).toEqual([]);
    expect(warns).toEqual([]);
  });

  it('崩溃先到、close 后到：先正常重载；关闭期间的再次崩溃不再动', () => {
    const { logs, warns } = captureCrashLogs();
    const win = createFakeWindow();

    handleRendererCrash(win.win, 'oom', 1, T0);
    expect(win.reloads(), '窗口还开着：第一次崩溃照常重载').toBe(1);

    win.beginClose();
    handleRendererCrash(win.win, 'oom', 1, T0 + 1);

    expect(win.reloads(), '窗口正在关闭：不得再重载').toBe(1);
    expect(logs).toEqual([reloadLogLine('oom', 1)]);
    expect(warns, '关闭中的崩溃不产生任何跳过/失败日志（压根没轮到动作）').toEqual([]);
    // 这次崩溃也没消耗预算：窗口内仍只有第一次那条记录
    expect(crashRecovery.trackerFor(win.win).onRendererCrash(T0 + 1).recent).toHaveLength(1);
  });

  it('窗口销毁后才到达的崩溃：不泄漏条目、不重载（先崩后关的收尾）', () => {
    const { logs, warns } = captureCrashLogs();
    const sizeBefore = crashRecovery.size;
    const win = createFakeWindow();

    handleRendererCrash(win.win, 'crashed', 1, T0);
    expect(win.reloads()).toBe(1);
    expect(crashRecovery.size).toBe(sizeBefore + 1);

    win.close(); // close → destroyed → closed
    expect(crashRecovery.size, '窗口销毁即清账').toBe(sizeBefore);

    handleRendererCrash(win.win, 'crashed', 1, T0 + 1);
    expect(win.reloads(), '迟到的崩溃事件不得再触发恢复动作').toBe(1);
    expect(crashRecovery.size, '不得为已销毁窗口重建条目（它不会再发 closed）').toBe(sizeBefore);
    expect(logs).toEqual([reloadLogLine('crashed', 1)]);
    expect(warns).toEqual([]);
  });
});
