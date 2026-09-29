/**
 * E2E（跨会话 · mock 确定性）：#981 —— 别的会话在跑时,本会话重发不该被拒/不该掐掉对方
 *
 * 为什么单独一条:issue #981 里**用户今天就能碰到**的修正不是线程 tab 那条(那条要
 * 子智能体 spawn 才走得到),而是这条跨会话路径。它按下面的顺序踩两个缺陷:
 *
 *   会话 A 起一个永不结束的 turn → 切到会话 B 发一条消息 → 回到 A 再发一条
 *
 * 修复前(单槽 `lifecycleRef` 只存「全组件最近一条 turn」):
 *   · 第三步在 A 重发时,槽里躺着的是 **B 的 turn** → `supersedeSameSession` 不成立
 *     → 既不做 supersede、也不等 A 的在飞 turn settle → 直接发第二条进 A 的 runtime
 *     → 后端 `TURN_IN_PROGRESS` 拒绝,用户看到「上一个任务还在进行中」;
 *   · 同一路径上,若某次判断真的走到 supersede,旧代码的 `cleanupListeners()` 无参调用
 *     会退订「全局最新那条 invocation」——那时它是 **B 的**,于是 B 的 turn 被挂死
 *     (终态无人处理、watchdog 消失),而用户并没有停 B。
 *
 * 两条断言分别盯这两个症状:
 *   ① A 重发后**不出现** TURN_IN_PROGRESS;
 *   ② 之后给 B 注入一条后台 progress,切回 B 必须看得见 —— 说明 B 的监听还活着。
 *
 * mock：scripts/mock_hang.py（POST 永不响应),两个会话的 turn 都一直 in-flight,
 * 于是「在飞 turn」是确定存在的,不依赖任何模型行为。
 *
 * ⚠️ 切会话只能点侧边栏里**已存在**的会话卡,不能点「+」新建:新建走
 * `createSession()` → `cleanupListeners()`,会把上一个回合的监听全退订,那是另一条
 * 路径(与 #1118 用例同一条注意事项)。所以顺序是「先建 B 再建 A,之后 A↔B 都走侧边栏」。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  APPS_DESKTOP,
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { startMockServer } from './helpers/mock-server';

/** 消息列表容器（#1034/#1118 同一选择器）。 */
const MSG_LIST = 'main [class*="max-w-[760px]"]';
/** 侧边栏会话卡容器。 */
const SIDEBAR = 'div.flex.flex-col.shrink-0.border-r';
/** TURN_IN_PROGRESS 的用户可见文案（sanitizeUiMessage.ts:47）。 */
const TURN_IN_PROGRESS_TEXT = '上一个任务还在进行中';

const SHOT_DIR = join(APPS_DESKTOP, 'test-reports', 'issue981-cross');

// 同 issue-981-task-parallel.spec.ts：mock 类 spec 在 macOS CI 上连不上本地 listener。
const SKIP_MOCK_ON_MACOS_CI = process.platform === 'darwin' && !!process.env.CI;

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

const listText = (page: Page): Promise<string> =>
  page.evaluate((sel) => document.querySelector(sel)?.textContent ?? '', MSG_LIST);

const seesInList = async (page: Page, marker: string): Promise<boolean> =>
  (await listText(page)).includes(marker);

/** 按标题解析会话 key（标题由首条消息派生，异步，所以要轮询）。 */
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
  if (!key) throw new Error(`session key for ${marker} not resolvable`);
  return key;
}

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

/** 经主窗口给渲染层发一条 chat:progress（模拟被切走那个会话的后台事件）。 */
async function injectProgress(
  electronApp: ElectronApplication,
  payload: Record<string, unknown>
): Promise<void> {
  await electronApp.evaluate(({ BrowserWindow }, data) => {
    const win = BrowserWindow.getAllWindows().find((w) => w.getTitle() === 'MiQroForge Desktop');
    if (!win) throw new Error('main window not found');
    win.webContents.send('chat:progress', data);
  }, payload);
}

