/**
 * #1036 传输层埋点端到端验证（真实模型）。
 *
 * 背景（issue #1036）：一个长 turn 里 `config.get` ×17 / `plugins.list` ×3
 * 全部走满 720s 客户端超时，而同一窗口内 `files.read` ×22 被正常应答 ——
 * 同一个通道、同一段时间，一部分请求有来有回，另一部分石沉大海。丢失点只
 * 可能在「main 写出 / 桥收取 / 桥回包 / main 匹配」四段中的某一段，而其中
 * 三段当时**一个字节的日志都不落盘**，所以事故无法定位。
 *
 * 本 spec 不 patch provider（本地 deepseek / CI siliconflow），跑一个真实回合，
 * 然后按**请求 id** 把整条链路串起来，证明每个环节都留痕、且能互相拼接：
 *
 *   bridge-req written  (main 写出，write 回调里)
 *     → stdin-read      (桥读线程取走，含 len/ctr)
 *     → stdin-enqueue   (事件循环 put 完成，含队列深度)
 *     → stdin-recv      (drain loop 取出，含剩余深度)
 *     → dispatch-start  (信号量排队时长 + 槽位占用)
 *     → dispatch-done   (handler 耗时 + 响应字节数)
 *     → bridge-resp sent(回包写 stdout，字节数应与上一条相等)
 *
 * 断言口径（刻意收敛，真实模型文案不可控）：
 *   1. 上述每一类埋点都必须出现，且都带桥进程 pid；
 *   2. 其中**同一条请求 id** 必须在六个桥侧环节里都能找到 —— 这是整条链路
 *      可拼接的硬证据（按方法名对齐会被并发同名请求骗到，必须按 id）；
 *   3. `dispatch-done` 的 `resp_bytes=` 必须等于 `bridge-resp sent` 的
 *      `bytes=`（同一份响应的两次测量必须一致）。
 *
 * 真实模型那一轮不参与断言（provider 抖动时 `sendUntilDoneOrProviderDown`
 * 已经处理）；链路证据来自同一次会话里真实走链路的 `config.get` —— 也就是
 * 事故里被饿死的那个方法本身。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron \
 *      tests/e2e/issue-1036-rpc-trace-real-llm.spec.ts
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import {
  APPS_DESKTOP,
  LLM_TIMEOUT,
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  sendUntilDoneOrProviderDown,
  waitForBridgeInitialized,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
const LOG_DIR = join(REPO_ROOT, 'workspace', 'logs');

/** Newest `electron-main-*.log` (the durable main-process log). */
function newestMainLog(): string | null {
  if (!existsSync(LOG_DIR)) return null;
  const files = readdirSync(LOG_DIR)
    .filter((f) => f.startsWith('electron-main-') && f.endsWith('.log'))
    .map((f) => join(LOG_DIR, f))
    .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  return files[0] ?? null;
}

/** Bytes appended to `file` since `offset` (offset is a whole-line boundary). */
function readAppended(file: string, offset: number): string {
  const buf = readFileSync(file);
  return buf.subarray(Math.min(offset, buf.length)).toString('utf8');
}

async function waitForLog(file: string, offset: number, match: (text: string) => boolean) {
  const deadline = Date.now() + 60_000;
  let text = '';
  while (Date.now() < deadline) {
    text = readAppended(file, offset);
    if (match(text)) return text;
    await new Promise((r) => setTimeout(r, 500));
  }
  return text;
}

/** Every segment a single request must leave a line in, in wire order. */
const TRACE_CHAIN = [
  'bridge-req written',
  'stdin-read',
  'stdin-enqueue',
  'stdin-recv',
  'dispatch-start',
  'dispatch-done',
  'bridge-resp sent',
] as const;

/** Request ids whose whole chain is present in `text`, newest response first. */
function fullyTracedIds(text: string): string[] {
  const lines = text.split('\n');
  const ids: string[] = [];
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!lines[i].includes('bridge-resp sent')) continue;
    // ` id=` with the leading space: a bare /id=/ would also match inside
    // `pid=<n>` (an 8+ digit pid would then be read as the request id).
    const id = lines[i].match(/ id=(\S+)/)?.[1];
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids.filter((id) => {
    const withId = lines.filter((line) => line.includes(`id=${id}`));
    return TRACE_CHAIN.every((prefix) => withId.some((line) => line.includes(prefix)));
  });
}

/** Last `n` lines — keeps a failed assertion readable. */
function tail(text: string, n = 40): string {
  return text.split('\n').slice(-n).join('\n');
}

/** Field value `key=<n>` on the first line containing `prefix`. */
function fieldOf(lines: string[], prefix: string, key: string): string | null {
  const line = lines.find((l) => l.includes(prefix));
  return line?.match(new RegExp(` ${key}=(\\d+)`))?.[1] ?? null;
}

