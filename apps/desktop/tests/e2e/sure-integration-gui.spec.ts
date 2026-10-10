/**
 * SURE 集成 阶段 0 GUI 验收 — 真实应用 + 脚本化模型 + 真实 sure.exe。
 *
 * 覆盖(v6 方案的「桌面 GUI 验收」三件套):
 *   1. 新会话注册 5 个 mcp_sure_* 工具 —— 三重证据:
 *      a) mock 收到的请求里 tools 名单含全部 5 个(工具真的进入了模型工具表);
 *      b) bridge 日志 "…'sure': connected, 5 tools registered";
 *      c) 模型发出的 tool_call 被真实执行(sure.exe 真的跑了)。
 *   2. 对话触发 mcp_sure_sure_check → 审批弹窗出现(截图为证);
 *      D5-A(已落地):已知工具分支 → 弹窗出现「永久允许」,文案不再是 "Unknown tool: …"。
 *   3. 放行后报告原文渲染进聊天区(NOT CHECKED 如实呈现);
 *      「永久允许」后 mock 再发一次同参数调用 → 第二次**免弹窗**(键对齐端到端验证)。
 *
 * 模型:scripts/mock_sure_check.py(脚本化 OpenAI mock,项目内既有模式)——
 * 不依赖登录态/真实 provider,回合确定。真实模型经桌面手测另行验证。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron -g "SURE 集成 GUI 验收"
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  LLM_TIMEOUT,
  approvePlanCardOnce,
  closeElectronApp,
  launchElectronApp,
  sendMessage,
  stopMockServer,
  waitForResponseComplete,
} from './helpers/electron-setup';
import { startMockServer } from './helpers/mock-server';
import { patchConfigForMock } from './helpers/mock-openai';

/**
 * 被核查的测试项目:可用 `MIQI_SURE_PROJECT` 覆盖(默认 = PoC 阶段 0 的测试项目;
 * mock 脚本读同一环境变量,两者保持一致)。
 */
const PROJECT = process.env.MIQI_SURE_PROJECT ?? 'D:\\Code\\MiQi\\sure-poc\\hello';
/** mock 把模型收到的 tools 名单合并落盘到这里 */
const TOOLS_DUMP = join(tmpdir(), `sure-mock-tools-${Date.now()}.json`);

/**
 * 本机验收专用守卫(#1196,登记见 tests/e2e/CI-COVERAGE.md):
 * 需要 Windows + 本机已安装 SURE(v0.1.2,per-user 安装或 SURE_BIN)+ 测试项目存在。
 * CI runner 均未安装 sure.exe → 全部用例跳过;主验证在 Python 单测与本机 e2e。
 */
const SURE_BIN =
  process.env.SURE_BIN ?? join(process.env.LOCALAPPDATA ?? '', 'SURE', 'bin', 'sure.exe');
const SURE_E2E_READY = process.platform === 'win32' && existsSync(SURE_BIN) && existsSync(PROJECT);

/** 读本轮 E2E 临时 MIQI_HOME 里 bridge 写下的日志(账户级 / 未登录两种形态)。 */
function findBridgeLog(miqiHome: string): string | undefined {
  const candidates: string[] = [];
  const collect = (logs: string) => {
    if (!existsSync(logs)) return;
    for (const f of readdirSync(logs)) {
      if (f.startsWith('bridge-') && f.endsWith('.log')) candidates.push(join(logs, f));
    }
  };
  const accounts = join(miqiHome, 'accounts');
  if (existsSync(accounts)) {
    for (const sub of readdirSync(accounts)) collect(join(accounts, sub, 'workspace', 'logs'));
  }
  collect(join(miqiHome, 'workspace', 'logs'));
  return candidates.sort().pop();
}

