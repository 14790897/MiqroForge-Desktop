/**
 * #1118 第八轮 P1（#1035 移植）— 启动恢复竞态：幽灵 lastSession 不得先落到 ChatConsole。
 *
 * ## 被锁的缺陷
 * `App.tsx` 的启动恢复校验是**异步** effect（`sessions.list()` ∪
 * `sessions.listArchived()`），而 `<ChatConsole sessionKey={sessionKey}>` 在同一
 * 次 render 就挂载了：ChatConsole 的加载 effect 立刻对恢复出来的 key 调
 * `sessions.get(key)`。bridge 的 `sessions.get` 对未知 key 走
 * `SessionManager.get_or_create`（get-or-create，不报错、返回空会话），于是
 * 「App 要丢弃的幽灵 key」先被 ChatConsole 当成正常会话加载了一遍，切走时还会被
 * 空会话 GC 当成「上一个会话」删一次。顺序错了：挂载不该发生在「这个 key 还存在
 * 吗」有结论之前。
 *
 * 实测（第八轮的独立探针直接打真实的 sessions_get_handler / sessions_list_handler）：
 * **裸 get 不会把幽灵落盘、也不会让它进 `sessions.list`**（空会话 `exclude_empty=True`
 * 被排除），所以第七轮的回退判定本身没被这拍 get 打穿；但顺序仍然是错的：
 * `sessions.get(workspace=…)` 那种形状**确实会落盘**（探针实测写出一条
 * `conversation.jsonl`）——挂载顺序不该依赖后端当前恰好是「空会话临时态」。
 *
 * ## 本文件的三个用例
 * 1. `幽灵 lastSession → 一次都不碰`：reload 之后主进程侧**没有任何**
 *    `sessions:get` / `sessions:delete` 带着幽灵 key；反假绿是「记录器确实收到过
 *    `desktop:default` 的 get」+「lastSession 收敛回哨兵」。
 * 2. `仍存在的 lastSession → 不误切`：用挂起的 mock provider 造一个真会话
 *    （用户消息立即落盘 → 会话进 sessions.list），把 lastSession 指向它后 reload。
 *    断言 ChatConsole 加载的就是这个 key、没有被回退到默认哨兵、首个请求就是它。
 *    这条守的是两阶段启动**不是**变成「过度回退」——校验失败/判定错都会在这里红。
 * 3. `校验执行失败（list 抛错）→ 显式回退默认`（第九轮新增，#1118 同步）：让
 *    `sessions:list` / `sessions:list_archived` 在主进程侧**抛错**（不是返回空
 *    列表——那是「明确查无此 key」，走的是另一条分支），reload 后断言 lastSession
 *    收敛回哨兵、默认会话被加载过、且幽灵 key 一次都没被请求。守的是第九轮 CR
 *    那条：**验证拿不到结论时不得用未验证的非默认 key 挂载**。
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
 * ## 变异验证（两阶段门的回归护栏怎么用）
 * 把 App.tsx 的 `restorePending` 初值改成 `false`（= 恢复成第八轮之前的旧顺序：
 * 校验还没结论就挂载 ChatConsole），本文件用例 1 必须**重新变红**并抓到幽灵 key
 * 的 get/delete；改回即绿。第八轮的实测数字：修复前 4 × get(幽灵) + 1 ×
 * delete(幽灵)，修复后 0 次、只剩 `desktop:default`。
 * 第九轮的兜底语义同理：把 `resolveUnverifiedRestoreKey` 的兜底从 `return
 * defaultKey` 改回 `return currentKey`（= 旧的「保留未验证 key」），用例 3 必须红
 * ——主断言是 lastSession 不收敛（停在幽灵 key 上），失败现场快照里 ChatConsole
 * 已挂载、输入框可见，正是「挂着幽灵身份进了界面」。#1034 上实测：单测 2 例红 +
 * E2E 用例红（`Expected: "desktop:default" / Received: "desktop:<ms>"`）；改回即绿。
 *
 * 前置：`npm run build`，再
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     -g "1035"        # 本文件三个 describe 标题都带 #1035（回归组）
 *   # 只跑本文件时：
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     --workers=1 issue-1118-session-restore-race.spec.ts
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
      const boom = () => {
        throw new Error('e2e: sessions list unavailable');
      };
      for (const channel of [channels.list, channels.archived]) {
        ipc.removeHandler(channel);
        ipc.handle(channel, async () => boom());
      }
    },
    { list: SESSIONS_LIST, archived: SESSIONS_LIST_ARCHIVED }
  );
}

/** Point every configured provider at *mockUrl* and pin the default model
 *  to deepseek so the patched provider is always exercised — no real API
 *  call may ever leave the specs. */
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
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('ghost lastSession 一次都不被 ChatConsole 碰（reload 整个 App）', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);

    // ── 前置：本轮 run 的 lastSession 必须是初始哨兵（profile 隔离自证）──
    // 这条不成立就说明 Chromium profile 跨 run 共享（#1034 第七轮的实测坑），
    // 后面的「幽灵 key」会退化成上一轮的真实会话，整个用例的前提就没了。
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
  });
});

test.describe('Issue #1035 — 校验执行失败时显式回退（#1118 第九轮同步）', () => {
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

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
    await waitForBridgeInitialized(page);

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
  });
});

test.describe('Issue #1035 — 恢复校验不误伤仍存在的会话（#1118 移植）', () => {
  test.describe.configure({ mode: 'serial', timeout: 300_000 });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome).catch(() => {});
  });

  test('restored lastSession 仍存在时原样加载（不回退、不被门挂住）', async () => {
    // 起一个永不响应的 mock provider，让真实回合一直存活（会话落盘但不结束）。
    const mock = await startMockServer('mock_hang.py');
    mockServer = mock.proc;
    const fixture = await launchElectronApp((config: any) => {
      patchProvidersToMock(config, mock.mockUrl);
      // No sandbox needed — 这里验的是启动恢复的挂载顺序。
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);

    // ── 造一个真会话 ──
    // 第一次「+」会复用当前的空会话（#615 的 reuse-empty 语义，所以它还是
    // desktop:default）；先在它里面发一条消息，再点一次「+」——这时 App 才铸出
    // 新的 `desktop:<ms>` key 并写进 lastSession。新会话里再发一条：**必须**有
    // 消息才会落盘进 sessions.list，否则存在性校验会把它当幽灵（空会话不进列表）。
    // 挂起的 mock 让回合一直不结束，但用户消息立即落盘。
    await createNewConversation(page);
    await sendMessage(page, `#1035 restore-normal warmup ${Date.now()}`);
    await createNewConversation(page);
    await sendMessage(page, `#1035 restore-normal ${Date.now()}`);
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
