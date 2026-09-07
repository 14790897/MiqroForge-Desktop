/**
 * QraftService — 登录态的生命周期编排：
 *   登录（平台登录 → 授权码流程 → userinfo）→ 加密落盘 → 自动刷新调度
 *   → 状态事件推送 → 退出登录清理。
 *
 * 刷新策略按实测数据：access_token 约 2 小时（expires_in=7199），
 * 提前 15 分钟用 refresh_token 刷新；刷新失败标记 requiresRelogin，
 * 由设置页引导用户重新登录。
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
import { CookieJar } from './cookie-jar';
import { QraftClient, QraftError, type QraftLogger, type ResolvedQraftConfig } from './client';
import { maskSecret } from './rsa';
import { QraftStore } from './store';
import {
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
import type { QraftBillingHistoryEntry } from '../../shared/ipc';

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
/** 瞬时失败（网络等）的退避重试间隔（30 分钟后重试）。
 *  refresh_token 已失效（REFRESH_TOKEN_INVALID）属永久错误，不重试。 */
const REFRESH_RETRY_MS = 30 * 60_000;

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
  // 1024–65535 随机端口；测试环境不校验注册值，生产环境需与注册值一致
  //（可在设置页"高级设置"中覆盖）。
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
  const env: QraftEnv = opts.env ?? stored?.env ?? 'test';
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
      // 测试环境不校验注册值，可自动生成 loopback 地址；
      // 生产环境必须使用注册值，缺失时由 validateConfig 拒绝。
      (env === 'test' ? makeRedirectUri() : ''),
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
      this.scheduleRefresh(stored);
      this.syncTokenFile(stored);
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
      const env: QraftEnv = opts.env ?? stored?.env ?? 'test';
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
      } catch (err) {
        this.options.log(
          'WARN',
          `qraft: userinfo 获取失败（${err instanceof QraftError ? err.code : err}），回退使用登录响应信息`
        );
      }

      this.persistLogin(env, config, account, tokens, aiGateway);
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
      const env: QraftEnv = opts.env ?? stored?.env ?? 'test';
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
      try {
        const info = await this.options.client.getUserInfo(config, tokens.accessToken);
        account = {
          phone: '',
          sub: info.sub,
          username: info.username,
          nickname: info.nickname,
        };
        aiGateway = info.aiGateway;
      } catch (err) {
        this.options.log(
          'WARN',
          `qraft: 浏览器登录 userinfo 获取失败（${err instanceof QraftError ? err.code : err}），账号信息留空`
        );
      }

      this.persistLogin(env, config, account, tokens, aiGateway);
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
    const env: QraftEnv = opts.env ?? stored?.env ?? 'test';
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
    aiGateway?: QraftAiGateway
  ): void {
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
    };
    this.options.store.save(state);
    this.refreshError = null;
    this.requiresRelogin = false;
    this.scheduleRefresh(state);
    this.syncTokenFile(state);
    this.emitStatus();
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
    if (!state) return { loggedIn: false };
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
        // 已过期且最近一次自动刷新失败 → 引导重新登录
        (this.refreshError !== null && now > state.tokens.expiresAt),
      points: this.pointsBalance ?? undefined,
      // 只透出非敏感网关信息（status/configVersion）；encryptedApiKey 不外发渲染进程。
      aiGateway: state.aiGateway
        ? { status: state.aiGateway.status, configVersion: state.aiGateway.configVersion }
        : undefined,
    };
  }

  logout(): void {
    this.cancelRefresh();
    // 使登出前发起的在途刷新结果作废（runRefresh 代际校验丢弃）。
    this.authGeneration += 1;
    this.inFlightRefresh = null;
    this.jar.clear();
    this.options.store.clear();
    this.deleteTokenFile();
    this.refreshError = null;
    this.requiresRelogin = false;
    this.pointsBalance = null;
    // Slurm 扣费历史随登出清除（换账号后不展示前任账号的计费记录）。
    this.billedChargeIds.clear();
    this.billedJobIds.clear();
    this.inFlightCharges.clear();
    const jobIdsPath = this.options.billedJobIdsPath?.();
    if (jobIdsPath) {
      try {
        rmSync(jobIdsPath, { force: true });
      } catch (err) {
        this.options.log(
          'WARN',
          `qraft: 计费索引删除失败（${err instanceof Error ? err.message : err}）`
        );
      }
    }
    const historyPath = this.options.billingHistoryPath?.();
    if (historyPath) {
      try {
        rmSync(historyPath, { force: true });
      } catch (err) {
        this.options.log(
          'WARN',
          `qraft: 扣费历史删除失败（${err instanceof Error ? err.message : err}）`
        );
      }
    }
    this.options.log('INFO', 'qraft: 已退出登录（cookie 与 token 均已清除）');
    this.emitStatus();
  }

  /** 拉取最新积分余额（设置页/登录后调用），成功后缓存并推送状态。 */
  async fetchPointsBalance(): Promise<
    { ok: true; points: QraftPointsBalance } | { ok: false; code: QraftErrorCode; message: string }
  > {
    const state = this.options.store.current;
    if (!state) return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
    try {
      const config: ResolvedQraftConfig = {
        baseUrl: state.baseUrl,
        clientId: state.clientId,
        clientSecret: state.clientSecret,
        redirectUri: state.redirectUri,
      };
      const points = await this.options.client.getPointsBalance(config, state.tokens.accessToken);
      // 拉取期间可能已退出登录：丢弃过期结果，不写缓存。
      if (!this.options.store.current) {
        return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
      }
      this.pointsBalance = points;
      this.emitStatus();
      return { ok: true, points };
    } catch (err) {
      this.options.log(
        'WARN',
        `qraft: 查询积分余额失败（${err instanceof QraftError ? err.code : err}）`
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
    const jobKey = jobId
      ? this.slurmJobKey(
          this.options.store.current?.account.sub ?? '',
          String(payload.server_name ?? '').slice(0, 64),
          jobId
        )
      : '';
    const inFlightJobKey = jobKey ? `j:${jobKey}` : null;
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
    const jobKey = jobId ? this.slurmJobKey(accountSub, serverName, jobId) : '';
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
    const sub = this.options.store.current?.account.sub;
    const history = this.loadBillingHistory();
    return sub ? history.filter((e) => !e.accountSub || e.accountSub === sub) : history;
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

  /** 手动刷新（设置页"刷新"按钮）。 */
  async refreshNow(): Promise<QraftLoginResult> {
    const state = this.options.store.current;
    if (!state) return { ok: false, code: 'INVALID_CONFIG', message: '尚未登录' };
    try {
      await this.doRefresh(state);
      this.refreshError = null;
      this.requiresRelogin = false;
      this.emitStatus();
      return { ok: true, account: state.account };
    } catch (err) {
      this.options.log(
        'ERROR',
        `qraft: 手动刷新失败（${err instanceof QraftError ? err.code : err}）`
      );
      if (err instanceof QraftError) {
        this.refreshError = err.code;
        this.requiresRelogin = true;
        if (err.code === 'REFRESH_TOKEN_INVALID') {
          // 永久失败：撤销还在排队的自动刷新定时器 —— 用已失效的
          // refresh_token 重试必然失败，只会在计划时间点再报一次错。
          this.cancelRefresh();
        }
        this.emitStatus();
        return { ok: false, code: err.code, message: err.message };
      }
      return {
        ok: false,
        code: 'INTERNAL',
        message: err instanceof Error ? err.message : String(err),
      };
    }
  }

  // ── 自动刷新调度 ──────────────────────────────────────────────────────

  /** 到期前 15 分钟刷新；刷新失败 30 分钟后重试一次。 */
  private scheduleRefresh(state: QraftStoredState): void {
    this.cancelRefresh();
    const now = Date.now();
    const expiresAt = state.tokens.expiresAt;
    if (now >= expiresAt) {
      // 已过期（应用重启后）：立即尝试刷新。
      const delay = 0;
      this.refreshScheduledAt = now;
      this.refreshTimer = setTimeout(() => void this.tickRefresh(state), delay);
      return;
    }
    const delay = Math.max(0, expiresAt - REFRESH_ADVANCE_MS - now);
    this.refreshScheduledAt = now + delay;
    this.refreshTimer = setTimeout(() => void this.tickRefresh(state), delay);
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
    try {
      await this.doRefresh(state);
      this.refreshError = null;
      this.requiresRelogin = false;
      this.emitStatus();
    } catch (err) {
      if (!this.options.store.current) return; // 失败发生在登出前后：同样丢弃
      const code = err instanceof QraftError ? err.code : 'REFRESH_FAILED';
      this.refreshError = code;
      this.requiresRelogin = true;
      if (code === 'REFRESH_TOKEN_INVALID') {
        // refresh_token 已失效属永久错误：重试必然失败，停止自动重试，
        // 由设置页引导重新登录（refreshError 保留错误码供 UI 展示指引）。
        this.refreshScheduledAt = null;
        this.options.log(
          'ERROR',
          `qraft: 自动刷新失败（${code}）：refresh_token 已失效，请重新登录（不再自动重试）`
        );
        this.emitStatus();
        return;
      }
      this.options.log('ERROR', `qraft: 自动刷新失败（${code}），30 分钟后重试`);
      this.refreshScheduledAt = Date.now() + REFRESH_RETRY_MS;
      this.refreshTimer = setTimeout(() => void this.tickRefresh(state), REFRESH_RETRY_MS);
      this.emitStatus();
    }
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
