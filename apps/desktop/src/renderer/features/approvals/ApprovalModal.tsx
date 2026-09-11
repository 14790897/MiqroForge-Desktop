import { Shield, Terminal, FileText, AlertTriangle, X, Clock } from 'lucide-react';
import { useApproval } from '../../contexts/ApprovalContext';
import { getApprovalDisplay, getApprovalTitle } from './approvalDisplayUtils';

/**
 * Choose an icon based on approval category.
 */
function ApprovalIcon({ category }: { category?: string }) {
  if (category === 'exec')
    return <Terminal size={13} className="text-[var(--text-faint)] mt-1 shrink-0" />;
  if (category === 'file_write')
    return <FileText size={13} className="text-[var(--text-faint)] mt-1 shrink-0" />;
  return <AlertTriangle size={13} className="text-[var(--text-faint)] mt-1 shrink-0" />;
}

export function ApprovalModal() {
  const { pending, resolve, timeout, remainingSeconds } = useApproval();
  if (!pending) return null;

  const pct = remainingSeconds != null ? (remainingSeconds / timeout) * 100 : 100;
  const isLow = remainingSeconds != null && remainingSeconds <= 5;
  const displayText = getApprovalDisplay(pending);
  const title = getApprovalTitle(pending.category);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center pb-8 px-4 pointer-events-none">
      <div
        className="pointer-events-auto w-full max-w-[600px] bg-[var(--surface-elevated)] border border-[var(--danger)] rounded-xl shadow-2xl overflow-hidden"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="approval-title"
      >
        {/* Header */}
        <div className="flex items-center gap-2 px-5 py-3 bg-[color-mix(in_srgb,var(--danger)_12%,transparent)] border-b border-[var(--danger)]">
          <Shield size={16} className="text-[var(--danger)] shrink-0" />
          <span
            id="approval-title"
            className="text-sm font-semibold text-[var(--danger)]"
            data-testid="approval-title"
          >
            {title}
          </span>
          <span className="ml-2 text-xs text-[var(--text-muted)] font-normal">
            {pending.description}
          </span>
          <button
            onClick={() => resolve('deny')}
            className="ml-auto text-[var(--text-faint)] hover:text-[var(--text)] transition-colors"
            title="拒绝"
          >
            <X size={14} />
          </button>
        </div>

        {/* Countdown bar */}
        {remainingSeconds != null && (
          <div className="px-5 pt-3">
            <div className="flex items-center gap-2 mb-1.5">
              <Clock
                size={11}
                className={isLow ? 'text-[var(--danger)]' : 'text-[var(--text-faint)]'}
              />
              <span
                className={`text-xs font-mono tabular-nums ${isLow ? 'text-[var(--danger)] font-semibold' : 'text-[var(--text-muted)]'}`}
              >
                {remainingSeconds > 0 ? `${remainingSeconds}秒` : '已超时'}
              </span>
            </div>
            <div className="h-1.5 bg-[var(--surface-muted)] rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-200 ${
                  isLow ? 'bg-[var(--danger)]' : 'bg-[var(--warning)]'
                }`}
                style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
              />
            </div>
          </div>
        )}

        {/* Command / Action display */}
        <div className="px-5 py-3">
          <div className="flex items-start gap-2">
            <ApprovalIcon category={pending.category} />
            <pre className="text-xs font-mono text-[var(--text)] bg-[var(--surface-muted)] rounded-lg px-3 py-2 flex-1 overflow-x-auto whitespace-pre-wrap break-all">
              {displayText}
            </pre>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 px-5 py-3 border-t border-[var(--border-subtle)]">
          <span className="text-xs text-[var(--text-faint)] flex-1">选择如何处理此命令：</span>
          <button
            onClick={() => resolve('deny')}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[color-mix(in_srgb,var(--danger)_15%,transparent)] text-[var(--danger)] hover:bg-[color-mix(in_srgb,var(--danger)_25%,transparent)] transition-colors"
          >
            拒绝
          </button>
          <button
            onClick={() => resolve('once')}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--surface-muted)] text-[var(--text)] hover:bg-[var(--border-subtle)] transition-colors"
          >
            允许一次
          </button>
          <button
            onClick={() => resolve('session')}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--surface-muted)] text-[var(--text)] hover:bg-[var(--border-subtle)] transition-colors"
          >
            本次会话允许
          </button>
          {pending.allow_permanent && (
            <button
              onClick={() => resolve('always')}
              className="px-3 py-1.5 rounded-lg text-sm font-medium bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)] transition-colors"
              data-testid="approval-allow-permanent"
            >
              永久允许
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
