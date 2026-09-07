/**
 * Qraft OAuth2 登录 — 主进程内部类型。
 *
 * 渲染进程与主进程共用的协议类型（QraftAccount / QraftErrorCode /
 * QraftLoginResult / QraftStatus）统一定义在 apps/desktop/src/shared/ipc.ts，
 * 这里直接复用并 re-export，避免两份声明漂移。
 *
 * 依据《Qraft OAuth2 接入实测文档》(issue #726)：
 * - access_token 实测有效期约 2 小时（expires_in=7199），并非官方的 24 小时；
 * - refresh_token 轮换（刷新成功后旧值立即失效），必须持久化响应中的新值；
 *   平台升级可能作废存量 refresh_token，此时刷新返回 REFRESH_TOKEN_INVALID；
 * - userinfo 响应无 picture 字段；
 * - 授权确认必须走 POST /oauth2/doConfirm（授权页修复前）；authorize 不传 state。
 */

export type {
  QraftAccount,
  QraftErrorCode,
  QraftLoginResult,
  QraftPointsBalance,
  QraftStatus,
} from '../../shared/ipc';
import type { QraftAccount } from '../../shared/ipc';

export type QraftEnv = 'test' | 'prod';

/** 每个环境默认接入配置。client_secret 不写入仓库：经环境变量注入或用户在设置页填写。 */
export interface QraftEnvConfig {
  /** API 基础地址，如 https://test.forge.miqroera.com/api */
  baseUrl: string;
  clientId: string;
}

export const QRAFT_ENV_DEFAULTS: Record<QraftEnv, QraftEnvConfig> = {
  test: {
    baseUrl: 'https://test.forge.miqroera.com/api',
    clientId: 'miqi',
  },
  prod: {
    baseUrl: 'https://forge.miqroera.com/api',
    clientId: 'miqi',
  },
};

/**
 * 测试环境 client_secret。当前处于测试阶段，开箱即用优先，默认值硬编码；
 * 可经 QRAFT_TEST_CLIENT_SECRET 环境变量覆盖（转正式环境接入前应移除默认值）。
 */
export function testEnvClientSecret(): string {
  return process.env.QRAFT_TEST_CLIENT_SECRET?.trim() || 'miqi123456';
}

/**
 * 生产环境 client_secret。测试阶段同样提供硬编码默认值（开箱即用），
 * 可经 QRAFT_PROD_CLIENT_SECRET 环境变量覆盖（转正式环境接入前应移除默认值）。
 * 注意：生产环境仍必须填写在平台注册的 redirect_uri（见 service.validateConfig）。
 */
export function prodEnvClientSecret(): string {
  return process.env.QRAFT_PROD_CLIENT_SECRET?.trim() || 'miqi123456';
}

/** 登录请求携带的可选覆盖项（设置页"高级设置"）。 */
export interface QraftLoginOptions {
  env?: QraftEnv;
  baseUrl?: string;
  clientId?: string;
  clientSecret?: string;
  /** OAuth 回调地址；测试环境自动生成 loopback 地址，生产环境必须填注册值。 */
  redirectUri?: string;
}

/** 换 token / 刷新后落盘保存的凭据。 */
export interface QraftTokens {
  accessToken: string;
  refreshToken: string;
  openid: string;
  idToken?: string;
  /** access_token 到期时间（epoch 毫秒），按实测 expires_in=7199 计算。 */
  expiresAt: number;
}

/** 持久化的完整登录态（加密落盘）。 */
export interface QraftStoredState {
  version: 1;
  env: QraftEnv;
  baseUrl: string;
  clientId: string;
  clientSecret: string;
  redirectUri: string;
  /** 平台登录态 cookie（服务端下发 Set-Cookie: Authorization=<uuid>）；浏览器登录路径为空。 */
  cookie: string;
  account: QraftAccount;
  tokens: QraftTokens;
  /**
   * 平台 AI 网关信息（userinfo 下发）。encryptedApiKey 属密钥：只存在于
   * safeStorage 加密的 store 与 0600 的 token 文件中，绝不进渲染进程/日志。
   * token 刷新不重拉 userinfo，故随本存储带入并在重写 token 文件时保留。
   */
  aiGateway?: QraftAiGateway;
}

/** 平台 AI 网关开通信息（腾讯云消费者密钥 + 状态 + 配置版本）。 */
export interface QraftAiGateway {
  /** 网关消费者密钥（X-Api-Key）。 */
  encryptedApiKey: string;
  /** 网关开通状态：active / provisioning / failed / disabled 等。 */
  status: string;
  /** 平台配置版本号（可热刷本地模型/网关清单，留后续）。 */
  configVersion?: number;
  consumerId?: string;
  consumerGroupId?: string;
}
