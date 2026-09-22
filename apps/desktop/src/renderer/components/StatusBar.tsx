import { useEffect, useRef, useState } from 'react';
import { cn } from '../lib/utils';
import { useRuntime } from '../contexts/RuntimeContext';
import { useRestartRequired } from '../contexts/RestartRequiredContext';
import { useQraftStatus } from '../hooks/useQraftStatus';
import { Coins, Loader2, RefreshCw } from 'lucide-react';
import type { QraftBillingHistoryEntry, QraftErrorCode } from '../../shared/ipc';

const STATES: Record<string, { label: string; color: string }> = {
  stopped: { label: '已停止', color: 'var(--text-faint)' },
  starting: { label: '启动中', color: 'var(--warning)' },
  running: { label: '运行中', color: 'var(--success)' },
  stopping: { label: '停止中', color: 'var(--warning)' },
  error: { label: '错误', color: 'var(--danger)' },
};

// ── 积分余额轮询重试策略（issue #1160）──────────────────────────────
/** 瞬时失败（网络抖动/平台暂不可达）的重试间隔：30 秒起步翻倍，封顶 5 分钟。 */
const POINTS_RETRY_BASE_MS = 30_000;
const POINTS_RETRY_MAX_MS = 5 * 60_000;
/** 瞬时失败最多尝试次数（含首次）：用尽即停，避免长期刷屏。 */
const POINTS_RETRY_MAX_ATTEMPTS = 5;

/**
 * 会话级永久失败：停止重试，等重新登录成功后自然重拉
 * （登录失效三件套——横幅/顶栏 chip/发送拦截——负责引导重新登录）。
 * 主进程 fetchPointsBalance 已先刷新重试一次，到这里仍返回
 * SESSION_EXPIRED / REFRESH_TOKEN_INVALID 说明会话确实失效。
 */
export function isPermanentPointsError(code: QraftErrorCode | undefined): boolean {
  return (
    code === 'SESSION_EXPIRED' || code === 'REFRESH_TOKEN_INVALID' || code === 'INVALID_CONFIG'
  );
}

/** 已完成 attemptsDone 次尝试（1 起）后的退避等待毫秒数：30s → 1m → 2m → 4m → 5m 封顶。 */
export function nextPointsRetryDelayMs(attemptsDone: number): number {
  return Math.min(POINTS_RETRY_BASE_MS * 2 ** (attemptsDone - 1), POINTS_RETRY_MAX_MS);
}

export interface PointsRetryDecision {
  /** 是否安排下一次重试。 */
  retry: boolean;
  /** 下次重试的等待毫秒数（不重试时无意义）。 */
  delayMs: number;
}

/** 根据本次结果与已完成尝试次数决定是否重试：成功/永久失败/次数用尽即停。 */
export function decidePointsRetry(
  outcome: { ok: boolean; code?: QraftErrorCode } | undefined,
  attemptsDone: number
): PointsRetryDecision {
  if (outcome?.ok) return { retry: false, delayMs: 0 };
  if (isPermanentPointsError(outcome?.code)) return { retry: false, delayMs: 0 };
  if (attemptsDone >= POINTS_RETRY_MAX_ATTEMPTS) return { retry: false, delayMs: 0 };
  return { retry: true, delayMs: nextPointsRetryDelayMs(attemptsDone) };
}

