/**
 * i18n bootstrap for the MiQroForge Desktop renderer (i18next + react-i18next).
 *
 * Languages: zh-CN (default, canonical copy) and en. The stored choice is kept
 * in localStorage under `miqi-language`, mirroring the other UI preferences in
 * lib/uiPreferences. Runtime switching re-renders via useTranslation/`t`.
 *
 * The pilot deliberately keeps zh-CN texts byte-identical to the previous
 * hardcoded strings (E2E specs assert on them) — see issue #986.
 */
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import { zhCN } from './locales/zh-CN';
import { en } from './locales/en';

export const LANGUAGE_KEY = 'miqi-language';
export const SUPPORTED_LANGUAGES = ['zh-CN', 'en'] as const;
export type Language = (typeof SUPPORTED_LANGUAGES)[number];

const DEFAULT_LANGUAGE: Language = 'zh-CN';

function readStoredLanguage(): Language {
  try {
    const raw = localStorage.getItem(LANGUAGE_KEY);
    return (SUPPORTED_LANGUAGES as readonly string[]).includes(raw ?? '')
      ? (raw as Language)
      : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

/** Stored language (falls back to zh-CN when storage is unavailable). */
export const getStoredLanguage = readStoredLanguage;

i18n.use(initReactI18next).init({
  resources: {
    'zh-CN': { translation: zhCN },
    en: { translation: en },
  },
  lng: readStoredLanguage(),
  fallbackLng: 'zh-CN',
  interpolation: {
    escapeValue: false, // React already protects against XSS in JSX
  },
  // Inline resources → init synchronously (i18next v25+ default), so the module
  // is safe to import in unit tests (node env) before the first render.
});

if (typeof document !== 'undefined') {
  document.documentElement.lang = i18n.language;
}

/** Switch the UI language and persist the choice (returns when applied). */
export function changeUILanguage(language: Language): Promise<unknown> {
  try {
    localStorage.setItem(LANGUAGE_KEY, language);
  } catch {
    // storage unavailable (tests / hardened context) — in-memory switch only
  }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = language;
  }
  return i18n.changeLanguage(language);
}

export { i18n };
export default i18n;