test.describe('#981 跨会话重发：不被锁拒、也不掐掉对方', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.skip(SKIP_MOCK_ON_MACOS_CI, 'macOS CI cannot reach the local mock server');

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
    console.log('[e2e981-cross] bridge initialized');
  }, 120_000);

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test('A 在飞 → B 发消息 → 回 A 重发：不撞 TURN_IN_PROGRESS，且 B 的监听仍活着', async () => {
    test.setTimeout(300_000);
    mkdirSync(SHOT_DIR, { recursive: true });

    const stamp = Date.now().toString(36).slice(-4);
    const B_PROMPT = `会话B：整理一份销售数据摘要（${stamp}）`;
    const A_PROMPT = `会话A：统计各月销售额趋势（${stamp}）`;
    const A_RESEND = `会话A：再按季度分组算一次（${stamp}）`;
    const B_BG = `会话B 的后台进度：已处理 3/12 个月（${stamp}）`;

    // ── 0. 先建 B，再建 A（顺序见文件头：切会话只能走侧边栏）─────────────
    await createNewConversation(page);
    await sendMessage(page, B_PROMPT);
    const bKey = await resolveSessionKey(page, B_PROMPT);
    console.log(`[e2e981-cross] session B = ${bKey}`);

    await createNewConversation(page);
    await sendMessage(page, A_PROMPT);
    const aKey = await resolveSessionKey(page, A_PROMPT);
    console.log(`[e2e981-cross] session A = ${aKey}`);
    expect(aKey, 'A 与 B 必须是两个会话').not.toBe(bKey);

    // ── 1. A 的 turn 在飞（mock 永不响应）───────────────────────────────
    // 等 send 把 A 的 chat:progress 监听挂上（注入是一次性事件）。
    await page.waitForTimeout(3000);
    await injectProgress(electronApp, {
      stream: 'reasoning',
      delta: 'A 的思考：先读表头',
      session_key: aKey,
    });
    await expect.poll(() => seesInList(page, 'A 的思考：先读表头'), { timeout: 15_000 }).toBe(true);

    // ── 2. 切到 B 发一条消息：B 的 invocation 成为「全局最新」────────────
    await switchToSession(page, B_PROMPT);
    await sendMessage(page, `B 的第二个回合（${stamp}）`);
    await page.waitForTimeout(3000);

    // ── 3. 回 A 重发 —— 判别点 ──────────────────────────────────────────
    await switchToSession(page, A_PROMPT);
    await sendMessage(page, A_RESEND);
    await page.waitForTimeout(5000);

    // 取证：这一刻的画面就是判别点（旧代码这里会出现红色的「上一个任务还在进行中」）。
    await page.screenshot({ path: join(SHOT_DIR, '1-after-resend-in-A.png') });

    // ① 旧代码：单槽 lifecycle 里是 B 的 turn → A 的重发不做 supersede → 后端拒
    await expect(
      page.getByText(TURN_IN_PROGRESS_TEXT),
      'A 在飞时回 A 重发不得被 TURN_IN_PROGRESS 拒绝（同任务 supersede 必须命中）'
    ).toHaveCount(0);

    // ② 旧代码：supersede 里无参 cleanupListeners() 会退订 B 的 invocation →
    //    B 的监听挂死。给 B 注入一条后台事件,切回 B 必须看得见。
    //
    // 注意事件类型：A 此刻是当前会话,所以这条事件会先落到「后台会话缓存」,切回 B 时
    // 由 cachedEventsToMessages 回放。而回放**只渲染 `text`/points/final/error/aborted**
    // —— `stream:'reasoning'` 的 delta 不进回放(它只走实时思考块)。所以这里用带 `text`
    // 的进度事件,而不是 reasoning delta,否则断言测的是回放口径、不是监听死活。
    await injectProgress(electronApp, { text: B_BG, session_key: bKey });
    await switchToSession(page, B_PROMPT);
    await expect
      .poll(() => seesInList(page, B_BG), {
        timeout: 15_000,
        message: 'B 的监听必须仍然活着（不得被 A 的 supersede 清理顺手退订）',
      })
      .toBe(true);

    await page.screenshot({ path: join(SHOT_DIR, '2-back-on-B-alive.png') });
    console.log(`[e2e981-cross] ok -> ${SHOT_DIR}`);
  });
});
