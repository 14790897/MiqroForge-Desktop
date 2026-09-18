/**
 * #1034 渲染进程内存压测探针（**测量用**，不是回归断言）。
 *
 * 目的：按 issue #1034「复现步骤 + 不依赖真实模型的注入范式」，向真实渲染进程
 * 注入 `{stream:'reasoning', delta:'x', session_key}` 的 chat:progress 事件，
 * 固定口径 200 msg/s × 200,000 条（≈16.7 分钟），同时采集渲染进程内存曲线。
 *
 * 采集口径（与 PR body 里的 BEFORE 基线同源，可直接对比）：
 *   - renderer workingSetSize（KB）：`app.getAppMetrics()` 里主窗口 webContents
 *     的 OS 进程条目——**只用 workingSetSize**，不用 peakWorkingSetSize；按
 *     pid 选中而不是按 `type === 'Tab'`（启动期 splash 也是 BrowserWindow）；
 *   - JS heapUsed / heapTotal / heapLimit（`performance.memory`，沙箱里取不到记 -1）；
 *   - DOM 节点数 + body 文本里的 'x' 计数（= 真正落到 UI 的推理字符数）；
 *   - 注入量、注入线程耗时、renderer-crash（render-process-gone）、wall time。
 *
 * 采样窗口：注入期每 5s 一条；**注入停止后再采 60s**（尾窗，观察 working set 是否
 * 回落）。采样由独立的 `samplingDone` 控制，注入结束（`done`）不跟着停采样——否则
 * 尾窗一个采样点都没有，配套脚本里的「末尾含回落观察」就成了假口径。
 *
 * 注入范式取自 tool-error-neutral.spec.ts Test B：
 *   1. 用 scripts/mock_hang.py 起一个永不响应的 provider mock；
 *   2. 发一条真实消息，前端只在回合存活期间注册 chat:progress 监听；
 *   3. 从 sessions.list 解析 session_key（= 渲染层 routingKey）；
 *   4. 用 webContents.send('chat:progress', …) 注入后端本该发的事件。
 *
 * 硬前置断言（issue 要求）：注入开始后必须先在 UI 看到思考块在**增长**，证明
 * 事件确实被消费；否则本轮测量无效，直接判失败。
 *
 * ⚠️ **默认跳过**：这是测量用长跑探针（默认口径 200k 条 ≈17 分钟），不进常规
 * e2e/CI 套件——否则 electron-e2e 会被拖过 30 分钟 job 超时、macos-e2e 也会因
 * 反复起 mock 而红。只有显式设了 `MIQI_1034_PROBE=1` 才运行；配套脚本
 * scripts/measure-reasoning-memory.mjs 会自动带上这个开关。
 *
 * 运行（推荐用配套脚本，它会带上正确参数并解析结果）：
 *   npm run build
 *   node scripts/measure-reasoning-memory.mjs            # 见 scripts/measure-reasoning-memory.mjs
 *
 * 或直接跑本 spec（**必须显式开开关**，否则整块被 skip）：
 *   MIQI_1034_PROBE=1 npx playwright test --config=playwright.config.ts --project=electron \
 *     --workers=1 -g "issue1034 probe"
 *
 * 可调环境变量：MIQI_1034_PROBE（=1 才真跑）/ MIQI_1034_TARGET / _RATE / _TICK_MS /
 * _MAX_BURST / _PRECONDITION_MS / _OUT
 * （_OUT 是输出目录，JSONL 与摘要都写在那里；默认 apps/desktop/test-reports/issue1034）
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { appendFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  sendMessage,
  waitForBridgeInitialized,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** 探针开关：默认关闭。常规 e2e / CI 里这块整体跳过，只有显式 =1 才跑（见文件头）。 */
const PROBE_ENABLED = process.env['MIQI_1034_PROBE'] === '1';

const TARGET = Number(process.env['MIQI_1034_TARGET'] ?? 200_000);
const RATE = Number(process.env['MIQI_1034_RATE'] ?? 200);
const TICK_MS = Number(process.env['MIQI_1034_TICK_MS'] ?? 50);
const MAX_BURST = Number(process.env['MIQI_1034_MAX_BURST'] ?? 400);
const PRECONDITION_MS = Number(process.env['MIQI_1034_PRECONDITION_MS'] ?? 60_000);
const UI_PROBE_MS = 5_000;
const OUT_DIR = process.env['MIQI_1034_OUT'] ?? join(APPS_DESKTOP, 'test-reports', 'issue1034');

const RUN_ID = new Date().toISOString().replace(/[:.]/g, '-');
const PROBE_JSONL = join(OUT_DIR, `issue1034_probe_${RUN_ID}.jsonl`);

