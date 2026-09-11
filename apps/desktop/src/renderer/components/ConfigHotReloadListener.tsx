import { useEffect, useState, useRef, useCallback } from 'react';
import { CheckCircle2, Info, RefreshCw } from 'lucide-react';
import { invalidateConfigCache } from '../lib/configCache';
import { useRestartRequired } from '../contexts/RestartRequiredContext';
import type { ConfigUpdatedPayload } from '../../shared/ipc';

export type ConfigUpdateFeedback = { kind: 'ok' | 'info' | 'warn'; text: string };

/**
 * Pure decision logic for the config_updated broadcast (#789) — extracted so
 * it can be unit-tested without a DOM.
 */
export function resolveConfigUpdateFeedback(
  payload: ConfigUpdatedPayload
): ConfigUpdateFeedback | null {
  const applied = payload.applied ?? [];
  const newSessions = payload.newSessionsOnly ?? [];
  const restart = payload.restartRequired ?? [];
  const reasons = payload.restartReasons ?? [];

  if (restart.length > 0) {
    const reason = (reasons[0] ?? '').replace('，修改后需重启应用', '');
    return { kind: 'warn', text: `已保存，部分配置需要重启后生效：${reason}` };
  }
  if (newSessions.length > 0) {
    return { kind: 'info', text: '已保存，对新建会话生效' };
  }
  if (applied.length > 0) {
    // Provider rebuild failed during hot-apply: the save is persisted but the
    // active session still uses the old provider — do NOT claim "已生效".
    // Only relevant when the save actually touched provider/model paths
    // (#10 review: an unrelated save must not raise the failure toast).
    const touchedProvider = applied.some(
      (p) => p.startsWith('providers') || p === 'agents.defaults.model'
    );
    if (payload.providerRebuilt === false && touchedProvider) {
      return {
        kind: 'info',
        text: '已保存，Provider 重建失败，新配置将在新会话生效',
      };
    }
    return { kind: 'ok', text: '配置已生效，无需重启' };
  }
  return null;
}

/**
 * Issue #789: listens for the bridge's `config_updated` broadcast (emitted
 * after any config save) and reacts:
 *
 * 1. Invalidates the frontend config cache so all settings tabs re-read the
 *    new values without a restart.
 * 2. Marks "restart required" (with reasons) only when tier-C paths changed
 *    — tier-A/B saves no longer force the restart banner.
 * 3. Shows a transient toast with the per-tier message:
 *    「已生效」/「已保存，对新建会话生效」/「需要重启」.
 */
export function ConfigHotReloadListener() {
  const { markRestartRequired, clearRestartRequired } = useRestartRequired();
  const [toast, setToast] = useState<ConfigUpdateFeedback | null>(null);
  const timerRef = useRef<number | null>(null);

  const showToast = useCallback((feedback: ConfigUpdateFeedback) => {
    setToast(feedback);
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  useEffect(() => {
    const off = window.miqi.config.onUpdated((payload: ConfigUpdatedPayload) => {
      // Always refresh the cached config — every save invalidates it.
      invalidateConfigCache();

      const restart = payload.restartRequired ?? [];
      const reasons = payload.restartReasons ?? [];
      if (restart.length > 0) {
        // Tier C — keep the restart banner (with reasons).
        markRestartRequired(reasons);
      } else {
        // #8 review: once a save no longer touches any tier-C path, the
        // banner must clear — previously it lingered forever (with stale
        // reasons) until a real restart.
        clearRestartRequired();
      }
      const feedback = resolveConfigUpdateFeedback(payload);
      if (feedback) showToast(feedback);
    });
    return () => {
      off();
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [markRestartRequired, clearRestartRequired, showToast]);

  if (!toast) return null;

  const Icon = toast.kind === 'ok' ? CheckCircle2 : toast.kind === 'warn' ? RefreshCw : Info;
  const color =
    toast.kind === 'ok'
      ? 'var(--success)'
      : toast.kind === 'warn'
        ? 'var(--warning)'
        : 'var(--accent)';

  return (
    <div
      data-testid="config-updated-toast"
      className="fixed bottom-[250px] left-1/2 -translate-x-1/2 z-[150] flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-medium shadow-[0_4px_20px_rgba(0,0,0,0.18)]"
      style={{
        background: 'var(--surface)',
        border: '1px solid var(--border-subtle)',
        color: 'var(--text)',
      }}
    >
      <Icon size={14} style={{ color }} />
      <span>{toast.text}</span>
    </div>
  );
}
