/**
 * #1118 第八轮 P1 — 启动恢复竞态：幽灵 lastSession 不得先落到 ChatConsole。
 *
 * ## 被锁的缺陷
 * `App.tsx` 的启动恢复校验是**异步** effect（`sessions.list()` ∪
 * `sessions.listArchived()`），而 `<ChatConsole sessionKey={sessionKey}>` 在同一
 * 次 render 就挂载了：ChatConsole 的加载 effect 立刻对恢复出来的 key 调
 * `sessions.get(key)`。bridge 的 `sessions.get` 对未知 key 走
 * `SessionManager.get_or_create`（get-or-create，不报错、返回空会话），于是
 * 「App 要丢弃的幽灵 key」先被 ChatConsole 当成正常会话加载了一遍。
 *
 * 本轮实测（独立探针直接打真实的 sessions_get_handler / sessions_list_handler）确认：
 * **裸 get 不会把幽灵落盘、也不会让它进 `sessions.list`**（空会话 `exclude_empty=True`
 * 被排除），所以 App 第七轮的回退判定本身没有被这拍 get 打穿。但顺序仍然是错的：
 * ChatConsole 的 get/delete 在「这个 key 是否还存在」有结论之前就发出去了，而
 * `sessions.get(workspace=…)` 这种形状**确实会落盘**（探针实测写出一条
 * `conversation.jsonl`）——挂载顺序不该依赖后端当前恰好是「空会话临时态」。
 *
 * ## 本文件的四个用例
 * 1. `幽灵 lastSession → 一次都不碰`：reload 之后主进程侧**没有任何**
 *    `sessions:get` / `sessions:delete` 带着幽灵 key；反假绿是「记录器确实收到过
 *    `desktop:default` 的 get」+「lastSession 收敛回哨兵」。
 * 2. `仍存在的 lastSession → 不误切`：用挂起的 mock provider 造一个真会话
 *    （用户消息立即落盘 → 会话进 sessions.list），把 lastSession 指向它后 reload。
 *    断言 ChatConsole 加载的就是这个 key、没有被回退到默认哨兵、首个请求就是它。
 *    这条守的是两阶段启动**不是**变成「过度回退」——校验失败/判定错都会在这里红。
 * 3. `校验执行失败（list 抛错）→ 显式回退默认`（第九轮新增）：让
 *    `sessions:list` / `sessions:list_archived` 在主进程侧**抛错**（不是返回空
 *    列表——那是「明确查无此 key」，走的是另一条分支），reload 后断言 lastSession
 *    收敛回哨兵、默认会话被加载过、且幽灵 key 一次都没被请求。守的是第九轮 CR
 *    那条：**验证拿不到结论时不得用未验证的非默认 key 挂载**。变异验证见用例内
 *    注释（把兜底改回「保留未验证 key」→ 必须红）。
 * 4. `同意页停留超过兜底预算`（第十轮新增，走**真同意门**）：桥的启动被同意门挡在
 *    后面（consent-first），同意前武装兜底计时器 = 拿用户在门页上的停留时间当
 *    「等桥」预算，超时就抢在存在性校验开始前把恢复 key 判负。见文件末尾的
 *    describe。第十一轮补齐时序：key 必须在 **App 挂载之前** 写好再 reload 重挂载，
 *    并显式断言「App 确实把它初始化成了恢复 key」——否则旧条件（只看
 *    `restorePending`）在这条用例上照样绿（详见文末 describe 的注释）。
 *
 * ## 观测手段
 * contextBridge 会把 `window.miqi.*` 冻结（见 repro-570-silent-send.spec.ts 的
 * 实测注释），渲染层 patch 不上；所以在**主进程**用
 * `electronApp.evaluate` 把 `sessions:get` / `sessions:delete` 的 handler 换成
 * 记录器（ipcMain 活过 renderer reload，所以 reload 之后仍在生效）。
 * reload 而不是 relaunch：重开进程会让「patch 记录器」永远晚于应用启动
 * （repro-570 已记录这一取舍），而 reload 同样会把 App 从挂载走到恢复校验、
 * 走完整条启动恢复路径。
 *
 * ## 幽灵 key 的形状
 * `desktop:<ms>` 且本轮 store 里不存在——与第七轮实测到的 flake 形状**完全一致**
 * （共享 Chromium profile 把上一轮 run 的 lastSession 带进本轮，本轮 store 里
 * 根本没有那个会话）。对 App 的存在性校验来说，「从没存在过」与「已被删除」
 * 不可区分（两者都不在 list ∪ listArchived 里）。
 *
 * 前置：`npm run build`，再
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     -g "1118 启动恢复竞态"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { startMockServer } from './helpers/mock-server';

const SESSIONS_GET = 'sessions:get';
const SESSIONS_DELETE = 'sessions:delete';
const SESSIONS_LIST = 'sessions:list';
const SESSIONS_LIST_ARCHIVED = 'sessions:list_archived';
const DEFAULT_SESSION = 'desktop:default';

interface RecordedCall {
  channel: string;
  sessionKey: string;
}

/** 主进程侧把 sessions:get / sessions:delete 换成记录器（见文件头「观测手段」）。
 *  返回值给一个形状合法的空会话 detail，让 ChatConsole 不需要重试、也不依赖真实
 *  bridge——记录的是**渲染层是否发出过这个 key 的请求**，与后端实现无关。 */
