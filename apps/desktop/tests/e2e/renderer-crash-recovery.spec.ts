/**
 * Issue #1035 — 渲染进程崩溃后自动重载与恢复提示（期望行为 1~4）。
 *
 * 用 `webContents.forcefullyCrashRenderer()` 真实打掉渲染进程（和 OOM 同一条
 * `render-process-gone` 路径），然后断言：
 *
 * 1. 自动重载：主进程留下 `[main] renderer-reloaded: attempt=N reason=…`；
 * 2. 窗口恢复可用：重载后聊天输入框重新出现；
 * 3. 期望行为 4：原生对话框弹出（已桩掉，不阻塞 CI）；
 * 4. **点击不是重载的前提**：对话框桩返回一个**永不 resolve** 的 Promise——
 *    若实现把重载藏在 `await dialog.showMessageBox(...)` 之后，重载就永远
 *    不会发生，第 1/2 条断言会直接失败。这是对「预算内自动重载」最硬的反向
 *    验证，比"对话框被调用过"强得多；
 * 5. 恢复提示：渲染层挂载时拉到 notice，往当前会话插一条 `chat-system-notice`
 *    系统消息，且一次页面加载只插一条。**必须轮询等它出现**：插入是挂载后的
 *    异步动作，紧接着的会话历史加载还会把 messages 整个换掉、由 ensure effect
 *    补插一次——首帧读到 0 是合法中间态，取"第一个就绪快照"会假红。等到之后再
 *    静置复查一次，防止"出现过又被冲掉"假绿。
 *
 * 主进程探针（重载日志捕获 + 对话框桩）都装在 `globalThis` 上，和
 * issue-1019-frame-send-guard.spec.ts 同一套路。
 *
 * 6. **会话隔离（外部评审 P1）**：恢复提示描述的是"崩溃瞬间还有在飞 turn 的
 *    会话"，主进程在 `RecoveryNotice.inFlightSessionKeys` 里记了下来。第 3 条
 *    用例在 A 会话发一条真消息（内联流式 mock，同 issue-1019 的套路）把 A 顶进
 *    在飞登记表，崩溃后先断言 A 里恰好一条，再切到 B 断言 0 条、切回 A 再断言
 *    恰好一条。没有这条用例，"提示被插进了别的会话"以及"来回切会话把补插预算
 *    耗光"都不会被发现。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "1035"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import {
  launchElectronApp,
  closeElectronApp,
  waitForInputReady,
  sendMessage,
  getSidebarSessionItems,
} from './helpers/electron-setup';

/** 渲染层插入的提示文案（与 ChatConsole.tsx 的 RENDERER_RECOVERY_NOTICE_TEXT 一致）。 */
const NOTICE_TEXT =
  '界面曾崩溃并已重新加载；进行中的 turn 输出可能不完整（若后台仍在运行，切回会话可继续看到新输出）';

const NOTICE_SELECTOR = '[data-testid="chat-system-notice"]';
const INPUT_SELECTOR = '[data-testid="chat-input-container"]';

const POLL_INTERVAL_MS = 250;
const RELOAD_TIMEOUT_MS = 60_000;
/** 等恢复提示出现的时间上限（渲染层要先挂载、再异步拉 notice，然后才插入）。 */
const NOTICE_TIMEOUT_MS = 30_000;
/** 提示出现后的静置复查时长：确保它不是"闪现一下又被冲掉"。 */
const NOTICE_SETTLE_MS = 2_500;

/**
 * 触发**长流**的消息标记：只有带上它的 turn 才会慢慢吐 45s，也就是"崩溃时仍然
 * 在飞"的那个 turn。其余请求（含标题生成之类）一律短流快速收尾，免得把测试
 * 拖成分钟级。长短按请求体里的标记分流（见 startStreamingMock）。
 */
const LONG_STREAM_MARKER = 'ISOLATION-LONG';
/** 短消息标记：用来给 A、B 会话各攒一条落盘的历史，它们必须很快收尾。 */
const SHORT_MARKER = 'ISOLATION-SHORT';
/** 短流 delta 数：5 × 50ms ≈ 0.25s 收尾，够让这条会话落盘进侧栏。 */
const SHORT_DELTA_COUNT = 5;
/** 长流 delta 数：900 × 50ms = 45s，覆盖"发出去 → 崩溃 → 切到 B → 切回 A"。 */
const LONG_DELTA_COUNT = 900;
const DELTA_INTERVAL_MS = 50;

