/**
 * Issue #1019 — render frame 已销毁后不再逐条 webContents.send。
 *
 * 复现方式：内联一个流式 mock LLM（不依赖真实模型、不影响 scripts/mock_openai.py），
 * 让一次对话产生**持续数十秒的 chat:progress 事件流**；流到一半把渲染进程打掉，
 * 之后的事件就会落在「WebContents 还活着、render frame 已销毁」这个组合上 ——
 * 也就是 #1019 的全部成因。修复前每一条都会在 Electron 内部失败并打一行
 * 'Render frame was disposed before WebFrameMain could be accessed'，修复后应为 0 条。
 *
 * 为了让这个断言非空洞，测试还会向 mock 查一次 /stats：崩溃**之后** mock 必须确实
 * 又发出了若干条 delta（否则「0 条报错」可能只是因为崩溃后根本没有事件）。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "1019"
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import http from 'node:http';
import {
  launchElectronApp,
  closeElectronApp,
  waitForInputReady,
  sendMessage,
} from './helpers/electron-setup';

/** Marker the mock streams first, so the test can tell streaming has begun. */
const STREAM_MARKER = 'FRAMEGUARD';

// Stream long enough that the crash always lands mid-stream, and leave enough
// deltas after the crash for the forwarding path to be exercised.
const DELTA_COUNT = 900;
const DELTA_INTERVAL_MS = 50;

interface MockStream {
  url: string;
  stats: () => { started: number; finished: number; deltas: number };
  close: () => Promise<void>;
}

/**
 * Minimal OpenAI-compatible SSE server. Streams many small content deltas so
 * the app emits one chat:progress per delta. `/stats` reports how many streams
 * have started and finished, which is what makes the post-crash assertion
 * meaningful.
 */
async function startStreamingMock(): Promise<MockStream> {
  let started = 0;
  let finished = 0;
  let deltas = 0;

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

    // Drain the request body, then stream.
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
          id: 'chatcmpl-frameguard',
          object: 'chat.completion.chunk',
          created: 0,
          model: 'frameguard-mock',
          choices: [{ index: 0, delta, finish_reason: finish }],
        }) +
        '\n\n';

      res.write(chunk({ role: 'assistant' }, null));

      let i = 0;
      const timer = setInterval(() => {
        i += 1;
        if (i > DELTA_COUNT) {
          clearInterval(timer);
          res.write(chunk({ content: STREAM_MARKER + ' done' }, null));
          res.write(chunk({}, 'stop'));
          res.write('data: [DONE]\n\n');
          finished += 1;
          res.end();
          return;
        }
        // reasoning_content, NOT content: content deltas are deliberately not
        // forwarded one-for-one (bridge/loop.py skips AgentMessageDeltaEvent),
        // so they cannot exercise the per-event forwarding that #1019 is about.
        // Reasoning deltas ARE forwarded per delta — that is the path that
        // produced 15,413 error lines.
        res.write(chunk({ reasoning_content: 'think' + i + ' ' }, null));
        deltas += 1;
      }, DELTA_INTERVAL_MS);

      // Stop the timer when the *response* connection goes away — NOT on
      // `req`'s 'close'. In Node, a server request emits 'close' as soon as its
      // body has been read, which would clear the timer at i=0 and cut the
      // stream after a single chunk (the app then hits its 30s LLM stream idle
      // timeout and the turn fails with nothing rendered).
      res.on('close', () => clearInterval(timer));
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const addr = server.address();
  const port = typeof addr === 'object' && addr ? addr.port : 0;
  return {
    url: `http://127.0.0.1:${port}/v1`,
    stats: () => ({ started, finished, deltas }),
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}

