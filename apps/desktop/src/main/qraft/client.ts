/**
 * Qraft OAuth2 授权码流程客户端。
 *
 * 完整流程（《Qraft OAuth2 接入实测文档》2026-08-13，与官方文档的差异
 * 已按实测适配，见各步骤注释）：
 *   ① 平台登录  POST /portal/auth/login（RSA PKCS#1 v1.5 加密密码）
 *              → Set-Cookie: Authorization=<uuid>（后续依赖 cookie 而非 header）
 *   ② 发起授权  GET /oauth2/authorize（redirect_uri 必填、不传 state、
 *              scope=openid,userinfo,oidc；授权页 accept=1 按钮实测无效）
 *   ③ 确认授权  POST /oauth2/doConfirm（必须走该接口）
 *   ④ 取授权码  再次 GET authorize → 302 Location 里的一次性 code
 *   ⑤ 换 token  POST /oauth2/token（grant_type=authorization_code）
 *   ⑥ 刷新      POST /oauth2/refresh（新平台轮换 refresh_token：旧值刷新后立即失效）
 *
 * 所有凭据（密码、token、cookie）均不进入日志 —— 只记录步骤与脱敏摘要。
 */

import { CookieJar, type CookieJarLike } from './cookie-jar';
import {
  encryptPasswordRsa,
  extractPublicKeyFromBundle,
  findEraBundleUrls,
  maskSecret,
} from './rsa';
import type {
  QraftAccount,
  QraftAiGateway,
  QraftErrorCode,
  QraftPointsBalance,
  QraftTokens,
} from './types';

// ── 可注入依赖（生产用 electron.net.fetch，测试用 mock） ──────────────

export interface FetchResponseLike {
  status: number;
  headers: {
    get(name: string): string | null;
    getSetCookie?: () => string[];
    raw?: () => Record<string, string[]>;
  };
  text(): Promise<string>;
  json(): Promise<unknown>;
}

export interface FetchInitLike {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  redirect?: 'manual' | 'follow' | 'error';
  signal?: AbortSignal;
}

export type FetchLike = (url: string, init?: FetchInitLike) => Promise<FetchResponseLike>;

export type QraftLogLevel = 'INFO' | 'WARN' | 'ERROR';
export type QraftLogger = (level: QraftLogLevel, message: string) => void;

export class QraftError extends Error {
  constructor(
    public readonly code: QraftErrorCode,
    message: string
  ) {
    super(message);
    this.name = 'QraftError';
  }
}

/** 解析后的接入配置（env 默认值 + 用户覆盖）。 */
export interface ResolvedQraftConfig {
  baseUrl: string; // 形如 https://test.forge.miqroera.com/api
  clientId: string;
  clientSecret: string;
  redirectUri: string;
}

// ── 请求封装 ────────────────────────────────────────────────────────────

interface RequestResult {
  res: FetchResponseLike;
  bodyText: string;
}

const DEFAULT_TIMEOUT_MS = 15_000;
const DEFAULT_RETRIES = 3;
/** 重试退避：实测出口线路偶发抖动（curl 表现为 HTTP 000），重试可恢复。 */
const RETRY_BACKOFF_MS = [400, 800, 1600];

function isTransientNetworkError(err: unknown): boolean {
  const name = err instanceof Error ? err.name : '';
  // fetch 网络失败 / AbortController 超时
  return (
    name === 'TypeError' ||
    name === 'AbortError' ||
    /fetch failed|network|timeout|ECONN/i.test(err instanceof Error ? err.message : '')
  );
}

/** 从 Location 头解析出 code 查询参数（失败返回 null）。 */
export function extractCodeFromLocation(location: string | null, base?: string): string | null {
  if (!location) return null;
  try {
    // HTTP 允许相对 Location（如 /callback?code=xxx），需要 base 解析。
    return new URL(location, base).searchParams.get('code');
  } catch {
    return null;
  }
}

/**
 * 从浏览器回调地址中提取一次性 code（浏览器登录路径）。
 * 只有当 URL 的 origin + pathname 与注册的 redirect_uri 完全一致时
 * 才解析 —— 防止把登录页/其他页面上的同名 code 参数误当授权码。
 */
