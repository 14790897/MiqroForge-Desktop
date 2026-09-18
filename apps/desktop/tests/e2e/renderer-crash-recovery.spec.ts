/**
 * Issue #1035 — 渲染进程崩溃后自动重载（恢复动作对用户完全不可见）。
 *
 * 用 `webContents.forcefullyCrashRenderer()` 真实打掉渲染进程（和 OOM 同一条
 * `render-process-gone` 路径），然后断言：
 *
 * 1. 自动重载：主进程留下 `[main] renderer-reloaded: attempt=N reason=…`；
 * 2. 窗口恢复可用：重载后聊天输入框重新出现；
 * 3. **恢复动作对用户不可见**（2026-09 口径，替换旧的"弹框 + 插提示"期望）：
 *    不弹任何原生对话框（探针断言 showMessageBox 零调用）、聊天流里不出现任何
 *    崩溃提示文案；再静置复查一次——会话历史加载落定后也不许补插。
 *
 * 主进程探针（重载日志捕获 + 对话框监视）都装在 `globalThis` 上，和
 * issue-1019-frame-send-guard.spec.ts 同一套路。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "1035"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import {
  launchElectronApp,
  closeElectronApp,
  waitForInputReady,
  sendMessage,
} from './helpers/electron-setup';

const NOTICE_SELECTOR = '[data-testid="chat-system-notice"]';
const INPUT_SELECTOR = '[data-testid="chat-input-container"]';
/** Composer 停止按钮（Composer.tsx）：只在 `streaming` 时渲染。 */
const STOP_BUTTON_SELECTOR = '[aria-label="停止生成"]';

const POLL_INTERVAL_MS = 250;
const RELOAD_TIMEOUT_MS = 60_000;
/** 无提示口径的静置复查时长：确保提示不是"闪现一下又被冲掉"。 */
const SETTLE_RECHECK_MS = 2_500;
/**
 * 崩溃前「turn 已经真的在流」的等待预算（**前置条件**，不是断言）。
 *
 * 一条会话的第一条 send 会先走 threads.start（渲染层 30s 后放弃它、直接发
 * chat.send），而这条后端路径在本机实测偶发要 60–95s 才返回——日志里
 * `IPC thread/start took 94631ms` / `68614ms` 这类记录 09-17、09-18 都有，
 * 期间 chat.send 排不上队，mock 一个请求都收不到（表现为本用例先在
 * 「请求应到达 mock」这一步红，而不是后面任何一条语义断言）。这是环境/后端
 * 既有的慢，不是本 spec 要验的东西：与其让用例替它背锅，不如给足预算——
 * 后面每条断言一个都没放宽。
 */
const STREAM_WARMUP_TIMEOUT_MS = 150_000;

/** 崩溃相关的全部用户可见文案（对话框标题 + 聊天流提示）：一个都不许出现。 */
const CRASH_TEXTS = ['界面曾崩溃', '界面已崩溃', '界面反复崩溃'];

