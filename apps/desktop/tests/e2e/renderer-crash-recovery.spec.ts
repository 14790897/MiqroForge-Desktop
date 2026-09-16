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
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "1035"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp, waitForInputReady } from './helpers/electron-setup';

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

/** 真打掉渲染进程（与 OOM 走同一条 render-process-gone）。 */
async function crashRenderer(electronApp: ElectronApplication): Promise<void> {
  const crashed = await electronApp.evaluate(({ BrowserWindow }) => {
    const win = BrowserWindow.getAllWindows()[0];
    if (!win) return false;
    win.webContents.forcefullyCrashRenderer();
    return true;
  });
  expect(crashed, '应能拿到主窗口并打掉其渲染进程').toBe(true);
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
  throw new Error(
    `未等到 renderer-reloaded attempt=${attempt}（对话框桩永不 resolve，` +
      `若实现 await 了它就永远等不到）；已捕获：${JSON.stringify(seen)}`
  );
}

interface RendererState {
  noticeCount: number;
  noticeText: string | null;
  inputReady: boolean;
}

/**
 * 一次性读渲染层 DOM（渲染层尚未就绪 / webContents 仍处于崩溃态时返回 null）。
 *
 * 刻意用主进程的 `webContents.executeJavaScript`，不用 Playwright 的 page
 * 句柄：渲染进程是被真打掉再重载的，page 句柄能否跨这次 renderer 更换继续
 * 可用不是本 issue 要验证的东西，用它会把测试的成败绑到无关的实现细节上。
 * executeJavaScript 直接跑在重载后的渲染进程里，只依赖"窗口确实活着"。
 */
async function readSnapshotOnce(electronApp: ElectronApplication): Promise<RendererState | null> {
  return electronApp.evaluate(
    async ({ BrowserWindow }, selectors) => {
      const wc = BrowserWindow.getAllWindows()[0]?.webContents;
      if (!wc || wc.isDestroyed() || wc.isCrashed()) return null;
      try {
        return await wc.executeJavaScript(`(() => {
          const notices = document.querySelectorAll(${JSON.stringify(selectors.notice)});
          return {
            noticeCount: notices.length,
            noticeText: notices.length ? notices[0].textContent : null,
            inputReady: !!document.querySelector(${JSON.stringify(selectors.input)}),
          };
        })()`);
      } catch {
        // 重载途中 document 还没就绪
        return null;
      }
    },
    { notice: NOTICE_SELECTOR, input: INPUT_SELECTOR }
  );
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