export function extractCodeForRedirect(url: string, redirectUri: string): string | null {
  try {
    const target = new URL(url);
    const base = new URL(redirectUri);
    if (target.origin !== base.origin || target.pathname !== base.pathname) return null;
    return target.searchParams.get('code');
  } catch {
    return null;
  }
}

/**
 * 构造 authorize 地址（浏览器登录路径与 API 路径共用）。
 * scope 使用字面逗号（与实测文档 curl 一致；URLSearchParams 会把逗号
 * 编码成 %2C，虽然语义等价，但保持与实测通过的形式一致更稳妥）。
 * 实测要点：不传 state —— 确认授权后再带 state 会报
 * "多次请求的 state 不可重复"（疑似服务端 bug）。
 */
export function buildAuthorizeUrl(config: ResolvedQraftConfig): string {
  const params =
    'response_type=code' +
    `&client_id=${encodeURIComponent(config.clientId)}` +
    '&scope=openid,userinfo,oidc' +
    `&redirect_uri=${encodeURIComponent(config.redirectUri)}`;
  return `${config.baseUrl}/oauth2/authorize?${params}`;
}

export class QraftClient {
  constructor(
    private readonly fetchImpl: FetchLike,
    private readonly log: QraftLogger,
    private readonly timeoutMs: number = DEFAULT_TIMEOUT_MS,
    private readonly retries: number = DEFAULT_RETRIES
  ) {}

  /**
   * 带重试的请求。仅网络类瞬时错误（超时/建连失败）重试；
   * HTTP 403（IP 白名单拦截）与业务错误不重试，直接分类抛出。
   */
  private async request(url: string, init: FetchInitLike = {}): Promise<RequestResult> {
    for (let attempt = 0; attempt <= this.retries; attempt += 1) {
      if (attempt > 0) {
        const backoff =
          RETRY_BACKOFF_MS[attempt - 1] ?? RETRY_BACKOFF_MS[RETRY_BACKOFF_MS.length - 1];
        this.log('WARN', `qraft: 请求失败，${backoff}ms 后第 ${attempt} 次重试`);
        await new Promise((r) => setTimeout(r, backoff));
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const res = await this.fetchImpl(url, { ...init, signal: controller.signal });
        if (res.status === 403) {
          // 实测：未加白 IP 访问任何路径统一返回 nginx 默认 403 页（HTML）。
          throw new QraftError('IP_NOT_WHITELISTED', '出口 IP 未加白，请联系 MiQroForge 管理员');
        }
        const bodyText = await res.text();
        return { res, bodyText };
      } catch (err) {
        if (err instanceof QraftError) throw err;
        if (isTransientNetworkError(err) && attempt < this.retries) {
          continue;
        }
        // 重试耗尽或非瞬时错误：循环内直接抛出（后面无不可达代码）。
        throw new QraftError(
          'NETWORK_UNREACHABLE',
          `网络请求失败（重试 ${attempt} 次后仍失败）：${err instanceof Error ? err.message : String(err)}`
        );
      } finally {
        clearTimeout(timer);
      }
    }
    // 理论上不可达（循环每次要么返回要么抛出），仅满足类型收口。
    throw new QraftError('NETWORK_UNREACHABLE', '网络请求失败');
  }

  private isJson(res: FetchResponseLike): boolean {
    const ct = res.headers.get('content-type') ?? '';
    return ct.toLowerCase().includes('json');
  }

  // ── 公钥提取 ─────────────────────────────────────────────────────────