async function installSessionRecorder(app: ElectronApplication): Promise<void> {
  await app.evaluate(
    async ({ ipcMain: ipc }, channels: { get: string; del: string }) => {
      const calls: Array<{ channel: string; sessionKey: string }> = ((
        globalThis as any
      ).__miqiSessionCalls = []);
      ipc.removeHandler(channels.get);
      ipc.handle(channels.get, async (_e: unknown, payload: any) => {
        const sessionKey = String(payload?.session_key ?? payload?.sessionKey ?? '');
        calls.push({ channel: channels.get, sessionKey });
        const now = new Date().toISOString();
        return {
          key: sessionKey,
          session_id: `e2e:${sessionKey}`,
          status: 'inactive',
          ownership: 'owned',
          messages: [],
          created_at: now,
          updated_at: now,
          metadata: {},
          interrupted_turns: [],
          workspace: null,
          agent_count: 0,
        };
      });
      ipc.removeHandler(channels.del);
      ipc.handle(channels.del, async (_e: unknown, payload: any) => {
        calls.push({
          channel: channels.del,
          sessionKey: String(payload?.session_key ?? payload?.sessionKey ?? ''),
        });
        return { ok: true };
      });
    },
    { get: SESSIONS_GET, del: SESSIONS_DELETE }
  );
}

async function recordedCalls(app: ElectronApplication): Promise<RecordedCall[]> {
  return (await app.evaluate(() => (globalThis as any).__miqiSessionCalls ?? [])) as RecordedCall[];
}

/** 本 spec 注入的 list 失败处理器被调用的次数（按 channel 计）。
 *
 *  反假绿用：`sessions:list_archived` 在可见 UI 里**只有恢复校验**会调
 *  （App.tsx 的 listKnownSessionKeys；SettingsPage 只在打开归档页时调，本用例
 *  到不了那里），所以它 >0 就是「存在性校验真的跑到了、真的撞上了注入的失败」
 *  的指纹——而这条正是用例想锁的 `unverified` 分支。计数为 0 说明回退是兜底
 *  计时器给的（桥没起来 / 校验根本没跑），断言必须红。 */
async function listFailureCalls(app: ElectronApplication): Promise<Record<string, number>> {
  return (await app.evaluate(() => (globalThis as any).__miqiListFailureCalls ?? {})) as Record<
    string,
    number
  >;
}

/** 硬断言桥已经到本 spec 需要的状态（`state === 'running'`）。
 *
 *  `waitForBridgeInitialized`（helpers/electron-setup.ts）轮询到超时**不抛**，
 *  只留一行日志——本 spec 三条用例都是「注入改掉桥的行为，再看 App 怎么反应」，
 *  桥没起来时注入全部落空，而「10s 兜底回退默认」这条与注入无关的路径照样会让
 *  「幽灵 key 没被请求过」的断言变绿（假绿）。所以等完之后**显式复核**一次；
 *  不放在共享 helper 里改是因为它有 ~50 个调用点，改签名/行为会波及全库 E2E
 *  （本轮 CR 的备选方案，默认不做）。
 *
 *  **为什么只看 `state`、不看 helper 里那个 `initialized`**：`RuntimeStatus`
 *  （src/shared/ipc.ts）只有 `{state, configured, python_version?,
 *  sandbox_available?, error?}`——**没有 `initialized` 字段**，`runtime.status()`
 *  永远返回 undefined，helper 的 `s?.state === 'running' && s?.initialized`
 *  因此**恒为假**（每个调用点都白等满 30s 才继续，正是本轮 CR 说的「超时不抛」
 *  的最坏形态）。而 `state === 'running'` 恰好就是等价且可用的信号：
 *  bridge.ts 的注释与代码表明主进程只在 `initialize` 握手**之后**才把
 *  state 置成 `running`（`await this.initializeConnection(); ... this.state =
 *  'running'`）——即「renderer 看得见 running」蕴含「已握手」。
 *
 *  备注：本断言把「桥 30s 没起来」从静默继续变成显式失败——那种情况下用例本来
 *  就是空跑的，失败才是正确结果（而不是被放宽断言掩盖）。 */
