/**
 * Slurm MCP 计费真实链路 E2E（opt-in，需凭据；CI 无凭据自动跳过）：
 *   真实 OAuth2 登录（设置页 MiQroForge 平台）→ 会话中经平台托管
 *   slurm MCP 网关提交真实作业（sleep 600 长驻）→ 轮询到 state=RUNNING
 *   → Python 发 slurm_job_running 事件 → Desktop 扣 10 积分 → 聊天区
 *   出现扣费提示。
 *
 * 模型不是被测对象：scripts/mock_slurm_billing.py 以确定性状态机驱动
 * 工具调用（提交→轮询→RUNNING→DONE_SLURM）——被测的是真实网关、
 * 真实集群作业、RUNNING 检测、扣费事件、Desktop 扣费与 UI 提示。
 * （此前用真实 LLM 驱动时 deepseek-v4-flash 经 AI 网关路由后单步
 * 推理耗时数分钟、行为方差大，E2E 无法稳定收敛。）
 *
 * 运行（会真实消耗：集群一次长驻作业 + 测试账号 10 积分）：
 *   QRAFT_PHONE=… QRAFT_PASSWORD=… SLURM_MCP_KEY=… npx playwright test \\
 *     --config=playwright.config.ts --project=electron tests/e2e/billing-live.spec.ts
 * 依赖：真实平台可达、平台托管 slurm MCP 网关可达（SLURM_MCP_URL）。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import {
  sendMessage,
  waitForResponseComplete,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
  APPS_DESKTOP,
  type ElectronFixture,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');
const SLURM_MCP_URL = process.env.SLURM_MCP_URL ?? 'http://124.220.57.194:9000/sse';
const SLURM_MCP_KEY = process.env.SLURM_MCP_KEY ?? '';

/** 启动 mock LLM（确定性状态机，见 scripts/mock_slurm_billing.py）。 */
async function startMockLLM(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_slurm_billing.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  let readyUrl = '';
  let stderrTail = '';
  proc.stdout?.on('data', (d) => {
    const t = String(d);
    console.log(`[mock-slurm] ${t.trim()}`);
    const m = t.match(/http:\/\/127\.0\.0\.1:(\d+)\/v1/);
    if (m) readyUrl = `http://127.0.0.1:${m[1]}/v1`;
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-slurm-err] ${String(d).trim()}`);
  });

  const deadline = Date.now() + 30_000;
  while (!readyUrl && Date.now() < deadline) {
    if (proc.exitCode !== null) {
      throw new Error(`mock-slurm server exited early (code ${proc.exitCode}): ${stderrTail}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(`mock-slurm startup line not seen in 30s: ${stderrTail}`);
  }
  console.log(`[test] mock-slurm LLM ready at ${readyUrl}`);
  return { proc, mockUrl: readyUrl };
}

const HAS_CREDS = !!process.env.QRAFT_PHONE && !!process.env.QRAFT_PASSWORD && !!SLURM_MCP_KEY;

const describeFn = HAS_CREDS ? test.describe : test.describe.skip;