  /**
   * 从登录页前端 JS bundle 动态提取 RSA 公钥。
   * 登录页在 {origin}/login；bundle 名形如 era-index-*.js；
   * 依次拉取候选 bundle，第一个命中 `BEGIN PUBLIC KEY` 的即为当前公钥。
   */
  async fetchPublicKey(config: ResolvedQraftConfig): Promise<string> {
    const origin = new URL(config.baseUrl).origin;
    const loginPageUrl = `${origin}/login`;
    this.log('INFO', 'qraft: 拉取登录页以提取 RSA 公钥');
    const page = await this.request(loginPageUrl, { redirect: 'manual' });
    const bundleUrls = findEraBundleUrls(page.bodyText, loginPageUrl);
    if (bundleUrls.length === 0) {
      throw new QraftError(
        'PUBLIC_KEY_EXTRACT_FAILED',
        '登录页未找到 era-index-*.js bundle，无法提取 RSA 公钥'
      );
    }
    for (const url of bundleUrls) {
      const bundle = await this.request(url);
      const key = extractPublicKeyFromBundle(bundle.bodyText);
      if (key) {
        this.log('INFO', 'qraft: RSA 公钥提取成功（来源 era-index bundle）');
        return key;
      }
    }
    throw new QraftError(
      'PUBLIC_KEY_EXTRACT_FAILED',
      'era-index bundle 中未找到 BEGIN PUBLIC KEY 公钥块'
    );
  }

  // ── ① 平台登录 ───────────────────────────────────────────────────────

