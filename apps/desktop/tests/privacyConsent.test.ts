/**
 * 隐私协议同意状态逻辑单元测试 (#837)。
 * lib/privacy.ts 的纯逻辑：语言探测、同意版本比对、localStorage 持久化。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  PRIVACY_VERSION,
  PRIVACY_CONSENT_KEY,
  PRIVACY_TEXTS,
  detectPrivacyLanguage,
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
  };
};

describe('detectPrivacyLanguage', () => {
  it('中文环境（含简体/繁体/区域变体）返回 zh-CN', () => {
    expect(detectPrivacyLanguage('zh-CN')).toBe('zh-CN');
    expect(detectPrivacyLanguage('zh-TW')).toBe('zh-CN');
    expect(detectPrivacyLanguage('zh')).toBe('zh-CN');
    expect(detectPrivacyLanguage('ZH-HANS-CN')).toBe('zh-CN');
  });

  it('非中文环境默认英文', () => {
    expect(detectPrivacyLanguage('en-US')).toBe('en-US');
    expect(detectPrivacyLanguage('ja-JP')).toBe('en-US');
    expect(detectPrivacyLanguage('')).toBe('en-US');
  });
});

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
    storage.setItem(PRIVACY_CONSENT_KEY, '0.9');
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

describe('PRIVACY_TEXTS', () => {
  it('中英文文本均非空且包含协议标题', () => {
    expect(PRIVACY_TEXTS['zh-CN']).toContain('隐私协议');
    expect(PRIVACY_TEXTS['en-US']).toContain('Privacy Agreement');
  });
});
