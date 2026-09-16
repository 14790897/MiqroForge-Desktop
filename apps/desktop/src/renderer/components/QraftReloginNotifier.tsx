import { useEffect, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useQraftStatus } from '../hooks/useQraftStatus';

/**
 * 平台登录失效的全局告知 —— 主进程只在平台作废 refresh_token
 * （REFRESH_TOKEN_INVALID，永久失效）时置 requiresRelogin，此时
 * 无论用户身处哪个页面都弹出顶部横幅引导重新登录；瞬时失败（网络等）
 * 由主进程静默退避重试，不置 requiresRelogin、不弹横幅（issue #1087）。
 *
 * 横幅常驻（不自动消失），可手动关闭；顶栏账号 chip 同时切换为
 * 失效态（见 TopBar），保证关闭横幅后仍有持续提示。
 */

/** 失效告知状态机（纯函数，便于单测）：
 *   - requiresRelogin 由 false 变 true → 弹横幅并标记已告知；
 *   - 初始快照即 true（应用启动即发现 token 已作废）→ 同样弹横幅；
 *   - 已告知期间（用户关闭横幅）不再重复弹；
 *   - requiresRelogin 回 false（重新登录/登出）→ 复位，下次失效再告知。
 */
export function nextReloginNotifyState(
  prev: { notified: boolean; visible: boolean },
  requiresRelogin: boolean
): { notified: boolean; visible: boolean } {
  if (!requiresRelogin) return { notified: false, visible: false };
  if (prev.notified) return prev;
  return { notified: true, visible: true };
}

export function QraftReloginNotifier({ onOpenQraft }: { onOpenQraft: () => void }) {
  const { status } = useQraftStatus();
  const requiresRelogin = status?.requiresRelogin === true;
  const [state, setState] = useState({ notified: false, visible: false });

  useEffect(() => {
    setState((prev) => nextReloginNotifyState(prev, requiresRelogin));
  }, [requiresRelogin]);

  if (!state.visible) return null;

  // requiresRelogin 只在平台作废 refresh_token（永久失效）时置位，
  // 横幅文案固定为引导重新登录（瞬时失败由主进程静默重试）。
  const text = 'MiQroForge 平台登录已失效，请重新登录恢复平台功能。';
  const action = '去重新登录';

  return (
    <div
      data-testid="qraft-relogin-notify"
      role="alert"
      className="fixed left-1/2 top-16 z-50 flex max-w-[min(640px,calc(100vw-24px))] -translate-x-1/2 items-center gap-2.5 rounded-xl border px-4 py-2.5 text-xs backdrop-blur animate-banner-in"
      style={{
        background: 'color-mix(in srgb, var(--approval-warning-bg) 92%, white)',
        borderColor: 'var(--approval-warning-border)',
        color: 'var(--approval-warning)',
      }}
    >
      <AlertTriangle size={14} className="shrink-0" />
      <span className="min-w-0 flex-1 font-medium">{text}</span>
      <button
        type="button"
        data-testid="qraft-relogin-notify-action"
        onClick={() => {
          setState((prev) => ({ ...prev, visible: false }));
          onOpenQraft();
        }}
        className="shrink-0 rounded-md px-2.5 py-1 text-size-2xs font-semibold transition-colors hover:bg-[rgba(124,45,18,0.08)]"
      >
        {action}
      </button>
      <button
        type="button"
        data-testid="qraft-relogin-notify-close"
        aria-label="关闭提醒"
        title="关闭提醒"
        onClick={() => setState((prev) => ({ ...prev, visible: false }))}
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-[rgba(124,45,18,0.12)]"
      >
        <X size={13} />
      </button>
    </div>
  );
}
