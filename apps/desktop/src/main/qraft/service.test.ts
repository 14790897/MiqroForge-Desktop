import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
  existsSync,
  linkSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { QraftService, resolveConfig, defaultRedirectUri } from './service';
import { QraftStore } from './store';
import { QraftError, type QraftClient, type QraftLogger } from './client';
import type { QraftStoredState, QraftTokens } from './types';

const noopLog = (() => undefined) as unknown as QraftLogger;

function makeTokens(overrides: Partial<QraftTokens> = {}): QraftTokens {
  return {
    accessToken: 'ACCESS-TOKEN',
    refreshToken: 'REFRESH-TOKEN',
    openid: 'OPENID',
    expiresAt: Date.now() + 7_199_000, // 实测 expires_in=7199
    ...overrides,
  };
}

function makeStoredState(overrides: Partial<QraftStoredState> = {}): QraftStoredState {
  return {
    version: 1,
    env: 'test',
    baseUrl: 'https://test.forge.miqroera.com/api',
    clientId: 'miqi',
    clientSecret: 'test-client-secret',
    redirectUri: 'http://localhost:38000/callback',
    cookie: 'Authorization=uuid-1',
    account: { phone: '18500000000', sub: '19', username: 'U-HKY4-GB4E', nickname: 'MiQi测试' },
    tokens: makeTokens(),
    ...overrides,
  };
}

interface ClientStub {
  platformLogin: ReturnType<typeof vi.fn>;
  authorizeFlow: ReturnType<typeof vi.fn>;
  exchangeCode: ReturnType<typeof vi.fn>;
  refreshTokens: ReturnType<typeof vi.fn>;
  getUserInfo: ReturnType<typeof vi.fn>;
}

function makeClientStub(): ClientStub {
  return {
    platformLogin: vi.fn(),
    authorizeFlow: vi.fn(),
    exchangeCode: vi.fn(),
    refreshTokens: vi.fn(),
    getUserInfo: vi.fn(),
  };
}

let dir: string;
let store: QraftStore;
let statusEvents: unknown[];

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'qraft-service-'));
  store = new QraftStore(join(dir, 'qraft-auth.json'), null, noopLog);
  statusEvents = [];
  // 测试环境 client_secret 不落仓库，测试从环境变量注入
  process.env.QRAFT_TEST_CLIENT_SECRET = 'test-env-secret';
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  delete process.env.QRAFT_TEST_CLIENT_SECRET;
  vi.useRealTimers();
});

function makeService(clientStub: ClientStub): QraftService {
  return new QraftService({
    client: clientStub as unknown as QraftClient,
    store,
    log: noopLog,
    makeRedirectUri: () => 'http://localhost:38000/callback',
    tokenFilePath: () => join(dir, 'qraft-token.json'),
    onStatusChanged: (status) => statusEvents.push(status),
  });
}

