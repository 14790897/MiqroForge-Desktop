/**
 * #1036 复现探针 —— 判断「这个问题到底存不存在」。
 *
 * 维护者要求先有一个能回答「这问题存不存在」的用例，并且明确要求**用真实的、
 * 不要模拟**：mock 只能证明「链路按我假设的形状走」，证明不了真实模型 + 真实
 * 工具路径真的会饿死请求。所以本用例：
 *
 *   · **真实 provider**：`launchElectronApp()` 不传 patchConfig，走本机
 *     `~/.miqi/config.json`（本地 deepseek）/ CI 的 siliconflow，无 mock server；
 *   · **真实长 turn**：让模型用 `exec` 工具跑一条约 25 分钟的命令（`echo` +
 *     `sleep` 循环），stdout 逐块回流，与事故现场「Slurm MCP + 多轮 exec」的
 *     形态同侧——都有真实子进程与真实工具事件；
 *   · **真实判据**：长 turn 期间每 30s 发一次 `config.get`（页面内
 *     fire-and-forget `setInterval`，与 TopBar 同一条调用链），**settle 用时
 *     ≥ 30s 即命中**。
 *
 * ⚠️ 判据只能看**用时**，不能看 `err`：超时路径上渲染侧只会 resolve 并拿到本地
 * 兜底配置（`sendSafe` 捕获后 `return null` 不 rethrow，`ipc/index.ts` 接着
 * `return bridgeConfig ?? readLocalConfig()`，而 `readLocalConfig` 从不抛），
 * 所以 `onRejected` 分支永不触发、`err` 恒为 null —— 按 `err` 判会永远判不出命中。
 *
 * ⚠️ 也必须 fire-and-forget（采样本写进页面数组、轮询其长度），否则 Playwright
 * 会等到这个 Promise **settle**（命中时要 720s）才返回，节奏变成 ~750s/次。
 *
 * ⚠️ **turn 存活率进判据**：真实模型的 turn 长度不完全可控。若 turn 在窗口中途
 * 就结束，剩下的样本是在「没有 turn 需要转发」的状态下采的，那这轮是**无结论**
 * 而不是「没命中」——所以用 chat 事件的时间跨度量出 turn 的真实存活时长，不足
 * 窗口的 MIN_ALIVE_RATIO 就判 skip（并打印全部证据）。
 *
 * 证据：全程录像（video: on）+ 起步/中途/收尾截图 + 收尾时把样本表渲染成图，
 * 都落在 `apps/desktop/test-results/`。
 *
 * ⚠️ **必须预授权，否则真实 exec 根本跑不起来**：命令审批卡是独立于
 * `approvals.bypass_all` 的一道门，而且卡片自带倒计时（超时=自动拒绝）。实测
 * 第一轮就是这样——模型确实调了 exec，但卡在审批上超时被拒，命令一个字节都没
 * 跑，turn 63 秒就结束了。所以 beforeAll 里按仓库通行的 e2e 做法
 * `approvals.addPermanent('*:*', 'always')` 预授权；轮询期间再兜底点击任何
 * 仍然弹出的审批卡（会打日志，不静默）。这是**配置**选择，不是模拟：
 * 「永久允许」按钮走的就是同一条 addPermanent 路径。
 *
 * ⚠️ **默认跳过**：默认窗口 20 分钟，不进常规 e2e / CI 套件（与
 * issue-1034-renderer-oom-probe.spec.ts 同一模式，见 CI-COVERAGE.md）。
 * 只有显式设了 `MIQI_1036_PROBE=1` 才运行。
 *
 * 运行：
 *   cd apps/desktop && npm run build
 *   MIQI_1036_PROBE=1 npx playwright test --config=playwright.config.ts \
 *     --project=electron --workers=1 \
 *     tests/e2e/issue-1036-rpc-starvation-probe.spec.ts
 *
 * 可调环境变量：MIQI_1036_PROBE（=1 才跑）/ _SAMPLE_MS / _SAMPLES（下限，不是
 * 停止条件）/ _WINDOW_MS（主旋钮，结论强度只由它决定）/ _CMD_SECONDS（让模型跑
 * 多久的命令，必须 > 窗口）
 *
 * 判读：**通过 = 本窗口内没复现；失败 = 复现了**；**skip = 这轮无结论**（turn
 * 早于窗口结束 / 一个样本都没落袋）。结论必须连窗口时长、样本数、turn 存活时长
 * 与事件数一起报。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
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

/** 探针开关：默认关闭（见文件头）。 */
const PROBE_ENABLED = process.env['MIQI_1036_PROBE'] === '1';

