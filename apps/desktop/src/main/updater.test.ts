import { describe, expect, it, vi } from 'vitest';
import { createUpdater, reduceUpdateSnapshot, type AutoUpdaterLike } from './updater';
import type { UpdateSnapshot } from '../shared/ipc';

const base: UpdateSnapshot = { state: 'idle', currentVersion: '1.0.0' };

describe('reduceUpdateSnapshot', () => {
  it('checking 清掉上一轮的进度与错误', () => {
    const prev: UpdateSnapshot = { state: 'error', currentVersion: '1.0.0', error: 'boom' };
    expect(reduceUpdateSnapshot(prev, { type: 'checking' })).toEqual({
      state: 'checking',
      currentVersion: '1.0.0',
      error: undefined,
      percent: undefined,
    });
  });

  it('available 记录新版本号并进入下载态（percent 0）', () => {
    const next = reduceUpdateSnapshot(base, { type: 'available', version: '1.1.0' });
    expect(next.state).toBe('available');
    expect(next.version).toBe('1.1.0');
    expect(next.percent).toBe(0);
  });

  it('not-available 清掉版本号回到 up-to-date', () => {
    const prev: UpdateSnapshot = { state: 'available', currentVersion: '1.0.0', version: '1.1.0' };
    const next = reduceUpdateSnapshot(prev, { type: 'not-available' });
    expect(next.state).toBe('up-to-date');
    expect(next.version).toBeUndefined();
  });

  it('progress 四舍五入并夹在 0-100', () => {
    const prev: UpdateSnapshot = { state: 'available', currentVersion: '1.0.0', version: '1.1.0' };
    expect(reduceUpdateSnapshot(prev, { type: 'progress', percent: 42.6 }).percent).toBe(43);
    expect(reduceUpdateSnapshot(prev, { type: 'progress', percent: -5 }).percent).toBe(0);
    expect(reduceUpdateSnapshot(prev, { type: 'progress', percent: 120 }).percent).toBe(100);
    expect(reduceUpdateSnapshot(prev, { type: 'progress', percent: 10 }).state).toBe('downloading');
  });

  it('已下载后的迟到进度不把状态降级回 downloading', () => {
    const prev: UpdateSnapshot = {
      state: 'downloaded',
      currentVersion: '1.0.0',
      version: '1.1.0',
      percent: 100,
    };
    expect(reduceUpdateSnapshot(prev, { type: 'progress', percent: 99 })).toBe(prev);
  });

  it('downloaded 固定 percent 100，并保留 info 缺失时的旧版本号', () => {
    const prev: UpdateSnapshot = {
      state: 'downloading',
      currentVersion: '1.0.0',
      version: '1.1.0',
    };
    expect(reduceUpdateSnapshot(prev, { type: 'downloaded', version: '' })).toEqual({
      state: 'downloaded',
      currentVersion: '1.0.0',
      version: '1.1.0',
      percent: 100,
      error: undefined,
    });
  });

  it('error 保留 version 与当前状态上下文，供设置页说明是哪个版本失败', () => {
    const prev: UpdateSnapshot = {
      state: 'downloading',
      currentVersion: '1.0.0',
      version: '1.1.0',
    };
    const next = reduceUpdateSnapshot(prev, { type: 'error', message: 'net down' });
    expect(next.state).toBe('error');
    expect(next.error).toBe('net down');
    expect(next.version).toBe('1.1.0');
  });
});

function makeFakeAutoUpdater(overrides: Partial<AutoUpdaterLike> = {}) {
  const listeners = new Map<string, (...args: unknown[]) => void>();
  const fake: AutoUpdaterLike = {
    autoDownload: false,
    autoInstallOnAppQuit: false,
    on: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
      listeners.set(event, listener);
      return fake;
    }),
    checkForUpdates: vi.fn(async () => undefined),
    quitAndInstall: vi.fn(),
    ...overrides,
  };
  return { fake, emit: (event: string, ...args: unknown[]) => listeners.get(event)?.(...args) };
}

