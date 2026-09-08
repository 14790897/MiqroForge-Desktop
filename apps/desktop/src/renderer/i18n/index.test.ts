import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import i18n, { changeUILanguage, getStoredLanguage, LANGUAGE_KEY } from './index';
import { zhCN } from './locales/zh-CN';
import { en } from './locales/en';

function memoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: (i: number) => [...store.keys()][i] ?? null,
    get length() {
      return store.size;
    },
  } as Storage;
}

describe('i18n resources', () => {
  it('en has exactly the same keys as zh-CN (translation parity)', () => {
    const zhKeys = Object.keys(zhCN).sort();
    const enKeys = Object.keys(en).sort();
    expect(enKeys).toEqual(zhKeys);
    expect(enKeys.length).toBeGreaterThan(50);
  });

  it('no empty or placeholder-looking zh values', () => {
    for (const [k, v] of Object.entries(zhCN)) {
      expect(v.trim().length, `zh value empty for ${k}`).toBeGreaterThan(0);
    }
  });
});

describe('language switching', () => {
  beforeEach(async () => {
    vi.unstubAllGlobals();
    await changeUILanguage('zh-CN');
  });
  afterEach(() => vi.unstubAllGlobals());

  it('defaults to zh-CN and renders the canonical zh copy', () => {
    expect(i18n.language).toBe('zh-CN');
    expect(i18n.t('common.cancel')).toBe('取消');
    expect(i18n.t('topbar.runtime.offline')).toBe('离线');
  });

  it('switches to en and back', async () => {
    expect(i18n.t('feedback.submit')).toBe('提交反馈');
    await changeUILanguage('en');
    expect(i18n.t('feedback.submit')).toBe('Submit feedback');
    expect(i18n.t('common.cancel')).toBe('Cancel');
    await changeUILanguage('zh-CN');
    expect(i18n.t('feedback.submit')).toBe('提交反馈');
  });

  it('interpolates parameters and plural keys per language', async () => {
    expect(i18n.t('feedback.errPartial', { count: 2, max: 5 })).toBe(
      '仅添加了前 2 张，已达 5 张上限'
    );
    expect(i18n.t('relative.minutesAgo', { count: 1 })).toBe('1 分钟前');
    await changeUILanguage('en');
    expect(i18n.t('feedback.errPartial', { count: 2, max: 5 })).toBe(
      'Only the first 2 were added — 5 max'
    );
    expect(i18n.t('relative.minutesAgo', { count: 1 })).toBe('1 minute ago');
    expect(i18n.t('relative.minutesAgo', { count: 5 })).toBe('5 minutes ago');
  });

  it('persists the choice and restores it', () => {
    vi.stubGlobal('localStorage', memoryStorage());
    void changeUILanguage('en');
    expect(localStorage.getItem(LANGUAGE_KEY)).toBe('en');
    expect(getStoredLanguage()).toBe('en');
  });

  it('falls back to zh-CN when the stored value is unknown', () => {
    vi.stubGlobal('localStorage', memoryStorage());
    localStorage.setItem(LANGUAGE_KEY, 'fr');
    expect(getStoredLanguage()).toBe('zh-CN');
  });
});