describe('resolveConfig', () => {
  it('未提供任何参数时使用测试环境默认值 + 生成 loopback 回调', () => {
    const config = resolveConfig({}, null, () => 'http://localhost:39999/callback');
    expect(config.baseUrl).toBe('https://test.forge.miqroera.com/api');
    expect(config.clientId).toBe('miqi');
    // QRAFT_TEST_CLIENT_SECRET 环境变量优先（beforeEach 注入）
    expect(config.clientSecret).toBe('test-env-secret');
    expect(config.redirectUri).toBe('http://localhost:39999/callback');
  });

  it('未注入环境变量时测试环境 client_secret 使用硬编码默认值（测试阶段开箱即用）', () => {
    delete process.env.QRAFT_TEST_CLIENT_SECRET;
    const config = resolveConfig({}, null, () => 'http://localhost:39999/callback');
    expect(config.clientSecret).toBe('miqi123456');
  });

  it('生产环境默认 client_secret 使用硬编码默认值（测试阶段开箱即用）、不自动生成 redirect_uri', () => {
    const config = resolveConfig({ env: 'prod' }, null, () => 'http://localhost:1/callback');
    expect(config.baseUrl).toBe('https://forge.miqroera.com/api');
    expect(config.clientSecret).toBe('miqi123456');
    expect(config.redirectUri).toBe('');
  });

  it('QRAFT_PROD_CLIENT_SECRET 环境变量可覆盖生产默认值', () => {
    process.env.QRAFT_PROD_CLIENT_SECRET = 'prod-override';
    try {
      const config = resolveConfig({ env: 'prod' }, null, () => 'http://localhost:1/callback');
      expect(config.clientSecret).toBe('prod-override');
    } finally {
      delete process.env.QRAFT_PROD_CLIENT_SECRET;
    }
  });

  it('用户覆盖优先于环境默认与上次存储', () => {
    const stored = makeStoredState({ env: 'test', baseUrl: 'https://old.example.com/api' });
    const config = resolveConfig(
      { baseUrl: 'https://new.example.com/api', clientSecret: 'secret-2' },
      stored,
      () => 'http://localhost:2/callback'
    );
    expect(config.baseUrl).toBe('https://new.example.com/api');
    expect(config.clientSecret).toBe('secret-2');
    expect(config.clientId).toBe('miqi');
  });

  it('redirect_uri 复用同环境上次存储值（同一登录态内保持一致）', () => {
    const stored = makeStoredState({ redirectUri: 'http://localhost:38000/callback' });
    const config = resolveConfig({}, stored, () => 'http://localhost:9/callback');
    expect(config.redirectUri).toBe('http://localhost:38000/callback');
  });

  it('切到生产环境时不串用测试环境存储的配置（baseUrl/secret/redirect_uri）', () => {
    const stored = makeStoredState({
      env: 'test',
      baseUrl: 'https://test.forge.miqroera.com/api',
      clientSecret: 'test-client-secret',
      redirectUri: 'http://localhost:38000/callback',
    });
    const config = resolveConfig({ env: 'prod' }, stored, () => 'http://localhost:9/callback');
    expect(config.baseUrl).toBe('https://forge.miqroera.com/api');
    expect(config.clientSecret).toBe('miqi123456'); // 生产默认值，非测试环境存储值
    expect(config.redirectUri).toBe('');
  });
});

describe('defaultRedirectUri', () => {
  it('生成带随机端口的 loopback 回调', () => {
    const uri = defaultRedirectUri();
    expect(uri).toMatch(/^http:\/\/localhost:\d+\/callback$/);
    const port = Number(uri.match(/:(\d+)\//)?.[1]);
    expect(port).toBeGreaterThanOrEqual(1024);
    expect(port).toBeLessThanOrEqual(65535);
  });
});

describe('QraftService.login', () => {
  it('成功登录：平台登录 → 授权码流程 → userinfo → 落盘 → 推送状态', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: '平台昵称',
    });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: 'MiQi测试',
    });
    const service = makeService(stub);

    const result = await service.login('18500000000', 'password');
    expect(result.ok).toBe(true);
    expect(result.account).toEqual({
      phone: '18500000000',
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: 'MiQi测试',
    });

    const status = service.status();
    expect(status.loggedIn).toBe(true);
    expect(status.account?.nickname).toBe('MiQi测试');
    expect(status.requiresRelogin).toBe(false);
    // 密码只传给 platformLogin，且 store 中不保存密码
    expect(store.current?.account.phone).toBe('18500000000');
    expect(JSON.stringify(store.current)).not.toContain('password');
    expect(statusEvents.length).toBeGreaterThan(0);
    // 自动刷新已调度（到期前 15 分钟）
    expect(status.refreshScheduledAt).toBeGreaterThan(Date.now());
  });

  it('userinfo 失败不阻断登录，回退平台登录响应信息', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'U', nickname: '登录昵称' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockRejectedValue(new QraftError('USERINFO_FAILED', 'userinfo boom'));
    const service = makeService(stub);

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true);
    expect(result.account?.nickname).toBe('登录昵称');
  });

  it('生产环境未填注册 redirect_uri 报 INVALID_CONFIG（不自动生成 loopback）', async () => {
    const stub = makeClientStub();
    const service = makeService(stub);
    const result = await service.login('18500000000', 'p', {
      env: 'prod',
      clientSecret: 'prod-secret',
    });
    expect(result.ok).toBe(false);
    expect(result.code).toBe('INVALID_CONFIG');
    expect(result.message).toContain('redirect_uri');
    expect(stub.platformLogin).not.toHaveBeenCalled();
  });

  it('登录失败返回错误码与提示，不落盘', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockRejectedValue(new QraftError('LOGIN_FAILED', '登录失败：密码错误'));
    const service = makeService(stub);
    const result = await service.login('18500000000', 'bad');
    expect(result).toEqual({ ok: false, code: 'LOGIN_FAILED', message: '登录失败：密码错误' });
    expect(store.current).toBeNull();
    expect(service.status().loggedIn).toBe(false);
  });
});