describe('createUpdater', () => {
  it('未打包（enabled=false）时停在 unsupported，不触发任何请求', async () => {
    const { fake } = makeFakeAutoUpdater();
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      enabled: false,
      broadcast: () => {},
    });
    expect(updater.snapshot()).toEqual({ state: 'unsupported', currentVersion: '1.0.0' });
    expect(await updater.check()).toEqual({ state: 'unsupported', currentVersion: '1.0.0' });
    expect(fake.checkForUpdates).not.toHaveBeenCalled();
    expect(updater.install()).toBe(false);
  });

  it('autoUpdater 为 null 同样降级为 unsupported', () => {
    const updater = createUpdater({
      autoUpdater: null,
      currentVersion: '1.0.0',
      broadcast: () => {},
    });
    expect(updater.snapshot().state).toBe('unsupported');
  });

  it('开启自动下载与退出后自动安装，并把事件折算成广播', () => {
    const { fake, emit } = makeFakeAutoUpdater();
    const seen: UpdateSnapshot[] = [];
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      broadcast: (s) => seen.push(s),
    });

    expect(fake.autoDownload).toBe(true);
    expect(fake.autoInstallOnAppQuit).toBe(true);

    emit('checking-for-update');
    emit('update-available', { version: '1.1.0' });
    emit('download-progress', { percent: 50 });
    emit('update-downloaded', { version: '1.1.0' });

    expect(seen.map((s) => s.state)).toEqual([
      'checking',
      'available',
      'downloading',
      'downloaded',
    ]);
    expect(updater.snapshot().version).toBe('1.1.0');
    expect(updater.snapshot().percent).toBe(100);
  });

  it('error 事件进入 error 态并走 log', () => {
    const { fake, emit } = makeFakeAutoUpdater();
    const logs: string[] = [];
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      broadcast: () => {},
      log: (_level, message) => logs.push(message),
    });
    emit('error', new Error('ENOTFOUND github.com'));
    expect(updater.snapshot().state).toBe('error');
    expect(updater.snapshot().error).toBe('ENOTFOUND github.com');
    expect(logs.join()).toContain('ENOTFOUND');
  });

  it('并发 check 只发一次请求（手动连点/启动检查撞车）', async () => {
    const resolvers: Array<() => void> = [];
    const { fake } = makeFakeAutoUpdater({
      checkForUpdates: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolvers.push(resolve);
          })
      ),
    });
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      broadcast: () => {},
    });
    const first = updater.check();
    const second = updater.check();
    expect(fake.checkForUpdates).toHaveBeenCalledTimes(1);
    resolvers[0]();
    await Promise.all([first, second]);
    // 完成后可以再次检查
    const third = updater.check();
    expect(fake.checkForUpdates).toHaveBeenCalledTimes(2);
    resolvers[1]();
    await third;
  });

  it('check 抛出异常时落到 error 态而不是拒绝', async () => {
    const { fake } = makeFakeAutoUpdater({
      checkForUpdates: vi.fn(async () => {
        throw new Error('404 latest.yml');
      }),
    });
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      broadcast: () => {},
    });
    const snap = await updater.check();
    expect(snap.state).toBe('error');
    expect(snap.error).toBe('404 latest.yml');
  });

  it('install 仅在 downloaded 后触发 quitAndInstall', () => {
    const { fake, emit } = makeFakeAutoUpdater();
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      broadcast: () => {},
    });
    expect(updater.install()).toBe(false);
    emit('update-downloaded', { version: '1.1.0' });
    expect(updater.install()).toBe(true);
    expect(fake.quitAndInstall).toHaveBeenCalledWith(false, true);
  });

  it('start 在延迟后自动检查一次', () => {
    const { fake } = makeFakeAutoUpdater();
    const scheduled: Array<() => void> = [];
    const updater = createUpdater({
      autoUpdater: fake,
      currentVersion: '1.0.0',
      broadcast: () => {},
      initialDelayMs: 1000,
      setTimeoutFn: ((fn: () => void) => {
        scheduled.push(fn);
        return { unref: () => {} } as unknown as ReturnType<typeof setTimeout>;
      }) as unknown as typeof setTimeout,
    });
    updater.start();
    expect(fake.checkForUpdates).not.toHaveBeenCalled();
    scheduled[0]();
    expect(fake.checkForUpdates).toHaveBeenCalledTimes(1);
  });
});