  /**
   * POST /portal/auth/login：密码 RSA 加密后登录，登录态存入 cookie jar。
   * 返回账号基础信息（nickname/username/userId）。
   */
  async platformLogin(
    config: ResolvedQraftConfig,
    phone: string,
    password: string,
    jar: CookieJarLike
  ): Promise<Omit<QraftAccount, 'phone'>> {
    const publicKey = await this.fetchPublicKey(config);
    const encrypted = encryptPasswordRsa(password, publicKey);
    this.log('INFO', `qraft: 平台登录 ${maskSecret(phone, 3, 4)}（密码已 RSA 加密）`);
    const { res, bodyText } = await this.request(`${config.baseUrl}/portal/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone, password: encrypted }),
    });
    jar.storeFromResponse({ headers: res.headers });
    // 先解析业务信封再校验 cookie：密码错误等服务端业务失败时不会下发
    // cookie，此时应把服务端 message（如"密码错误"）透给用户，
    // 而不是笼统的"未下发 Authorization cookie"。
    if (!this.isJson(res)) {
      throw new QraftError(
        'LOGIN_FAILED',
        `登录失败：HTTP ${res.status}，服务端未返回业务 JSON（可能是 IP 未加白）`
      );
    }
    const data = parseBusinessJson(bodyText);
    if (data.code !== 200) {
      throw new QraftError('LOGIN_FAILED', `登录失败：${data.message || data.msg || '未知错误'}`);
    }
    if (!jar.get('Authorization')) {
      throw new QraftError('LOGIN_FAILED', '登录失败：服务端未下发 Authorization cookie');
    }
    const inner = (data.data ?? {}) as Record<string, unknown>;
    const account = {
      sub: String(inner.userId ?? ''),
      username: String(inner.username ?? ''),
      nickname: String(inner.nickname ?? ''),
    };
    this.log('INFO', `qraft: 平台登录成功（昵称 ${account.nickname || '(空)'}）`);
    return account;
  }

  // ── ②③④⑤ 授权码流程 ─────────────────────────────────────────────────

  private authorizeUrl(config: ResolvedQraftConfig): string {
    return buildAuthorizeUrl(config);
  }

  private authHeaders(
    jar: CookieJarLike,
    extra: Record<string, string> = {}
  ): Record<string, string> {
    return { Cookie: jar.header(), ...extra };
  }

  /**
   * 授权码流程：
   *   GET authorize →（未确认授权时返回 200 确认页）POST doConfirm → GET authorize
   *   → 302 Location 携带一次性 code → POST /oauth2/token。
   */
  async authorizeFlow(config: ResolvedQraftConfig, jar: CookieJarLike): Promise<QraftTokens> {
    const authorizeUrl = this.authorizeUrl(config);
    this.log('INFO', 'qraft: 发起授权（GET /oauth2/authorize，不带 state）');

    // 第一次 authorize：已登录未确认授权 → 200 授权确认页；
    // 已确认授权 → 302 直接带 code；无登录态 → 302 到登录页。
    const first = await this.request(authorizeUrl, {
      redirect: 'manual',
      headers: this.authHeaders(jar),
    });
    if (first.res.status >= 300 && first.res.status < 400) {
      const location = first.res.headers.get('location');
      if (location && /\/login(\?|$)/i.test(location)) {
        throw new QraftError('SESSION_EXPIRED', '登录态已失效，请重新登录');
      }
      const code = extractCodeFromLocation(location, config.baseUrl);
      if (code) {
        this.log('INFO', 'qraft: 授权已确认，直接取到授权码');
        return this.exchangeCode(config, code);
      }
    }
    if (first.res.status !== 200) {
      throw new QraftError(
        'AUTHORIZE_FAILED',
        `发起授权失败：HTTP ${first.res.status}（${first.bodyText.slice(0, 120)}）`
      );
    }

    // 实测：授权页 accept=1 按钮无效，确认授权必须走 doConfirm 接口。
    const doConfirmUrl =
      `${config.baseUrl}/oauth2/doConfirm?` +
      `client_id=${encodeURIComponent(config.clientId)}` +
      '&scope=openid,userinfo,oidc' +
      `&redirect_uri=${encodeURIComponent(config.redirectUri)}`;
    this.log('INFO', 'qraft: 确认授权（POST /oauth2/doConfirm）');
    const confirmed = await this.request(doConfirmUrl, {
      method: 'POST',
      headers: this.authHeaders(jar),
    });
    const confirmData = this.isJson(confirmed.res) ? parseBusinessJson(confirmed.bodyText) : null;
    if (confirmed.res.status !== 200 || (confirmData && confirmData.code !== 200)) {
      throw new QraftError(
        'AUTHORIZE_FAILED',
        `确认授权失败：HTTP ${confirmed.res.status}（${confirmed.bodyText.slice(0, 120)}）`
      );
    }

    // 再次 authorize 取 302 Location 里的一次性 code。
    const second = await this.request(authorizeUrl, {
      redirect: 'manual',
      headers: this.authHeaders(jar),
    });
    const location = second.res.headers.get('location');
    const code = extractCodeFromLocation(location, config.baseUrl);
    if (!code) {
      throw new QraftError(
        'AUTHORIZE_FAILED',
        `获取授权码失败：HTTP ${second.res.status}，Location=${maskSecret(location)}`
      );
    }
    this.log('INFO', `qraft: 获取授权码成功（${maskSecret(code, 6, 0)}）`);
    return this.exchangeCode(config, code);
  }

  /** ⑤ POST /oauth2/token：code 一次性，换取后立即失效。
   *  公开方法 —— 浏览器登录路径（用户在页面点击"同意"后拿到 code）也走这里。 */
  async exchangeCode(config: ResolvedQraftConfig, code: string): Promise<QraftTokens> {
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      client_id: config.clientId,
      client_secret: config.clientSecret,
      redirect_uri: config.redirectUri,
    });
    const { res, bodyText } = await this.request(`${config.baseUrl}/oauth2/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!this.isJson(res)) {
      throw new QraftError(
        'TOKEN_EXCHANGE_FAILED',
        `换取 token 失败：HTTP ${res.status}（${bodyText.slice(0, 120)}）`
      );
    }
    const data = parseBusinessJson(bodyText);
    if (data.code !== 200 || !data.access_token) {
      throw new QraftError(
        'TOKEN_EXCHANGE_FAILED',
        `换取 token 失败：${data.message || data.msg || '响应缺少 access_token'}`
      );
    }
    this.log(
      'INFO',
      `qraft: 换取 token 成功（access_token ${maskSecret(String(data.access_token))}）`
    );
    return this.buildTokens(data);
  }

  // ── ⑥ 刷新 token ─────────────────────────────────────────────────────

