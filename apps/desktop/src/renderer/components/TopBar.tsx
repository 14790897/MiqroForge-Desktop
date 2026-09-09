import { useEffect, useState } from 'react';
import { useRuntime } from '../contexts/RuntimeContext';
import { AlertTriangle, RefreshCw, Loader2, Folder, UserRound } from 'lucide-react';
import { cn } from '../lib/utils';
import { MiQroForgeLogo } from './MiQroForgeLogo';
import { useQraftStatus } from '../hooks/useQraftStatus';
import { QraftLoginButton } from '../features/settings/components/QraftLoginCard';

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

function getBypassLabel(status: ApprovalBypassStatus | null, autoMode: boolean): string {
  if (autoMode) return '自动';
  if (isAllBypassOn(status)) return '全部绕过';
  return '绕过';
}

function getBypassTitle(status: ApprovalBypassStatus | null, autoMode: boolean = false): string {
  if (autoMode) return '自动模式：所有审批已绕过';
  if (status?.bypassAll) return '所有审批类别已启用绕过';
  const labels: string[] = [];
  if (status?.bypassCommandApproval) labels.push('命令审批');
  if (status?.bypassFileWriteApproval) labels.push('文件写入审批');
  if (status?.bypassToolConfirmation) labels.push('工具确认');
  if (status?.bypassNetworkApproval) labels.push('网络审批');
  return labels.length > 0 ? `已绕过: ${labels.join('、')}` : '打开审批设置';
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
  onOpenQraft,
  workspace,
}: {
  onOpenApprovals?: () => void;
  /** #1000: 账号 chip 点击 → 设置 → MiQroForge 平台。 */
  onOpenQraft?: () => void;
  workspace?: string;
}) {
  const { status, start } = useRuntime();
  // #1000: 顶栏登录入口 —— 未登录显示一键登录 chip（错误经 title 提示，
  // 不内联渲染避免顶栏抖动）；已登录显示账号 chip，点击进平台账号页。
  const { status: qraftStatus, loggedIn } = useQraftStatus();
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
    bypassDetails.push('自动模式');
  } else if (isAllBypassOn(approvalBypass)) {
    bypassDetails.push('全部操作');
  } else {
    if (approvalBypass?.bypassCommandApproval) bypassDetails.push('命令执行');
    if (approvalBypass?.bypassFileWriteApproval) bypassDetails.push('文件写入');
    if (approvalBypass?.bypassToolConfirmation) bypassDetails.push('工具调用');
    if (approvalBypass?.bypassNetworkApproval) bypassDetails.push('网络请求');
  }
  const bypassDetailText = bypassDetails.length ? ' · ' + bypassDetails.join(' · ') : '';

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
            aria-label={getBypassTitle(approvalBypass, autoMode)}
            title={getBypassTitle(approvalBypass, autoMode)}
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
              <span>{getBypassLabel(approvalBypass, autoMode)}</span>
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
              ? '运行时已连接'
              : isStarting
                ? '正在启动/停止运行时…'
                : '运行时未连接，点击重新连接'
          }
          aria-label={
            isRunning
              ? '运行时已连接'
              : isStarting
                ? '正在启动/停止运行时'
                : '运行时未连接，点击重新连接'
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
          <span>{isRunning ? '已同步' : isStarting ? '同步中' : '离线'}</span>
        </button>
      </div>

      {/* Right: account entry + user avatar */}
      <div className="flex items-center gap-2">
        {/* #1000 登录入口：未登录一键登录；已登录账号 chip 点击进平台账号页 */}
        {loggedIn ? (
          <button
            type="button"
            onClick={onOpenQraft}
            className="flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors hover:brightness-95"
            style={{
              background: 'var(--accent-soft)',
              color: 'var(--accent)',
            }}
            data-testid="topbar-account-chip"
            title="查看平台账号（设置 → MiQroForge 平台）"
          >
            <UserRound size={12} />
            {qraftStatus?.account?.nickname || qraftStatus?.account?.username || '已登录'}
          </button>
        ) : (
          <QraftLoginButton
            testId="topbar-login-btn"
            size="sm"
            busyLabel="等待授权中…"
            inlineFeedback={false}
          />
        )}
        <span
          className="text-xs font-medium hidden sm:block"
          style={{ color: 'var(--topbar-text)' }}
        >
          MiQroForge 智能体
        </span>
        <MiQroForgeLogo size={28} />
      </div>
    </div>
  );
}
