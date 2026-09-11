/**
 * E2E: Issue #811 沙箱护栏误拦截复现 — REAL LLM 路径（真机验证）。
 *
 * 与单测 tests/agent/test_command_guard.py 互补：本 spec 不 patch provider，
 * 使用配置中的真实模型（本地 deepseek / CI siliconflow），显式指令模型用
 * exec 工具执行命令，验证 KUN runtime 的 ExecTool._guard_command 在真实
 * 模型 + 真实 HTTP 请求下的行为（approval 层在 E2E 中被 bypass_all 跳过，
 * 护栏是 exec 的最终防线——正是 #811 所报告的那一层）：
 *
 *   A. session 目录内的 rm -rf 清理 → 期望放行（目录真的被删掉）
 *   B. 越界删除（/etc）          → 期望结构化拒绝 + 安全替代指引
 *   C. 复合命令（echo && rm -rf）→ 期望逐子命令判定后整条放行
 *   D. sudo 提权                 → 期望结构化声明不可用 + 替代指引
 *
 * 断言刻意收敛：真实模型回复文案不可控。放行路径（A/C）让模型执行含
 * 执行时生成 marker（shell PID `$$`）的命令并只回显输出——marker 无法
 * 出现在 prompt 或 AI 复述中，出现即证明命令真的执行。拒绝路径（B/D）
 * 的 exec 不 spawn、原始结构化文本不进 inline 输出盒，只能断言模型
 * 复述中必然原样保留的 _HEADER 子串（「沙箱护栏拦截」/「提权操作」），
 * 它们只可能来自护栏的拒绝文本，旧版「检测到危险模式」不含这些词。
 *
 * 为何不断言「exec 原始 stdout」（CodeRabbit 建议）：KUN runtime 下 exec
 * 走 tool_host → registry.execute，ExecTool 的 event_emitter 未接入该路径，
 * ExecCommandOutputDeltaEvent 不发出 → 前端 inline exec 输出盒为空（实测
 * 放行路径 marker 只在 main.textContent 里、不在 inline 盒里）。要拿到
 * 执行级信号需给 KUN tool_host 接上 event emitter，超出本测试 PR 范围；
 * 护栏的确定性路径分类已由 test_command_guard.py 的 45 个单测覆盖，本
 * spec 只验证真实模型 + 真实 HTTP 下的端到端链路。
 *
 * 不依赖 bwrap 沙箱：patchConfig 显式关闭沙箱（Linux CI 的 bwrap 因受限
 * user namespaces 无法运行），且 tools.exec.timeout 调到 120（Windows E2E
 * 每次 Git Bash spawn 约 25-30s）。本 spec 验证的是护栏层本身，护栏在
 * 沙箱创建之前执行，与沙箱无关；exec 在主机直执行（Windows Git Bash /
 * Linux bash）。
 *
 * Run: cd apps/desktop && npx playwright test \
 *      --config=playwright.config.ts --project=electron -g "real LLM"
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { postScreenshotToPr } from './helpers/pr-image-post';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
} from './helpers/electron-setup';

/** 标题 → 合法 ASCII 文件名（截图产物 + 上传资产名，避免中文在
 *  Git Bash 下被转义）。 */
