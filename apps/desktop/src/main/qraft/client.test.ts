import { describe, expect, it, vi } from 'vitest';
import { generateKeyPairSync } from 'node:crypto';
import {
  QraftClient,
  QraftError,
  buildAuthorizeUrl,
  extractCodeForRedirect,
  extractCodeFromLocation,
  parseBusinessJson,
  type FetchLike,
  type FetchResponseLike,
} from './client';
import { CookieJar } from './cookie-jar';
import type { ResolvedQraftConfig } from './client';

// ── mock fetch 工具 ──────────────────────────────────────────────────────

// 真实 RSA 密钥对：password 加密链路在测试中真实执行。
const { publicKey: TEST_PUBLIC_PEM } = generateKeyPairSync('rsa', {
  modulusLength: 2048,
  publicKeyEncoding: { type: 'spki', format: 'pem' },
  privateKeyEncoding: { type: 'pkcs8', format: 'pem' },
});

function jsonHeaders(): Record<string, string> {
  return { 'content-type': 'application/json' };
}

function mockResponse(
  status: number,
  body: string,
  headers: Record<string, string> = {}
): FetchResponseLike {
  const lower: Record<string, string> = {};
  for (const [k, v] of Object.entries(headers)) lower[k.toLowerCase()] = v;
  return {
    status,
    headers: {
      get: (name: string) => lower[name.toLowerCase()] ?? null,
      getSetCookie: () => (lower['set-cookie'] ? [lower['set-cookie']] : []),
    },
    text: async () => body,
    json: async () => JSON.parse(body),
  };
}

type Route = {
  method?: string;
  url: RegExp;
  response: FetchResponseLike | (() => FetchResponseLike);
};

/** 按顺序匹配路由的 mock fetch；未命中抛出 TypeError（模拟网络失败）。 */
function createFetchMock(
  routes: Route[],
  onCall?: (url: string, init?: { method?: string; body?: string }) => void
): FetchLike {
  return async (url, init) => {
    onCall?.(url, init);
    for (const route of routes) {
      if (route.method && (init?.method ?? 'GET') !== route.method) continue;
      if (route.url.test(url)) {
        return typeof route.response === 'function' ? route.response() : route.response;
      }
    }
    throw new TypeError('fetch failed');
  };
}

const silentLog = () => undefined;
const noopLog = silentLog as unknown as import('./client').QraftLogger;

const CONFIG: ResolvedQraftConfig = {
  baseUrl: 'https://test.forge.miqroera.com/api',
  clientId: 'miqi',
  clientSecret: 'test-client-secret',
  redirectUri: 'http://localhost:38000/callback',
};

const LOGIN_PAGE_HTML = `
  <html><head><script src="/static/js/era-index-abc123.js"></script></head></html>`;
// bundle 中以字符串字面量形式携带公钥（换行转义），与真实 era-index-*.js 一致。
const BUNDLE_JS = `var pub="${TEST_PUBLIC_PEM.replace(/\n/g, '\\n')}";`;

const LOGIN_OK_JSON = JSON.stringify({
  code: 200,
  message: '登录成功',
  data: {
    tokenName: 'Authorization',
    tokenValue: 'test-auth-cookie',
    userId: '19',
    username: 'U-HKY4-GB4E',
    nickname: 'MiQi测试',
    roleCode: 'user',
  },
});

const TOKEN_OK_JSON = JSON.stringify({
  code: 200,
  msg: 'ok',
  token_type: 'bearer',
  access_token: 'ACCESS-TOKEN-abcdef',
  refresh_token: 'REFRESH-TOKEN-abcdef',
  expires_in: '7199',
  refresh_expires_in: '2591999',
  client_id: 'miqi',
  scope: 'openid,userinfo,oidc',
  openid: '69144a8f0f1bedac1084e3e1ebf4d723',
  id_token: 'FAKE-ID-TOKEN-FOR-TEST',
});