describeFn('Billing live E2E — slurm MCP RUNNING 扣分 (opt-in)', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;
  let mockServer: ChildProcess;

  test.beforeAll(async () => {
    const mock = await startMockLLM();
    mockServer = mock.proc;

    fixture = await launchElectronApp((config: Record<string, any>) => {
      // 所有 provider 指向 mock LLM（确定性状态机驱动工具调用）；
      // 清空 providerActivation——内置激活会把 api_base 强制回官方
      // 端点、忽略这里的补丁（#929 收口后的直连路径同理）。
      config.desktop = { ...(config.desktop ?? {}), providerActivation: {} };
      const providers = config.providers ?? {};
      for (const [, p] of Object.entries(providers)) {
        if (p && typeof p === 'object') {
          (p as any).apiBase = mock.mockUrl;
          if (!(p as any).apiKey) (p as any).apiKey = 'mock-key';
        }
      }
      config.providers = providers;
      // 平台托管 slurm MCP 网关：SSE 传输 + 非回环 http 显式 opt-in
      //（生产路径；config.json 用 camelCase 键，蛇形键会遮蔽，两个都写）
      config.tools = config.tools || {};
      const slurm = {
        type: 'sse',
        url: SLURM_MCP_URL,
        headers: { Authorization: `Bearer ${SLURM_MCP_KEY}` },
        insecure_http: true,
        tool_timeout: 90,
        toolTimeout: 90,
        description:
          'SLURM cluster job management: submit_slurm_job, check_job_status, cancel_slurm_job, list_partitions, get_job_output',
      };
      config.tools.mcpServers = { slurm };
      config.tools.mcp_servers = { slurm };
      return config;
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
  }, 180_000);

  test.afterAll(async () => {
    // 诊断：清理前落出桥接日志中 billing/tool 关键行（定位扣费事件断点）
    try {
      const fs = await import('node:fs');
      const path = await import('node:path');
      for (const root of [fixture?.miqiHome, path.join(fixture?.miqiHome ?? '', 'workspace')]) {
        const logsDir = path.join(root, 'logs');
        if (!fs.existsSync(logsDir)) continue;
        for (const f of fs.readdirSync(logsDir)) {
          if (!f.endsWith('.log')) continue;
          const content = fs.readFileSync(path.join(logsDir, f), 'utf8');
          const raw = content.split('\n');
          const key = raw.filter((l) => /billing|Tool execute|mcp_slurm|slurm_job_running|emitter/i.test(l));
          console.log(`[test] BRIDGE LOG ${f} total lines:`, raw.length);
          console.log(`[test] BRIDGE LOG ${f} key lines:`, JSON.stringify(key.slice(-30)));
        }
      }
    } catch (e) {
      console.log('[test] BRIDGE LOG read failed:', e);
    }
    mockServer?.kill();
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
  });

  test(
    '真实登录 → slurm MCP 提交作业 → RUNNING 扣 10 积分 → 聊天区提示',
    { timeout: 420_000 },
    async () => {
      // 1. 设置页真实登录（残留登录态 token 可能已被平台作废——先退出重登）
      await page.getByText(/^(System Settings|系统设置)$/).click();
      await page
        .getByRole('tab')
        .filter({ hasText: /MiQroForge/ })
        .first()
        .click();
      const loggedInBadge = page.getByText('已登录');
      if (await loggedInBadge.isVisible({ timeout: 5000 }).catch(() => false)) {
        await page.getByTestId('qraft-logout-btn').click();
        await expect(loggedInBadge).toBeHidden({ timeout: 30_000 });
      }
      await page.getByTestId('qraft-phone-input').fill(process.env.QRAFT_PHONE!);
      await page.getByTestId('qraft-password-input').fill(process.env.QRAFT_PASSWORD!);
      await page.getByTestId('qraft-login-btn').click();
      await expect(loggedInBadge).toBeVisible({ timeout: 90_000 });

      // 登录后剥离 token 文件的 aiGateway 块：make_provider 在登录态
      // aiGateway=active 时强制走平台 AI 网关（真实模型），移除后回落
      // 直连 → config.providers 里补丁过的 mock LLM 生效。
      {
        const fsNode = await import('node:fs');
        const pathNode = await import('node:path');
        const tokenFile = pathNode.join(fixture.miqiHome, 'workspace', '.qraft', 'token.json');
        if (fsNode.existsSync(tokenFile)) {
          const data = JSON.parse(fsNode.readFileSync(tokenFile, 'utf8'));
          if (data.aiGateway) {
            delete data.aiGateway;
            fsNode.writeFileSync(tokenFile, JSON.stringify(data), { encoding: 'utf8' });
            console.log('[test] aiGateway 块已剥离 → mock LLM 直连生效');
          }
        }
      }

      // 2. 新会话 + 预授权（避免审批卡住工具执行）
      await createNewConversation(page);
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // 3. 发消息：mock LLM 确定性驱动 提交→轮询→RUNNING→DONE_SLURM
      await sendMessage(page, '使用 slurm 工具提交一个长驻作业并轮询到 RUNNING，完成后回复 DONE_SLURM');

      // 4. RUNNING 扣费提示（10 积分）——出现即截图，作为证据
      await expect(page.getByText(/已扣 10 积分/).first()).toBeVisible({ timeout: 300_000 });
      await page.screenshot({ path: 'test-results/slurm-billing-charge.png', fullPage: true });

      // 5. 回合正常收尾
      await waitForResponseComplete(page, 120_000);
      await expect(
        page
          .getByTestId('chat-message-assistant')
          .getByText(/DONE_SLURM/)
          .first()
      ).toBeVisible({ timeout: 30_000 });

      await page.screenshot({ path: 'test-results/slurm-billing-live.png', fullPage: true });
    }
  );
});