describe('QraftService.loginWithCode（浏览器登录路径）', () => {
  it('成功：code 换 token → userinfo → 落盘 → 推送状态（账号信息以 userinfo 为准）', async () => {
    const stub = makeClientStub();
    stub.exchangeCode.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({
      sub: '19',
      username: 'U-BROWSER',
      nickname: '浏览器用户',
    });
    const service = makeService(stub);

    const result = await service.loginWithCode('browser-code', { env: 'test' });
    expect(result.ok).toBe(true);
    expect(result.account).toEqual({
      phone: '', // 浏览器路径无手机号
      sub: '19',
      username: 'U-BROWSER',
      nickname: '浏览器用户',
    });

    const status = service.status();
    expect(status.loggedIn).toBe(true);
    expect(status.account?.nickname).toBe('浏览器用户');
    expect(store.current?.tokens.accessToken).toBe('ACCESS-TOKEN');
    // 浏览器路径没有平台登录 cookie
    expect(store.current?.cookie).toBe('');
    expect(statusEvents.length).toBeGreaterThan(0);
  });

  it('userinfo 失败不阻断登录，账号信息留空', async () => {
    const stub = makeClientStub();
    stub.exchangeCode.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockRejectedValue(new QraftError('USERINFO_FAILED', 'boom'));
    const service = makeService(stub);

    const result = await service.loginWithCode('browser-code');
    expect(result.ok).toBe(true);
    expect(result.account).toEqual({ phone: '', sub: '', username: '', nickname: '' });
    expect(service.status().loggedIn).toBe(true);
  });

  it('换 token 失败返回错误码，不落盘', async () => {
    const stub = makeClientStub();
    stub.exchangeCode.mockRejectedValue(new QraftError('TOKEN_EXCHANGE_FAILED', 'code 无效'));
    const service = makeService(stub);

    const result = await service.loginWithCode('stale-code');
    expect(result).toEqual({ ok: false, code: 'TOKEN_EXCHANGE_FAILED', message: 'code 无效' });
    expect(store.current).toBeNull();
    expect(service.status().loggedIn).toBe(false);
  });
});

describe('QraftService 自动刷新', () => {
  it('到期前 15 分钟自动刷新；刷新成功更新 token 并重新调度', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    // 惰性计算 expiresAt：假时钟推进后每次刷新都返回"从现在起 2 小时"，
    // 避免桩数据里的固定过期时间被时间推进越过导致循环刷新。
    stub.refreshTokens.mockImplementation(async () =>
      makeTokens({ accessToken: 'ACCESS-NEW', expiresAt: Date.now() + 7_199_000 })
    );
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    const delay = 7_199_000 - 15 * 60_000; // expires_in - 15min
    await vi.advanceTimersByTimeAsync(delay + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);
    expect(store.current?.tokens.accessToken).toBe('ACCESS-NEW');
    expect(service.status().refreshError).toBeUndefined();
    expect(service.status().requiresRelogin).toBe(false);
    // 重新调度了下一次刷新
    expect(service.status().refreshScheduledAt).toBeGreaterThan(Date.now());
  });

  it('自动刷新失败标记 requiresRelogin，30 分钟后重试', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens.mockRejectedValue(new QraftError('REFRESH_FAILED', 'refresh_token 无效'));
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    const delay = 7_199_000 - 15 * 60_000;
    await vi.advanceTimersByTimeAsync(delay + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);
    expect(service.status().refreshError).toBe('REFRESH_FAILED');
    expect(service.status().requiresRelogin).toBe(true);

    // 30 分钟后自动重试一次
    await vi.advanceTimersByTimeAsync(30 * 60_000 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(2);
  });

  it('refresh_token 已失效（永久错误）不再自动重试，标记需重新登录', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens.mockRejectedValue(
      new QraftError('REFRESH_TOKEN_INVALID', 'refresh_token 已失效，请重新登录')
    );
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    const delay = 7_199_000 - 15 * 60_000;
    await vi.advanceTimersByTimeAsync(delay + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);
    expect(service.status().refreshError).toBe('REFRESH_TOKEN_INVALID');
    expect(service.status().requiresRelogin).toBe(true);
    // 不再调度下一次重试（refreshScheduledAt 清空）
    expect(service.status().refreshScheduledAt).toBeUndefined();

    // 30 分钟后仍不重试（无新请求、无新定时器）
    await vi.advanceTimersByTimeAsync(30 * 60_000 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);
  });

  it('应用启动时恢复登录态并调度刷新', () => {
    vi.useFakeTimers();
    store.save(makeStoredState({ tokens: makeTokens({ expiresAt: Date.now() + 7_199_000 }) }));
    const stub = makeClientStub();
    const service = makeService(stub);
    expect(service.status().loggedIn).toBe(true);
    expect(service.status().refreshScheduledAt).toBeGreaterThan(Date.now());
  });
});

