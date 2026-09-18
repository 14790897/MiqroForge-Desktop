/**
 * #1118 复审 P1 — 跨 session replay E2E（**真实**切换 + **真实**缓存溢出 + 真实回放）。
 *
 * 覆盖的链路（#378 的离线事件缓存 + #1034/#1118 的上限与驱逐）：
 *   A 长 turn（reasoning 很长）→ 切到 B → A 在后台继续产生事件直到缓存顶到
 *   1 MiB 字节上限 → A 的 final 到达 → 切回 A。
 *
 * 为什么必须是 E2E：这一整条链路的每一段都在别处测过（单测覆盖
 * `pushInFlightEvent` / `evictInFlightOverflow` / `splitCachedMessages`），
 * 但「切会话 → 事件进缓存 → load() 把缓存并回 UI」这条**接线**只有在真实渲染
 * 进程里才成立：缓存是模块级的、`load()` 是组件内的、路由键是每个 send 闭包
 * 捕获的。任何一环接错，单测全绿而用户仍然看到卡住的「思考中…」或者凭空多出
 * 一条回复。
 *
 * 断言（对应 review 的四条）：
 *   1. 只出现一个 final —— 回复标记在助手气泡里恰好一次；
 *   2. turnDone 正确 —— 思考块**存在**且不再是 live 态（`思考… · N 秒` 的省略号
 *      消失）；卡死的 live 思考块正是这条链路历史上出过的 bug（Audit #1）；
 *   3. reasoning 不重复、不串 session —— A 的标记不出现在 B，B 的标记不出现在 A；
 *   4. 驱逐真的发生了 —— 溢出前注入的「头部标记」progress 行在回放里**不存在**，
 *      溢出后注入的「尾部标记」**存在**。这条是本次唯一的 eviction 观察点，
 *      没有它「缓存达到上限」就只是个说法。
 *
 * 触发量（如实记录，不许悄悄缩小到测不到 eviction）：
 *   - 单事件硬上限 IN_FLIGHT_MAX_EVENT_BYTES = 64 KiB；
 *   - 缓冲区上限 IN_FLIGHT_MAX_BYTES = 1 MiB（本次由**字节**上限先到，不是
 *     2000 条计数上限）；
 *   - 实际注入：3 × 1,000,000 字符的 reasoning delta（≈6 MB UTF-16 记账字节，
 *     被 splitter 切成 ~64 KiB 的块并逐块合并）= 上限的 ~6 倍。注入量由主进程
 *     侧计数器回报，用例结束时断言 sent > 0 且记账字节 > 2 × 上限——注入没送到
 *     的话用例直接失败，不会假绿。
 *
 * 前置：`npm run build`（E2E 跑 out/ 构建产物），再
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     -g "1118 cross-session replay"
 *
 * mock：scripts/mock_hang.py —— POST 永不响应，于是 A 的 turn 一直存活、渲染层
 * 的 chat:progress / chat:final 监听一直注册着（缓存路径的前提）。真实 provider
 * 全程不被调用。
 *
 * ⚠️ 切走的方向必须是**已存在的会话**（侧边栏点卡片），不能点「+」新建：
 * `createSession()` 走 `cleanupListeners()`（ChatConsole.tsx:6276），会把上一回合
 * 那一组 chat:progress/chat:final 监听**全部退订**，于是缓存永远收不到事件——
 * 那不是本用例要测的缓存路径。（本例因此先建 B 再建 A，之后 A↔B 都走侧边栏
 * 切换。这条差异已在汇报里单独提出。）
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  APPS_DESKTOP,
  createNewConversation,
  closeElectronApp,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
/** 消息列表容器（既有 #1034/#378 用例的同一选择器）。 */
const MSG_LIST = 'main [class*="max-w-[760px]"]';
/** 侧边栏会话卡容器。 */
const SIDEBAR = 'div.flex.flex-col.shrink-0.border-r';

