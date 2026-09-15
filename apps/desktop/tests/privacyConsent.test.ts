/**
 * 法律文件同意状态逻辑单元测试 (#837 / #1068)。
 * lib/privacy.ts 的纯逻辑：同意版本比对、localStorage 持久化、法律文件目录。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  PRIVACY_VERSION,
  PRIVACY_CONSENT_KEY,
  LEGAL_DOCUMENTS,
  CONSENT_NOTICE_TEXT,
  clearConsent,
  getLegalDocument,
  readConsentVersion,
  readDurableConsent,
  readStoredConsent,
  isConsentCurrent,
  recordConsent,
} from '../src/renderer/lib/privacy';

const makeStorage = (entries: Record<string, string> = {}) => {
  const store = new Map(Object.entries(entries));
  return {
    store,
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value);
    },
    removeItem: (key: string) => {
      store.delete(key);
    },
  };
};

/** 桩：preload 暴露的 privacy 桥接（记录 setConsent 调用）。 */
const makePrivacyBridge = (initialConsentVersion: string | null = null) => {
  const calls: Array<string | null> = [];
  return {
    calls,
    bridge: {
      privacy: {
        initialConsentVersion,
        setConsent: async (version: string | null) => {
          calls.push(version);
          return { ok: true };
        },
      },
    },
  };
};

describe('readStoredConsent / isConsentCurrent / recordConsent', () => {
  const storage = makeStorage();
  beforeEach(() => storage.store.clear());
  afterEach(() => vi.unstubAllGlobals());

  it('未同意（无存储或存储不可用）返回 null 且判定为需要确认', () => {
    expect(readStoredConsent(storage)).toBeNull();
    expect(readStoredConsent(null)).toBeNull();
    expect(isConsentCurrent(null)).toBe(false);
  });

  it('已同意的旧版本在协议更新后需要重新确认', () => {
    storage.setItem(PRIVACY_CONSENT_KEY, '1.0');
    expect(isConsentCurrent(readStoredConsent(storage))).toBe(false);
    expect(isConsentCurrent(readStoredConsent(storage), '2.0')).toBe(false);
  });

  it('recordConsent 写入当前版本，读回后判定为已同意', () => {
    recordConsent(storage);
    expect(readStoredConsent(storage)).toBe(PRIVACY_VERSION);
    expect(isConsentCurrent(readStoredConsent(storage))).toBe(true);
  });

  it('localStorage 抛异常时读写均安全降级', () => {
    const broken = {
      getItem: () => {
        throw new Error('denied');
      },
      setItem: () => {
        throw new Error('denied');
      },
    };
    expect(readStoredConsent(broken)).toBeNull();
    expect(() => recordConsent(broken)).not.toThrow();
  });

  it('全局 localStorage getter 本身抛 SecurityError 时安全降级（不传 storage）', () => {
    // 存储被禁用时访问 localStorage 全局即抛错（typeof 也会触发 getter）
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError: storage disabled');
      },
    });
    try {
      expect(() => readStoredConsent()).not.toThrow();
      expect(readStoredConsent()).toBeNull();
      expect(() => recordConsent()).not.toThrow();
    } finally {
      delete (globalThis as Record<string, unknown>)['localStorage'];
    }
  });

  it('global localStorage 路径可用（vi.stubGlobal）', () => {
    vi.stubGlobal('localStorage', makeStorage());
    recordConsent();
    expect(readStoredConsent()).toBe(PRIVACY_VERSION);
  });
});

