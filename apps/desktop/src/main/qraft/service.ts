/**
 * QraftService — 登录态的生命周期编排：
 *   登录（平台登录 → 授权码流程 → userinfo）→ 加密落盘 → 自动刷新调度
 *   → 状态事件推送 → 退出登录清理。
 *
 * 刷新策略：按平台下发的 expires_in（2026-09-21 实测约 30 天，早期约 2 小时）
 * 提前 15 分钟用 refresh_token 刷新。刷新失败按性质区分（issue #1087）：
 * 瞬时失败（网络不可达/平台 5xx）静默指数退避重试，不打扰用户；平台明确作废
 * refresh_token（REFRESH_TOKEN_INVALID）或会话整体被拒时不再重试，**自动退出
 * 登录**并在登录页说明「登录已失效，已自动退出」（见 logoutSessionExpired）。
 */

import {
  chmodSync,
  closeSync,
  existsSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'fs';
import { randomUUID } from 'crypto';
import { dirname, join } from 'path';
import {
  claimLegacyWorkspace,
  clearActiveAccount,
  isValidAccountSub,
  setActiveAccount,
} from '../ipc/workspace-path';
import { CookieJar } from './cookie-jar';
import { decryptMcpGatewayKey } from './mcp-gateway-key';
import { QraftClient, QraftError, type QraftLogger, type ResolvedQraftConfig } from './client';
import { maskSecret } from './rsa';
import { QraftStore } from './store';
import {
  PROD_REDIRECT_URI,
  QRAFT_ENV_DEFAULTS,
  prodEnvClientSecret,
  testEnvClientSecret,
  type QraftAccount,
  type QraftAiGateway,
  type QraftEnv,
  type QraftErrorCode,
  type QraftLoginOptions,
  type QraftLoginResult,
  type QraftPointsBalance,
  type QraftStatus,
  type QraftStoredState,
  type QraftTokens,
} from './types';
import type { FeedbackPlatformOutcome, QraftBillingHistoryEntry } from '../../shared/ipc';

/** 到期前提前刷新的提前量（15 分钟）。 */
const REFRESH_ADVANCE_MS = 15 * 60_000;
/** Slurm MCP 作业单价（issue #927）：每次作业运行扣 10 积分。 */
export const SLURM_JOB_COST = 10;
/** 扣费历史文件最多保留条目数。 */
const MAX_BILLING_HISTORY = 200;
/** Slurm 扣费结果（chargeSlurmJob 返回值；同时回传 Python 决议）。 */
export interface SlurmChargeResult {
  ok: boolean;
  code?: string;
  message?: string;
  /** 扣费后的可用余额（成功时）。 */
  balance?: number;
  /** 去重命中（该作业已计费过），未发起新的扣费请求。 */
  dedup?: boolean;
}
/** 瞬时刷新失败（网络/平台 5xx）的指数退避重试：1 分钟起步翻倍，
 *  封顶 30 分钟（issue #1087：瞬时失败静默退避，不置 requiresRelogin）。
 *  refresh_token 已失效（REFRESH_TOKEN_INVALID）属永久错误，不重试。 */
const REFRESH_RETRY_BASE_MS = 60_000;
const REFRESH_RETRY_MAX_MS = 30 * 60_000;
/** 网关信息（userinfo）补拉的退避重试：1 分钟起步翻倍，封顶 8 分钟，最多 5 次。
 *  平台侧开通网关发生在用户登录之后是常态（先登录、后台再开通），只在登录
 *  那一刻拉一次的旧行为会让应用永远停在「未下发」——见 syncAccountInfo。 */
const GATEWAY_INFO_RETRY_BASE_MS = 60_000;
const GATEWAY_INFO_RETRY_MAX_MS = 8 * 60_000;
const GATEWAY_INFO_RETRY_LIMIT = 5;
/** Node setTimeout 上限（32 位有符号毫秒数，约 24.8 天）。超过会被截断为
 *  1ms——超长有效期的 token（实测平台刷新返回 30 天）若不封顶，会形成
 *  「刷新成功 → 调度 30 天 → 1ms 后立即再刷新」的高频刷新循环。 */
const MAX_TIMEOUT_MS = 2_147_483_647;

/** 刷新失败的永久性判定：只有平台明确作废 refresh_token 才引导重新登录；
 *  其余错误码（网络不可达/平台瞬时错误）均按瞬时失败静默退避重试。 */
export function isPermanentRefreshError(code: QraftErrorCode | null): boolean {
  return code === 'REFRESH_TOKEN_INVALID';
}

/** 登录态里的网关是否已经「可用」——拿到可用网关就停止补拉重试。
 *  判据与 Python 侧 read_gateway_creds 一致（凭据非空 + status==='active'），
 *  只有这一种情况模型调用会走网关。其余状态（未下发 / 开通中 / 开通失败 /
 *  已停用）都继续按退避重试：平台侧开通或恢复后能自动接上，代价只是最多
 *  5 次 userinfo 请求。 */
export function isGatewayUsable(state: QraftStoredState): boolean {
  return !!state.aiGateway?.encryptedApiKey && state.aiGateway.status === 'active';
}

/** 积分查询失败的日志文案：带上服务端明细，否则只留一个错误码，
 *  平台侧异常（如 SQL 报错）会完全不可见。 */
function pointsFailureLog(err: unknown): string {
  // QraftError 的 message 自带「查询积分余额失败：」前缀，错误码缀在末尾即可。
  if (err instanceof QraftError) return `qraft: ${err.message}（${err.code}）`;
  return `qraft: 查询积分余额失败（${err instanceof Error ? err.message : String(err)}）`;
}

export interface QraftServiceOptions {
  client: QraftClient;
  store: QraftStore;
  log: QraftLogger;
  /** 状态变化时回调（ipc 层把最新状态推给所有窗口）。 */
  onStatusChanged?: (status: QraftStatus) => void;
  /** 生成 loopback 回调地址（随机端口），测试可注入固定值。 */
  makeRedirectUri?: () => string;
  /**
   * 供 Skill/agent 读取 access_token 的 token 文件路径解析器。
   * 登录/刷新成功后写入 { accessToken, expiresAt }（0600），退出登录删除；
   * 返回 null 表示不启用 token 文件（如 workspace 不可解析时）。
   */
  tokenFilePath?: () => string | null;
  /**
   * 扣费历史文件路径解析器（issue #927：Slurm 作业扣费记录本地留存）。
   * 返回 null 表示不启用历史持久化。
   */
  billingHistoryPath?: () => string | null;
  /**
   * 已计费作业 ID 的持久化索引文件（无条数上限）：展示历史有 200 条
   * 截断，去重索引必须跨重启完整保留，否则被淘汰的作业会重复扣费。
   */
  billedJobIdsPath?: () => string | null;
}

export function defaultRedirectUri(): string {
  // 1024–65535 随机端口（仅测试环境用；生产环境走平台注册值
  // PROD_REDIRECT_URI，可在设置页"高级设置"中覆盖）。
  const port = 1024 + Math.floor(Math.random() * (65535 - 1024));
  return `http://localhost:${port}/callback`;
}

/**
 * 解析登录配置：用户覆盖 > 同环境上次登录存下的配置 > 环境默认值。
 * 上次存储只在与目标环境一致时复用 —— 防止切到生产环境时串用测试环境的
 * baseUrl / clientSecret / loopback redirect_uri。
 */
export function resolveConfig(
  opts: QraftLoginOptions,
  stored: QraftStoredState | null,
  makeRedirectUri: () => string
): ResolvedQraftConfig {
  const env: QraftEnv = opts.env ?? stored?.env ?? 'prod';
  const defaults = QRAFT_ENV_DEFAULTS[env];
  const storedMatches = stored && stored.env === env ? stored : null;
  return {
    baseUrl: opts.baseUrl ?? storedMatches?.baseUrl ?? defaults.baseUrl,
    clientId: opts.clientId ?? storedMatches?.clientId ?? defaults.clientId,
    clientSecret:
      opts.clientSecret ??
      storedMatches?.clientSecret ??
      (env === 'test' ? testEnvClientSecret() : prodEnvClientSecret()),
    redirectUri:
      opts.redirectUri ??
      storedMatches?.redirectUri ??
      // 生产环境用平台注册值（随机端口未注册，平台会拒）；测试环境不校验
      // 注册值，可用随机 loopback 便于隔离。
      (env === 'prod' ? PROD_REDIRECT_URI : makeRedirectUri()),
  };
}

function validateConfig(config: ResolvedQraftConfig, env: QraftEnv): void {
  if (!/^https:\/\/.+/i.test(config.baseUrl)) {
    throw new QraftError('INVALID_CONFIG', 'MiQroForge 基础地址必须是 https:// 开头的完整 URL');
  }
  if (!config.clientId) {
    throw new QraftError('INVALID_CONFIG', 'client_id 不能为空');
  }
  if (!config.clientSecret) {
    throw new QraftError(
      'INVALID_CONFIG',
      'client_secret 未配置：请在设置页"高级设置"中填写，或通过 QRAFT_TEST_CLIENT_SECRET 环境变量注入（测试环境）'
    );
  }
  if (env === 'prod' && !config.redirectUri) {
    throw new QraftError(
      'INVALID_CONFIG',
      '生产环境必须使用在 MiQroForge 平台注册的 redirect_uri，请在设置页"高级设置"中填写'
    );
  }
  if (config.redirectUri && !/^https?:\/\//i.test(config.redirectUri)) {
    throw new QraftError('INVALID_CONFIG', 'redirect_uri 必须是 http(s):// 开头的完整地址');
  }
}

export class QraftService {
  private jar = new CookieJar();
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private refreshScheduledAt: number | null = null;
  private refreshError: QraftErrorCode | null = null;
  private requiresRelogin = false;
  /** 因平台判定登录已失效而自动退出登录：登录页据此给出「已自动退出」说明。
   *  只存在于本次进程内，重新登录成功即清除（见 logoutSessionExpired）。 */
  private sessionExpired = false;
  /** 网关信息补拉的定时器与代数（syncAccountInfo 的退避重试）。 */
  private gatewayInfoTimer: ReturnType<typeof setTimeout> | null = null;
  private gatewayInfoAttempt = 0;
  /** 瞬时刷新失败的退避重试代数（决定下次重试间隔），成功刷新/登录/登出时归零。 */
  private refreshRetryAttempt = 0;
  /** 最近一次已处理失败的在途刷新 Promise：手动与自动路径并发 await
   *  同一个 inFlightRefresh 时，失败归类与重试调度只做一次（CodeRabbit #1114）。 */
  private lastHandledRefreshFailure: Promise<void> | null = null;
  /** 登录代际：退出登录时递增，用于丢弃登出前发起的在途刷新结果，
   *  防止"刷新完成于登出之后"把凭据写回磁盘/内存。 */
  private authGeneration = 0;
  /** 最近一次拉取的积分余额（随 status() 推送给设置页；任务扣费后由
   *  设置页重新拉取刷新）。 */
  private pointsBalance: QraftPointsBalance | null = null;
  /** 已计费的 charge_id / 复合作业键（账号+服务器+作业 ID）内存集合
   *  （issue #927）：去重不依赖历史文件持久化——文件写盘失败时同进程
   *  内仍能保证同一作业只扣一次。 */
  private billedChargeIds = new Set<string>();
  private billedJobIds = new Set<string>();
  /** 在途扣费（charge_id / 复合作业键 → 首次请求的 Promise）：并发到达
   *  的同一作业 RUNNING 事件共享同一次扣费，后到者等待首个结果。 */
  private inFlightCharges = new Map<string, Promise<SlurmChargeResult>>();

  constructor(private readonly options: QraftServiceOptions) {
    // 应用启动时恢复登录态、重建刷新调度，并同步 token 文件
    //（应用重启后文件可能已过期/被清理，按当前存储重写）。
    const stored = this.options.store.load();
    if (stored) {
      this.restoreJar(stored);
      // 账号维度的工作区根（#1185）必须在 syncTokenFile 之前就位：token
      // 文件写在 <workspace>/.qraft/ 下，先写就会落进上一个账号的工作区。
      //
      // 这里与 persistLogin 里的调用不同：启动时只是**重申**一个通常已经正确
      // 的标记（上一轮运行写下的就是同一个 sub），写失败不改变现状；而新登录
      // 时标记指向的是上一个账号，写失败必须让登录失败。所以这里吞掉异常、记
      // 一条日志继续启动，由 persistLogin 那条路径负责终止登录。
      let accountReady = true;
      try {
        this.activateAccount(stored.account?.sub);
      } catch (err) {
        accountReady = false;
        this.options.log(
          'ERROR',
          `qraft: 启动时激活账号失败（${
            err instanceof Error ? err.message : err
          }）；本次不写 token 文件，以免凭据落进上一个账号的工作区`
        );
      }
      this.scheduleRefresh(stored);
      // 标记没能换成当前账号时**不能**同步 token 文件：`syncTokenFile` 的路径由
      // `getWorkspacePath()` 解析，而它跟的是磁盘上那个（此时可能还是别人的）标记
      // —— 写下去就是把当前账号的凭据留进上一个账号的工作区（#1185 评审）。
      if (accountReady) this.syncTokenFile(stored);
      // 启动时补拉一次账号 / 网关信息：平台在用户登录**之后**才开通网关、
      // 或登录那次 userinfo 失败时，登录态里的「未下发」会一直留着 ——
      // 启动即重拉，让「重启应用」也能生效（拉不到时再按退避重试）。
      if (accountReady) void this.syncAccountInfo();
    } else {
      // 没有登录态（含 E2E loginBypass）：清掉可能残留的标记，否则运行时
      // 会停在上一次会话用过的账号工作区上。
      //
      // 这里同样只报告不抛出：构造函数不该因为清不掉一个标记而起不来 ——
      // 紧接着的 else 语义是「本次以无账号态运行」，那正是标记清掉后的结果。
      try {
        clearActiveAccount();
      } catch (err) {
        this.options.log(
          'ERROR',
          `qraft: 启动时清除账号标记失败（${
            err instanceof Error ? err.message : err
          }）；运行时可能仍按上一个账号解析工作区`
        );
      }
    }
    // 启动时恢复内存去重集合：charge_id 来自展示历史；复合作业键来自
    // 独立无上限索引文件（展示历史有 200 条截断，索引必须完整）。
    for (const entry of this.loadBillingHistory()) {
      if (entry.status === 'billed') this.billedChargeIds.add(entry.chargeId);
    }
    for (const jobKey of this.loadBilledJobIds()) {
      this.billedJobIds.add(jobKey);
    }
  }

  private restoreJar(stored: QraftStoredState): void {
    // 登录态 cookie 序列化格式为 "Authorization=<uuid>; ..."（可能为空）。
    for (const pair of stored.cookie.split(';')) {
      const trimmed = pair.trim();
      const eq = trimmed.indexOf('=');
      if (eq > 0) this.jar.set(trimmed.slice(0, eq), trimmed.slice(eq + 1));
    }
  }

  // ── 对外操作 ──────────────────────────────────────────────────────────

  async login(
    phone: string,
    password: string,
    opts: QraftLoginOptions = {}
  ): Promise<QraftLoginResult> {
    try {
      const stored = this.options.store.current;
      const env: QraftEnv = opts.env ?? stored?.env ?? 'prod';
      const config = resolveConfig(
        opts,
        stored,
        this.options.makeRedirectUri ?? defaultRedirectUri
      );
      validateConfig(config, env);
      this.options.log('INFO', `qraft: 开始登录（环境 ${env}，${config.baseUrl}）`);

      this.jar.clear();
      const loginAccount = await this.options.client.platformLogin(
        config,
        phone,
        password,
        this.jar
      );
      const tokens = await this.options.client.authorizeFlow(config, this.jar);

      // 登录后展示账号信息以 userinfo 为准（实测响应无 picture 字段）；
      // userinfo 失败不阻断登录 —— 回退用平台登录响应里的 nickname/username。
      let account: QraftAccount = { phone, ...loginAccount };
      let aiGateway: QraftAiGateway | undefined;
      let mcpGatewayKey: string | undefined;
      try {
        const info = await this.options.client.getUserInfo(config, tokens.accessToken);
        // 显式取身份字段：info 额外携带 aiGateway（含密钥），绝不并入 account，
        // 否则会经 status().account 泄漏给渲染进程。
        account = {
          phone,
          sub: info.sub || loginAccount.sub,
          username: info.username,
          nickname: info.nickname,
        };
        aiGateway = info.aiGateway;
        // 平台按用户下发的凭据优先；未下发时解密内置共享凭据
        //（全客户端同一 token，2026-09-07 产品确认的过渡方案）。
        mcpGatewayKey = info.mcpGatewayKey ?? decryptMcpGatewayKey() ?? undefined;
      } catch (err) {
        this.options.log(
          'WARN',
          `qraft: userinfo 获取失败（${err instanceof QraftError ? err.code : err}），回退使用登录响应信息`
        );
        // userinfo 失败不能静默丢弃已存储的 MCP 网关凭据：仅当本次登录
        // 证明为同一账号时保留（CodeRabbit #951）；账号身份不一致时
        // 保持未设置，绝不把旧账号的凭据带进新账号会话。
        const previous = this.options.store.current;
        if (previous && account.sub && previous.account.sub === account.sub) {
          mcpGatewayKey = previous.mcpGatewayKey;
        }
      }

      this.persistLogin(env, config, account, tokens, aiGateway, mcpGatewayKey);
      this.options.log('INFO', `qraft: 登录完成（${account.nickname || account.username}）`);
      // 登录后尽力拉取一次积分余额，让设置页直接展示（失败不阻断登录）。
      void this.fetchPointsBalance().catch(() => {});
      return { ok: true, account };
    } catch (err) {
      this.options.log('ERROR', `qraft: 登录失败（${err instanceof QraftError ? err.code : err}）`);
      return this.errorResult(err);
    }
  }

  /**
   * 浏览器登录路径：MiQroForge 授权页修复后，用户在页面自行登录并点击"同意"，
   * 授权回调里的 code 由 IPC 层拦截后传入，这里换取 token 并完成登录。
   */
  async loginWithCode(code: string, opts: QraftLoginOptions = {}): Promise<QraftLoginResult> {
    try {
      const stored = this.options.store.current;
      const env: QraftEnv = opts.env ?? stored?.env ?? 'prod';
      const config = resolveConfig(
        opts,
        stored,
        this.options.makeRedirectUri ?? defaultRedirectUri
      );
      validateConfig(config, env);
      this.options.log('INFO', `qraft: 浏览器登录：换取 token（code ${maskSecret(code, 6, 0)}）`);

      this.jar.clear();
      const tokens = await this.options.client.exchangeCode(config, code);

      // 浏览器路径没有平台登录响应，账号信息以 userinfo 为准
      //（实测响应无 picture 字段、也不含手机号）。
      let account: QraftAccount = { phone: '', sub: '', username: '', nickname: '' };
      let aiGateway: QraftAiGateway | undefined;
      let mcpGatewayKey: string | undefined;
      try {
        const info = await this.options.client.getUserInfo(config, tokens.accessToken);
        account = {
          phone: '',
          sub: info.sub,
          username: info.username,
          nickname: info.nickname,
        };
        aiGateway = info.aiGateway;
        // 平台按用户下发的凭据优先；未下发时解密内置共享凭据
        //（全客户端同一 token，2026-09-07 产品确认的过渡方案）。
        mcpGatewayKey = info.mcpGatewayKey ?? decryptMcpGatewayKey() ?? undefined;
      } catch (err) {
        this.options.log(
          'WARN',
          `qraft: 浏览器登录 userinfo 获取失败（${err instanceof QraftError ? err.code : err}），账号信息留空`
        );
      }

      this.persistLogin(env, config, account, tokens, aiGateway, mcpGatewayKey);
      this.options.log(
        'INFO',
        `qraft: 浏览器登录完成（${account.nickname || account.username || account.sub}）`
      );
      // 登录后尽力拉取一次积分余额（失败不阻断登录）。
      void this.fetchPointsBalance().catch(() => {});
      return { ok: true, account };
    } catch (err) {
      this.options.log(
        'ERROR',
        `qraft: 浏览器登录失败（${err instanceof QraftError ? err.code : err}）`
      );
      return this.errorResult(err);
    }
  }

  /**
   * 供 IPC 层在打开登录窗口前解析接入配置 —— 窗口里的 authorize URL 与
   * 之后换 token 必须使用同一份配置（同一 redirect_uri）。
   */
  resolveLoginConfig(opts: QraftLoginOptions): ResolvedQraftConfig {
    const stored = this.options.store.current;
    const env: QraftEnv = opts.env ?? stored?.env ?? 'prod';
    const config = resolveConfig(opts, stored, this.options.makeRedirectUri ?? defaultRedirectUri);
    validateConfig(config, env);
    return config;
  }

  /** 保存登录态 + 重置刷新状态 + 调度自动刷新 + 推送状态事件。 */
  private persistLogin(
    env: QraftEnv,
    config: ResolvedQraftConfig,
    account: QraftAccount,
    tokens: QraftTokens,
    aiGateway?: QraftAiGateway,
    mcpGatewayKey?: string
  ): void {
    // 先切工作区根再落 token 文件：syncTokenFile 的路径由 workspace 解析
    // 得出（qraft/ipc.ts 的 tokenFilePath），顺序反了会把凭据写进上一个
    // 账号的工作区。
    this.activateAccount(account.sub);
    const state: QraftStoredState = {
      version: 1,
      env,
      baseUrl: config.baseUrl,
      clientId: config.clientId,
      clientSecret: config.clientSecret,
      redirectUri: config.redirectUri,
      cookie: this.jar.header(),
      account,
      tokens,
      ...(aiGateway ? { aiGateway } : {}),
      ...(mcpGatewayKey ? { mcpGatewayKey } : {}),
    };
    this.options.store.save(state);
    this.refreshError = null;
    this.refreshRetryAttempt = 0;
    this.requiresRelogin = false;
    // 重新登录成功：清掉「已自动退出」的说明，登录页不再赘述上一次失效。
    this.sessionExpired = false;
    this.scheduleRefresh(state);
    this.syncTokenFile(state);
    this.emitStatus();
    // 登录时平台没下发网关（未开通/开通中）→ 排上补拉重试：平台侧开通后
    // 无需重新登录即可生效（#1251）。**先取消**：不登出直接重新登录时
    //（例如登录失效后重登），上一份登录态可能已经用完退避预算、或还挂着
    // 旧计时器 —— 不重置的话新登录会拿不到补拉（正是本 issue 要修的现象）。
    this.cancelGatewayInfoRetry();
    if (!isGatewayUsable(state)) this.scheduleGatewayInfoRetry();
  }

  private errorResult(err: unknown): QraftLoginResult {
    if (err instanceof QraftError) return { ok: false, code: err.code, message: err.message };
    return {
      ok: false,
      code: 'INTERNAL',
      message: err instanceof Error ? err.message : String(err),
    };
  }

  // ── token 文件（供 Skill/agent 读取 access_token） ─────────────────────

  /** 登录/刷新成功后写入 token 文件：accessToken + expiresAt + baseUrl（0600）。
   *  baseUrl 供 KUN 计费闸门定位平台接口；Skill 侧 auth.py 只读前两个字段。
   *  防符号链接/硬链接重定向：.qraft 目录必须是真实目录、token 文件必须是
   *  真实常规文件且为本进程用户所有，否则跳过写入并告警（workspace 对
   *  agent 可写，恶意/意外替换成 symlink 或预置文件时不能把凭据写进去）。
   *  写入采用同目录临时文件 + rename 原子替换：rename 替换目录条目本身
   *  （不跟随目标 symlink），且凭据只落在新建 inode 上 —— 原地 writeFileSync
   *  会跟随 symlink、并把攻击者经硬链接预置的文件就地覆写。 */
  private syncTokenFile(state: QraftStoredState): void {
    const filePath = this.options.tokenFilePath?.();
    if (!filePath) return;
    let tmpPath: string | null = null;
    try {
      const dir = dirname(filePath);
      mkdirSync(dir, { recursive: true });
      const dirStat = lstatSync(dir);
      if (!dirStat.isDirectory() || dirStat.isSymbolicLink()) {
        throw new Error('.qraft 不是真实目录（可能被符号链接替换），跳过写入');
      }
      // mkdir 后目录若被替换为 symlink，lstat 会拿到链接本身 → 上面已拦截。
      if (existsSync(filePath)) {
        const fileStat = lstatSync(filePath);
        if (!fileStat.isFile() || fileStat.isSymbolicLink()) {
          throw new Error('token 文件路径被非常规文件/symlink 占用，跳过写入');
        }
        // 拒绝替换其他用户拥有的文件（POSIX 语义；Windows 无 uid 概念，
        // rename 替换 + 0600 已足够）。攻击者预置的文件绝不原地覆写。
        if (process.platform !== 'win32' && typeof process.getuid === 'function') {
          if (statSync(filePath).uid !== process.getuid()) {
            throw new Error('token 文件被其他用户所有，跳过写入');
          }
        }
      }
      // 原子替换：临时文件 O_EXCL 创建（0600）→ rename。任何异常路径下
      // 临时文件都会被 finally 清理，不残留凭据。
      tmpPath = join(dir, `.qraft-token-${process.pid}-${randomUUID()}.tmp`);
      const fd = openSync(tmpPath, 'wx', 0o600);
      try {
        writeFileSync(
          fd,
          JSON.stringify({
            accessToken: state.tokens.accessToken,
            expiresAt: state.tokens.expiresAt,
            // 平台 API 基础地址：KUN 计费闸门（billing.py）据此定位
            // /oauth2/points/deduct；Skill 侧 auth.py 只读前两个字段，无影响。
            baseUrl: state.baseUrl,
            // AI 网关信息（Python make_provider 读取；登出即随文件删除）。
            // billing/auth.py 只读已知字段，追加字段向后兼容。
            ...(state.mcpGatewayKey ? { mcpGatewayKey: state.mcpGatewayKey } : {}),
            ...(state.aiGateway
              ? {
                  aiGateway: {
                    encryptedApiKey: state.aiGateway.encryptedApiKey,
                    status: state.aiGateway.status,
                    configVersion: state.aiGateway.configVersion,
                    consumerId: state.aiGateway.consumerId,
                    consumerGroupId: state.aiGateway.consumerGroupId,
                  },
                }
              : {}),
          }),
          { encoding: 'utf8' }
        );
      } finally {
        closeSync(fd);
      }
      renameSync(tmpPath, filePath);
      tmpPath = null;
      chmodSync(filePath, 0o600);
    } catch (err) {
      this.options.log(
        'WARN',
        `qraft: 同步 token 文件失败（${err instanceof Error ? err.message : err}）`
      );
    } finally {
      if (tmpPath) {
        try {
          rmSync(tmpPath, { force: true });
        } catch {
          // 清理失败无碍：临时文件不包含可用凭据引用，且 0600。
        }
      }
    }
  }

  /** 退出登录时删除 token 文件，避免过期凭据残留。 */
  private deleteTokenFile(): void {
    const filePath = this.options.tokenFilePath?.();
    if (!filePath) return;
    try {
      rmSync(filePath, { force: true });
    } catch (err) {
      this.options.log(
        'WARN',
        `qraft: 删除 token 文件失败（${err instanceof Error ? err.message : err}）`
      );
    }
  }

  status(): QraftStatus {
    const state = this.options.store.current;
    // 未登录也要带出「为什么退出」：平台判定失效后应用自动退出，登录页据此说明。
    if (!state)
      return { loggedIn: false, ...(this.sessionExpired ? { sessionExpired: true } : {}) };
    const now = Date.now();
    return {
      loggedIn: true,
      account: state.account,
      env: state.env,
      baseUrl: state.baseUrl,
      expiresAt: state.tokens.expiresAt,
      refreshScheduledAt: this.refreshScheduledAt ?? undefined,
      refreshError: this.refreshError ?? undefined,
      requiresRelogin:
        this.requiresRelogin ||
        // 已过期且最近一次刷新失败属永久作废 → 引导重新登录。
        // 瞬时失败（网络等）重试中不算失效，不引导重登（issue #1087）。
        (this.refreshError !== null &&
          isPermanentRefreshError(this.refreshError) &&
          now > state.tokens.expiresAt),
      points: this.pointsBalance ?? undefined,
      // 只透出非敏感网关信息（status/configVersion）；encryptedApiKey 不外发渲染进程。
      aiGateway: state.aiGateway
        ? { status: state.aiGateway.status, configVersion: state.aiGateway.configVersion }
        : undefined,
    };
  }

  /**
   * 退出登录。`sessionExpired` 表示这次是被平台判定登录失效后的**自动退出**
   * （见 logoutSessionExpired）：登录页据此说明原因；用户主动登出不带该标记。
   */
  logout(opts: { sessionExpired?: boolean } = {}): void {
    this.cancelRefresh();
    // 登出后不再补拉网关信息：计时器留着会在无登录态时白跑一次。
    this.cancelGatewayInfoRetry();
    // 先记下来：emitStatus 在方法末尾，登录页读到的就是这次退出的原因。
    this.sessionExpired = opts.sessionExpired === true;
    // 使登出前发起的在途刷新结果作废（runRefresh 代际校验丢弃）。
    this.authGeneration += 1;
    this.inFlightRefresh = null;
    this.jar.clear();
    this.options.store.clear();
    // deleteTokenFile 先于 clearActiveAccount：token 文件的路径由当前工作区
    // 解析得出，标记清掉之后再删就会指向共享工作区（删错文件、留下凭据）。#1185
    this.deleteTokenFile();
    try {
      clearActiveAccount();
    } catch (err) {
      // 凭据已经清掉了，用户确实是登出状态 —— 不能因此把登出判失败。但这件事
      // 必须看得见：标记还在，长期驻留的运行时在下一次登录成功之前会继续按
      // **上一个账号**解析工作区（#1185 评审）。
      this.options.log(
        'ERROR',
        `qraft: 退出登录时清除账号标记失败（${
          err instanceof Error ? err.message : err
        }）；在下一次登录成功之前，运行时可能仍按上一个账号解析工作区`
      );
    }
    this.refreshError = null;
    this.refreshRetryAttempt = 0;
    this.requiresRelogin = false;
    this.pointsBalance = null;
    // 扣费历史与已计费作业索引不随登出删除：读取时按 account.sub 过滤，
    // 换账号自然看不到前任账号的记录；删除会让同一账号重新登录后历史
    // 全丢（平台轮换 refresh_token 迫使重新登录是常态），并把跨重启
    // 去重一并放开导致同一作业被重复扣费。
    this.inFlightCharges.clear();
    this.options.log('INFO', 'qraft: 已退出登录（cookie 与 token 均已清除）');
    this.emitStatus();
  }

  /**
   * 把工作区根切到 `sub` 账号名下（#1185）。
   *
   * 未登录 / 拿不到合法 sub（老平台响应缺字段、sub 为空）时退回共享工作区
   * 而不是猜一个目录：分享别人工作区比多一个共享目录更糟。
   *
   * 标记写不进去时**抛出**——调用方（`persistLogin`）必须让这次登录失败，
   * 否则磁盘上留下的是上一个账号的标记，而长期驻留的运行时每次解析工作区都会
   * 读它，于是新账号继续在上一个账号的工作区里干活。
   */
  private activateAccount(sub: string | undefined): void {
    if (!isValidAccountSub(sub)) {
      clearActiveAccount();
      return;
    }
    // 认领在前：存量 `~/.forge/workspace` 归首个登录账号，之后 getWorkspacePath
    // 才会把它解析成这个账号的工作区。认领本身是尽力而为的：认领失败只是让这个
    // 账号拿到自己的空目录，不会把它带进别人的数据里。
    claimLegacyWorkspace(sub);
    setActiveAccount(sub);
  }

  /**
   * 拉取最新积分余额（设置页/登录后/状态栏轮询调用），成功后缓存并推送状态。
   * access_token 失效（SESSION_EXPIRED）先刷新再重试一次（对齐
   * submitPlatformFeedback）；新 token 仍被平台拒绝说明会话整体失效，
   * 置 requiresRelogin 由登录失效三件套引导重新登录（issue #1160）。
   */
  async fetchPointsBalance(): Promise<
    { ok: true; points: QraftPointsBalance } | { ok: false; code: QraftErrorCode; message: string }
  > {
    const state = this.options.store.current;
    if (!state) return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
    const config: ResolvedQraftConfig = {
      baseUrl: state.baseUrl,
      clientId: state.clientId,
      clientSecret: state.clientSecret,
      redirectUri: state.redirectUri,
    };
    const generation = this.authGeneration;
    const accountSub = state.account.sub;
    try {
      const points = await this.options.client.getPointsBalance(config, state.tokens.accessToken);
      // 拉取期间可能已退出登录：丢弃过期结果，不写缓存。
      if (!this.options.store.current) {
        return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
      }
      this.pointsBalance = points;
      this.emitStatus();
      return { ok: true, points };
    } catch (err) {
      if (err instanceof QraftError && err.code === 'SESSION_EXPIRED') {
        // access_token 已失效：主进程自动刷新可能刚好错过窗口，先刷新再重试一次。
        const refreshed = await this.refreshNow();
        if (!refreshed.ok) {
          return {
            ok: false,
            code: refreshed.code ?? 'REFRESH_FAILED',
            message: refreshed.message ?? '刷新 token 失败',
          };
        }
        const fresh = this.options.store.current;
        // 刷新期间可能退出登录/换账号：绝不拿新账号的凭据顶替原账号查询。
        if (!fresh || this.authGeneration !== generation || fresh.account.sub !== accountSub) {
          return {
            ok: false,
            code: 'SESSION_EXPIRED',
            message: '登录状态已变化，积分余额未拉取',
          };
        }
        try {
          const points = await this.options.client.getPointsBalance(
            {
              baseUrl: fresh.baseUrl,
              clientId: fresh.clientId,
              clientSecret: fresh.clientSecret,
              redirectUri: fresh.redirectUri,
            },
            fresh.tokens.accessToken
          );
          // 拉取期间可能再次退出登录：丢弃过期结果，不写缓存。
          if (!this.options.store.current) {
            return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
          }
          this.pointsBalance = points;
          this.emitStatus();
          return { ok: true, points };
        } catch (retryErr) {
          if (retryErr instanceof QraftError && retryErr.code === 'SESSION_EXPIRED') {
            // 新 token 仍被平台拒绝：会话整体失效（平台作废整会话等），
            // 自动退出登录并说明原因（issue #1160 / 自动退出）。
            this.logoutSessionExpired(
              '积分余额查询',
              '刷新后仍被平台拒绝（会话已失效）',
              generation
            );
          } else {
            this.options.log('WARN', pointsFailureLog(retryErr));
          }
          if (retryErr instanceof QraftError) {
            return { ok: false, code: retryErr.code, message: retryErr.message };
          }
          return {
            ok: false,
            code: 'INTERNAL',
            message: retryErr instanceof Error ? retryErr.message : String(retryErr),
          };
        }
      }
      this.options.log('WARN', pointsFailureLog(err));
      if (err instanceof QraftError) return { ok: false, code: err.code, message: err.message };
      return {
        ok: false,
        code: 'INTERNAL',
        message: err instanceof Error ? err.message : String(err),
      };
    }
  }

  /**
   * 以当前登录用户身份向平台提交反馈（issue #1054）。
   * 未登录返回 INVALID_CONFIG（调用方据此跳过平台通道）；access_token 失效
   * 先刷新重试一次，刷新失败按 refreshNow 的语义置 requiresRelogin 并推状态，
   * 由登录失效三件套（横幅 / 顶栏 chip / 发送拦截）引导重新登录。
   */
  async submitPlatformFeedback(req: {
    type?: string;
    content: string;
    contact?: string;
  }): Promise<FeedbackPlatformOutcome> {
    const state = this.options.store.current;
    if (!state) return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
    const config: ResolvedQraftConfig = {
      baseUrl: state.baseUrl,
      clientId: state.clientId,
      clientSecret: state.clientSecret,
      redirectUri: state.redirectUri,
    };
    const generation = this.authGeneration;
    const accountSub = state.account.sub;
    try {
      await this.options.client.submitFeedback(config, state.tokens.accessToken, req);
      return { ok: true };
    } catch (err) {
      if (err instanceof QraftError && err.code === 'SESSION_EXPIRED') {
        // 主进程自动刷新可能刚好错过窗口：先刷新再重试一次。
        const refreshed = await this.refreshNow();
        if (!refreshed.ok) return { ok: false, code: refreshed.code, message: refreshed.message };
        const fresh = this.options.store.current;
        // 刷新期间可能退出登录/换账号：绝不拿新账号的凭据顶替原账号提交。
        if (!fresh || this.authGeneration !== generation || fresh.account.sub !== accountSub) {
          return {
            ok: false,
            code: 'SESSION_EXPIRED',
            message: '登录状态已变化，反馈未提交到平台',
          };
        }
        try {
          await this.options.client.submitFeedback(
            {
              baseUrl: fresh.baseUrl,
              clientId: fresh.clientId,
              clientSecret: fresh.clientSecret,
              redirectUri: fresh.redirectUri,
            },
            fresh.tokens.accessToken,
            req
          );
          return { ok: true };
        } catch (retryErr) {
          if (retryErr instanceof QraftError && retryErr.code === 'SESSION_EXPIRED') {
            // 刷新后的新 token 仍被平台拒绝：会话整体失效 → 自动退出登录
            //（与积分余额查询路径同一判定，CodeRabbit #1255）。
            this.logoutSessionExpired(
              '反馈平台提交',
              '刷新后仍被平台拒绝（会话已失效）',
              generation
            );
          }
          if (retryErr instanceof QraftError) {
            return { ok: false, code: retryErr.code, message: retryErr.message };
          }
          return {
            ok: false,
            code: 'INTERNAL',
            message: retryErr instanceof Error ? retryErr.message : String(retryErr),
          };
        }
      }
      this.options.log(
        'WARN',
        `qraft: 反馈平台提交失败（${err instanceof QraftError ? err.code : err}）`
      );
      if (err instanceof QraftError) return { ok: false, code: err.code, message: err.message };
      return {
        ok: false,
        code: 'INTERNAL',
        message: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ── Slurm MCP 作业计费（issue #927）──────────────────────────────────

  /** 跨层去重用的稳定复合键：账号 + MCP 服务器 + 作业 ID。
   *
   * 不同 MCP 服务器上报相同 job_id 的作业互相独立（如 slurm-a 与
   * slurm-b 都有作业 123）；在途/内存/历史/持久索引四层统一使用，
   * jobId 本身仅用于展示（#936 CodeRabbit 评审）。
   */
  private slurmJobKey(accountSub: string, serverName: string, jobId: string): string {
    return `${accountSub}::${serverName}::${jobId}`;
  }

  /**
   * Slurm 作业扣费（issue #927，2026-09-04 产品确认）：作业状态变为
   * RUNNING 时由这里执行实际扣费（10 分/次，memo 携带作业信息）。
   * 作业已在运行——扣费失败（余额不足等）不阻断作业，记录到扣费历史
   * 并通过 points 事件流提示。
   *
   * 去重：同一 charge_id 或同一作业 ID 只扣一次（历史文件持久化，
   * 跨重启不重复扣费）；token 失效时先尝试一次刷新再重试。
   */
  async chargeSlurmJob(payload: {
    charge_id?: string;
    job_id?: string;
    server_name?: string;
    tool_name?: string;
    args_summary?: string;
    session_key?: string;
    turn_id?: string;
  }): Promise<SlurmChargeResult> {
    const chargeId = String(payload.charge_id ?? '').slice(0, 128);
    const jobId = String(payload.job_id ?? '').slice(0, 64);
    if (!chargeId) return { ok: false, code: 'INVALID_CONFIG', message: '计费请求缺少 charge_id' };
    // 无作业 ID 的请求拒绝在扣费之前：jobId 是去重键的组成部分，
    // 缺失时重复轮询会以新 charge_id 反复扣费（Python 侧同样跳过）。
    if (!jobId) return { ok: false, code: 'INVALID_CONFIG', message: '计费请求缺少 job_id' };

    // 并发去重：同一 charge_id / 复合作业键（账号+服务器+作业 ID）的
    // 在途请求共享同一次扣费，后到者等待首个结果（状态轮询会并发报告 RUNNING）。
    const inFlightKey = `c:${chargeId}`;
    // 空 subject（浏览器登录 userinfo 失败）不作复合作业键：索引跨登出保留后
    // `::server::jobId` 会在账号之间串用，既可能误挡他人作业也可能被人误挡。
    const inFlightSub = this.options.store.current?.account.sub ?? '';
    const inFlightJobKey = inFlightSub
      ? `j:${this.slurmJobKey(inFlightSub, String(payload.server_name ?? '').slice(0, 64), jobId)}`
      : null;
    const inFlight =
      this.inFlightCharges.get(inFlightKey) ??
      (inFlightJobKey ? this.inFlightCharges.get(inFlightJobKey) : undefined);
    if (inFlight) {
      this.options.log('INFO', `qraft: slurm 扣费在途去重（charge=${chargeId.slice(0, 8)}）`);
      return inFlight;
    }

    const run = this.runSlurmCharge(chargeId, jobId, payload);
    this.inFlightCharges.set(inFlightKey, run);
    if (inFlightJobKey) this.inFlightCharges.set(inFlightJobKey, run);
    try {
      return await run;
    } finally {
      this.inFlightCharges.delete(inFlightKey);
      if (inFlightJobKey) this.inFlightCharges.delete(inFlightJobKey);
    }
  }

  private async runSlurmCharge(
    chargeId: string,
    jobId: string,
    payload: Record<string, unknown>
  ): Promise<SlurmChargeResult> {
    const state = this.options.store.current;
    if (!state) {
      return {
        ok: false,
        code: 'INVALID_CONFIG',
        message: '尚未登录 MiQroForge，无法完成 Slurm 作业计费',
      };
    }

    // 去重：同一 charge_id 或同一复合作业键（账号+服务器+作业 ID）
    // 只扣一次。内存集合为第一道（历史文件写盘失败时同进程内仍不
    // 重复扣费），历史文件覆盖跨重启。
    const accountSub = state.account.sub;
    const serverName = String(payload.server_name ?? '').slice(0, 64);
    // 空 subject 不构键：userinfo 失败留下的空 sub 会让 `::server::jobId`
    // 在账号之间串用（索引现在跨登出保留），退化为仅按 charge_id（每次
    // 工具调用新生成的 uuid4）去重，不会牵连其他账号的记录。
    const jobKey = accountSub ? this.slurmJobKey(accountSub, serverName, jobId) : '';
    const history = this.loadBillingHistory();
    const existing =
      history.find((e) => e.chargeId === chargeId) ||
      (jobKey
        ? history.find(
            (e) =>
              e.status === 'billed' &&
              this.slurmJobKey(e.accountSub ?? '', e.serverName ?? '', e.jobId ?? '') === jobKey
          )
        : undefined);
    if (existing) {
      this.options.log(
        'INFO',
        `qraft: slurm 扣费去重命中（charge=${chargeId.slice(0, 8)}，job=${jobId || '-'}）`
      );
      return existing.status === 'billed'
        ? { ok: true, balance: existing.balanceAfter, dedup: true }
        : { ok: false, code: existing.status, message: '该作业已计费过，未重复扣费', dedup: true };
    }
    if (this.billedChargeIds.has(chargeId) || (jobKey && this.billedJobIds.has(jobKey))) {
      this.options.log('INFO', `qraft: slurm 扣费内存去重命中（job=${jobId || '-'}）`);
      return {
        ok: true,
        balance: this.pointsBalance?.availablePoints,
        dedup: true,
      };
    }

    // 单个时间戳贯穿 memo 与历史记录；memo 各字段分别截断，保证 JSON
    // 完整有效且 session/turn 字段不因整体切片而丢失。
    const now = new Date().toISOString();
    const memo = JSON.stringify({
      jobId: jobId || null,
      tool: `${payload.server_name ?? ''}.${payload.tool_name ?? ''}`.slice(0, 120),
      args: String(payload.args_summary ?? '').slice(0, 200),
      session: String(payload.session_key ?? '').slice(0, 128),
      turn: String(payload.turn_id ?? '').slice(0, 64),
      submittedAt: now,
    });
    const generation = this.authGeneration;

    const config: ResolvedQraftConfig = {
      baseUrl: state.baseUrl,
      clientId: state.clientId,
      clientSecret: state.clientSecret,
      redirectUri: state.redirectUri,
    };

    try {
      let balance: QraftPointsBalance;
      try {
        balance = await this.options.client.deductPoints(config, state.tokens.accessToken, {
          amount: SLURM_JOB_COST,
          source: 'slurm-job',
          resourceType: 'slurm',
          memo,
        });
      } catch (err) {
        // token 失效：主进程自动刷新可能刚好错过窗口，先刷新再重试一次。
        if (err instanceof QraftError && err.code === 'SESSION_EXPIRED') {
          this.options.log('WARN', 'qraft: slurm 扣费前 token 失效，尝试刷新后重试');
          const refreshed = await this.refreshNow();
          if (refreshed.ok) {
            // 刷新期间可能退出登录/换账号：登录代际或账号身份变化时
            // 绝不拿新账号的凭据给旧作业计费（C4）。
            const fresh = this.options.store.current;
            if (!fresh || this.authGeneration !== generation || fresh.account.sub !== accountSub) {
              throw new QraftError('INTERNAL', '登录状态在计费期间发生变化，本次作业计费已取消');
            }
            // 刷新后的新 token 仍被平台拒绝 = 会话整体失效 → 自动退出登录
            //（与积分余额查询路径同一判定，CodeRabbit #1255）。只在这一步
            // 判定：刷新本身失败属瞬时/永久由刷新路径负责，不在这里重复。
            try {
              balance = await this.options.client.deductPoints(
                {
                  ...config,
                  baseUrl: fresh.baseUrl,
                  clientId: fresh.clientId,
                  clientSecret: fresh.clientSecret,
                },
                fresh.tokens.accessToken,
                { amount: SLURM_JOB_COST, source: 'slurm-job', resourceType: 'slurm', memo }
              );
            } catch (retryErr) {
              if (retryErr instanceof QraftError && retryErr.code === 'SESSION_EXPIRED') {
                this.logoutSessionExpired(
                  'Slurm 作业扣费',
                  '刷新后仍被平台拒绝（会话已失效）',
                  generation
                );
              }
              throw retryErr;
            }
          } else {
            throw err;
          }
        } else {
          throw err;
        }
      }

      // 先记内存集合（持久化失败也保去重），再落历史文件与
      // 无上限 job-id 索引（展示历史截断不丢去重）。
      this.billedChargeIds.add(chargeId);
      if (jobKey) {
        this.billedJobIds.add(jobKey);
        this.persistBilledJobId(jobKey);
      }
      this.pointsBalance = balance;
      this.appendBillingHistory({
        chargeId,
        jobId: jobId || undefined,
        deductedAt: now,
        cost: SLURM_JOB_COST,
        balanceAfter: balance.availablePoints,
        status: 'billed',
        serverName: String(payload.server_name ?? ''),
        toolName: String(payload.tool_name ?? ''),
        argsSummary: String(payload.args_summary ?? '').slice(0, 200),
        sessionKey: String(payload.session_key ?? ''),
        accountSub,
      });
      this.emitStatus();
      this.options.log(
        'INFO',
        `qraft: slurm 作业扣费成功（charge=${chargeId.slice(0, 8)}，余额 ${balance.availablePoints}）`
      );
      return { ok: true, balance: balance.availablePoints };
    } catch (err) {
      const code = err instanceof QraftError ? err.code : 'INTERNAL';
      const message = err instanceof Error ? err.message : String(err);
      const status: QraftBillingHistoryEntry['status'] =
        code === 'INSUFFICIENT_POINTS' ? 'insufficient' : 'error';
      this.appendBillingHistory({
        chargeId,
        jobId: jobId || undefined,
        deductedAt: now,
        cost: SLURM_JOB_COST,
        status,
        serverName: String(payload.server_name ?? ''),
        toolName: String(payload.tool_name ?? ''),
        argsSummary: String(payload.args_summary ?? '').slice(0, 200),
        sessionKey: String(payload.session_key ?? ''),
        accountSub,
      });
      this.options.log('WARN', `qraft: slurm 作业扣费失败（${code}）`);
      return { ok: false, code, message };
    }
  }

  /** 读取扣费历史（新→旧；只返回当前登录账号的记录）。 */
  getBillingHistory(): QraftBillingHistoryEntry[] {
    const state = this.options.store.current;
    // 未登录不外发任何记录：历史文件保留其他账号的条目。
    if (!state) return [];
    const sub = state.account.sub;
    // 账号身份未知（浏览器登录 userinfo 失败会留下空 sub）时同样不外发：
    // 空 sub 无法作过滤依据，返回全量等于把其他账号的作业与计费数据
    // 展示给当前用户（CodeRabbit #1067）。
    if (!sub) return [];
    const history = this.loadBillingHistory();
    // 无 accountSub 的是加字段前的老记录（归属不可知，按历史行为展示）；
    // 空字符串来自身份未知的一次会话，无法归属到任何账号，不外发。
    return history.filter((e) => e.accountSub === undefined || e.accountSub === sub);
  }

  // ── 扣费历史持久化（userData/qraft-billing-history.json）──────────────

  private loadBillingHistory(): QraftBillingHistoryEntry[] {
    const filePath = this.options.billingHistoryPath?.();
    if (!filePath) return [];
    try {
      if (!existsSync(filePath)) return [];
      const raw = JSON.parse(readFileSync(filePath, 'utf8')) as unknown;
      if (!Array.isArray(raw)) return [];
      return raw as QraftBillingHistoryEntry[];
    } catch {
      return [];
    }
  }

  private appendBillingHistory(entry: QraftBillingHistoryEntry): void {
    const history = this.loadBillingHistory();
    history.unshift(entry);
    this.writeBillingHistory(history.slice(0, MAX_BILLING_HISTORY));
  }

  private writeBillingHistory(history: QraftBillingHistoryEntry[]): void {
    const filePath = this.options.billingHistoryPath?.();
    if (!filePath) return;
    try {
      mkdirSync(dirname(filePath), { recursive: true });
      writeFileSync(filePath, JSON.stringify(history, null, 2), { encoding: 'utf8' });
    } catch (err) {
      this.options.log(
        'WARN',
        `qraft: 扣费历史写入失败（${err instanceof Error ? err.message : err}）`
      );
    }
  }

  // ── 已计费作业 ID 索引（无上限持久化，展示历史截断不丢去重）────

  private loadBilledJobIds(): string[] {
    const filePath = this.options.billedJobIdsPath?.();
    if (!filePath) return [];
    try {
      if (!existsSync(filePath)) return [];
      const raw = JSON.parse(readFileSync(filePath, 'utf8')) as unknown;
      if (!Array.isArray(raw)) return [];
      return raw.filter((v): v is string => typeof v === 'string');
    } catch {
      return [];
    }
  }

  private persistBilledJobId(jobId: string): void {
    const filePath = this.options.billedJobIdsPath?.();
    if (!filePath) return;
    try {
      const existing = new Set(this.loadBilledJobIds());
      existing.add(jobId);
      mkdirSync(dirname(filePath), { recursive: true });
      writeFileSync(filePath, JSON.stringify([...existing]), { encoding: 'utf8' });
    } catch (err) {
      this.options.log(
        'WARN',
        `qraft: 计费索引写入失败（${err instanceof Error ? err.message : err}）`
      );
    }
  }

  /** 手动刷新（设置页"刷新"按钮）。
   *
   *  刷新 token 之后顺带补拉一次 userinfo（syncInfo，见 syncAccountInfo）：
   *  平台在用户登录**之后**才开通网关是常态，这个按钮也是用户唯一的
   *  「重新取一次下发」入口 —— 只刷 token 的话，界面会一直停在「未下发」。 */
  async refreshNow(opts: { syncInfo?: boolean } = {}): Promise<QraftLoginResult> {
    const state = this.options.store.current;
    if (!state) return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
    // 发起前捕获登录代际：失败回来时若已登出/重登，这份失败属于旧会话。
    const generation = this.authGeneration;
    const refresh = this.doRefresh(state);
    try {
      await refresh;
      this.refreshRetryAttempt = 0;
      this.refreshError = null;
      this.requiresRelogin = false;
      this.emitStatus();
      // 补拉失败不影响刷新结果：token 已经是新的，网关信息下次再取。
      if (opts.syncInfo !== false) await this.syncAccountInfo();
      return { ok: true, account: this.options.store.current?.account ?? state.account };
    } catch (err) {
      this.options.log(
        'ERROR',
        `qraft: 手动刷新失败（${err instanceof QraftError ? err.code : err}）`
      );
      if (err instanceof QraftError) {
        // 手动与自动刷新共享同一个 inFlightRefresh：同一失败的并发观察者
        // 不再重复处理 —— 先到的路径已归类失败并排好退避重试（CodeRabbit #1114）。
        if (this.lastHandledRefreshFailure !== refresh) {
          this.lastHandledRefreshFailure = refresh;
          this.handleRefreshFailure(err, state, '手动', generation);
        }
        return { ok: false, code: err.code, message: err.message };
      }
      return {
        ok: false,
        code: 'INTERNAL',
        message: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ── 账号 / 网关信息补拉 ────────────────────────────────────────────────

  /**
   * 重新拉取 `/oauth2/userinfo`，把账号与网关信息落盘（含 token 文件同步）。
   * 安全入口：补拉是尽力而为的，绝不让异常影响调用方（刷新结果、启动流程）
   * ——否则会被刷新路径误判成刷新失败去排重试。
   */
  private async syncAccountInfo(): Promise<void> {
    try {
      await this.syncAccountInfoInner();
    } catch (err) {
      this.options.log(
        'WARN',
        `qraft: 补拉网关信息异常（${err instanceof Error ? err.message : err}）`
      );
    }
  }

  /**
   * 为什么需要补拉：AI 网关是平台按账号开通的，而开通动作经常发生在用户登录
   * **之后**（或登录那一次 userinfo 恰好失败 —— 登录流程只记警告、照常成功）。
   * 而 userinfo 原先只在两处登录入口调用过，token 自动刷新与设置页「立即刷新」
   * 都不重拉，登录态里那份「未下发」就永远留着，用户只能退出登录重登。
   *
   * 调用点：应用启动、手动刷新、token 自动刷新成功后。拉不到「可用」网关时
   * 按退避再试有限次（见 scheduleGatewayInfoRetry），平台侧开通后无需重登。
   */
  private async syncAccountInfoInner(): Promise<void> {
    const initial = this.options.store.current;
    if (!initial) return;
    const generation = this.authGeneration;
    const accountSub = initial.account.sub;

    let outcome = await this.fetchUserInfo(initial);
    if (!outcome.ok && outcome.expired) {
      // access_token 可能刚好过期（启动补拉时常见）：刷新一次再取。
      // 这里的刷新不再回头补拉（syncInfo:false），避免两条路径互相递归。
      const refreshed = await this.refreshNow({ syncInfo: false });
      if (!refreshed.ok) return;
      const fresh = this.options.store.current;
      if (!fresh) return;
      outcome = await this.fetchUserInfo(fresh);
    }
    if (!outcome.ok) {
      this.options.log('WARN', `qraft: 补拉网关信息失败（${outcome.code}）`);
      this.scheduleGatewayInfoRetry();
      return;
    }

    // 补拉期间登出 / 换账号：丢弃结果，绝不把别的账号的信息写进当前登录态。
    const current = this.options.store.current;
    if (!current || this.authGeneration !== generation || current.account.sub !== accountSub) {
      return;
    }
    // 平台返回的账号与本地登录态不一致：多半是凭据串了，宁可停在旧信息上
    // 也不能悄悄切换账号（工作区根是按 sub 解析的）。
    const { info } = outcome;
    if (info.sub && accountSub && info.sub !== accountSub) {
      this.options.log(
        'WARN',
        `qraft: userinfo 账号（${info.sub}）与登录态（${accountSub}）不一致，忽略本次补拉`
      );
      return;
    }

    const next: QraftStoredState = {
      ...current,
      account: {
        ...current.account,
        sub: info.sub || current.account.sub,
        username: info.username || current.account.username,
        nickname: info.nickname || current.account.nickname,
      },
      // 本次没带网关时保留原有值（可能来自更早一次下发），不因「这次没带」清空。
      ...(info.aiGateway ? { aiGateway: info.aiGateway } : {}),
      ...(info.mcpGatewayKey ? { mcpGatewayKey: info.mcpGatewayKey } : {}),
    };
    this.options.store.save(next);
    this.syncTokenFile(next);
    this.emitStatus();
    this.options.log(
      'INFO',
      `qraft: 账号/网关信息已更新（网关 ${info.aiGateway?.status ?? '未下发'}）`
    );
    if (!isGatewayUsable(next)) this.scheduleGatewayInfoRetry();
    else this.cancelGatewayInfoRetry();
  }

  /** 单次 userinfo 调用；expired 表示 access_token 失效（调用方可刷新后重试）。 */
  private async fetchUserInfo(
    state: QraftStoredState
  ): Promise<
    | { ok: true; info: Awaited<ReturnType<QraftClient['getUserInfo']>> }
    | { ok: false; expired: boolean; code: string }
  > {
    try {
      const info = await this.options.client.getUserInfo(
        {
          baseUrl: state.baseUrl,
          clientId: state.clientId,
          clientSecret: state.clientSecret,
          redirectUri: state.redirectUri,
        },
        state.tokens.accessToken
      );
      return { ok: true, info };
    } catch (err) {
      if (err instanceof QraftError)
        return { ok: false, expired: err.code === 'SESSION_EXPIRED', code: err.code };
      return { ok: false, expired: false, code: err instanceof Error ? err.message : String(err) };
    }
  }

  /**
   * 网关未下发 / 开通中 / 开通失败时的退避重试（1 分钟起步翻倍，封顶 8 分钟，
   * 最多 5 次）。
   *
   * 平台侧开通发生在登录之后是常态，这个重试让应用自己「过一会儿就好了」，
   * 而不是逼用户退出重登或重启。拿到可用网关即停（见 isGatewayUsable）。
   */
  private scheduleGatewayInfoRetry(): void {
    if (this.gatewayInfoTimer !== null) return; // 已经排好，不重复排
    if (this.gatewayInfoAttempt >= GATEWAY_INFO_RETRY_LIMIT) return;
    const delay = Math.min(
      GATEWAY_INFO_RETRY_BASE_MS * 2 ** this.gatewayInfoAttempt,
      GATEWAY_INFO_RETRY_MAX_MS
    );
    this.gatewayInfoAttempt += 1;
    this.gatewayInfoTimer = setTimeout(() => {
      this.gatewayInfoTimer = null;
      void this.syncAccountInfo();
    }, delay);
    this.options.log('INFO', `qraft: 网关信息未就绪，${Math.round(delay / 60_000)} 分钟后重新拉取`);
  }

  private cancelGatewayInfoRetry(): void {
    if (this.gatewayInfoTimer !== null) {
      clearTimeout(this.gatewayInfoTimer);
      this.gatewayInfoTimer = null;
    }
    this.gatewayInfoAttempt = 0;
  }

  // ── 自动刷新调度 ──────────────────────────────────────────────────────

  /** 到期前 15 分钟刷新；瞬时失败按指数退避静默重试（见 tickRefresh）。 */
  private scheduleRefresh(state: QraftStoredState): void {
    this.cancelRefresh();
    const now = Date.now();
    const fireAt = state.tokens.expiresAt - REFRESH_ADVANCE_MS;
    if (now >= fireAt) {
      // 已到期/进入提前刷新窗口（应用重启后）：立即尝试刷新。
      this.refreshScheduledAt = now;
      this.refreshTimer = setTimeout(() => void this.tickRefresh(state), 0);
      return;
    }
    // 超长有效期（> 24.8 天）超出 setTimeout 上限，必须封顶（见
    // MAX_TIMEOUT_MS）：提前醒来时若仍未到刷新时刻则重新调度，
    // 而不是直接刷新，避免溢出截断成 1ms 后的高频刷新循环。
    const delay = Math.min(fireAt - now, MAX_TIMEOUT_MS);
    this.refreshScheduledAt = now + delay;
    this.refreshTimer = setTimeout(() => {
      if (Date.now() < fireAt) {
        this.scheduleRefresh(state);
        return;
      }
      void this.tickRefresh(state);
    }, delay);
    this.options.log('INFO', `qraft: 已调度自动刷新（${Math.round(delay / 60_000)} 分钟后）`);
  }

  private cancelRefresh(): void {
    if (this.refreshTimer !== null) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
    this.refreshScheduledAt = null;
  }

  private async tickRefresh(state: QraftStoredState): Promise<void> {
    // 已退出登录（store 已清）时丢弃过期定时任务，不重试也不写回任何状态。
    if (!this.options.store.current) return;
    // 发起前捕获登录代际（同 refreshNow）：失败回来时可能已经登出/重登。
    const generation = this.authGeneration;
    const refresh = this.doRefresh(state);
    try {
      await refresh;
      this.refreshRetryAttempt = 0;
      this.refreshError = null;
      this.requiresRelogin = false;
      this.emitStatus();
      // 每次自动刷新顺带补拉一次网关信息：应用长期驻留时也能拿到后来才
      // 开通的网关（拉不到时按退避重试，见 syncAccountInfo）。
      await this.syncAccountInfo();
    } catch (err) {
      if (!this.options.store.current) return; // 失败发生在登出前后：同样丢弃
      // 同一失败的并发观察者（手动刷新也 await 同一个 inFlightRefresh）：
      // 失败只处理一次，避免重复递增退避代数、把已排好的重试定时器后推
      // （CodeRabbit #1114）。
      if (this.lastHandledRefreshFailure === refresh) return;
      this.lastHandledRefreshFailure = refresh;
      this.handleRefreshFailure(err, state, '自动', generation);
    }
  }

  /**
   * 平台判定登录已失效（refresh_token 被作废 / 会话被平台拒绝）时的收尾：
   * **自动退出登录**，而不是留在「已登录但平台调用必然失败」的僵尸状态里，
   * 等用户自己点重新登录。退出后登录门（#1095）把人停在登录页，并由
   * sessionExpired 标记说明「登录已失效，已自动退出」——用户不会看到
   * 一个没有解释的登录页，也不会误以为应用崩了。
   *
   * 只处理永久失效：瞬时失败（网络/平台 5xx）继续静默退避重试，不打扰用户。
   *
   * `generation` 是发起这次请求时的登录代际：退出登录/重新登录会把它 +1，
   * 于是「上一份登录态的在途请求失败后才回来」不会被拿来踢掉新会话
   *（CodeRabbit #1255）—— 否则用户刚重登成功就会被一条旧失败退出登录。
   */
  private logoutSessionExpired(via: string, reason: string, generation: number): void {
    if (this.authGeneration !== generation) {
      this.options.log('WARN', `qraft: ${via}：${reason}，但登录态已变化，忽略本次自动退出`);
      return;
    }
    this.options.log('WARN', `qraft: ${via}：${reason}，已自动退出登录`);
    this.logout({ sessionExpired: true });
  }

  /**
   * 刷新失败的统一处理（refreshNow 与 tickRefresh 共用，按在途 Promise
   * 身份去重后只调用一次）：
   *   - REFRESH_TOKEN_INVALID（平台作废）：永久失败，不再重试，自动退出登录
   *     （见 logoutSessionExpired）；
   *   - 其余（网络/平台 5xx）：瞬时失败，不打扰用户，指数退避静默重试
   *     （issue #1087）。
   *
   * `generation` 由调用方在发起刷新前捕获，用于判定这份失败是否还属于当前
   * 登录态（见 logoutSessionExpired）。
   */
  private handleRefreshFailure(
    err: unknown,
    state: QraftStoredState,
    via: '自动' | '手动',
    generation: number
  ): void {
    const code = err instanceof QraftError ? err.code : 'REFRESH_FAILED';
    if (this.authGeneration !== generation) {
      // 这份失败属于已经结束的登录态（期间登出或重新登录）：既不能拿它
      // 踢掉新会话，也不能把错误码/退避重试写到新会话的状态上（CodeRabbit #1255）。
      this.options.log('WARN', `qraft: ${via}刷新失败（${code}）属于已结束的登录态，忽略`);
      return;
    }
    this.refreshError = code;
    if (isPermanentRefreshError(code)) {
      this.logoutSessionExpired(via + '刷新失败', '平台判定 refresh_token 已失效', generation);
    } else {
      const delay = this.nextRefreshRetryDelay();
      this.refreshRetryAttempt += 1;
      this.cancelRefresh();
      this.refreshScheduledAt = Date.now() + delay;
      this.refreshTimer = setTimeout(() => void this.tickRefresh(state), delay);
      this.options.log(
        'WARN',
        `qraft: ${via}刷新失败（${code}），${Math.round(delay / 60_000)} 分钟后静默重试`
      );
    }
    this.emitStatus();
  }

  /** 当前代数的退避重试间隔：1 分钟起步翻倍，封顶 30 分钟。 */
  private nextRefreshRetryDelay(): number {
    return Math.min(REFRESH_RETRY_BASE_MS * 2 ** this.refreshRetryAttempt, REFRESH_RETRY_MAX_MS);
  }

  /** 刷新进行中的去重：手动刷新与自动刷新并发时共享同一次请求，避免
   *  两次 refreshTokens 并发写盘、后写覆盖新 token（若服务端启用轮换语义）。 */
  private inFlightRefresh: Promise<void> | null = null;

  private doRefresh(state: QraftStoredState): Promise<void> {
    if (this.inFlightRefresh) return this.inFlightRefresh;
    const generation = this.authGeneration;
    this.inFlightRefresh = this.runRefresh(state, generation).finally(() => {
      // 只有代际未变（未退出登录）才清理；登出后新登录创建的刷新
      // 不被旧请求的 finally 误清。
      if (this.authGeneration === generation) this.inFlightRefresh = null;
    });
    return this.inFlightRefresh;
  }

  private async runRefresh(state: QraftStoredState, generation: number): Promise<void> {
    const config: ResolvedQraftConfig = {
      baseUrl: state.baseUrl,
      clientId: state.clientId,
      clientSecret: state.clientSecret,
      redirectUri: state.redirectUri,
    };
    const tokens = await this.options.client.refreshTokens(config, state.tokens.refreshToken);
    if (this.authGeneration !== generation) {
      // 退出登录发生在刷新完成前：丢弃本次结果，不落盘、不写 token 文件。
      this.options.log('WARN', 'qraft: 刷新完成前已退出登录，丢弃本次刷新结果');
      return;
    }
    // 新平台轮换 refresh_token（旧值服务端立即失效）：必须以响应中的新值为准
    // 落盘，否则下一次刷新必然失败（REFRESH_TOKEN_INVALID）。
    const next: QraftStoredState = { ...state, tokens };
    this.options.store.save(next);
    this.scheduleRefresh(next);
    this.syncTokenFile(next);
  }

  private emitStatus(): void {
    try {
      this.options.onStatusChanged?.(this.status());
    } catch {
      /* 回调异常不影响主流程 */
    }
  }
}
