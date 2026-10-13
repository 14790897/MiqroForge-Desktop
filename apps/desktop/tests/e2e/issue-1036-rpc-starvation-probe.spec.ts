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
 * ⚠️ 采样记录要**在发起时就落**、settle 时回填：只记 settle 的调用会漏掉最典型的
 * 饿死 —— 饿死从窗口后段开始时那条请求要 720s 才 settle，永远赶不上窗口结束，
 * 于是「样本全健康」判成假阴性。窗口结束时仍未 settle 的，按「已等待时长」计入。
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
 *
 * 判据是**三路合并**的，原因是实测踩到过一次假阴性：注入的采样器那一相位的调用
 * 全部正常（~10ms），被吃掉的却是应用自己另一个相位的 30s 轮询。所以除了注入
 * 采样，还必须读主进程日志里**应用自身**的证据：
 *   1. `IPC <method> took ≥30s`（应用自己观测到的饿死）；
 *   2. `bridge-req written` 存在、但桥侧从未出现该 id 的 `stdin-read`
 *      —— 即 issue「待确认」表第 2 行「卡在管道/读线程侧」，这是最有力的定位指标。
 * 判定顺序也是踩出来的：**先判有没有丢失证据，再谈无结论**。第二轮实测注入采样
 * 一个样本都没落袋（请求全被吃、连 720s 超时都还没到），若先按「样本太少」判
 * skip，就会把最强的命中读成「无结论」。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdirSync, readFileSync, readdirSync, statSync } from 'node:fs';
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
const LOG_DIR = join(APPS_DESKTOP, '..', '..', 'workspace', 'logs');

interface CfgSample {
  /** 发起时刻（epoch ms）。 */
  t0: number;
  /**
   * settle 用时。**在窗口结束时仍未 settle 的记录里，这里是「已经等了多久」**
   * —— 必须这样算，否则会漏掉最典型的饿死：饿死从窗口后段开始时，那条请求
   * 要 720s 才 settle，永远赶不上窗口结束，于是「全部样本健康」→ 假阴性
   * （CodeRabbit 指出的那条）。
   */
  ms: number;
  settled: boolean;
  /** 超时路径也会 resolve（拿到本地兜底配置），所以它只作留痕，不参与判据。 */
  gotValue: boolean;
  err?: string;
}

interface ProbeState {
  samples: CfgSample[];
  /** turn 存活证据：chat 事件条数与最后一次到达的时刻。 */
  progressCount: number;
  lastProgressAt: number;
}

/**
 * 主进程日志里**应用自己**留下的证据 —— 与注入的采样器互补。
 *
 * 为什么必须有这一路：注入采样只能证明「我这一路调用没饿死」。实测第一轮正式
 * 实验里，注入采样（相位 :26/:56）全部 ~10ms 正常，而被吃掉的恰恰是应用自己
 * 另一个相位的 30s 轮询（:00/:30）——只看注入采样会给出**假阴性**。
 */
interface AppEvidence {
  /** 用时 ≥ 判据的 IPC 行（应用自身观测到的饿死）。 */
  slowIpc: string[];
  maxIpcMs: number;
  /** `bridge-req written` 总条数。 */
  written: number;
  /** 主进程写出去了、桥侧却从未 `stdin-read` 的请求 id（issue 表第 2 行）。 */
  unread: string[];
  /**
   * 窗口内「请求没拿到桥的值、静默回退到本地配置」的次数。
   *
   * 关键：**resolve 得快不等于成功**。桥没在跑时 `sendSafe` 立刻 `return null`，
   * `ipc/index.ts` 紧接着返回本地那份 —— 这条路径 10ms 内就 resolve，用时判据完全
   * 看不出来。所以必须单独数这两条日志（本轮 #1036 新加的埋点）：
   *   · `config.get returned no value — serving the local config instead`
   *   · `sendSafe … skipped: bridge not running`
   * 只统计**窗口内**的：应用启动/退出时也会有这类行（那时桥确实没在跑），拿整份
   * 日志数会永远命中。
   */
  fallbackServed: number;
  orphans: number;
  writeFailed: number;
  parseErrors: number;
}

