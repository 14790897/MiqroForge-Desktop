/**
 * 渲染进程崩溃后的自动重载（issue #1035）。
 *
 * 崩溃时窗口停留在最后一帧且不可用：主窗口没有原生菜单（`removeMenu()`），
 * 也就没有 Reload 快捷键，用户只能整进程重启——连带杀掉 bridge 和正在跑的
 * turn。本模块提供三件事：
 *
 * 1. **重载预算**：同一窗口 10 分钟内最多自动重载 3 次，超限停止自动重载；
 * 2. **可检索日志**：`[main] renderer-reloaded: attempt=N reason=oom`，
 *    超预算时 `[main] renderer-reload-skipped: ...`，重载动作抛异常时
 *    `[main] renderer-reload-failed: ...`；
 * 3. **生命周期守卫**（#1035 复审 P1）：窗口已销毁 / webContents 已销毁 /
 *    正在关闭（`close` 已发、`closed` 未到）都算不可恢复，一律不做恢复动作——
 *    崩溃事件与 `close` 可能几乎同时到达，判定集中在 `canRecover` 一处。
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
  /**
   * 窗口生命周期事件（与 `BrowserWindow` 同名同义）：
   * - `close`：窗口开始关闭、尚未销毁——此后不再做任何恢复动作；
   * - `closed`：窗口已销毁——预算与生命周期记录随之清理。
   */
  once(event: 'close' | 'closed', listener: () => void): unknown;
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
 * 纯函数：重载动作**抛异常**时的日志行（#1035 复审 P2）。
 *
 * 与 `renderer-reload-skipped` 分开：那条是"预算用完了没做动作"，这条是
 * "动作做了但失败了"——两者排查方向完全不同（前者调预算，后者查窗口是否
 * 正好在重载途中被销毁），混成一条会把排查带偏。
 */
export function reloadFailedLogLine(reason: string, attempt: number, error: unknown): string {
  const detail = error instanceof Error ? error.message : String(error);
  return `[main] renderer-reload-failed: attempt=${attempt} reason=${reason} error=${detail}`;
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
 *
 * 除了预算，本表还持有窗口的**生命周期状态**（`closing`）：窗口「正在关闭」
 * 是一个只存在于 `close` 与 `closed` 之间的瞬时状态，#1035 复审 P1 要求把
 * 它也算作不可恢复状态——否则崩溃事件正好落在这个窗口里时会去给一个正在拆
 * 的窗口续命（用户看到的是"关不掉的窗口"）。
 */
export class CrashRecoveryRegistry {
  private readonly trackers = new Map<number, CrashRecoveryTracker>();
  /** 已登记生命周期监听的窗口 id（`watch` 幂等用）。 */
  private readonly watched = new Set<number>();
  /** 已进入关闭流程（`close` 已发、`closed` 未到）的窗口 id。 */
  private readonly closing = new Set<number>();

  /**
   * 登记窗口的生命周期（幂等），给 `canRecover` 提供「正在关闭」这个信号。
   *
   * **必须在窗口创建时调用**（index.ts 的 createWindow）：`close` 是一次性
   * 事件，等崩溃发生才来登记就已经错过了它——那一刻窗口可能已经在关，而我们
   * 无从得知。`trackerFor` 里也调一次作为兜底，让直接调用方不至于完全没有
   * 生命周期记录。
   */
  watch(win: CrashRecoverableWindow): void {
    if (this.watched.has(win.id)) return;
    this.watched.add(win.id);
    win.once('close', () => {
      this.closing.add(win.id);
    });
    // 预算与生命周期记录都随窗口销毁一起清掉：id 可能被重建的窗口复用。
    win.once('closed', () => this.forget(win.id));
  }

  /**
   * 该窗口此刻是否还能做恢复动作——不可恢复的三种情形**只在这里判定**。
   *
   * - `isDestroyed()`：窗口已销毁。销毁后不会再发 `closed`，为它建 tracker
   *   就是永久残留；也无从重载。
   * - `webContents.isDestroyed()`：没有可重载的对象（窗口可能还在，但渲染
   *   进程/内容已经没了）。
   * - `closing`：窗口已进入关闭流程（`close` 已发、`closed` 未到）。
   *
   * 注：本应用没有任何地方 `preventDefault()` 窗口的 `close`，所以 `close`
   * 一旦发出，关闭就一定会走到 `closed`——`closing` 不会变成滞留的误判。
   */
  canRecover(win: CrashRecoverableWindow): boolean {
    if (win.isDestroyed()) return false;
    if (win.webContents.isDestroyed()) return false;
    return !this.closing.has(win.id);
  }

  /**
   * 取该窗口的 tracker（首次访问时创建），并登记生命周期清理。
   *
   * 调用方**必须先过 `canRecover`**：预算表只为「真的崩过、且还能恢复」的
   * 窗口建条目——对不可恢复的窗口建条目就是留垃圾（已销毁的窗口永远不会再发
   * `closed`）。
   */
  trackerFor(win: CrashRecoverableWindow): CrashRecoveryTracker {
    const existing = this.trackers.get(win.id);
    if (existing) return existing;
    const tracker = new CrashRecoveryTracker();
    this.trackers.set(win.id, tracker);
    this.watch(win);
    return tracker;
  }

  /** 丢弃某窗口的预算与生命周期记录（`closed` 时由 `watch` 登记的回调调用）。 */
  forget(windowId: number): void {
    this.trackers.delete(windowId);
    this.watched.delete(windowId);
    this.closing.delete(windowId);
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
  // 不可恢复状态集中判定（#1035 复审 P1）：窗口已销毁 / webContents 已销毁 /
  // 窗口正在关闭——三种情形一律直接返回：不建 tracker（已销毁的窗口不会再发
  // `closed`，建了就是永久残留）、不记账、不做恢复动作，也不打任何
  // reloaded / skipped 日志（那是误导：恢复动作根本没轮到执行）。
  if (!win || !crashRecovery.canRecover(win)) return;

  const tracker = crashRecovery.trackerFor(win);
  const budget = tracker.onRendererCrash(now);
  if (!budget.allowed) {
    // 超预算：静默停止自动重载。
    console.warn(reloadSkippedLogLine(reason, budget.recent.length));
    return;
  }

  // 先记账再动作：预算是按「尝试过的恢复动作」算的，失败也占额度——否则一个
  // 反复崩-反复失败的窗口会把日志刷爆（每次失败各自留痕，见下）。
  const attempt = tracker.recordReload(now);
  try {
    win.webContents.reload();
  } catch (error) {
    // 失败不静默（#1035 复审 P2）：窗口可能正好在重载途中被销毁。这里同时
    // 挡住异常冒泡到 `render-process-gone` 监听器——那儿没人接，会掀翻主进程。
    console.warn(reloadFailedLogLine(reason, attempt, error));
    return;
  }
  console.log(reloadLogLine(reason, attempt));
}
