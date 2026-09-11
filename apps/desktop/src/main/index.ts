import { join } from 'path';
import { inspect } from 'util';
import { createHash } from 'node:crypto';
import { electron } from '../shared/electron';
import { registerIpcHandlers } from './ipc';
import { BridgeManager } from './bridge';
import { writeMainProcessLog } from './electron-log';
import { createSplash, closeSplash } from './splash';
import { safeWrite, guardStdStreams } from './console-guard';

const originalConsoleLog = console.log.bind(console);
const originalConsoleWarn = console.warn.bind(console);
const originalConsoleError = console.error.bind(console);

const { app, BrowserWindow, shell, Menu } = electron;

let mainWindow: typeof BrowserWindow.prototype | null = null;
let bridgeManager: BridgeManager | null = null;

/** Resolve the app icon path for both dev (source) and packaged (resources) modes. */
function getIconPath(): string {
  const iconName = process.platform === 'darwin' ? 'icon.icns' : 'icon.ico';
  if (app.isPackaged) {
    return join(process.resourcesPath, iconName);
  }
  return join(__dirname, '../../src/renderer/assets', iconName);
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 900,
    minHeight: 760,
    title: 'MiQroForge Desktop',
    icon: getIconPath(),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: join(__dirname, '../preload/index.js'),
      // E2E 专用标记：helper 设 MIQI_E2E=1 时随 argv 下发到 sandbox preload，
      // 渲染层据此跳过隐私协议确认门（#837），避免 E2E 被全屏确认页阻断。
      // 仅限未打包环境——打包产物被外部注入 MIQI_E2E=1 不得绕过确认门。
      additionalArguments: !app.isPackaged && process.env['MIQI_E2E'] === '1' ? ['--miqi-e2e'] : [],
    },
  });

  // Remove native menu bar — app has its own navigation
  mainWindow.removeMenu();

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url);
    return { action: 'deny' };
  });

  // Diagnostics: surface preload / renderer failures to the terminal
  mainWindow.webContents.on(
    'did-fail-load',
    (_event, errorCode, errorDescription, validatedURL) => {
      // console.error is globally overridden to call writeMainProcessLog,
      // so we only call it once here to avoid double-logging.
      console.error(
        `[main] did-fail-load: code=${errorCode} desc=${errorDescription} url=${validatedURL}`
      );
    }
  );

  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    console.error(
      `[main] render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`
    );
  });

  mainWindow.webContents.on('console-message', (_event: unknown, ...args: unknown[]) => {
    // Support both old API (level, message, ...) and new API (event params object)
    const first = args[0];
    let level = 0;
    let message = '';
    if (typeof first === 'object' && first !== null && 'level' in first) {
      const params = first as { level: number; message: string };
      level = params.level;
      message = params.message;
    } else {
      level = (first as number) ?? 0;
      message = (args[1] as string) ?? '';
    }
    // Strip %c CSS format specifiers — Electron does not resolve them,
    // so they appear as literal text in the log (e.g. React DevTools banner).
    const cleanMessage = message.replace(/%c/g, '');
    // Map Electron console-message level to log level string
    // 0=verbose, 1=info(log), 2=warning, 3=error
    const levelStr = level >= 3 ? 'ERROR' : level >= 2 ? 'WARN' : 'INFO';
    writeMainProcessLog(levelStr, cleanMessage, bridgeManager?.getProjectRoot(), 'renderer');
  });

  // 添加右键菜单，支持打开开发者工具
  mainWindow.webContents.on('context-menu', (_event, props) => {
    const { x, y } = props;
    const win = mainWindow;
    if (!win) return;
    Menu.buildFromTemplate([
      {
        label: '开发者工具',
        click: () => {
          win.webContents.openDevTools();
        },
      },
    ]).popup({ window: win, x, y });
  });

  if (process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL']);
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'));
  }
}

export function main(): void {
  const formatLogArgs = (args: unknown[]) =>
    args.map((arg) => (typeof arg === 'string' ? arg : inspect(arg, { depth: 4 }))).join(' ');

  // When stdout is a pipe whose reader has gone away (e.g. the app was
  // spawned by another process that exited), writing to it throws EPIPE and
  // would crash the main process as an uncaught exception. Guard the console
  // writes (both the synchronous throw and the async stream 'error' event);
  // the log file is the durable record, the console is best-effort.
  guardStdStreams();

  console.log = (...args: unknown[]) => {
    writeMainProcessLog('INFO', formatLogArgs(args), bridgeManager?.getProjectRoot());
    safeWrite(process.stdout, originalConsoleLog, args);
  };
  console.warn = (...args: unknown[]) => {
    writeMainProcessLog('WARN', formatLogArgs(args), bridgeManager?.getProjectRoot());
    safeWrite(process.stderr, originalConsoleWarn, args);
  };
  console.error = (...args: unknown[]) => {
    writeMainProcessLog('ERROR', formatLogArgs(args), bridgeManager?.getProjectRoot());
    safeWrite(process.stderr, originalConsoleError, args);
  };

  // ── Dev-mode cache isolation ──────────────────────────────────────
  // 多 checkout 并行开发（如 ziti 与 539 工作区）时，各实例共享同一个
  // Chromium userData（%APPDATA%\miqi-desktop），会互相踩缓存：启动时
  // disk_cache / Gpu Cache Creation failed 报错、前端 localStorage 串味
  // （会话/配置互相覆盖）。开发模式下按仓库绝对路径 hash 出独立子目录
  // （%APPDATA%\miqi-desktop-dev\ws-<hash>），每个工作区各用各的缓存。
  // 打包版保持默认行为（单安装目录，无多实例问题）。
  if (!app.isPackaged) {
    const repoRoot = join(__dirname, '../../..');
    const wsHash = createHash('sha256').update(repoRoot).digest('hex').slice(0, 16);
    app.setPath('userData', join(app.getPath('appData'), 'miqi-desktop-dev', `ws-${wsHash}`));
  }

  app.whenReady().then(() => {
    bridgeManager = new BridgeManager();
    registerIpcHandlers(bridgeManager);

    // Forward bridge events to renderer
    const onState = (status: unknown) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('runtime:state', status);
      }
    };
    const onLog = (msg: string) => {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('runtime:log', msg);
      }
    };
    bridgeManager.on('state', onState);
    bridgeManager.on('log', onLog);

    createSplash(() => {
      closeSplash();
    });
    createWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });

  app.on('window-all-closed', () => {
    bridgeManager?.stop();
    if (process.platform !== 'darwin') {
      app.quit();
    }
  });

  app.on('before-quit', () => {
    bridgeManager?.stop();
  });
}