function newestMainLog(): string | null {
  try {
    const files = readdirSync(LOG_DIR)
      .filter((f) => f.startsWith('electron-main-') && f.endsWith('.log'))
      .map((f) => join(LOG_DIR, f))
      .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
    return files[0] ?? null;
  } catch {
    return null;
  }
}

/**
 * 窗口起点的日志位置：**文件名与偏移一起记**。
 *
 * 只记偏移是不够的 —— 文件名按日期取，窗口跨 UTC 日界时主进程会改写到新文件，
 * 那时把旧偏移套到新文件上会跳过新文件开头、又完全读不到旧文件尾部，证据凭空
 * 少一截，探针就可能给出「没复现」的假阴性（其实现场有）。`issue-1036-rpc-trace-
 * real-llm.spec.ts` 的 `readSince` 已是这个写法，这里保持一致。
 */
interface LogCursor {
  file: string;
  offset: number;
}

function openLogCursor(): LogCursor | null {
  const file = newestMainLog();
  return file ? { file, offset: statSync(file).size } : null;
}

/** 游标之后的全部日志：换过文件就把「旧文件尾段 + 新文件全文」拼起来。 */
function readSince(cursor: LogCursor): string {
  const file = newestMainLog() ?? cursor.file;
  const old = readFileSync(cursor.file).subarray(cursor.offset).toString('utf8');
  if (file === cursor.file) return old;
  return old + readFileSync(file).toString('utf8');
}

/** 应用自身证据。`graceMs` 内的请求可能还在飞，不计入 unread。 */ function readAppEvidence(
  cursor: LogCursor | null,
  windowEndAt: number
): AppEvidence {
  const empty: AppEvidence = {
    slowIpc: [],
    maxIpcMs: 0,
    written: 0,
    unread: [],
    fallbackServed: 0,
    orphans: 0,
    writeFailed: 0,
    parseErrors: 0,
  };
  if (!cursor) return empty;
  const text = readSince(cursor);
  const lines = text.split('\n');

  const written = new Map<string, number>(); // id -> written 时刻(ms)
  const read = new Set<string>();
  const slowIpc: string[] = [];
  let maxIpcMs = 0;
  let fallbackServed = 0;
  let orphans = 0;
  let writeFailed = 0;
  let parseErrors = 0;

  for (const line of lines) {
    let m = line.match(/IPC (\S+) took (\d+)ms/);
    if (m) {
      const ms = Number(m[2]);
      if (ms > maxIpcMs) maxIpcMs = ms;
      if (ms >= HEALTHY_MS) slowIpc.push(m[0].trim());
    }
    m = line.match(
      /\[(\S+?Z)\] \[INFO\] \[bridge\] bridge-req written pid=\d+ id=(\S+) method=(\S+)/
    );
    if (m) {
      written.set(m[2], Date.parse(m[1]));
      continue;
    }
    m = line.match(/stdin-read pid=\d+ id=(\S+) len/);
    if (m) {
      read.add(m[1]);
      continue;
    }
    if (line.includes('returned no value — serving the local config')) fallbackServed += 1;
    if (line.includes('skipped: bridge not running')) fallbackServed += 1;
    if (line.includes('bridge-resp orphan')) orphans += 1;
    if (line.includes('bridge-req write-failed')) writeFailed += 1;
    if (line.includes('Error processing stdout line')) parseErrors += 1;
  }

  const unread = [...written.entries()]
    .filter(([id, at]) => !read.has(id) && windowEndAt - at > 60_000)
    .map(([id]) => id);

  return {
    slowIpc: slowIpc.slice(-20),
    maxIpcMs,
    written: written.size,
    unread,
    fallbackServed,
    orphans,
    writeFailed,
    parseErrors,
  };
}