const USERINFO_JSON = JSON.stringify({
  sub: '19',
  username: 'U-HKY4-GB4E',
  nickname: 'MiQi测试',
});

describe('extractCodeFromLocation', () => {
  it('从 302 Location 解析 code', () => {
    expect(extractCodeFromLocation('http://localhost:8080/callback?code=1wnXdda7PQ2iOODUm')).toBe(
      '1wnXdda7PQ2iOODUm'
    );
  });

  it('无 code / 非法 URL 返回 null', () => {
    expect(extractCodeFromLocation('http://localhost:8080/callback')).toBeNull();
    expect(extractCodeFromLocation(null)).toBeNull();
    expect(extractCodeFromLocation('not a url')).toBeNull();
  });

  it('相对 Location 通过 base 解析（HTTP 允许相对路径）', () => {
    expect(extractCodeFromLocation('/callback?code=rel-code')).toBeNull();
    expect(
      extractCodeFromLocation('/callback?code=rel-code', 'https://test.forge.miqroera.com/api')
    ).toBe('rel-code');
  });
});

describe('extractCodeForRedirect（浏览器登录回调拦截）', () => {
  const redirectUri = 'http://localhost:38000/callback';

  it('origin 与 path 完全一致时提取 code', () => {
    expect(
      extractCodeForRedirect('http://localhost:38000/callback?code=browser-code-1', redirectUri)
    ).toBe('browser-code-1');
  });

  it('端口不一致视为非授权回调（防止误拦其他 localhost 页面）', () => {
    expect(
      extractCodeForRedirect('http://localhost:39999/callback?code=x', redirectUri)
    ).toBeNull();
  });

  it('路径不一致 / 无 code / 非法 URL 返回 null', () => {
    expect(extractCodeForRedirect('http://localhost:38000/other?code=x', redirectUri)).toBeNull();
    expect(extractCodeForRedirect('http://localhost:38000/callback', redirectUri)).toBeNull();
    expect(extractCodeForRedirect('not a url', redirectUri)).toBeNull();
  });
});

describe('buildAuthorizeUrl', () => {
  it('不带 state、scope 用字面逗号、redirect_uri 编码（与实测文档一致）', () => {
    const url = buildAuthorizeUrl(CONFIG);
    expect(url).toBe(
      'https://test.forge.miqroera.com/api/oauth2/authorize?response_type=code&client_id=miqi&scope=openid,userinfo,oidc&redirect_uri=http%3A%2F%2Flocalhost%3A38000%2Fcallback'
    );
    expect(url).not.toContain('state=');
    expect(url).toContain('scope=openid,userinfo,oidc');
  });
});

describe('parseBusinessJson', () => {
  it('解析业务信封', () => {
    expect(parseBusinessJson('{"code":200,"msg":"ok"}').code).toBe(200);
  });

  it('非 JSON 返回 code=-1', () => {
    expect(parseBusinessJson('<html>nginx</html>').code).toBe(-1);
  });
});

