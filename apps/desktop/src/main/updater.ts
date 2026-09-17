/**
 * 自动更新（issue #1124）。
 *
 * electron-updater 只在打包环境可用（feed 来自构建时写入的 app-update.yml，
 * 即 GitHub Release 资产 latest.yml）。本模块不直接 import electron-*，
 * 由调用方注入 autoUpdater（见 index.ts 的懒加载），因此状态机可以在
 * vitest 中脱开 Electron 单测。
 *
 * 失败策略：检查/下载失败不打断用户（内测机网络抖动频繁），状态落到
 * error 由设置页展示；只有「已下载」才弹横幅引导重启安装。
 */

import type { UpdateSnapshot } from '../shared/ipc';

export type UpdateEvent =
  | { type: 'checking' }
  | { type: 'available'; version: string }
  | { type: 'not-available' }
  | { type: 'progress'; percent: number }
  | { type: 'downloaded'; version: string }
  | { type: 'error'; message: string };

/** 纯状态机：把 autoUpdater 事件折算成新的状态快照。 */
export function reduceUpdateSnapshot(current: UpdateSnapshot, ev: UpdateEvent): UpdateSnapshot {
  switch (ev.type) {
    case 'checking':
      return { ...current, state: 'checking', error: undefined, percent: undefined };
    case 'available':
      return { ...current, state: 'available', version: ev.version, percent: 0, error: undefined };
    case 'not-available':
      return {
        ...current,
        state: 'up-to-date',
        version: undefined,
        percent: undefined,
        error: undefined,
      };
    case 'progress': {
      // 下载完成后的迟到进度不把状态降级回去
      if (current.state === 'downloaded') return current;
      const percent = Math.max(0, Math.min(100, Math.round(ev.percent)));
      return { ...current, state: 'downloading', percent };
    }
    case 'downloaded':
      return {
        ...current,
        state: 'downloaded',
        version: ev.version || current.version,
        percent: 100,
        error: undefined,
      };
    case 'error':
      // 保留 version：下载失败时设置页要能说清是哪个版本失败
      return { ...current, state: 'error', error: ev.message };
  }
}

/** electron-updater 的 autoUpdater 中我们用到的子集（便于测试替身）。 */
export interface AutoUpdaterLike {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  on(event: string, listener: (...args: unknown[]) => void): unknown;
  checkForUpdates(): Promise<unknown>;
  quitAndInstall(isSilent?: boolean, isForceRunAfter?: boolean): void;
}

export interface UpdaterOptions {
  autoUpdater: AutoUpdaterLike | null;
  currentVersion: string;
  /** false（非打包环境）时状态固定 unsupported，check 不发起请求。 */
  enabled?: boolean;
  broadcast: (snapshot: UpdateSnapshot) => void;
  log?: (level: 'INFO' | 'WARN' | 'ERROR', message: string) => void;
  /** 启动后自动检查的延迟毫秒（默认 30s）。 */
  initialDelayMs?: number;
  setTimeoutFn?: typeof setTimeout;
}

export interface Updater {
  snapshot(): UpdateSnapshot;
  check(): Promise<UpdateSnapshot>;
  /** 仅在已下载（downloaded）时生效；返回是否真的触发了重启安装。 */
  install(): boolean;
  /** 启动后延迟自动检查一次（手动检查始终可用）。 */
  start(): void;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err ?? '未知错误');
}

export function createUpdater(opts: UpdaterOptions): Updater {
  const { autoUpdater, currentVersion, broadcast } = opts;
  const enabled = (opts.enabled ?? true) && autoUpdater !== null;
  const log = opts.log ?? (() => {});
  let snap: UpdateSnapshot = { state: enabled ? 'idle' : 'unsupported', currentVersion };

  const apply = (ev: UpdateEvent) => {
    snap = reduceUpdateSnapshot(snap, ev);
    broadcast(snap);
  };

  if (enabled && autoUpdater) {
    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;

    autoUpdater.on('checking-for-update', () => apply({ type: 'checking' }));
    autoUpdater.on('update-available', (info) =>
      apply({ type: 'available', version: String((info as { version?: unknown })?.version ?? '') })
    );
    autoUpdater.on('update-not-available', () => apply({ type: 'not-available' }));
    autoUpdater.on('download-progress', (p) =>
      apply({ type: 'progress', percent: Number((p as { percent?: unknown })?.percent ?? 0) })
    );
    autoUpdater.on('update-downloaded', (info) =>
      apply({ type: 'downloaded', version: String((info as { version?: unknown })?.version ?? '') })
    );
    autoUpdater.on('error', (err) => {
      log('WARN', `[updater] ${errorMessage(err)}`);
      apply({ type: 'error', message: errorMessage(err) });
    });
  }

  // 并发去重：设置页连点「检查更新」或启动检查与手动检查撞车时只发一次请求
  let inFlight: Promise<UpdateSnapshot> | null = null;

  const check = async (): Promise<UpdateSnapshot> => {
    if (!enabled || !autoUpdater) return snap;
    if (inFlight) return inFlight;
    apply({ type: 'checking' });
    inFlight = autoUpdater
      .checkForUpdates()
      .catch((err) => {
        log('WARN', `[updater] check failed: ${errorMessage(err)}`);
        apply({ type: 'error', message: errorMessage(err) });
      })
      .then(() => {
        inFlight = null;
        return snap;
      });
    return inFlight;
  };

  const install = (): boolean => {
    if (!enabled || !autoUpdater || snap.state !== 'downloaded') return false;
    log('INFO', `[updater] quitAndInstall ${snap.version ?? ''}`.trim());
    autoUpdater.quitAndInstall(false, true);
    return true;
  };

  const start = (): void => {
    if (!enabled) return;
    const timer = (opts.setTimeoutFn ?? setTimeout)(() => {
      void check();
    }, opts.initialDelayMs ?? 30_000);
    // 待触发的检查不应阻止进程退出
    (timer as { unref?: () => void }).unref?.();
  };

  return { snapshot: () => snap, check, install, start };
}
