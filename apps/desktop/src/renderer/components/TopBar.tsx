import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useRuntime } from '../contexts/RuntimeContext';
import { changeUILanguage } from '../i18n';
import type { Language } from '../i18n';
import { AlertTriangle, RefreshCw, Loader2, Folder } from 'lucide-react';
import { cn } from '../lib/utils';
import { MiQroForgeLogo } from './MiQroForgeLogo';

interface ApprovalBypassStatus {
  bypassAll?: boolean;
  bypassCommandApproval?: boolean;
  bypassFileWriteApproval?: boolean;
  bypassToolConfirmation?: boolean;
  bypassNetworkApproval?: boolean;
}

function isBypassEnabled(status: ApprovalBypassStatus | null): boolean {
  if (!status) return false;
  return Boolean(
    status.bypassAll ||
    status.bypassCommandApproval ||
    status.bypassFileWriteApproval ||
    status.bypassToolConfirmation ||
    status.bypassNetworkApproval
  );
}

function isAllBypassOn(status: ApprovalBypassStatus | null): boolean {
  if (!status) return false;
  return !!(
    status.bypassAll ||
    (status.bypassCommandApproval &&
      status.bypassFileWriteApproval &&
      status.bypassToolConfirmation &&
      status.bypassNetworkApproval)
  );
}

function getBypassLabel(
  status: ApprovalBypassStatus | null,
  autoMode: boolean,
  t: TFunction
): string {
  if (autoMode) return t('topbar.bypass.labelAuto');
  if (isAllBypassOn(status)) return t('topbar.bypass.labelAll');
  return t('topbar.bypass.labelBypass');
}

function getBypassTitle(
  status: ApprovalBypassStatus | null,
  autoMode: boolean = false,
  t: TFunction
): string {
  if (autoMode) return t('topbar.bypass.titleAuto');
  if (status?.bypassAll) return t('topbar.bypass.titleAll');
  const labels: string[] = [];
  if (status?.bypassCommandApproval) labels.push(t('topbar.bypass.itemCommand'));
  if (status?.bypassFileWriteApproval) labels.push(t('topbar.bypass.itemFileWrite'));
  if (status?.bypassToolConfirmation) labels.push(t('topbar.bypass.itemTool'));
  if (status?.bypassNetworkApproval) labels.push(t('topbar.bypass.itemNetwork'));
  return labels.length > 0
    ? t('topbar.bypass.joined', { list: labels.join(t('common.listSeparator')) })
    : t('topbar.bypass.titleNone');
}

function formatWorkspace(workspace: string): string {
  let display = workspace;
  if (display.length > 30) {
    const segs = display.split(/[\\/]/);
    if (segs.length > 2) {
      display = segs[0] + '/.../' + segs[segs.length - 1];
    }
  }
  return display;
}

