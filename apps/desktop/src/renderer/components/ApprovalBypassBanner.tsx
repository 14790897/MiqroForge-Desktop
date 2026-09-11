import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';

interface ApprovalBypassStatus {
  bypassAll?: boolean;
  bypassCommandApproval?: boolean;
  bypassFileWriteApproval?: boolean;
  bypassToolConfirmation?: boolean;
  bypassNetworkApproval?: boolean;
}

function hasApprovalBypass(config: Record<string, unknown>): boolean {
  const approvals = (config.approvals ?? {}) as ApprovalBypassStatus;
  return Boolean(
    approvals.bypassAll ||
    approvals.bypassCommandApproval ||
    approvals.bypassFileWriteApproval ||
    approvals.bypassToolConfirmation ||
    approvals.bypassNetworkApproval
  );
}

function isAllBypassOn(status: ApprovalBypassStatus): boolean {
  return !!(
    status.bypassAll ||
    (status.bypassCommandApproval &&
      status.bypassFileWriteApproval &&
      status.bypassToolConfirmation &&
      status.bypassNetworkApproval)
  );
}

export function ApprovalBypassBanner({ onOpenApprovals }: { onOpenApprovals?: () => void }) {
  const [enabled, setEnabled] = useState(false);
  const [phase, setPhase] = useState<'hidden' | 'show' | 'hide'>('hidden');
  const timer = useRef<number>(0);
  const firstLoad = useRef(true);

  const load = useCallback(async () => {
    if (!(window as any).miqi?.config?.get) {
      setEnabled(false);
      return;
    }
    try {
      const config = await window.miqi.config.get();
      const isBypass = hasApprovalBypass(config);
      setEnabled(isBypass);
    } catch {
      setEnabled(false);
    }
  }, []);

  useEffect(() => {
    load();
    window.addEventListener('miqi:approval-bypass-updated', load);
    return () => {
      window.removeEventListener('miqi:approval-bypass-updated', load);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  // Trigger show animation when enabled becomes true (skip first load)
  useEffect(() => {
    if (!enabled) {
      setPhase('hidden');
      return;
    }
    if (firstLoad.current) {
      firstLoad.current = false;
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setPhase('show');
    timer.current = window.setTimeout(() => setPhase('hide'), 2500);
  }, [enabled]);

  if (phase === 'hidden') return null;

  return (
    <div
      className={[
        'approval-bypass-island fixed left-1/2 top-12 z-50 flex max-w-[min(720px,calc(100vw-24px))] items-center gap-2 rounded-full border px-3 py-2 text-xs backdrop-blur pointer-events-auto',
        phase === 'show' ? 'animate-banner-in' : 'animate-banner-out',
      ].join(' ')}
      style={{
        background: 'color-mix(in srgb, var(--approval-warning-bg) 92%, white)',
        borderColor: 'var(--approval-warning-border)',
        color: 'var(--approval-warning)',
      }}
    >
      <AlertTriangle size={14} className="shrink-0" />
      <span className="min-w-0 truncate font-medium">
        审批绕过已开启，本次会话的部分操作会直接放行。
      </span>
      <button
        type="button"
        onClick={onOpenApprovals}
        className="shrink-0 rounded-full px-2.5 py-1 text-size-2xs font-semibold transition-colors hover:bg-[rgba(124,45,18,0.08)]"
      >
        查看设置
      </button>
      <button
        type="button"
        onClick={() => setPhase('hide')}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full hover:bg-[rgba(124,45,18,0.12)] transition-colors"
        title="关闭提醒"
      >
        <X size={13} />
      </button>
    </div>
  );
}
