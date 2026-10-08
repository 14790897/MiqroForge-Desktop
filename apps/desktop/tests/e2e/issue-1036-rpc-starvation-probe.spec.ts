/**
 * #1036 复现探针 —— **判断「这个问题是否存在」的用例**，不是回归断言。
 *
 * 维护者要求先有一个能回答「这问题到底存不存在」的测试用例，再谈修法。issue
 * 自己也给了最小复现（复现步骤第 2 条）并注明「未验证，请先跑通再当判据用」。
 * 本 spec 就是那个用例，判据按 issue 写死：
 *
 *   长 turn 期间每 SAMPLE_MS 发一次 `config.get`（页面内 fire-and-forget
 *   `setInterval`，与 TopBar 的调用链完全相同），**settle 用时 ≥ 30s 即为命中**。
 *
 * ⚠️ 判据只能看**用时**，不能看 `err`：超时路径上渲染侧只会 resolve 并拿到本地
 * 兜底配置（`sendSafe` 捕获后 `return null` 不 rethrow，`ipc/index.ts` 接着
 * `return bridgeConfig ?? readLocalConfig()`，而 `readLocalConfig` 从不抛），
 * 所以 `onRejected` 分支永不触发、`err` 恒为 null —— 写成「err 形如 timed out」
 * 会永远判不出命中。
 *
 * ⚠️ 也必须 fire-and-forget（sample 数组 + 轮询其长度，而不是 await 一个
 * 720s 的 Promise），否则采样节奏会变成 ~750s/次而不是 SAMPLE_MS。
 *
 * 与 issue 原稿的一处**必要偏离**：issue 的骨架用 `mock_hang.py` 让 turn 挂着，
 * 但那样 turn 是**事件静默**的。事故现场已经排除「桥忙不过来」（同一窗口内
 * `files.read` 至少 22 次被正常应答），所以事件静默的挂起 turn 复现不了它。
 * 本用例默认改用 `scripts/mock_stream_forever.py`：真实 SSE 增量流，持续
 * 900s、30 deltas/s —— 与事故的形态一致（58 分钟 turn / 111,982 条 reasoning
 * chunk ≈ 32/s），既让 turn 长时间存活，也让桥→main→渲染层这条链路一直有活。
 * 想跑 issue 原稿那一版就用 `MIQI_1036_MOCK=mock_hang.py`。
 *
 * ⚠️ **默认跳过**：命中时要等满 720s×N，是长跑测量，不进常规 e2e / CI 套件
 * （与 issue-1034-renderer-oom-probe.spec.ts 同一模式，见 CI-COVERAGE.md）。
 * 只有显式设了 `MIQI_1036_PROBE=1` 才运行。
 *
 * 运行：
 *   cd apps/desktop && npm run build
 *   MIQI_1036_PROBE=1 npx playwright test --config=playwright.config.ts \
 *     --project=electron --workers=1 \
 *     tests/e2e/issue-1036-rpc-starvation-probe.spec.ts
 *
 * 可调环境变量：MIQI_1036_PROBE（=1 才跑）/ _MOCK / _STREAM_SECONDS / _RATE /
 * _SAMPLE_MS / _SAMPLES（下限，不是停止条件）/ _WINDOW_MS（主旋钮：采样跑满
 * 这个窗口才下结论，因为「没命中」的证据强度只由窗口长度决定）
 *
 * 判读：**用例通过 = 本次窗口内没复现出饿死；用例失败 = 复现了**（失败消息里带
 * 每个样本的用时）。结论必须连 `窗口长度 / 样本数 / 事件速率` 一起报——20 分钟
 * 没命中和 58 分钟没命中，证据强度不同。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
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

/** 探针开关：默认关闭（见文件头）。 */
const PROBE_ENABLED = process.env['MIQI_1036_PROBE'] === '1';

