import { AlertTriangle, type LucideIcon } from 'lucide-react';
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
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger = false,
  icon: Icon = AlertTriangle,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
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
            {cancelLabel}
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
            {confirmLabel}
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
  return (
    <ConfirmDialog
      title="覆盖文件"
      message={
        <>
          此操作将覆盖 <code className="text-[var(--text)] font-mono">{filePath}</code>{' '}
          的内容，此操作不可撤销。
        </>
      }
      confirmLabel="确认覆盖"
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