/**
 * 补插窗口（ChatConsole 的 `RECOVERY_NOTICE_ENSURE_WINDOW_MS`，60s）的保守内界。
 *
 * 第 3 条用例里"切到 B 后 0 条"是负向断言：如果它只是发生在窗口过期之后，
 * 那它什么都没证明——窗口没了本来就哪儿都不插。所以断言"观察时刻距提示出现
 * 不超过 50s"：提示上屏距它被拉到的时刻只差一个 effect 周期（亚秒级），
 * 所以 50s 内必然还在 60s 窗口里。
 */
const ENSURE_WINDOW_GUARD_MS = 50_000;

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

    // ── 探针 2：原生对话框桩 ────────────────────────────────────────
    // 走 globalThis.__ELECTRON__.dialog：trampoline 注入的就是这个对象，
    // src/shared/electron.ts 读的也是它，所以桩一定是被实现代码调到的那个。
    g.__msgBoxCalls = [];
    const dialog = g.__ELECTRON__.dialog;
    const stub = (...args: any[]) => {
      const options = args[args.length - 1] ?? {};
      g.__msgBoxCalls.push({
        // 参数个数 = 是否带父窗。带父窗是窗口模态（会禁掉刚恢复的界面），
        // 预算内路径必须不带，断言见下。
        argCount: args.length,
        title: options.title,
        message: options.message,
        buttons: options.buttons,
      });
      // 永不 resolve：点击不是重载的前提，所以不点也必须能恢复。
      return new Promise(() => {});
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
    `未等到 renderer-reloaded attempt=${attempt}（对话框桩永不 resolve，` +
      `若实现 await 了它就永远等不到）；已捕获：${JSON.stringify(seen)}；` +
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
  inputReady: boolean;
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

/** 一次性读渲染层 DOM（渲染层尚未就绪 / webContents 仍处于崩溃态时返回 null）。 */
async function readSnapshotOnce(electronApp: ElectronApplication): Promise<RendererState | null> {
  return evalInRenderer<RendererState>(
    electronApp,
    `(() => {
      const notices = document.querySelectorAll(${JSON.stringify(NOTICE_SELECTOR)});
      return {
        noticeCount: notices.length,
        noticeText: notices.length ? notices[0].textContent : null,
        inputReady: !!document.querySelector(${JSON.stringify(INPUT_SELECTOR)}),
      };
    })()`
  );
}

/** App.tsx 每次 sessionKey 变化都写这里，是"当前是哪个会话"最直接的口径。 */
async function readActiveSessionKey(electronApp: ElectronApplication): Promise<string | null> {
  return evalInRenderer<string | null>(
    electronApp,
    `(() => { try { return window.localStorage.getItem('miqi:lastSession'); } catch { return null; } })()`
  );
}

/** 侧栏会话卡片（Sidebar.tsx 里没有 data 属性，只能按结构选；同 electron-setup）。 */
const SIDEBAR_CARD_SELECTOR = 'div.flex.flex-col.shrink-0.border-r button.rounded-xl';

/**
 * 点第 idx 张会话卡片。返回卡片总数，越界返回 -1，渲染层不可达返回 null。
 *
 * 卡片上既没有 key 也没有可用的 data 属性（标题还可能重复），所以"切到某一个
 * 会话"只能靠点击 + 从 localStorage 读回当前 key 来确认，不能靠文本匹配。
 */
async function clickSidebarCard(
  electronApp: ElectronApplication,
  idx: number
): Promise<number | null> {
  return evalInRenderer<number>(
    electronApp,
    `(() => {
      const shell = document.querySelector('div.flex.flex-col.shrink-0.border-r');
      const cards = shell ? Array.from(shell.querySelectorAll('button.rounded-xl')) : [];
      if (${idx} >= cards.length) return -1;
      cards[${idx}].click();
      return cards.length;
    })()`
  );
}

/** 轮询等当前会话变成 key（每次点击后单独限时，避免被前面的点击长时间卡住）。 */
async function waitForActiveSession(
  electronApp: ElectronApplication,
  key: string,
  timeoutMs: number
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await readActiveSessionKey(electronApp)) === key) return true;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  return false;
}

