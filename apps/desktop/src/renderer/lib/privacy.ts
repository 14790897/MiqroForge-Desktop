/**
 * 隐私协议 (#837) — 版本、同意状态持久化与文本资源。
 *
 * 协议文本的规范来源是 src/renderer/assets/legal/privacy.{zh-CN,en-US}.txt
 * （入库版本化）。三处消费方共用同一份文本，避免多份拷贝漂移：
 *   - 渲染层经 Vite ?raw 直接内联（本文件）；
 *   - NSIS 安装器经 scripts/sync-legal.mjs 复制为 build/license_<语言>.txt，
 *     electron-builder 自动生成按安装语言匹配的协议页（拒绝即终止安装）。
 */
import privacyZh from '../assets/legal/privacy.zh-CN.txt?raw';
import privacyEn from '../assets/legal/privacy.en-US.txt?raw';

/** 当前协议版本。协议内容有实质变更时递增，已同意的用户会重新看到确认页。 */
export const PRIVACY_VERSION = '1.0';

/** 同意状态在 localStorage 中的键（值为已同意的协议版本）。 */
export const PRIVACY_CONSENT_KEY = 'miqi:privacyConsentVersion';

export type PrivacyLanguage = 'zh-CN' | 'en-US';

export const PRIVACY_TEXTS: Record<PrivacyLanguage, string> = {
  'zh-CN': privacyZh,
  'en-US': privacyEn,
};

/** 按浏览器语言选择协议语言：中文环境用中文，其余默认英文。 */
export function detectPrivacyLanguage(language?: string): PrivacyLanguage {
  const lang = (
    language ?? (typeof navigator !== 'undefined' ? navigator.language : '')
  ).toLowerCase();
  return lang.startsWith('zh') ? 'zh-CN' : 'en-US';
}

/** 解析全局 localStorage；存储被禁用时 getter 本身会抛 SecurityError，一并吞掉。 */
function resolveLocalStorage(): Storage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null;
  }
}

/** 读取已持久化的同意版本；存储不可用或未同意时返回 null。 */
export function readStoredConsent(storage?: Pick<Storage, 'getItem'> | null): string | null {
  const s = storage ?? resolveLocalStorage();
  if (!s) return null;
  try {
    return s.getItem(PRIVACY_CONSENT_KEY);
  } catch {
    return null;
  }
}

/** 已同意的版本是否与当前协议版本一致（一致则无需再次确认）。 */
export function isConsentCurrent(
  stored: string | null,
  version: string = PRIVACY_VERSION
): boolean {
  return stored === version;
}

/** 记录同意（持久化到 localStorage）。 */
export function recordConsent(
  storage?: Pick<Storage, 'setItem'> | null,
  version: string = PRIVACY_VERSION
): void {
  const s = storage ?? resolveLocalStorage();
  if (!s) return;
  try {
    s.setItem(PRIVACY_CONSENT_KEY, version);
  } catch {
    /* storage unavailable — consent applies for this session only */
  }
}
