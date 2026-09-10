/**
 * Qraft 登录错误码 → 用户修复指引（issue #726）。
 *
 * 从 QraftPage 抽出共享（issue #1000）：首屏登录卡片、未登录拦截引导
 * 与设置页共用同一份错误文案，避免多处拷贝漂移。
 */
import type { QraftErrorCode, QraftLoginResult } from '../../shared/ipc';

/** 各错误码对应的修复指引（服务端 message 优先展示，这里只兜底）。 */
export const ERROR_GUIDANCE: Partial<Record<QraftErrorCode, string>> = {
  IP_NOT_WHITELISTED:
    '出口 IP 未加白：本机出口 IP 不在 MiQroForge 平台白名单内，请联系 MiQroForge 管理员加白后重试。',
  NETWORK_UNREACHABLE:
    '网络请求失败（多次重试后仍超时）。请检查网络连接后重试；如持续失败可能是出口线路抖动。',
  PUBLIC_KEY_EXTRACT_FAILED:
    '无法从 MiQroForge 登录页前端 bundle 提取 RSA 公钥。请确认 MiQroForge 基础地址正确、当前网络可访问登录页。',
  SESSION_EXPIRED: 'MiQroForge 登录态已失效，请重新登录。',
  AUTHORIZE_FAILED: '授权流程失败。可尝试退出后重新登录；如反复出现请查看日志排查。',
  TOKEN_EXCHANGE_FAILED: '换取 token 失败。可尝试重新登录；如反复出现请查看日志排查。',
  REFRESH_FAILED: 'token 刷新失败，登录已过期，请重新登录。',
  REFRESH_TOKEN_INVALID:
    'refresh_token 已失效（平台升级或安全策略变更可能导致登录态作废），请重新登录。',
  USERINFO_FAILED: '获取用户信息失败（不影响已登录状态）。',
  LOGIN_CANCELLED: '已取消：登录窗口在完成授权前被关闭。',
  BROWSER_LOGIN_FAILED:
    '浏览器登录失败：无法打开 MiQroForge 登录页或等待授权超时，请检查网络后重试。',
  INVALID_CONFIG: '接入配置不完整或非法，请检查高级设置中的 client_secret 等项。',
  INTERNAL: '发生未知错误，请查看日志排查。',
};

/** 登录结果 → 用户可读的错误文案（服务端 message 优先，code 兜底，再兜底 fallback）。 */
export function qraftErrorText(result: QraftLoginResult | null, fallback: string): string {
  if (!result) return fallback;
  if (result.message) return result.message;
  return ERROR_GUIDANCE[result.code ?? 'INTERNAL'] ?? fallback;
}
