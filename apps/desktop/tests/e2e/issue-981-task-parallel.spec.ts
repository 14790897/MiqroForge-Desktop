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
 * 这个用例里**什么真、什么造**（读它的结果前先看这段）：
 *
 *   ✅ 真的：真实 Electron 应用（本分支源码构建）；主任务与子任务都是真实回合
 *      （真的 chat.send、真的 runtime、真的 turn 锁）；**子线程 tab 由真实
 *      sub-agent 产生** —— 用例走产品自己的 `window.miqi.agents.spawn`（与 UI 同
 *      一条 IPC），Python 侧 `sub_agent_spawned` 经本 PR 加的接线转成
 *      `agent:spawned` 投给渲染层建 tab；
 *   ❌ 造的：**模型回复的增量流** —— provider 指向 mock_hang（POST 永不响应），
 *      思考内容与后台进度都是用例写好、经 `chat:progress` 的 `delta` 手动喂进去
 *      的（`delta` 就是「AI 回复的每次增量」，手填它等于替模型说话）。这样做的
 *      目的见常量处说明：要的是**渲染层判定**可稳定复现，不是模型行为。
 *
 * 所以本用例能证明的是：#981 修好的那段判定（主任务那条 turn 的监听是否还在）
 * 在真实应用里按预期工作；它**不证明**任何模型行为。
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

/** 解析 agents.spawn 的返回句柄（扁平 {agent_id, thread_id}，见 subagent 用例）。 */
function resolveSpawnedAgent(raw: any): { agent_id: string; thread_id: string } | null {
  const r = raw ?? {};
  for (const candidate of [r, r.result, r.agent, r.result?.agent]) {
    if (candidate && typeof candidate.agent_id === 'string') return candidate;
  }
  return null;
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
    /**
     * ⚠️ 下面两个常量是**本用例伪造的「模型输出」**，不是 AI 的真实回复。
     *
     * `chat:progress` 的 `delta` 字段就是「AI 回复的每次增量」；这里手动填它，
     * 等于替模型说话。之所以这么做：本用例要覆盖的是**渲染层的判定**（主任务的
     * 那条 turn 的监听是否还活着、还消费不消费这条流），不是模型的回答质量——
     * 用一个必然 in-flight 的 mock provider + 自己造的增量流，才能稳定地把
     * 「该被消费 / 不该被消费」这一点逼出来。判据本身（下面断言的那句）是**注入
     * 文本**，读的时候不要把它当成模型会说出来的话。
     */
    /** 注入的后台进度 delta —— 主任务「仍在被消费」的判据（伪造的模型输出）。 */
    const INJECTED_BG_DELTA = `正在处理 ${DATA_FILE}：已汇总 7/12 个月`;
    /** 注入的前台思考 delta（伪造的模型输出，用来先证明「注入确实会被消费」）。 */
    const INJECTED_THINKING_DELTAS = [
      '先读表头确认列结构，',
      '再按月份分组求和，',
      '最后按月份排序输出。',
    ];

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
    // （下面喂的是本用例伪造的 delta，不是模型输出 —— 见常量处的说明。）
    for (const delta of INJECTED_THINKING_DELTAS) {
      await injectProgress(electronApp, { stream: 'reasoning', delta, session_key: mainKey });
    }
    await expect
      .poll(() => seesInList(page, INJECTED_THINKING_DELTAS[0]), {
        timeout: 15_000,
        message: '前台注入必须被消费（思考块真的在长）',
      })
      .toBe(true);

    await page.screenshot({ path: join(SHOT_DIR, '1-main-task-running.png') });

    // ── 2. 真子智能体：走产品自己的桥接口（与 UI 同一条 IPC）─────────────
    // 这一步同时就是 #981 接线的验证：主进程把 Python 的 `sub_agent_spawned`
    // 转成 `agent:spawned` 投给渲染层，渲染层据此建 tab。接线之前，这里无论等
    // 多久都不会出现第二个 tab（当时只能靠注入事件伪造）。
    const sessionKey = await page.evaluate(
      () => localStorage.getItem('miqi:lastSession') ?? 'desktop:default'
    );
    let spawnResult: any = null;
    for (let attempt = 0; attempt < 2 && resolveSpawnedAgent(spawnResult) === null; attempt++) {
      spawnResult = await page.evaluate(
        (args: any) => (window as any).miqi.agents.spawn(args.t, args.task, args.label, args.sk),
        {
          t: 'code-agent',
          task: '生成一份本周待办清单草稿。',
          label: SUB_TAB_LABEL,
          sk: sessionKey,
        }
      );
      if (resolveSpawnedAgent(spawnResult) === null) await page.waitForTimeout(1500);
    }
    if (resolveSpawnedAgent(spawnResult) === null) {
      // 同 subagent-bridge-api.spec.ts：宿主 runner 上沙箱不可用时 spawn 返回
      // null，属环境限制而非回归。
      test.skip(true, `agent.spawn 未返回句柄（沙箱/环境限制）：${JSON.stringify(spawnResult)}`);
    }

    // tab 的 id 来自 `agent:spawned` 事件里的 `sub_thread_id`，与 spawn 返回值里
    // 那个 namespaced `thread_id` 不是同一个串 —— 按「非主 tab 的那一个」定位。
    const subTab = page.locator('[data-testid="chat-thread-tab"]:not([data-thread-id="main"])');
    await expect(
      page.getByTestId('chat-thread-tab'),
      '主 tab + 真子智能体 tab 应共两个（#981 接线）'
    ).toHaveCount(2, { timeout: 60_000 });
    await expect(subTab.first(), '子线程 tab 必须出现').toBeVisible({ timeout: 15_000 });
    await subTab.first().click();
    await page.waitForTimeout(1000);
    await expect(subTab.first(), '点击后子线程 tab 应处于选中态').toHaveAttribute(
      'data-active',
      'true'
    );

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
    // 注意这条 delta 是**本用例伪造的模型输出**（不是 AI 的真实回复）；它测的
    // 是「主任务那条 turn 的监听还在不在」，不是模型行为。
    await injectProgress(electronApp, {
      stream: 'reasoning',
      delta: INJECTED_BG_DELTA,
      session_key: mainKey,
    });

    const mainTab = page.locator('[data-testid="chat-thread-tab"][data-thread-id="main"]');
    await mainTab.click();
    await expect(mainTab).toHaveAttribute('data-active', 'true');
    await expect
      .poll(() => seesInList(page, INJECTED_BG_DELTA), {
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