describe('QraftClient.platformLogin', () => {
  it('提取公钥 → RSA 加密密码 → 登录 → 保存 Authorization cookie', async () => {
    const calls: string[] = [];
    const fetch = createFetchMock(
      [
        { url: /forge\.miqroera\.com\/login$/, response: mockResponse(200, LOGIN_PAGE_HTML) },
        { url: /era-index-abc123\.js$/, response: mockResponse(200, BUNDLE_JS) },
        {
          method: 'POST',
          url: /\/portal\/auth\/login$/,
          response: mockResponse(200, LOGIN_OK_JSON, {
            'Set-Cookie': 'Authorization=test-auth-cookie; Path=/',
            ...jsonHeaders(),
          }),
        },
      ],
      (url) => calls.push(url)
    );
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    const account = await client.platformLogin(CONFIG, '18500000000', 'not-a-real-password', jar);

    expect(account).toEqual({ sub: '19', username: 'U-HKY4-GB4E', nickname: 'MiQi测试' });
    expect(jar.get('Authorization')).toBe('test-auth-cookie');
    expect(calls).toEqual([
      'https://test.forge.miqroera.com/login',
      'https://test.forge.miqroera.com/static/js/era-index-abc123.js',
      'https://test.forge.miqroera.com/api/portal/auth/login',
    ]);
  });

  it('登录响应未下发 cookie 时报 LOGIN_FAILED', async () => {
    const fetch = createFetchMock([
      { url: /forge\.miqroera\.com\/login$/, response: mockResponse(200, LOGIN_PAGE_HTML) },
      { url: /era-index/, response: mockResponse(200, BUNDLE_JS) },
      {
        method: 'POST',
        url: /\/portal\/auth\/login$/,
        response: mockResponse(200, LOGIN_OK_JSON, jsonHeaders()),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(
      client.platformLogin(CONFIG, '18500000000', 'not-a-real-password', new CookieJar())
    ).rejects.toMatchObject({ code: 'LOGIN_FAILED' });
  });

  it('业务 code != 200 时报 LOGIN_FAILED 并透出服务端 message', async () => {
    const fetch = createFetchMock([
      { url: /forge\.miqroera\.com\/login$/, response: mockResponse(200, LOGIN_PAGE_HTML) },
      { url: /era-index/, response: mockResponse(200, BUNDLE_JS) },
      {
        method: 'POST',
        url: /\/portal\/auth\/login$/,
        response: mockResponse(200, JSON.stringify({ code: 500, message: '密码错误' }), {
          'Set-Cookie': 'Authorization=x; Path=/',
          ...jsonHeaders(),
        }),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(
      client.platformLogin(CONFIG, '18500000000', 'bad', new CookieJar())
    ).rejects.toMatchObject({ code: 'LOGIN_FAILED', message: '登录失败：密码错误' });
  });

  it('登录页找不到 era bundle 时报 PUBLIC_KEY_EXTRACT_FAILED', async () => {
    const fetch = createFetchMock([
      {
        url: /forge\.miqroera\.com\/login$/,
        response: mockResponse(200, '<html><script src="/app.js"></script></html>'),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(
      client.platformLogin(CONFIG, '18500000000', 'x', new CookieJar())
    ).rejects.toMatchObject({ code: 'PUBLIC_KEY_EXTRACT_FAILED' });
  });
});

describe('QraftClient.authorizeFlow', () => {
  const AUTHORIZE_URL =
    /\/oauth2\/authorize\?response_type=code&client_id=miqi&scope=openid,userinfo,oidc&redirect_uri=http%3A%2F%2Flocalhost%3A38000%2Fcallback/;
  const DO_CONFIRM_URL = /\/oauth2\/doConfirm\?/;
  const TOKEN_URL = /\/oauth2\/token$/;

  it('完整流程：200 确认页 → doConfirm → 302 取 code → 换 token（不传 state）', async () => {
    const code = '1wnXdda7PQ2iOODUmfmaXWUJsLkkLN8H0SXSn5ac7QFqlcvib6quXEtYHUAy';
    const authorizedUrls: string[] = [];
    const fetch = createFetchMock(
      [
        {
          url: AUTHORIZE_URL,
          response: () => {
            // 第一次调用返回确认页，第二次返回 302 code —— 用调用次数区分。
            authorizedUrls.push('authorize');
            if (authorizedUrls.filter((u) => u === 'authorize').length === 1) {
              return mockResponse(200, '<html>授权确认页</html>');
            }
            return mockResponse(302, '', {
              location: `http://localhost:38000/callback?code=${code}`,
            });
          },
        },
        {
          method: 'POST',
          url: DO_CONFIRM_URL,
          response: mockResponse(200, JSON.stringify({ code: 200, msg: 'ok' }), jsonHeaders()),
        },
        {
          method: 'POST',
          url: TOKEN_URL,
          response: mockResponse(200, TOKEN_OK_JSON, jsonHeaders()),
        },
      ],
      (url, init) => {
        if (url.includes('/oauth2/authorize')) {
          // 实测要点：authorize 不传 state。
          expect(url).not.toContain('state=');
          expect(init?.method ?? 'GET').toBe('GET');
        }
      }
    );
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    jar.set('Authorization', 'uuid-1');
    const tokens = await client.authorizeFlow(CONFIG, jar);

    expect(tokens.accessToken).toBe('ACCESS-TOKEN-abcdef');
    expect(tokens.refreshToken).toBe('REFRESH-TOKEN-abcdef');
    expect(tokens.openid).toBe('69144a8f0f1bedac1084e3e1ebf4d723');
    // 实测 expires_in=7199（约 2 小时）
    const expectedExpiry = Date.now() + 7199 * 1000;
    expect(Math.abs(tokens.expiresAt - expectedExpiry)).toBeLessThan(5_000);
  });

  it('已确认授权时第一次 authorize 直接 302 带 code，跳过 doConfirm', async () => {
    const code = 'already-confirmed-code';
    const doConfirmCalled = vi.fn();
    const fetch = createFetchMock(
      [
        {
          url: AUTHORIZE_URL,
          response: mockResponse(302, '', {
            location: `http://localhost:38000/callback?code=${code}`,
          }),
        },
        {
          method: 'POST',
          url: TOKEN_URL,
          response: mockResponse(200, TOKEN_OK_JSON, jsonHeaders()),
        },
      ],
      (url) => {
        if (url.includes('doConfirm')) doConfirmCalled();
      }
    );
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    jar.set('Authorization', 'uuid-1');
    const tokens = await client.authorizeFlow(CONFIG, jar);
    expect(tokens.accessToken).toBe('ACCESS-TOKEN-abcdef');
    expect(doConfirmCalled).not.toHaveBeenCalled();
  });

  it('302 到登录页（登录态失效）报 SESSION_EXPIRED', async () => {
    const fetch = createFetchMock([
      {
        url: AUTHORIZE_URL,
        response: mockResponse(302, '', { location: 'https://test.forge.miqroera.com/login' }),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    jar.set('Authorization', 'uuid-stale');
    await expect(client.authorizeFlow(CONFIG, jar)).rejects.toMatchObject({
      code: 'SESSION_EXPIRED',
    });
  });

  it('doConfirm 业务失败报 AUTHORIZE_FAILED', async () => {
    const fetch = createFetchMock([
      { url: AUTHORIZE_URL, response: mockResponse(200, '<html>授权确认页</html>') },
      {
        method: 'POST',
        url: DO_CONFIRM_URL,
        response: mockResponse(200, JSON.stringify({ code: 500, msg: '系统繁忙' }), jsonHeaders()),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    jar.set('Authorization', 'uuid-1');
    await expect(client.authorizeFlow(CONFIG, jar)).rejects.toMatchObject({
      code: 'AUTHORIZE_FAILED',
    });
  });

  it('第二次 authorize 未返回 code 报 AUTHORIZE_FAILED', async () => {
    const fetch = createFetchMock([
      { url: AUTHORIZE_URL, response: mockResponse(200, '<html>还是确认页</html>') },
      {
        method: 'POST',
        url: DO_CONFIRM_URL,
        response: mockResponse(200, '{"code":200,"msg":"ok"}', jsonHeaders()),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    jar.set('Authorization', 'uuid-1');
    await expect(client.authorizeFlow(CONFIG, jar)).rejects.toMatchObject({
      code: 'AUTHORIZE_FAILED',
    });
  });

  it('换 token 响应缺少 access_token 报 TOKEN_EXCHANGE_FAILED', async () => {
    const code = 'code-without-token';
    const fetch = createFetchMock([
      {
        url: AUTHORIZE_URL,
        response: mockResponse(302, '', {
          location: `http://localhost:38000/callback?code=${code}`,
        }),
      },
      {
        method: 'POST',
        url: TOKEN_URL,
        response: mockResponse(
          200,
          JSON.stringify({ code: 500, msg: '应用暂未开放此授权模式' }),
          jsonHeaders()
        ),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const jar = new CookieJar();
    jar.set('Authorization', 'uuid-1');
    await expect(client.authorizeFlow(CONFIG, jar)).rejects.toMatchObject({
      code: 'TOKEN_EXCHANGE_FAILED',
    });
  });
});

describe('QraftClient.exchangeCode（浏览器登录路径直接用 code 换 token）', () => {
  it('POST /oauth2/token 携带 authorization_code 表单，返回 tokens', async () => {
    let capturedBody = '';
    const fetch = createFetchMock(
      [
        {
          method: 'POST',
          url: /\/oauth2\/token$/,
          response: mockResponse(200, TOKEN_OK_JSON, jsonHeaders()),
        },
      ],
      (_url, init) => {
        capturedBody = init?.body ?? '';
      }
    );
    const client = new QraftClient(fetch, noopLog);
    const tokens = await client.exchangeCode(CONFIG, 'browser-code');

    expect(tokens.accessToken).toBe('ACCESS-TOKEN-abcdef');
    const form = new URLSearchParams(capturedBody);
    expect(form.get('grant_type')).toBe('authorization_code');
    expect(form.get('code')).toBe('browser-code');
    expect(form.get('client_id')).toBe('miqi');
    expect(form.get('client_secret')).toBe('test-client-secret');
    expect(form.get('redirect_uri')).toBe('http://localhost:38000/callback');
  });
});

describe('QraftClient.refreshTokens', () => {
  it('用 refresh_token 刷新；响应携带新 refresh_token（轮换语义）时以新值为准', async () => {
    const body = JSON.stringify({
      code: 200,
      msg: 'ok',
      token_type: 'bearer',
      access_token: 'NEW-ACCESS',
      refresh_token: 'NEW-REFRESH',
      expires_in: '7199',
    });
    const fetch = createFetchMock([
      {
        method: 'POST',
        url: /\/oauth2\/refresh$/,
        response: mockResponse(200, body, jsonHeaders()),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const tokens = await client.refreshTokens(CONFIG, 'REFRESH-TOKEN-abcdef');
    expect(tokens.refreshToken).toBe('NEW-REFRESH');
    expect(tokens.accessToken).toBe('NEW-ACCESS');
    expect(tokens.expiresAt).toBeGreaterThan(Date.now());
  });

  it('成功响应缺少 refresh_token（轮换语义下无法续期）→ REFRESH_TOKEN_INVALID', async () => {
    const body = JSON.stringify({
      code: 200,
      msg: 'ok',
      token_type: 'bearer',
      access_token: 'NEW-ACCESS',
      expires_in: '7199',
    });
    const fetch = createFetchMock([
      {
        method: 'POST',
        url: /\/oauth2\/refresh$/,
        response: mockResponse(200, body, jsonHeaders()),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.refreshTokens(CONFIG, 'REFRESH-TOKEN-abcdef')).rejects.toMatchObject({
      code: 'REFRESH_TOKEN_INVALID',
    });
  });

  it('refresh_token 已失效（平台作废）分类为 REFRESH_TOKEN_INVALID，且不泄漏 token 原文', async () => {
    // 实测平台返回：originalMessage 会回显 refresh_token 原文。
    const body = JSON.stringify({
      code: 500,
      msg: '未知错误',
      data: {
        message: '未知错误',
        originalMessage: 'SaOAuth2RefreshTokenException: 无效refresh_token: STALE-TOKEN-VALUE',
      },
    });
    const fetch = createFetchMock([
      {
        method: 'POST',
        url: /\/oauth2\/refresh$/,
        response: mockResponse(200, body, jsonHeaders()),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.refreshTokens(CONFIG, 'STALE-TOKEN-VALUE')).rejects.toMatchObject({
      code: 'REFRESH_TOKEN_INVALID',
    });
    await expect(client.refreshTokens(CONFIG, 'STALE-TOKEN-VALUE')).rejects.toSatisfy(
      (err: unknown) => err instanceof Error && !err.message.includes('STALE-TOKEN-VALUE'),
      '错误消息不得包含 refresh_token 原文'
    );
  });

  it('其他业务错误保持 REFRESH_FAILED（瞬时错误，服务层继续重试）', async () => {
    const fetch = createFetchMock([
      {
        method: 'POST',
        url: /\/oauth2\/refresh$/,
        response: mockResponse(
          200,
          JSON.stringify({ code: 500, msg: '服务繁忙，请稍后重试' }),
          jsonHeaders()
        ),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.refreshTokens(CONFIG, 'STALE')).rejects.toMatchObject({
      code: 'REFRESH_FAILED',
    });
  });
});

describe('QraftClient.getUserInfo', () => {
  it('返回 sub/username/nickname（实测无 picture 字段）', async () => {
    const fetch = createFetchMock([
      { url: /\/oauth2\/userinfo$/, response: mockResponse(200, USERINFO_JSON, jsonHeaders()) },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const info = await client.getUserInfo(CONFIG, 'ACCESS-TOKEN');
    expect(info).toEqual({ sub: '19', username: 'U-HKY4-GB4E', nickname: 'MiQi测试' });
    expect('picture' in info).toBe(false);
  });

  it('401 报 SESSION_EXPIRED', async () => {
    const fetch = createFetchMock([
      { url: /\/oauth2\/userinfo$/, response: mockResponse(401, 'unauthorized') },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.getUserInfo(CONFIG, 'BAD')).rejects.toMatchObject({
      code: 'SESSION_EXPIRED',
    });
  });

  it('解析顶层平铺的网关字段（encryptedApiKey/aiGatewayStatus/configVersion）', async () => {
    const body = JSON.stringify({
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: 'MiQi测试',
      encryptedApiKey: 'sk-test-top-level',
      aiGatewayStatus: 'active',
      configVersion: 1,
      consumerId: 'C-123',
    });
    const fetch = createFetchMock([
      { url: /\/oauth2\/userinfo$/, response: mockResponse(200, body, jsonHeaders()) },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const info = await client.getUserInfo(CONFIG, 'ACCESS-TOKEN');
    expect(info).toEqual({
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: 'MiQi测试',
      aiGateway: {
        encryptedApiKey: 'sk-test-top-level',
        status: 'active',
        configVersion: 1,
        consumerId: 'C-123',
      },
    });
  });

  it('解析嵌套在 data 内的网关字段；configVersion 数字字符串归一为 number', async () => {
    const body = JSON.stringify({
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: 'MiQi测试',
      data: {
        encryptedApiKey: 'sk-test-nested',
        aiGatewayStatus: 'active',
        configVersion: '3',
      },
    });
    const fetch = createFetchMock([
      { url: /\/oauth2\/userinfo$/, response: mockResponse(200, body, jsonHeaders()) },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const info = await client.getUserInfo(CONFIG, 'ACCESS-TOKEN');
    expect(info.aiGateway).toEqual({
      encryptedApiKey: 'sk-test-nested',
      status: 'active',
      configVersion: 3,
    });
  });

  it('encryptedApiKey 缺失（即使有 aiGatewayStatus）时整体省略 aiGateway', async () => {
    const body = JSON.stringify({
      sub: '19',
      username: 'U-HKY4-GB4E',
      nickname: 'MiQi测试',
      aiGatewayStatus: 'disabled',
      configVersion: 0,
    });
    const fetch = createFetchMock([
      { url: /\/oauth2\/userinfo$/, response: mockResponse(200, body, jsonHeaders()) },
    ]);
    const client = new QraftClient(fetch, noopLog);
    const info = await client.getUserInfo(CONFIG, 'ACCESS-TOKEN');
    expect(info).toEqual({ sub: '19', username: 'U-HKY4-GB4E', nickname: 'MiQi测试' });
    expect('aiGateway' in info).toBe(false);
  });
});

describe('QraftClient 错误分类与重试', () => {
  it('统一 403（nginx 默认页，IP 未加白）报 IP_NOT_WHITELISTED 且不重试', async () => {
    let calls = 0;
    const fetch = createFetchMock(
      [{ url: /.*/, response: mockResponse(403, '<html><body>403 Forbidden nginx</body></html>') }],
      () => {
        calls += 1;
      }
    );
    const client = new QraftClient(fetch, noopLog);
    await expect(client.getUserInfo(CONFIG, 'TOKEN')).rejects.toMatchObject({
      code: 'IP_NOT_WHITELISTED',
    });
    expect(calls).toBe(1);
  });

  it('瞬时网络错误自动重试，成功后返回（模拟实测 HTTP 000 抖动）', async () => {
    let calls = 0;
    const fetch: FetchLike = async (url) => {
      calls += 1;
      if (calls <= 2) throw new TypeError('fetch failed');
      return mockResponse(200, USERINFO_JSON, jsonHeaders());
    };
    const client = new QraftClient(fetch, noopLog);
    const info = await client.getUserInfo(CONFIG, 'TOKEN');
    expect(info.nickname).toBe('MiQi测试');
    expect(calls).toBe(3);
  }, 10_000);

  it('重试耗尽后报 NETWORK_UNREACHABLE', async () => {
    const fetch: FetchLike = async () => {
      throw new TypeError('fetch failed');
    };
    const client = new QraftClient(fetch, noopLog);
    await expect(client.getUserInfo(CONFIG, 'TOKEN')).rejects.toMatchObject({
      code: 'NETWORK_UNREACHABLE',
    });
  }, 10_000);

  it('日志中不出现明文密码与 token', async () => {
    const lines: string[] = [];
    const log = ((_level: string, message: string) => {
      lines.push(message);
    }) as unknown as import('./client').QraftLogger;
    const fetch = createFetchMock([
      { url: /forge\.miqroera\.com\/login$/, response: mockResponse(200, LOGIN_PAGE_HTML) },
      { url: /era-index/, response: mockResponse(200, BUNDLE_JS) },
      {
        method: 'POST',
        url: /\/portal\/auth\/login$/,
        response: mockResponse(200, LOGIN_OK_JSON, {
          'Set-Cookie': 'Authorization=test-auth-cookie; Path=/',
          ...jsonHeaders(),
        }),
      },
    ]);
    const client = new QraftClient(fetch, log);
    await client.platformLogin(CONFIG, '18500000000', 'super-secret-password', new CookieJar());
    const joined = lines.join('\n');
    expect(joined).not.toContain('super-secret-password');
    expect(joined).not.toContain('18500000000'); // 手机号也只出现脱敏片段
  });
});

describe('QraftError', () => {
  it('携带稳定错误码', () => {
    const err = new QraftError('IP_NOT_WHITELISTED', '出口 IP 未加白，请联系 MiQroForge 管理员');
    expect(err.code).toBe('IP_NOT_WHITELISTED');
    expect(err.message).toContain('出口 IP 未加白');
  });
});

// ── 积分接口（OAuth2 第三方接入指南）────────────────────────────────────

const POINTS_OK = JSON.stringify({
  code: 200,
  message: 'ok',
  data: { availablePoints: 270, heldPoints: 0, totalEarned: 300, totalSpent: 30 },
});

describe('QraftClient.getPointsBalance', () => {
  it('GET /oauth2/points/balance 返回余额字段', async () => {
    const calls: Array<{ url: string; auth: string | null }> = [];
    const fetch = createFetchMock(
      [
        {
          url: /\/oauth2\/points\/balance$/,
          response: mockResponse(200, POINTS_OK, jsonHeaders()),
        },
      ],
      (url, init) =>
        calls.push({
          url,
          auth: (init as { headers?: Record<string, string> })?.headers?.['Authorization'] ?? null,
        })
    );
    const client = new QraftClient(fetch, noopLog);
    const balance = await client.getPointsBalance(CONFIG, 'TOKEN');
    expect(balance).toEqual({
      availablePoints: 270,
      heldPoints: 0,
      totalEarned: 300,
      totalSpent: 30,
    });
    expect(calls[0].url).toBe('https://test.forge.miqroera.com/api/oauth2/points/balance');
    expect(calls[0].auth).toBe('Bearer TOKEN');
  });

  it('40101/40102 业务码 → SESSION_EXPIRED', async () => {
    const fetch = createFetchMock([
      {
        url: /\/oauth2\/points\/balance$/,
        response: mockResponse(
          200,
          JSON.stringify({ code: 40102, message: 'access_token 无效或已过期' }),
          jsonHeaders()
        ),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.getPointsBalance(CONFIG, 'TOKEN')).rejects.toMatchObject({
      code: 'SESSION_EXPIRED',
    });
  });

  it('HTTP 401 → SESSION_EXPIRED', async () => {
    const fetch = createFetchMock([
      { url: /\/oauth2\/points\/balance$/, response: mockResponse(401, 'unauthorized') },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.getPointsBalance(CONFIG, 'TOKEN')).rejects.toMatchObject({
      code: 'SESSION_EXPIRED',
    });
  });

  it('业务失败（无 data）→ POINTS_FAILED 透出 message', async () => {
    const fetch = createFetchMock([
      {
        url: /\/oauth2\/points\/balance$/,
        response: mockResponse(
          200,
          JSON.stringify({ code: 500, message: '服务端异常' }),
          jsonHeaders()
        ),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(client.getPointsBalance(CONFIG, 'TOKEN')).rejects.toMatchObject({
      code: 'POINTS_FAILED',
      message: expect.stringContaining('服务端异常') as unknown as string,
    });
  });
});

describe('QraftClient.deductPoints', () => {
  it('POST /oauth2/points/deduct 携带金额与来源，返回扣后余额', async () => {
    const bodies: string[] = [];
    const fetch = createFetchMock(
      [
        {
          method: 'POST',
          url: /\/oauth2\/points\/deduct$/,
          response: mockResponse(200, POINTS_OK, jsonHeaders()),
        },
      ],
      (_url, init) => bodies.push((init as { body?: string })?.body ?? '')
    );
    const client = new QraftClient(fetch, noopLog);
    const balance = await client.deductPoints(CONFIG, 'TOKEN', {
      amount: 30,
      source: 'desktop-agent-task',
      memo: 'thread:thread-1',
    });
    expect(balance.availablePoints).toBe(270);
    const sent = JSON.parse(bodies[0]) as Record<string, unknown>;
    expect(sent.amount).toBe(30);
    expect(sent.source).toBe('desktop-agent-task');
    expect(sent.memo).toBe('thread:thread-1');
  });

  it('40003 余额不足 → INSUFFICIENT_POINTS 并带可用积分', async () => {
    const fetch = createFetchMock([
      {
        method: 'POST',
        url: /\/oauth2\/points\/deduct$/,
        response: mockResponse(
          200,
          JSON.stringify({ code: 40003, message: '可用积分不足', data: { availablePoints: 5 } }),
          jsonHeaders()
        ),
      },
    ]);
    const client = new QraftClient(fetch, noopLog);
    await expect(
      client.deductPoints(CONFIG, 'TOKEN', { amount: 30, source: 'desktop-agent-task' })
    ).rejects.toMatchObject({
      code: 'INSUFFICIENT_POINTS',
      message: expect.stringContaining('5') as unknown as string,
    });
  });
});