const SAMPLE_MS = Number(process.env['MIQI_1036_SAMPLE_MS'] ?? 30_000);
/** 样本数下限 —— 不是停止条件（健康样本 ~10ms 就落袋，攒够就停会把结论
 *  退化成「60 秒没命中」）。 */
const MIN_SAMPLES = Number(process.env['MIQI_1036_SAMPLES'] ?? 3);
/** issue 的判据：一次 `config.get` settle 用时 ≥ 30s 即不健康（超时要 720s）。 */
const HEALTHY_MS = 30_000;
/** 采样窗口（主旋钮）。 */
const WINDOW_MS = Number(process.env['MIQI_1036_WINDOW_MS'] ?? 20 * 60_000);
/** 让模型跑的 exec 命令时长（秒）。必须 > 窗口，turn 才不会中途收尾。 */
const CMD_SECONDS = Number(process.env['MIQI_1036_CMD_SECONDS'] ?? 1500);
/** turn 至少要活过窗口的这个比例，否则本轮无结论。 */
const MIN_ALIVE_RATIO = 0.8;
const POLL_MS = 1_000;
const SCREENSHOT_EVERY_MS = 3 * 60_000;

const OUT_DIR = join(APPS_DESKTOP, 'test-results');

interface CfgSample {
  ms: number;
  /** 超时路径也会 resolve（拿到本地兜底配置），所以它只作留痕，不参与判据。 */
  gotValue: boolean;
}

interface ProbeState {
  samples: CfgSample[];
  /** turn 存活证据：chat 事件条数与最后一次到达的时刻。 */
  progressCount: number;
  lastProgressAt: number;
}

/**
 * 驱动一个**真实的长 turn**：让模型用 exec 跑一条约 *seconds* 秒的命令。
 *
 * 两个坑都要在提示词里挡住，否则 turn 会以各种方式提前结束：
 *   · `timeout` 必须显式给出 —— exec 的默认超时是 60s，不写就会被杀在半路
 *     （`miqi/agent/tools/shell.py` 的 per-call timeout，上限 1800s）；
 *   · 命令**不能用 `$( )` 命令替换** —— 护栏的 deny 表里有
 *     `\$\([^)\n]{1,500}\)`，`for i in $(seq 1 40)` 会被硬拦。实测第二版就是
 *     这样：审批放行了，命令仍被拒，模型拿到「命令被安全护栏拦截（检测到危险
 *     模式）」并原样报告，turn 3 秒就收了尾。所以这里用 bash 花括号展开
 *     `{1..N}`（已逐条对着 deny 正则验证过放行），并要求原样执行。
 */
function longTurnPrompt(seconds: number): string {
  const ticks = Math.ceil(seconds / 5);
  return (
    `请调用 exec 工具执行下面这一条命令，并等它**完整跑完**再回复我。\n\n` +
    `命令：for i in {1..${ticks}}; do echo tick-$i; sleep 5; done\n` +
    `参数：timeout=${seconds + 120}\n\n` +
    `要求：**原样执行**——不要改写、不要拆成多条、不要换成 $(seq …) 之类的` +
    `命令替换（命令替换会被安全护栏直接拦下）、也不要放到后台。\n` +
    `这条命令要跑大约 ${Math.round(seconds / 60)} 分钟，所以 timeout 必须按上面给的写` +
    `（默认 60 秒会被杀掉）。运行期间不要做别的事。等它返回之后，只回一句「完成」。`
  );
}

