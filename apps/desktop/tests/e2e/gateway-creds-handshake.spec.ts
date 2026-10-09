/**
 * 网关凭据「握手文件」失效时的保存语义（#1258）。
 *
 * 背景（真实用户两次遇到）：登录后应用把默认模型写成网关模型
 * `deepseek/deepseek-v4-flash`，后端却回
 * `Error invoking remote method 'config:update': Error: Unsupported model: …
 *  (INVALID_PARAMS)`，平台侧「重新登录就好了」。
 *
 * 成因不在平台：渲染进程判定「网关可用」用的是**内存里的登录态 store**
 * （aiGateway.status==='active'），而后端保存门控读的是**磁盘上的握手文件**
 * `<workspace>/.qraft/token.json`（Python read_gateway_creds）。两者之间
 * 从不校验一致性，且握手文件的每一次失败都是静默的：
 *   - tokenFilePath() 解析失败 → 直接 return（连日志都没有）；
 *   - syncTokenFile 的守卫/写入失败 → 只有一条 WARN；
 *   - Python 读失败（缺失/损坏/OSError）→ 静默当成「无凭据」。
 * 于是只要握手文件没能落到后端读的那个路径上，用户看到的就是
 * 「Unsupported model」——一个既误导（模型没选错）又不可重试（自动就绪
 * 重试 4 次后静默放弃）的错误。
 *
 * 本 spec 用真实主进程链路复现这条链路，并固定修复后的契约：
 *   1. 握手文件缺失 → 保存网关模型必须给出**可重试**的
 *      GATEWAY_CREDS_UNAVAILABLE，而不是 Unsupported model；
 *   2. 重新同步握手文件（qraft.syncToken）后重试一次即可保存成功；
 *   3. 握手文件**写不进去**（.qraft 被 junction/symlink 占用，Windows 上
 *      lstat 会把 junction 报成 symlink，syncTokenFile 的守卫会拒绝写入）
 *      时，同步必须**显式报告失败**，不能像以前那样静默只留一条 WARN。
 *
 * 不依赖 MiQroForge 网络：登录态由测试预置（plain 信封），与 ai-gateway.spec.ts 同策略。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync, symlinkSync } from 'node:fs';
import {
  closeElectronApp,
  getAccountWorkspaceDir,
  launchElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';
const GATEWAY_KEY = 'sk-e2e-gateway-secret-key';
const GATEWAY_MODEL = 'deepseek/deepseek-v4-flash';
const ACCOUNT_SUB = '19';

/** 预置网关 active 的登录态（plain 信封，QraftStore 无 safeStorage 时降级读取）。 */
function buildSeededStoreContent(): string {
  const state: Record<string, unknown> = {
    version: 1,
    env: 'test',
    baseUrl: 'https://test.forge.miqroera.com/api',
    clientId: 'miqi',
    clientSecret: 'test-client-secret',
    redirectUri: 'http://localhost:38000/callback',
    cookie: 'Authorization=e2e-test-cookie',
    account: {
      phone: '18500000000',
      sub: ACCOUNT_SUB,
      username: 'E2E-GATEWAY',
      nickname: 'E2E网关测试',
    },
    tokens: {
      accessToken: 'e2e-fake-access-token',
      refreshToken: 'e2e-fake-refresh-token',
      openid: 'e2e-fake-openid',
      expiresAt: Date.now() + 7_199_000,
    },
    aiGateway: {
      encryptedApiKey: GATEWAY_KEY,
      status: 'active',
      configVersion: 1,
      consumerId: 'C-E2E',
    },
  };
  return JSON.stringify({
    v: 1,
    enc: 'plain',
    payload: Buffer.from(JSON.stringify(state), 'utf8').toString('base64'),
  });
}

/**
 * 直接调渲染层的 config.update（用户报错里那一条 IPC），返回错误文案或 null。
 *
 * 只对**桥请求超时**这一种基础设施抖动重试（文案见 bridge 的
 * `Request <method> timed out`）：它与本 spec 断言的业务语义无关，机器繁忙时
 * 会偶发（实测挂过一次）。任何真实业务错误立即返回，绝不被重试掩盖。
 */
async function saveGatewayModel(page: Page): Promise<string | null> {
  const once = () =>
    page.evaluate(async (model) => {
      try {
        await (
          window as unknown as { miqi: { config: { update: (c: unknown) => Promise<unknown> } } }
        ).miqi.config.update({ agents: { defaults: { model } } });
        return null;
      } catch (err) {
        return err instanceof Error ? err.message : String(err);
      }
    }, GATEWAY_MODEL);

  let last: string | null = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    last = await once();
    if (last === null || !/timed out/i.test(last)) return last;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  return last;
}

let storePath: string;

/** 打开 设置 → 模型 tab（模型面板 ModelQuickPanel 所在处）。 */
async function gotoModelTab(page: Page): Promise<void> {
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page.getByRole('tab', { name: '模型' }).click();
}

