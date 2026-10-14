/**
 * 阶段 5 事件桥接 E2E —— 跨平台;**AI 与 SURE 都是真实的**(评审要求:不 mock)。
 *
 * 在真实 Electron 应用里把整条观测链路跑通:
 *   对话(真实模型)→ 模型自主调用工具(词表内)→ 编排器执行
 *   → sure_hooks 钩子 → spawn `sure hook ingest --source miqi`(真实二进制)
 *   → 事件落真实记录库 → `sure history show` 读回断言。
 *
 * 前置不满足即 skip(绝不 mock 顶替、绝不产假绿):
 *   - SURE:查找链 env `SURE_BIN` → PATH → 平台默认安装位置;且 `sure doctor`
 *     必须列出 miqi(官方 0.1.2 不支持 `hook ingest --source miqi` 的 fork 构建才列);
 *   - AI:本机经平台账号网关(spec 自动把真实工作区的 `.qraft/token.json`
 *     凭据复制进本轮隔离工作区);CI 无该文件时走注入的 config.json provider。
 *
 * 防护面(评审「不然出问题不知道」的补测):
 *   - 钩子未装配(双 HookRuntime 那个装配 bug 回归)→ UI 有工具调用、记录库零事件;
 *   - 词表过滤回归 → 记录出现词表外工具名;
 *   - 完成误记 → 无 tool.completed;
 *   - 最小采集回归 → 工具请求记录携带参数/内容字段。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron sure-event-bridge
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { readFileSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  PROVIDER_UNAVAILABLE_TEXT,
  sendUntilDoneOrProviderDown,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { findRealSureBin, runSureJson, sureSupportsMiqi } from './helpers/sure-binary';
import { seedGatewayCredentials } from './helpers/real-ai';

const VOCABULARY = ['exec', 'read_file', 'write_file', 'edit_file', 'apply_patch'];

/** 自建临时目录清理:Windows 下刚退出的子进程句柄可能瞬时占用,重试+容忍。 */
function cleanupDir(dir: string): void {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  } catch {
    // TEMP 里的残留由系统兜底;清理失败绝不连累用例结果
  }
}

/**
 * 从记录库的会话列表里认出**本轮**的会话:按 project_root 匹配本轮临时 home。
 *
 * 为什么不能用「不在 baseline 里、且 started_at 够新」:并发下(CI 的 4 workers、
 * 本地 --repeat-each)同一记录库里同时存在多个「新鲜」会话 —— acceptance 的
 * check/repair/recheck 也在建会话。弱条件会捞到别人的:轻则归属断言失败
 * (project_root 对不上本轮 home),重则守卫误报「疑似桥接回归」(别人的会话里
 * 没有 tool.completed)。2026-10-14 本地 3 workers × 9 次复现:6 次失败,全栽在
 * 这里;CI 上那条反复出现的 flaky 也是同一个成因。
 */
function isOurSession(session: unknown, homeName: string): boolean {
  const root = String((session as { project_root?: unknown } | null)?.project_root ?? '');
  return homeName.length > 0 && root.toLowerCase().includes(homeName.toLowerCase());
}

/**
 * 本轮 app 实际执行过哪些工具 —— 取自桥接日志里 orchestrator 的执行记录
 * (`Tool execute: name=<tool>`)。
 *
 * 为什么不用 UI 文本:工具卡的标题是 `工具调用 · N`(多调用折叠态),而
 * 「写入文件」这类字样恰恰会出现在**模型回复正文**里(2026-10-14 本机实测:
 * 正文「已将 … 写入文件：」把标签匹配骗过了),用文本判会在该静默时判红。
 * 日志里的执行记录才是 app 真做了什么。
 *
 * 两种 home 布局都扫:旧 `<home>/workspace/logs`,新 `<home>/accounts/<id>/workspace/logs`
 * (getWorkspacePath,见 src/main/ipc/workspace-path.ts)。
 */
function readExecutedTools(miqiHome: string): string[] {
  const files: string[] = [];
  const collect = (dir: string) => {
    try {
      for (const entry of readdirSync(dir)) {
        if (entry.startsWith('bridge-') && entry.endsWith('.log')) files.push(join(dir, entry));
      }
    } catch {
      // 该布局不存在,跳过
    }
  };
  collect(join(miqiHome, 'workspace', 'logs'));
  try {
    for (const account of readdirSync(join(miqiHome, 'accounts'))) {
      collect(join(miqiHome, 'accounts', account, 'workspace', 'logs'));
    }
  } catch {
    // 旧布局没有 accounts
  }
  const names = new Set<string>();
  for (const file of files) {
    let content = '';
    try {
      content = readFileSync(file, 'utf8');
    } catch {
      continue;
    }
    for (const m of content.matchAll(/Tool execute: name=([A-Za-z0-9_]+)/g)) names.add(m[1]);
  }
  return [...names];
}

/**
 * 本机真实工作区里的网关凭据复用(见 helpers/real-ai.ts):
 * E2E 的临时工作区是空的,复制这份文件进去,真实模型即可经平台网关调用。
 */
