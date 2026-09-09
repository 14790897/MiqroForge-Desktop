import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '../lib/utils';
import { useRuntime } from '../contexts/RuntimeContext';
import { useRestartRequired } from '../contexts/RestartRequiredContext';
import { useQraftStatus } from '../hooks/useQraftStatus';
import { Coins, Loader2, RefreshCw } from 'lucide-react';
import type { QraftBillingHistoryEntry } from '../../shared/ipc';

const STATES: Record<string, { labelKey: string; color: string }> = {
  stopped: { labelKey: 'statusBar.stopped', color: 'var(--text-faint)' },
  starting: { labelKey: 'statusBar.starting', color: 'var(--warning)' },
  running: { labelKey: 'statusBar.running', color: 'var(--success)' },
  stopping: { labelKey: 'statusBar.stopping', color: 'var(--warning)' },
  error: { labelKey: 'statusBar.error', color: 'var(--danger)' },
};

function fmtDateTime(epochMs?: number): string {
  if (!epochMs) return '—';
  const d = new Date(epochMs);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function StatusBar({ onOpenPoints }: { onOpenPoints?: () => void }) {
  const { t } = useTranslation();
  const { status, start, stop } = useRuntime();
  const { restartRequired, restartReasons, clearRestartRequired } = useRestartRequired();
  const { status: qraftStatus, loggedIn } = useQraftStatus();
  const s = STATES[status.state] ?? STATES.stopped;
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);
  /** 积分按钮弹层：打开时展示本地扣费历史（issue #927 明细入口）。 */
  const [historyOpen, setHistoryOpen] = useState(false);
  const [billingHistory, setBillingHistory] = useState<QraftBillingHistoryEntry[] | null>(null);
  const [historyError, setHistoryError] = useState(false);
  const pointsWrapRef = useRef<HTMLDivElement>(null);

  // 弹层打开时拉取扣费历史；打开期间余额变化（新扣费推送状态事件）时重拉，
  // 保证刚扣完费的记录即时可见。
  useEffect(() => {
    if (!historyOpen) return;
    let cancelled = false;
    try {
      window.miqi.qraft
        .billingHistory()
        .then((entries) => {
          if (!cancelled) {
            setBillingHistory(entries);
            setHistoryError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setHistoryError(true);
        });
    } catch {
      // preload 存在但无 billingHistory 方法（旧版 preload）：同样落错误态，
      // 不能停在「加载中…」。
      if (!cancelled) setHistoryError(true);
    }
    return () => {
      cancelled = true;
    };
  }, [historyOpen, qraftStatus?.points?.availablePoints]);

  // 点击弹层外部或按 Escape 关闭。
  useEffect(() => {
    if (!historyOpen) return;
    const onPointerDown = (e: MouseEvent) => {
      if (pointsWrapRef.current && !pointsWrapRef.current.contains(e.target as Node)) {
        setHistoryOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setHistoryOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [historyOpen]);

  // 登录后拉取一次积分余额：主进程（QraftService.fetchPointsBalance）成功
  // 缓存后会推送 statusChanged，此处经 useQraftStatus 自动收到带 points
  // 的状态。拉取失败（平台暂不可达等）30 秒后重试，成功或退出登录即停。
  useEffect(() => {
    if (!loggedIn || qraftStatus?.points !== undefined) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const attempt = () => {
      try {
        window.miqi.qraft
          .pointsBalance()
          .catch(() => {})
          .then((result) => {
            if (!result?.ok && !cancelled) timer = setTimeout(attempt, 30_000);
          });
      } catch {
        /* 旧版 preload（如 smoke mock）没有 qraft 命名空间 */
      }
    };
    attempt();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [loggedIn, qraftStatus?.points]);

  const handleRestart = async () => {
    setRestarting(true);
    setRestartError(null);
    try {
      await stop();
      const result = await start();
      if (result?.state === 'running') {
        clearRestartRequired();
      } else if (result) {
        setRestartError(t('statusBar.runtimeState', { state: result.state }));
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setRestartError(
        message.includes('Bridge not running')
          ? t('statusBar.restartBusy')
          : t('statusBar.restartFailed')
      );
    } finally {
      setRestarting(false);
    }
  };

  return (
    <div
      className="flex items-center gap-3 h-7 px-4 shrink-0 text-xs"
      style={{
        background: 'var(--surface-muted)',
        borderTop: '1px solid var(--border-subtle)',
        color: 'var(--text-faint)',
      }}
    >
      <span className="flex items-center gap-1.5">
        <span
          className={cn(
            'inline-block w-1.5 h-1.5 rounded-full',
            restartRequired && 'animate-pulse'
          )}
          style={{
            backgroundColor: restartRequired ? 'var(--warning)' : s.color,
          }}
        />
        <span style={{ color: 'var(--text-muted)' }}>
          {restartRequired ? t('statusBar.needRestart') : t(s.labelKey)}
        </span>
      </span>

      {status.configured && !restartRequired && (
        <span style={{ color: 'var(--text-faint)' }}>{t('statusBar.configured')}</span>
      )}

      {restartRequired && (
        <span
          className="flex items-center gap-2"
          style={{ color: 'var(--warning)' }}
          title={
            restartReasons.length > 0
              ? t('statusBar.reasonsTitle', {
                  list: restartReasons.join(t('statusBar.reasonSeparator')),
                })
              : t('statusBar.restartHint')
          }
        >
          {t('statusBar.configChanged')}
          {restartReasons.length > 0 && (
            <span
              className="text-[var(--text-faint)] max-w-[220px] truncate"
              title={restartReasons.join(t('statusBar.reasonSeparator'))}
            >
              {restartReasons[0]}
            </span>
          )}
          <button
            onClick={handleRestart}
            disabled={restarting}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium transition-all disabled:opacity-60"
            style={{ background: 'var(--accent)', color: 'var(--accent-text)' }}
          >
            {restarting ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
            {t('statusBar.restartNow')}
          </button>
        </span>
      )}

      {restartError && <span style={{ color: 'var(--danger)' }}>{restartError}</span>}

      <div className="ml-auto flex items-center gap-3">
        {loggedIn && qraftStatus?.points && (
          <div className="relative" ref={pointsWrapRef}>
            <button
              type="button"
              onClick={() => setHistoryOpen((open) => !open)}
              aria-expanded={historyOpen}
              className={cn(
                'flex items-center gap-1 rounded px-1 py-0.5 text-xs transition-colors',
                historyOpen && 'bg-[var(--surface-raised)]'
              )}
              style={{ color: 'var(--text-muted)' }}
              data-testid="statusbar-points"
              title={t('statusBar.pointsTitle', {
                avail: qraftStatus.points.availablePoints,
                earned: qraftStatus.points.totalEarned,
                spent: qraftStatus.points.totalSpent,
              })}
            >
              <Coins size={12} style={{ color: 'var(--accent)' }} />
              {t('statusBar.pointsCount', { count: qraftStatus.points.availablePoints })}
            </button>
            {historyOpen && (
              <div
                className="absolute bottom-full right-0 z-50 mb-1.5 w-80 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface)] shadow-lg"
                data-testid="statusbar-points-popover"
              >
                <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-2">
                  <span className="text-xs font-medium text-[var(--text)]">
                    {t('statusBar.historyTitle')}
                  </span>
                  <span className="text-size-2xs text-[var(--text-faint)]">
                    {t('statusBar.earnedSpent', {
                      earned: qraftStatus.points.totalEarned,
                      spent: qraftStatus.points.totalSpent,
                    })}
                  </span>
                </div>
                {historyError ? (
                  <p className="px-3 py-3 text-size-2xs text-[var(--text-faint)]">
                    {t('statusBar.historyLoadError')}
                  </p>
                ) : billingHistory === null ? (
                  <p className="px-3 py-3 text-size-2xs text-[var(--text-faint)]">
                    {t('statusBar.loading')}
                  </p>
                ) : billingHistory.length === 0 ? (
                  <p
                    className="px-3 py-3 text-size-2xs text-[var(--text-faint)]"
                    data-testid="statusbar-billing-empty"
                  >
                    {t('statusBar.noHistory')}
                  </p>
                ) : (
                  <ul
                    className="flex max-h-64 flex-col gap-1.5 overflow-y-auto px-3 py-2"
                    data-testid="statusbar-billing-history"
                  >
                    {billingHistory.map((entry) => (
                      <li
                        key={entry.chargeId}
                        className="flex items-center justify-between gap-2 text-size-2xs"
                      >
                        <div className="min-w-0">
                          <p className="flex items-center text-[var(--text)]">
                            <span className="min-w-0 truncate">
                              {entry.jobId
                                ? t('statusBar.job', { id: entry.jobId })
                                : `${entry.serverName ?? ''}.${entry.toolName ?? ''}`}
                            </span>
                            <span className="ml-2 shrink-0 text-[var(--text-faint)]">
                              {fmtDateTime(Date.parse(entry.deductedAt))}
                            </span>
                          </p>
                          {entry.argsSummary && (
                            <p className="truncate text-[var(--text-faint)]">{entry.argsSummary}</p>
                          )}
                        </div>
                        <div className="shrink-0 text-right">
                          {entry.status === 'billed' ? (
                            <>
                              <span className="text-[var(--danger)]">-{entry.cost}</span>
                              {entry.balanceAfter !== undefined && (
                                <span className="ml-1 text-[var(--text-faint)]">
                                  {t('statusBar.balance', { count: entry.balanceAfter })}
                                </span>
                              )}
                            </>
                          ) : (
                            <span className="text-[var(--warning)]">
                              {entry.status === 'insufficient'
                                ? t('statusBar.insufficient')
                                : t('statusBar.billingFailed')}
                            </span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setHistoryOpen(false);
                    onOpenPoints?.();
                  }}
                  className="w-full border-t border-[var(--border-subtle)] px-3 py-2 text-center text-xs text-[var(--accent)]"
                  data-testid="statusbar-points-open-settings"
                >
                  {t('statusBar.viewAllInSettings')}
                </button>
              </div>
            )}
          </div>
        )}
        <span className="text-text-faint">
          MiQroForge Desktop v{typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'}
        </span>
      </div>
    </div>
  );
}