/** 等一条新的重载日志行（不假定 attempt 号，`-g` 过滤单独跑时它不会是 1）。 */
async function waitForNewReloadLine(
  electronApp: ElectronApplication,
  beforeCount: number
): Promise<string> {
  const deadline = Date.now() + RELOAD_TIMEOUT_MS;
  let seen: string[] = [];
  while (Date.now() < deadline) {
    seen = await electronApp.evaluate(() => (globalThis as any).__reloadLines ?? []);
    if (seen.length > beforeCount) return seen[seen.length - 1];
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  throw new Error(
    `未等到新的重载日志行；崩溃前已有 ${beforeCount} 条，` + `已捕获：${JSON.stringify(seen)}`
  );
}

/**
 * 装两个主进程探针。返回 false 表示对话框桩没打上——那种情况下后面只会得到
 * "对话框没被调用"之类的误导性失败，所以这里显式断言。
 */
async function installMainProcessProbes(electronApp: ElectronApplication): Promise<boolean> {
  return electronApp.evaluate(() => {
    const g = globalThis as any;

    // ── 探针 1：捕获重载日志行 ──────────────────────────────────────
    // console.log 在 main() 里已被换成写日志文件的版本，这里再包一层拿同样的
    // 文本；包在外层，原实现照常执行（日志文件不受影响）。
    g.__reloadLines = [];
    if (!g.__reloadProbeInstalled) {
      g.__reloadProbeInstalled = true;
      const orig = console.log;
      console.log = (...a: unknown[]) => {
        const text = a.map((x) => (x instanceof Error ? x.message : String(x))).join(' ');
        if (text.includes('renderer-reloaded') || text.includes('renderer-reload-skipped')) {
          g.__reloadLines.push(text);
        }
        orig(...(a as []));
      };
      // console.error 另行捕获崩溃事件行：main 日志不进 CI 作业日志（实测），
      // 若不在这里留副本，"render-process-gone 到底有没有触发"只能靠猜。
      g.__mainErrLines = [];
      const origErr = console.error;
      console.error = (...a: unknown[]) => {
        const text = a.map((x) => (x instanceof Error ? x.message : String(x))).join(' ');
        if (/render-process-gone|did-fail-load/.test(text)) {
          g.__mainErrLines.push(text);
        }
        origErr(...(a as []));
      };
    }

    // ── 探针 2：原生对话框监视 ──────────────────────────────────────
    // 走 globalThis.__ELECTRON__.dialog：trampoline 注入的就是这个对象，
    // src/shared/electron.ts 读的也是它，所以桩一定是被实现代码调到的那个。
    // 恢复动作对用户不可见（2026-09 口径）后，实现不应再碰 showMessageBox；
    // 这里换成"记录 + 立刻 resolve"的监视桩——若实现意外调用，用例断言立刻
    // 红，同时也不会把用例挂在真对话框上。
    g.__msgBoxCalls = [];
    const dialog = g.__ELECTRON__.dialog;
    const stub = (...args: any[]) => {
      const options = args[args.length - 1] ?? {};
      g.__msgBoxCalls.push({
        argCount: args.length,
        title: options.title,
        message: options.message,
        buttons: options.buttons,
      });
      return Promise.resolve({ response: 0 });
    };
    dialog.showMessageBox = stub;
    return dialog.showMessageBox === stub;
  });
}

/**
 * 真打掉渲染进程（与 OOM 走同一条 render-process-gone）。
 *
 * 不信任单次 `forcefullyCrashRenderer()`：Ubuntu CI（xvfb）实测它会"只 resolve
 * 不崩"——调用没抛、窗口还在，但 render-process-gone 从未出现、用例空等 60s。
 * 所以打完先等 `webContents.isCrashed()` 真变 true；没崩就用进程级 kill 补一刀
 * （getOSProcessId + process.kill，绕开 Electron 内部实现差异），两刀都没成就在
 * 报错里带全量诊断（窗口表 + 已记录的崩溃事件），不再以"等不到重载行"收场。
 */
async function crashRenderer(electronApp: ElectronApplication): Promise<void> {
  const target = await electronApp.evaluate(({ BrowserWindow }) => {
    const g = globalThis as any;
    // 独立监听所有窗口的 render-process-gone：只判断"事件有没有发"，
    // 不依赖被测实现把接线挂在哪个窗口上（避免"崩了别的窗口"时误判）。
    g.__goneEvents = g.__goneEvents ?? [];
    const wins = BrowserWindow.getAllWindows();
    for (const w of wins) {
      const wc = w.webContents as any;
      if (!wc.__goneHooked) {
        wc.__goneHooked = true;
        wc.on('render-process-gone', (_e: unknown, d: any) => {
          g.__goneEvents.push({
            winId: w.id,
            reason: d?.reason,
            exitCode: d?.exitCode,
            t: Date.now(),
          });
        });
      }
    }
    const win = wins[0];
    if (!win) return null;
    const snapshot = {
      count: wins.length,
      winId: win.id,
      title: win.getTitle(),
      url: win.webContents.getURL().slice(0, 100),
      goneBaseline: g.__goneEvents.length,
    };
    win.webContents.forcefullyCrashRenderer();
    return snapshot;
  });
  expect(target, '应能拿到主窗口并打掉其渲染进程').not.toBeNull();
  if (!target) return; // 类型收窄；为 null 的情况已被上面的断言兜住

  // 成功判据 = 独立监听器收到**新的** render-process-gone，而不是 webContents.isCrashed()：
  // 实现的重载是毫秒级的，isCrashed() 只在"崩了还没重载"的缝隙里为 true，250ms 轮询
  // 必然错过（首版就栽在这——崩溃明明发生了却报"打不掉"，还把兜底 kill 打到了
  // 刚重载起来的新渲染进程上）。用事件计数做判据没有这个时序缝。
  const gone = await waitForGoneEventCount(electronApp, target.goneBaseline, 10_000);
  if (!gone) {
    // 兜底：直接杀渲染进程（跨平台）。Ubuntu CI 实测 forcefullyCrashRenderer
    // 会"只 resolve 不崩"，这一步保证 render-process-gone 一定发出。
    // 必须显式 SIGKILL：Chromium 渲染进程忽略 SIGTERM（CI 实测默认信号打不死它，
    // 事件一直不来）；kill 的结果记下来进诊断——静默 catch 是上一轮的盲区。
    await electronApp.evaluate(({ BrowserWindow }) => {
      const g = globalThis as any;
      g.__killAttempts = g.__killAttempts ?? [];
      const win = BrowserWindow.getAllWindows()[0];
      const pid = win ? win.webContents.getOSProcessId() : -1;
      let err: string | null = null;
      try {
        process.kill(pid, 'SIGKILL');
      } catch (e) {
        err = String(e);
      }
      g.__killAttempts.push({ pid, err, t: Date.now() });
    });
  }
  const ok = gone || (await waitForGoneEventCount(electronApp, target.goneBaseline, 10_000));
  if (!ok) {
    const diag = await electronApp.evaluate(({ BrowserWindow }) => ({
      windows: BrowserWindow.getAllWindows().map((w) => ({
        id: w.id,
        crashed: w.webContents.isCrashed(),
      })),
      goneEvents: (globalThis as any).__goneEvents ?? [],
      killAttempts: (globalThis as any).__killAttempts ?? [],
    }));
    throw new Error(
      `渲染进程打不掉：forcefullyCrashRenderer 与进程级 kill 均未生效；诊断=${JSON.stringify(diag)}`
    );
  }
}

/** 等 `__goneEvents` 条数超过基线（= 收到了一条新的 render-process-gone）。 */
async function waitForGoneEventCount(
  electronApp: ElectronApplication,
  baseline: number,
  timeoutMs: number
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const n = await electronApp.evaluate(() => ((globalThis as any).__goneEvents ?? []).length);
    if (n > baseline) return true;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  return false;
}

/** 轮询主进程，等第 attempt 次重载的日志行落地。 */
async function waitForReloadLine(
  electronApp: ElectronApplication,
  attempt: number
): Promise<string> {
  const deadline = Date.now() + RELOAD_TIMEOUT_MS;
  let seen: string[] = [];
  while (Date.now() < deadline) {
    seen = await electronApp.evaluate(() => (globalThis as any).__reloadLines ?? []);
    const hit = seen.find((line) => line.includes(`renderer-reloaded: attempt=${attempt} `));
    if (hit) return hit;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  const diag = await electronApp
    .evaluate(({ BrowserWindow }) => ({
      crashEvents: (globalThis as any).__goneEvents ?? [],
      mainErrLines: (globalThis as any).__mainErrLines ?? [],
      killAttempts: (globalThis as any).__killAttempts ?? [],
      isCrashed: BrowserWindow.getAllWindows().map((w) => ({
        id: w.id,
        crashed: w.webContents.isCrashed(),
      })),
    }))
    .catch(() => null);
  await attachAppLogs();
  throw new Error(
    `未等到 renderer-reloaded attempt=${attempt}（恢复动作对用户不可见；` +
      `若实现意外弹框或等待，这里会等不到重载行）；已捕获：${JSON.stringify(seen)}；` +
      `崩溃诊断：${JSON.stringify(diag)}`
  );
}

/**
 * 把 app 落盘的三大日志（main / renderer / bridge，位于 <repo>/workspace/logs）
 * 作为附件带进测试报告。CI 作业日志读不到 main 的 stdout（实测），出问题时
 * 这几份文件是唯一能回放"main 侧到底发生了什么"的durable记录。
 */
async function attachAppLogs(): Promise<void> {
  const candidates = [
    path.resolve(process.cwd(), '..', '..'),
    path.resolve(process.cwd(), '..', '..', '..'),
  ];
  const root = candidates.find((c) => fs.existsSync(path.join(c, 'workspace', 'logs')));
  if (!root) return;
  const logDir = path.join(root, 'workspace', 'logs');
  const files = fs
    .readdirSync(logDir)
    .filter((f) => /^(electron-main|renderer|bridge)-.*\.log$/.test(f))
    .map((f) => path.join(logDir, f))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)
    .slice(0, 3);
  for (const file of files) {
    try {
      const text = fs.readFileSync(file, 'utf8').split('\n').slice(-200).join('\n');
      await test.info().attach(`app-log/${path.basename(file)}`, {
        body: text,
        contentType: 'text/plain',
      });
    } catch {
      /* 附件尽力而为，不因它再抛 */
    }
  }
}

interface RendererState {
  noticeCount: number;
  noticeText: string | null;
  /** document.body.innerText 全文：无提示口径按文案扫描断言。 */
  bodyText: string;
  inputReady: boolean;
  /** Composer 的「停止生成」按钮是否在屏：`streaming` 且输入框为空时渲染。 */
  streaming: boolean;
}

/**
 * 在（重载后的）渲染层里跑一段脚本，渲染层不可达时返回 null。
 *
 * 刻意用主进程的 `webContents.executeJavaScript`，不用 Playwright 的 page
 * 句柄：渲染进程是被真打掉再重载的，page 句柄能否跨这次 renderer 更换继续
 * 可用不是本 issue 要验证的东西，用它会把测试的成败绑到无关的实现细节上。
 * executeJavaScript 直接跑在重载后的渲染进程里，只依赖"窗口确实活着"。
 * 渲染层崩着 / 正在重载时 executeJavaScript 会抛，统一吞成 null 交给调用方轮询。
 */
async function evalInRenderer<T>(
  electronApp: ElectronApplication,
  script: string
): Promise<T | null> {
  return electronApp.evaluate(async ({ BrowserWindow }, src: string) => {
    const wc = BrowserWindow.getAllWindows()[0]?.webContents;
    if (!wc || wc.isDestroyed() || wc.isCrashed()) return null;
    try {
      return await wc.executeJavaScript(src);
    } catch {
      // 重载途中 document 还没就绪
      return null;
    }
  }, script) as Promise<T | null>;
}

/**
 * 一次性读渲染层 DOM（渲染层尚未就绪 / webContents 仍处于崩溃态时返回 null）。
 *
 * 刻意用主进程的 `webContents.executeJavaScript`，不用 Playwright 的 page
 * 句柄：渲染进程是被真打掉再重载的，page 句柄能否跨这次 renderer 更换继续
 * 可用不是本 issue 要验证的东西，用它会把测试的成败绑到无关的实现细节上。
 * executeJavaScript 直接跑在重载后的渲染进程里，只依赖"窗口确实活着"。
 * 渲染层崩着 / 正在重载时 executeJavaScript 会抛，统一吞成 null 交给调用方轮询。
 */
async function readSnapshotOnce(electronApp: ElectronApplication): Promise<RendererState | null> {
  return evalInRenderer<RendererState>(
    electronApp,
    `(() => {
      const notices = document.querySelectorAll(${JSON.stringify(NOTICE_SELECTOR)});
      return {
        noticeCount: notices.length,
        noticeText: notices.length ? notices[0].textContent : null,
        bodyText: document.body ? document.body.innerText : '',
        inputReady: !!document.querySelector(${JSON.stringify(INPUT_SELECTOR)}),
        streaming: !!document.querySelector(${JSON.stringify(STOP_BUTTON_SELECTOR)}),
      };
    })()`
  );
}

/** 等渲染层可达且界面就绪（聊天输入框已挂载），返回该快照；超时返回最后一次读数。 */
async function waitForUiReady(electronApp: ElectronApplication): Promise<RendererState | null> {
  const deadline = Date.now() + RELOAD_TIMEOUT_MS;
  let last: RendererState | null = null;
  while (Date.now() < deadline) {
    const snapshot = await readSnapshotOnce(electronApp);
    if (snapshot) {
      last = snapshot;
      if (snapshot.inputReady) return snapshot;
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  console.log('[diagnostic] waitForUiReady 超时：界面尚未就绪');
  return last;
}

test.describe('Issue #1035 — 渲染进程崩溃后自动重载与恢复提示', () => {
  // 两条用例共享同一个 app 实例、同一份崩溃预算，必须按序执行。
  // 超时走 describe.configure（Playwright 1.62 的 `test(title, {timeout}, fn)`
  // 里 TestDetails 只有 tag/annotation，传 timeout 既不生效也不通过类型检查）。
  test.describe.configure({ mode: 'serial', timeout: 180_000 });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string | undefined;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const patched = await installMainProcessProbes(electronApp);
    expect(patched, '主进程探针未能装上（对话框监视桩）').toBe(true);
  });

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* 渲染进程是被故意打掉的，收尾可能嘈杂 */
    }
  });

  test('崩溃后自动重载、界面恢复可用，且全程无任何提示或弹窗', async () => {
    // 崩溃前先确认界面已就绪：ChatConsole 已挂载。
    await waitForInputReady(page);

    await crashRenderer(electronApp);

    // 期望行为 1 + 2：自动重载 + 可检索日志行
    const line = await waitForReloadLine(electronApp, 1);
    expect(line).toContain('[main] renderer-reloaded: attempt=1 reason=');

    // 恢复动作对用户不可见（2026-09 口径）：不弹任何原生对话框。
    const calls = await electronApp.evaluate(() => (globalThis as any).__msgBoxCalls);
    expect(calls, '崩溃恢复不应弹任何对话框').toHaveLength(0);

    // 窗口恢复可用，且聊天流里没有插入任何崩溃提示。
    const state = await waitForUiReady(electronApp);
    expect(state, '重载后应能读到渲染层 DOM（等界面就绪超时）').not.toBeNull();
    expect(state!.inputReady, '重载后聊天界面应恢复可用').toBe(true);
    expect(state!.noticeCount, '不应出现任何系统提示消息').toBe(0);
    for (const t of CRASH_TEXTS) {
      expect(state!.bodyText, `界面不应出现「${t}」`).not.toContain(t);
    }

    // 静置复查：立刻没有还不够，会话历史加载落定后也不许补插。
    await new Promise((r) => setTimeout(r, SETTLE_RECHECK_MS));
    const settled = await readSnapshotOnce(electronApp);
    expect(settled, '静置复查应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.noticeCount, `静置 ${SETTLE_RECHECK_MS}ms 后仍不应有任何提示`).toBe(0);
    for (const t of CRASH_TEXTS) {
      expect(settled!.bodyText, `静置后界面仍不应出现「${t}」`).not.toContain(t);
    }
  });

  test('预算内再次崩溃：自动重载继续，attempt 递增', async () => {
    await crashRenderer(electronApp);

    const line = await waitForReloadLine(electronApp, 2);
    expect(line).toContain('reason=');

    const state = await waitForUiReady(electronApp);
    expect(state, '第二次重载后应能读到渲染层 DOM（等界面就绪超时）').not.toBeNull();
    expect(state!.inputReady).toBe(true);
    expect(state!.noticeCount, '第二次重载后同样不应有任何提示').toBe(0);

    await new Promise((r) => setTimeout(r, SETTLE_RECHECK_MS));
    const settled = await readSnapshotOnce(electronApp);
    expect(settled, '第二次重载的静置复查应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.noticeCount, `静置 ${SETTLE_RECHECK_MS}ms 后仍不应有任何提示`).toBe(0);
  });
});