describe('QraftService 手动刷新与退出', () => {
  it('refreshNow 成功后清除刷新错误', async () => {
    const stub = makeClientStub();
    stub.refreshTokens.mockResolvedValue(makeTokens({ accessToken: 'NEW' }));
    store.save(makeStoredState());
    const service = makeService(stub);
    const result = await service.refreshNow();
    expect(result.ok).toBe(true);
    expect(store.current?.tokens.accessToken).toBe('NEW');
  });

  it('refreshNow 失败返回错误并标记需重新登录', async () => {
    const stub = makeClientStub();
    stub.refreshTokens.mockRejectedValue(new QraftError('REFRESH_FAILED', '无效'));
    store.save(makeStoredState());
    const service = makeService(stub);
    const result = await service.refreshNow();
    expect(result.ok).toBe(false);
    expect(result.code).toBe('REFRESH_FAILED');
    expect(service.status().requiresRelogin).toBe(true);
  });

  it('refreshNow 永久失败（REFRESH_TOKEN_INVALID）撤销自动刷新定时器', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens.mockRejectedValue(
      new QraftError('REFRESH_TOKEN_INVALID', 'refresh_token 已失效，请重新登录')
    );
    const service = makeService(stub);
    await service.login('18500000000', 'p');
    // 登录后已调度自动刷新（约 105 分钟后）
    expect(service.status().refreshScheduledAt).toBeGreaterThan(Date.now());

    const result = await service.refreshNow();
    expect(result.code).toBe('REFRESH_TOKEN_INVALID');
    // 永久失败撤销定时器：计划时间清空，时间推进到原计划点也不会再请求
    expect(service.status().refreshScheduledAt).toBeUndefined();
    await vi.advanceTimersByTimeAsync(7_199_000);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1); // 仅手动那一次
  });

  it('并发刷新去重：手动与自动刷新共享同一次 refreshTokens 请求', async () => {
    let resolveRefresh!: (t: QraftTokens) => void;
    const refreshPromise = new Promise<QraftTokens>((r) => {
      resolveRefresh = r;
    });
    const stub = makeClientStub();
    stub.refreshTokens.mockReturnValue(refreshPromise);
    store.save(makeStoredState());
    const service = makeService(stub);

    const first = service.refreshNow();
    const second = service.refreshNow();
    await Promise.resolve(); // 让两个调用都进入 doRefresh
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);

    resolveRefresh(makeTokens({ accessToken: 'DEDUPED' }));
    const [r1, r2] = await Promise.all([first, second]);
    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(true);
    expect(store.current?.tokens.accessToken).toBe('DEDUPED');
  });

  it('logout 清除 cookie 与 token，推送未登录状态', async () => {
    const stub = makeClientStub();
    store.save(makeStoredState());
    const service = makeService(stub);
    expect(service.status().loggedIn).toBe(true);

    service.logout();
    expect(service.status().loggedIn).toBe(false);
    expect(store.current).toBeNull();
    expect(statusEvents.some((s) => (s as { loggedIn: boolean }).loggedIn === false)).toBe(true);
  });
});