/**
 * 按会话 key 切会话：反复扫侧栏卡片，点一张就回头确认 localStorage 里的当前
 * key。与卡片顺序、标题文案都无关，只依赖"点了会切"这条 UI 契约；扫完一轮没
 * 命中就从头再扫（侧栏是异步刷新的，卡片可能刚出现）。
 */
async function switchToSessionByKey(
  electronApp: ElectronApplication,
  key: string,
  timeoutMs = 30_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let idx = 0;
  while (Date.now() < deadline) {
    const total = await clickSidebarCard(electronApp, idx);
    if (total === null) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      continue;
    }
    if (total === -1) {
      idx = 0;
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      continue;
    }
    idx += 1;
    if (await waitForActiveSession(electronApp, key, 3_000)) return;
  }
  throw new Error(`切到会话 ${key} 超时（侧栏点击后 localStorage 未变成该 key）`);
}

/** 崩溃前的切会话：走 Playwright 的 page 句柄（那时渲染层还没被换掉）。 */
async function switchToSessionByKeyOnPage(
  page: Page,
  key: string,
  timeoutMs = 30_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let idx = 0;
  while (Date.now() < deadline) {
    const cards = getSidebarSessionItems(page);
    const n = await cards.count().catch(() => 0);
    if (n === 0) {
      idx = 0;
      await page.waitForTimeout(POLL_INTERVAL_MS);
      continue;
    }
    if (idx >= n) idx = 0;
    await cards
      .nth(idx)
      .click()
      .catch(() => {});
    idx += 1;

    const inner = Date.now() + 3_000;
    while (Date.now() < inner) {
      const cur = await page.evaluate(() => {
        try {
          return window.localStorage.getItem('miqi:lastSession');
        } catch {
          return null;
        }
      });
      if (cur === key) return;
      await page.waitForTimeout(POLL_INTERVAL_MS);
    }
  }
  throw new Error(`切到会话 ${key} 超时（侧栏点击后 localStorage 未变成该 key）`);
}

/**
 * 等会话出现在 `sessions.list()` 里。侧栏渲染的就是这份列表，不落盘的会话
 * （渲染层刚生成 key、还没写过消息的）在侧栏里没有卡片，也就点不到——而崩溃
 * 之后只能靠点侧栏切会话。
 */
async function waitForSessionOnDisk(page: Page, key: string, timeoutMs = 20_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let last: string[] = [];
  while (Date.now() < deadline) {
    last = await page.evaluate(async () => {
      try {
        const r = await (window as any).miqi.sessions.list();
        return (r?.sessions ?? []).map((s: any) => s.key as string);
      } catch {
        return [];
      }
    });
    if (last.includes(key)) return;
    await page.waitForTimeout(POLL_INTERVAL_MS);
  }
  throw new Error(`会话 ${key} 未在 ${timeoutMs}ms 内落盘；当前列表：${JSON.stringify(last)}`);
}

/**
 * 等用户气泡计数在连续 3 次 500ms 轮询里保持不变（且不少于 min）。
 * 切回会话会触发历史加载与内存快照合并；实测两者并发的短窗口里同一条用户消息
 * 可能短暂出现两份（随后稳定）——发送下一步前先等计数稳定，避免把这类同步噪声
 * 记进后续断言。
 */
async function waitForStableUserBubbleCount(page: Page, min = 1): Promise<void> {
  const bubbles = page.getByTestId('chat-message-user');
  const deadline = Date.now() + 15_000;
  let prev = -1;
  let stable = 0;
  while (Date.now() < deadline) {
    const n = await bubbles.count();
    if (n >= min && n === prev) stable += 1;
    else stable = 0;
    prev = n;
    if (stable >= 3) return;
    await page.waitForTimeout(500);
  }
}

/** 等当前会话变成 excludeKey 之外的某个 key（点「+」新建会话后用它拿 B 的 key）。 */
async function waitForNewActiveSession(
  electronApp: ElectronApplication,
  excludeKey: string,
  timeoutMs = 15_000
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let cur = await readActiveSessionKey(electronApp);
  while (Date.now() < deadline && (!cur || cur === excludeKey)) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    cur = await readActiveSessionKey(electronApp);
  }
  if (!cur || cur === excludeKey) throw new Error(`点「+」后当前会话仍是 ${cur}`);
  return cur;
}

