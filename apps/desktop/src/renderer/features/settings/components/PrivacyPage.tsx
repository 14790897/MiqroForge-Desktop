import { useEffect, useState } from 'react';
import { Check, Languages } from 'lucide-react';
import { cn } from '../../../lib/utils';
import {
  PRIVACY_TEXTS,
  PRIVACY_VERSION,
  detectPrivacyLanguage,
  isConsentCurrent,
  readStoredConsent,
  type PrivacyLanguage,
} from '../../../lib/privacy';

/**
 * 设置 → 隐私协议 (#837)：随时查阅当前版本协议。
 * 文本与 NSIS 安装器协议页、首次启动确认门共用同一份源文件。
 */
export function PrivacyPage() {
  const [language, setLanguage] = useState<PrivacyLanguage>(() => detectPrivacyLanguage());
  const [consented, setConsented] = useState(false);

  useEffect(() => {
    setConsented(isConsentCurrent(readStoredConsent()));
  }, []);

  return (
    <div className="flex h-full flex-col" data-testid="settings-privacy-page">
      <div className="flex items-center justify-between gap-3 px-6 pb-4 pt-5">
        <div className="min-w-0">
          <h3 className="text-subheading flex items-center gap-2 text-[var(--text)]">
            {language === 'zh-CN' ? '隐私协议' : 'Privacy Agreement'}
            <span className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-2 py-0.5 text-caption font-normal text-[var(--text-muted)]">
              {language === 'zh-CN' ? '版本 ' : 'v'}
              {PRIVACY_VERSION}
            </span>
            {consented ? (
              <span className="flex items-center gap-1 rounded-full border border-[var(--accent)]/40 bg-[var(--accent-soft)] px-2 py-0.5 text-caption font-normal text-[var(--accent)]">
                <Check size={11} />
                {language === 'zh-CN' ? '已同意' : 'Accepted'}
              </span>
            ) : (
              <span className="flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-0.5 text-caption font-normal text-[var(--text-muted)]">
                {language === 'zh-CN' ? '未同意' : 'Not accepted'}
              </span>
            )}
          </h3>
          <p className="mt-1 text-xs text-[var(--text-faint)]">
            {language === 'zh-CN'
              ? '本协议同时用于安装流程与首次启动确认；协议更新后再次启动时需重新确认。'
              : 'This agreement also applies to the installer and first-launch consent; re-confirmation is required after updates.'}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)]/60 p-0.5">
          {(['zh-CN', 'en-US'] as const).map((lang) => (
            <button
              key={lang}
              onClick={() => setLanguage(lang)}
              aria-pressed={language === lang}
              className={cn(
                'rounded-md px-2 py-1 text-xs font-medium transition-colors',
                language === lang
                  ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--text)]'
              )}
            >
              {lang === 'zh-CN' ? '中文' : 'English'}
            </button>
          ))}
          <Languages size={12} className="mr-1 text-[var(--text-faint)]" />
        </div>
      </div>

      <div className="mx-6 mb-6 flex-1 overflow-y-auto rounded-xl border border-[var(--border-subtle)] bg-[var(--surface)] px-5 py-4">
        <pre
          className="whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed text-[var(--text)]"
          data-testid="settings-privacy-text"
        >
          {PRIVACY_TEXTS[language]}
        </pre>
      </div>
    </div>
  );
}