describe('QraftService token 文件通道（供 Skill/agent 读取 access_token）', () => {
  const tokenPath = () => join(dir, 'qraft-token.json');

  it('登录成功后写入 token 文件（仅 accessToken + expiresAt，无 refreshToken）', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    const service = makeService(stub);

    await service.login('18500000000', 'p');
    expect(existsSync(tokenPath())).toBe(true);
    const content = JSON.parse(readFileSync(tokenPath(), 'utf8'));
    expect(content.accessToken).toBe('ACCESS-TOKEN');
    expect(content.expiresAt).toBeGreaterThan(Date.now());
    expect(content).not.toHaveProperty('refreshToken');
  });

  it('自动刷新成功后更新 token 文件中的 accessToken', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens.mockImplementation(async () =>
      makeTokens({ accessToken: 'ACCESS-NEW', expiresAt: Date.now() + 7_199_000 })
    );
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    await vi.advanceTimersByTimeAsync(7_199_000 - 15 * 60_000 + 100);
    const content = JSON.parse(readFileSync(tokenPath(), 'utf8'));
    expect(content.accessToken).toBe('ACCESS-NEW');
  });

  it('退出登录删除 token 文件', async () => {
    const stub = makeClientStub();
    store.save(makeStoredState());
    const service = makeService(stub);
    expect(existsSync(tokenPath())).toBe(true); // 构造时从磁盘恢复并同步

    service.logout();
    expect(existsSync(tokenPath())).toBe(false);
  });

  it('tokenFilePath 返回 null 时静默跳过（workspace 不可解析不报错）', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    const service = new QraftService({
      client: stub as unknown as QraftClient,
      store,
      log: noopLog,
      makeRedirectUri: () => 'http://localhost:38000/callback',
      tokenFilePath: () => null,
    });

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true);
    service.logout();
  });
});

describe('QraftService 登出竞态与 token 文件防护', () => {
  it('在途刷新完成于退出登录之后：丢弃结果，不写回 store 与 token 文件', async () => {
    let resolveRefresh!: (t: QraftTokens) => void;
    const refreshPromise = new Promise<QraftTokens>((r) => {
      resolveRefresh = r;
    });
    const stub = makeClientStub();
    stub.refreshTokens.mockReturnValue(refreshPromise);
    store.save(makeStoredState());
    const service = makeService(stub);
    const tokenPath = join(dir, 'qraft-token.json');
    expect(existsSync(tokenPath)).toBe(true); // 启动恢复时已同步

    const pendingRefresh = service.refreshNow();
    service.logout();
    expect(existsSync(tokenPath)).toBe(false);

    // 刷新在登出后才完成 —— 结果必须被代际校验丢弃
    resolveRefresh(makeTokens({ accessToken: 'LATE-RESULT' }));
    await pendingRefresh;
    expect(store.current).toBeNull();
    expect(existsSync(tokenPath)).toBe(false);
    expect(service.status().loggedIn).toBe(false);
  });

  it('.qraft 路径被普通文件占用时跳过写入且不崩溃', async () => {
    const blockedDir = join(dir, 'blocked-dir');
    writeFileSync(blockedDir, 'not-a-dir', 'utf8');
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    const service = new QraftService({
      client: stub as unknown as QraftClient,
      store,
      log: noopLog,
      makeRedirectUri: () => 'http://localhost:38000/callback',
      tokenFilePath: () => join(blockedDir, 'token.json'),
    });

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true); // 登录不受 token 文件失败影响
    expect(store.current?.tokens.accessToken).toBe('ACCESS-TOKEN');
  });

  it.skipIf(process.platform === 'win32')('.qraft 为符号链接时拒绝写入目标位置', async () => {
    const targetDir = join(dir, 'target-dir');
    mkdirSync(targetDir, { recursive: true });
    const linkDir = join(dir, 'qraft-link');
    symlinkSync(targetDir, linkDir, 'dir');

    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    const service = new QraftService({
      client: stub as unknown as QraftClient,
      store,
      log: noopLog,
      makeRedirectUri: () => 'http://localhost:38000/callback',
      tokenFilePath: () => join(linkDir, 'token.json'),
    });

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true);
    // 凭据绝不能写到 symlink 指向的目标目录
    expect(existsSync(join(targetDir, 'token.json'))).toBe(false);
  });

  it('预置 token 文件经硬链接共享时：原子替换不覆写共享 inode', async () => {
    // 攻击者在 .qraft 预置一个 token 文件并持有硬链接别名。若实现原地
    // writeFileSync，凭据会写进共享 inode，攻击者经别名即可读到；原子
    // 替换（临时文件 + rename）后别名仍应是旧内容。
    const qraftDir = join(dir, 'qraft-hardlink');
    mkdirSync(qraftDir, { recursive: true });
    const tokenPath = join(qraftDir, 'token.json');
    const attackerAlias = join(dir, 'attacker-alias.json');
    writeFileSync(tokenPath, '{"planted":"old"}', 'utf8');
    linkSync(tokenPath, attackerAlias);

    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    const service = new QraftService({
      client: stub as unknown as QraftClient,
      store,
      log: noopLog,
      makeRedirectUri: () => 'http://localhost:38000/callback',
      tokenFilePath: () => tokenPath,
    });

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true);
    // 主路径已被替换为新凭据
    expect(JSON.parse(readFileSync(tokenPath, 'utf8')).accessToken).toBe('ACCESS-TOKEN');
    // 攻击者别名仍是旧内容 —— 凭据未落入共享 inode
    expect(JSON.parse(readFileSync(attackerAlias, 'utf8'))).toEqual({ planted: 'old' });
  });

  it.skipIf(process.platform === 'win32')('预置文件为其他用户所有时拒绝写入', async () => {
    const qraftDir = join(dir, 'qraft-owned');
    mkdirSync(qraftDir, { recursive: true });
    const tokenPath = join(qraftDir, 'token.json');
    writeFileSync(tokenPath, '{"planted":"attacker"}', 'utf8');
    // 模拟非本用户 uid（测试进程无法 chown）：owner 检查据此拒绝写入。
    // process.getuid 仅 POSIX 存在，类型上需断言（tsconfig.node 不含该声明）。
    const realUid = process.getuid?.() ?? 0;
    const getuidSpy = vi
      .spyOn(process as unknown as { getuid: () => number }, 'getuid')
      .mockReturnValue(realUid + 1);
    try {
      const stub = makeClientStub();
      stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
      stub.authorizeFlow.mockResolvedValue(makeTokens());
      stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
      const service = new QraftService({
        client: stub as unknown as QraftClient,
        store,
        log: noopLog,
        makeRedirectUri: () => 'http://localhost:38000/callback',
        tokenFilePath: () => tokenPath,
      });

      const result = await service.login('18500000000', 'p');
      expect(result.ok).toBe(true); // 登录不受 token 文件失败影响
      // 凭据绝不能写入其他用户拥有的文件
      expect(JSON.parse(readFileSync(tokenPath, 'utf8'))).toEqual({ planted: 'attacker' });
    } finally {
      getuidSpy.mockRestore();
    }
  });
});