export function TopBar({
  onOpenApprovals,
  workspace,
}: {
  onOpenApprovals?: () => void;
  workspace?: string;
}) {
  const { status, start } = useRuntime();
  const { t, i18n } = useTranslation();
  const [approvalBypass, setApprovalBypass] = useState<ApprovalBypassStatus | null>(null);
  const [bypassHovered, setBypassHovered] = useState(false);
  const [autoMode, setAutoMode] = useState(() => sessionStorage.getItem('miqi:mode:auto') === '1');

  const isRunning = status.state === 'running';
  const isStarting = status.state === 'starting' || status.state === 'stopping';
  const isOffline = !isRunning && !isStarting;
  const bypassEnabled = isBypassEnabled(approvalBypass) || autoMode;

  const handleStatusClick = async () => {
    if (!isOffline) return;
    try {
      await start();
    } catch {
      // start 失败时状态会由 onStateChange / refreshStatus 更新为 error，无需额外处理
    }
  };

  // Listen for auto mode changes
  useEffect(() => {
    const h = () => setAutoMode(sessionStorage.getItem('miqi:mode:auto') === '1');
    window.addEventListener('miqi:mode-changed', h);
    return () => window.removeEventListener('miqi:mode-changed', h);
  }, []);

  // Build detail text for hover expansion
  const bypassDetails: string[] = [];
  if (autoMode) {
    bypassDetails.push(t('topbar.bypass.detailAuto'));
  } else if (isAllBypassOn(approvalBypass)) {
    bypassDetails.push(t('topbar.bypass.detailAll'));
  } else {
    if (approvalBypass?.bypassCommandApproval) bypassDetails.push(t('topbar.bypass.detailCommand'));
    if (approvalBypass?.bypassFileWriteApproval)
      bypassDetails.push(t('topbar.bypass.detailFileWrite'));
    if (approvalBypass?.bypassToolConfirmation) bypassDetails.push(t('topbar.bypass.detailTool'));
    if (approvalBypass?.bypassNetworkApproval) bypassDetails.push(t('topbar.bypass.detailNetwork'));
  }
  const bypassDetailText = bypassDetails.length
    ? t('topbar.bypass.detailSeparator') + bypassDetails.join(t('topbar.bypass.detailSeparator'))
    : '';

  useEffect(() => {
    let cancelled = false;
    let inFlight = false; // debounce guard: don't pile up requests when bridge is slow

    const loadApprovalBypass = async () => {
      if (inFlight) return; // skip if previous request still pending
      if (!(window as any).miqi?.config?.get) {
        if (!cancelled) setApprovalBypass(null);
        return;
      }
      inFlight = true;
      try {
        const cfg = await window.miqi.config.get();
        const approvals = (cfg.approvals ?? {}) as ApprovalBypassStatus;
        if (!cancelled) {
          setApprovalBypass(approvals);
        }
      } catch {
        // Keep the last known good state — bridge may be temporarily busy
        // Don't clear approvalBypass; a null here cascades into false
        // "runtime not started" UI.  See PR #xxx.
      } finally {
        inFlight = false;
      }
    };
    loadApprovalBypass();
    window.addEventListener('miqi:approval-bypass-updated', loadApprovalBypass);
    const timer = window.setInterval(loadApprovalBypass, 30_000);
    return () => {
      cancelled = true;
      window.removeEventListener('miqi:approval-bypass-updated', loadApprovalBypass);
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div
      className="flex items-center justify-between h-10 px-5 shrink-0"
      style={{
        background: 'var(--topbar-bg)',
        borderBottom: '1px solid var(--topbar-border)',
      }}
    >
      {/* Left: logo text */}
      <div className="flex items-center gap-2">
        <span
          className="text-sm font-semibold tracking-tight"
          style={{ color: 'var(--topbar-text)' }}
        >
          MiQroForge
        </span>
        <span className="text-xs font-light opacity-50" style={{ color: 'var(--topbar-text)' }}>
          Desktop
        </span>
      </div>

      {/* Center: status pills */}
      <div className="flex items-center gap-2">
        {workspace && (
          <div
            className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px]"
            title={workspace}
            style={{
              background: 'var(--surface-muted)',
              color: 'var(--text-muted)',
            }}
          >
            <Folder size={10} className="shrink-0" />
            <span className="truncate max-w-[200px]">{formatWorkspace(workspace)}</span>
          </div>
        )}
        {bypassEnabled && (
          <button
            type="button"
            onClick={onOpenApprovals}
            onMouseEnter={() => setBypassHovered(true)}
            onMouseLeave={() => setBypassHovered(false)}
            onFocus={() => setBypassHovered(true)}
            onBlur={() => setBypassHovered(false)}
            aria-label={getBypassTitle(approvalBypass, autoMode, t)}
            title={getBypassTitle(approvalBypass, autoMode, t)}
            className="flex items-center rounded-full text-size-2xs font-medium overflow-hidden h-6 shrink-0"
            style={{
              color: 'var(--approval-warning)',
              background: bypassHovered
                ? 'color-mix(in srgb, var(--approval-warning-bg) 80%, transparent)'
                : 'color-mix(in srgb, var(--approval-warning-bg) 50%, transparent)',
              border: '1px solid var(--approval-warning-border)',
              transition: 'background 0.2s ease',
            }}
          >
            <span className="flex items-center gap-1 px-2.5 whitespace-nowrap shrink-0">
              <AlertTriangle size={10} className="shrink-0" />
              <span>{getBypassLabel(approvalBypass, autoMode, t)}</span>
            </span>
            {bypassDetailText && (
              <span
                className="whitespace-nowrap overflow-hidden pr-2.5"
                style={{
                  maxWidth: bypassHovered ? '400px' : '0px',
                  opacity: bypassHovered ? 1 : 0,
                  transition: 'max-width 0.3s ease, opacity 0.25s ease',
                }}
              >
                <span style={{ color: 'var(--text-muted)' }}>{bypassDetailText}</span>
              </span>
            )}
          </button>
        )}
        {/* Sync state */}
        <button
          type="button"
          data-testid="runtime-status-capsule"
          onClick={handleStatusClick}
          disabled={isRunning || isStarting}
          title={
            isRunning
              ? t('topbar.runtime.connected')
              : isStarting
                ? t('topbar.runtime.startingDots')
                : t('topbar.runtime.offlineTitle')
          }
          aria-label={
            isRunning
              ? t('topbar.runtime.connected')
              : isStarting
                ? t('topbar.runtime.starting')
                : t('topbar.runtime.offlineTitle')
          }
          className={cn(
            'flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium',
            isOffline &&
              'cursor-pointer hover:brightness-95 active:brightness-90 transition-[filter]',
            !isOffline && 'cursor-default'
          )}
          style={{
            background: isRunning
              ? 'var(--success-bg)'
              : isStarting
                ? 'var(--warning-bg)'
                : 'var(--danger-bg)',
            color: isRunning ? 'var(--success)' : isStarting ? 'var(--warning)' : 'var(--danger)',
          }}
        >
          {isStarting ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
          <span>
            {isRunning
              ? t('topbar.runtime.synced')
              : isStarting
                ? t('topbar.runtime.syncing')
                : t('topbar.runtime.offline')}
          </span>
        </button>
      </div>

      {/* Right: user avatar */}
      <div className="flex items-center gap-2">
        <span
          className="text-xs font-medium hidden sm:block"
          style={{ color: 'var(--topbar-text)' }}
        >
          {t('topbar.agentLabel')}
        </span>
        <MiQroForgeLogo size={28} />
      </div>
    </div>
  );
}