const MOCK_SCRIPT = process.env['MIQI_1036_MOCK'] ?? 'mock_stream_forever.py';
const STREAM_SECONDS = Number(process.env['MIQI_1036_STREAM_SECONDS'] ?? 900);
const STREAM_RATE = Number(process.env['MIQI_1036_RATE'] ?? 30);
const SAMPLE_MS = Number(process.env['MIQI_1036_SAMPLE_MS'] ?? 30_000);
const TARGET_SAMPLES = Number(process.env['MIQI_1036_SAMPLES'] ?? 3);
/** issue 的判据：一次 `config.get` settle 用时 ≥ 30s 即不健康（超时需要 720s）。 */
const HEALTHY_MS = 30_000;
/** 采样窗口上限。命中一次至少要等满 720s，所以要留够 TARGET×720s 的余量。 */
const WINDOW_MS = Number(process.env['MIQI_1036_WINDOW_MS'] ?? 25 * 60_000);
const POLL_MS = 1_000;

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
const LOG_DIR = join(REPO_ROOT, 'workspace', 'logs');

interface CfgSample {
  ms: number;
  /** 超时路径也会 resolve（拿到本地兜底配置），所以这个字段只是留痕，不参与判据。 */
  gotValue: boolean;
}

/** 把所有 provider 指向 mock，并把默认模型钉到 mock 认的模型名上。 */
function patchProvidersToMock(config: any, mockUrl: string): void {
  const providers = config.providers ?? {};
  for (const [, p] of Object.entries(providers)) {
    if (p && typeof p === 'object') {
      (p as any).apiBase = mockUrl;
      if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
    }
  }
  // mock 的 /v1/models 只认 mock-model，但 provider 解析走的是 agents.defaults
  // 里的模型名；apiBase 已被改指 mock，模型名不再影响路由，保持原值即可。
  config.agents = config.agents ?? {};
  config.agents.defaults = config.agents.defaults ?? {};
  config.providers = providers;
}

/** 最新的主进程日志（追加写、跨 run 共享，所以只取本次窗口的增量）。 */
function mainLogSize(): number {
  if (!existsSync(LOG_DIR)) return 0;
  const files = readdirSync(LOG_DIR)
    .filter((f) => f.startsWith('electron-main-') && f.endsWith('.log'))
    .map((f) => join(LOG_DIR, f))
    .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  return files[0] ? statSync(files[0]).size : 0;
}

function logTail(bytes: number) {
  const files = readdirSync(LOG_DIR)
    .filter((f) => f.startsWith('electron-main-') && f.endsWith('.log'))
    .map((f) => join(LOG_DIR, f))
    .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  if (!files[0]) return '';
  const buf = readFileSync(files[0]);
  return buf.subarray(Math.max(0, buf.length - bytes)).toString('utf8');
}

// 长跑：关掉录屏/截图/trace，附属产物既拖慢也占磁盘。
// 必须在文件顶层（放进 describe 里 Playwright 会报 "forces a new worker"）。
test.use({ video: 'off', screenshot: 'off', trace: 'off' });

