/**
 * Build a display string for the approval modal with fallback order:
 *   1. pending.command (exec tool)
 *   2. pending.details?.command (exec tool via details)
 *   3. pending.details?.path (file_write tool)
 *   4. pending.details?.tool_name (unknown tool)
 *   5. pending.description (always available)
 */
export function getApprovalDisplay(pending: {
  command?: string;
  description: string;
  details?: Record<string, unknown>;
}): string {
  if (pending.command && pending.command.trim()) return pending.command.trim();
  const d = pending.details;
  if (d) {
    if (typeof d.command === 'string' && d.command.trim()) return d.command.trim();
    if (typeof d.path === 'string' && d.path.trim()) return d.path.trim();
    if (typeof d.tool_name === 'string' && d.tool_name.trim()) return d.tool_name.trim();
  }
  return pending.description || '(no details)';
}

/**
 * Choose an appropriate title based on approval category.
 */
import { i18n } from '../../i18n';

export function getApprovalTitle(category?: string): string {
  if (category === 'exec') return i18n.t('approvals.titleCmd');
  if (category === 'file_write') return i18n.t('approvals.titleFile');
  return i18n.t('approvals.titleOp');
}