async function expectBridgeRunning(page: Page): Promise<void> {
  const status = await page.evaluate(async () => {
    try {
      return await (window as any).miqi.runtime.status();
    } catch (e) {
      return { error: String(e) };
    }
  });
  expect(
    status?.state,
    `用例的注入只对运行中的桥生效：runtime.status().state 必须是 running（实际 ${JSON.stringify(status)}）`
  ).toBe('running');
}

/** 收集渲染层 console 警告。
 *
 *  App 的两条回退路径各打一条 warn（App.tsx `openGateUnverified` / 校验 effect）：
 *  - 「... no longer exists — falling back to desktop:default」= list 成功、判定幽灵；
 *  - 「... (bridge not running within 10000ms) ...」= **兜底计时器**判的负。
 *  「幽灵 key 没被请求过」在两条路径下都绿，只有日志能证明回退到底是谁给的结论。 */
function collectAppWarnings(page: Page, sink: string[]): void {
  page.on('console', (msg) => {
    if (msg.type() === 'warning' || msg.type() === 'error') sink.push(msg.text());
  });
}

/** 断言回退**不是**兜底计时器给的结论（计时器路径会把「还没验证」当成「验证失败」）。 */
function expectNoTimeoutFallback(warnings: readonly string[]): void {
  const timeoutWarns = warnings.filter((w) => w.includes('bridge not running within'));
  expect(
    timeoutWarns,
    `回退必须是存在性校验给出的结论，不能是兜底计时器（实际抓到 ${JSON.stringify(timeoutWarns)}）`
  ).toEqual([]);
}

/** 让 `sessions.list` / `sessions.listArchived` 在**执行层抛错**。
 *
 *  与「返回空列表」是两条不同分支：空列表 = 明确查无此 key（`fallback`），抛错 =
 *  **拿不到结论**（`unverified`）。第九轮 CR 指的就是后者：旧兜底把「没结论」当
 *  「保持现状」，于是门一开，ChatConsole 就带着一个从没验证过的 key 挂载了。
 *  Sidebar 自己的 `sessions.list()` 已经有 try/catch（Bridge not available），
 *  所以这里不会顺带把整个 UI 打挂——只是列表空着。 */
async function installListFailure(app: ElectronApplication): Promise<void> {
  await app.evaluate(
    async ({ ipcMain: ipc }, channels: { list: string; archived: string }) => {
      const counts: Record<string, number> = ((globalThis as any).__miqiListFailureCalls = {});
      const boom = (channel: string) => {
        counts[channel] = (counts[channel] ?? 0) + 1;
        throw new Error('e2e: sessions list unavailable');
      };
      for (const channel of [channels.list, channels.archived]) {
        ipc.removeHandler(channel);
        ipc.handle(channel, async () => boom(channel));
      }
    },
    { list: SESSIONS_LIST, archived: SESSIONS_LIST_ARCHIVED }
  );
}

/** 把所有 provider 指向 mock（同 issue-1118-cross-session-replay.spec.ts）。 */
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

