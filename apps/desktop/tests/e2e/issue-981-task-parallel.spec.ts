/**
 * E2E: #981 多任务并行 —— 切换/在另一个任务里发消息，不得中断正在跑的旧任务
 *
 * 「任务」= 一个 desktop session 下的一个线程 tab。发给 chat.send 的
 * `session_key` 按 tab 分（主 tab = 基础 session key，子线程 tab =
 * `desktop:<threadId>`，见 threadTabs.ts:routingKeyFor），所以两条 turn 落在
 * 不同 runtime、不同 turn 锁上——后端本就支持并行，卡住的是前端按 session
 * 粒度做的判定。本用例在真实应用里把这两条路径都跑一遍：
 *
 *   场景 1（issue 主症状「切换即中断」）：主 tab 起一个永不结束的 turn →
 *     子线程 tab 发消息 → 断言**没有**把主 turn 中断。判据取「主任务的后台事件
 *     仍被 live 消费」：注入一条带主 routing key 的 chat:progress，切回主 tab
 *     必须能看见它。修复前的 supersede 会先 `cleanupListeners()` 退订主
 *     invocation 的监听再 abort，这条注入就再也不会被消费。
 *
 *   场景 2（复审发现的残留「回原任务被锁拒」）：承接场景 1，回到主 tab 再发一条
 *     —— 同任务内重发必须命中并 supersede 自己那条在飞 turn；修复前单槽
 *     `lifecycleRef` 已被 B 的 turn 顶掉，会不做 supersede 直接再发，后端以
 *     TURN_IN_PROGRESS 拒绝。断言全程不出现该错误文案。
 *
 * mock：scripts/mock_hang.py —— POST 永不响应，于是两个 tab 的 turn 都一直
 * in-flight。真实 provider 全程不被调用。会话正文与「思考中」的内容由本用例
 * 按一个正常的数据汇总场景书写（文件名带本轮短码，作为唯一判据）。
 *
 * ⚠️ 本用例覆盖的是**当前产品里走不到的一条路径**，必须知道这点再读它的结果：
 * 子线程 tab 只能由 `agent:spawned` 事件产生（ChatConsole 的 `agents.onSpawned`
 * 是 `addThreadTab` 的唯一调用点），而 `IPC_EVENTS.AGENT_SPAWNED`
 * （src/shared/ipc.ts:224）**全仓库没有任何地方 send 过**——主进程 chat 事件的
 * 转发白名单里只有 progress/final/error/aborted/approval*/userInput*/
 * subagent_result（src/main/ipc/index.ts:379-395），Python 侧发的是
 * `sub_agent_spawned` 且没有转发。也就是说：当前版本 tab 列表永远只有
 * `['main']`，tab 栏不渲染，「同一会话下的多个任务」在产品里尚不存在。
 * 本用例因此**主动注入** `agent:spawned` 来构造这条路径——它锁的是 #981 修好的
 * 那段判定逻辑（一旦 spawn 事件接线，或将来有别的入口产生第二个任务，这段逻辑
 * 就是对的），不是「用户现在真能复现」的证据。真实模型版同理跑不通（等不到
 * 第二个 tab），已删除。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  APPS_DESKTOP,
  closeElectronApp,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { startMockServer } from './helpers/mock-server';

/** 消息列表容器（#1034/#1118 同一选择器）。 */
const MSG_LIST = 'main [class*="max-w-[760px]"]';
/** TURN_IN_PROGRESS 的用户可见文案（sanitizeUiMessage.ts:47）。 */
const TURN_IN_PROGRESS_TEXT = '上一个任务还在进行中';

const SHOT_DIR = join(APPS_DESKTOP, 'test-reports', 'issue981');

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

/** 当前显示的消息列表里能看到 `marker`。 */
const seesInList = async (page: Page, marker: string): Promise<boolean> =>
  (await listText(page)).includes(marker);

/** 经主窗口给渲染层发一条 chat:progress（前台/后台注入共用）。 */
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

