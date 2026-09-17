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
 * 本文件是纯逻辑（无 electron 运行时依赖），便于单测。
 */

// 只引类型：`import type` 编译期即被抹除，单测加载本模块时不会碰到 electron。
import type { BrowserWindow } from 'electron';

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
 * 主进程侧的内存小状态表。进程内单例见文件末尾的 `crashRecovery`。
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

/** 进程内单例：主进程只有一个主窗口，状态表按窗口维度记即可。 */
export const crashRecovery = new CrashRecoveryTracker();

/**
 * `render-process-gone` 的接线：按预算自动重载，并打一条可检索的重载记录。
 *
 * 恢复动作对用户完全不可见（2026-09 用户要求）：预算内静默重载；超预算静默
 * 停止（不弹对话框、不退出），窗口停在崩溃态由用户自行重启。
 */
export function handleRendererCrash(
  win: BrowserWindow | null,
  reason: string,
  exitCode: number,
  now: number = Date.now()
): void {
  void exitCode; // 崩溃详情只进 [main] 日志（见 main/index.ts 的 listener）
  const budget = crashRecovery.onRendererCrash(now);
  if (budget.allowed) {
    if (!win || win.isDestroyed() || win.webContents.isDestroyed()) return;
    console.log(reloadLogLine(reason, crashRecovery.recordReload(now)));
    win.webContents.reload();
    return;
  }
  // 超预算：静默停止自动重载。
  console.warn(reloadSkippedLogLine(reason, budget.recent.length));
}