/**
 * 本次 `chat.send` 的（桥 pid, 请求 id）—— 从窗口内第一条 `method=chat.send`
 * 的写入行里抓。
 *
 * 恢复用例必须**按这对标识**等 drain 完成：只等「窗口里出现过 `chat.send drain done`」
 * 会被别的会话/别的回合的 drain 满足，于是「刚 drain 完就发 config.get」这个前提
 * 根本没成立（评审指出的那条）。标识取自 main 侧的写入行（桥自报的 pid + UUID），
 * 再拿去匹配桥侧的完成行，顺带交叉验证了两侧用的是同一个 pid。
 */
function chatSendIdentity(text: string): { pid: string; id: string } | null {
  const m = text.match(/bridge-req written pid=(\d+) id=(\S+) method=chat\.send/);
  return m ? { pid: m[1], id: m[2] } : null;
}

/** 窗口内是否出现了**这一次** chat.send 的 drain 完成行（request 与 pid 都要对上）。 */
function drainedFor(text: string, identity: { pid: string; id: string }): boolean {
  return text
    .split('\n')
    .some(
      (line) =>
        line.includes('chat.send drain done') &&
        line.includes(`request=${identity.id}`) &&
        line.includes(`pid=${identity.pid}`)
    );
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

/** 恢复用例里让模型跑多长（秒）—— 短到能在几分钟内收尾。 */
const RECOVERY_CMD_SECONDS = Number(process.env['MIQI_1036_RECOVERY_CMD_SECONDS'] ?? 120);

test.describe('#1036 RPC starvation probe (real model + real exec)', () => {
  // describe 级 skip：默认整块跳过（含 beforeAll —— 不 launch Electron）。
  test.skip(!PROBE_ENABLED, '测量用长跑探针默认跳过：设 MIQI_1036_PROBE=1 才运行');

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 不 patch provider —— 真实模型往返（本地 deepseek / CI siliconflow）。
    // 只打开顶栏那个「审批绕过」标注：它的文案与显隐都来自 `config.get`，正是
    // 本次修复影响的那条通道 —— 用来验证「后台请求被打断时，用户在界面上会看到
    // 什么」。注意必须写 camelCase（渲染层读的是 `approvals.bypassAll`）。
    const fixture = await launchElectronApp((config: any) => {
      config.approvals = { ...(config.approvals ?? {}), bypassAll: true, bypass_all: true };
    });
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

  // ⚠️ 顺序有意为之：这条**放在长 turn 那条之前**跑，这样它执行时没有别的 turn 在飞，
  // 「drain done」不会来自另一条用例的回合。
  test('turn 结束后立刻发 config.get —— 必须在 30s 内返回（期望行为 3）', async () => {
    // 期望行为 3 的判据：drain 结束后发出的请求必须很快返回。必须**卡在 drain 那一刻**
    // 立刻发 —— 不能靠现成 UI 触发（TopBar 是 30s 周期 + inFlight 去重，人工卡不进窗口）。
    test.setTimeout(10 * 60_000);

    await createNewConversation(page);
    const cursor = openLogCursor();
    await sendMessage(page, longTurnPrompt(RECOVERY_CMD_SECONDS));

    // 先认出**本次** chat.send 的标识（桥 pid + 请求 id），再等它的 drain 完成行。
    // 只等「有 drain done」会被别的会话/回合满足 —— 那样「刚 drain 完就发请求」
    // 这个前提根本没成立（评审指出的那条）。
    const deadline = Date.now() + 7 * 60_000;
    let identity: { pid: string; id: string } | null = null;
    while (Date.now() < deadline && !identity) {
      identity = cursor ? chatSendIdentity(readSince(cursor)) : null;
      if (!identity) await page.waitForTimeout(500);
    }
    expect(
      identity,
      '认不出本次 chat.send 的标识：窗口内没有 method=chat.send 的写入行（它没被发出去？）'
    ).toBeTruthy();

    let drained = false;
    while (Date.now() < deadline) {
      if (cursor && drainedFor(readSince(cursor), identity!)) {
        drained = true;
        break;
      }
      await page.waitForTimeout(1_000);
    }
    test.skip(
      !drained,
      `本轮无结论：等不到本次 chat.send（request=${identity!.id} / pid=${identity!.pid}）的 drain 完成行`
    );

    const t0 = Date.now();
    const value = await page.evaluate(() => (window as any).miqi.config.get());
    const ms = Date.now() - t0;
    console.log(`[probe1036] drain 之后立刻 config.get：${ms}ms`);
    expect(value, 'drain 之后必须真的从桥拿到配置（不是本地兜底）').toBeTruthy();
    expect(ms, '期望行为 3：drain 结束后发出的 config.get 必须在 30s 内返回').toBeLessThan(
      HEALTHY_MS
    );
  });

  test('真实长 turn 期间每 30s 打一次 config.get —— settle 用时是否走满超时', async () => {
    // 命中时要等满 720s，project 级 timeout 会先斩断，运行时放大。
    test.setTimeout(WINDOW_MS + 10 * 60_000);

    await createNewConversation(page);

    // 「用户能看到的那一面」：顶栏的审批绕过标注，显隐与文案都来自 config.get。
    // 请求被饿死时界面回退到本地兜底配置（空）→ 这个标注**整个消失**，用户看到
    // 的是「没有绕过」，而后台其实还开着 —— 这就是 #1036 在界面上唯一可见的差异。
    const bypassChip = page.getByRole('button', { name: /绕过/ });
    expect(
      await bypassChip.count(),
      '前置不成立：顶栏的审批绕过标注没渲染出来，本轮无从验证用户可见性'
    ).toBe(1);

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
        // 先落一条「已发起」记录，settle 时再回填 —— 只记 settle 的调用会漏掉
        // 那些到窗口结束还没 settle 的（= 正在被饿死的）。
        const rec: any = { t0: Date.now(), ms: -1, settled: false, gotValue: false };
        w.__cfgSamples.push(rec);
        w.miqi.config.get().then(
          (v: unknown) =>
            Object.assign(rec, { ms: Date.now() - rec.t0, gotValue: v != null, settled: true }),
          (e: unknown) =>
            Object.assign(rec, { ms: Date.now() - rec.t0, settled: true, err: String(e) })
        );
      }, sampleMs);
    }, SAMPLE_MS);

    // 记下主进程日志的起点：应用自身的证据（慢 IPC / 写了却没被桥读到）只读这段增量。
    const logCursor = openLogCursor();

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
      // 仍未 settle 的按「到此刻已经等了多久」计 —— 这正是被饿死的那批。
      const now = Date.now();
      for (const s of w.__cfgSamples ?? []) {
        if (!s.settled) s.ms = now - s.t0;
      }
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
      .map((s, i) => {
        const tag =
          s.ms < HEALTHY_MS
            ? 'ok'
            : s.settled
              ? '⚠️ 命中'
              : '⚠️ 命中（到窗口结束仍未 settle，按已等待时长计）';
        return `  #${i + 1} ${s.ms}ms ${tag}`;
      })
      .join('\n');

    // 应用自身的证据（注入采样之外的那一路，见 AppEvidence 注释）。
    const app = readAppEvidence(logCursor, Date.now());
    // 同一时刻再确认那次「用户可见的标注」还在不在（见上面 bypassChip 的说明）。
    const chipAtEnd = await bypassChip.count();
    const appSummary =
      `[probe1036] 应用自身证据：bridge-req written ${app.written} 条，` +
      `其中「写了但桥侧从未 stdin-read」${app.unread.length} 条` +
      (app.unread.length
        ? `（${app.unread
            .slice(0, 6)
            .map((i) => i.slice(0, 8))
            .join(', ')}…）`
        : '') +
      `\n[probe1036] 顶栏「审批绕过」标注：开始=1 窗口结束时=${chipAtEnd}` +
      `\n[probe1036] 静默回退到本地配置的次数（窗口内）=${app.fallbackServed}` +
      `\n[probe1036] 最长 IPC=${app.maxIpcMs}ms` +
      (app.slowIpc.length ? `，≥判据的 ${app.slowIpc.length} 条：${app.slowIpc.join(' | ')}` : '') +
      `\n[probe1036] orphan=${app.orphans} write-failed=${app.writeFailed} ` +
      `stdout 解析失败=${app.parseErrors}`;

    const summary =
      `[probe1036] 真实模型 + 真实 exec（无 mock）\n` +
      `[probe1036] 命令时长=${CMD_SECONDS}s 窗口=${windowS}s 样本=${samples.length} ` +
      `命中=${unhealthy.length}\n` +
      `[probe1036] turn 存活=${aliveS}s / 窗口 ${windowS}s，chat 事件 ${state.progressCount} 条\n` +
      `${appSummary}\n` +
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

    // 判据（issue 写死）：用时 < 30s 才算健康；err 恒为 null，不看它。
    // 三路一起判：注入采样 + 应用自身的慢 IPC + 「写了但桥侧从未读到」。
    // 只看注入采样会漏两次（实测都踩到了）：注入那一路可能全绿而被吃的是应用
    // 另一个相位的轮询；也可能**一个样本都不落袋**——那恰恰是注入的请求全部
    // 被吃、连 720s 超时都还没到。所以先判「有没有丢失证据」，再谈无结论。
    const reasons: string[] = [];
    if (unhealthy.length) {
      const pendingHits = unhealthy.filter((s) => !s.settled).length;
      reasons.push(
        `${unhealthy.length}/${samples.length} 次注入采样走满超时` +
          (pendingHits ? `（其中 ${pendingHits} 次到窗口结束时仍未 settle，按已等待时长计）` : '')
      );
    }
    if (app.maxIpcMs >= HEALTHY_MS) {
      reasons.push(`应用自身出现 ${app.maxIpcMs}ms 的 IPC（≥ ${HEALTHY_MS}ms）`);
    }
    if (app.fallbackServed) {
      reasons.push(
        `窗口内有 ${app.fallbackServed} 次请求没拿到桥的值、静默回退到本地配置` +
          `（resolve 得快也算命中：桥没在跑时这条路径 10ms 就返回，用时判据看不出来）`
      );
    }
    if (app.unread.length) {
      reasons.push(
        `${app.unread.length}/${app.written} 条请求「主进程写出去了、桥侧从未读到」` +
          `（issue「待确认」表第 2 行：卡在管道/读线程侧）`
      );
    }
    if (samples.length === 0 && app.unread.length > 0) {
      reasons.push(
        '注入采样的请求一个都没 settle —— 它们本身就落进了上面那批「写了但桥侧从未读到」'
      );
    }
    if (chipAtEnd === 0) {
      reasons.push(
        '顶栏的「审批绕过」标注在长 turn 期间消失了 —— 界面拿到了本地兜底配置，' +
          '用户看到的是一个与后台实际状态不符的界面（#1036 在界面上唯一可见的那一面）'
      );
    }
    expect(
      reasons,
      `复现了：\n  - ${reasons.join('\n  - ')}\n${summary}\n` +
        `—— 这正是 issue 描述的形态（同一通道，一部分有来有回，另一部分石沉大海）。`
    ).toEqual([]);

    // 到这里说明「没有丢失证据」。此时若 turn 没活满窗口、或样本太少，才是无结论。
    test.skip(
      aliveS < windowS * MIN_ALIVE_RATIO,
      `本轮无结论：turn 只活了 ${aliveS}s / 窗口 ${windowS}s（模型可能没按提示跑长命令）。` +
        `证据见 probe1036-verdict.png；样本表在用例输出里。`
    );
    test.skip(
      samples.length < MIN_SAMPLES,
      `本轮无结论：只落袋 ${samples.length} 个样本（< ${MIN_SAMPLES}），采样器可能没跑起来`
    );
  });
});
