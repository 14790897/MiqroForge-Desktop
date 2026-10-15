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
 * 临时 MIQI_HOME 的收尾策略,一处定义。
 *
 * CI 用 MIQI_E2E_KEEP_HOME=1 留住它 —— 「Bridge ingest anomalies」「Dump bridge logs」
 * 两步读的正是 `<home>/workspace/logs/bridge-*.log`,无条件删掉等于把它们变成空转
 * (这个 spec 的失败现场就在那份日志里)。此前这段策略在三条 afterAll/finally 里各写
 * 一遍、写法还不一致,改一处漏一处。
 */
function disposeMiqiHome(miqiHome: string): void {
  if (!miqiHome) return;
  if (process.env.MIQI_E2E_KEEP_HOME) {
    console.log(`[test] MIQI_E2E_KEEP_HOME=1,保留 ${miqiHome}`);
    return;
  }
  cleanupDir(miqiHome);
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
 * 本轮 app 实际执行过哪些工具、成功与否 —— 取自桥接日志里 orchestrator 的执行记录
 * (miqi/execution/orchestrator.py):
 *   成功 → `Tool <name> done (Nms): result prefix=…`(DEBUG)
 *   失败 → `Tool <name> execution failed (Nms): …`(WARNING)
 *
 * 为什么不用 UI 文本:工具卡标题是 `工具调用 · N`(多调用折叠态),而「写入文件」这类
 * 字样恰恰会出现在**模型回复正文**里(2026-10-14 本机实测:正文「已将 … 写入文件：」
 * 骗过了标签匹配)。日志里的执行记录才是 app 真做了什么。
 *
 * 为什么区分成败:词表内工具**执行失败**时,桥接按设计只发 requested、不发 completed
 * (sure_hooks.on_post_tool_use 对 status != SUCCESS 直接 return)——那不是回归;
 * 成功执行却没有 completed 才是。
 *
 * 只扫 `<home>/workspace/logs`:E2E 的临时 home 没有活跃账号(launchElectronApp 不写
 * accounts/.active、且 MIQI_LOGIN_BYPASS=1),workspace 解析恒为旧布局 —— 与 CI 两个
 * 读日志的步骤同一个 glob。若将来布局变了,返回的 logsFound 会是 0,调用方据此**判红**
 * 而不是跳过(见下方守卫),不会静默失明。
 */
function readExecutedTools(miqiHome: string): {
  logsFound: number;
  ok: Set<string>;
  failed: Set<string>;
} {
  const dir = join(miqiHome, 'workspace', 'logs');
  let files: string[] = [];
  try {
    files = readdirSync(dir).filter(
      (entry) => entry.startsWith('bridge-') && entry.endsWith('.log')
    );
  } catch {
    // 目录不存在 → logsFound 0,由调用方判红
  }
  const ok = new Set<string>();
  const failed = new Set<string>();
  let readable = 0;
  for (const entry of files) {
    let content = '';
    try {
      content = readFileSync(join(dir, entry), 'utf8');
    } catch {
      continue; // 读不了这份就跳过;一份都读不到时 logsFound 为 0 → 判红
    }
    readable += 1;
    for (const m of content.matchAll(/Tool ([A-Za-z0-9_]+) done \(/g)) ok.add(m[1]);
    for (const m of content.matchAll(/Tool ([A-Za-z0-9_]+) execution failed \(/g)) failed.add(m[1]);
  }
  return { logsFound: readable, ok, failed };
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
    disposeMiqiHome(miqiHome);
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
      const executed = readExecutedTools(miqiHome);
      // 读不到日志 ≠ 链路干净:判不了就不许按「前提不成立」滑过去(否则日志格式一改,
      // 真回归会被静默降级成 skip)。
      if (executed.logsFound === 0) {
        throw new Error(
          '读不到本轮的桥接日志(<MIQI_HOME>/workspace/logs/bridge-*.log),无法判定这轮是否发生过桥接——不按「用例前提不成立」跳过'
        );
      }
      const okVocabulary = [...executed.ok].filter((tool) => VOCABULARY.includes(tool));
      if (okVocabulary.length > 0) {
        throw new Error(
          `应用成功执行了词表内工具(${okVocabulary.join(', ')}),但真实 SURE 记录里没有 tool.completed——疑似桥接回归`
        );
      }
      const failedVocabulary = [...executed.failed].filter((tool) => VOCABULARY.includes(tool));
      if (failedVocabulary.length > 0) {
        // 执行失败的工具按设计只发 requested:这一轮的 completed 断言前提不成立。
        test.skip(
          true,
          `词表内工具执行失败(${failedVocabulary.join(', ')})——按设计只发 requested 不发 completed,用例前提不成立`
        );
      }
      test.skip(
        true,
        executed.ok.size + executed.failed.size > 0
          ? `真实模型本回合只执行了词表外工具(${[...executed.ok, ...executed.failed].join(', ')})——按设计不上报,用例前提不成立`
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