test.describe('#981 多任务并行：切换任务不中断', () => {
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
    console.log('[e2e981] bridge initialized');
  });

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test('主 tab 长 turn → 子线程 tab 发消息 → 主 turn 不被中断 → 回主 tab 重发不撞 TURN_IN_PROGRESS', async () => {
    test.setTimeout(300_000);
    mkdirSync(SHOT_DIR, { recursive: true });

    // 内容按一个正常的「数据汇总」场景书写；本轮短码放进文件名里，既自然又是
    // 唯一判据（profile 每轮隔离，跨 run 不会串）。
    const run = Date.now().toString(36).slice(-4);
    const DATA_FILE = `sales-${run}.csv`;
    const MAIN_PROMPT = `帮我统计 ${DATA_FILE} 里各月份的销售额，并画一张趋势图`;
    const SUB_PROMPT = '把刚才的汇总结果整理成 Markdown 表格';
    const RESEND_PROMPT = '再把这个表格导出成 CSV';
    const SUB_TAB_LABEL = `汇总 ${DATA_FILE}`;
    /** 后台注入的进度文本 —— 主任务「仍在被消费」的判据。 */
    const BG_PROGRESS = `正在处理 ${DATA_FILE}：已汇总 7/12 个月`;
    /** 前台注入的思考内容片段（保证注入真的被消费）。 */
    const THINKING = ['先读表头确认列结构，', '再按月份分组求和，', '最后按月份排序输出。'];

    // ── 1. 主任务：起一个永不结束的 turn ────────────────────────────────
    await sendMessage(page, MAIN_PROMPT);
    // 主 tab 的 routing key = 基础 session key；send 之后它才被写进 localStorage。
    let mainKey = '';
    for (let attempt = 0; attempt < 30 && !mainKey; attempt += 1) {
      mainKey = await page.evaluate(() => localStorage.getItem('miqi:lastSession') ?? '');
      if (!mainKey) await page.waitForTimeout(1000);
    }
    expect(mainKey, '主会话的 routing key 必须能解析出来').not.toBe('');
    console.log(`[e2e981] main session key = ${mainKey}`);
    // 等 send 把 chat:progress 监听挂上（注入是一次性事件，监听没挂上就白丢）。
    await page.waitForTimeout(3000);

    // 前台自检：注入的思考内容必须被消费（思考块真的在长）。
    // 这一步同时是后面「后台仍被消费」判据的对照组。
    for (const delta of THINKING) {
      await injectProgress(electronApp, { stream: 'reasoning', delta, session_key: mainKey });
    }
    await expect
      .poll(() => seesInList(page, THINKING[0]), {
        timeout: 15_000,
        message: '前台注入必须被消费（思考块真的在长）',
      })
      .toBe(true);

    await page.screenshot({ path: join(SHOT_DIR, '1-main-task-running.png') });

    // ── 2. 生成子线程 tab（注入 agent:spawned —— 当前产品里没有任何地方发它，
    //      见文件头 ⚠️；这是构造这条路径的唯一方式）────────────────────────
    await electronApp.evaluate(
      ({ BrowserWindow }, payload) => {
        const win = BrowserWindow.getAllWindows().find(
          (w) => w.getTitle() === 'MiQroForge Desktop'
        );
        if (!win) throw new Error('main window not found');
        win.webContents.send('agent:spawned', payload);
      },
      { sub_thread_id: `sub-${run}`, agent_type: 'code-agent', task_label: SUB_TAB_LABEL }
    );

    const subTab = page.locator(`[data-testid="chat-thread-tab"][data-thread-id="sub-${run}"]`);
    await expect(subTab, '子线程 tab 必须出现').toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('chat-thread-tab')).toHaveCount(2, { timeout: 15_000 });
    await subTab.click();
    await page.waitForTimeout(1000);
    await expect(subTab, '点击后子线程 tab 应处于选中态').toHaveAttribute('data-active', 'true');

    // ── 3. 场景 1：在子线程 tab 发消息（旧实现会在这里 abort 掉主任务）──
    await sendMessage(page, SUB_PROMPT);
    await page.waitForTimeout(4000);

    // 修前：这里（以及 handleAbort）会渲染 TURN_IN_PROGRESS / 已停止。
    await expect(
      page.getByText(TURN_IN_PROGRESS_TEXT),
      '子线程 tab 发消息不得被 TURN_IN_PROGRESS 拒绝'
    ).toHaveCount(0);

    await page.screenshot({ path: join(SHOT_DIR, '2-sub-task-sending.png') });

    // 判据：主任务的后台事件仍被 live 消费 —— 注入一条带主 key 的 progress，
    // 切回主 tab 必须看得见。修复前 supersede 先 cleanupListeners() 退订了主
    // invocation，这条注入不会再被消费。
    await injectProgress(electronApp, {
      stream: 'reasoning',
      delta: BG_PROGRESS,
      session_key: mainKey,
    });

    const mainTab = page.locator('[data-testid="chat-thread-tab"][data-thread-id="main"]');
    await mainTab.click();
    await expect(mainTab).toHaveAttribute('data-active', 'true');
    await expect
      .poll(() => seesInList(page, BG_PROGRESS), {
        timeout: 15_000,
        message: '切回主 tab 后，主任务的后台事件必须仍被消费（主 turn 未被中断）',
      })
      .toBe(true);
    await page.screenshot({ path: join(SHOT_DIR, '3-back-on-main-still-live.png') });

    // ── 4. 场景 2：回主 tab 重发（同任务内 supersede）──────────────────
    // 修前：单槽 lifecycleRef 已被子 tab 的 turn 顶掉 → 查不到主任务的在飞
    // turn → 不做 supersede 直接再发 → 后端拒 TURN_IN_PROGRESS。
    await sendMessage(page, RESEND_PROMPT);
    await page.waitForTimeout(5000);

    await expect(
      page.getByText(TURN_IN_PROGRESS_TEXT),
      '回原任务重发不得撞 TURN_IN_PROGRESS（同任务 supersede 必须命中）'
    ).toHaveCount(0);
    await expect.poll(() => seesInList(page, RESEND_PROMPT), { timeout: 10_000 }).toBe(true);

    await page.screenshot({ path: join(SHOT_DIR, '4-resend-on-main-ok.png') });
    console.log(`[e2e981] screenshots -> ${SHOT_DIR}`);
  });
});