/** 溢出触发量：每个 delta 的字符数（见文件头「触发量」）。 */
const BALLAST_CHARS = 1_000_000;
/** 注入几个这样的 delta：3 × 1M 字符 ≈ 6 MB 记账字节，是 1 MiB 上限的 ~6 倍。 */
const BALLAST_CHUNKS = 3;
/** 断言注入量下界（远大于 1 MiB 上限，够触发多轮驱逐）。 */
const MIN_INJECTED_BYTES = 2 * 1024 * 1024;

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
  console.log(`[e2e1118] mock ${script} ready at ${readyUrl}`);
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

/**
 * 按**标题**解析会话的 session_key（= 渲染层 routingKey，注入事件按它过滤）。
 *
 * 不用「created_at 最新」那种口径：`sessions.list` 里 active-but-not-on-disk
 * 的条目 `created_at` 是 null，排序会把它们甩到最后，于是两次调用都返回同一个
 * 默认会话——用例会拿 B 的标记去和 A 比，得到「两个会话同一个 key」的假失败。
 * 标题由首条消息派生（异步），所以这里轮询等到位。
 */
async function resolveSessionKey(page: Page, marker: string): Promise<string> {
  let key = '';
  for (let attempt = 0; attempt < 30 && !key; attempt += 1) {
    key = await page.evaluate(async (m) => {
      const list = await (window as any).miqi.sessions.list();
      const sessions = (list?.sessions ?? []) as Array<{ key?: string; title?: string }>;
      return sessions.find((s) => (s.title ?? '').includes(m))?.key ?? '';
    }, marker);
    if (!key) await page.waitForTimeout(1000);
  }
  if (!key) {
    const dump = await page.evaluate(async () =>
      JSON.stringify((await (window as any).miqi.sessions.list())?.sessions ?? [])
    );
    throw new Error(`session key for ${marker} not resolvable; sessions=${dump}`);
  }
  return key;
}

/**
 * **消息列表**（会话正文）的可见文本。
 *
 * 不用 `main` —— `main` 里还有侧边栏/会话切换面板/顶栏的文本，而侧边栏会把每个
 * 会话的标题（= 首条消息）渲染出来：拿 `main` 断言「A 里不该出现 B 的标记」会
 * 因为侧边栏里的 B 卡片而假红（实测过一次），「切到某会话了」的判据也会因为
 * marker 一直在侧边栏里而变成恒真。消息列表面片才是会话正文。
 */
const listText = (page: Page): Promise<string> =>
  page.evaluate((sel) => document.querySelector(sel)?.textContent ?? '', MSG_LIST);

/** 点侧边栏里带 `marker` 的会话卡，等到消息列表面片里能看见该 marker。 */
async function switchToSession(page: Page, marker: string): Promise<void> {
  const sidebar = page.locator(SIDEBAR).first();
  const target = sidebar.getByText(marker, { exact: false }).first();
  await expect(target, `sidebar entry for ${marker} should be visible`).toBeVisible({
    timeout: 30_000,
  });
  await target.click();
  await expect.poll(() => listText(page), { timeout: 30_000 }).toContain(marker);
}