test.describe('Issue #1035 — 启动恢复竞态：幽灵 lastSession 不得先落到 ChatConsole（#1118 移植）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  // 第九轮：必须把临时 MIQI_HOME 留给 afterAll，否则 closeElectronApp 拿不到它，
  // 每跑一次就在 tmpdir 里留一个 `miqi-e2e-*`（连同其中的 Chromium profile）。
  let miqiHome: string;

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('ghost lastSession is never loaded/deleted by ChatConsole before App validation', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    const warnings: string[] = [];
    collectAppWarnings(page, warnings);
    await waitForBridgeInitialized(page);
    await expectBridgeRunning(page); // 本用例要的是「桥在跑，校验给出了幽灵结论」

    // ── 前置：本轮 run 的 lastSession 必须是初始哨兵（profile 隔离自证）──
    const initial = await page.evaluate(() => localStorage.getItem('miqi:lastSession'));
    expect(initial, '本轮 run 的 Chromium profile 应是从未写过 lastSession 的新 profile').toBe(
      DEFAULT_SESSION
    );

    await installSessionRecorder(electronApp);

    // ── 触发：把 lastSession 改成幽灵 key，然后重挂载整个 App ──
    const ghostKey = `desktop:${Date.now()}`;
    await page.evaluate((k) => localStorage.setItem('miqi:lastSession', k), ghostKey);
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    // ── 断言 3：回退落地（App 把 lastSession 写回默认哨兵）──
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('miqi:lastSession')), {
        timeout: 30_000,
        message: 'App 应把幽灵 lastSession 回退到默认哨兵并持久化',
      })
      .toBe(DEFAULT_SESSION);

    // ── 断言 2（反假绿）：默认会话确实被 ChatConsole 加载过 ──
    await expect
      .poll(
        async () =>
          (await recordedCalls(electronApp)).some((c) => c.sessionKey === DEFAULT_SESSION),
        {
          timeout: 30_000,
          message:
            'ChatConsole 应加载回退后的默认会话（记录器自证：否则「没有幽灵 key」只是没发生任何事）',
        }
      )
      .toBe(true);

    // ── 断言 1（主）：幽灵 key 从未被渲染层请求过 ──
    const calls = await recordedCalls(electronApp);
    // 先打印再断言：失败时也要留下「抓到哪些请求」这条证据（断言抛了就来不及打了）。
    console.log(
      `[e2e] reload 后主进程记录到的会话请求：${JSON.stringify(calls)}（幽灵 key=${ghostKey}）`
    );
    const ghostCalls = calls.filter((c) => c.sessionKey === ghostKey);
    expect(
      ghostCalls,
      `幽灵 key ${ghostKey} 不应被 ChatConsole 加载或删除（实际抓到 ${JSON.stringify(ghostCalls)}）`
    ).toEqual([]);
    // 附带断言：首次请求就该是回退后的 key，而不是「先幽灵后默认」
    expect(calls[0]?.sessionKey).toBe(DEFAULT_SESSION);
    // ── 断言 4（回退来源自证）：结论必须来自存在性校验（list 说「查无此 key」），
    // 不能来自兜底计时器——计时器路径同样会把 lastSession 写成默认哨兵，
    // 上面三条断言在桥没起来时全会绿。这里直接锁「哪条路径给的结论」。──
    console.log(`[e2e] 回退来源日志：${JSON.stringify(warnings)}`);
    expect(
      warnings.some((w) => w.includes(`restored session ${ghostKey} no longer exists`)),
      `回退结论应来自存在性校验（应出现 "restored session ${ghostKey} no longer exists"，实际 ${JSON.stringify(warnings)}）`
    ).toBe(true);
    expectNoTimeoutFallback(warnings);
  });
});

test.describe('Issue #1035 — 校验执行失败时显式回退（#1118 第九轮同步）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('sessions.list throws → fall back to default, never mount an unverified key', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    const warnings: string[] = [];
    collectAppWarnings(page, warnings);
    await waitForBridgeInitialized(page);
    // 本用例的核心是「注入的 list 失败 → unverified → 回退默认」。桥没起来时
    // 注入永远不生效，而 10s 兜底同样把 lastSession 写成默认哨兵 + 让 ChatConsole
    // 加载默认会话——下面三条断言会**全部为真**却没有验证任何东西（假绿）。
    await expectBridgeRunning(page);

    // ── 前置：本轮 run 的 lastSession 必须是初始哨兵（profile 隔离自证）──
    const initial = await page.evaluate(() => localStorage.getItem('miqi:lastSession'));
    expect(initial, '本轮 run 的 Chromium profile 应是从未写过 lastSession 的新 profile').toBe(
      DEFAULT_SESSION
    );

    await installSessionRecorder(electronApp);
    await installListFailure(electronApp); // 校验拿不到结论（不是「查无此 key」）

    const ghostKey = `desktop:${Date.now()}`;
    await page.evaluate((k) => localStorage.setItem('miqi:lastSession', k), ghostKey);
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    // ── 断言 1（主）：显式回退落地 ──
    // 旧兜底（只清 restorePending、保留原 key）在这里就红了：lastSession 会一直
    // 停在 ghostKey 上，ChatConsole 随即带着它挂载。
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('miqi:lastSession')), {
        timeout: 30_000,
        message: '校验执行失败时必须显式回退到默认哨兵（保留未验证的 key 正是第九轮修的缺陷）',
      })
      .toBe(DEFAULT_SESSION);

    // ── 断言 2（反假绿）：默认会话确实被 ChatConsole 加载过 ──
    await expect
      .poll(
        async () =>
          (await recordedCalls(electronApp)).some((c) => c.sessionKey === DEFAULT_SESSION),
        {
          timeout: 30_000,
          message:
            'ChatConsole 应加载回退后的默认会话（记录器自证：否则「没有幽灵 key」只是没发生任何事）',
        }
      )
      .toBe(true);

    // ── 断言 3：幽灵 key 一次都没被请求（未验证的 key 绝不放行）──
    const calls = await recordedCalls(electronApp);
    console.log(
      `[e2e] list 抛错后主进程记录到的会话请求：${JSON.stringify(calls)}（幽灵 key=${ghostKey}）`
    );
    const ghostCalls = calls.filter((c) => c.sessionKey === ghostKey);
    expect(
      ghostCalls,
      `幽灵 key ${ghostKey} 在未能验证时不应被 ChatConsole 加载或删除（实际抓到 ${JSON.stringify(ghostCalls)}）`
    ).toEqual([]);
    expect(calls[0]?.sessionKey).toBe(DEFAULT_SESSION);

    // ── 断言 4（注入真的生效了）：恢复校验必须**真的调过** sessions.list_archived
    // 并撞上注入的失败。list_archived 在可见 UI 里只有恢复校验会调，所以这条计数
    // 就是「unverified 分支被走到」的指纹；计数为 0 = 回退其实是兜底计时器给的，
    // 上面三条断言全是空跑。──
    const injected = await listFailureCalls(electronApp);
    console.log(`[e2e] 注入的 list 失败处理器调用次数：${JSON.stringify(injected)}`);
    expect(
      injected[SESSIONS_LIST_ARCHIVED] ?? 0,
      `恢复校验应撞上注入的 sessions.list_archived 失败（实际调用计数 ${JSON.stringify(injected)}）——为 0 说明回退不是注入这条路径给的`
    ).toBeGreaterThan(0);

    // ── 断言 5（回退来源自证）：结论来自校验（unverified），不是兜底计时器 ──
    console.log(`[e2e] 回退来源日志：${JSON.stringify(warnings)}`);
    expect(
      warnings.some(
        (w) =>
          w.includes('could not verify restored session') && w.includes('sessions list unavailable')
      ),
      `回退结论应来自由注入失败触发的 unverified 分支（实际 ${JSON.stringify(warnings)}）`
    ).toBe(true);
    expectNoTimeoutFallback(warnings);
  });
});

