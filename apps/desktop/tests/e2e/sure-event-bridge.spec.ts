/**
 * 阶段 5 事件桥接 E2E —— 跨平台、零外部依赖(不要求本机安装 SURE、不连真实模型)。
 *
 * 在真实 Electron 应用里把整条观测链路跑通:
 *   对话 →(mock LLM 发一条 write_file 工具调用)→ 编排器执行工具(词表内)
 *   → sure_hooks 钩子 → spawn `sure hook ingest --source miqi` → 落盘断言。
 *
 * SURE_BIN 指向本 spec 生成的包装脚本(win: .cmd / posix: sh),内部以
 * `python scripts/mock_sure_ingest.py <捕获文件>` 冒充 sure,把 ingest 的
 * stdin 事件逐条追加到捕获文件。LLM 用 scripts/mock_llm_write_file.py
 * (静态:第一轮工具调用、拿到工具结果后文本收尾)。
 *
 * 防护面(评审「不然出问题不知道」的补测):
 *   - 钩子未装配(双 HookRuntime 那个装配 bug 回归)→ 一条事件都没有;
 *   - 最小采集回归 → 载荷出现 args/command/写入内容;
 *   - 完成误记 → 无 PostToolUse 或 result 非 ok;
 *   - 双份装配/重复触发 → Pre/Post 不再严格各一条。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron sure-event-bridge
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import {
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  stopMockServer,
  waitForBridgeInitialized,
  waitForResponseComplete,
} from './helpers/electron-setup';
import { resolveMockPython, startMockServer } from './helpers/mock-server';
import { patchConfigForMock } from './helpers/mock-openai';

const REPO_ROOT = resolve(__dirname, '..', '..', '..', '..');
const MOCK_SURE_INGEST = join(REPO_ROOT, 'scripts', 'mock_sure_ingest.py');
const PROBE_MARKER = 'sure-bridge-probe';

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

test.describe.serial('SURE 事件桥接 E2E · 对话工具调用 → hook ingest 落盘', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let mockProc: ChildProcess | undefined;
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

    // 静态 LLM mock:第一轮 write_file 工具调用,工具结果回来后文本收尾
    const mock = await startMockServer('mock_llm_write_file.py');
    mockProc = mock.proc;
    const fixture = await launchElectronApp((cfg) => patchConfigForMock(cfg, mock.mockUrl));
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page, 30);
    await createNewConversation(page);
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
    await stopMockServer(mockProc, 'mock llm (write_file)');
    cleanupDir(binDir);
    cleanupDir(miqiHome);
    // 还原 SURE_BIN,避免同一 worker 上后续 spec 继承 mock 二进制
    if (prevSureBin === undefined) delete process.env.SURE_BIN;
    else process.env.SURE_BIN = prevSureBin;
  });

  test('工具调用 → SessionStart/PreToolUse/PostToolUse 落盘(词表工具、最小采集、归属正确)', async () => {
    await sendMessage(page, '请把探针内容写入文件');
    await waitForResponseComplete(page);

    // 桥接是后台排空(fire-and-forget):轮询等 PostToolUse 真正落盘
    await expect
      .poll(() => readCapture(captureFile).filter((e) => e.event === 'PostToolUse').length, {
        timeout: 30_000,
        message: '等待 sure hook ingest 捕获 PostToolUse',
      })
      .toBeGreaterThanOrEqual(1);

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

    // 词表工具恰好执行一次 → 严格一条 requested + 一条 completed(兼防双份装配)
    const toolEvents = events.filter((e) => e.event === 'PreToolUse' || e.event === 'PostToolUse');
    expect(toolEvents, JSON.stringify(events)).toHaveLength(2);
    const [pre, post] = toolEvents;
    expect(pre.event).toBe('PreToolUse');
    expect(pre.tool).toBe('write_file');
    expect(post.event).toBe('PostToolUse');
    expect(post.tool).toBe('write_file');
    expect(post.result).toBe('ok'); // 完成不误记:成功才发 completed

    // 归属:两事件同一会话;该会话的 SessionStart 在前(保序),项目根一致
    const sessionId = pre.harness_session_id;
    expect(post.harness_session_id).toBe(sessionId);
    const sessionEvents = events.filter((e) => e.harness_session_id === sessionId);
    const names = sessionEvents.map((e) => e.event);
    const startIdx = names.indexOf('SessionStart');
    expect(
      startIdx,
      `会话 ${String(sessionId)} 缺 SessionStart:${JSON.stringify(names)}`
    ).toBeGreaterThanOrEqual(0);
    expect(names.indexOf('PreToolUse')).toBeGreaterThan(startIdx);
    expect(names.indexOf('PostToolUse')).toBeGreaterThan(names.indexOf('PreToolUse'));

    const start = sessionEvents[startIdx];
    expect(pre.project_root).toBe(start.project_root);
    expect(post.project_root).toBe(start.project_root);

    // 项目根就是本轮会话的工作区(临时 MIQI_HOME 下),归属不是空壳
    const homeName = miqiHome.split(/[\\/]/).filter(Boolean).pop() ?? '';
    expect(homeName.length).toBeGreaterThan(0);
    expect(String(start.project_root).toLowerCase()).toContain(homeName.toLowerCase());

    // 最小采集(逐字扫描原始落盘):工具参数与写入内容不得出现在任何载荷里
    const raw = readFileSync(captureFile, 'utf-8');
    expect(raw).not.toContain('args');
    expect(raw).not.toContain(PROBE_MARKER);
    expect(raw).not.toContain('content');

    await page.screenshot({ path: 'test-results/sure-bridge-events.png', fullPage: true });
  });
});