test.describe.serial('SURE 事件桥接 E2E · 对话工具调用 → 真实 SURE 落库', () => {
  let electronApp: ElectronApplication | null = null;
  let page: Page;
  let miqiHome = '';
  /** 本轮临时 home 的末段 —— 用来在 SURE 记录库的多个会话里认出**自己**那轮(见 isOurSession)。 */
  let homeName = '';
  let sureBin: string | null = null;
  /** 本轮新建的 SURE 会话 id(断言与清理共用)。 */
  let sureSessionId: string | null = null;

  test.beforeAll(async () => {
    sureBin = findRealSureBin();
    if (sureBin && !sureSupportsMiqi(sureBin)) sureBin = null; // 官方构建不支持 miqi
    if (!sureBin) return; // 用例里 skip;不启动 app,省一整轮启动开销

    // AI 走真实模型:不 patch、不 mock。本机模型经「平台账号 AI 网关」——
    // launch 后把真实工作区的网关凭据复制进本轮临时工作区(见 helpers/real-ai.ts);
    // CI 上找不到 → 不注入,走注入的 config.json provider。
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    homeName = miqiHome.split(/[\\/]/).filter(Boolean).pop() ?? '';
    seedGatewayCredentials(miqiHome);
    await waitForBridgeInitialized(page, 30);
    await createNewConversation(page);
  });

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp);
    // 与「完整修复闭环」那条一致:Bridge ingest anomalies / Dump bridge logs 两步都要
    // 读 <MIQI_HOME>/workspace/logs/bridge-*.log,而无条件删掉临时 home 等于把它们
    // 变成空转 —— 本 spec 的失败恰恰是「UI 有工具卡但没有 tool.completed」,
    // 现场就在那份日志里。CI 由工作流设 MIQI_E2E_KEEP_HOME=1。
    if (miqiHome) {
      if (process.env.MIQI_E2E_KEEP_HOME)
        console.log(`[test] MIQI_E2E_KEEP_HOME=1,保留 ${miqiHome}`);
      else cleanupDir(miqiHome);
    }
    // 清理本轮测试写进真实记录库的会话(尽力;失败只记日志不连累用例)
    if (sureBin) {
      if (!sureSessionId) {
        const now = runSureJson(sureBin, ['history', 'list']);
        const fresh = ((now?.details?.sessions ?? []) as { sure_session_id: string }[]).find((s) =>
          isOurSession(s, homeName)
        );
        if (fresh) sureSessionId = String(fresh.sure_session_id);
      }
      if (sureSessionId) {
        const del = runSureJson(sureBin, ['history', 'delete', '--session', sureSessionId]);
        console.log(`[test] 清理测试会话 ${sureSessionId}: outcome=${del?.outcome ?? '<无输出>'}`);
      }
    }
  });

  test('工具调用 → 真实 SURE 记录(词表工具、决策、归属正确)', async () => {
    if (!sureBin) {
      test.skip(
        true,
        '未找到含 miqi 集成的真实 SURE(设 SURE_BIN 指向 fork 构建;官方 0.1.2 不支持 hook ingest)——E2E 不 mock 外部依赖'
      );
    }

    // 轮询我们真正关心的事实:记录库里出现本轮新会话,且其中已有 tool.completed
    const pollCompleted = (): boolean => {
      const now = runSureJson(sureBin!, ['history', 'list']);
      const fresh = ((now?.details?.sessions ?? []) as any[]).find((s) =>
        isOurSession(s, homeName)
      );
      if (!fresh) return false;
      sureSessionId = String(fresh.sure_session_id);
      const shown = runSureJson(sureBin!, ['history', 'show', sureSessionId]);
      return ((shown?.details?.events ?? []) as any[]).some(
        (e) => e.event_type === 'tool.completed'
      );
    };

    // 真实模型的回合没有确定性时长(首字迟、限流都会发生)——直接等上面的事实;
    // provider 错误触发重发,静默窗口超时按 real-llm 用例的既有惯例放弃。
    // 提示词显式点名 write_file:桥接只上报词表内工具,模型若改用 list_dir 之类
    // 词表外工具(按设计静默不上报),这一轮的断言前提就不成立了。
    const done = await sendUntilDoneOrProviderDown(
      page,
      '请把 "bridge-probe" 这行文字写入工作区中的文件 sure-bridge-probe.txt,完成后告诉我。' +
        '(直接用 write_file 工具写,不要先列目录、也不要先读别的文件)',
      async () => pollCompleted(),
      { perAttemptWaitMs: 150_000, silenceExtendMs: 150_000 }
    );
    if (!done) {
      if ((await page.getByText(PROVIDER_UNAVAILABLE_TEXT).count()) > 0) {
        test.skip(true, 'provider 不可用,被测回合未运行');
      }
      // 这里必须把两类「UI 有工具卡但没有 completed」分开,否则守卫会误报:
      //   ① 执行了词表内工具(exec/read_file/write_file/edit_file/apply_patch)却没上报
      //      → 真回归,该红;
      //   ② 这轮只执行了词表外工具 → sure_hooks 的 on_pre/post_tool_use 对非词表工具
      //      **直接 return,不打日志不计数**,UI 照样出工具卡 —— 按设计就是不上报,
      //      是前提不成立,不是回归。
      // 判据取 app 的**实际执行记录**(readExecutedTools 读桥接日志),不取 UI 文本:
      // 本机实测过文本判据会被模型回复正文骗过(正文写「已将 … 写入文件：」,而工具卡
      // 只显示「工具调用 · N」)—— 那样守卫会在该静默时判红、在该红时静默,两头都错。
      //
      // 注:2026-10-14 CI 上反复出现的那条「UI 有工具卡但没有 tool.completed」,事后
      // 查明**不是**这里的 ②,而是 spec 认错了会话(并发下按「新鲜度」捞到了 acceptance
      // 的会话,里面本来就没有 tool.completed)——见 isOurSession。② 仍是一个真实且独立
      // 的误报源(模型只用词表外工具时按设计静默),所以这个判据保留。
      const executedTools = readExecutedTools(miqiHome);
      const vocabularyExecuted = executedTools.filter((tool) => VOCABULARY.includes(tool));
      if (vocabularyExecuted.length > 0) {
        throw new Error(
          `应用执行了词表内工具(${vocabularyExecuted.join(', ')}),但真实 SURE 记录里没有 tool.completed——疑似桥接回归`
        );
      }
      test.skip(
        true,
        executedTools.length > 0
          ? `真实模型本回合只执行了词表外工具(${executedTools.join(', ')})——按设计不上报,用例前提不成立`
          : '真实模型本回合未调用工具——用例前提不成立'
      );
    }

    // 读回本轮会话全文并断言
    const shown = runSureJson(sureBin!, ['history', 'show', sureSessionId!]);
    expect(shown?.outcome).toBe('ok');
    const session = (shown?.details?.session ?? {}) as Record<string, unknown>;
    const events = (shown?.details?.events ?? []) as any[];

    // 归属:harness=miqi;项目根是本轮会话工作区(临时 MIQI_HOME 下,中文路径逐字保真)。
    // homeName 由 beforeAll 设好,与 isOurSession 用的是同一个(别再在用例体里重声明 ——
    // 那会遮蔽外层绑定,让定义在前的 pollCompleted 落进 TDZ 直接抛 ReferenceError)。
    expect(session.harness).toBe('miqi');
    expect(session.project_root).toBeTruthy();
    expect(homeName.length).toBeGreaterThan(0);
    expect(String(session.project_root).toLowerCase()).toContain(homeName.toLowerCase());

    // 事件:开始→请求→完成齐备;capability 如实为 observed(tier 1)
    const started = events.filter((e) => e.event_type === 'session.started');
    const requested = events.filter((e) => e.event_type === 'tool.requested');
    const completed = events.filter((e) => e.event_type === 'tool.completed');
    expect(started.length, JSON.stringify(events)).toBeGreaterThanOrEqual(1);
    expect(requested.length).toBeGreaterThanOrEqual(1);
    expect(completed.length).toBeGreaterThanOrEqual(1);
    for (const e of [...started, ...requested, ...completed]) {
      expect(e.capability_tier).toBe('observed');
    }

    // 词表过滤:请求记录的工具必须在词表内(SURE 里工具名记在 requested 的
    // decision 对象上;completed 记录不含工具名)
    for (const e of requested) {
      expect(VOCABULARY, JSON.stringify(e)).toContain(String(e.decision?.tool));
    }
    expect(requested.length).toBeGreaterThanOrEqual(completed.length);
    // 决策被如实记录(allow 或 block 均可——本机 SURE 默认保护模式会把
    // write_file 拦为 block;桥接是 Tier 1 观测通道,MiQi 侧照常执行,
    // 这里只断言「SURE 给出了决策且没有丢失」)
    expect(
      requested.some((e) => e.decision && String(e.decision.decision).length > 0),
      JSON.stringify(requested)
    ).toBe(true);

    // 最小采集:工具请求记录没有任何参数/内容字段
    for (const e of requested) {
      const keys = Object.keys(e);
      for (const forbidden of ['arguments', 'args', 'content', 'command']) {
        expect(keys).not.toContain(forbidden);
      }
    }

    // 保序:started 不晚于 requested,requested 不晚于 completed(ISO 时间戳字典序)
    const ts = (e: { timestamp: string }) => String(e.timestamp);
    const minTs = (arr: { timestamp: string }[]) => arr.map(ts).sort()[0];
    const maxTs = (arr: { timestamp: string }[]) => arr.map(ts).sort().at(-1) ?? '';
    expect(minTs(started) <= minTs(requested)).toBe(true);
    expect(minTs(requested) <= maxTs(completed)).toBe(true);

    await page.screenshot({ path: 'test-results/sure-bridge-events.png', fullPage: true });
  });
});
