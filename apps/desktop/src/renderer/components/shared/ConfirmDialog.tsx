import { AlertTriangle, type LucideIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '../../lib/utils';

import type { ReactNode } from 'react';

export interface ConfirmDialogProps {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  icon?: LucideIcon;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel,
  danger = false,
  icon: Icon = AlertTriangle,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const confirm = confirmLabel ?? t('common.confirm');
  const cancel = cancelLabel ?? t('common.cancel');
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
      role="dialog"
      aria-modal="true"
      aria-label={typeof title === 'string' ? title : undefined}
    >
      <div className="bg-[var(--surface-elevated)] border border-[var(--border)] rounded-xl shadow-xl w-[400px]">
        <div className="flex items-center gap-2 px-5 py-4 border-b border-[var(--border-subtle)]">
          <Icon size={16} className={danger ? 'text-[var(--danger)]' : 'text-[var(--warning)]'} />
          <h2 className="text-sm font-semibold text-[var(--text)]">{title}</h2>
        </div>
        <div className="px-5 py-4 text-sm text-[var(--text-muted)]">{message}</div>
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[var(--border-subtle)]">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
          >
            {cancel}
          </button>
          <button
            onClick={onConfirm}
            className={cn(
              'px-4 py-1.5 rounded-lg text-white text-sm font-medium transition-all',
              danger
                ? 'bg-[var(--danger)] hover:brightness-110'
                : 'bg-[var(--accent)] hover:bg-[var(--accent-hover)]'
            )}
          >
            {confirm}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * File overwrite confirmation dialog — specialized variant of ConfirmDialog.
 */
export function SaveConfirmDialog({
  filePath,
  onConfirm,
  onCancel,
}: {
  filePath: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <ConfirmDialog
      title={t('saveConfirm.title')}
      message={
        <>
          {t('saveConfirm.msgPrefix')}
          <code className="text-[var(--text)] font-mono">{filePath}</code>
          {t('saveConfirm.msgSuffix')}
        </>
      }
      confirmLabel={t('saveConfirm.confirm')}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
