/**
 * 渲染进程崩溃后的自动重载（issue #1035）。
 *
 * 崩溃时窗口停留在最后一帧且不可用：主窗口没有原生菜单（`removeMenu()`），
 * 也就没有 Reload 快捷键，用户只能整进程重启——连带杀掉 bridge 和正在跑的
 * turn。本模块提供两件事：
 *
 * 1. **重载预算**：同一窗口 10 分钟内最多自动重载 3 次，超限停止自动重载；
 * 2. **可检索日志**：`[main] renderer-reloaded: attempt=N reason=oom`，
 *    超预算时 `[main] renderer-reload-skipped: ...`。
 *
 * 恢复动作对用户**完全不可见**（用户要求，2026-09）：不插系统消息、不弹
 * 对话框；超预算即静默停止自动重载（窗口停在崩溃态，由用户自行重启）。
 *
 * 预算是**按窗口**的（#1035 复审）：记录按 `BrowserWindow.id` 分表，窗口销毁
 * 即清理。macOS 关闭最后一个窗口不退出应用，`activate` 会重建 BrowserWindow，
 * 若沿用进程级单例，旧窗口耗尽的预算会把新窗口的首次崩溃也静默跳过。
 *
 * 本文件是纯逻辑（无 electron 运行时依赖），便于单测。
 */

/**
 * 崩溃恢复需要的最小窗口接口——`BrowserWindow` 的结构子集。
 *
 * 只依赖这几个成员：本模块保持零 electron 运行时依赖（单测可注入假窗口），
 * 「真实 BrowserWindow 满足该接口」由 index.ts 的调用点静态守住。
 */
export interface CrashRecoverableWindow {
  /** `BrowserWindow.id`：预算按窗口分表。 */
  readonly id: number;
  isDestroyed(): boolean;
  /** 窗口销毁事件——预算记录随之清理。 */
  once(event: 'closed', listener: () => void): unknown;
  readonly webContents: {
    isDestroyed(): boolean;
    reload(): void;
  };
}

/** 重载预算窗口：10 分钟。 */
export const RELOAD_WINDOW_MS = 10 * 60 * 1000;
/** 预算窗口内最多自动重载的次数。 */
export const MAX_RELOADS_PER_WINDOW = 3;

export interface ReloadBudget {
  /** 本次是否还有自动重载预算。 */
  allowed: boolean;
  /** 本次重载的序号（1 起），即日志里的 attempt=N。 */
  attempt: number;
  /** 预算窗口内的历史重载时刻，已剔除过期项。 */
  recent: number[];
}

/**
 * 纯函数：按「同一窗口 10 分钟内最多 3 次」算预算。
 *
 * 过期的记录会被丢弃，所以一阵崩溃潮过去后预算自动恢复，不需要额外重置。
 */
export function evaluateReloadBudget(
  history: readonly number[],
  now: number,
  windowMs: number = RELOAD_WINDOW_MS,
  max: number = MAX_RELOADS_PER_WINDOW
): ReloadBudget {
  // 边界：年龄恰好等于窗口宽度的记录已经过期（`<` 而非 `<=`）。
  const recent = history.filter((t) => now - t < windowMs);
  return { allowed: recent.length < max, attempt: recent.length + 1, recent };
}

/** 纯函数：可检索的重载日志行。 */
export function reloadLogLine(reason: string, attempt: number): string {
  return `[main] renderer-reloaded: attempt=${attempt} reason=${reason}`;
}

/** 纯函数：超预算时的日志行——没有重载也留下可检索的痕迹。 */
export function reloadSkippedLogLine(reason: string, reloadsInWindow: number): string {
  return `[main] renderer-reload-skipped: reason=${reason} reloadsInWindow=${reloadsInWindow} max=${MAX_RELOADS_PER_WINDOW}`;
}

/**
 * 单个窗口的重载预算状态表。
 *
 * - `reloadHistory`：重载时刻，用于 10 分钟预算。
 */
export class CrashRecoveryTracker {
  private reloadHistory: number[] = [];

  /** 记账一次重载并返回它的序号（1 起）。 */
  recordReload(now: number = Date.now()): number {
    const budget = evaluateReloadBudget(this.reloadHistory, now);
    this.reloadHistory = [...budget.recent, now];
    return budget.attempt;
  }

  /** 渲染进程崩溃：按预算给出本次是否自动重载（细节见 `handleRendererCrash`）。 */
  onRendererCrash(now: number = Date.now()): ReloadBudget {
    return evaluateReloadBudget(this.reloadHistory, now);
  }

  /** 仅供测试复位。 */
  reset(): void {
    this.reloadHistory = [];
  }
}

/**
 * 按窗口维度的重载预算表：key 为 `BrowserWindow.id`。
 *
 * 「同一窗口 10 分钟 3 次」是**按窗口**的语义，所以记录随窗口走：窗口销毁
 * （`closed`）即清理对应条目，既不留已关闭窗口的记录，也避免 id 被复用后
 * 新窗口继承旧预算。
 */
export class CrashRecoveryRegistry {
  private readonly trackers = new Map<number, CrashRecoveryTracker>();

  /**
   * 取该窗口的 tracker（首次访问时创建），并登记 `closed` 清理。
   *
   * 清理登记在创建处而不是调用点：预算表只为「真的崩过」的窗口建条目，
   * 谁建谁清，不给调用方留漏清理的机会。
   */
  trackerFor(win: CrashRecoverableWindow): CrashRecoveryTracker {
    const existing = this.trackers.get(win.id);
    if (existing) return existing;
    const tracker = new CrashRecoveryTracker();
    this.trackers.set(win.id, tracker);
    win.once('closed', () => this.forget(win.id));
    return tracker;
  }

  /** 丢弃某窗口的预算记录（窗口销毁时由 `trackerFor` 登记的回调调用）。 */
  forget(windowId: number): void {
    this.trackers.delete(windowId);
  }

  /** 仍在记账的窗口数（诊断用）。 */
  get size(): number {
    return this.trackers.size;
  }
}

/** 进程内单例：一张表按窗口 id 分桶，每个窗口各自 10 分钟 3 次。 */
export const crashRecovery = new CrashRecoveryRegistry();

/**
 * `render-process-gone` 的接线：按**该窗口**的预算自动重载，并打一条可检索
 * 的重载记录。
 *
 * 恢复动作对用户完全不可见（2026-09 用户要求）：预算内静默重载；超预算静默
 * 停止（不弹对话框、不退出），窗口停在崩溃态由用户自行重启。
 */
export function handleRendererCrash(
  win: CrashRecoverableWindow | null,
  reason: string,
  exitCode: number,
  now: number = Date.now()
): void {
  void exitCode; // 崩溃详情只进 [main] 日志（见 main/index.ts 的 listener）
  // 已销毁的窗口不建条目：销毁后不会再发 `closed`，建了就是永久残留；
  // 也无从重载（原先这个判断只在预算内分支里，故超预算时仍会打跳过日志）。
  if (!win || win.isDestroyed()) return;
  const tracker = crashRecovery.trackerFor(win);
  const budget = tracker.onRendererCrash(now);
  if (budget.allowed) {
    if (win.webContents.isDestroyed()) return;
    console.log(reloadLogLine(reason, tracker.recordReload(now)));
    win.webContents.reload();
    return;
  }
  // 超预算：静默停止自动重载。
  console.warn(reloadSkippedLogLine(reason, budget.recent.length));
}