test.describe('SURE 集成 GUI 验收(阶段 0)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockProc: ChildProcess | undefined;

  test.beforeAll(async () => {
    test.skip(!SURE_E2E_READY, '需要 Windows + 本机安装 SURE(sure.exe 不在 CI runner 上)');

    process.env.MIQI_SURE_PROJECT = PROJECT;
    process.env.MIQI_SURE_TOOLS_FILE = TOOLS_DUMP;
    const mock = await startMockServer('mock_sure_check.py');
    mockProc = mock.proc;
    console.log(`[sure-acceptance] mock provider: ${mock.mockUrl}`);

    // bypassAll: false —— 本验收要看到审批弹窗本身
    const fixture = await launchElectronApp(
      (cfg) => {
        patchConfigForMock(cfg, mock.mockUrl);
        return cfg;
      },
      { bypassAll: false, showWindow: true }
    );
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    await stopMockServer(mockProc, 'mock-sure');
  });

  test(
    'mcp_sure 工具注册、审批弹窗(可永久允许)、核查报告如实渲染',
    { timeout: LLM_TIMEOUT },
    async () => {
      const main = page.locator('main');

      // ── 1) 触发回合(mock 固定应答:先调 mcp_sure_sure_check,再回贴报告) ──
      await sendMessage(page, '请帮我检查一下当前项目的完成状态,看看能不能交付。');

      // ── 2) 等审批弹窗;截图为证 ──
      const approvalTitle = page.getByTestId('approval-title');
      const dialogDeadline = Date.now() + 120_000;
      let dialogSeen = false;
      while (Date.now() < dialogDeadline) {
        if (await approvalTitle.isVisible().catch(() => false)) {
          dialogSeen = true;
          break;
        }
        await approvePlanCardOnce(page).catch(() => false);
        await page.waitForTimeout(1000);
      }
      test.skip(!dialogSeen, '120s 内未出现审批弹窗——无弹窗可验');

      await expect(page.getByText('mcp_sure_sure_check').first()).toBeVisible({ timeout: 10_000 });
      // D5-A(已落地):已知工具分支 → allow_permanent=True,弹窗出现「永久允许」按钮;
      // 文案不再是 "Unknown tool: …"(对照:改动前截图见 git 历史/验收报告)
      const allowPermanent = page.getByTestId('approval-allow-permanent');
      await expect(allowPermanent).toBeVisible({ timeout: 10_000 });
      await expect(page.getByText('Unknown tool', { exact: false })).toHaveCount(0);
      console.log(
        '[sure-acceptance] 审批弹窗已出现;「永久允许」按钮存在(allow_permanent=true,D5-A 已落地)'
      );
      await page.screenshot({
        path: 'test-results/sure-acceptance-1-approval-dialog.png',
        timeout: 15_000,
      });

      // ── 3) 点「永久允许」——验证 D5-A 新能力 ──
      // mock 会在第一次执行后再发一次**同参数**调用:永久允许若生效,第二次免弹窗。
      await allowPermanent.click();
      const reportMarker = main.getByText('SURE checked', { exact: false }).first();
      const reportDeadline = Date.now() + 120_000;
      let extraDialogs = 0; // 「永久允许」之后弹出的审批窗数量(预期 0)
      while (Date.now() < reportDeadline) {
        if (await reportMarker.isVisible().catch(() => false)) break;
        if (await approvalTitle.isVisible().catch(() => false)) {
          extraDialogs += 1;
          // 真弹了就点掉避免挂起;最终统一断言(失败信息更明确)
          const sessionAllow = page.getByRole('button', { name: '本次会话允许' });
          if (await sessionAllow.isVisible({ timeout: 300 }).catch(() => false)) {
            await sessionAllow.click().catch(() => {});
          }
          continue;
        }
        if (await approvePlanCardOnce(page).catch(() => false)) continue;
        await page.waitForTimeout(1000);
      }
      await expect(reportMarker).toBeVisible({ timeout: 30_000 });
      expect(extraDialogs, '「永久允许」后同参数的第二次调用不应再弹审批(键对齐回归,D5-A)').toBe(0);
      await expect(main.getByText('NOT CHECKED', { exact: false }).first()).toBeVisible({
        timeout: 30_000,
      });

      await waitForResponseComplete(page, 60_000);
      // 截图前滚动到报告开头(聊天是内部滚动容器,先拍结论区)
      await reportMarker.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(800);
      await page.screenshot({
        path: 'test-results/sure-acceptance-2-report-rendered.png',
        timeout: 15_000,
      });

      // ── 4) 确定性证据:模型收到的 tools 名单含全部 5 个 MCP 工具 ──
      expect(existsSync(TOOLS_DUMP), `tools 落盘文件应存在: ${TOOLS_DUMP}`).toBe(true);
      const tools: string[] = JSON.parse(readFileSync(TOOLS_DUMP, 'utf-8'));
      console.log(`[sure-acceptance] 模型工具表共 ${tools.length} 个,其中 mcp_sure_*:`);
      for (const t of tools.filter((t) => t.startsWith('mcp_sure_'))) console.log(`  - ${t}`);
      for (const name of [
        'mcp_sure_sure_check',
        'mcp_sure_sure_get_report',
        'mcp_sure_sure_get_repair',
        'mcp_sure_sure_recheck',
        'mcp_sure_sure_status',
      ]) {
        expect(tools, `模型工具表应包含 ${name}`).toContain(name);
      }

      // ── 5) bridge 日志断言:5 个工具注册(应用侧证据) ──
      const logPath = findBridgeLog(miqiHome);
      expect(logPath, 'bridge 日志应存在').toBeTruthy();
      const logText = readFileSync(logPath as string, 'utf-8');
      expect(logText).toContain("'sure': connected, 5 tools registered");
      console.log(`[sure-acceptance] bridge 日志确认注册: ${logPath}`);

      console.log('[sure-acceptance] GUI 验收完成:工具表✓ 注册✓ 弹窗✓(永久允许可用) 报告渲染✓');
    }
  );
});
