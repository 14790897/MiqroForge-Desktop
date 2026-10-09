/**
 * 网关凭据握手真实账号 live E2E（opt-in，默认跳过，不入 CI 常规执行）—
 * #1258 的修复验证。
 *
 * 与 ai-gateway-live.spec.ts 同策略：凭据仅经环境变量注入，登录态与密钥
 * 都落在 launchElectronApp 的临时 MIQI_HOME，结束随临时目录清理。
 *
 * 用法：
 *   QRAFT_LIVE=1 QRAFT_PHONE=<测试账号> QRAFT_PASSWORD=<密码> \
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     gateway-creds-live.spec.ts
 *
 * 覆盖真实链路（真实平台登录 → userinfo 下发 encryptedApiKey → 主进程写
 * <workspace>/.qraft/token.json → Python read_gateway_creds 判定）：
 *   1. 真实登录后握手文件必须真的落到后端读的那个路径上（含 aiGateway active）
 *      —— 「渲染进程认为网关可用」与「后端能读到凭据」必须一致；
 *   2. 手工把握手文件拿掉（模拟磁盘视图丢失：写失败/被清理/读到半个文件）后，
 *      保存网关模型必须给**可重试**的 GATEWAY_CREDS_UNAVAILABLE，而不是
 *      误导性的 Unsupported model；
 *   3. qraft.syncToken 重新同步握手文件后，同一个保存请求成功落盘。
 */

import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { existsSync, readFileSync, rmSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  browserLogin,
  getAccountWorkspaceDir,
  type ElectronFixture,
} from './helpers/electron-setup';

const LIVE = process.env.QRAFT_LIVE === '1';
const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';
const READY = LIVE && PHONE !== '' && PASSWORD !== '';

const GATEWAY_MODEL = 'deepseek/deepseek-v4-flash';

const describeFn = READY ? test.describe : test.describe.skip;

describeFn('网关凭据握手真实账号 live E2E (#1258)', () => {
  let fixture: ElectronFixture;

  test.beforeAll(async () => {
    // 与 ai-gateway-live 一致：全新安装（不拷贝本机 provider 配置）。
    fixture = await launchElectronApp(undefined, { noUserConfig: true });
  }, 180_000);

  test.afterAll(async () => {
    if (fixture?.electronApp) await closeElectronApp(fixture.electronApp, fixture.miqiHome);
  });

  /** 握手文件的两个可能落点：账号工作区，或该账号认领的存量共享工作区（#1185）。 */
  function tokenFileCandidates(sub: string): string[] {
    return [
      join(getAccountWorkspaceDir(fixture.miqiHome, sub), '.qraft', 'token.json'),
      join(fixture.miqiHome, 'workspace', '.qraft', 'token.json'),
    ];
  }

  /** 直接调渲染层那条 config.update（用户报错里那条 IPC），返回错误文案或 null。 */
  async function saveGatewayModel(): Promise<string | null> {
    return fixture.page.evaluate(async (model) => {
      try {
        await (
          window as unknown as { miqi: { config: { update: (c: unknown) => Promise<unknown> } } }
        ).miqi.config.update({ agents: { defaults: { model } } });
        return null;
      } catch (err) {
        return err instanceof Error ? err.message : String(err);
      }
    }, GATEWAY_MODEL);
  }

  test(
    '真实登录 → 握手文件就位 → 缺失时可重试 → 重新同步后保存成功',
    { timeout: 180_000 },
    async () => {
      const page = fixture.page;

      // 1. 真实登录（幂等：dev userData 可能残留上次登录态）
      const loggedInBadge = page.getByText('已登录');
      if (!(await loggedInBadge.isVisible({ timeout: 5000 }).catch(() => false))) {
        await browserLogin(page, fixture.electronApp, PHONE, PASSWORD);
      }
      await expect(loggedInBadge).toBeVisible({ timeout: 90_000 });
      await expect(page.getByTestId('qraft-ai-gateway-status')).toHaveText('可用', {
        timeout: 30_000,
      });

      const sub = String(
        await page.evaluate(async () => {
          const s = await (
            window as unknown as { miqi: { qraft: { status: () => Promise<any> } } }
          ).miqi.qraft.status();
          return s?.account?.sub ?? '';
        })
      );
      expect(sub).not.toBe('');

      // 2. 握手文件必须真的在后端读的路径上，且 aiGateway active（真实密钥）
      const candidates = tokenFileCandidates(sub);
      await expect
        .poll(() => candidates.find((p) => existsSync(p)) ?? '', { timeout: 30_000 })
        .not.toBe('');
      const tokenFile = candidates.find((p) => existsSync(p))!;
      const handshake = JSON.parse(readFileSync(tokenFile, 'utf8'));
      expect(handshake.aiGateway?.status).toBe('active');
      expect(String(handshake.aiGateway?.encryptedApiKey ?? '').length).toBeGreaterThan(0);

      // 3. 拿掉握手文件 → 保存网关模型必须可重试，不能说模型不支持
      rmSync(tokenFile, { force: true });
      const firstError = await saveGatewayModel();
      expect(firstError).not.toBeNull();
      expect(firstError!).toContain('GATEWAY_CREDS_UNAVAILABLE');
      expect(firstError!).not.toContain('Unsupported model');

      // 4. 重新同步握手文件 → 同一请求成功落盘
      const synced = await page.evaluate(() =>
        (
          window as unknown as { miqi: { qraft: { syncToken: () => Promise<unknown> } } }
        ).miqi.qraft.syncToken()
      );
      expect(synced).toMatchObject({ ok: true });
      expect(existsSync(tokenFile)).toBe(true);

      const secondError = await saveGatewayModel();
      expect(secondError).toBeNull();
      const config = JSON.parse(readFileSync(join(fixture.miqiHome, 'config.json'), 'utf8'));
      expect(config.agents?.defaults?.model).toBe(GATEWAY_MODEL);

      // 5. 真实界面留证：模型 tab 显示网关「使用中」
      await page.getByText(/^(System Settings|系统设置)$/).click();
      await page.getByRole('tab', { name: '模型' }).click();
      await expect(page.getByTestId('providers-active-model')).toHaveText(
        `当前默认模型：${GATEWAY_MODEL}`,
        { timeout: 60_000 }
      );
      await expect(page.getByTestId('model-gateway-status')).toContainText('使用中');
      await page.screenshot({
        path: 'test-results/gateway-creds-live-1258.png',
        fullPage: true,
      });
    }
  );
});