test.describe('Issue #1035 — 恢复校验不误伤仍存在的会话（#1118 移植）', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('restored lastSession that still exists is loaded as-is (no fallback, no hold-up)', async () => {
    test.setTimeout(300_000);
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
    // 造会话 / 发消息 / 落盘都要求桥真的在跑（超时不抛的 helper 会把「桥没起来」
    // 一路带到后面，报错点变成会话造不出来，掩盖真正的原因）。
    await expectBridgeRunning(page);

    // ── 造一个真会话 ──
    // 第一次「+」会复用当前的空会话（#615 的 reuse-empty 语义，所以它还是
    // desktop:default）；先在它里面发一条消息，再点一次「+」——这时 App 才铸出
    // 新的 `desktop:<ms>` key 并写进 lastSession。新会话里再发一条：**必须**有
    // 消息才会落盘进 sessions.list，否则存在性校验会把它当幽灵（空会话不进列表）。
    // 挂起的 mock 让回合一直不结束，但用户消息立即落盘。
    await createNewConversation(page);
    await sendMessage(page, `#1118 restore-normal warmup ${Date.now()}`);
    await createNewConversation(page);
    await sendMessage(page, `#1118 restore-normal ${Date.now()}`);
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('miqi:lastSession')), {
        timeout: 60_000,
        message: '真实回合开始后 App 应把当前会话 key 写进 lastSession',
      })
      .not.toBe(DEFAULT_SESSION);
    const realKey = (await page.evaluate(() => localStorage.getItem('miqi:lastSession'))) as string;
    // 落盘是异步的（sendMessage 只等到乐观气泡挂上）——轮询到它进列表为止。
    const listedKeys = async (): Promise<string[]> =>
      page.evaluate(async () => {
        const r = await (window as any).miqi.sessions.list();
        return (r?.sessions ?? []).map((s: any) => s.key) as string[];
      });
    await expect
      .poll(async () => (await listedKeys()).includes(realKey), {
        timeout: 30_000,
        message: `会话 ${realKey} 应落盘并进 sessions.list（否则存在性校验会把它当幽灵）`,
      })
      .toBe(true);

    await installSessionRecorder(electronApp);
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    // ── 主断言 1：ChatConsole 加载的就是这个 key ──
    await expect
      .poll(async () => (await recordedCalls(electronApp)).some((c) => c.sessionKey === realKey), {
        timeout: 30_000,
        message: `ChatConsole 应加载恢复出来的会话 ${realKey}`,
      })
      .toBe(true);

    // ── 主断言 2：没有被回退（站稳 3s 再看，排除「先切后回落」）──
    await page.waitForTimeout(3_000);
    expect(
      await page.evaluate(() => localStorage.getItem('miqi:lastSession')),
      '会话仍然存在时不得回退到默认哨兵'
    ).toBe(realKey);

    // ── 主断言 3：首个请求就是它（没有被占位/回退插进来先摸一把别的 key）──
    const calls = await recordedCalls(electronApp);
    console.log(`[e2e] 正常恢复路径的会话请求：${JSON.stringify(calls)}（会话 key=${realKey}）`);
    expect(calls[0]?.sessionKey).toBe(realKey);
    expect(calls.some((c) => c.sessionKey === DEFAULT_SESSION)).toBe(false);
  });
});

/** 恢复门兜底计时器的预算（App.tsx 的 `RESTORE_GATE_MAX_MS`；改那边时这里同步）。 */
const RESTORE_GATE_MAX_MS = 10_000;