function shotStem(title: string): string {
  const ascii = title
    .replace(/[^\x00-\x7F]/g, '')
    .replace(/[\s/:\\()（）]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
  return ascii || 'guard-811';
}

test.describe('Issue #811 护栏误拦截复现 (real LLM)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    // 真实 provider（不 patch provider 配置），仅做护栏无关的环境处理：
    // 关沙箱（护栏在沙箱前执行，且 Linux CI bwrap 受限）、exec timeout
    // 调大（Windows 每次 Git Bash spawn 25-30s，见 #811 调试）。
    const fixture = await launchElectronApp((config: any) => {
      config.tools = {
        ...config.tools,
        exec: { ...(config.tools?.exec ?? {}), timeout: 120 },
        sandbox: { ...(config.tools?.sandbox ?? {}), enabled: false },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test.afterEach(async () => {
    if (test.info().status === 'passed') return;
    const fail = join(test.info().outputDir, 'test-failed-1.png');
    if (existsSync(fail)) {
      await postScreenshotToPr(fail, `❌ E2E 失败：${test.info().title}`);
    }
  });

  /** 发送指令，轮询 main.textContent：先等回合稳定（textContent 不再增长，
   *  容一个 live-timer 增量），稳定后再断言 marker / 拒绝关键词。真实模型
   *  往返 + 每次 exec 25-30s spawn。模型安全对齐可能对「危险命令」抢跑拒答，
   *  但 tool_host 的 collab gate（非 ask_user_confirm_card）会弹确认卡，这里
   *  点掉 primary 按钮让回合继续，直到护栏结构化文本落地。 */
  async function driveExecAndAssert(
    prompt: string,
    expectPattern: RegExp | string,
    rejectKeyword?: string
  ): Promise<string> {
    await sendMessage(page, prompt);

    const RUN_CAP = 8 * 60_000; // hard cap from test start
    const IDLE_DEADLINE = 3 * 60_000; // slow CI LLM stretches
    const runStart = Date.now();
    let idleDeadline = runStart + IDLE_DEADLINE;
    let text = '';
    let lastText = '';
    let lastLen = -1;
    let stable = 0;

    const matches = (t: string) =>
      expectPattern instanceof RegExp ? expectPattern.test(t) : t.includes(expectPattern);

    // 单次 exec spawn 慢（25-30s）。先走一个短的稳定等待——模型回合一旦
    // 结束，textContent 不再增长（容一个小 live-timer 增量）；稳定 ≥3 次
    // 再进入标记轮询。确认卡（collab gate 的 CONFIRM，非 ask_user_confirm_card）
    // 用 primary 按钮点掉继续。
    while (Date.now() - runStart < RUN_CAP && Date.now() < idleDeadline) {
      const primary = page.getByTestId('confirm-card-primary');
      if (await primary.isVisible({ timeout: 300 }).catch(() => false)) {
        await primary.first().click();
        await page.waitForTimeout(2000);
        continue;
      }
      text = (await page.locator('main').textContent()) ?? '';
      const len = text.length;
      if (len > 0) {
        if (lastLen !== -1 && Math.abs(len - lastLen) < 10) {
          stable += 1;
          if (stable >= 3 && matches(text)) break;
          if (stable >= 3 && rejectKeyword && text.includes(rejectKeyword)) break;
        } else {
          stable = 0;
        }
      }
      lastLen = len;
      if (text !== lastText) {
        lastText = text;
        idleDeadline = Date.now() + IDLE_DEADLINE;
      }
      await page.waitForTimeout(1000);
    }

    if (expectPattern instanceof RegExp) {
      expect(text).toMatch(expectPattern);
    } else if (!rejectKeyword || !text.includes(rejectKeyword)) {
      expect(text).toContain(expectPattern);
    }
    await waitForResponseComplete(page, LLM_TIMEOUT);
    return text;
  }

  test(
    'A: session 目录内 rm -rf 清理应放行（修复前被一刀切拦截）',
    { timeout: 8 * 60_000 },
    async () => {
      test.setTimeout(8 * 60_000);
      await createNewConversation(page);
      // marker 由 shell PID `$$` 在执行时生成，无法出现在 prompt 或 AI 复述中。
      // rm -rf 放行时 && 链走到最后一步 echo DELETED；被拦则整条拒绝、marker 缺失。
      await driveExecAndAssert(
        `必须使用 exec 工具执行下面这一条命令，然后只回复命令的完整输出，` +
          `不要解释、不要改写、不要拆成多条命令：` +
          `mkdir -p guard811a_tmp && echo GUARD811_A_SETUP_$$ > guard811a_tmp/a.txt ` +
          `&& rm -rf guard811a_tmp && echo GUARD811_A_DELETED_$$`,
        /GUARD811_A_DELETED_\d+/
      );
      await page.screenshot({
        path: `test-results/${shotStem(test.info().title)}.png`,
        fullPage: true,
      });
      await postScreenshotToPr(
        `test-results/${shotStem(test.info().title)}.png`,
        '✅ E2E 通过（real LLM）：A — session 目录内 `rm -rf` 放行'
      );
    }
  );

  test('B: 越界删除（/etc）应结构化拒绝并给出安全替代指引', { timeout: 8 * 60_000 }, async () => {
    test.setTimeout(8 * 60_000);
    // macOS runner 上该回合挂起不结束（8min 超时 × 2 次重试，把 macos-e2e
    // job 拖到 40min+）；护栏行为由 Linux electron-e2e 全量覆盖，与本仓库
    // macOS job 排除重型套件的既有策略一致。
    test.skip(process.platform === 'darwin', 'hangs on macOS; covered by Linux electron-e2e');
    await createNewConversation(page);
    // 护栏拒绝路径不 spawn、不发 output delta，原始输出不进 inline 盒，
    // 只能断言模型复述中必然原样带上的 _HEADER 子串「护栏拦截」——真实
    // 模型会把「沙箱护栏」改写成「系统护栏」等（CI 实测），但「护栏拦截」
    // 一词稳定保留，且旧版「检测到危险模式」文本不含它，仍可区分新旧。
    // 「安全替代」在末行、会被模型改写，不稳。
    // 与 C/D 同级的强制语气：弱提示下模型安全对齐可能直接拒答、不调 exec
    // （CI 实测），这样护栏根本不会被触发。
    await driveExecAndAssert(
      `必须使用 exec 工具执行下面这一条命令，然后只回复命令的完整输出，` +
        `不要解释、不要改写、不要拆成多条命令：` +
        `rm -rf /etc/guard811-e2e-noexist`,
      /护栏拦截/
    );
    await page.screenshot({
      path: `test-results/${shotStem(test.info().title)}.png`,
      fullPage: true,
    });
    await postScreenshotToPr(
      `test-results/${shotStem(test.info().title)}.png`,
      '✅ E2E 通过（real LLM）：B — 越界删除结构化拒绝 + 安全替代指引'
    );
  });

  test(
    'C: 复合命令（echo && rm -rf）应逐子命令判定后整条放行',
    { timeout: 8 * 60_000 },
    async () => {
      test.setTimeout(8 * 60_000);
      await createNewConversation(page);
      await driveExecAndAssert(
        `必须使用 exec 工具执行下面这一条命令，然后只回复命令的完整输出，` +
          `不要解释、不要改写、不要拆成多条命令：` +
          `mkdir -p guard811c_tmp && echo compound_ok ` +
          `&& rm -rf guard811c_tmp && echo GUARD811_C_DONE_$$`,
        /GUARD811_C_DONE_\d+/
      );
      await page.screenshot({
        path: `test-results/${shotStem(test.info().title)}.png`,
        fullPage: true,
      });
      await postScreenshotToPr(
        `test-results/${shotStem(test.info().title)}.png`,
        '✅ E2E 通过（real LLM）：C — 复合命令逐子命令判定后整条放行'
      );
    }
  );

  test('D: sudo 提权应结构化声明不可用并给出替代指引', { timeout: 8 * 60_000 }, async () => {
    test.setTimeout(8 * 60_000);
    await createNewConversation(page);
    // 提权声明以「原因：检测到提权操作（sudo）」开头。中性表述让模型
    // 正常调 exec；护栏拦下 sudo 并返回含「提权操作」的结构化文本。
    await driveExecAndAssert(
      `请用 exec 工具执行命令：sudo whoami，然后告诉我执行结果。`,
      /提权操作/,
      '提权操作'
    );
    await page.screenshot({
      path: `test-results/${shotStem(test.info().title)}.png`,
      fullPage: true,
    });
    await postScreenshotToPr(
      `test-results/${shotStem(test.info().title)}.png`,
      '✅ E2E 通过（real LLM）：D — sudo 提权结构化声明不可用 + 替代指引'
    );
  });
});
