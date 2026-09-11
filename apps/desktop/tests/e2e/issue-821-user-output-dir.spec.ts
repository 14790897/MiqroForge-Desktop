/**
 * E2E — issue #821 用户点名输出目录自动授权
 *
 * 验证链路（用户视角）：
 *   1. 用户要求 agent 把结果写到工作区外的自定义目录
 *      （如 C:\Users\<user>\Desktop\test_result —— 本测试用系统临时目录代替，
 *      同样在 workspace 之外）
 *   2. KUN 循环从用户消息提取点名目录 → ToolHostContext →
 *      tool_host 注入 _user_roots → 文件工具放行
 *   3. write_file 成功写入，而不是像修复前那样被
 *      "不在任何合法根目录" 拒绝、被迫 exec+Python 绕过
 *
 * ══════════════════════════════════════════════════════════════
 *  NOTE: Python 单元测试（tests/agent/tools/test_user_roots.py 28 用例 +
 *  tests/sandbox/test_wsl_sandbox_path_mapping.py 3 用例 +
 *  tests/kun_runtime 注入/传播用例）是 PRIMARY validation。
 *
 *  本 E2E 是补充验证，仅按需运行：
 *    - 需要 Windows + WSL 沙箱（修复前的拒绝发生在 WSL 合法根检查）
 *    - 需要真实 LLM provider
 *    - CI 默认跳过，除非 MIQI_RUN_SANDBOX_E2E=1
 * ══════════════════════════════════════════════════════════════
 *
 * 运行：
 *   cd apps/desktop
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     issue-821-user-output-dir.spec.ts --workers=1
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForBridgeInitialized,
  sendMessage,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  waitForSandboxReady,
} from './helpers/electron-setup';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { existsSync, readFileSync, unlinkSync, mkdirSync, rmSync } from 'node:fs';

const SKIP_SANDBOX_E2E = !!process.env.CI && process.env.MIQI_RUN_SANDBOX_E2E !== '1';

test.describe('用户点名输出目录自动授权 (#821)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  // Skip entire suite when WSL is not available (non-Windows / CI without flag)
  test.skip(
    () => SKIP_SANDBOX_E2E || process.platform !== 'win32',
    '本 E2E 需要 Windows + WSL 沙箱；CI 默认跳过（MIQI_RUN_SANDBOX_E2E=1 开启）'
  );

  test.beforeAll(async () => {
    // 本 spec 回归的是 WSL 合法根检查：必须启用沙箱，否则 native 路径
    // 没有 contain 检查、断言会空转通过（sandbox-exec 同款考量）。
    // 临时 MIQI_HOME 会复制用户 config，此处仅强制打开 sandbox 开关。
    const fixture = await launchElectronApp((config: any) => {
      const tools = config.tools ?? {};
      config.tools = {
        ...tools,
        sandbox: { ...(tools.sandbox ?? {}), enabled: true },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    await waitForBridgeInitialized(page);
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'write_file 写入用户点名的 workspace 外目录（临时目录模拟桌面输出目录）',
    { timeout: LLM_TIMEOUT * 5 },
    async () => {
      // WSL 冷启动（export/import/apt）可能超过 5 分钟，放宽到 10 分钟
      const ready = await waitForSandboxReady(page, 600_000);
      if (!ready) {
        throw new Error('Sandbox manager did not become ready within 600s');
      }

      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // 输出目录：系统临时目录下（workspace 之外），模拟用户点名的桌面目录
      const timestamp = Date.now();
      const outDir = join(tmpdir(), `miqi_e2e_821_out_${timestamp}`);
      mkdirSync(outDir, { recursive: true });
      const fname = `e2e_821_report_${timestamp}.md`;
      const content = `E2E #821 user-mentioned output dir test ${timestamp}`;
      console.log(`[test] Output dir (outside workspace): ${outDir}`);

      await createNewConversation(page);

      // 关键：消息里必须带完整绝对路径（含反斜杠），与 issue 用户原话同形
      const trigger =
        `请使用 write_file 工具，把结果写入这个目录里的文件：` +
        `${outDir}\\${fname}，文件内容为 "${content}"。` +
        `只回复工具结果原文，不要添加解释，不要用 exec。`;

      await sendMessage(page, trigger);

      // 不依赖 approveLoop/waitForResponseComplete 的"文本稳定即完成"判定
      // （回复开始流式前文本也稳定，会提前返回）；launchElectronApp 已写
      // approvals.bypass_all: true，审批不会阻塞。改用 Playwright 轮询：
      // 修复前 write_file 会被 "不在任何合法根目录" 拒绝、agent 被迫
      // exec+Python 绕过（回复不会出现 write_file 的成功原文）。
      await expect(
        page.locator('main'),
        'write_file 应返回成功原文（而非被合法根拒绝后走 exec 绕过）'
      ).toContainText('Successfully wrote', { timeout: 600_000 });

      const mainText = await page.locator('main').textContent();
      console.log('[test] AI response:', mainText?.substring(0, 500));
      expect(mainText, '不应出现合法根拒绝错误').not.toContain('权限被拒绝');

      // 产物确实落在 workspace 之外的用户点名目录
      await page.waitForTimeout(3000); // Allow filesystem sync
      const hostFile = join(outDir, fname);
      expect(existsSync(hostFile), `Host file not found: ${hostFile}`).toBe(true);
      const actual = readFileSync(hostFile, 'utf-8');
      console.log(`[test] File content on host: "${actual}"`);
      expect(actual).toContain(content);
      console.log('[test] ✅ write_file 写入用户点名目录成功');

      // Cleanup
      try {
        unlinkSync(hostFile);
      } catch {}
      try {
        rmSync(outDir, { recursive: true, force: true });
      } catch {}
      await page.screenshot({
        path: `test-results/issue-821-user-output-dir.png`,
      });
    }
  );
});