test.describe('网关凭据握手失效时的保存语义（#1258）', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;
  let workspaceDir: string;
  let tokenFile: string;

  test.beforeAll(async () => {
    storePath = join(tmpdir(), `qraft-handshake-e2e-store-${process.pid}.json`);
    process.env[STORE_ENV] = storePath;
    writeFileSync(storePath, buildSeededStoreContent(), 'utf8');
    fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    workspaceDir = getAccountWorkspaceDir(fixture.miqiHome, ACCOUNT_SUB);
    tokenFile = join(workspaceDir, '.qraft', 'token.json');
    // 握手文件由开机时的登录态恢复写出来（QraftService 构造函数 → syncTokenFile）。
    await expect.poll(() => existsSync(tokenFile), { timeout: 30_000 }).toBe(true);
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    if (electronApp) await closeElectronApp(electronApp, fixture?.miqiHome);
    if (existsSync(storePath)) rmSync(storePath, { force: true });
  });

  test('握手文件缺失：报可重试的 GATEWAY_CREDS_UNAVAILABLE，不再说 Unsupported model', async () => {
    test.setTimeout(120_000);
    // 复现「桥的磁盘视图读不到凭据」：渲染进程仍认为网关可用（store 里有密钥），
    // 但后端读的那个文件不在了。
    rmSync(tokenFile, { force: true });

    const err = await saveGatewayModel(page);

    expect(err).not.toBeNull();
    // 修复前这里是 `Unsupported model: … (INVALID_PARAMS)`——误导且不可重试。
    expect(err!).toContain('GATEWAY_CREDS_UNAVAILABLE');
    expect(err!).not.toContain('Unsupported model');
    await page.screenshot({
      path: 'test-results/gateway-creds-unavailable-1258.png',
      fullPage: true,
    });
  });

  test('重新同步握手文件后重试一次即写入成功（前端自愈路径）', async () => {
    test.setTimeout(120_000);
    rmSync(tokenFile, { force: true }); // 每个用例自带前置条件

    const synced = await page.evaluate(() =>
      (
        window as unknown as { miqi: { qraft: { syncToken: () => Promise<unknown> } } }
      ).miqi.qraft.syncToken()
    );
    expect(synced).toMatchObject({ ok: true });
    expect(existsSync(tokenFile)).toBe(true);
    expect(readFileSync(tokenFile, 'utf8')).toContain('"aiGateway"');

    const err = await saveGatewayModel(page);
    expect(err).toBeNull();

    const saved = JSON.parse(readFileSync(join(fixture.miqiHome, 'config.json'), 'utf8'));
    expect(saved.agents?.defaults?.model).toBe(GATEWAY_MODEL);

    // 界面留证：模型面板显示网关「使用中」
    await gotoModelTab(page);
    await expect(page.getByTestId('providers-active-model')).toHaveText(
      `当前默认模型：${GATEWAY_MODEL}`,
      { timeout: 30_000 }
    );
    await expect(page.getByTestId('model-gateway-status')).toContainText('使用中');
    await page.screenshot({
      path: 'test-results/gateway-creds-ui-ok-1258.png',
      fullPage: true,
    });
  });

  test('真实界面路径：模型面板里保存网关模型 → 显示可执行的提示（不再是 Unsupported model）', async () => {
    test.setTimeout(120_000);
    await gotoModelTab(page);
    // 断掉后端读的凭据：这次保存必然走「凭据不可用」分支
    rmSync(tokenFile, { force: true });

    await page.locator('select').first().selectOption(GATEWAY_MODEL);
    await page
      .getByRole('button', { name: /^(保存|已保存)$/ })
      .first()
      .click();

    // 面板内联错误（ModelQuickPanel 的 error 节点）：给用户的是可执行的中文提示
    const errorBox = page.getByText(/网关凭据对当前账号不可用/).first();
    await expect(errorBox).toBeVisible({ timeout: 30_000 });
    await expect(errorBox).not.toContainText('Unsupported model');
    await expect(errorBox).not.toContainText('Error invoking remote method');

    await page.screenshot({
      path: 'test-results/gateway-creds-ui-error-1258.png',
      fullPage: true,
    });
  });

  test('握手文件写不进去（.qraft 被 junction 占用）：同步显式报错，不再静默', async () => {
    test.setTimeout(120_000);
    // Windows 上 lstat 把目录 junction 报成 symlink → syncTokenFile 的守卫
    // 拒绝写入。修复前只有一条 WARN，调用方完全看不到（静默吞掉）。
    const qraftDir = join(workspaceDir, '.qraft');
    const junctionTarget = join(fixture.miqiHome, 'qraft-junction-target');
    rmSync(qraftDir, { recursive: true, force: true });
    mkdirSync(junctionTarget, { recursive: true });
    symlinkSync(junctionTarget, qraftDir, 'junction');

    const synced = (await page.evaluate(() =>
      (
        window as unknown as { miqi: { qraft: { syncToken: () => Promise<unknown> } } }
      ).miqi.qraft.syncToken()
    )) as { ok: boolean; message?: string };

    expect(synced.ok).toBe(false);
    expect(String(synced.message ?? '')).not.toBe('');

    // 保存仍必须给出可重试语义（凭据就是没到位），而不是说模型不存在。
    const err = await saveGatewayModel(page);
    expect(err!).toContain('GATEWAY_CREDS_UNAVAILABLE');

    rmSync(qraftDir, { recursive: true, force: true });
  });
});