// 17 分钟长跑：关掉录屏/截图/trace —— 附属产物既拖慢采样又占满磁盘。
test.use({ video: 'off', screenshot: 'off', trace: 'off' });

/** 起一个 mock provider（scripts/ 下的脚本），等它打出启动行。 */
async function startMockServer(script: string): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env['MIQI_PYTHON_PATH'] || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', script), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
  });

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock ${script} exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock ${script} startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[probe1034] mock ${script} ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl };
}

/** 把所有 provider 指向 mock，并把默认模型钉到 deepseek —— 真实 API 永不被调用。 */
function patchProvidersToMock(config: any, mockUrl: string): void {
  const providers = config.providers ?? {};
  for (const [, p] of Object.entries(providers)) {
    if (p && typeof p === 'object') {
      (p as any).apiBase = mockUrl;
      if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
    }
  }
  config.agents = config.agents ?? {};
  config.agents.defaults = config.agents.defaults ?? {};
  config.agents.defaults.model = 'deepseek/deepseek-chat';
  const deepseek = (providers as any).deepseek ?? {};
  deepseek.apiBase = mockUrl;
  deepseek.apiKey = `${deepseek.apiKey ?? 'sk-mock-key'}`;
  (providers as any).deepseek = deepseek;
  config.providers = providers;
}

/** 解析刚落盘会话的 session_key（= 渲染层 routingKey，注入事件按它过滤）。 */
async function resolveActiveSessionKey(page: Page): Promise<string> {
  let key = '';
  for (let attempt = 0; attempt < 30 && !key; attempt += 1) {
    key = await page.evaluate(async () => {
      const list = await (window as any).miqi.sessions.list();
      const sessions = (list?.sessions ?? []) as Array<{ key: string; created_at?: string }>;
      sessions.sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
      return sessions[sessions.length - 1]?.key ?? '';
    });
    if (!key) await page.waitForTimeout(1000);
  }
  expect(
    key,
    'session key must resolve (empty session persists only after the first message)'
  ).not.toBe('');
  return key;
}

/** 渲染进程侧的 DOM 探针：数 'x' 字符 = 真正被消费并渲染出来的推理字节数。 */
const DOM_PROBE = `(() => {
  const m = performance.memory || {};
  const body = document.body ? (document.body.textContent || '') : '';
  let x = 0;
  for (let i = 0; i < body.length; i++) if (body.charCodeAt(i) === 120) x++;
  return {
    xCount: x,
    bodyLen: body.length,
    domNodes: document.getElementsByTagName('*').length,
    heapUsed: m.usedJSHeapSize || -1,
    heapTotal: m.totalJSHeapSize || -1,
    heapLimit: m.jsHeapSizeLimit || -1,
  };
})()`;

interface UiSample {
  atIso: string;
  elapsedMs: number;
  sent: number;
  xCount: number;
  bodyLen: number;
  domNodes: number;
  /** 渲染进程 workingSetSize（KB），与 PR body 的 BEFORE 基线同一口径。 */
  wsKb: number;
  heapUsed: number;
  err?: string;
}

