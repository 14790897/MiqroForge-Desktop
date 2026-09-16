/**
 * 渲染进程崩溃后的自动重载与恢复提示（issue #1035）。
 *
 * 崩溃时窗口停留在最后一帧且不可用：主窗口没有原生菜单（`removeMenu()`），
 * 也就没有 Reload 快捷键，用户只能整进程重启——连带杀掉 bridge 和正在跑的
 * turn。本模块提供三件事：
 *
 * 1. **重载预算**：同一窗口 10 分钟内最多自动重载 3 次，超限停止自动重载
 *    并交回用户决定（"重新加载" / "退出"）；
 * 2. **可检索日志**：`[main] renderer-reloaded: attempt=N reason=oom`；
 * 3. **在飞 turn 登记表 + 恢复提示**：崩溃瞬间记下哪些会话还有在飞 turn，
 *    渲染层重载后挂载时主动拉取，自己往消息列表插一条系统消息。
 *
 * 第 3 点的记账之所以必须在**主进程**：渲染层的 `moduleInFlightCache` /
 * `moduleMessagesSnapshot` 都是模块级内存态，reload 后全部清空，无法用来
 * 判断"turn 还在不在"。投递方式也刻意不走 `chat:progress`——那一路没有常驻
 * 订阅者（唯一订阅点在 `ChatConsole.handleSend` 的闭包里，abort/error/切会话
 * 即摘除），重载后的推送会被静默丢弃。
 *
 * 本文件除 `handleRendererCrash` 外都是纯逻辑，便于单测；Electron 只在
 * 该函数的调用点用动态 import 取（`src/shared/electron.ts` 在模块加载期就会
 * 校验 trampoline 注入，顶层静态 import 会让单测无法加载本模块）。
 */

// 只引类型：`import type` 编译期即被抹除，单测加载本模块时不会碰到 electron。
import type { BrowserWindow, MessageBoxOptions } from 'electron';
import type { RecoveryNotice } from '../shared/ipc';

/** 重载预算窗口：10 分钟。 */
export const RELOAD_WINDOW_MS = 10 * 60 * 1000;
/** 预算窗口内最多自动重载的次数。 */
export const MAX_RELOADS_PER_WINDOW = 3;
/** 恢复提示有效期——超过则视为陈旧，不再下发给渲染层。 */
export const NOTICE_TTL_MS = 30 * 60 * 1000;

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

/** 纯函数：可检索的重载日志行（期望行为 2）。 */
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
 * - `inFlight`：`chat.send` 记、`final`/`error`/`aborted` 清；
 * - `reloadHistory`：重载时刻，用于 10 分钟预算；
 * - `notice`：最近一次崩溃留下的恢复提示，等渲染层来拉。
 *
 * 已知限制：`inFlight` 是 `Map<sessionKey, startedAt>`，同一会话上的并发 turn
 * 会互相覆盖——后发的 turn 落定（或通道异常结束）时把先发 turn 的登记一并清
 * 掉，于是崩溃提示的 `inFlightSessionKeys` 会漏掉那个会话。当前 UI 不允许同一
 * 会话同时发多个 turn，故未处理；真要支持并发，演进方向是把值换成
 * `Set<turnId>` 或按会话计数的计数器（settle 一次减一）。
 */
export class CrashRecoveryTracker {
  private readonly inFlight = new Map<string, number>();
  private reloadHistory: number[] = [];
  private notice: RecoveryNotice | null = null;

  /** `chat.send` 受理后登记。 */
  markTurnStarted(sessionKey: string, now: number = Date.now()): void {
    if (!sessionKey) return;
    this.inFlight.set(sessionKey, now);
  }

  /** 收到 `final` / `error` / `aborted`（或通道异常结束）时清除。 */
  markTurnSettled(sessionKey: string): void {
    this.inFlight.delete(sessionKey);
  }

  getInFlightSessionKeys(): string[] {
    return [...this.inFlight.keys()];
  }

  /** 记账一次重载并返回它的序号（1 起）。 */
  recordReload(now: number = Date.now()): number {
    const budget = evaluateReloadBudget(this.reloadHistory, now);
    this.reloadHistory = [...budget.recent, now];
    return budget.attempt;
  }

  /**
   * 渲染进程崩溃：按预算决定是否自动重载，并留下恢复提示。
   *
   * 刻意**不动**在飞登记表——bridge 里的 turn 可能仍在跑，稍后仍会走到
   * `final`/`error`/`aborted` 把它清掉；提前清会让提示里的
   * "若后台仍在运行，切回会话可继续看到新输出" 失真。
   */
  onRendererCrash(
    reason: string,
    exitCode: number,
    now: number = Date.now()
  ): { budget: ReloadBudget; notice: RecoveryNotice } {
    const budget = evaluateReloadBudget(this.reloadHistory, now);
    const notice: RecoveryNotice = {
      id: String(now),
      crashedAt: now,
      reason,
      exitCode,
      // 超预算不重载，也就没有 attempt 可言；留 0 让渲染层与日志都看得出来。
      attempt: budget.allowed ? this.recordReload(now) : 0,
      inFlightSessionKeys: this.getInFlightSessionKeys(),
    };
    this.notice = notice;
    return { budget, notice };
  }