test.describe('#1036 RPC starvation probe (measurement only)', () => {
  // describe 级 skip：默认整块跳过（含 beforeAll —— 不起 mock、不 launch）。
  test.skip(!PROBE_ENABLED, '测量用长跑探针默认跳过：设 MIQI_1036_PROBE=1 才运行');

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    // 时长/速率经 env 传给 mock（startMockServer 会把 process.env 转发下去）。
    process.env['MIQI_MOCK_STREAM_SECONDS'] = String(STREAM_SECONDS);
    process.env['MIQI_MOCK_STREAM_RATE'] = String(STREAM_RATE);
    const mock = await startMockServer(MOCK_SCRIPT);
    mockServer = mock.proc;
    console.log(`[probe1036] mock=${MOCK_SCRIPT} url=${mock.mockUrl}`);

    const fixture = await launchElectronApp((config: any) => {
      patchProvidersToMock(config, mock.mockUrl);
      config.tools = { ...config.tools, sandbox: { ...config.tools?.sandbox, enabled: false } };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
    console.log('[probe1036] bridge initialized');
  });

  test.afterAll(async () => {
    mockServer?.kill();
    await closeElectronApp(electronApp, miqiHome);
  });

  test('长 turn 期间每 30s 打一次 config.get —— settle 用时是否走满超时', async () => {
    // 命中时要等满 720s×N，project 级 timeout 会先斩断，运行时放大。
    test.setTimeout(WINDOW_MS + 5 * 60_000);

    const logBytesBefore = mainLogSize();

    await createNewConversation(page);
    const marker = `rpc 饿死复现 ${Date.now()}`;
    await sendMessage(page, marker);

    // 采样器与证据采集都装在页面里。采样必须 fire-and-forget：写成
    // `await page.evaluate(...)` 时 Playwright 会等到这个 Promise settle
    // （命中时要 720s）才返回，节奏就变成 ~750s/次而不是 30s。
    await page.evaluate((sampleMs: number) => {
      const w = window as any;
      w.__cfgSamples = [];
      w.__progressCount = 0;
      // 事件流的旁证：onProgress 只在 turn 存活期间有事件，用它证明这次实验
      // 不是「事件静默的挂起 turn」（那种形态复现不了本 bug）。
      w.__offProgress = w.miqi.chat.onProgress(() => {
        w.__progressCount += 1;
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

    // 攒够样本**不是**停止条件 —— 判读要的是「这个窗口里有没有命中」，健康
    // 样本落袋很快（实测 ~10ms），攒够就停会让结论退化成「60 秒没命中」。
    // 所以跑满整个窗口，TARGET_SAMPLES 只作下限。
    const deadline = Date.now() + WINDOW_MS;
    let samples: CfgSample[] = [];
    while (Date.now() < deadline) {
      samples = (await page.evaluate(() => (window as any).__cfgSamples ?? [])) as CfgSample[];
      await page.waitForTimeout(POLL_MS);
    }

    const observed = (await page.evaluate(() => {
      const w = window as any;
      clearInterval(w.__cfgTimer);
      w.__offProgress?.();
      return { samples: w.__cfgSamples ?? [], progress: w.__progressCount ?? 0 };
    })) as { samples: CfgSample[]; progress: number };
    samples = observed.samples;

    const elapsedS = Math.round((Date.now() - deadline + WINDOW_MS) / 1000);
    const growthKb = Math.round((mainLogSize() - logBytesBefore) / 1024);
    const table = samples
      .map((s, i) => `  #${i + 1} ${s.ms}ms ${s.ms >= HEALTHY_MS ? '⚠️ 命中' : 'ok'}`)
      .join('\n');

    console.log(
      `[probe1036] mock=${MOCK_SCRIPT} stream=${STREAM_SECONDS}s@${STREAM_RATE}/s ` +
        `sample=${SAMPLE_MS}ms\n` +
        `[probe1036] 采样窗口=${elapsedS}s 样本=${samples.length}\n` +
        `[probe1036] 事件旁证: onProgress=${observed.progress} 条, 主进程日志 +${growthKb}KB\n` +
        `[probe1036] 样本:\n${table || '  （一个样本都没落袋）'}`
    );

    // 判据（issue 写死）：用时 < 30s 才算健康；err 恒为 null，不看它。
    const unhealthy = samples.filter((s) => s.ms >= HEALTHY_MS);
    expect(
      samples.length,
      `一个样本都没落袋 —— 采样器没跑起来（不是「没命中」）：\n${logTail(4000)}`
    ).toBeGreaterThan(0);
    expect(
      unhealthy.length,
      `复现了：${unhealthy.length}/${samples.length} 次 config.get 走满超时（判据 ≥ ${HEALTHY_MS}ms）。\n` +
        `窗口=${elapsedS}s 事件=${observed.progress} 条\n${table}\n` +
        `—— 这正是 issue 描述的形态；下一步照 issue「待确认」表对着看埋点。`
    ).toBe(0);
  });
});