test.describe('#1034 renderer memory probe (measurement only)', () => {
  // describe 级 skip：默认整块跳过（**含 beforeAll**——不起 mock、不 launch Electron、
  // 不注入 200k）。放在 describe 体里而不是测试体里，才能连 hook 一起挡住。
  test.skip(!PROBE_ENABLED, '测量用长跑探针默认跳过：设 MIQI_1034_PROBE=1 才运行');

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockServer('mock_hang.py');
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) => {
      patchProvidersToMock(config, mock.mockUrl);
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
    console.log('[probe1034] bridge initialized');
  });

  test.afterAll(async () => {
    try {
      await electronApp?.evaluate(() => {
        const s = (globalThis as any).__miqi1034;
        if (s) {
          s.done = true;
          s.samplingDone = true;
        }
      });
    } catch {
      /* renderer/main may already be gone */
    }
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test('issue1034 probe: 200 msg/s × 200k reasoning deltas → renderer working-set curve', async () => {
    // project 级 timeout(600s) 会先斩断长跑 —— 实测第一轮在 600s 被截（116k/200k）。
    // 运行时 setTimeout 会重算当前 slot 的 deadline，40 分钟足够 200k@200/s 跑完
    // 并留出收尾观察窗口。
    test.setTimeout(2_400_000);
    mkdirSync(OUT_DIR, { recursive: true });
    console.log(
      `[probe1034] target=${TARGET} rate=${RATE}/s tick=${TICK_MS}ms maxBurst=${MAX_BURST} ` +
        `jsonl=${PROBE_JSONL}`
    );

    await createNewConversation(page);
    // 挂起的 mock 让回合一直存活 —— 前端只在回合进行中注册 chat 监听。
    await sendMessage(page, `#1034 内存探针 ${Date.now()}`);
    await page.waitForTimeout(3000);
    const sessionKey = await resolveActiveSessionKey(page);
    console.log(`[probe1034] session key = ${sessionKey}`);

    // 注入前的基线（'x' 计数基线，UI 里本来就可能有零星 x）。
    const baseline = (await page.evaluate(DOM_PROBE)) as Record<string, number>;
    console.log(`[probe1034] DOM baseline = ${JSON.stringify(baseline)}`);

    // ── 在主进程里装注入 harness ────────────────────────────────────
    // 逐条 electronApp.evaluate（issue 里的字面写法）每发一条要一次 CDP 往返，
    // 撑不到 200/s；改成主进程内的自校正节流循环，测试侧只轮询状态。
    const startedAtIso = await electronApp.evaluate(
      ({ app, BrowserWindow }, cfg) => {
        const g = globalThis as any;
        const win = BrowserWindow.getAllWindows().find(
          (w) => w.getTitle() === 'MiQroForge Desktop'
        );
        if (!win) throw new Error('main window not found');
        const wc = win.webContents;

        const state: any = {
          sent: 0,
          target: cfg.target,
          rate: cfg.rate,
          sessionKey: cfg.sessionKey,
          startedAtMs: Date.now(),
          startedAtIso: new Date().toISOString(),
          // done = 注入结束；samplingDone = 采样结束。两者**必须分开**：注入一停就停采
          // 的话，收尾那 60s 观察窗一个采样点都没有（原来就是这么坏的）。
          done: false,
          samplingDone: false,
          gone: null,
          ui: null,
          uiHistory: [] as any[],
          ticks: 0,
          stallTicks: 0,
          sendErrors: 0,
          sendMs: 0,
          maxBurstMs: 0,
          lastBurstAtMs: 0,
        };
        g.__miqi1034 = state;

        // 崩溃时刻与当时已注入量（测试侧旁的旁证，产品代码未改动）。
        wc.on('render-process-gone', (_e: unknown, details: any) => {
          state.gone = {
            reason: details?.reason,
            exitCode: details?.exitCode,
            atIso: new Date().toISOString(),
            elapsedMs: Date.now() - state.startedAtMs,
            sent: state.sent,
            uiXCount: state.ui?.xCount ?? -1,
            wsKb: state.ui?.wsKb ?? -1,
          };
          state.done = true;
          state.samplingDone = true;
        });

        const injectTick = () => {
          if (state.done) return;
          const elapsedMs = Date.now() - state.startedAtMs;
          const due = Math.min(state.target, Math.floor((elapsedMs / 1000) * cfg.rate));
          let n = due - state.sent;
          if (n > cfg.maxBurst) n = cfg.maxBurst;
          if (n > 0) {
            const t0 = Date.now();
            // 只记真正发出去的条数：send 抛错时循环中断，整批计入 sent 会把
            // 分母抬高，斜率被稀释（analyze 用的是 sent 差值做分母）。
            let ok = 0;
            try {
              for (let i = 0; i < n; i++) {
                wc.send('chat:progress', {
                  stream: 'reasoning',
                  delta: 'x',
                  session_key: cfg.sessionKey,
                });
                ok += 1;
              }
            } catch {
              state.sendErrors += 1;
            }
            const dt = Date.now() - t0;
            state.sendMs += dt;
            state.lastBurstAtMs = Date.now();
            if (dt > state.maxBurstMs) state.maxBurstMs = dt;
            state.sent += ok;
            // sent 现在只记成功数，所以「发不出去」时它到不了 target —— 兜底：连续
            // 整批失败满 100 个 tick（默认 50ms/tick ≈ 5s）就结束注入，别无限自排期。
            if (ok === 0) {
              state.stallTicks += 1;
              if (state.stallTicks >= 100) {
                state.done = true;
                return;
              }
            } else {
              state.stallTicks = 0;
            }
          }
          state.ticks += 1;
          if (state.sent >= state.target) {
            state.done = true;
            return;
          }
          setTimeout(injectTick, cfg.tickMs);
        };

        const uiTick = async () => {
          if (state.gone) return;
          const atMs = Date.now();
          const base = {
            atIso: new Date(atMs).toISOString(),
            elapsedMs: atMs - state.startedAtMs,
            sent: state.sent,
          };
          try {
            const probe = await Promise.race([
              wc.executeJavaScript(cfg.domProbe, true),
              new Promise((resolve) => setTimeout(() => resolve({ __timeout: true }), 4_000)),
            ]);
            // workingSetSize 从主进程侧取：按主窗口 webContents 的 OS 进程 pid
            // 选中（不能只按 type === 'Tab'：启动期 splash 也是 BrowserWindow）。
            let wsKb = -1;
            try {
              const metric = app.getAppMetrics().find((m) => m.pid === wc.getOSProcessId());
              wsKb = metric?.memory?.workingSetSize ?? -1;
            } catch {
              wsKb = -1;
            }
            if ((probe as any)?.__timeout) {
              state.ui = {
                ...base,
                xCount: -1,
                bodyLen: -1,
                domNodes: -1,
                wsKb,
                err: 'probe-timeout',
              };
            } else {
              state.ui = { ...base, wsKb, ...(probe as any) };
            }
          } catch (err) {
            state.ui = {
              ...base,
              xCount: -1,
              bodyLen: -1,
              domNodes: -1,
              wsKb: -1,
              err: String(err).slice(0, 200),
            };
          }
          if (state.uiHistory.length < 2000) state.uiHistory.push(state.ui);
          // 跟着 samplingDone 排期（而不是 done）：注入停了还要继续采满观察窗。
          if (!state.samplingDone) setTimeout(uiTick, cfg.uiProbeMs);
        };

        setTimeout(injectTick, cfg.tickMs);
        setTimeout(uiTick, 1_000);
        return state.startedAtIso;
      },
      {
        target: TARGET,
        rate: RATE,
        tickMs: TICK_MS,
        maxBurst: MAX_BURST,
        sessionKey,
        domProbe: DOM_PROBE,
        uiProbeMs: UI_PROBE_MS,
      }
    );

    console.log(`[probe1034] injection started at ${startedAtIso} (UTC)`);
    appendFileSync(
      PROBE_JSONL,
      JSON.stringify({
        type: 'start',
        startedAtIso,
        target: TARGET,
        rate: RATE,
        tickMs: TICK_MS,
        maxBurst: MAX_BURST,
        sessionKey,
        baseline,
      }) + '\n'
    );

    // ── 前置断言：必须先看到思考块在 UI 里增长 ─────────────────────
    const minGrowth = 200;
    let preconditionOk = false;
    const preconditionDeadline = Date.now() + PRECONDITION_MS;
    while (Date.now() < preconditionDeadline) {
      const ui = (await electronApp.evaluate(() => {
        const s = (globalThis as any).__miqi1034;
        return s?.ui ?? null;
      })) as UiSample | null;
      if (ui && ui.xCount >= 0) {
        console.log(
          `[probe1034] precondition: sent=${ui.sent} uiX=${ui.xCount} (baseline ${baseline['xCount']}) ` +
            `domNodes=${ui.domNodes} ws=${ui.wsKb}KB`
        );
        if (ui.xCount - (baseline['xCount'] ?? 0) >= minGrowth) {
          preconditionOk = true;
          break;
        }
      }
      await new Promise((r) => setTimeout(r, 3_000));
    }

    if (!preconditionOk) {
      // 本轮测量无效：先停注入和采样，把现场留给排查（turn-alive / 监听注册）。
      await electronApp.evaluate(() => {
        const s = (globalThis as any).__miqi1034;
        if (s) {
          s.done = true;
          s.samplingDone = true;
        }
      });
      const lastUi = (await electronApp.evaluate(
        () => (globalThis as any).__miqi1034?.ui
      )) as UiSample | null;
      throw new Error(
        `前置断言失败：注入 ${PRECONDITION_MS}ms 内 UI 里的思考块没有增长 ` +
          `(需 ≥${minGrowth} 个 'x'，baseline=${baseline['xCount']})，最后采样=${JSON.stringify(lastUi)}。` +
          ` 本轮测量无效——需先排查 turn 是否存活 / chat:progress 监听是否注册。`
      );
    }
    console.log('[probe1034] ✅ precondition met — reasoning block is growing in the UI');

    // ── 测量循环：每 15s 打一行（stdout 只留小体积摘要）────────────
    let lastLogAt = 0;
    let finalSnap: any = null;
    for (;;) {
      const snap = await electronApp.evaluate(() => {
        const s = (globalThis as any).__miqi1034;
        if (!s) return null;
        return {
          sent: s.sent,
          target: s.target,
          done: s.done,
          gone: s.gone,
          ui: s.ui,
          ticks: s.ticks,
          sendErrors: s.sendErrors,
          sendMs: s.sendMs,
          maxBurstMs: s.maxBurstMs,
        };
      });
      if (!snap) throw new Error('harness state vanished (main process reloaded?)');
      finalSnap = snap;
      appendFileSync(PROBE_JSONL, JSON.stringify({ type: 'snap', ...snap }) + '\n');

      if (Date.now() - lastLogAt > 15_000) {
        lastLogAt = Date.now();
        const ui = snap.ui ?? {};
        console.log(
          `[probe1034] sent=${snap.sent}/${snap.target} uiX=${ui.xCount ?? '?'} ` +
            `ws=${ui.wsKb ?? '?'}KB heap=${ui.heapUsed ?? '?'} domNodes=${ui.domNodes ?? '?'} ` +
            `tick=${snap.ticks} sendMs=${snap.sendMs} ` +
            `maxBurstMs=${snap.maxBurstMs} gone=${snap.gone ? JSON.stringify(snap.gone) : 'no'}`
        );
      }

      if (snap.gone) {
        console.log(`[probe1034] 💥 renderer gone: ${JSON.stringify(snap.gone)}`);
        break;
      }
      if (snap.done) {
        // done 也可能是 stall 兜底拉停的（发送一直失败、sent 到不了 target），
        // 那种情况别报成「注入正常跑完」——injected 会如实少于 target。
        console.log(
          snap.sent >= snap.target
            ? '[probe1034] injection finished without a renderer crash'
            : `[probe1034] injection stopped early at ${snap.sent}/${snap.target} ` +
                '(send stall) without a renderer crash'
        );
        break;
      }
      await new Promise((r) => setTimeout(r, 3_000));
    }

    // 崩溃/收尾后再等 60s（12 个采样周期）：观察注入停止后 working set 是否回落，
    // 用于区分「常驻增长」与「瞬态垃圾未回收」。这段窗口里 uiTick 仍在采样——它跟
    // samplingDone 排期，不跟 done，所以下面是真采到数据，不是空窗。
    await new Promise((r) => setTimeout(r, 60_000));
    // 观察窗结束，先停采样再读 summary，免得读到一半又追加新采样点。
    await electronApp.evaluate(() => {
      const s = (globalThis as any).__miqi1034;
      if (s) s.samplingDone = true;
    });

    const summary = await electronApp.evaluate(() => {
      const s = (globalThis as any).__miqi1034;
      return {
        sent: s.sent,
        target: s.target,
        ticks: s.ticks,
        stallTicks: s.stallTicks,
        sendErrors: s.sendErrors,
        sendMs: s.sendMs,
        maxBurstMs: s.maxBurstMs,
        startedAtIso: s.startedAtIso,
        wallMs: Date.now() - s.startedAtMs,
        gone: s.gone,
        uiHistory: s.uiHistory,
      };
    });

    appendFileSync(PROBE_JSONL, JSON.stringify({ type: 'summary', ...summary }) + '\n');

    console.log('[probe1034] ────────── SUMMARY ──────────');
    console.log(
      `[probe1034] injected=${summary.sent}/${summary.target} ticks=${summary.ticks} ` +
        `sendErrors=${summary.sendErrors} main-thread-sendMs=${summary.sendMs} ` +
        `maxBurstMs=${summary.maxBurstMs} wallMs=${summary.wallMs}`
    );
    console.log(`[probe1034] crash=${summary.gone ? JSON.stringify(summary.gone) : 'none'}`);
    const hist = (summary.uiHistory ?? []) as UiSample[];
    const first = hist[0];
    const last = hist[hist.length - 1];
    if (first && last) {
      console.log(
        `[probe1034] UI-consumed: first x=${first.xCount} → last x=${last.xCount} ` +
          `(sent ${first.sent} → ${last.sent}); domNodes ${first.domNodes} → ${last.domNodes}; ` +
          `ws ${first.wsKb}KB → ${last.wsKb}KB`
      );
    }
    // 注入结束之后的采样条数——CR 复审点名要求这段必须真有采样；为 0 说明采样生命周期
    // 又跟注入一起停掉了。口径与 analyze 的尾窗一致：注入结束那条本身不算尾窗。
    const endIdx = hist.findIndex((s) => s.sent >= summary.target);
    const tailSamples = endIdx >= 0 ? hist.length - endIdx - 1 : 0;
    console.log(
      `[probe1034] uiHistory samples=${hist.length} (post-injection samples=${tailSamples}) ` +
        `jsonl=${PROBE_JSONL}`
    );

    // 测量轮不做通过/失败判定（唯一硬门是上面的前置断言）。
    expect(finalSnap).not.toBeNull();
  });
});
