import { useState, useEffect } from 'react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { getCachedConfig, invalidateConfigCache } from '../../../lib/configCache';

interface Props {
  label: string;
  icon: LucideIcon;
  testId: string;
  /** Read current value from config */
  getInitial: (cfg: any) => boolean;
  /** Persist new value */
  onToggle: (next: boolean) => Promise<void>;
  /** Optional: poll runtime status for readiness */
  pollReady?: boolean;
  /** Optional: extra label suffix when toggling */
  togglingLabel?: string;
  /** Optional: extra label when ready */
  readyLabel?: string;
}

export function SettingsToggle({
  label,
  icon: Icon,
  testId,
  getInitial,
  onToggle,
  pollReady,
  togglingLabel,
  readyLabel,
}: Props) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [ready, setReady] = useState<boolean | null>(null);
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pollReady) return;
    const check = () => {
      window.miqi.runtime
        .status()
        .then((s: any) => setReady(s?.sandbox_available === true))
        .catch(() => {});
    };
    check();
    const i = setInterval(check, 5000);
    return () => clearInterval(i);
  }, [pollReady]);

  useEffect(() => {
    getCachedConfig()
      .then((cfg) => setEnabled(getInitial(cfg)))
      .catch(() => setEnabled(false));
  }, [getInitial]);

  const handle = async () => {
    if (enabled === null) return;
    const next = !enabled;
    setToggling(true);
    setError(null);
    try {
      await onToggle(next);
      invalidateConfigCache();
      setEnabled(next);
    } catch (err: any) {
      const msg = err?.message || String(err);
      if (msg.includes('Unknown method') || msg.includes('Bridge not running')) {
        // #14 review: nothing was persisted — do NOT flip the toggle nor
        // claim "已保存".  Surface the real state instead.
        invalidateConfigCache();
        setError('运行时未连接，无法保存设置');
        setTimeout(() => setError(null), 4000);
        setToggling(false);
        return;
      }
      setError(msg || 'Bridge 通信失败');
    }
    setToggling(false);
  };

  const text =
    enabled === null
      ? '…'
      : toggling
        ? (togglingLabel ?? (enabled ? '正在关闭…' : '正在开启…'))
        : enabled
          ? pollReady && ready
            ? (readyLabel ?? label)
            : (readyLabel ?? label)
          : '已关闭';

  return (
    <div className="flex items-center gap-3">
      <button
        onClick={handle}
        disabled={toggling || enabled === null}
        data-testid={`${testId}-btn`}
        className={cn(
          'relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50',
          enabled ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
        )}
      >
        <span
          className={cn(
            'inline-block h-4 w-4 rounded-full bg-white transition-transform',
            enabled ? 'translate-x-6' : 'translate-x-1'
          )}
        />
      </button>
      <Icon
        size={14}
        className={enabled ? 'text-[var(--accent)]' : 'text-[var(--muted-foreground)]'}
      />
      <span
        className={cn(
          'text-xs font-medium',
          enabled
            ? toggling
              ? 'text-amber-400'
              : 'text-[var(--accent)]'
            : 'text-[var(--muted-foreground)]'
        )}
        data-testid={`${testId}-label`}
      >
        {text}
      </span>
      {error && <p className="text-xs text-[var(--warning)]">{error}</p>}
    </div>
  );
}
