/**
 * 阶段 5 事件桥接 E2E —— 跨平台;**AI 用真实模型**(评审要求:不 mock AI)。
 *
 * 在真实 Electron 应用里把整条观测链路跑通:
 *   对话(真实模型)→ 模型自主调用工具(词表内)→ 编排器执行
 *   → sure_hooks 钩子 → spawn `sure hook ingest --source miqi` → 落盘断言。
 *
 * SURE_BIN 指向本 spec 生成的包装脚本(win: .cmd / posix: sh),内部以
 * `python scripts/mock_sure_ingest.py <捕获文件>` 冒充 sure,把 ingest 的
 * stdin 事件逐条追加到捕获文件——**只 mock 环境(CI 上没有 SURE 安装),
 * 不 mock AI**。真实模型不可用(限流/过载)或本回合没用工具时:按
 * real-llm 用例的既有惯例 skip,绝不产出假绿。
 *
 * 防护面(评审「不然出问题不知道」的补测):
 *   - 钩子未装配(双 HookRuntime 那个装配 bug 回归)→ UI 有工具调用、捕获零事件;
 *   - 最小采集回归 → 载荷出现 args/command/content;
 *   - 完成误记 → 无 PostToolUse 或 result 非 ok;
 *   - 词表过滤回归 → 出现词表外工具名。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron sure-event-bridge
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  PROVIDER_UNAVAILABLE_TEXT,
  sendUntilDoneOrProviderDown,
  waitForBridgeInitialized,
} from './helpers/electron-setup';
import { resolveMockPython } from './helpers/mock-server';

const REPO_ROOT = resolve(__dirname, '..', '..', '..', '..');
const MOCK_SURE_INGEST = join(REPO_ROOT, 'scripts', 'mock_sure_ingest.py');

/** 自建临时目录清理:Windows 下刚退出的子进程句柄可能瞬时占用,重试+容忍。 */
function cleanupDir(dir: string): void {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  } catch {
    // TEMP 里的残留由系统兜底;清理失败绝不连累用例结果
  }
}

/** 生成包装脚本:SURE_BIN 指向它;`sure hook ingest` 的事件落进 captureFile。 */
function makeCaptureSureWrapper(dir: string, captureFile: string): string {
  const { command, args } = resolveMockPython();
  const argStr = args.map((a) => `"${a}"`).join(' ');
  if (process.platform === 'win32') {
    const wrapper = join(dir, 'sure.cmd');
    writeFileSync(
      wrapper,
      `@echo off\r\n"${command}" ${argStr} "${MOCK_SURE_INGEST}" "${captureFile}" %*\r\n`
    );
    return wrapper;
  }
  const wrapper = join(dir, 'sure');
  writeFileSync(
    wrapper,
    `#!/bin/sh\nexec "${command}" ${argStr} "${MOCK_SURE_INGEST}" "${captureFile}" "$@"\n`
  );
  chmodSync(wrapper, 0o755);
  return wrapper;
}

type BridgeEvent = Record<string, unknown>;

function readCapture(captureFile: string): BridgeEvent[] {
  if (!existsSync(captureFile)) return [];
  return readFileSync(captureFile, 'utf-8')
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as BridgeEvent);
}

/**
 * 本机真实工作区里的平台网关凭据(`<MIQI_HOME>/workspace/.qraft/token.json`)。
 *
 * 本机 MiQi 的模型经「平台账号 AI 网关」(config.providers 为空):Python 端
 * 从会话工作区这份文件的 `aiGateway` 块取凭据路由(fixtures:providers/gateway.py,
 * 明文 JSON)。E2E 的临时工作区是空的 → 复制这份文件进去,真实模型即可经
 * 平台网关调用,环境仍完全隔离。CI 上主目录里没有该文件 → 返回 null →
 * 不复制,走 CI 注入的 config.json provider。
 */