describe('LEGAL_DOCUMENTS', () => {
  it('目录为律师设计稿的五份文件且顺序一致', () => {
    expect(LEGAL_DOCUMENTS.map((d) => d.id)).toEqual([
      'terms',
      'privacy',
      'privacy-summary',
      'data-collection',
      'data-sharing',
    ]);
    expect(LEGAL_DOCUMENTS.map((d) => d.title)).toEqual([
      '《用户协议》',
      '《隐私政策》',
      '《隐私政策摘要》',
      '《个人信息收集清单》',
      '《个人信息对外提供清单及第三方服务清单》',
    ]);
  });

  it('每份文本非空且包含自身标题与生效日期（中文权威版）', () => {
    for (const doc of LEGAL_DOCUMENTS) {
      expect(doc.text.length).toBeGreaterThan(200);
      // 每份文件带一级标题（部分文件前置发布/生效日期行，标题不在首行）
      expect(doc.text).toMatch(/(^|\n)# \S/);
    }
    expect(getLegalDocument('terms').text).toContain('# MiQroForge DeskTop用户服务协议');
    expect(getLegalDocument('privacy').text).toContain('# MiQroForge DeskTop隐私政策');
    expect(getLegalDocument('privacy').text).toContain('生效日期：2026年9月28日');
    expect(getLegalDocument('terms').text).toContain('生效日期：2026年9月28日');
    // 第三方清单（律师版）列出的 SDK 提供者
    expect(getLegalDocument('data-sharing').text).toContain('腾讯AI网关');
    expect(getLegalDocument('data-sharing').text).toContain('deepseek v4 pro');
  });

  it('getLegalDocument 对未知 id 抛错', () => {
    expect(() => getLegalDocument('nope' as never)).toThrow();
  });
});

describe('CONSENT_NOTICE_TEXT（首次启动弹窗提示）', () => {
  it('无遗留的「请插入超链接」占位，两个链接占位符各出现一次', () => {
    expect(CONSENT_NOTICE_TEXT).not.toContain('请插入超链接');
    expect(CONSENT_NOTICE_TEXT.match(/\{\{privacy\}\}/g)).toHaveLength(1);
    expect(CONSENT_NOTICE_TEXT.match(/\{\{terms\}\}/g)).toHaveLength(1);
  });

  it('首尾与关键条款为律师原文', () => {
    expect(CONSENT_NOTICE_TEXT.startsWith('欢迎您使用MiQroForge DeskTop！')).toBe(true);
    expect(CONSENT_NOTICE_TEXT).toContain(
      '(以下简称“隐私政策”)和{{terms}}，请您在使用我们的产品前'
    );
    expect(CONSENT_NOTICE_TEXT).toContain('我们严格遵循最小必要原则');
    expect(CONSENT_NOTICE_TEXT).toContain(
      '若您点击“同意”，仅代表您同意已了解应用提供的基本功能及所需的必要个人信息，不代表您同意我们收集、处理非必要个人信息。'
    );
  });

  it('占位符替换为文内名称后语句通顺（链接两侧不残留标记）', () => {
    const rendered = CONSENT_NOTICE_TEXT.replace('{{privacy}}', '《隐私政策》').replace(
      '{{terms}}',
      '《用户协议》'
    );
    expect(rendered).toContain('制定了《隐私政策》(以下简称“隐私政策”)和《用户协议》，请您在使用');
    expect(rendered).not.toContain('{{');
  });
});

describe('主进程权威存储（#1071，缓存丢失兜底）', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('缓存为空时回落到权威存储并回填缓存', () => {
    const storage = makeStorage();
    const { calls, bridge } = makePrivacyBridge('2.0');
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('window', { miqi: bridge });

    expect(readConsentVersion()).toBe('2.0');
    // 回填缓存（下次启动走快路径），同时把权威存储值原样写回
    expect(storage.getItem(PRIVACY_CONSENT_KEY)).toBe('2.0');
    expect(calls).toEqual(['2.0']);
  });

  it('缓存优先：有缓存时不依赖权威存储', () => {
    const storage = makeStorage({ [PRIVACY_CONSENT_KEY]: '2.0' });
    const { calls, bridge } = makePrivacyBridge('1.0');
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('window', { miqi: bridge });

    expect(readConsentVersion()).toBe('2.0');
    expect(calls).toEqual([]);
  });

  it('两处都为空时返回 null（需要确认）', () => {
    vi.stubGlobal('localStorage', makeStorage());
    const { bridge } = makePrivacyBridge(null);
    vi.stubGlobal('window', { miqi: bridge });

    expect(readConsentVersion()).toBeNull();
    expect(readDurableConsent()).toBeNull();
    expect(isConsentCurrent(readConsentVersion())).toBe(false);
  });

  it('recordConsent 同时写缓存与权威存储', () => {
    const storage = makeStorage();
    const { calls, bridge } = makePrivacyBridge(null);
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('window', { miqi: bridge });

    recordConsent();
    expect(storage.getItem(PRIVACY_CONSENT_KEY)).toBe(PRIVACY_VERSION);
    expect(calls).toEqual([PRIVACY_VERSION]);
  });

  it('clearConsent 清缓存与权威存储（撤回同意/E2E 重置）', () => {
    const storage = makeStorage({ [PRIVACY_CONSENT_KEY]: '2.0' });
    const { calls, bridge } = makePrivacyBridge('2.0');
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('window', { miqi: bridge });

    clearConsent();
    expect(storage.getItem(PRIVACY_CONSENT_KEY)).toBeNull();
    expect(calls).toEqual([null]);
  });

  it('无 window/桥接（浏览器或单测环境）时安全降级', () => {
    vi.stubGlobal('localStorage', makeStorage());
    // 不 stub window：node 环境没有 window
    expect(() => readConsentVersion()).not.toThrow();
    expect(readConsentVersion()).toBeNull();
    expect(readDurableConsent()).toBeNull();
    expect(() => recordConsent()).not.toThrow();
    expect(() => clearConsent()).not.toThrow();
  });
});
