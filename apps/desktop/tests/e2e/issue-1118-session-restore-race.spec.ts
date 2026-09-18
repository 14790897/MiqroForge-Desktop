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
 * 本轮实测（`tests/runtime` 之外的独立探针，见汇报）确认：**裸 get 不会把幽灵
 * 落盘、也不会让它进 `sessions.list`**（空会话 `exclude_empty=True` 被排除），
 * 所以 App 第七轮的回退判定本身没有被这拍 get 打穿。但顺序仍然是错的：
 * ChatConsole 的 get/delete 在「这个 key 是否还存在」有结论之前就发出去了，
 * 而 `sessions.get(workspace=…)` 这种形状**确实会落盘**（探针实测写出一条
 * `conversation.jsonl`）——挂载顺序不该依赖后端当前恰好是「空会话临时态」。
 *
 * ## 断言（本文件）
 * 1. 主断言：reload 之后，主进程侧**没有任何** `sessions:get` 带着幽灵 key
 *    （也不得有 `sessions:delete` 带着它——切走时的空会话 GC 会删「上一个会话」）；
 * 2. 反假绿：记录器必须**确实收到过** `desktop:default` 的 get（证明 ChatConsole
 *    真的加载过、记录器真的在工作；否则「没有幽灵 key」可能只是什么都没发生）；
 * 3. App 的回退确实落地：`localStorage['miqi:lastSession']` 收敛回 `desktop:default`。
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
 *     -g "1118 restore race"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  closeElectronApp,
  launchElectronApp,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

const SESSIONS_GET = 'sessions:get';
const SESSIONS_DELETE = 'sessions:delete';
const DEFAULT_SESSION = 'desktop:default';

interface RecordedCall {
  channel: string;
  sessionKey: string;
}

test.describe('#1118 启动恢复竞态', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.afterAll(async () => {
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('ghost lastSession is never loaded/deleted by ChatConsole before App validation', async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    await waitForBridgeInitialized(page);

    // ── 前置：本轮 run 的 lastSession 必须是初始哨兵（profile 隔离自证）──
    const initial = await page.evaluate(() => localStorage.getItem('miqi:lastSession'));
    expect(initial, '本轮 run 的 Chromium profile 应是从未写过 lastSession 的新 profile').toBe(
      DEFAULT_SESSION
    );

    // ── 装记录器：主进程侧换掉 sessions:get / sessions:delete 的 handler ──
    // 返回值给一个形状合法的空会话 detail，让 ChatConsole 不需要重试、也不依赖
    // 真实 bridge（记录的是**渲染层是否发出过这个 key 的请求**，与后端实现无关）。
    await electronApp.evaluate(
      async ({ ipcMain: ipc }, channels: { get: string; del: string }) => {
        const calls: Array<{ channel: string; sessionKey: string }> = ((
          globalThis as any
        ).__miqiSessionCalls = []);
        const respond = async (_e: unknown, payload: any) => {
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
        };
        ipc.removeHandler(channels.get);
        ipc.handle(channels.get, respond);
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
    const recorded = async (): Promise<RecordedCall[]> =>
      electronApp.evaluate(() => (globalThis as any).__miqiSessionCalls ?? []);
    await expect
      .poll(async () => (await recorded()).some((c) => c.sessionKey === DEFAULT_SESSION), {
        timeout: 30_000,
        message:
          'ChatConsole 应加载回退后的默认会话（记录器自证：否则「没有幽灵 key」只是没发生任何事）',
      })
      .toBe(true);

    // ── 断言 1（主）：幽灵 key 从未被渲染层请求过 ──
    const calls = await recorded();
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