test.describe('#1118 cross-session replay', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  // 首条 send 冷启动慢（已知 flaky 基线），等待预算放宽；断言不放宽。
  const COLD_START_MS = 30_000;

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
    console.log('[e2e1118] bridge initialized');
  });

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test('A 长 turn → 切 B → 缓存溢出 → final → 切回 A：单 final / turnDone / 不串会话', async () => {
    test.setTimeout(300_000);

    const stamp = Date.now().toString(36);
    const A_PROMPT = `XREPLAY_A_${stamp}`;
    const B_PROMPT = `XREPLAY_B_${stamp}`;
    const HEAD = `XREPLAY_HEAD_${stamp}`;
    const TAIL = `XREPLAY_TAIL_${stamp}`;
    const REPLY = `XREPLAY_REPLY_${stamp}`;

    // ── 0. 先建 B 再建 A（顺序见文件头：切走只能用侧边栏，不能用「+」）──
    await createNewConversation(page);
    await sendMessage(page, B_PROMPT);
    const bKey = await resolveSessionKey(page, B_PROMPT);
    console.log(`[e2e1118] session B = ${bKey}`);

    // ── 1. 会话 A：起一个永不结束的 turn ─────────────────────────────
    await createNewConversation(page);
    await sendMessage(page, A_PROMPT);
    const aKey = await resolveSessionKey(page, A_PROMPT);
    console.log(`[e2e1118] session A = ${aKey}`);
    // 等 send 把 chat:progress 监听注册好：注入是一次性事件，监听还没挂上就白丢了
    // （探针里同样是 send 之后先 sleep 再注入）。
    await page.waitForTimeout(3000);

    // A 在前台时注入 reasoning：这一段会进快照（切走时的最后渲染状态），
    // 也就是切回后「思考块是否被正确收尾」的那一块。
    const warmup = await electronApp.evaluate(
      ({ BrowserWindow }, cfg) => {
        const win = BrowserWindow.getAllWindows().find(
          (w) => w.getTitle() === 'MiQroForge Desktop'
        );
        if (!win) throw new Error('main window not found');
        let sent = 0;
        for (let i = 0; i < cfg.count; i += 1) {
          win.webContents.send('chat:progress', {
            stream: 'reasoning',
            delta: 'x',
            session_key: cfg.sessionKey,
          });
          sent += 1;
        }
        return sent;
      },
      { count: 300, sessionKey: aKey }
    );
    expect(warmup, '前台注入必须真的发出去').toBe(300);

    // 非空洞性：前台注入必须**被消费**（思考块真的在长）。没长起来说明监听没注册，
    // 后面的缓存断言就都是空的。
    await expect.poll(() => listText(page), { timeout: COLD_START_MS }).toContain('xxxxxxxxxx');
    // live 态：标题带省略号（`深度思考… · N 秒` / `快速思考… · N 秒`）
    expect(await listText(page), '前台流式期间思考块应是 live 态').toContain('思考…');
    console.log('[e2e1118] ✅ A 前台 reasoning 已被消费（思考块 live 且在增长）');

    // ── 2. 切到 B（侧边栏，已存在会话）：A 变成后台会话 ────────────────
    expect(bKey, 'B 必须是另一个会话').not.toBe(aKey);
    await switchToSession(page, B_PROMPT);
    // 切走之后 A 的思考块不在 B 的界面里（不串会话）
    expect(await listText(page), 'B 不应看到 A 的思考块').not.toContain('思考…');

    // ── 3. A 在后台持续产生事件，直到缓存被顶到上限 ──────────────────
    // 顺序即语义：头部标记（最旧，应被驱逐）→ 大量 ballast → 尾部标记（最新，
    // 应留下）→ final（终态，load() 靠它判定 turnDone 与是否追加回复）。
    const injected = await electronApp.evaluate(
      ({ BrowserWindow }, cfg) => {
        const win = BrowserWindow.getAllWindows().find(
          (w) => w.getTitle() === 'MiQroForge Desktop'
        );
        if (!win) throw new Error('main window not found');
        const wc = win.webContents;
        let sent = 0;
        let bytes = 0;
        let sendErrors = 0;
        const push = (payload: Record<string, unknown>): void => {
          try {
            wc.send('chat:progress', payload);
            sent += 1;
            // 记账口径与渲染进程的 inFlightEventBytes 同阶（UTF-16 2 B/字符 + 开销），
            // 只用于「注入量确实超过上限」的自证，不参与实现判断。
            bytes += JSON.stringify(payload).length * 2 + 128;
          } catch {
            sendErrors += 1;
          }
        };

        // (a) 头部标记：一条普通 progress 行，是缓存里最旧的 progress 事件。
        push({ text: cfg.head, session_key: cfg.sessionKey });

        // (b) ballast：单条远超 64 KiB 硬上限的 delta —— 渲染进程会把它切成
        //     ≤64 KiB 的块再逐块合并，记账总量是 1 MiB 缓冲上限的数倍，
        //     于是 evictInFlightOverflow 必须丢最旧的 progress 事件。
        for (let i = 0; i < cfg.ballastChunks; i += 1) {
          push({
            stream: 'reasoning',
            delta: 'y'.repeat(cfg.ballastChars),
            session_key: cfg.sessionKey,
          });
        }

        // (c) 尾部标记：最新的一条 progress，驱逐不该动它（丢的永远是最旧的）。
        push({ text: cfg.tail, session_key: cfg.sessionKey });

        // (d) 终态：只发一次。回放里如果出现两条回复，就是这里被重复消费了。
        let finals = 0;
        try {
          wc.send('chat:final', { content: cfg.reply, session_key: cfg.sessionKey });
          finals += 1;
          bytes += JSON.stringify({ content: cfg.reply }).length * 2 + 128;
        } catch {
          sendErrors += 1;
        }

        return { sent, bytes, finals, sendErrors };
      },
      {
        head: HEAD,
        tail: TAIL,
        reply: REPLY,
        sessionKey: aKey,
        ballastChars: BALLAST_CHARS,
        ballastChunks: BALLAST_CHUNKS,
      }
    );

    console.log(`[e2e1118] injected=${JSON.stringify(injected)}`);
    expect(injected.finals, 'final 必须只注入一次').toBe(1);
    expect(injected.sendErrors, '注入不应有发送失败').toBe(0);
    expect(injected.sent, '注入量必须 > 0').toBeGreaterThan(0);
    // 真正的非空洞性保证：注入的记账字节数必须**超过缓冲区上限**，否则这一轮
    // 根本没有触发驱逐，下面的「头部标记消失」断言就成了假口径。
    expect(
      injected.bytes,
      `注入 ${injected.bytes} B 必须超过缓存上限（${MIN_INJECTED_BYTES} B）才会触发驱逐`
    ).toBeGreaterThan(MIN_INJECTED_BYTES);

    // 让 IPC 队列跑完再切回：尾部标记与 final 都在队列末尾，切回后能看到它们
    // 就是「整批都送到了」的证明（IPC 保序）。
    await page.waitForTimeout(3000);

    // ── 4. 切回 A：回放 ─────────────────────────────────────────────
    await switchToSession(page, A_PROMPT);
    // 回放要等 load() 拉完 sessions.get，等回复落地（这是缓存唯一的来源：
    // 挂起的 mock 永远不会把助手消息写进持久化历史）。
    await expect.poll(() => listText(page), { timeout: 60_000 }).toContain(REPLY);
    const aText = await listText(page);
    console.log(`[e2e1118] back on A, message-list length = ${aText.length}`);

    // (1) 只出现一个 final —— 回复标记在助手气泡里恰好一次。
    const replyBubbles = await page.evaluate(
      (marker) =>
        Array.from(document.querySelectorAll('[data-testid="chat-message-assistant"]')).filter(
          (el) => (el.textContent ?? '').includes(marker)
        ).length,
      REPLY
    );
    expect(replyBubbles, '回复气泡必须恰好一个（重复消费 = 一条回复渲染两遍）').toBe(1);
    // A 的提问还在，且没有被 B 的内容污染。
    expect(aText, 'A 应保留自己的提问').toContain(A_PROMPT);
    expect(aText, 'A 不应出现 B 的标记').not.toContain(B_PROMPT);

    // (2) turnDone —— 思考块还在（快照恢复），但已不是 live 态。
    //     live 态的判据是标题里的省略号（ThinkBlock: `深度思考… · N 秒`）；
    //     卡死的思考块会一直显示它 —— 那正是本链路历史上的 bug。
    expect(aText, '切回后应看到 A 的思考块').toContain('思考');
    expect(aText, '思考块不应停在 live 态（思考中… 卡死）').not.toContain('思考…');

    // (3) 驱逐真的发生了 —— 最旧的 progress 行被丢，最新的留下。
    expect(aText, '尾部标记（最新事件）必须活下来').toContain(TAIL);
    expect(
      aText,
      `头部标记（最旧事件）必须已被驱逐 —— 否则说明缓存没到上限，本用例没测到 eviction`
    ).not.toContain(HEAD);

    // ── 5. B 不受影响 ───────────────────────────────────────────────
    await switchToSession(page, B_PROMPT);
    const bText = await listText(page);
    expect(bText, 'B 应保留自己的提问').toContain(B_PROMPT);
    expect(bText, 'B 不应看到 A 的回复').not.toContain(REPLY);
    expect(bText, 'B 不应看到 A 的注入标记').not.toContain(TAIL);
    expect(bText, 'B 不应看到 A 的注入标记').not.toContain(HEAD);
    console.log('[e2e1118] ✅ 全部断言通过');
  });
});