/** 等渲染层可达（重载途中 executeJavaScript 会抛），返回第一个就绪快照。 */
async function readRendererState(electronApp: ElectronApplication): Promise<RendererState | null> {
  const deadline = Date.now() + RELOAD_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const snapshot = await readSnapshotOnce(electronApp);
    if (snapshot) return snapshot;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  console.log('[diagnostic] readRendererState 超时：渲染层尚未就绪或 webContents 仍处于崩溃态');
  return null;
}

/**
 * 轮询等 `noticeCount` 达到期望值，返回命中的快照。
 *
 * 恢复提示是挂载后**异步**拉取并插入的，而且首次插入可能被紧随其后的会话历史
 * 加载整个替换掉、再由 ChatConsole 的 ensure effect 补插一次——中间状态里
 * noticeCount 就是 0。所以只能等它出现，不能拿"第一个就绪快照"直接断言（那
 * 会假红：本 issue 的原始缺陷正是"插了又被冲掉"，而修复后的补插也在亚秒级）。
 * 超时返回最后一次读到的快照（可能是 0），交给调用方的断言给出真实数字。
 */
async function waitForNoticeCount(
  electronApp: ElectronApplication,
  expected: number,
  timeoutMs: number = NOTICE_TIMEOUT_MS
): Promise<RendererState | null> {
  const deadline = Date.now() + timeoutMs;
  let last: RendererState | null = null;
  while (Date.now() < deadline) {
    const snapshot = await readSnapshotOnce(electronApp);
    if (snapshot) {
      last = snapshot;
      if (snapshot.noticeCount === expected) return snapshot;
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
  console.log(
    `[diagnostic] waitForNoticeCount(${expected}) 超时：最后读到的 noticeCount=${last?.noticeCount ?? 'null'}`
  );
  return last;
}

interface StreamingMock {
  url: string;
  /** 长流专用计数：`longDeltas` 说明流真的开始了，`longFinished` 说明它收尾了。 */
  stats: () => { requests: number; longDeltas: number; longFinished: number };
  close: () => Promise<void>;
}

/**
 * 内联的 OpenAI 兼容流式 mock（同 issue-1019-frame-send-guard.spec.ts）。
 *
 * 隔离用例需要"崩溃瞬间会话 A 上确实有一个在飞的 turn"——`chat.send` 一受理主
 * 进程就 `markTurnStarted`，所以只要 mock 还在慢慢吐 delta，在飞登记表里就有
 * A。用真 provider 会让这条用例依赖网络和 key。
 *
 * 长短按**请求体里的标记**分流，而不是按第几个请求：标题生成之类的旁路请求也
 * 会打到这个 mock，按计数分流会把它们算进去。
 *
 * 注意 delta 走的是 `reasoning_content`：渲染层对 reasoning delta 是逐条上屏
 * 的（content delta 不逐条上屏），所以"等屏幕上有字"才能和流式进度对上。
 */
async function startStreamingMock(): Promise<StreamingMock> {
  let requests = 0;
  let longDeltas = 0;
  let longFinished = 0;

  const server = http.createServer((req, res) => {
    if (req.method === 'GET' && req.url?.startsWith('/stats')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ requests, longDeltas, longFinished }));
      return;
    }
    if (req.method !== 'POST' || !req.url?.includes('/chat/completions')) {
      res.writeHead(404).end();
      return;
    }
    requests += 1;

    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const bodyText = Buffer.concat(chunks).toString('utf8');
      const isLong = Buffer.concat(chunks).toString('utf8').includes(LONG_STREAM_MARKER);
      const total = isLong ? LONG_DELTA_COUNT : SHORT_DELTA_COUNT;

      res.writeHead(200, {
        'content-type': 'text/event-stream',
        'cache-control': 'no-cache',
        connection: 'keep-alive',
      });

      const writeChunk = (delta: Record<string, unknown>, finish: string | null) => {
        res.write(
          `data: ${JSON.stringify({
            id: 'mock',
            object: 'chat.completion.chunk',
            choices: [{ index: 0, delta, finish_reason: finish }],
          })}\n\n`
        );
      };
      // 开场 role 块 + 结尾 content/finish 块缺一不可（对齐 issue-1019 的 mock）：
      // 只有 reasoning delta + [DONE] 时回合不会落到终态，UI 停在"任务进行中"，
      // 后续 send 会被 busy 守卫拒绝（本轮首次真机跑就栽在这）。
      writeChunk({ role: 'assistant' }, null);

      let sent = 0;
      const timer = setInterval(() => {
        if (sent >= total) {
          clearInterval(timer);
          if (isLong) longFinished += 1;
          writeChunk({ content: 'mock done' }, null);
          writeChunk({}, 'stop');
          res.write('data: [DONE]\n\n');
          res.end();
          return;
        }
        sent += 1;
        if (isLong) longDeltas += 1;
        writeChunk({ reasoning_content: `d${sent} ` }, null);
      }, DELTA_INTERVAL_MS);

      // 用 res 的 close：客户端断开时它一定会触发（req 的 close 在本例里不保证）。
      res.on('close', () => clearInterval(timer));
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;

  return {
    url: `http://127.0.0.1:${port}/v1`,
    stats: () => ({ requests, longDeltas, longFinished }),
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
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
    expect(patched, 'dialog.showMessageBox 桩未能装上').toBe(true);
  });

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* 渲染进程是被故意打掉的，收尾可能嘈杂 */
    }
  });

  test('崩溃后自动重载并插入恢复提示（期望行为 1~4）', async () => {
    // 崩溃前先确认界面已就绪：ChatConsole 已挂载，挂载时的 notice 拉取才会跑。
    await waitForInputReady(page);

    await crashRenderer(electronApp);

    // 期望行为 1 + 2：自动重载 + 可检索日志行
    const line = await waitForReloadLine(electronApp, 1);
    expect(line).toContain('[main] renderer-reloaded: attempt=1 reason=');

    // 期望行为 4：原生对话框确实弹了（桩返回永不 resolve，说明重载没等它）
    const calls = await electronApp.evaluate(() => (globalThis as any).__msgBoxCalls);
    expect(calls, '预算内路径应恰好弹一次告知框').toHaveLength(1);
    expect(calls[0].title).toBe('界面已崩溃');
    expect(calls[0].buttons).toEqual(['重新加载']);
    // 不带父窗 ⇒ 非窗口模态，不会禁用刚刚重载完成的界面
    expect(calls[0].argCount, '预算内告知框不应带父窗').toBe(1);

    // 恢复提示：轮询等它出现（不是取第一个就绪快照），再静置复查一次。
    const state = await waitForNoticeCount(electronApp, 1);
    expect(state, '重载后应能读到渲染层 DOM（等提示超时）').not.toBeNull();
    expect(state!.inputReady, '重载后聊天界面应恢复可用').toBe(true);
    expect(state!.noticeCount, '一次页面加载只插一条恢复提示').toBe(1);
    expect(state!.noticeText?.trim()).toContain(NOTICE_TEXT);

    // 静置复查：提示出现过不等于留得住——会话历史加载若把它冲掉且没补插，
    // 这里会读到 0，避免"闪现一下"被当成通过。
    await new Promise((r) => setTimeout(r, NOTICE_SETTLE_MS));
    const settled = await readRendererState(electronApp);
    expect(settled, '静置复查应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.noticeCount, `静置 ${NOTICE_SETTLE_MS}ms 后恢复提示仍应恰好一条`).toBe(1);
    expect(settled!.noticeText?.trim()).toContain(NOTICE_TEXT);
  });

  test('预算内再次崩溃：自动重载继续，attempt 递增', async () => {
    await crashRenderer(electronApp);

    const line = await waitForReloadLine(electronApp, 2);
    expect(line).toContain('reason=');

    // 同样轮询等提示出现 + 静置复查（第二次重载走的是和第一次完全一样的时序）。
    const state = await waitForNoticeCount(electronApp, 1);
    expect(state, '第二次重载后应能读到渲染层 DOM（等提示超时）').not.toBeNull();
    expect(state!.inputReady).toBe(true);
    // 上一条提示没被持久化（sessionMsgsToUi 不认 system 角色），重载后按新
    // notice 重新插一条，不会累积成两条。
    expect(state!.noticeCount).toBe(1);

    await new Promise((r) => setTimeout(r, NOTICE_SETTLE_MS));
    const settled = await readRendererState(electronApp);
    expect(settled, '第二次重载的静置复查应能读到渲染层 DOM').not.toBeNull();
    expect(settled!.noticeCount, `静置 ${NOTICE_SETTLE_MS}ms 后恢复提示仍应恰好一条`).toBe(1);
  });
});