describe('QraftService AI 网关字段（#922）', () => {
  const GATEWAY = {
    encryptedApiKey: 'sk-test-gateway-secret',
    status: 'active',
    configVersion: 2,
    consumerId: 'C-1',
  };
  const userinfoWithGateway = () => ({
    sub: '19',
    username: 'U-GATEWAY',
    nickname: '网关用户',
    aiGateway: { ...GATEWAY },
  });

  it('登录后：store 保存 encryptedApiKey；status() 只透出非敏感字段', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue(userinfoWithGateway());
    const service = makeService(stub);

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true);
    // 加密 store 内保存完整 aiGateway（含密钥）
    expect(store.current?.aiGateway).toEqual(GATEWAY);
    // 透给渲染进程的状态只含 status/configVersion，不含 encryptedApiKey
    expect(service.status().aiGateway).toEqual({ status: 'active', configVersion: 2 });
    expect(JSON.stringify(service.status())).not.toContain('sk-test-gateway-secret');
  });

  it('token 文件写入 aiGateway 块（Python 握手）；登出删除', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue(userinfoWithGateway());
    const service = makeService(stub);
    const tokenPath = () => join(dir, 'qraft-token.json');

    await service.login('18500000000', 'p');
    const content = JSON.parse(readFileSync(tokenPath(), 'utf8'));
    expect(content.aiGateway).toEqual(GATEWAY);
    expect(content.accessToken).toBe('ACCESS-TOKEN');

    service.logout();
    expect(existsSync(tokenPath())).toBe(false);
    expect(store.current).toBeNull();
  });

  it('自动刷新重写 token 文件时保留 aiGateway（刷新不重拉 userinfo）', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue(userinfoWithGateway());
    stub.refreshTokens.mockImplementation(async () =>
      makeTokens({ accessToken: 'ACCESS-NEW', expiresAt: Date.now() + 7_199_000 })
    );
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    await vi.advanceTimersByTimeAsync(7_199_000 - 15 * 60_000 + 100);
    const content = JSON.parse(readFileSync(join(dir, 'qraft-token.json'), 'utf8'));
    expect(content.accessToken).toBe('ACCESS-NEW');
    expect(content.aiGateway).toEqual(GATEWAY);
    expect(store.current?.aiGateway).toEqual(GATEWAY);
  });

  it('浏览器登录路径同样携带 aiGateway', async () => {
    const stub = makeClientStub();
    stub.exchangeCode.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue(userinfoWithGateway());
    const service = makeService(stub);

    const result = await service.loginWithCode('browser-code');
    expect(result.ok).toBe(true);
    expect(store.current?.aiGateway?.encryptedApiKey).toBe('sk-test-gateway-secret');
    expect(service.status().aiGateway?.status).toBe('active');
  });
});

