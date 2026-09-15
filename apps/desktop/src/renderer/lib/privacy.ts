/**
 * 法律文件 — 版本、同意状态持久化与文本资源。
 *
 * 文本入库于 src/renderer/assets/legal/*.zh-CN.md（律师定稿，生效 2026-09-28），
 * 渲染层经 Vite ?raw 直接内联；NSIS 安装器经 scripts/sync-legal.mjs 复制为
 * build/license_*.txt（去 Markdown 标记），electron-builder 据此生成协议页。
 *
 * 法律文本仅中文：律师出具的中文版为唯一权威版本，界面不再提供中英切换。
 */
import termsZh from '../assets/legal/terms.zh-CN.md?raw';
import privacyZh from '../assets/legal/privacy.zh-CN.md?raw';
import privacySummaryZh from '../assets/legal/privacy-summary.zh-CN.md?raw';
import dataCollectionZh from '../assets/legal/data-collection.zh-CN.md?raw';
import dataSharingZh from '../assets/legal/data-sharing.zh-CN.md?raw';

/** 当前协议版本。协议内容有实质变更时递增，已同意的用户会重新看到确认页。 */
export const PRIVACY_VERSION = '2.0';

/** 同意状态在 localStorage 中的键（值为已同意的协议版本）。 */
export const PRIVACY_CONSENT_KEY = 'miqi:privacyConsentVersion';

export type LegalDocumentId =
  'terms' | 'privacy' | 'privacy-summary' | 'data-collection' | 'data-sharing';

export interface LegalDocument {
  id: LegalDocumentId;
  /** 侧边目录中的名称（与律师设计稿一致）。 */
  title: string;
  /** Markdown 正文，与安装器协议页、首次启动确认门共用同一份源文件。 */
  text: string;
}

/** 设置 → 法律文件的目录顺序（律师设计稿顺序）。 */
export const LEGAL_DOCUMENTS: LegalDocument[] = [
  { id: 'terms', title: '《用户协议》', text: termsZh },
  { id: 'privacy', title: '《隐私政策》', text: privacyZh },
  { id: 'privacy-summary', title: '《隐私政策摘要》', text: privacySummaryZh },
  { id: 'data-collection', title: '《个人信息收集清单》', text: dataCollectionZh },
  { id: 'data-sharing', title: '《个人信息对外提供清单及第三方服务清单》', text: dataSharingZh },
];

export function getLegalDocument(id: LegalDocumentId): LegalDocument {
  const doc = LEGAL_DOCUMENTS.find((d) => d.id === id);
  if (!doc) throw new Error(`unknown legal document: ${id}`);
  return doc;
}

/**
 * 首次启动弹窗的《温馨提示》(律师定稿 2026-09-11)。
 *
 * {{privacy}} / {{terms}} 是设计稿「【请插入超链接】」占位处，渲染为可点开
 * 对应全文的链接；除占位符外与律师原文逐字一致（单字符串常量，不受 JSX
 * 换行折叠与 prettier 重排影响）。
 */
export const CONSENT_NOTICE_TEXT =
  '欢迎您使用MiQroForge DeskTop！我们非常重视用户个人信息保护并遵守相关法律法规的要求，我们依据相关法律法规制定了{{privacy}}(以下简称“隐私政策”)和{{terms}}，请您在使用我们的产品前仔细阅读并充分理解相关条款，以充分了解您的权利与义务。请您务必仔细阅读、充分理解隐私政策中的各条款，尤其是以加粗进行标识的重要条款或个人信息。我们严格遵循最小必要原则，在法律规定的必要信息范围内及与实现业务相关联的个人信息范围内处理个人信息。若您点击“同意”，仅代表您同意已了解应用提供的基本功能及所需的必要个人信息，不代表您同意我们收集、处理非必要个人信息。';

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

/** 记录同意（localStorage 缓存 + 主进程权威存储）。 */
export function recordConsent(
  storage?: Pick<Storage, 'setItem'> | null,
  version: string = PRIVACY_VERSION
): void {
  writeConsentCache(version, storage);
  const bridge = resolvePrivacyBridge();
  try {
    void bridge?.setConsent(version)?.catch?.(() => {
      /* 主进程不可用：本次已在缓存/内存生效 */
    });
  } catch {
    /* 桥接同步异常：同上 */
  }
}

/** 只写 localStorage 缓存（权威值回填用，不再回写主进程）。 */
function writeConsentCache(version: string, storage?: Pick<Storage, 'setItem'> | null): void {
  const s = storage ?? resolveLocalStorage();
  if (!s) return;
  try {
    s.setItem(PRIVACY_CONSENT_KEY, version);
  } catch {
    /* storage unavailable — consent applies for this session only */
  }
}

/** 主进程权威存储的读取结果：read=false 表示主进程不可用，而非「未同意」。 */
export interface DurableConsent {
  read: boolean;
  version: string | null;
}

/** 主进程权威存储中的同意版本（preload 在页面脚本之前同步读到）。 */
export function readDurableConsent(): DurableConsent {
  const bridge = resolvePrivacyBridge();
  const value = bridge?.initialConsent;
  if (value && typeof value.read === 'boolean') {
    return {
      read: value.read,
      version: typeof value.version === 'string' && value.version ? value.version : null,
    };
  }
  return { read: false, version: null };
}

/**
 * 解析当前同意版本（#1071）：主进程存储是权威来源——读取成功时以它为准
 * （包括「无记录」的 null），localStorage 仅在主进程不可用时兜底；权威版本
 * 回填缓存以便下次走快路径。缓存不会覆盖权威结论：过期缓存（例如权威记录
 * 已被清除）不能放行未经同意的启动（CodeRabbit 评审）。
 */
export function readConsentVersion(): string | null {
  const durable = readDurableConsent();
  if (durable.read) {
    if (durable.version) writeConsentCache(durable.version);
    return durable.version;
  }
  return readStoredConsent();
}

/** 清除同意记录（localStorage 缓存 + 主进程存储）。 */
export function clearConsent(): void {
  const s = resolveLocalStorage();
  if (s) {
    try {
      s.removeItem(PRIVACY_CONSENT_KEY);
    } catch {
      /* ignore */
    }
  }
  const bridge = resolvePrivacyBridge();
  try {
    void bridge?.setConsent(null)?.catch?.(() => {
      /* ignore */
    });
  } catch {
    /* ignore */
  }
}

/** 取 preload 暴露的隐私桥接；非 Electron 环境（单测/浏览器）返回 null。 */
function resolvePrivacyBridge(): Window['miqi']['privacy'] | null {
  try {
    if (typeof window === 'undefined') return null;
    return window.miqi?.privacy ?? null;
  } catch {
    return null;
  }
}