// 长跑：留录像（维护者要求结果要有视频/截图证明），关掉 trace（拖慢且没用）。
// 必须在文件顶层 —— 放进 describe 里 Playwright 会报 "forces a new worker"。
test.use({ video: 'on', screenshot: 'off', trace: 'off' });

/**
 * 兜底：审批卡自带倒计时，超时即自动拒绝（实测第一轮 exec 就是这么被拒的）。
 * 预授权之外若还有卡（Action Guard 是另一个入口，`addPermanent` 不一定覆盖），
 * 就点「本次会话允许」放行，并**打日志**——静默放行会把「其实被门挡住了」伪装成
 * 「跑过了」。
 */
async function dismissApprovalCards(page: Page): Promise<void> {
  const title = page.getByTestId('approval-title');
  if (!(await title.isVisible().catch(() => false))) return;
  const label = (await title.textContent().catch(() => '')) ?? '';
  const sessionBtn = page.getByRole('button', { name: '本次会话允许' });
  const once = (await sessionBtn.count()) > 0;
  console.log(`[probe1036] 兜底放行审批卡「${label}」→ ${once ? '本次会话允许' : '允许一次'}`);
  await (once ? sessionBtn : page.getByRole('button', { name: '允许一次' })).first().click();
}

test.describe('#1036 RPC starvation probe (real model + real exec)', () => {
  // describe 级 skip：默认整块跳过（含 beforeAll —— 不 launch Electron）。
  test.skip(!PROBE_ENABLED, '测量用长跑探针默认跳过：设 MIQI_1036_PROBE=1 才运行');

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 不 patch provider —— 真实模型往返（本地 deepseek / CI siliconflow）。
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
    // 预授权（见文件头）：不预授权时审批卡会倒计时自动拒绝，exec 跑不起来。
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
    console.log(
      '[probe1036] bridge initialized (real provider, no mock); approvals *:* pre-granted'
    );
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('真实长 turn 期间每 30s 打一次 config.get —— settle 用时是否走满超时', async () => {
    // 命中时要等满 720s，project 级 timeout 会先斩断，运行时放大。
    test.setTimeout(WINDOW_MS + 10 * 60_000);

    await createNewConversation(page);

    // 采样器与证据采集都装在页面里。采样必须 fire-and-forget：写成
    // `await page.evaluate(...)` 时 Playwright 会等到这个 Promise settle
    // （命中时要 720s）才返回，节奏就变成 ~750s/次而不是 30s。
    await page.evaluate((sampleMs: number) => {
      const w = window as any;
      w.__cfgSamples = [];
      w.__progressCount = 0;
      w.__lastProgressAt = 0;
      // 事件流的旁证：onProgress 只在 turn 进行中才有事件，用它量出 turn 的
      // 真实存活时长 —— 真实模型的 turn 长度不可控，没这一步就没法区分
      // 「没命中」和「turn 早就结束了」。
      w.__offProgress = w.miqi.chat.onProgress(() => {
        w.__progressCount += 1;
        w.__lastProgressAt = Date.now();
      });
      w.__cfgTimer = setInterval(() => {
        const t0 = Date.now();
        w.miqi.config.get().then(
          (v: unknown) => w.__cfgSamples.push({ ms: Date.now() - t0, gotValue: v != null }),
          (e: unknown) =>
            w.__cfgSamples.push({ ms: Date.now() - t0, gotValue: false, err: String(e) })
        );
      }, sampleMs);
    }, SAMPLE_MS);

    const turnStartedAt = Date.now();
    await sendMessage(page, longTurnPrompt(CMD_SECONDS));

    await page.screenshot({ path: join(OUT_DIR, 'probe1036-00-turn-start.png') });

    // 跑满整个窗口，MIN_SAMPLES 只作下限。
    const deadline = turnStartedAt + WINDOW_MS;
    let nextShot = turnStartedAt + SCREENSHOT_EVERY_MS;
    let samples: CfgSample[] = [];
    while (Date.now() < deadline) {
      samples = (await page.evaluate(() => (window as any).__cfgSamples ?? [])) as CfgSample[];
      await dismissApprovalCards(page);
      if (Date.now() >= nextShot) {
        const atMin = Math.round((Date.now() - turnStartedAt) / 60_000);
        await page.screenshot({
          path: join(OUT_DIR, `probe1036-${String(atMin).padStart(2, '0')}min.png`),
        });
        nextShot = Date.now() + SCREENSHOT_EVERY_MS;
      }
      await page.waitForTimeout(POLL_MS);
    }

    const state = (await page.evaluate(() => {
      const w = window as any;
      clearInterval(w.__cfgTimer);
      w.__offProgress?.();
      return {
        samples: w.__cfgSamples ?? [],
        progressCount: w.__progressCount ?? 0,
        lastProgressAt: w.__lastProgressAt ?? 0,
      };
    })) as ProbeState;
    samples = state.samples;

    const windowS = Math.round((Date.now() - turnStartedAt) / 1000);
    const aliveS = state.lastProgressAt
      ? Math.round((state.lastProgressAt - turnStartedAt) / 1000)
      : 0;
    const unhealthy = samples.filter((s) => s.ms >= HEALTHY_MS);
    const table = samples
      .map((s, i) => `  #${i + 1} ${s.ms}ms ${s.ms >= HEALTHY_MS ? '⚠️ 命中' : 'ok'}`)
      .join('\n');
    const summary =
      `[probe1036] 真实模型 + 真实 exec（无 mock）\n` +
      `[probe1036] 命令时长=${CMD_SECONDS}s 窗口=${windowS}s 样本=${samples.length} ` +
      `命中=${unhealthy.length}\n` +
      `[probe1036] turn 存活=${aliveS}s / 窗口 ${windowS}s，chat 事件 ${state.progressCount} 条\n` +
      `[probe1036] 样本:\n${table || '  （一个样本都没落袋）'}`;
    console.log(summary);

    // 把结论渲染成图（与样本表同源），这样 PR 上的证据不是二手转述。
    await page.evaluate(
      ({ title, body }) => {
        const box = document.createElement('div');
        box.style.position = 'fixed';
        box.style.inset = '0';
        box.style.zIndex = '2147483647';
        box.style.background = '#0b1021';
        box.style.color = '#d7e4ff';
        box.style.overflow = 'auto';
        box.style.padding = '16px 20px';
        box.style.font = '11px/1.5 ui-monospace, Consolas, monospace';

        const heading = document.createElement('div');
        heading.style.font = '600 14px/1.4 system-ui, sans-serif';
        heading.style.color = '#8fb6ff';
        heading.style.marginBottom = '8px';
        heading.textContent = title;

        const pre = document.createElement('pre');
        pre.style.whiteSpace = 'pre-wrap';
        pre.style.margin = '0';
        pre.textContent = body;

        box.append(heading, pre);
        document.body.append(box);
      },
      { title: '#1036 repro probe — real model + real exec', body: summary }
    );
    mkdirSync(OUT_DIR, { recursive: true });
    await page.screenshot({ path: join(OUT_DIR, 'probe1036-verdict.png'), fullPage: true });

    // turn 没活满窗口 ⇒ 本轮无结论（剩下的样本是在「没有 turn 要转发」的
    // 状态下采的），别把这种结果当成「没命中」。
    test.skip(
      aliveS < windowS * MIN_ALIVE_RATIO,
      `本轮无结论：turn 只活了 ${aliveS}s / 窗口 ${windowS}s（模型可能没按提示跑长命令）。` +
        `证据见 probe1036-verdict.png；样本表在用例输出里。`
    );
    test.skip(
      samples.length < MIN_SAMPLES,
      `本轮无结论：只落袋 ${samples.length} 个样本（< ${MIN_SAMPLES}），采样器可能没跑起来`
    );

    // 判据（issue 写死）：用时 < 30s 才算健康；err 恒为 null，不看它。
    expect(
      unhealthy.length,
      `复现了：${unhealthy.length}/${samples.length} 次 config.get 走满超时（判据 ≥ ${HEALTHY_MS}ms）。\n` +
        `${summary}\n—— 这正是 issue 描述的形态；下一步照 issue「待确认」表对着看埋点。`
    ).toBe(0);
  });
});