/** 渲染层 `miqi:lastSession` 读写探针的快照（见 installLastSessionProbe）。 */
interface LastSessionProbe {
  /** `getItem('miqi:lastSession')` 被调用的次数（reload 后的新文档内累计）。 */
  reads: number;
  /** 其中返回值 === 恢复 key 的次数。0 = App 没读到这个 key（用例前提不成立）。 */
  readsWithRestoreKey: number;
  lastReadValue: string | null;
  /** `setItem('miqi:lastSession', …)` 依次写入的值（降级会把哨兵写进来）。 */
  writes: string[];
}

/**
 * 在**页面脚本之前**挂钩 `Storage.prototype` 的读写，记录 App 对
 * `miqi:lastSession` 的每一次读写（构造见文末 describe 的注释）。
 *
 * `page.addInitScript` 只对**后续导航**生效，所以调用点必须在 `page.reload()`
 * 之前；钩子装不上时探针计数恒为 0，用例会在「App 应读过这个 key」那条断言上显式
 * 失败（而不是静默变绿）——这正是它要防的假绿。
 *
 * 计数用 `>= 1` 而不是 `=== 1`：React 的惰性 initializer 在渲染尝试被丢弃时会重跑
 * （实测读到 2 次而只 write 1 次——说明只有一次提交跑了 effect），所以「读几次」
 * 是实现细节，「读到过且写回的是它」才是断言该锁的性质。
 */
function installLastSessionProbe(restoreKey: string): void {
  const probe: LastSessionProbe = {
    reads: 0,
    readsWithRestoreKey: 0,
    lastReadValue: null,
    writes: [],
  };
  (window as any).__miqiLastSessionProbe = probe;
  const proto = Storage.prototype as any;
  const originalGet = proto.getItem;
  const originalSet = proto.setItem;
  proto.getItem = function (key: string) {
    const value = originalGet.call(this, key);
    if (key === 'miqi:lastSession') {
      probe.reads += 1;
      probe.lastReadValue = value;
      if (value === restoreKey) probe.readsWithRestoreKey += 1;
    }
    return value;
  };
  proto.setItem = function (key: string, value: string) {
    if (key === 'miqi:lastSession') probe.writes.push(String(value));
    return originalSet.call(this, key, value);
  };
}

async function lastSessionProbe(page: Page): Promise<LastSessionProbe | null> {
  return (await page.evaluate(
    () => (window as any).__miqiLastSessionProbe ?? null
  )) as LastSessionProbe | null;
}

/** 自证桥**此刻不在 running**：同意门挡着时桥不得启动，否则「门页停留」不消耗
 *  等桥预算，本用例要复现的时序不成立（runtime.start() 只由渲染层 start effect
 *  在 consentOk 之后调用，主进程不会自己起桥）。reload 前后各跑一次。 */
async function expectBridgeNotRunning(page: Page): Promise<void> {
  const status = await page.evaluate(async () => {
    try {
      return await (window as any).miqi.runtime.status();
    } catch (e) {
      return { error: String(e) };
    }
  });
  expect(
    status?.state,
    `同意门挡着时桥不得处于 running（实际 ${JSON.stringify(status)}）`
  ).not.toBe('running');
}

