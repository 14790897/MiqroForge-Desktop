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
import {
  getDefaultWorkspacePath,
  readActiveAccount,
  readLegacyWorkspaceOwner,
  setActiveAccount,
} from '../ipc/workspace-path';
import { PROD_REDIRECT_URI, type QraftStoredState, type QraftTokens } from './types';

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
/** 本用例的数据根。QraftService 会往里写账号标记，见下面的注释。 */
let miqiHome: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'qraft-service-'));
  store = new QraftStore(join(dir, 'qraft-auth.json'), null, noopLog);
  statusEvents = [];
  // 每个用例都钉住 MIQI_HOME（#1185）：QraftService 的构造函数会按已存储的
  // 登录态激活账号，也就是往 `<数据根>/accounts/` 写标记文件——不钉住的话，
  // 「从存储恢复登录态」这类用例会把标记写进**真实用户目录**，既是环境污染，
  // 也会让同进程里其它读工作区的测试读到这个活跃账号而失败（CI 上正是如此）。
  miqiHome = join(dir, 'miqi-home');
  mkdirSync(miqiHome, { recursive: true });
  process.env['MIQI_HOME'] = miqiHome;
  // 测试环境 client_secret 不落仓库，测试从环境变量注入
  process.env.QRAFT_TEST_CLIENT_SECRET = 'test-env-secret';
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  delete process.env['MIQI_HOME'];
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
  it('未提供任何参数时默认生产环境 + 使用平台注册的 redirect_uri', () => {
    const config = resolveConfig({}, null, () => 'http://localhost:39999/callback');
    expect(config.baseUrl).toBe('https://www.miqroforge.com/api');
    expect(config.clientId).toBe('miqi');
    // 生产 client_secret 默认值（QRAFT_PROD_CLIENT_SECRET 未注入）
    expect(config.clientSecret).toBe('miqi123456');
    expect(config.redirectUri).toBe(PROD_REDIRECT_URI);
  });

  it('未注入环境变量时测试环境 client_secret 使用硬编码默认值（测试阶段开箱即用）', () => {
    delete process.env.QRAFT_TEST_CLIENT_SECRET;
    const config = resolveConfig({ env: 'test' }, null, () => 'http://localhost:39999/callback');
    expect(config.clientSecret).toBe('miqi123456');
  });

  it('生产环境默认 client_secret 使用硬编码默认值（测试阶段开箱即用）、redirect_uri 用平台注册值', () => {
    const config = resolveConfig({ env: 'prod' }, null, () => 'http://localhost:1/callback');
    expect(config.baseUrl).toBe('https://www.miqroforge.com/api');
    expect(config.clientSecret).toBe('miqi123456');
    expect(config.redirectUri).toBe(PROD_REDIRECT_URI);
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
    expect(config.baseUrl).toBe('https://www.miqroforge.com/api');
    expect(config.clientSecret).toBe('miqi123456'); // 生产默认值，非测试环境存储值
    expect(config.redirectUri).toBe(PROD_REDIRECT_URI);
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

  it('生产环境缺省 redirect_uri 时用平台注册值登录', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'U', nickname: '登录昵称' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'U', nickname: '登录昵称' });
    const service = makeService(stub);

    const result = await service.login('18500000000', 'p', {
      env: 'prod',
      clientSecret: 'prod-secret',
    });
    expect(result.ok).toBe(true);
    expect(stub.platformLogin).toHaveBeenCalledWith(
      expect.objectContaining({
        baseUrl: 'https://www.miqroforge.com/api',
        redirectUri: PROD_REDIRECT_URI,
      }),
      '18500000000',
      'p',
      expect.anything()
    );
  });

  it('生产环境显式传空 redirect_uri 仍报 INVALID_CONFIG（注册值要求不放松）', async () => {
    const stub = makeClientStub();
    const service = makeService(stub);
    const result = await service.login('18500000000', 'p', {
      env: 'prod',
      clientSecret: 'prod-secret',
      redirectUri: '',
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

  it('自动刷新瞬时失败不置 requiresRelogin，指数退避静默重试并自动恢复（#1087）', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens
      .mockRejectedValueOnce(new QraftError('NETWORK_UNREACHABLE', '断网'))
      .mockRejectedValueOnce(new QraftError('NETWORK_UNREACHABLE', '断网'))
      .mockImplementation(async () =>
        makeTokens({ accessToken: 'ACCESS-RECOVERED', expiresAt: Date.now() + 7_199_000 })
      );
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    const delay = 7_199_000 - 15 * 60_000;
    await vi.advanceTimersByTimeAsync(delay + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);
    // 瞬时失败：记录错误码，但不置 requiresRelogin（不弹横幅/不拦截发送）
    expect(service.status().refreshError).toBe('NETWORK_UNREACHABLE');
    expect(service.status().requiresRelogin).toBe(false);

    // 第一次退避 1 分钟后重试，仍失败 → 仍不置 requiresRelogin
    await vi.advanceTimersByTimeAsync(60_000 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(2);
    expect(service.status().requiresRelogin).toBe(false);

    // 第二次退避翻倍为 2 分钟，重试成功 → 错误清除、token 更新、重新调度
    await vi.advanceTimersByTimeAsync(120_000 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(3);
    expect(service.status().refreshError).toBeUndefined();
    expect(service.status().requiresRelogin).toBe(false);
    expect(store.current?.tokens.accessToken).toBe('ACCESS-RECOVERED');
    expect(service.status().refreshScheduledAt).toBeGreaterThan(Date.now());
  });

  it('瞬时刷新失败后 access_token 过期：仍不置 requiresRelogin（静默重试中，#1087）', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens.mockRejectedValue(new QraftError('REFRESH_FAILED', '平台 5xx'));
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    const delay = 7_199_000 - 15 * 60_000;
    await vi.advanceTimersByTimeAsync(delay + 100);
    expect(service.status().refreshError).toBe('REFRESH_FAILED');
    expect(service.status().requiresRelogin).toBe(false);

    // 越过原 access_token 过期点：瞬时失败仍在退避重试，不算登录失效
    await vi.advanceTimersByTimeAsync(16 * 60_000);
    expect(service.status().requiresRelogin).toBe(false);
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

  it('超长有效期（30 天）封顶调度：不在 1ms 截断下高频循环刷新', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    // 实测平台刷新返回 30 天有效期（约 43185 分钟，超出 setTimeout 上限）。
    // 惰性计算 expiresAt：假时钟推进后每次刷新都返回「从现在起 30 天」。
    const longLived = () => makeTokens({ expiresAt: Date.now() + 2_591_999_000 });
    stub.authorizeFlow.mockResolvedValue(longLived());
    stub.getUserInfo.mockResolvedValue({ sub: '1', username: 'u', nickname: 'n' });
    stub.refreshTokens.mockImplementation(async () => longLived());
    const service = makeService(stub);
    await service.login('18500000000', 'p');

    // 首个定时器被 setTimeout 32 位上限（约 24.8 天）封顶。越过封顶点时
    // 仍未到刷新时刻（30 天 - 15 分钟），只是重新调度，不发任何请求。
    await vi.advanceTimersByTimeAsync(2_147_483_647 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(0);

    // 推进到真正的刷新时刻：恰好刷新一次，并重新调度下一次。
    await vi.advanceTimersByTimeAsync(2_591_999_000 - 900_000 - 2_147_483_647 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);
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

  it('refreshNow 瞬时失败不标记需重新登录，排退避重试后自动恢复（#1087）', async () => {
    vi.useFakeTimers();
    const stub = makeClientStub();
    stub.refreshTokens
      .mockRejectedValueOnce(new QraftError('REFRESH_FAILED', '平台 5xx'))
      .mockResolvedValue(makeTokens({ accessToken: 'NEW' }));
    store.save(makeStoredState());
    const service = makeService(stub);
    const result = await service.refreshNow();
    expect(result.ok).toBe(false);
    expect(result.code).toBe('REFRESH_FAILED');
    // 瞬时失败：不弹横幅/不拦截发送，仅排退避重试
    expect(service.status().requiresRelogin).toBe(false);
    expect(service.status().refreshScheduledAt).toBeGreaterThan(Date.now());

    // 1 分钟后退避重试成功 → 错误清除、token 更新
    await vi.advanceTimersByTimeAsync(60_000 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(2);
    expect(service.status().refreshError).toBeUndefined();
    expect(service.status().requiresRelogin).toBe(false);
    expect(store.current?.tokens.accessToken).toBe('NEW');
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

  it('并发刷新失败去重：同一在途刷新只调度一次退避重试（CodeRabbit #1114）', async () => {
    vi.useFakeTimers();
    let rejectFirst!: (e: QraftError) => void;
    const stub = makeClientStub();
    stub.refreshTokens
      .mockImplementationOnce(() => new Promise<QraftTokens>((_, reject) => (rejectFirst = reject)))
      .mockImplementation(async () =>
        makeTokens({ accessToken: 'RECOVERED', expiresAt: Date.now() + 7_199_000 })
      );
    store.save(makeStoredState());
    const service = makeService(stub);

    // 手动与自动并发：手动立即发起，自动在到期前 15 分钟触发
    const manual = service.refreshNow();
    const delay = 7_199_000 - 15 * 60_000;
    await vi.advanceTimersByTimeAsync(delay + 100);
    // 两条路径共享同一次 refreshTokens（inFlightRefresh 去重）
    expect(stub.refreshTokens).toHaveBeenCalledTimes(1);

    rejectFirst(new QraftError('REFRESH_FAILED', '平台 5xx'));
    const result = await manual;
    expect(result.ok).toBe(false);
    expect(service.status().refreshError).toBe('REFRESH_FAILED');
    expect(service.status().requiresRelogin).toBe(false);
    // 失败只处理一次：退避代数只递增一次，首次重试仍按 1 分钟排
    expect((service.status().refreshScheduledAt ?? 0) - Date.now()).toBe(60_000);

    // 1 分钟后唯一的一次重试成功 → 错误清除、token 恢复
    await vi.advanceTimersByTimeAsync(60_000 + 100);
    expect(stub.refreshTokens).toHaveBeenCalledTimes(2);
    expect(service.status().refreshError).toBeUndefined();
    expect(store.current?.tokens.accessToken).toBe('RECOVERED');
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
    billedJobIdsPath: () => join(dir, 'billed-job-ids.json'),
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

  it('登出保留扣费历史与去重索引：重新登录同一账号记录仍在、同作业不重复扣费', async () => {
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
    expect(svc.getBillingHistory()).toHaveLength(1);

    svc.logout();
    // 未登录不展示任何记录（文件保留，但没有账号作过滤依据）
    expect(svc.getBillingHistory()).toEqual([]);
    expect(store.current).toBeNull();

    // 重新登录同一账号（模拟平台轮换 refresh_token 后重新登录 + 重启应用）
    store.save(makeStoredState());
    const svc2 = makeChargeService(client);
    expect(svc2.getBillingHistory()).toHaveLength(1);
    expect(svc2.getBillingHistory()[0].chargeId).toBe('charge-abc');

    // 去重索引独立于展示历史：模拟展示历史上限（200 条）把该记录挤出，
    // 再用**新的 charge_id** 报同一作业 —— 只能由登出后仍保留的持久化
    // 作业 ID 索引挡住（CodeRabbit #1067）。
    rmSync(join(dir, 'billing-history.json'), { force: true });
    const svc3 = makeChargeService(client); // 重启：索引与去重集合从文件恢复
    const again = await svc3.chargeSlurmJob({ ...SLURM_PAYLOAD, charge_id: 'charge-new' });
    expect(again.ok).toBe(true);
    expect((again as { dedup?: boolean }).dedup).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(1);
  });

  it('账号身份未知（空 sub）时不外发历史、也不与他账号串用去重键', async () => {
    const client = makeChargeClient();
    client.deductPoints.mockResolvedValue({
      availablePoints: 840,
      heldPoints: 0,
      totalEarned: 0,
      totalSpent: 10,
    });
    store.save(makeStoredState());
    const svc = makeChargeService(client);
    await svc.chargeSlurmJob(SLURM_PAYLOAD); // sub 19 的既有记录

    svc.logout();
    // 浏览器登录 userinfo 失败会留下空 sub 的登录态（loginWithCode 兜底）
    store.save(makeStoredState({ account: { phone: '', sub: '', username: '', nickname: '' } }));
    const svc2 = makeChargeService(client);
    // 空 sub 无过滤依据 → 不外发任何历史（含他账号记录）
    expect(svc2.getBillingHistory()).toEqual([]);

    // 同一 server/job_id 以新 charge_id 上报：不被 sub 19 的记录误挡
    const again = await svc2.chargeSlurmJob({ ...SLURM_PAYLOAD, charge_id: 'charge-unknown' });
    expect(again.ok).toBe(true);
    expect((again as { dedup?: boolean }).dedup).toBeUndefined();
    expect(client.deductPoints).toHaveBeenCalledTimes(2);

    // 空 sub 的这次扣费不落跨账号作业键（索引里只有 sub 19 的那条）
    const index = JSON.parse(readFileSync(join(dir, 'billed-job-ids.json'), 'utf8')) as string[];
    expect(index).toEqual(['19::slurm::12345']);
  });

  it('换账号登录看不到前任账号的扣费记录（读取时按 account.sub 过滤）', async () => {
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

    svc.logout();
    store.save(
      makeStoredState({
        account: { phone: '18600000000', sub: '77', username: 'U-OTHER', nickname: '其他账号' },
      })
    );
    const svc2 = makeChargeService(client);
    expect(svc2.getBillingHistory()).toEqual([]);
    // 另一位账号的同一作业照常独立计费（去重键含 account.sub）
    const other = await svc2.chargeSlurmJob({
      ...SLURM_PAYLOAD,
      charge_id: 'charge-other',
    });
    expect(other.ok).toBe(true);
    expect(client.deductPoints).toHaveBeenCalledTimes(2);
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

describe('QraftService 反馈平台通道（issue #1054）', () => {
  function makeFeedbackClient() {
    return { submitFeedback: vi.fn(), refreshTokens: vi.fn() };
  }

  it('登录态：以当前 access_token 提交，返回 ok', async () => {
    const client = makeFeedbackClient();
    client.submitFeedback.mockResolvedValue(undefined);
    store.save(makeStoredState());
    const svc = makeService(client as any);

    const result = await svc.submitPlatformFeedback({
      type: 'bug',
      content: '提交时闪退',
      contact: 'user@example.com',
    });

    expect(result).toEqual({ ok: true });
    const [, tokenArg, reqArg] = client.submitFeedback.mock.calls[0] as any[];
    expect(tokenArg).toBe('ACCESS-TOKEN');
    expect(reqArg).toEqual({ type: 'bug', content: '提交时闪退', contact: 'user@example.com' });
  });

  it('未登录：跳过平台通道（INVALID_CONFIG，不发起请求）', async () => {
    const client = makeFeedbackClient();
    const svc = makeService(client as any);

    const result = await svc.submitPlatformFeedback({ content: 'x' });

    expect(result.ok).toBe(false);
    expect(result.code).toBe('INVALID_CONFIG');
    expect(client.submitFeedback).not.toHaveBeenCalled();
  });

  it('token 失效：刷新后带新 token 重试一次，返回 ok', async () => {
    const client = makeFeedbackClient();
    client.submitFeedback
      .mockRejectedValueOnce(new QraftError('SESSION_EXPIRED', 'access_token 已失效'))
      .mockResolvedValueOnce(undefined);
    client.refreshTokens.mockResolvedValue(
      makeTokens({ accessToken: 'NEW-ACCESS', refreshToken: 'NEW-REFRESH' })
    );
    store.save(makeStoredState());
    const svc = makeService(client as any);

    const result = await svc.submitPlatformFeedback({ content: 'x' });

    expect(result).toEqual({ ok: true });
    expect(client.submitFeedback).toHaveBeenCalledTimes(2);
    expect((client.submitFeedback.mock.calls[1] as any[])[1]).toBe('NEW-ACCESS');
  });

  it('refresh_token 已作废：置 requiresRelogin 并推状态（登录失效三件套）', async () => {
    const client = makeFeedbackClient();
    client.submitFeedback.mockRejectedValue(
      new QraftError('SESSION_EXPIRED', 'access_token 已失效')
    );
    client.refreshTokens.mockRejectedValue(
      new QraftError('REFRESH_TOKEN_INVALID', 'refresh_token 已失效')
    );
    store.save(makeStoredState());
    const svc = makeService(client as any);

    const result = await svc.submitPlatformFeedback({ content: 'x' });

    expect(result).toMatchObject({ ok: false, code: 'REFRESH_TOKEN_INVALID' });
    expect(svc.status().requiresRelogin).toBe(true);
    expect(svc.status().refreshError).toBe('REFRESH_TOKEN_INVALID');
    expect(statusEvents.some((s: any) => s?.requiresRelogin === true)).toBe(true);
  });

  it('刷新失败（瞬时）：不重试提交，返回刷新错误码', async () => {
    const client = makeFeedbackClient();
    client.submitFeedback.mockRejectedValue(
      new QraftError('SESSION_EXPIRED', 'access_token 已失效')
    );
    client.refreshTokens.mockRejectedValue(new QraftError('REFRESH_FAILED', '刷新 token 失败'));
    store.save(makeStoredState());
    const svc = makeService(client as any);

    const result = await svc.submitPlatformFeedback({ content: 'x' });

    expect(result).toMatchObject({ ok: false, code: 'REFRESH_FAILED' });
    expect(client.submitFeedback).toHaveBeenCalledTimes(1);
    // 瞬时刷新失败不置 requiresRelogin（不弹横幅/不拦截发送，#1087），仅排退避重试
    expect(svc.status().requiresRelogin).toBe(false);
  });

  it('平台 400 参数校验失败：透出服务端 message（不触发重登）', async () => {
    const client = makeFeedbackClient();
    client.submitFeedback.mockRejectedValue(
      new QraftError('FEEDBACK_FAILED', '提交反馈失败：content 不能为空')
    );
    store.save(makeStoredState());
    const svc = makeService(client as any);

    const result = await svc.submitPlatformFeedback({ content: 'x' });

    expect(result).toMatchObject({ ok: false, code: 'FEEDBACK_FAILED' });
    expect(result.message).toContain('content 不能为空');
    expect(client.refreshTokens).not.toHaveBeenCalled();
    expect(svc.status().requiresRelogin).toBe(false);
  });
});

// #1185: 登录/登出驱动工作区根的账号维度。bridge 是长期驻留进程、不在登录时
// 重启，所以切换完全依赖 .active 标记文件；标错了就会把会话写进别人的工作区。
describe('账号维度的工作区根 (#1185)', () => {
  // 数据根由文件级 beforeEach 钉在临时目录上（见上面那段注释）——这一组的用例
  // 正是会触发标记文件写入的那些。
  const loggedInClient = () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '19', username: 'U', nickname: '登录昵称' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockResolvedValue({ sub: '19', username: 'U', nickname: '登录昵称' });
    return stub;
  };

  it('登录后工作区切到该账号，登出后回到共享根', async () => {
    const service = makeService(loggedInClient());

    // 登录前：无账号 → 共享工作区
    expect(readActiveAccount()).toBeNull();
    expect(getDefaultWorkspacePath()).toBe(join(miqiHome, 'workspace'));

    const result = await service.login('18500000000', 'p');
    expect(result.ok).toBe(true);
    expect(readActiveAccount()).toBe('19');
    expect(getDefaultWorkspacePath()).toBe(join(miqiHome, 'accounts', '19', 'workspace'));

    service.logout();
    expect(readActiveAccount()).toBeNull();
    expect(getDefaultWorkspacePath()).toBe(join(miqiHome, 'workspace'));
  });

  it('启动时按已存储的登录态恢复账号维度', () => {
    // 已经登录过的用户重启应用：构造函数里就要把根切过去，否则首屏的
    // sessions.list 读的是共享工作区（上一个账号的数据）。
    store.save(makeStoredState());
    makeService(makeClientStub());

    expect(readActiveAccount()).toBe('19');
    expect(getDefaultWorkspacePath()).toBe(join(miqiHome, 'accounts', '19', 'workspace'));
  });

  it('没有登录态时清掉残留标记（含 E2E loginBypass）', () => {
    setActiveAccount('19');
    makeService(makeClientStub());

    // 存储里没有登录态 → 运行时不该停在上一次会话用过的账号工作区上。
    expect(readActiveAccount()).toBeNull();
    expect(getDefaultWorkspacePath()).toBe(join(miqiHome, 'workspace'));
  });

  it('首个登录的账号认领升级前的存量工作区', async () => {
    // 升级场景：<数据根>/workspace 里已经是老用户的会话与记忆。
    mkdirSync(join(miqiHome, 'workspace', 'sessions', 'desktop_old'), { recursive: true });
    const service = makeService(loggedInClient());

    await service.login('18500000000', 'p');

    expect(readLegacyWorkspaceOwner()).toBe('19');
    expect(getDefaultWorkspacePath()).toBe(join(miqiHome, 'workspace'));
    expect(existsSync(join(miqiHome, 'workspace', 'sessions', 'desktop_old'))).toBe(true);
  });

  it('sub 为空（平台响应缺字段）时不猜目录，退回共享根', async () => {
    const stub = makeClientStub();
    stub.platformLogin.mockResolvedValue({ sub: '', username: 'U', nickname: 'N' });
    stub.authorizeFlow.mockResolvedValue(makeTokens());
    stub.getUserInfo.mockRejectedValue(new QraftError('USERINFO_FAILED', 'boom'));
    const service = makeService(stub);

    const result = await service.login('18500000000', 'p');

    expect(result.ok).toBe(true);
    expect(readActiveAccount()).toBeNull();
  });
});
