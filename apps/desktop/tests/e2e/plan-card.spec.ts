/**
 * Plan Card E2E（#646-v2）— plan workstream → execution → dangerous action.
 *
 * The plan is part of the agent work stream. The user can execute the current
 * plan or adjust it inline; adjustment feedback is returned to the model which
 * produces a new plan before any mutation continues.
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { createConnection } from 'node:net';
import { join } from 'node:path';
import {
  LLM_TIMEOUT,
  sendMessage,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  APPS_DESKTOP,
} from './helpers/electron-setup';

const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

async function waitForTcpListener(port: number): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    await new Promise<void>((resolve) => {
      const socket = createConnection({ host: '127.0.0.1', port });
      const finish = () => {
        socket.removeAllListeners();
        socket.destroy();
        resolve();
      };
      socket.once('connect', finish);
      socket.once('error', finish);
      socket.setTimeout(1000, finish);
    });
    // A successful TCP connect is a direct readiness signal; unlike a
    // child-process stdout pipe it does not depend on Python stdout delivery.
    // Re-probe until the server is actually accepting connections.
    const probe = await new Promise<boolean>((resolve) => {
      const socket = createConnection({ host: '127.0.0.1', port });
      const ok = () => {
        socket.destroy();
        resolve(true);
      };
      const fail = () => {
        socket.destroy();
        resolve(false);
      };
      socket.once('connect', ok);
      socket.once('error', fail);
      socket.setTimeout(1000, fail);
    });
    if (probe) return;
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(
    `mock OpenAI server did not accept TCP connections on 127.0.0.1:${port} within 30s`
  );
}

async function startMockOpenAI(): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const python = process.env.MIQI_PYTHON_PATH || 'python';
  const port = 20000 + Math.floor(Math.random() * 20000);
  const proc = spawn(python, [join(REPO_ROOT, 'scripts', 'mock_openai.py'), String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });
  let stderrTail = '';
  proc.stdout?.on('data', (d) => console.log(`[mock] ${String(d).trim()}`));
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-2000);
    console.log(`[mock-err] ${String(d).trim()}`);
  });
  proc.on('exit', (code) => console.log(`[test] mock server exited: ${code}`));

  const startupDeadline = Date.now() + 30_000;
  while (proc.exitCode === null && Date.now() < startupDeadline) {
    try {
      await waitForTcpListener(port);
      const mockUrl = `http://127.0.0.1:${port}/v1`;
      console.log(`[test] mock OpenAI server ready at ${mockUrl}`);
      return { proc, mockUrl };
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  if (proc.exitCode !== null) {
    throw new Error(`mock OpenAI server exited early (code ${proc.exitCode}): ${stderrTail}`);
  }
  proc.kill();
  throw new Error(`mock OpenAI server did not become ready in 30s: ${stderrTail}`);
}

async function launchWithMock() {
  const mock = await startMockOpenAI();
  const fixture = await launchElectronApp((config: any) => {
    const providers = config.providers ?? {};
    for (const [name, provider] of Object.entries(providers)) {
      if (provider && typeof provider === 'object') {
        (provider as any).apiBase = mock.mockUrl;
        if (!(provider as any).apiKey) (provider as any).apiKey = 'mock-key';
      }
    }
    config.providers = providers;
  });
  return { ...fixture, mockServer: mock.proc };
}

test.describe('Plan Card (#646-v2)', () => {
  test('计划工作流：当前方案执行 → ActionCard → 完成', { timeout: LLM_TIMEOUT }, async () => {
    const fixture = await launchWithMock();
    const electronApp: ElectronApplication = fixture.electronApp;
    const page: Page = fixture.page;

    try {
      await createNewConversation(page);
      await sendMessage(page, '计划：生成 MOF-5 实验报告并上传');

      const planCard = page.getByTestId('plan-card').first();
      await expect(planCard).toBeVisible({ timeout: 60_000 });
      await expect(planCard.getByText('生成 MOF-5 实验报告')).toBeVisible();
      await expect(planCard.getByText('搜集论文资料')).toBeVisible();
      await expect(planCard.getByText('上传到 Qraft')).toBeVisible();
      await expect(planCard.getByText('网络')).toBeVisible();
      await expect(planCard.getByText('外部')).toBeVisible();
      await expect(planCard.getByTestId('plan-confirm')).toBeVisible();
      await expect(planCard.getByTestId('confirm-modify')).toBeVisible();

      await page.screenshot({ path: 'test-results/plan-card-waiting.png' });
      await planCard.getByTestId('plan-confirm').click();

      const autoApprove = async () => {
        try {
          for (let i = 0; i < 60; i++) {
            const dialog = page.getByRole('alertdialog').first();
            if (await dialog.isVisible().catch(() => false)) {
              const allow = dialog.getByRole('button', { name: /允许一次|允许/ }).first();
              if (await allow.isVisible().catch(() => false)) await allow.click();
            }
            await page.waitForTimeout(500);
          }
        } catch {
          // App closed: nothing left to approve.
        }
      };
      const approveTask = autoApprove();

      const actionCard = page.getByTestId('action-card').first();
      await expect(actionCard).toBeVisible({ timeout: 60_000 });
      await expect(actionCard.getByText('☁ 上传').first()).toBeVisible();
      await expect(actionCard.getByText('Qraft').first()).toBeVisible();
      await expect(actionCard.getByText('mof-report.json').first()).toBeVisible();
      await expect(actionCard.getByText(/23\.0 KB/)).toBeVisible();

      await page.screenshot({ path: 'test-results/action-card-upload.png' });
      await actionCard.getByRole('button', { name: '确认上传' }).click();
      await waitForResponseComplete(page, LLM_TIMEOUT);
      await expect(page.getByText(/已完成：MOF-5 实验报告/)).toBeVisible({ timeout: 30_000 });

      await approveTask;
    } finally {
      await closeElectronApp(electronApp, fixture.miqiHome);
      fixture.mockServer.kill();
    }
  });

  test(
    '调整方案：内联输入意见 → Agent 重新规划 → 不执行旧方案',
    { timeout: LLM_TIMEOUT },
    async () => {
      const fixture = await launchWithMock();
      const electronApp: ElectronApplication = fixture.electronApp;
      const page: Page = fixture.page;

      try {
        await createNewConversation(page);
        await sendMessage(page, '计划：生成 MOF-5 实验报告并上传');

        const planCard = page.getByTestId('plan-card').first();
        await expect(planCard).toBeVisible({ timeout: 60_000 });
        await planCard.getByTestId('confirm-modify').click();

        const adjustment = planCard.getByTestId('plan-adjustment-input');
        await expect(adjustment).toBeVisible();
        await adjustment.fill('不要上传 Qraft，先完成本地报告并增加成本对比步骤。');
        await planCard.getByTestId('plan-submit-adjustment').click();

        const revised = page.getByTestId('plan-card').last();
        await expect(revised).toBeVisible({ timeout: 60_000 });
        await expect(revised.getByText('生成 MOF-5 实验报告（修改版）')).toBeVisible();
        await expect(revised.getByText('对比合成成本')).toBeVisible();
        await expect(page.getByTestId('action-card')).toHaveCount(0);

        await revised.getByTestId('plan-cancel').click();
      } finally {
        await closeElectronApp(electronApp, fixture.miqiHome);
        fixture.mockServer.kill();
      }
    }
  );
});