/**
 * #1118 第十轮 CR（Finding 1）——兜底计时器只在**同意门开启后**武装。
 *
 * ## 时序（consent-first）
 * 桥的启动被隐私同意门挡在后面（App.tsx：`if (!consentOk) return;` 时不调
 * `runtime.start()`），所以门还挡着的时候 `status.state` 恒不为 `running`、
 * 存在性校验根本不跑。此时武装兜底计时器 = 拿用户在门页上读协议的时间去消耗
 * 「等桥」的预算：停留超过 RESTORE_GATE_MAX_MS，`openGateUnverified` 就把恢复
 * 出来的 key 降级成 `desktop:default`（顺带把 `restoredSessionCheckedRef` 置真，
 * 用户点了同意之后校验再也不会跑）——**校验还没开始就被判负**。
 *
 * ## 本用例断言什么
 * 门页上停留超过兜底预算（13s > 10s）后，`miqi:lastSession` 必须原样是恢复出来
 * 的那个 key，且门仍然挡着（从没离开过同意页）。变异验证：把 App.tsx 的武装条件
 * 改回只看 `restorePending`（或让 `shouldArmRestoreTimeout` 返回 `restorePending`）
 * ——本用例在 10s 处立刻变红。
 *
 * ## 第十一轮 CR（P2）：key 必须在 **App 挂载之前** 写、且要自证 App 看见了它
 * 第十轮把「写 localStorage」放在门页已经渲染之后，这条用例因此是**假绿**的：
 * App 只在首次挂载时读一次 `miqi:lastSession`（App.tsx:158 的 `useState` 初值，
 * `restorePending` 由它经 :255 推出）。首启 profile 里这个值是 `desktop:default`
 * ⇒ `restorePending` 已是 false；之后再改 localStorage 既不更新 React state、也
 * 不会武装计时器。于是把武装条件改回 `return restorePending` 这种旧条件，本用例
 * 照样通过——旗舰守卫抓不到它要守的缺陷（真值表单测能抓，但 E2E 这条必须同样能抓）。
 *
 * 现在的时序（与真实用户路径一致）：先写非默认 key → `page.reload()` 重挂载
 * App → 门页重新出现 → 在门页上停留超过预算。reload 只影响渲染层，桥（主进程）
 * 仍然没启动，所以「门页停留不消耗等桥预算」这个前提照旧成立（reload 后重跑
 * 一次自证）。
 *
 * 同时把「App 确实看见了这个 key」变成显式断言（CR 点名要求）——否则「没降级」
 * 可能只是「App 根本没看见」：那种情况下 `restorePending` 恒为 false、计时器永远
 * 不武装，所有断言都是空跑。观测手段全部在测试侧，不改生产代码：
 * `page.addInitScript` 在页面脚本之前挂 `Storage.prototype` 的 getItem/setItem
 * 钩子（只对 reload 之后的新文档生效，所以必须在 reload 前装），记录
 *   ① 读 `miqi:lastSession` 确实发生且读到的就是恢复 key；
 *   ② 挂载时的持久化 effect（App.tsx:227）把**恢复 key**写回了 lastSession——
 *      「写回的是它」等价于「sessionKey 初值 = 它」，而 `restorePending` 正是从
 *      这个初值推出来的（同一个 `restoredSessionKeyRef`），这条把「读到了」升级成
 *      「确实把它初始化成了恢复 key」。
 * 负向信号用现成的可观察量：降级路径 `openGateUnverified`（App.tsx:267）会打
 * `console.warn('... could not verify restored session ... (bridge not running
 * within 10000ms)')`，且会把哨兵写进 localStorage——两者在旧条件下 10s 处必然
 * 出现，本用例断言它们**一条都没有**。
 *
 * 为什么不在生产代码加 `data-*` 之类的观察点：测试侧已能拿到上述两个信号，而门页
 * 期间 App 走的是 `if (!consentOk) return`（占位/ChatConsole 都不挂载），渲染层
 * 没有现成的 DOM 信号可用——加观察点会为测试改产品渲染树，代价大于收益。
 *
 * ## key 为什么是「有效性未知」的那个形状
 * 这恰恰是被修的行为本身：App 在门开之前**无法**知道这个 key 是真会话还是幽灵
 * （校验要等桥起来），所以它不得在门页上被降级。用 `desktop:<ms>` 这种「本轮
 * store 里不存在」的 key，与第七轮实测到的真实 flake 形状一致（见文件头）。
 *
 * ## 为什么不断言「点同意之后 key 还在」
 * 那是**另一条**时间线：同意之后桥才开始冷启动，仍然只有 10s 预算（第十轮的修复
 * 就是把这段完整的 10s 还回来）。断言「同意后 key 不被降级」等于断言「CI runner
 * 的桥冷启动必须 < 10s」——那是 runner 速度、不是本修复的性质，写进用例就是
 * 为了绿灯放宽断言的反面（假红 / flaky）。门页停留这一段是**确定性**的：桥在
 * 门开着时压根没启动，与 runner 快慢无关。
 */