// ── Issue #1035 P1: backend turn keeps producing output after renderer reload ──

interface RecoveryMockStream {
  url: string;
  stats: () => { started: number; finished: number; deltas: number };
  /**
   * Let the in-flight stream emit its final chunk on the next tick, instead of
   * waiting out the whole delta budget. Lets a test hold a turn open for as
   * long as it needs observations, then end it deterministically.
   */
  release: () => void;
  close: () => Promise<void>;
}

/**
 * OpenAI-compatible SSE mock that streams reasoning deltas for ~30s and then
 * a final content chunk. Used to verify that a turn whose renderer was killed
 * mid-stream continues to deliver events to the reloaded renderer.
 */
async function startRecoveryMock(): Promise<RecoveryMockStream> {
  let started = 0;
  let finished = 0;
  let deltas = 0;
  // Set by release(): the next tick flushes the final chunk of every stream
  // still open (and of any stream opened afterwards — the test only releases
  // when it means it).
  let releasing = false;

  const server = http.createServer((req, res) => {
    if (req.url && req.url.startsWith('/stats')) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ started, finished }));
      return;
    }
    if (!req.url || !req.url.includes('/chat/completions')) {
      res.writeHead(404);
      res.end();
      return;
    }

    req.on('data', () => {});
    req.on('end', () => {
      started += 1;
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      });

      const chunk = (delta: Record<string, unknown>, finish: string | null) =>
        'data: ' +
        JSON.stringify({
          id: 'chatcmpl-recovery',
          object: 'chat.completion.chunk',
          created: 0,
          model: 'recovery-mock',
          choices: [{ index: 0, delta, finish_reason: finish }],
        }) +
        '\n\n';

      res.write(chunk({ role: 'assistant' }, null));

      let i = 0;
      const timer = setInterval(() => {
        i += 1;
        if (releasing || i > 600) {
          clearInterval(timer);
          res.write(chunk({ content: ' recovery-final' }, 'stop'));
          res.write('data: [DONE]\n\n');
          finished += 1;
          res.end();
          return;
        }
        res.write(chunk({ reasoning_content: `r${i} ` }, null));
        deltas += 1;
      }, 50);
      res.on('close', () => clearInterval(timer));
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const addr = server.address();
  const port = typeof addr === 'object' && addr ? addr.port : 0;
  return {
    url: `http://127.0.0.1:${port}/v1`,
    stats: () => ({ started, finished, deltas }),
    release: () => {
      releasing = true;
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}

test.describe('Issue #1035 — 崩溃重载后继续接收后台 turn 输出', () => {
  // 300s：含 STREAM_WARMUP_TIMEOUT_MS 的冷启动预算 + 崩溃重载 + 收尾等待。
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  let electronApp: ElectronApplication;
  let page: Page;
  let mock: RecoveryMockStream;
  let miqiHome: string | undefined;

  test.beforeAll(async () => {
    mock = await startRecoveryMock();
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      for (const [, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const patched = await installMainProcessProbes(electronApp);
    expect(patched, '主进程探针未能装上（对话框监视桩）').toBe(true);
  });

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* renderer was crashed on purpose; teardown may be noisy */
    }
    await mock?.close();
  });

  test('后台 turn 仍在输出时崩溃重载，重载后仍能看到新进展', async () => {
    await waitForInputReady(page);

    await sendMessage(page, 'stream please');

    // Wait until the mock has streamed enough deltas and has not finished.
    const deadline = Date.now() + STREAM_WARMUP_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const s = mock.stats();
      if (s.deltas >= 30 && s.finished === 0) break;
      await page.waitForTimeout(250);
    }
    const beforeCrash = mock.stats();
    expect(beforeCrash.deltas, '崩溃前应已开始流式输出').toBeGreaterThanOrEqual(30);
    expect(beforeCrash.finished, '崩溃前后台 turn 不应已结束').toBe(0);

    await crashRenderer(electronApp);

    const line = await waitForReloadLine(electronApp, 1);
    expect(line).toContain('[main] renderer-reloaded: attempt=1 reason=');

    const state = await waitForUiReady(electronApp);
    expect(state, '重载后界面应恢复可用').not.toBeNull();
    expect(state!.inputReady, '重载后聊天输入框应重新出现').toBe(true);

    // 「对用户不可见」在「崩溃时后台 turn 还在跑」这条路径上同样成立。
    const calls = await electronApp.evaluate(() => (globalThis as any).__msgBoxCalls);
    expect(calls, '崩溃恢复不应弹任何对话框').toHaveLength(0);
    expect(state!.noticeCount, '不应出现任何系统提示消息').toBe(0);
    for (const t of CRASH_TEXTS) {
      expect(state!.bodyText, `界面不应出现「${t}」`).not.toContain(t);
    }

    // 重载后渲染层里唯一能把「生成中」点亮的就是恢复监听器收到的 progress
    // 事件：reload 没有 handleSend，streaming 初值为 false，load() 的三条
    // 判活启发式（streamingBySession / inFlightCache / snapshot）在全新挂载上
    // 全为空。所以「停止生成」按钮在屏 = 后台 turn 的进展真的送达并被应用了；
    // 轮询 mock 计数做不到这件事（那只是 HTTP 服务端的计数器，与渲染层无关）。
    const finalDeadline = Date.now() + 60_000;
    let lastBodyText = state!.bodyText;
    let sawStreamingAfterReload = false;
    while (Date.now() < finalDeadline) {
      const snapshot = await readSnapshotOnce(electronApp);
      if (snapshot) {
        lastBodyText = snapshot.bodyText;
        if (snapshot.streaming) sawStreamingAfterReload = true;
      }
      if (lastBodyText.includes('recovery-final')) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    expect(
      sawStreamingAfterReload,
      '重载后应回到「生成中」状态——证明后台 turn 的 progress 事件已送达渲染层'
    ).toBe(true);
    expect(lastBodyText, '重载后应渲染出后台 turn 的最终内容').toContain('recovery-final');
    // 终态：后台 turn 真在 mock 上跑完了，且渲染层已退出生成中。
    expect(mock.stats().finished, '后台 turn 应在 mock 上正常收尾').toBeGreaterThan(
      beforeCrash.finished
    );
    const settled = await readSnapshotOnce(electronApp);
    expect(settled, '终态应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.streaming, '收到 final 后不应再停留在生成中').toBe(false);
  });
});

// ── Issue #1035 复审 P1: thread-scoped turn 的崩溃重载恢复 ────────────────────
//
// 场景：在子线程 tab 里发起一个持续 streaming 的 turn（routing key =
// `desktop:<threadId>`），打掉渲染进程。重载后必须：
//   1. 子线程 tab 仍在、且仍是选中态（tab 状态按会话持久化）；
//   2. 后台 turn 的 progress 继续送达（恢复监听器按 routing key 认领事件，
//      旧实现只认基础 session，thread 事件被整条丢弃）；
//   3. final 落到 UI，气泡退出「生成中」。

interface RenderedThreadTab {
  threadId: string | null;
  active: boolean;
  label: string | null;
}

/**
 * 渲染层当前渲染出来的 thread tab 列表。
 *
 * 走主进程的 executeJavaScript（与 readSnapshotOnce 同一套路）：渲染进程是被
 * 真打掉再重载的，重载后没有可靠的 page 句柄。
 */
async function readThreadTabs(
  electronApp: ElectronApplication
): Promise<RenderedThreadTab[] | null> {
  return evalInRenderer<RenderedThreadTab[]>(
    electronApp,
    `(() => Array.from(document.querySelectorAll('[data-testid="chat-thread-tab"]')).map((el) => ({
      threadId: el.getAttribute('data-thread-id'),
      active: el.getAttribute('data-active') === 'true',
      label: el.textContent,
    })))()`
  );
}

/**
 * 切换 thread tab（崩溃重载后也用它，不用 Playwright 的 page 句柄）。
 *
 * 和 readThreadTabs / readSnapshotOnce 同一条主进程 executeJavaScript 通道，
 * 理由也一样：渲染进程是被真打掉再重载的，page 句柄能否跨这次 renderer 更换
 * 继续可用不是本 issue 要验证的东西。`el.click()` 派发的是会冒泡的原生 click，
 * React 挂在根上的委托监听器照常收到，等价于点这一下。
 */
async function clickThreadTab(
  electronApp: ElectronApplication,
  threadId: string
): Promise<boolean> {
  return (
    (await evalInRenderer<boolean>(
      electronApp,
      `(() => {
        const el = document.querySelector(
          '[data-testid="chat-thread-tab"][data-thread-id=${JSON.stringify(threadId)}]'
        );
        if (!el) return false;
        el.click();
        return true;
      })()`
    )) ?? false
  );
}

/** 等某个 thread tab 变成选中态（读渲染层真实属性，不依赖 page 句柄）。 */
async function waitForActiveThread(
  electronApp: ElectronApplication,
  threadId: string,
  timeoutMs: number
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const tabs = await readThreadTabs(electronApp);
    const hit = tabs?.find((t) => t.threadId === threadId);
    if (hit?.active) return true;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  return false;
}

/**
 * 从主进程注入一个 `agent:spawned` 事件，让渲染层长出子线程 tab。
 *
 * 这是 ChatConsole 真正订阅的那条通道（preload `agents.onSpawned`），只是 E2E
 * 里没有可用的真实 spawn 入口：真起一个 subagent 要跑沙箱，托管 runner 上会
 * 直接 skip（见 subagent-bridge-api.spec.ts）。这里只验证 UI 侧的 thread 路由，
 * 后端只认 `desktop:<threadId>` 这个 session key，与 subagent 是否真实存在无关。
 */
async function spawnThreadTab(
  electronApp: ElectronApplication,
  threadId: string,
  label: string
): Promise<void> {
  const sent = await electronApp.evaluate(
    ({ BrowserWindow }, arg: { threadId: string; label: string }) => {
      const win = BrowserWindow.getAllWindows()[0];
      if (!win || win.webContents.isDestroyed()) return false;
      win.webContents.send('agent:spawned', {
        sub_agent_id: 'e2e-sub-agent',
        sub_thread_id: arg.threadId,
        agent_type: 'code-agent',
        task_label: arg.label,
      });
      return true;
    },
    { threadId, label }
  );
  expect(sent, '应能把 agent:spawned 事件发进渲染层').toBe(true);
}

test.describe('Issue #1035 — thread-scoped turn 崩溃重载后可恢复', () => {
  // 300s：含 STREAM_WARMUP_TIMEOUT_MS 的冷启动预算 + 崩溃重载 + 收尾等待。
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  const THREAD_ID = 'e2e-thread-recovery';
  const THREAD_LABEL = 'E2E 子线程';

  let electronApp: ElectronApplication;
  let page: Page;
  let mock: RecoveryMockStream;
  let miqiHome: string | undefined;

  test.beforeAll(async () => {
    mock = await startRecoveryMock();
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      for (const [, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const patched = await installMainProcessProbes(electronApp);
    expect(patched, '主进程探针未能装上（对话框监视桩）').toBe(true);
  });

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* renderer was crashed on purpose; teardown may be noisy */
    }
    await mock?.close();
  });

  test('thread tab 保持 + 重载后继续 progress + final 落 UI', async () => {
    await waitForInputReady(page);

    // 造出子线程 tab 并选中它 —— 之后的发送都会走 `desktop:<threadId>`。
    await spawnThreadTab(electronApp, THREAD_ID, THREAD_LABEL);
    const tab = page.locator(`[data-testid="chat-thread-tab"][data-thread-id="${THREAD_ID}"]`);
    await expect(tab, '子线程 tab 应出现在 tab 条上').toBeVisible({ timeout: 10_000 });
    await tab.click();
    await expect(tab, '点击后该 tab 应为选中态').toHaveAttribute('data-active', 'true');

    await sendMessage(page, 'thread stream please');

    // 崩溃前确认：后台 turn 已经真的在 mock 上跑起来（thread routing key 被后端
    // 正常受理），且在流式输出、尚未结束。
    const crashDeadline = Date.now() + STREAM_WARMUP_TIMEOUT_MS;
    let beforeCrash = mock.stats();
    while (Date.now() < crashDeadline) {
      beforeCrash = mock.stats();
      if (beforeCrash.started >= 1 && beforeCrash.deltas >= 30 && beforeCrash.finished === 0) break;
      await page.waitForTimeout(250);
    }
    // >= 1（而不是 ==1）：provider 兜底/重试会让同一个 turn 再打一次 mock，
    // 不影响本用例要验的东西——只确认这个 thread turn 真的到达了 mock。
    expect(
      beforeCrash.started,
      'thread turn 的请求应到达 mock（routing key 已被后端受理）'
    ).toBeGreaterThanOrEqual(1);
    expect(beforeCrash.deltas, '崩溃前应已开始流式输出').toBeGreaterThanOrEqual(30);
    expect(beforeCrash.finished, '崩溃前后台 turn 不应已结束').toBe(0);

    await crashRenderer(electronApp);

    const line = await waitForReloadLine(electronApp, 1);
    expect(line).toContain('[main] renderer-reloaded: attempt=1 reason=');

    const state = await waitForUiReady(electronApp);
    expect(state, '重载后界面应恢复可用').not.toBeNull();
    expect(state!.inputReady, '重载后聊天输入框应重新出现').toBe(true);

    // 1) thread tab 恢复，且回到崩溃前选中的那个（reload 不再把用户甩回主 tab）
    const tabs = await readThreadTabs(electronApp);
    expect(tabs, '重载后应能读到渲染层 DOM').not.toBeNull();
    const restored = tabs!.find((t) => t.threadId === THREAD_ID);
    expect(restored, `重载后应恢复子线程 tab（实际渲染：${JSON.stringify(tabs)}）`).toBeDefined();
    expect(restored!.active, '重载后应仍选中崩溃前的 thread tab').toBe(true);

    // 2) + 3) progress 继续送达 → 「停止生成」回到屏上；final 落 UI 后退出生成中。
    // 与主 session 用例同理：全新挂载的渲染层里，唯一能把 streaming 点亮的就是
    // 恢复监听器收到的 progress。
    const finalDeadline = Date.now() + 60_000;
    let lastBodyText = state!.bodyText;
    let sawStreamingAfterReload = false;
    while (Date.now() < finalDeadline) {
      const snapshot = await readSnapshotOnce(electronApp);
      if (snapshot) {
        lastBodyText = snapshot.bodyText;
        if (snapshot.streaming) sawStreamingAfterReload = true;
      }
      if (lastBodyText.includes('recovery-final')) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    expect(
      sawStreamingAfterReload,
      '重载后应回到「生成中」——证明 thread turn 的 progress 事件已送达渲染层'
    ).toBe(true);
    expect(lastBodyText, '重载后应渲染出 thread turn 的最终内容').toContain('recovery-final');
    expect(mock.stats().finished, 'thread turn 应在 mock 上正常收尾').toBeGreaterThan(
      beforeCrash.finished
    );
    const settled = await readSnapshotOnce(electronApp);
    expect(settled, '终态应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.streaming, '收到 final 后不应再停留在生成中').toBe(false);
  });
});

// ── Issue #1035 复审 P1: 并发 turn —— reload 后只恢复当前 tab 的那条 ─────────
//
// 场景：子线程 tab 选中、它的 turn 正在 streaming 时打掉渲染进程。重载后恢复
// 监听器只能认领**当前选中 tab 的 routing key**（`desktop:<threadId>`）：
//
//   1. 同一 session 的另一条 key（主 tab 的会话 key）的事件必须被整条丢弃 ——
//      认领它就会把两条流汇进同一份 reasoning 缓冲（思考块混流），而且它的
//      terminal 会先抢到 turn latch，让真正在看的 turn 被当成 superseded 丢掉、
//      永远停在「生成中」；
//   2. 当前 tab 的 turn 仍要正常 progress / final 收尾；
//   3. 切走 tab 时不许把 spinner 悬挂：被放弃的 turn 的 terminal 已经不再被
//      认领，spinner 必须在切换时同步落下，而不是等一个永远不来的终态。
//
// 为什么是「注入另一条 key 的事件」而不是真的并跑两个 turn：handleSend 对同一
// session 的两次发送是互斥的 —— 新的 send 会 supersede 旧 turn（lifecycleRef
// 的 sessionKey 是基础 session，与 routing key 无关），UI 上根本起不出两个真
// 并发 turn；并发的另一条 key 只会来自后端自发的 turn（子 agent / 系统推进）。
// 所以这里让真 turn 跑在子线程 key 上（走完整的 bridge → main → renderer 链路），
// 另一条 key 的事件则从主进程按**真实 IPC 通道**注入：preload 的 chat.onProgress
// /onFinal 就是对该 payload 的直通转发，于是走的仍是渲染层恢复监听器那段代码。

/** 注入用的「另一条 key」turn id —— 与真 turn 的 turn_id 必须不同。 */
const OTHER_KEY_TURN_ID = 'turn-other-key-e2e';
/** 注入事件里的呼吸标记：出现在界面上 = 被误认领了。 */
const OTHER_KEY_BLEED = 'MAIN-KEY-BLEED';
/** 注入的 terminal 正文：出现说明它的 final 被认领（并抢走了 latch）。 */
const OTHER_KEY_FINAL = 'MAIN-KEY-LEDGER-FINAL';

/**
 * 读取渲染层当前会话的**基础 session key**（= 主 tab 的 routing key）。
 *
 * 不写死 `desktop:default`：会话 key 是应用状态，不是本用例要验的东西。渲染层
 * 自己把 tab 状态按 `miqi-active-thread:<sessionKey>` 存进 sessionStorage（也是
 * 重载后 tab 能恢复的原因），所以从那里反查拿到的就是真 key，且必须跨重载存活。
 */
async function readBaseSessionKey(electronApp: ElectronApplication): Promise<string | null> {
  return evalInRenderer<string | null>(
    electronApp,
    `(() => {
      const prefix = 'miqi-active-thread:';
      const keys = Object.keys(sessionStorage).filter((k) => k.startsWith(prefix));
      return keys.length ? keys[0].slice(prefix.length) : null;
    })()`
  );
}

/**
 * 从主进程起一个定时器，按真实 IPC 通道注入「另一条 routing key」的 turn 事件。
 *
 * 每条 progress 都带同一 turn_id；第一拍补一枪 `chat:final` —— 旧实现里它既会
 * 被认领（同一 session 的基础 session key 在接纳口径内），又会先 latch 住自己
 * 的 turn_id，让真正在看的 turn 的 final 永远判 superseded。
 */
async function startOtherKeyInjection(
  electronApp: ElectronApplication,
  arg: { sessionKey: string; turnId: string; bleed: string; finalText: string }
): Promise<void> {
  const startedOk = await electronApp.evaluate(
    (
      { BrowserWindow },
      params: { sessionKey: string; turnId: string; bleed: string; finalText: string }
    ) => {
      const g = globalThis as any;
      const win = BrowserWindow.getAllWindows()[0];
      if (!win || win.webContents.isDestroyed()) return false;
      let i = 0;
      g.__otherKeyTicks = 0;
      // 自证计数：**只有**真正送上 IPC 通道的事件才 +1。标记文本必须由 `params`
      // 传进来 —— evaluate 的回调是被序列化到主进程里执行的，直接引用测试文件
      // 模块作用域的常量会在定时器回调里抛 ReferenceError，而且抛在 `wc.send`
      // 之前：注入一个事件都没发出去，用例却会以「标记没出现」假绿通过（上一轮
      // 就是这么崩在主进程弹窗上的）。有了这个计数，下面的「无混流」断言才是
      // 可证伪的：sent === 0 时它直接红，而不是替一个坏掉的注入背书。
      g.__otherKeySent = 0;
      g.__otherKeyTimer = setInterval(() => {
        i += 1;
        g.__otherKeyTicks = i;
        const wc = BrowserWindow.getAllWindows()[0]?.webContents;
        if (!wc || wc.isDestroyed()) return;
        try {
          wc.send('chat:progress', {
            type: 'progress',
            session_key: params.sessionKey,
            turn_id: params.turnId,
            stream: 'reasoning',
            delta: ` ${params.bleed}-${i} `,
          });
          g.__otherKeySent += 1;
          if (i === 1) {
            wc.send('chat:final', {
              type: 'final',
              session_key: params.sessionKey,
              turn_id: params.turnId,
              content: params.finalText,
            });
            g.__otherKeySent += 1;
          }
        } catch {
          // 渲染层这一拍已经拆了：什么都没送达，就不计数——别让计数替失败背书。
        }
      }, 200);
      return true;
    },
    arg
  );
  expect(startedOk, '应能在主进程里装上另一条 key 的事件注入').toBe(true);
}

/**
 * 注入实际送上 IPC 通道的事件数（`__otherKeySent`）。注入跑完读一次，为 0 就说明
 * 注入被上面的作用域类 bug 吞了——此时「标记没出现」的断言毫无意义。
 */
async function readOtherKeySentCount(electronApp: ElectronApplication): Promise<number> {
  return electronApp.evaluate(() => (globalThis as any).__otherKeySent ?? 0);
}

/** 停掉注入，返回一共注入了多少拍（0 表示它根本没跑过）。 */
async function stopOtherKeyInjection(electronApp: ElectronApplication): Promise<number> {
  return electronApp.evaluate(() => {
    const g = globalThis as any;
    if (g.__otherKeyTimer) {
      clearInterval(g.__otherKeyTimer);
      g.__otherKeyTimer = null;
    }
    return g.__otherKeyTicks ?? 0;
  });
}

/** 轮询「生成中」标志，直到它变成 `want` 或超时；返回最后一次读数。 */
async function waitForStreamingFlag(
  electronApp: ElectronApplication,
  want: boolean,
  timeoutMs: number
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  let last = !want;
  while (Date.now() < deadline) {
    const snapshot = await readSnapshotOnce(electronApp);
    if (snapshot) {
      last = snapshot.streaming;
      if (last === want) return last;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return last;
}

test.describe('Issue #1035 — 并发 turn：reload 后只恢复当前 tab 的那条', () => {
  // 300s：含 STREAM_WARMUP_TIMEOUT_MS 的冷启动预算 + 崩溃重载 + 注入窗口 +
  // 切 tab 观察 + 收尾等待，180s 会被冷启动挤爆。
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  const THREAD_ID = 'e2e-thread-concurrent';
  const THREAD_LABEL = 'E2E 并发子线程';

  let electronApp: ElectronApplication;
  let page: Page;
  let mock: RecoveryMockStream;
  let miqiHome: string | undefined;

  test.beforeAll(async () => {
    mock = await startRecoveryMock();
    const fixture = await launchElectronApp((config: any) => {
      const providers = config.providers ?? {};
      for (const [, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.url;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    const patched = await installMainProcessProbes(electronApp);
    expect(patched, '主进程探针未能装上（对话框监视桩）').toBe(true);
  });

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* renderer was crashed on purpose; teardown may be noisy */
    }
    await mock?.close();
  });

  test('另一条 key 的事件不混入当前 tab，切走不悬挂，当前 tab 的 turn 正常收尾', async () => {
    await waitForInputReady(page);

    // ── 造出子线程 tab 并选中，让真 turn 跑在 `desktop:<THREAD_ID>` 上 ──
    await spawnThreadTab(electronApp, THREAD_ID, THREAD_LABEL);
    const threadTab = page.locator(
      `[data-testid="chat-thread-tab"][data-thread-id="${THREAD_ID}"]`
    );
    await expect(threadTab, '子线程 tab 应出现在 tab 条上').toBeVisible({ timeout: 10_000 });
    await threadTab.click();
    await expect(threadTab, '点击后该 tab 应为选中态').toHaveAttribute('data-active', 'true');

    await sendMessage(page, 'concurrent stream please');

    // 崩溃前：turn 已在 mock 上流式输出且尚未结束。
    const crashDeadline = Date.now() + STREAM_WARMUP_TIMEOUT_MS;
    let beforeCrash = mock.stats();
    while (Date.now() < crashDeadline) {
      beforeCrash = mock.stats();
      if (beforeCrash.started >= 1 && beforeCrash.deltas >= 30 && beforeCrash.finished === 0) break;
      await page.waitForTimeout(250);
    }
    expect(beforeCrash.started, 'thread turn 的请求应到达 mock').toBeGreaterThanOrEqual(1);
    expect(beforeCrash.deltas, '崩溃前应已开始流式输出').toBeGreaterThanOrEqual(30);
    expect(beforeCrash.finished, '崩溃前后台 turn 不应已结束').toBe(0);

    await crashRenderer(electronApp);

    const line = await waitForReloadLine(electronApp, 1);
    expect(line).toContain('[main] renderer-reloaded: attempt=1 reason=');

    const state = await waitForUiReady(electronApp);
    expect(state, '重载后界面应恢复可用').not.toBeNull();
    expect(state!.inputReady, '重载后聊天输入框应重新出现').toBe(true);

    const tabs = await readThreadTabs(electronApp);
    const restored = tabs?.find((t) => t.threadId === THREAD_ID);
    expect(restored, `重载后应恢复子线程 tab（实际渲染：${JSON.stringify(tabs)}）`).toBeDefined();
    expect(restored!.active, '重载后应仍选中崩溃前的 thread tab').toBe(true);

    // ── 注入另一条 key（主 tab 的基础 session）的事件 ──
    const baseKey = await readBaseSessionKey(electronApp);
    expect(baseKey, '应能从渲染层 sessionStorage 反查到基础 session key').toBeTruthy();
    expect(baseKey, '注入的必须是另一条 routing key').not.toBe(`desktop:${THREAD_ID}`);

    await startOtherKeyInjection(electronApp, {
      sessionKey: baseKey as string,
      turnId: OTHER_KEY_TURN_ID,
      bleed: OTHER_KEY_BLEED,
      finalText: OTHER_KEY_FINAL,
    });

    // 注入期间持续取样：标记一次都不许出现；同时当前 tab 的 turn 必须仍在推进
    // （收起 spinner 之外，唯一能把它点亮的就是恢复监听器收到的真 progress）。
    const injected: string[] = [];
    let sawOwnStreaming = false;
    const bleedDeadline = Date.now() + 12_000;
    while (Date.now() < bleedDeadline) {
      const snapshot = await readSnapshotOnce(electronApp);
      if (snapshot) {
        if (snapshot.streaming) sawOwnStreaming = true;
        for (const marker of [OTHER_KEY_BLEED, OTHER_KEY_FINAL]) {
          if (snapshot.bodyText.includes(marker) && !injected.includes(marker)) {
            injected.push(marker);
          }
        }
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    expect(
      injected,
      '另一条 key 的事件被认领了：并发 turn 的 reasoning/terminal 混进了当前 tab'
    ).toEqual([]);
    expect(sawOwnStreaming, '当前 tab 的 turn 应仍在「生成中」——它自己的 progress 还在被认领').toBe(
      true
    );

    // ── 切走：被放弃的 turn 的 terminal 不再被认领，spinner 不许悬挂 ──
    const ticks = await stopOtherKeyInjection(electronApp);
    expect(ticks, '注入应真的跑过（否则这一段没验到东西）').toBeGreaterThan(0);
    // 注入自证：上面「标记一次都没出现」只有在事件真的发出去过时才有意义。
    // 计数在 wc.send 返回后才 +1，为 0 就说明这段是假绿（注入被吞了）。
    expect(
      await readOtherKeySentCount(electronApp),
      '注入的事件应真的送上 IPC 通道（为 0 = 注入根本没发生，混流断言是假绿）'
    ).toBeGreaterThan(0);

    expect(await clickThreadTab(electronApp, 'main'), '应能点到主 tab').toBe(true);
    expect(await waitForActiveThread(electronApp, 'main', 5_000), '主 tab 应变为选中态').toBe(true);
    expect(
      await waitForStreamingFlag(electronApp, false, 5_000),
      '切走后 spinner 必须落下（那条 turn 的终态已不再被认领，悬挂即 bug）'
    ).toBe(false);
    // 再静置一拍：真 turn 的 progress 仍在流，不许把它重新点亮。
    await new Promise((r) => setTimeout(r, 1_500));
    const afterSwitch = await readSnapshotOnce(electronApp);
    expect(afterSwitch, '切换后应能读到渲染层 DOM').not.toBeNull();
    expect(
      afterSwitch!.streaming,
      '切走后当前 tab 不应停留在生成中（另一条 key 的 progress 不该被认领）'
    ).toBe(false);

    // ── 切回子线程 tab，放开 mock：真 turn 的 final 必须收尾 ──
    expect(await clickThreadTab(electronApp, THREAD_ID), '应能点回子线程 tab').toBe(true);
    expect(
      await waitForActiveThread(electronApp, THREAD_ID, 5_000),
      '切回后子线程 tab 应重新选中'
    ).toBe(true);
    mock.release();

    const finalDeadline = Date.now() + 60_000;
    let lastBodyText = '';
    while (Date.now() < finalDeadline) {
      const snapshot = await readSnapshotOnce(electronApp);
      if (snapshot) lastBodyText = snapshot.bodyText;
      if (lastBodyText.includes('recovery-final')) break;
      await new Promise((r) => setTimeout(r, 250));
    }
    expect(
      lastBodyText,
      '当前 tab 的 turn 应正常收尾：final 落到 UI（若 latch 被另一条 key 抢走，这里永远等不到）'
    ).toContain('recovery-final');
    expect(
      lastBodyText,
      '注入的 terminal 不应被认领（否则会渲染出它的 assistant 气泡）'
    ).not.toContain(OTHER_KEY_FINAL);
    expect(mock.stats().finished, 'thread turn 应在 mock 上正常收尾').toBeGreaterThan(
      beforeCrash.finished
    );
    const settled = await readSnapshotOnce(electronApp);
    expect(settled, '终态应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.streaming, '收到 final 后不应再停留在生成中').toBe(false);
  });
});