function findRealWorkspaceToken(): string | null {
  for (const base of ['.forge', '.miqi']) {
    const file = join(homedir(), base, 'workspace', '.qraft', 'token.json');
    if (existsSync(file)) return file;
  }
  return null;
}

test.describe.serial('SURE 事件桥接 E2E · 对话工具调用 → hook ingest 落盘', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let prevSureBin: string | undefined;
  let binDir: string;
  let captureFile: string;
  let miqiHome: string;

  test.beforeAll(async () => {
    // ⚠️ 两个约束:
    // 1) mkdtemp 放 beforeAll:describe 体在收集阶段也会执行一次(见 acceptance-page.spec.ts);
    // 2) 目录必须落在**纯 ASCII 路径**——用仓库内而非 os.tmpdir()(本机用户名含
    //    中文时 os.tmpdir() 是 C:\Users\<中文>\...)。原因:cmd.exe 以系统 ANSI
    //    代码页(中文系统 GBK)读取 .cmd 文件字节,UTF-8 中文路径会被读坏(连路径
    //    分隔符一起吞掉)→ wrapper 里引用的捕获文件/脚本路径全失效、子进程静默
    //    rc≠0(实测踩中)。pytest conftest 把 basetemp 钉在仓库内(.pytest-basetemp-*)
    //    是同款考虑;验收面板 spec 的 wrapper 内容恰好全 ASCII 才一直没暴露。
    binDir = mkdtempSync(join(REPO_ROOT, '.sure-bridge-e2e-'));
    captureFile = join(binDir, 'ingest-captured.jsonl');
    prevSureBin = process.env.SURE_BIN;
    process.env.SURE_BIN = makeCaptureSureWrapper(binDir, captureFile);

    // AI 走真实模型:不 patch、不 mock。本机模型经「平台账号 AI 网关」——
    // launch 后把真实工作区的网关凭据复制进本轮临时工作区(见
    // findRealWorkspaceToken);CI 上找不到 → 不复制,走注入的 config.json provider。
    const realToken = findRealWorkspaceToken();
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    if (realToken) {
      const dst = join(miqiHome, 'workspace', '.qraft');
      mkdirSync(dst, { recursive: true });
      copyFileSync(realToken, join(dst, 'token.json'));
      console.log(`[test] 复用本机真实网关凭据: ${realToken}`);
    }
    await waitForBridgeInitialized(page, 30);
    await createNewConversation(page);
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
    cleanupDir(binDir);
    cleanupDir(miqiHome);
    // 还原 SURE_BIN,避免同一 worker 上后续 spec 继承 mock 二进制
    if (prevSureBin === undefined) delete process.env.SURE_BIN;
    else process.env.SURE_BIN = prevSureBin;
  });

  test('工具调用 → SessionStart/PreToolUse/PostToolUse 落盘(词表工具、最小采集、归属正确)', async () => {
    const hasPostToolUse = () => readCapture(captureFile).some((e) => e.event === 'PostToolUse');

    // 真实模型的回合没有确定性时长(首字迟、限流都会发生),不能靠
    // waitForResponseComplete 的文本稳定启发式判"回合开始/结束"——直接轮询
    // 我们真正关心的事实:桥接已把 completed 送达。provider 错误触发重发,
    // 静默窗口超时按 real-llm 用例的既有惯例放弃。
    const done = await sendUntilDoneOrProviderDown(
      page,
      '请把 "bridge-probe" 这行文字写入工作区中的文件 sure-bridge-probe.txt,完成后告诉我。',
      async () => hasPostToolUse(),
      { perAttemptWaitMs: 150_000, silenceExtendMs: 150_000 }
    );
    if (!done) {
      if ((await page.getByText(PROVIDER_UNAVAILABLE_TEXT).count()) > 0) {
        test.skip(true, 'provider 不可用,被测回合未运行');
      }
      // 应用执行过工具(UI 有工具卡)但桥接一条 completed 都没送达 → 疑似回归;
      // 模型压根没调工具 → 用例前提不成立,跳过不产假绿。
      const uiShowsToolCall =
        (await page
          .locator('main')
          .getByText(/工具调用/)
          .count()) > 0;
      if (uiShowsToolCall) {
        throw new Error(
          '应用已执行工具调用(UI 有工具卡),但 sure hook ingest 未送达 PostToolUse——疑似桥接回归'
        );
      }
      test.skip(true, '真实模型本回合未调用工具——用例前提不成立');
    }

    const events = readCapture(captureFile);
    expect(events.length).toBeGreaterThan(0);

    // 全量事件:来源、身份、时间戳齐备;载荷只允许最小采集字段
    const allowedKeys = [
      'event',
      'harness_session_id',
      'project_root',
      'result',
      'source',
      'timestamp_utc',
      'tool',
    ];
    for (const event of events) {
      expect(event.source, JSON.stringify(event)).toBe('miqi');
      expect(String(event.harness_session_id ?? '').length).toBeGreaterThan(0);
      expect(String(event.timestamp_utc ?? '').length).toBeGreaterThan(0);
      expect(event.project_root, JSON.stringify(event)).toBeTruthy();
      for (const key of Object.keys(event)) expect(allowedKeys).toContain(key);
    }

    // 词表过滤 + 配对:出现的工具名必须在词表内;completed 只在成功时发
    const VOCABULARY = ['exec', 'read_file', 'write_file', 'edit_file', 'apply_patch'];
    const pre = events.filter((e) => e.event === 'PreToolUse');
    const post = events.filter((e) => e.event === 'PostToolUse');
    expect(pre.length, JSON.stringify(events)).toBeGreaterThanOrEqual(1);
    for (const e of [...pre, ...post]) expect(VOCABULARY).toContain(String(e.tool));
    for (const e of post) {
      expect(e.result).toBe('ok'); // 完成不误记:成功才发 completed
      // 每个 completed 的工具应有同名的 requested(没有"无请求的完成")
      expect(pre.some((p) => p.tool === e.tool)).toBe(true);
    }
    // 保序:最早的 requested 早于最晚的 completed
    const names = events.map((e) => e.event);
    expect(names.indexOf('PreToolUse')).toBeLessThan(names.lastIndexOf('PostToolUse'));

    // 归属:工具事件同一会话;该会话的 SessionStart 在前,项目根一致
    const sessionId = post[0].harness_session_id;
    expect(pre.every((e) => e.harness_session_id === sessionId)).toBe(true);
    const sessionEvents = events.filter((e) => e.harness_session_id === sessionId);
    const sNames = sessionEvents.map((e) => e.event);
    const startIdx = sNames.indexOf('SessionStart');
    expect(
      startIdx,
      `会话 ${String(sessionId)} 缺 SessionStart:${JSON.stringify(sNames)}`
    ).toBeGreaterThanOrEqual(0);
    expect(sNames.indexOf('PreToolUse')).toBeGreaterThan(startIdx);

    const start = sessionEvents[startIdx];
    for (const e of [...pre, ...post]) expect(e.project_root).toBe(start.project_root);

    // 项目根就是本轮会话的工作区(临时 MIQI_HOME 下),归属不是空壳
    const homeName = miqiHome.split(/[\\/]/).filter(Boolean).pop() ?? '';
    expect(homeName.length).toBeGreaterThan(0);
    expect(String(start.project_root).toLowerCase()).toContain(homeName.toLowerCase());

    // 最小采集(逐字扫描原始落盘):工具参数/输出字段不得出现在任何载荷里
    const raw = readFileSync(captureFile, 'utf-8');
    expect(raw).not.toContain('"args"');
    expect(raw).not.toContain('"command"');
    expect(raw).not.toContain('"content"');

    await page.screenshot({ path: 'test-results/sure-bridge-events.png', fullPage: true });
  });
});