test.describe('#1118 同意页停留超过兜底预算', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('dwelling on the consent gate past the fallback budget must not downgrade the restore key', async () => {
    test.setTimeout(240_000);
    // 不绕过同意门（consent=real，同 privacy-consent.spec）：profile 与同意记录
    // 都被 electron-setup 钉在本次 run 的临时 MIQI_USER_DATA_DIR 上，全新目录 ⇒
    // 门必然出现（若没出现，下面的可见性断言会显式失败，而不是静默降级成别的
    // 场景）。
    const fixture = await launchElectronApp(undefined, { noConsentBypass: true });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    await expect(page.getByTestId('privacy-consent-gate')).toBeVisible({ timeout: 60_000 });

    // ── 前置自证：桥在同意前**没有**启动（见 expectBridgeNotRunning）。──
    await expectBridgeNotRunning(page);

    // ── 触发（第十一轮修正）：key 必须在 App **挂载之前** 写好 —— 先写 key，再
    //    reload 重挂载，最后在门页上停留超过兜底预算。App 只在首次挂载时读一次
    //    lastSession（App.tsx:158 → :255 推出 restorePending），在门页渲染之后
    //    才写 key 的话 restorePending 永远是 false、计时器永远不武装，旧条件照样
    //    绿（第十轮用例的假绿，见上面 rationale）。──
    const restoreKey = `desktop:${Date.now()}`;
    // 探针只对后续导航生效 ⇒ 必须在 reload 之前装（见 installLastSessionProbe）。
    await page.addInitScript(installLastSessionProbe, restoreKey);
    await page.evaluate((k) => localStorage.setItem('miqi:lastSession', k), restoreKey);

    // 只收 reload 之后的渲染层日志（降级路径的 warn 必须在这里面找）。
    const warnings: string[] = [];
    collectAppWarnings(page, warnings);
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    // 门页重新出现：localStorage 里只有 lastSession 被改过（同意记录在磁盘/persist
    // 存储里，没动），所以逗留场景与首次启动一致。这一刻 App 才第一次读到上面那个
    // 非默认 key。
    await expect(page.getByTestId('privacy-consent-gate')).toBeVisible({ timeout: 60_000 });
    // reload 后重跑「桥没启动」自证：reload 只重挂渲染层，桥（主进程）仍不该起来。
    await expectBridgeNotRunning(page);

    // ── 反假绿（CR 第十一轮点名）：App 启动时**确实**读到了这个非默认 key ──
    // 没有这条，「没降级」可能只是「App 根本没看见这个 key」：那种情况下
    // restorePending 恒为 false、计时器永不武装，下面全部断言都是空跑。
    await expect
      .poll(async () => (await lastSessionProbe(page))?.reads ?? 0, {
        timeout: 30_000,
        message:
          'App 启动时应读到 miqi:lastSession（reload 后的挂载读，探针见 addInitScript）；恒为 0 = 探针没装上或 App 压根没看这个 key',
      })
      .toBeGreaterThan(0);
    // 挂载时的持久化 effect（App.tsx:227）把**恢复 key** 写回 lastSession ⇒
    // sessionKey 初值就是它 ⇒ restorePending 为真（同一个初值经 :255 推出）。
    // 「写回的是它」比「读到了它」更强：哪怕读到了却没采纳，这里也会红。
    await expect
      .poll(async () => (await lastSessionProbe(page))?.writes ?? [], {
        timeout: 30_000,
        message: `App 挂载时应把恢复 key ${restoreKey} 写回 lastSession（写成别的值说明初值不是它）`,
      })
      .toContain(restoreKey);
    const probe = (await lastSessionProbe(page)) as LastSessionProbe;
    console.log(
      `[e2e] reload 后 lastSession 读写探针（挂载读已发生）：${JSON.stringify(probe)}（恢复 key=${restoreKey}）`
    );
    expect(
      probe.readsWithRestoreKey,
      `App 启动时必须读到非默认的恢复 key ${restoreKey}（探针 ${JSON.stringify(probe)}）`
    ).toBeGreaterThan(0);

    // ── 停留超过兜底预算（门开着 ⇒ 桥没启动，与 runner 快慢无关）──
    await page.waitForTimeout(RESTORE_GATE_MAX_MS + 3_000);

    // ── 主断言：key 原样还在（修复前 10s 处被降级成 desktop:default）──
    const stored = await page.evaluate(() => localStorage.getItem('miqi:lastSession'));
    console.log(
      `[e2e] 门页停留 ${(RESTORE_GATE_MAX_MS + 3_000) / 1000}s 后的 lastSession=${stored}（恢复 key=${restoreKey}）`
    );
    expect(
      stored,
      `同意门开启前不得武装兜底计时器：lastSession 应仍是 ${restoreKey}（实际 ${stored}）`
    ).toBe(restoreKey);

    // ── 负向信号：降级路径的痕迹一条都不该有。旧条件下 10s 处必然出现
    //    「could not verify restored session … (bridge not running within 10000ms)」
    //    的 warn + 把哨兵写进 localStorage。──
    const after = (await lastSessionProbe(page)) as LastSessionProbe;
    console.log(
      `[e2e] 停留结束时的探针：${JSON.stringify(after)}；渲染层日志：${JSON.stringify(warnings)}`
    );
    expectNoTimeoutFallback(warnings);
    expect(
      warnings.filter((w) => w.includes('could not verify restored session')),
      `门页停留期间不得出现「恢复 key 被判负」的日志（实际 ${JSON.stringify(warnings)}）`
    ).toEqual([]);
    expect(
      after.writes.filter((v) => v !== restoreKey),
      `门页停留期间不得把 lastSession 写成别的值（降级会把哨兵写进来；实际写入序列 ${JSON.stringify(after.writes)}）`
    ).toEqual([]);

    // ── 附带断言：门始终挡着（上面那 13s 确实停在同意页上，没有别的路径替我们
    //    把应用推过门）──
    await expect(page.getByTestId('privacy-consent-gate')).toBeVisible();
  });
});