test.describe('#1036 bridge RPC transport trace (real LLM)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 真实 provider（不 patch 配置）——本地走 deepseek，CI 走 siliconflow
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('真实回合 + 按 request id 串起的完整链路埋点', { timeout: LLM_TIMEOUT * 2 }, async () => {
    await waitForBridgeInitialized(page, 60);

    // 只读本次用例新追加的部分：这份日志是追加写、跨 run 共享的。
    const logFile = newestMainLog();
    expect(
      logFile,
      `主进程日志不存在：${LOG_DIR}（E2E 前必须先在仓库根 npm run build，` +
        `且不要在 worktree 里 junction out/）`
    ).toBeTruthy();
    const offset = statSync(logFile!).size;

    // ── 真实模型往返（本 spec 的存在理由之一）────────────────────────
    await createNewConversation(page);
    const marker = `issue1036_${Date.now()}`;
    const replied = await sendUntilDoneOrProviderDown(
      page,
      `请只回复一个词：收到（${marker}）`,
      async () => (await page.getByTestId('chat-message-assistant').count()) > 0
    );
    if (!replied) {
      // provider 抖动不是回归；链路证据仍来自下面的 config.get。
      console.log('[issue1036] no model reply on any attempt — continuing with the channel trace');
    }

    // ── 链路证据：真实走一次事故里被饿死的方法 ────────────────────────
    const cfgValue = await page.evaluate(() => (window as any).miqi.config.get());
    expect(cfgValue, 'config.get 必须真的从桥拿到值（而不是本地兜底）').toBeTruthy();

    const text = await waitForLog(logFile!, offset, (t) => fullyTracedIds(t).length > 0);

    // 1. 每一类埋点都必须出现（桥的启动行除外 —— 它在本窗口之前就发了）
    for (const prefix of TRACE_CHAIN) {
      expect(text, `本次窗口里缺少埋点「${prefix}」：\n${tail(text)}`).toContain(prefix);
    }

    // 2. 至少有一条请求能被**按 id** 完整串起来（按方法名对齐会被并发同名
    //    请求骗到，这正是本 spec 要防的）
    const ids = fullyTracedIds(text);
    expect(ids.length, `没有任何请求走完整条链路：\n${tail(text)}`).toBeGreaterThan(0);

    const joinedId = ids[0];
    const chain = text.split('\n').filter((line) => line.includes(`id=${joinedId}`));

    // 3. 链路两侧的 pid 必须是同一个数。main 侧用的是 ready 握手里桥自报的
    //    pid（Windows 上 venv 的 python.exe 是启动器，child.pid 与之不同），
    //    两边对不上就说明埋点没按同一代桥对齐。
    const bridgePid = fieldOf(chain, 'bridge-resp sent', 'pid');
    expect(bridgePid, `bridge-resp sent 必须带 pid=：\n${chain.join('\n')}`).toBeTruthy();
    for (const line of chain) {
      expect(line, `链路各段必须属于同一代桥（pid=${bridgePid}）：\n${chain.join('\n')}`).toContain(
        `pid=${bridgePid}`
      );
    }
    // 并且这一代桥确实有一条启动行 —— 跨重启的日志串台正是它要防的。
    const fullText = readFileSync(logFile!, 'utf8');
    expect(fullText, `日志里没有 bridge-start pid=${bridgePid} 这一代`).toContain(
      `bridge-start pid=${bridgePid} epoch=`
    );

    // 4. 同一份响应的两次测量必须一致（handler 返回时 / 真正写出时）
    const respBytes = fieldOf(chain, 'dispatch-done', 'resp_bytes');
    const sentBytes = fieldOf(chain, 'bridge-resp sent', 'bytes');
    expect(respBytes, `dispatch-done 必须带 resp_bytes=：\n${chain.join('\n')}`).toBeTruthy();
    expect(sentBytes, `bridge-resp sent 必须带 bytes=：\n${chain.join('\n')}`).toBeTruthy();
    expect(sentBytes).toBe(respBytes);

    // 顺带留证据：这条请求的排队/执行耗时（期望行为 2B 的产出）。
    console.log(
      `[issue1036] request id=${joinedId} pid=${bridgePid}\n` +
        chain
          .filter((l) => /dispatch-start|dispatch-done|bridge-resp sent/.test(l))
          .map((l) => `  ${l.trim()}`)
          .join('\n')
    );

    await page.screenshot({ path: 'test-results/issue1036-app-after-turn.png', fullPage: true });

    // 把这条请求的完整链路渲染成图：日志正文截不到，而这一串正是本次交付的
    // 东西（PR 截图节用它）。样式全部走 CSSOM —— 应用页有 CSP，style 属性
    // 会被拦，`el.style.x = …` 不会。
    await page.evaluate(
      ({ title, body }) => {
        const box = document.createElement('div');
        box.style.position = 'fixed';
        box.style.inset = '0';
        box.style.zIndex = '2147483647';
        box.style.background = '#0b1021';
        box.style.color = '#d7e4ff';
        box.style.overflow = 'auto';
        box.style.padding = '18px 22px';
        box.style.font = '12px/1.6 ui-monospace, Consolas, monospace';

        const heading = document.createElement('div');
        heading.style.font = '600 15px/1.4 system-ui, sans-serif';
        heading.style.color = '#8fb6ff';
        heading.style.marginBottom = '10px';
        heading.textContent = title;

        const pre = document.createElement('pre');
        pre.style.whiteSpace = 'pre-wrap';
        pre.style.margin = '0';
        pre.textContent = body;

        box.append(heading, pre);
        document.body.append(box);
      },
      {
        title: `#1036 transport trace — one request, joined by request id (pid=${bridgePid})`,
        body: chain.map((l) => l.trim()).join('\n'),
      }
    );
    await page.screenshot({ path: 'test-results/issue1036-transport-trace.png' });
  });
});