  /** POST /oauth2/refresh：新平台轮换 refresh_token（旧值刷新后立即失效），
   *  必须持久化响应中的新值；成功响应缺失 refresh_token 时拒绝（沿用旧值
   *  必然导致下一次刷新失败）。refresh_token 已失效（平台侧作废）属于永久
   *  错误，分类为 REFRESH_TOKEN_INVALID —— 重试必然失败，应由服务层停止
   *  自动重试并引导用户重新登录。 */
  async refreshTokens(config: ResolvedQraftConfig, refreshToken: string): Promise<QraftTokens> {
    const body = new URLSearchParams({
      grant_type: 'refresh_token',
      refresh_token: refreshToken,
      client_id: config.clientId,
      client_secret: config.clientSecret,
    });
    const { res, bodyText } = await this.request(`${config.baseUrl}/oauth2/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!this.isJson(res)) {
      throw new QraftError('REFRESH_FAILED', `刷新 token 失败：HTTP ${res.status}`);
    }
    const data = parseBusinessJson(bodyText);
    if (data.code !== 200 || !data.access_token) {
      const detail = refreshServerDetail(data, refreshToken);
      if (isInvalidRefreshToken(detail)) {
        throw new QraftError(
          'REFRESH_TOKEN_INVALID',
          `refresh_token 已失效，请重新登录（平台返回：${detail || '无详情'}）`
        );
      }
      throw new QraftError(
        'REFRESH_FAILED',
        `刷新 token 失败：${detail || data.message || data.msg || '响应缺少 access_token'}`
      );
    }
    if (!data.refresh_token) {
      // 轮换语义下旧值已立即失效：成功响应不携带新 refresh_token 即无法
      // 续期，沿用旧值下一次刷新必然失败 —— 直接按永久错误引导重登。
      throw new QraftError(
        'REFRESH_TOKEN_INVALID',
        '刷新响应缺少新的 refresh_token（轮换语义下旧值已失效），请重新登录'
      );
    }
    this.log('INFO', 'qraft: token 刷新成功');
    return this.buildTokens(data, refreshToken);
  }

  // ── 业务接口 ─────────────────────────────────────────────────────────

  /** GET /oauth2/userinfo：实测响应无 picture 字段。
   *  网关字段（encryptedApiKey/aiGatewayStatus/configVersion/consumerId 等）可能
   *  平铺在顶层或嵌套在 data 内（实测两层都出现过），两层都取。
   *  encryptedApiKey 为空（未开通/未下发）时省略 aiGateway —— 调用方据此区分
   *  "平台未开通网关" 与 "已开通"，同时不把空串当密钥处理。 */
  async getUserInfo(
    config: ResolvedQraftConfig,
    accessToken: string
  ): Promise<Omit<QraftAccount, 'phone'> & { aiGateway?: QraftAiGateway }> {
    const { res, bodyText } = await this.request(`${config.baseUrl}/oauth2/userinfo`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (res.status === 401) {
      throw new QraftError('SESSION_EXPIRED', 'access_token 已失效');
    }
    if (!this.isJson(res)) {
      throw new QraftError('USERINFO_FAILED', `获取用户信息失败：HTTP ${res.status}`);
    }
    // 复用 parseBusinessJson：响应体被截断/非法 JSON 时转换为 QraftError
    // 契约（code=-1），而不是让原生 SyntaxError 逃逸出错误分类。
    const data = parseBusinessJson(bodyText);
    if (typeof data.sub !== 'string' && typeof data.username !== 'string') {
      throw new QraftError('USERINFO_FAILED', 'userinfo 响应缺少 sub/username 字段');
    }
    const nested = (data.data ?? {}) as Record<string, unknown>;
    const field = (name: string): unknown =>
      name in data ? data[name] : (nested[name] ?? undefined);
    const encryptedApiKey = String(field('encryptedApiKey') ?? '');
    const status = String(field('aiGatewayStatus') ?? '');
    const aiGateway: QraftAiGateway | undefined = encryptedApiKey
      ? {
          encryptedApiKey,
          status,
          configVersion: coerceConfigVersion(field('configVersion')),
          consumerId: field('consumerId') != null ? String(field('consumerId')) : undefined,
          consumerGroupId:
            field('consumerGroupId') != null ? String(field('consumerGroupId')) : undefined,
        }
      : undefined;
    this.log('INFO', 'qraft: userinfo 获取成功');
    return {
      sub: String(data.sub ?? ''),
      username: String(data.username ?? ''),
      nickname: String(data.nickname ?? ''),
      ...(aiGateway ? { aiGateway } : {}),
    };
  }

  /**
   * GET /oauth2/points/balance：查询当前用户积分余额。
   * 业务码 40101/40102（token 缺失/失效）→ SESSION_EXPIRED。
   */
  async getPointsBalance(
    config: ResolvedQraftConfig,
    accessToken: string
  ): Promise<QraftPointsBalance> {
    const { res, bodyText } = await this.request(`${config.baseUrl}/oauth2/points/balance`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (res.status === 401) {
      throw new QraftError('SESSION_EXPIRED', 'access_token 已失效');
    }
    if (!this.isJson(res)) {
      throw new QraftError('POINTS_FAILED', `查询积分余额失败：HTTP ${res.status}`);
    }
    const data = parseBusinessJson(bodyText);
    if (data.code === 40101 || data.code === 40102) {
      throw new QraftError('SESSION_EXPIRED', 'access_token 已失效，请重新登录');
    }
    if (data.code !== 200 || !data.data) {
      throw new QraftError(
        'POINTS_FAILED',
        `查询积分余额失败：${data.message || data.msg || '未知错误'}`
      );
    }
    this.log('INFO', 'qraft: 积分余额查询成功');
    return parsePointsBalance(data.data);
  }

  /**
   * POST /oauth2/points/deduct：扣除当前用户可用积分（算力计费）。
   * 业务码 40003（可用积分不足）→ INSUFFICIENT_POINTS；40101/40102 → SESSION_EXPIRED。
   * 成功返回扣费后的最新余额（响应 data 为 PointBalanceVO）。
   */
  async deductPoints(
    config: ResolvedQraftConfig,
    accessToken: string,
    req: { amount: number; source: string; resourceType?: string; project?: string; memo?: string }
  ): Promise<QraftPointsBalance> {
    const { res, bodyText } = await this.request(`${config.baseUrl}/oauth2/points/deduct`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify(req),
    });
    if (res.status === 401) {
      throw new QraftError('SESSION_EXPIRED', 'access_token 已失效');
    }
    if (!this.isJson(res)) {
      throw new QraftError('POINTS_FAILED', `扣除积分失败：HTTP ${res.status}`);
    }
    const data = parseBusinessJson(bodyText);
    if (data.code === 40101 || data.code === 40102) {
      throw new QraftError('SESSION_EXPIRED', 'access_token 已失效，请重新登录');
    }
    if (data.code === 40003) {
      const inner = (data.data ?? {}) as Record<string, unknown>;
      const available = Number.parseInt(String(inner.availablePoints ?? ''), 10);
      const suffix = Number.isFinite(available) ? `（当前可用 ${available}）` : '';
      throw new QraftError(
        'INSUFFICIENT_POINTS',
        `可用积分不足${suffix}：${data.message || data.msg || '本次扣除失败'}`
      );
    }
    if (data.code !== 200 || !data.data) {
      throw new QraftError(
        'POINTS_FAILED',
        `扣除积分失败：${data.message || data.msg || '未知错误'}`
      );
    }
    this.log('INFO', 'qraft: 积分扣除成功');
    return parsePointsBalance(data.data);
  }

  /**
   * 从 token 响应构造统一结构；实测 expires_in=7199（约 2 小时，非官方 24 小时）。
   * 新平台轮换 refresh_token：刷新路径（refreshTokens）强制响应携带新值，
   * 缺失直接报 REFRESH_TOKEN_INVALID；此处的回退只服务于 exchangeCode
   *（登录换取 token，响应缺失属防御性兜底）。
   */
  private buildTokens(data: Record<string, unknown>, fallbackRefreshToken = ''): QraftTokens {
    const expiresIn = Number.parseInt(String(data.expires_in ?? '7199'), 10) || 7199;
    return {
      accessToken: String(data.access_token),
      // 轮换语义：以响应中的新值为准；未携带时沿用入参（旧值）。
      refreshToken: String(data.refresh_token ?? fallbackRefreshToken),
      openid: String(data.openid ?? ''),
      idToken: data.id_token ? String(data.id_token) : undefined,
      expiresAt: Date.now() + expiresIn * 1000,
    };
  }
}

// ── 工具函数 ────────────────────────────────────────────────────────────

interface BusinessEnvelope {
  code: number;
  message?: string;
  msg?: string;
  data?: unknown;
  access_token?: unknown;
  refresh_token?: unknown;
  expires_in?: unknown;
  openid?: unknown;
  id_token?: unknown;
  [key: string]: unknown;
}

/** 解析 MiQroForge 业务 JSON 信封（HTTP 200 + {code: 200, ...} 表示成功）。 */
export function parseBusinessJson(bodyText: string): BusinessEnvelope {
  try {
    const parsed = JSON.parse(bodyText) as Record<string, unknown>;
    return { code: Number(parsed.code ?? 200), ...parsed } as BusinessEnvelope;
  } catch {
    return { code: -1, message: '响应不是合法 JSON' };
  }
}

/** 服务端错误消息可能回显 refresh_token 原文（实测 originalMessage 形如
 *  "无效refresh_token: <token>")：替换为掩码并截断，防止凭据进入日志/界面。 */
function sanitizeServerMessage(message: string, secrets: string[]): string {
  let out = message;
  for (const secret of secrets) {
    if (secret) out = out.split(secret).join('***');
  }
  return out.slice(0, 200);
}

/** 提取刷新失败的服务端明细：优先 data.data.originalMessage（Sa-Token 异常
 *  详情），退化为顶层 message/msg；含脱敏。 */
function refreshServerDetail(data: BusinessEnvelope, refreshToken: string): string {
  const nested = data.data;
  const original =
    typeof nested === 'object' && nested !== null
      ? String((nested as Record<string, unknown>).originalMessage ?? '')
      : '';
  const raw = [original, data.message, data.msg]
    .filter((s): s is string => typeof s === 'string' && s.length > 0)
    .join('；');
  return sanitizeServerMessage(raw, [refreshToken]);
}

/** refresh_token 已失效（平台侧作废/过期）：重试必然失败，属永久错误。 */
function isInvalidRefreshToken(detail: string): boolean {
  return /RefreshTokenException|无效\s*refresh_token|refresh_token\s*(无效|已失效|已过期|过期)|invalid\s+refresh/i.test(
    detail
  );
}

/** configVersion 防御性归一为 number：平台可能下发数字或数字字符串。 */
function coerceConfigVersion(raw: unknown): number | undefined {
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  if (typeof raw === 'string') {
    const n = Number.parseInt(raw, 10);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

/** 解析 PointBalanceVO（balance/deduct 响应的 data 字段）。 */
function parsePointsBalance(raw: unknown): QraftPointsBalance {
  const inner = (raw ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number => {
    const n = Number.parseInt(String(v ?? ''), 10);
    return Number.isFinite(n) ? n : 0;
  };
  return {
    availablePoints: num(inner.availablePoints),
    heldPoints: num(inner.heldPoints),
    totalEarned: num(inner.totalEarned),
    totalSpent: num(inner.totalSpent),
  };
}

/** 新建一个带默认依赖的客户端（生产：electron.net.fetch + 主进程日志）。 */
export function createQraftClient(options?: {
  fetch?: FetchLike;
  log?: QraftLogger;
  timeoutMs?: number;
  retries?: number;
}): QraftClient {
  const log =
    options?.log ??
    ((level, message) => {
      const line = `[qraft] ${message}`;
      if (level === 'ERROR') console.error(line);
      else if (level === 'WARN') console.warn(line);
      else console.log(line);
    });
  return new QraftClient(
    options?.fetch ?? globalThis.fetch,
    log,
    options?.timeoutMs,
    options?.retries
  );
}

export { CookieJar };