/**
 * 外部评审 P1：恢复提示必须绑定「崩溃时还有在飞 turn 的会话」。
 *
 * 独立 describe + 独立 app 实例：上面两条用例已经把渲染进程打崩两次，而
 * Playwright 的 `page` 句柄在 renderer 崩溃/重载后就失效（之后的 DOM 读取只能
 * 走主进程的 executeJavaScript，见 evalInRenderer）。本条用例崩溃前要用 `page`
 * 发消息，所以必须有一个没崩过的渲染进程；顺带也拿到独立的崩溃预算。
 */
test.describe('Issue #1035 — 恢复提示的会话归属（外部评审 P1）', () => {
  test.describe.configure({ mode: 'serial', timeout: 240_000 });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string | undefined;
  let mock: StreamingMock;

  test.beforeAll(async () => {
    mock = await startStreamingMock();
    const fixture = await launchElectronApp((config: any) => {
      // 把所有 provider 指到内联 mock：turn 要真的发得出去，且速度可控。
      // （同 issue-1019——provider 解析看配置的 model，只改一个会漏出真实请求。）
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
    expect(patched, 'dialog.showMessageBox 桩未能装上').toBe(true);

    // 源码模式（uv run python）下桥冷启动后，客户端发往桥的**第一条 thread/start
    // 请求会丢**（实测：主进程已发出、桥侧从未收到；渲染层 30s 救生圈到点才放行
    // chat.send，把首条消息拖慢半分钟并把各步等待拖爆）。这里先打一发热身请求把
    // 这次丢弃吸收掉——请求即使丢失也不产生回合效果，之后的真实发送一路畅通。
    await page.evaluate(() => {
      void (window as any).miqi?.threads?.start?.({ title: 'e2e-warmup' })?.catch?.(() => {});
    });
    await page.waitForTimeout(1_200);
  });

  test.afterAll(async () => {
    try {
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* 渲染进程是被故意打掉的，收尾可能嘈杂 */
    }
    await mock.close();
  });

  test('恢复提示只落在崩溃时在飞的会话：切到别的会话不串台', async () => {
    await waitForInputReady(page);

    // ── (a) 让 A、B 都落盘，再在 A 上起一个真在飞的 turn ───────────────
    // 会话 key 由渲染层自己生成（`desktop:<ts>`，见 ChatConsole.createSession），
    // 没有"新建会话"的 IPC，只能从 localStorage 读。
    const keyA = await readActiveSessionKey(electronApp);
    expect(keyA, '应能从 localStorage 读到当前会话 key').toBeTruthy();

    // A 先攒一条消息：①「+」只在当前会话非空时才新建（空会话会被复用）；
    // ②侧栏渲染的就是 sessions.list()，没落盘的会话崩溃后在侧栏里没有卡片，
    // 而重载之后只能靠点侧栏切会话。短流（5 × 50ms）保证这个 turn 很快落定，
    // 不会混进崩溃时刻的在飞登记表。
    await sendMessage(page, `${SHORT_MARKER} A`);
    await waitForSessionOnDisk(page, keyA, 30_000);

    // 点「+」新建并切到 B，同样喂一条短消息让它进侧栏。
    await page.click('[data-testid="nav-new-session"]');
    const keyB = await waitForNewActiveSession(electronApp, keyA);
    expect(keyB).not.toBe(keyA);
    await sendMessage(page, `${SHORT_MARKER} B`);
    await waitForSessionOnDisk(page, keyB);

    // 切回 A，并在 A 上发长流：崩溃瞬间在飞的只有 A。
    await switchToSessionByKeyOnPage(page, keyA);
    expect(await readActiveSessionKey(electronApp), '崩溃前当前会话应是 A').toBe(keyA);
    // 等 A 的历史加载/快照合并稳定后再发长流（见 waitForStableUserBubbleCount 注释）。
    await waitForStableUserBubbleCount(page);

    // 归属来自主进程的在飞登记表，而登记表是 `chat.send` 受理时写下的——所以
    // 必须真的发一条消息，"崩溃时 A 上有 turn"才是事实而不是假设。长流
    // 900 × 50ms = 45s，远超后面的崩溃 + 切会话 + 断言。
    await sendMessage(page, `${LONG_STREAM_MARKER} 请慢慢回答`);

    // 同步点：轮询 mock，等流真的开始且肯定还没结束。刻意不"等屏幕上有字"——
    // 进度只有 mock 自己知道，等渲染结果会等到整条流结束，那时就没有在飞 turn
    // 可崩了（同 issue-1019 的同步点）。
    const streamDeadline = Date.now() + 30_000;
    let streamStats = mock.stats();
    while (
      Date.now() < streamDeadline &&
      !(streamStats.longDeltas >= 20 && streamStats.longFinished === 0)
    ) {
      await page.waitForTimeout(POLL_INTERVAL_MS);
      streamStats = mock.stats();
    }
    expect(
      streamStats.longDeltas,
      'mock 应已吐出 ≥20 个长流 delta（A 的 turn 正在飞）'
    ).toBeGreaterThanOrEqual(20);
    expect(streamStats.longFinished, '崩溃前长流不应结束，否则 A 上没有在飞 turn').toBe(0);

    // ── (b) 崩溃 + 重载：A（当前会话）里恰好一条提示 ───────────────────
    const linesBefore = await electronApp.evaluate(
      () => ((globalThis as any).__reloadLines ?? []).length
    );
    await crashRenderer(electronApp);
    const line = await waitForNewReloadLine(electronApp, linesBefore);
    expect(line).toContain('renderer-reloaded: attempt=');

    const stateA = await waitForNoticeCount(electronApp, 1);
    expect(stateA, '重载后应能读到渲染层 DOM（等提示超时）').not.toBeNull();
    expect(stateA!.noticeCount, '崩溃时在飞的会话 A 应恰好一条恢复提示').toBe(1);
    expect(stateA!.noticeText?.trim()).toContain(NOTICE_TEXT);
    expect(await readActiveSessionKey(electronApp), '重载后应回到崩溃时的会话 A').toBe(keyA);
    const noticeSeenAt = Date.now();

    // ── (c) 切到 B：一条都不该有 ─────────────────────────────────────
    await switchToSessionByKey(electronApp, keyB);
    // 切会话会触发历史加载 + ensure effect，负向断言必须等这些跑完再读：
    // "没等到"和"确实没有"是两回事。
    await new Promise((r) => setTimeout(r, NOTICE_SETTLE_MS));
    const stateB = await readSnapshotOnce(electronApp);
    expect(stateB, '切到 B 后应能读到渲染层 DOM').not.toBeNull();
    expect(
      stateB!.noticeCount,
      `B 不是崩溃时在飞的会话，不应出现恢复提示（实际文案：${stateB!.noticeText}）`
    ).toBe(0);
    expect(stateB!.noticeText).toBeNull();

    // 再静置一轮：插入是被异步拉取驱动的，第一次读到 0 也可能是"还没轮到"。
    await new Promise((r) => setTimeout(r, NOTICE_SETTLE_MS));
    const settledB = await readSnapshotOnce(electronApp);
    expect(settledB, '静置复查 B 时应能读到渲染层 DOM').not.toBeNull();
    expect(settledB!.noticeCount, `在 B 里静置 ${NOTICE_SETTLE_MS}ms 后仍应为 0 条`).toBe(0);
    expect(settledB!.inputReady, 'B 里界面应保持可用').toBe(true);

    // 非空洞检查：以上观察必须落在补插窗口（60s）内，否则"B 里 0 条"可能只是
    // 窗口过期导致的，什么都没证明。
    const elapsedMs = Date.now() - noticeSeenAt;
    expect(
      elapsedMs,
      `B 的负向断言必须在补插窗口（60s）内读到，实际距提示出现 ${elapsedMs}ms`
    ).toBeLessThan(ENSURE_WINDOW_GUARD_MS);

    // ── (d) 切回 A：还是恰好一条 ─────────────────────────────────────
    // 切走时 A 的消息被会话历史加载整个替换过，切回来必须仍然有一条：既证明
    // 提示没有因为"切到 B 被消费掉"而丢失，也证明来回切换没有把它变成两条。
    await switchToSessionByKey(electronApp, keyA);
    const backA = await waitForNoticeCount(electronApp, 1, 15_000);
    expect(backA, '切回 A 后应能读到渲染层 DOM').not.toBeNull();
    expect(
      backA!.noticeCount,
      `切回崩溃会话 A 后仍应恰好一条提示（实际文案：${backA!.noticeText}）`
    ).toBe(1);

    await new Promise((r) => setTimeout(r, NOTICE_SETTLE_MS));
    const settledA = await readSnapshotOnce(electronApp);
    expect(settledA!.noticeCount, `在 A 里静置 ${NOTICE_SETTLE_MS}ms 后仍应恰好一条`).toBe(1);
  });
});