function fmtDateTime(epochMs?: number): string {
  if (!epochMs) return '—';
  const d = new Date(epochMs);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function StatusBar({ onOpenPoints }: { onOpenPoints?: () => void }) {
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
  // 的状态。失败分两类（issue #1160）：会话级永久失败（SESSION_EXPIRED /
  // REFRESH_TOKEN_INVALID）不再重试，由登录失效三件套引导重新登录；瞬时
  // 失败（平台暂不可达等）按 30 秒起步翻倍退避重试，最多 5 次尝试。
  useEffect(() => {
    if (!loggedIn || qraftStatus?.points !== undefined || qraftStatus?.requiresRelogin) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let attemptsDone = 0;
    const attempt = () => {
      attemptsDone += 1;
      try {
        window.miqi.qraft
          .pointsBalance()
          .catch(() => undefined)
          .then((result) => {
            if (cancelled) return;
            const decision = decidePointsRetry(result, attemptsDone);
            if (decision.retry) timer = setTimeout(attempt, decision.delayMs);
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
  }, [loggedIn, qraftStatus?.points, qraftStatus?.requiresRelogin]);

  const handleRestart = async () => {
    setRestarting(true);
    setRestartError(null);
    try {
      await stop();
      const result = await start();
      if (result?.state === 'running') {
        clearRestartRequired();
      } else if (result) {
        setRestartError(`运行时状态：${result.state}`);
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setRestartError(
        message.includes('Bridge not running')
          ? '运行时正在重启，请稍后再试。'
          : '重启失败，请稍后再试或重新打开应用。'
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
        <span style={{ color: 'var(--text-muted)' }}>{restartRequired ? '需要重启' : s.label}</span>
      </span>

      {status.configured && !restartRequired && (
        <span style={{ color: 'var(--text-faint)' }}>已配置</span>
      )}

      {restartRequired && (
        <span
          className="flex items-center gap-2"
          style={{ color: 'var(--warning)' }}
          title={
            restartReasons.length > 0
              ? `需要重启的原因：${restartReasons.join('；')}`
              : '部分配置需要重启应用后才能生效'
          }
        >
          配置已变更
          {restartReasons.length > 0 && (
            <span
              className="text-[var(--text-faint)] max-w-[220px] truncate"
              title={restartReasons.join('；')}
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
            立即重启
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
              title={`可用积分 ${qraftStatus.points.availablePoints} · 累计获得 ${qraftStatus.points.totalEarned} · 累计支出 ${qraftStatus.points.totalSpent}（点击查看明细）`}
            >
              <Coins size={12} style={{ color: 'var(--accent)' }} />
              积分 {qraftStatus.points.availablePoints}
            </button>
            {historyOpen && (
              <div
                className="absolute bottom-full right-0 z-50 mb-1.5 w-80 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface)] shadow-lg"
                data-testid="statusbar-points-popover"
              >
                <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-3 py-2">
                  <span className="text-xs font-medium text-[var(--text)]">积分明细</span>
                  <span className="text-size-2xs text-[var(--text-faint)]">
                    累计获得 {qraftStatus.points.totalEarned} · 累计支出{' '}
                    {qraftStatus.points.totalSpent}
                  </span>
                </div>
                {historyError ? (
                  <p className="px-3 py-3 text-size-2xs text-[var(--text-faint)]">
                    扣费历史加载失败
                  </p>
                ) : billingHistory === null ? (
                  <p className="px-3 py-3 text-size-2xs text-[var(--text-faint)]">加载中…</p>
                ) : billingHistory.length === 0 ? (
                  <p
                    className="px-3 py-3 text-size-2xs text-[var(--text-faint)]"
                    data-testid="statusbar-billing-empty"
                  >
                    暂无扣费记录
                  </p>
                ) : (
                  <ul
                    className="flex max-h-64 flex-col gap-1.5 overflow-y-auto px-3 py-2"
                    data-testid="statusbar-billing-history"
                  >
                    {billingHistory.map((entry) => (
                      <li key={entry.chargeId} className="flex flex-col gap-0.5 text-size-2xs">
                        {/* 主信息行：作业 ID + 金额。日期不与标题同行——窄弹层
                            下（CI Linux 字体更宽）「日期+金额」会吃满整行，把
                            作业 ID 挤成 0 宽整条不可见。 */}
                        <div className="flex items-center justify-between gap-2">
                          <span className="min-w-0 truncate text-[var(--text)]">
                            {entry.jobId
                              ? `作业 ${entry.jobId}`
                              : `${entry.serverName ?? ''}.${entry.toolName ?? ''}`}
                          </span>
                          <span className="shrink-0 text-right">
                            {entry.status === 'billed' ? (
                              <>
                                <span className="text-[var(--danger)]">-{entry.cost}</span>
                                {entry.balanceAfter !== undefined && (
                                  <span className="ml-1 text-[var(--text-faint)]">
                                    余额 {entry.balanceAfter}
                                  </span>
                                )}
                              </>
                            ) : (
                              <span className="text-[var(--warning)]">
                                {entry.status === 'insufficient' ? '余额不足' : '扣费失败'}
                              </span>
                            )}
                          </span>
                        </div>
                        <p className="flex items-center gap-2 text-[var(--text-faint)]">
                          <span className="shrink-0">
                            {fmtDateTime(Date.parse(entry.deductedAt))}
                          </span>
                          {entry.argsSummary && (
                            <span className="min-w-0 truncate">{entry.argsSummary}</span>
                          )}
                        </p>
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
                  在设置中查看全部
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
