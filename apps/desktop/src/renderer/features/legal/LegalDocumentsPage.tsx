import { useEffect, useState } from 'react';
import { Check } from 'lucide-react';
import { cn } from '../../lib/utils';
import {
  LEGAL_DOCUMENTS,
  PRIVACY_VERSION,
  getLegalDocument,
  isConsentCurrent,
  readConsentVersion,
  type LegalDocumentId,
} from '../../lib/privacy';
import { LegalDocContent } from './LegalDocContent';

/**
 * 设置 → 法律文件（律师设计稿 2026-09-11）：左侧目录列出五份法律文件，
 * 右侧展示所选文件正文。文本与安装器协议页、首次启动确认门共用同一份源文件。
 */
export function LegalDocumentsPage() {
  const [activeId, setActiveId] = useState<LegalDocumentId>('terms');
  const [consented, setConsented] = useState(false);

  useEffect(() => {
    // 与 AppShell 同一判定来源（缓存 + 主进程权威存储），避免缓存丢失时
    // 应用已放行而徽标显示「未同意」（CodeRabbit 评审）。
    setConsented(isConsentCurrent(readConsentVersion()));
  }, []);

  const doc = getLegalDocument(activeId);

  return (
    <div className="flex h-full min-h-0" data-testid="settings-legal-page">
      <nav
        className="w-56 shrink-0 overflow-y-auto border-r border-[var(--border-subtle)] px-2 py-4"
        aria-label="法律文件目录"
      >
        <ul className="flex flex-col gap-0.5">
          {LEGAL_DOCUMENTS.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => setActiveId(item.id)}
                aria-current={activeId === item.id ? 'true' : undefined}
                data-testid={`legal-nav-${item.id}`}
                className={cn(
                  'w-full rounded-lg px-2.5 py-1.5 text-left text-[13px] leading-snug transition-colors',
                  activeId === item.id
                    ? 'bg-[var(--accent-soft)] font-medium text-[var(--accent)]'
                    : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]'
                )}
              >
                {item.title}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-6 pb-3 pt-5">
          <div className="min-w-0">
            <h3 className="text-subheading flex items-center gap-2 text-[var(--text)]">
              法律文件
              <span
                className="rounded-full border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-2 py-0.5 text-caption font-normal text-[var(--text-muted)]"
                data-testid="legal-version-badge"
              >
                版本 {PRIVACY_VERSION}
              </span>
              {consented ? (
                <span className="flex items-center gap-1 rounded-full border border-[var(--accent)]/40 bg-[var(--accent-soft)] px-2 py-0.5 text-caption font-normal text-[var(--accent)]">
                  <Check size={11} />
                  已同意
                </span>
              ) : (
                <span className="flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-2 py-0.5 text-caption font-normal text-[var(--text-muted)]">
                  未同意
                </span>
              )}
            </h3>
            <p className="mt-1 text-xs text-[var(--text-faint)]">
              本文件同时用于安装流程与首次启动确认；协议更新后再次启动时需重新确认。
            </p>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          <LegalDocContent text={doc.text} testId="legal-doc-content" />
        </div>
      </div>
    </div>
  );
}