test.describe('Issue #1019 — no per-event send to a disposed render frame', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let mock: MockStream;
  let miqiHome: string | undefined;

  test.beforeAll(async () => {
    mock = await startStreamingMock();
    const fixture = await launchElectronApp((config: any) => {
      // Point every configured provider at the mock (same strategy as
      // confirm-card.spec.ts — provider resolution depends on the configured
      // model, so patching only one would leak a real API call).
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

    // (#1035 适配) 崩溃现在会触发主进程自动重载，本 spec 需要一个"真死帧"窗口：
    // 1) 预热一发 thread/start——源码模式桥冷启动后首发该请求会丢（实测），
    //    不预热会把首条消息拖 30 秒、把下面的流同步拖爆；
    // 2) 把原生对话框桩成永不 resolve——被测崩溃会先把 10 分钟/3 次的自动
    //    重载预算花光，随后走「跳过」路径 await 这个对话框；桩悬停即处理器
    //    悬停：帧保持真死（不重载、不退出），断言前提得以保留。
    //    预算预支放在用例里、UI 操作之后（见用例内注释）。
    await page.evaluate(() => {
      void (window as any).miqi?.threads?.start?.({ title: 'e2e-warmup' })?.catch?.(() => {});
    });
    await electronApp.evaluate(() => {
      const g = globalThis as any;
      g.__ELECTRON__.dialog.showMessageBox = () => new Promise(() => {});
    });
    await page.waitForTimeout(1_200);
  }, 180_000);

  test.afterAll(async () => {
    try {
      // Pass the temp home so closeElectronApp removes it — without the second
      // argument the directory is left behind on every run.
      await closeElectronApp(electronApp, miqiHome);
    } catch {
      /* renderer was crashed on purpose; teardown may be noisy */
    }
    await mock?.close();
  });

  test(
    'crashing the renderer mid-stream produces zero "Render frame was disposed" lines',
    { timeout: 420_000 },
    async () => {
      // Count Electron's internal send-failure lines in the main process.
      // Electron emits these as console.error('Error sending from webFrameMain: ', err)
      // — the human-readable text is in a LATER argument, not args[0], so join
      // every argument before matching (matching only args[0] silently counts
      // nothing and makes this whole spec vacuous).
      await electronApp.evaluate(() => {
        const g = globalThis as any;
        g.__disposedLines = 0;
        const orig = console.error;
        console.error = (...a: unknown[]) => {
          const text = a.map((x) => (x instanceof Error ? x.message : String(x))).join(' ');
          if (text.includes('Render frame was disposed')) g.__disposedLines += 1;
          orig(...(a as []));
        };
      });

      // Count invocations of the guard's own liveness check. sendToFrame calls
      // isFrameAlive -> contents.mainFrame.isDestroyed(), so a rise in this
      // counter after the crash is direct proof that post-crash progress events
      // reached the guarded send path. The mock's delta count alone only shows
      // the mock kept writing — if desktop forwarding stopped before the guard,
      // deltas would still climb and disposedLines would still read 0.
      await electronApp.evaluate(({ BrowserWindow }) => {
        const g = globalThis as any;
        g.__frameChecks = 0;
        const wc = BrowserWindow.getAllWindows()[0]?.webContents;
        if (!wc) return;
        // WebFrameMain is not exported from the electron module, so reach its
        // prototype through a live instance; the prototype is shared by every
        // frame, so patching it here covers the frames used later.
        const proto = Object.getPrototypeOf(wc.mainFrame);
        const orig = proto.isDestroyed;
        proto.isDestroyed = function (this: unknown) {
          g.__frameChecks += 1;
          return orig.call(this);
        };
      });

      await createFreshSession(page);
      await sendMessage(page, 'stream please');

      // Sync point: poll the mock until the stream is genuinely underway and
      // still unfinished. Do NOT wait on rendered text — content deltas are
      // not painted progressively, so the first paint lands only after the
      // whole stream is done (i.e. too late to crash mid-stream). Polling the
      // mock is both the real "mid-stream" signal and the non-vacuity check.
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        const s = mock.stats();
        if (s.deltas >= 100 && s.finished === 0) break;
        await page.waitForTimeout(250);
      }
      const beforeCrash = mock.stats();
      expect(beforeCrash.deltas).toBeGreaterThanOrEqual(100);
      expect(beforeCrash.finished).toBe(0);

      // Evidence: the app mid-stream, an instant before the renderer is killed.
      // (The usual end-of-test screenshot is impossible here — the page is
      // deliberately crashed — so capture the state we are interrupting.)
      await page
        .screenshot({ path: 'test-results/issue-1019-mid-stream.png', timeout: 10_000 })
        .catch(() => {});

      // (#1035 适配) 预算预支：此刻 UI 操作已全部完成（往下只剩主进程求值与
      // mock 统计，不再需要 page），连崩 3 次把 10 分钟/3 次的自动重载预算花光。
      // 否则下面的被测崩溃会被自动重载在毫秒级把死帧换成活帧，「向死帧持续
      // 发送 ≥50 次守卫检查」的前提就不存在了。放在这里是因为：每次崩溃都会
      // 更换渲染进程、令 Playwright 的 page 句柄永久作废（连 firstWindow()
      // 也救不回），而上面的建会话/发消息/同步/截图都还要用 page。
      for (let i = 0; i < 3; i += 1) {
        await electronApp.evaluate(({ BrowserWindow }) => {
          BrowserWindow.getAllWindows()[0]?.webContents.forcefullyCrashRenderer();
        });
        await new Promise((r) => setTimeout(r, 3_000)); // 等这次自动重载跑完
      }

      // Kill the renderer the way an OOM does: process gone, frame disposed,
      // WebContents object still alive (which is exactly why the old
      // `!wc.isDestroyed()` guard let every event through).
      const state = await electronApp.evaluate(({ BrowserWindow }) => {
        const win = BrowserWindow.getAllWindows()[0];
        if (!win) return 'no-window';
        win.webContents.forcefullyCrashRenderer();
        return 'crashed';
      });
      expect(state).toBe('crashed');
      // Only guard invocations from here on count as "post-crash".
      const checksAtCrash = await electronApp.evaluate(() => (globalThis as any).__frameChecks);

      // Let the still-running turn keep emitting progress events at the dead frame.
      await new Promise((r) => setTimeout(r, 15_000));

      const disposedLines = await electronApp.evaluate(() => (globalThis as any).__disposedLines);
      const afterChecks = await electronApp.evaluate(() => (globalThis as any).__frameChecks);
      const afterWait = mock.stats();

      // Non-vacuity: the mock kept emitting reasoning deltas after the crash, so
      // the app really was forwarding events to the (now frame-less) WebContents
      // during the window below. Without this, "0 error lines" could just mean
      // nothing happened. Measured against the old frame-blind guard this same
      // window produced ~244 Electron error lines, so the floor below is well
      // clear of noise.
      expect(afterWait.deltas - beforeCrash.deltas).toBeGreaterThanOrEqual(50);
      expect(afterWait.finished).toBe(beforeCrash.finished);

      // ...and that those events actually reached the guarded send path: the
      // frame-liveness check itself ran, on a disposed frame, ≥50 times.
      expect(afterChecks - checksAtCrash).toBeGreaterThanOrEqual(50);

      // The assertion this whole spec exists for.
      expect(disposedLines).toBe(0);
    }
  );
});

/** Start from an empty conversation so the mock's stream is the only content. */
async function createFreshSession(page: Page): Promise<void> {
  await page.evaluate(() => {
    document.querySelectorAll('[data-radix-focus-guard]').forEach((e) => e.remove());
  });
  const newBtn = page.getByRole('button', { name: /新建|新任务|New/i }).first();
  if (await newBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await newBtn.click();
    await page.waitForTimeout(500);
  }
}
