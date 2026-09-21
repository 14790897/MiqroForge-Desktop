/**
 * MiQroForge 真实环境集成测试（可选，默认跳过）。
 *
 * 用法（凭据优先从环境变量读取，client_secret 测试阶段有默认值）：
 *   QRAFT_LIVE=1 QRAFT_PHONE=<测试账号手机号> QRAFT_PASSWORD=<密码> \
 *     npx vitest run src/main/qraft/live.integration.test.ts
 *
 * QRAFT_BASE_URL 可覆盖平台地址（默认测试环境），用于验证新域名。
 *
 * 走通完整流程：提取公钥 → 平台登录（RSA 加密）→ authorize → doConfirm
 * → 取 code → 换 token → userinfo → refresh。断言只检查脱敏摘要，
 * 不打印任何凭据。
 */

import { describe, expect, it } from 'vitest';
import { CookieJar } from './cookie-jar';
import { createQraftClient, QraftError, type QraftLogger } from './client';
import { maskSecret } from './rsa';
import type { ResolvedQraftConfig } from './client';

const LIVE = process.env.QRAFT_LIVE === '1';
const PHONE = process.env.QRAFT_PHONE ?? '';
const PASSWORD = process.env.QRAFT_PASSWORD ?? '';
// 测试阶段硬编码默认值；QRAFT_CLIENT_SECRET 可覆盖
const CLIENT_SECRET = process.env.QRAFT_CLIENT_SECRET ?? 'miqi123456';

const silentLog = (() => undefined) as unknown as QraftLogger;

const CONFIG: ResolvedQraftConfig = {
  baseUrl: process.env.QRAFT_BASE_URL ?? 'https://test.forge.miqroera.com/api',
  clientId: 'miqi',
  clientSecret: CLIENT_SECRET,
  redirectUri: 'http://localhost:38000/callback',
};

describe.skipIf(!LIVE || !PHONE || !PASSWORD)('MiQroForge live integration', () => {
  it('完整登录流程：平台登录 → 授权码 → token → userinfo → refresh', async () => {
    const client = createQraftClient({ log: silentLog });
    const jar = new CookieJar();

    // ① 平台登录（RSA 加密密码，公钥从真实前端 bundle 动态提取）
    const loginAccount = await client.platformLogin(CONFIG, PHONE, PASSWORD, jar);
    expect(jar.get('Authorization')).toBeTruthy();
    expect(loginAccount.nickname.length).toBeGreaterThan(0);

    // ②③④⑤ 授权码流程
    const tokens = await client.authorizeFlow(CONFIG, jar);
    expect(tokens.accessToken.length).toBeGreaterThan(20);
    expect(tokens.refreshToken.length).toBeGreaterThan(20);
    expect(tokens.openid).toBeTruthy();
    // 有效期由平台下发：2026-09-21 实测 expires_in=2591999（约 30 天），
    // 早期实测 7199（约 2 小时）——不做窗口断言，只验证解析出正值并记录。
    const ttlMs = tokens.expiresAt - Date.now();
    expect(ttlMs).toBeGreaterThan(60_000);
    console.log(
      `[live] access_token=${maskSecret(tokens.accessToken)} ttl=${Math.round(ttlMs / 1000)}s`
    );

    // ⑥ 业务接口：userinfo（实测无 picture 字段）
    const info = await client.getUserInfo(CONFIG, tokens.accessToken);
    expect(info.nickname).toBeTruthy();
    expect(info.username).toBeTruthy();
    expect(info.sub).toBeTruthy();

    // ⑦ 刷新：平台当前不轮换 refresh_token（旧值可复用，2026-09-15 实测）。
    // 不强行断言轮换与否（以真实平台行为为准），只记录供排查。
    const refreshed = await client.refreshTokens(CONFIG, tokens.refreshToken);
    expect(refreshed.accessToken.length).toBeGreaterThan(20);
    expect(refreshed.refreshToken.length).toBeGreaterThan(20);
    const rotated = refreshed.refreshToken !== tokens.refreshToken;
    console.log(
      `[live] refresh ok（refresh_token ${rotated ? '已轮换' : '未轮换'}：${maskSecret(refreshed.refreshToken)}）`
    );
  }, 120_000);

  it('未加白/凭据类错误能给出分类提示（防御性验证错误映射）', async () => {
    const client = createQraftClient({ log: silentLog });
    const jar = new CookieJar();
    try {
      await client.platformLogin(CONFIG, PHONE, 'wrong-password-for-sure', jar);
    } catch (err) {
      expect(err).toBeInstanceOf(QraftError);
      const code = (err as QraftError).code;
      // 密码错误 → LOGIN_FAILED；出口 IP 未加白 → IP_NOT_WHITELISTED
      expect(['LOGIN_FAILED', 'IP_NOT_WHITELISTED']).toContain(code);
      return;
    }
    throw new Error('预期登录失败（错误密码），但请求成功了');
  }, 120_000);
});
