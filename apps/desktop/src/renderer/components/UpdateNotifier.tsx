import { useState } from 'react';
import { Download, X } from 'lucide-react';
import { useUpdateStatus } from '../hooks/useUpdateStatus';

/**
 * 自动更新横幅（#1124）。
 *
 * 只在「下载中 / 已下载」时出现：检查失败、已是最新等状态不打扰用户
 * （瞬时网络失败由主进程静默处理，细节在设置 → 关于页查看）。
 * 已下载后引导「立即重启」完成安装；下载中可关闭，下载完成后会再次出现。
 */
export function UpdateNotifier() {
  const snapshot = useUpdateStatus();
  // 按「状态:版本」记录已关闭的提示，避免同一阶段重复弹出
  const [dismissed, setDismissed] = useState<string | null>(null);

  if (!snapshot) return null;
  const { state, version, percent } = snapshot;
  if (state !== 'downloading' && state !== 'downloaded') return null;

  const key = `${state}:${version ?? ''}`;
  if (dismissed === key) return null;

  const versionLabel = version ? ` v${version}` : '';
  const text =
    state === 'downloaded'
      ? `新版本${versionLabel} 已下载，重启应用即可完成更新。`
      : `正在下载新版本${versionLabel}… ${percent ?? 0}%`;

  return (
    <div
      data-testid="update-notify"
      role="status"
      className="fixed left-1/2 top-16 z-50 flex max-w-[min(640px,calc(100vw-24px))] -translate-x-1/2 items-center gap-2.5 rounded-xl border px-4 py-2.5 text-xs backdrop-blur animate-banner-in"
      style={{
        background: 'color-mix(in srgb, var(--approval-warning-bg) 92%, white)',
        borderColor: 'var(--approval-warning-border)',
        color: 'var(--approval-warning)',
      }}
    >
      <Download size={14} className="shrink-0" />
      <span className="min-w-0 flex-1 font-medium">{text}</span>
      {state === 'downloaded' && (
        <button
          type="button"
          data-testid="update-notify-restart"
          onClick={() => {
            void window.miqi.update.install();
          }}
          className="shrink-0 rounded-md px-2.5 py-1 text-size-2xs font-semibold transition-colors hover:bg-[rgba(124,45,18,0.08)]"
        >
          立即重启
        </button>
      )}
      <button
        type="button"
        data-testid="update-notify-close"
        aria-label="关闭提醒"
        title="关闭提醒"
        onClick={() => setDismissed(key)}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-[rgba(124,45,18,0.12)]"
      >
        <X size={13} />
      </button>
    </div>
  );
}
