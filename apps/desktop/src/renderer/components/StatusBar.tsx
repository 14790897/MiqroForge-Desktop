import { useEffect, useState } from 'react';
import { cn } from '../lib/utils';
import { useRuntime } from '../contexts/RuntimeContext';
import { useRestartRequired } from '../contexts/RestartRequiredContext';
import { useQraftStatus } from '../hooks/useQraftStatus';
import { Coins, Loader2, RefreshCw } from 'lucide-react';

const STATES: Record<string, { label: string; color: string }> = {
  stopped: { label: '已停止', color: 'var(--text-faint)' },
  starting: { label: '启动中', color: 'var(--warning)' },
  running: { label: '运行中', color: 'var(--success)' },
  stopping: { label: '停止中', color: 'var(--warning)' },
  error: { label: '错误', color: 'var(--danger)' },
};

export function StatusBar({ onOpenPoints }: { onOpenPoints?: () => void }) {
  const { status, start, stop } = useRuntime();
  const { restartRequired, restartReasons, clearRestartRequired } = useRestartRequired();
  const { status: qraftStatus, loggedIn } = useQraftStatus();
  const s = STATES[status.state] ?? STATES.stopped;
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);

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
          <button
            type="button"
            onClick={onOpenPoints}
            disabled={!onOpenPoints}
            className="flex items-center gap-1 rounded px-1 py-0.5 text-xs transition-colors disabled:cursor-default"
            style={{ color: 'var(--text-muted)' }}
            data-testid="statusbar-points"
            title={`可用积分 ${qraftStatus.points.availablePoints} · 累计获得 ${qraftStatus.points.totalEarned} · 累计支出 ${qraftStatus.points.totalSpent}${onOpenPoints ? '（点击查看明细）' : ''}`}
          >
            <Coins size={12} style={{ color: 'var(--accent)' }} />
            积分 {qraftStatus.points.availablePoints}
          </button>
        )}
        <span className="text-text-faint">
          MiQroForge Desktop v{typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'}
        </span>
      </div>
    </div>
  );
}
