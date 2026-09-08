/**
 * Frontend mirror of _sanitize_exc_for_ui() in miqi/execution/orchestrator.py.
 *
 * Applies the same sanitization to error messages before they are displayed in
 * the renderer UI: truncate at 300 chars, strip absolute paths, URLs, and long
 * Base64-like tokens.  The server-side logger retains the full exception; this
 * function is only for user-visible error text.
 */

/** Maximum length for a user-visible error message. */
const MAX_LEN = 300;

/**
 * Matches http(s) URLs.
 * Applied BEFORE the path regex so URLs are replaced as a whole unit,
 * rather than having their path segments fragmented into [path] markers.
 */
const RE_URL = /https?:\/\/[^\s"'<>]{1,200}/g;

/**
 * Matches Unix / Windows absolute paths.
 * E.g. /home/user/file.py, C:\Users\test\data.json, \\?\C:\foo\bar
 *
 * Applied AFTER the URL regex so we never see path segments inside URLs.
 *
 * Three alternatives (longest-first so UNC wins over drive-letter):
 *   1) UNC:  \\?\X:\dir\...\file
 *   2) Windows drive:  X:\dir\...\file
 *   3) Unix absolute:  /dir/.../file — 负向后顾要求斜杠前不是单词字符，
 *      避免把 deepseek/deepseek-v4-flash 这类 provider/model id 误当路径
 *      打码成 [path]（实测：保存网关模型报错被显示成 deepseek[path]）。
 */
const RE_PATH =
  /(?:\\\\\?\\[A-Za-z]:[\\/](?:[^\s"'<>|:]+[\\/])*[^\s"'<>|:]+)|(?:[A-Za-z]:[\\/](?:[^\s"'<>|:]+[\\/])*[^\s"'<>|:]+)|(?:(?<![A-Za-z0-9_.-])\/(?:[^\s"'<>|:]+[\/])*[^\s"'<>|:]+)/g;

/** Matches long Base64-like tokens (40+ contiguous base64 chars). */
const RE_TOKEN = /\b[A-Za-z0-9+/=]{40,}\b/g;

export function sanitizeUiMessage(raw: string): string {
  if (!raw) return '';

  let s = raw;
  const lower = s.toLowerCase();
  if (lower.includes('turn is already in progress') || lower.includes('turn_in_progress')) {
    return '上一个任务还在进行中，请稍候片刻或新开一个会话。';
  }
  // Missing/invalid provider credential — must be checked BEFORE the bridge
  // checks: Electron wraps every chat:send failure as "Error invoking remote
  // method 'chat:send': …", so the real cause would otherwise be swallowed and
  // misreported as a runtime problem (#617).
  if (
    lower.includes('no api key') ||
    lower.includes('no_api_key') ||
    lower.includes('api key not configured')
  ) {
    return '未配置 API Key，请前往 设置 > 模型 配置后再试。';
  }
  // Only treat genuine bridge-down signals as "runtime not started". The
  // generic "error invoking remote method 'chat:send'" prefix appears on ANY
  // chat:send failure (provider errors, rate limits, …) and must not be used
  // as a bridge-down signal by itself (#617).
  if (
    lower.includes('bridge not running') ||
    lower.includes('bridge is not running') ||
    lower.includes('bridge process exited') ||
    lower.includes('bridge process error') ||
    lower.includes('bridge initialization failed') ||
    lower.includes('bridge stopped') ||
    lower.includes('bridge restarted')
  ) {
    return '运行时未启动或正在重启，请稍后再试。';
  }
  if (lower.includes('provider test failed')) {
    return '连接测试失败，请检查 API Key、API Base、模型名称或网络。';
  }
  if (lower.includes('connection error')) {
    return '连接模型服务失败，请检查网络或 API Base。';
  }
  if (lower.includes('request') && lower.includes('timed out')) {
    return '请求超时，请稍后重试。';
  }
  // 1) Truncate first (same order as Python _sanitize_exc_for_ui)
  if (s.length > MAX_LEN) {
    s = s.slice(0, MAX_LEN) + '…'; // horizontal ellipsis (one char)
  }
  // 2) Strip URLs before paths (avoids path regex fragmenting URL segments)
  s = s.replace(RE_URL, '[url]');
  // 3) Strip paths
  s = s.replace(RE_PATH, '[path]');
  // 4) Strip long tokens
  s = s.replace(RE_TOKEN, '[token]');

  return s;
}