// ── Slurm 作业扣费（issue #927）─────────────────────────────────────────

interface ChargeClientStub {
  deductPoints: ReturnType<typeof vi.fn>;
  refreshTokens: ReturnType<typeof vi.fn>;
}

function makeChargeClient(): ChargeClientStub {
  return { deductPoints: vi.fn(), refreshTokens: vi.fn() };
}

function makeChargeService(clientStub: ChargeClientStub): QraftService {
  return new QraftService({
    client: clientStub as unknown as QraftClient,
    store,
    log: noopLog,
    makeRedirectUri: () => 'http://localhost:38000/callback',
    tokenFilePath: () => join(dir, 'qraft-token.json'),
    billingHistoryPath: () => join(dir, 'billing-history.json'),
    onStatusChanged: (status) => statusEvents.push(status),
  });
}

const SLURM_PAYLOAD = {
  charge_id: 'charge-abc',
  job_id: '12345',
  server_name: 'slurm',
  tool_name: 'submit_job',
  args_summary: '{"script": "job.sh"}',
  session_key: 'desktop:default',
  turn_id: 'turn-1',
};

describe('QraftService Slurm 作业扣费（issue #927）', () => {
  it('登录态下扣费成功：10 分 + memo 携带作业信息，历史落 billed 记录', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockResolvedValue({
      availablePoints: 840,
      heldPoints: 0,
      totalEarned: 0,
      totalSpent: 10,
    });
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    const result = await svc.chargeSlurmJob(SLURM_PAYLOAD);

    expect(result).toEqual({ ok: true, balance: 840 });
    const [configArg, tokenArg, reqArg] = client.deductPoints.mock.calls[0] as any[];
    expect(tokenArg).toBe('ACCESS-TOKEN');
    expect(reqArg.amount).toBe(10);
    expect(reqArg.source).toBe('slurm-job');
    expect(reqArg.resourceType).toBe('slurm');
    const memo = JSON.parse(reqArg.memo);
    expect(memo.tool).toBe('slurm.submit_job');
    expect(memo.args).toContain('job.sh');
    expect(memo.session).toBe('desktop:default');

    const history = svc.getBillingHistory();
    expect(history).toHaveLength(1);
    expect(history[0]).toMatchObject({
      chargeId: 'charge-abc',
      cost: 10,
      status: 'billed',
      balanceAfter: 840,
    });
    // 余额缓存更新并推送状态
    expect(statusEvents.some((s: any) => s?.points?.availablePoints === 840)).toBe(true);
  });

  it('同一 charge_id 只扣一次（历史持久化去重，跨重启不重复扣费）', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockResolvedValue({
      availablePoints: 840,
      heldPoints: 0,
      totalEarned: 0,
      totalSpent: 10,
    });
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    await svc.chargeSlurmJob(SLURM_PAYLOAD);
    const again = await svc.chargeSlurmJob(SLURM_PAYLOAD);

    expect(again.ok).toBe(true);
    expect(again.balance).toBe(840);
    expect(client.deductPoints).toHaveBeenCalledTimes(1);
    // 新实例（模拟重启）读历史文件同样不重复扣
    const svc2 = makeChargeService(client);
    const third = await svc2.chargeSlurmJob(SLURM_PAYLOAD);
    expect(third.ok).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(1);
  });

  it('余额不足（40003）fail-closed：返回阻止并记 insufficient 历史', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockRejectedValue(
      new QraftError('INSUFFICIENT_POINTS', '可用积分不足（当前可用 5）')
    );
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    const result = await svc.chargeSlurmJob(SLURM_PAYLOAD);

    expect(result.ok).toBe(false);
    expect(result.code).toBe('INSUFFICIENT_POINTS');
    expect(result.message).toContain('可用积分不足');
    const history = svc.getBillingHistory();
    expect(history).toHaveLength(1);
    expect(history[0].status).toBe('insufficient');
    expect(history[0].chargeId).toBe('charge-abc');
  });

  it('token 失效时先刷新一次再重试扣费', async () => {
    const client = makeChargeClient();
    client.deductPoints
      .mockRejectedValueOnce(new QraftError('SESSION_EXPIRED', 'access_token 已失效'))
      .mockResolvedValueOnce({
        availablePoints: 840,
        heldPoints: 0,
        totalEarned: 0,
        totalSpent: 10,
      });
    client.refreshTokens.mockResolvedValue(makeTokens({ accessToken: 'FRESH-TOKEN' }));
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    const result = await svc.chargeSlurmJob(SLURM_PAYLOAD);

    expect(result.ok).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(2);
    expect((client.deductPoints.mock.calls[1] as any[])[1]).toBe('FRESH-TOKEN');
  });

  it('未登录时不发请求，返回 INVALID_CONFIG', async () => {
    const client = makeChargeClient();
    const svc = makeChargeService(client);

    const result = await svc.chargeSlurmJob(SLURM_PAYLOAD);

    expect(result.ok).toBe(false);
    expect(result.code).toBe('INVALID_CONFIG');
    expect(client.deductPoints).not.toHaveBeenCalled();
  });

  it('RUNNING 事件携带作业 ID：历史记录与 memo 均含 jobId', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockResolvedValue({
      availablePoints: 840,
      heldPoints: 0,
      totalEarned: 0,
      totalSpent: 10,
    });
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    await svc.chargeSlurmJob(SLURM_PAYLOAD);

    const history = svc.getBillingHistory();
    expect(history[0].jobId).toBe('12345');
    const memo = JSON.parse((client.deductPoints.mock.calls[0] as any[])[2].memo);
    expect(memo.jobId).toBe('12345');
  });

  it('同一作业 ID 只扣一次（轮询重复报告 RUNNING 不重复扣费）', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockResolvedValue({
      availablePoints: 840,
      heldPoints: 0,
      totalEarned: 0,
      totalSpent: 10,
    });
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    await svc.chargeSlurmJob(SLURM_PAYLOAD);
    // 状态轮询再次报告同一作业 RUNNING（新 charge_id、同 job_id）→ 去重
    const again = await svc.chargeSlurmJob({
      ...SLURM_PAYLOAD,
      charge_id: 'charge-later',
    });

    expect(again.ok).toBe(true);
    expect(again.dedup).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(1);
  });

  it('不同 MCP 服务器上报相同 job_id 独立计费（复合键含 server_name）', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockResolvedValue({
      availablePoints: 840,
      heldPoints: 0,
      totalEarned: 0,
      totalSpent: 10,
    });
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    // slurm-a 与 slurm-b 各自有作业 12345：互不干扰，各扣一次
    await svc.chargeSlurmJob(SLURM_PAYLOAD);
    const second = await svc.chargeSlurmJob({
      ...SLURM_PAYLOAD,
      charge_id: 'charge-bbb',
      server_name: 'slurm-b',
    });
    expect(second.ok).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(2);

    // 同服务器同 job_id 仍被去重（第三声 charge_id 也拦得住）
    const dup = await svc.chargeSlurmJob({
      ...SLURM_PAYLOAD,
      charge_id: 'charge-later',
    });
    expect(dup.ok).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(2);

    // 新实例（模拟重启）：历史按复合键匹配，两服务器的作业各自保持
    // 已计费状态且互不串扰（slurm-b/12345 不再触发新扣费）
    const svc2 = makeChargeService(client);
    const restartHit = await svc2.chargeSlurmJob({
      ...SLURM_PAYLOAD,
      charge_id: 'charge-after-restart',
      server_name: 'slurm-b',
    });
    expect(restartHit.ok).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(2);
  });

  it('缺少 job_id 的计费请求在扣费前被拒绝（CodeRabbit #936）', async () => {
    const client = makeChargeClient();
    store.save(makeStoredState());
    const svc = makeChargeService(client);

    const result = await svc.chargeSlurmJob({ ...SLURM_PAYLOAD, job_id: '' });

    expect(result.ok).toBe(false);
    expect(result.code).toBe('INVALID_CONFIG');
    expect(result.message).toContain('job_id');
    expect(client.deductPoints).not.toHaveBeenCalled();
  });
});