  /**
   * 只读拉取（不消费）：同一次崩溃可能被多次挂载读到（会话切换、StrictMode
   * 双调用），消费式读取会让第一次被丢弃的结果把提示一起吃掉。去重交给
   * 渲染层的模块级 Set（按 `id`），它在 reload 时清空，正好一次崩溃一条。
   */
  peekNotice(now: number = Date.now()): RecoveryNotice | null {
    if (!this.notice) return null;
    if (now - this.notice.crashedAt > NOTICE_TTL_MS) return null;
    return this.notice;
  }

  /** 仅供测试复位。 */
  reset(): void {
    this.inFlight.clear();
    this.reloadHistory = [];
    this.notice = null;
  }
}

/** 进程内单例：主进程只有一个主窗口，状态表按窗口维度记即可。 */
export const crashRecovery = new CrashRecoveryTracker();

/** 对话框按钮文案（也是 `response` 的下标，含义见 `handleRendererCrash`）。 */
const BUTTON_RELOAD = '重新加载';
const BUTTON_QUIT = '退出';

/**
 * `render-process-gone` 的接线：按预算自动重载（期望行为 1），打一条可检索的
 * 重载记录（期望行为 2），并弹原生对话框让崩溃可见（期望行为 4）。
 *
 * **预算内路径不把点击当作重载的前提**：期望行为 1 写的是"自动重载该
 * webContents"，期望行为 4 只要求"崩溃可见"。所以顺序是
 * 「日志 → 重载 → 非阻塞告知框」——重载不 await 对话框（否则用户不点就永远
 * 不恢复），告知框也不带父窗（带父窗会变成窗口模态，禁用掉刚刚恢复的界面）。
 *
 * **超预算路径**才需要用户决定，所以保持 await 的模态对话框
 * （"重新加载" / "退出"）。
 */
export async function handleRendererCrash(
  win: BrowserWindow | null,
  reason: string,
  exitCode: number,
  now: number = Date.now()
): Promise<void> {
  const { budget } = crashRecovery.onRendererCrash(reason, exitCode, now);

  // 惰性取 Electron：`src/shared/electron.ts` 在模块加载期就校验 trampoline
  // 注入，顶层静态 import 会让本模块的纯逻辑无法被单测加载。
  const { dialog, app } = (await import('../shared/electron')).electron;
  // 窗口已销毁时退化成无父窗对话框（否则 Electron 会抛）。
  const showBox = (options: MessageBoxOptions) =>
    win && !win.isDestroyed()
      ? dialog.showMessageBox(win, options)
      : dialog.showMessageBox(options);

  if (budget.allowed) {
    if (!win || win.isDestroyed() || win.webContents.isDestroyed()) return;
    // 顺序：日志 → 重载 → 告知框。重载必须在对话框之前发出（期望行为 1 的
    // "自动重载"不以用户点击为条件）；对话框随后**非阻塞**弹出，让崩溃在
    // 重载前的不可用窗口期过去后仍然可见（期望行为 4）。
    console.log(reloadLogLine(reason, budget.attempt));
    win.webContents.reload();
    // 刻意不带父窗：带父窗是窗口模态，会禁用掉刚刚重载完成的界面。
    // 不 await、错误吞掉——对话框只是告知，弹不出来也不该影响恢复。
    void dialog
      .showMessageBox({
        type: 'warning',
        title: '界面已崩溃',
        message: '界面已崩溃，已自动重新加载。',
        detail:
          `原因：${reason}（exitCode=${exitCode}）\n` +
          `这是 10 分钟内的第 ${budget.attempt} 次自动重载（上限 ${MAX_RELOADS_PER_WINDOW} 次）。\n` +
          '重载后当前会话会插入一条提示，进行中的 turn 输出可能不完整。',
        buttons: [BUTTON_RELOAD],
        defaultId: 0,
        noLink: true,
      })
      .catch(() => {});
    return;
  }

  // 超预算：停止自动重载，交回用户决定。
  console.warn(reloadSkippedLogLine(reason, budget.recent.length));
  const { response } = await showBox({
    type: 'error',
    title: '界面反复崩溃',
    message: `界面在 10 分钟内已重载 ${budget.recent.length} 次，已停止自动重载。`,
    detail:
      `原因：${reason}（exitCode=${exitCode}）\n` +
      '重新加载可以再试一次；若仍然崩溃，选择退出后重启应用更稳妥。',
    buttons: [BUTTON_RELOAD, BUTTON_QUIT],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  if (response !== 0) {
    app.quit();
    return;
  }
  if (!win || win.isDestroyed() || win.webContents.isDestroyed()) return;
  console.log(reloadLogLine(reason, crashRecovery.recordReload()));
  win.webContents.reload();
}
