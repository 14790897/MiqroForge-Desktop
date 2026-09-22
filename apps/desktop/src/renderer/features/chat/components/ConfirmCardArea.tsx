import { useState } from 'react';
import { useUserInput } from '../../../contexts/UserInputContext';
import { ConfirmCard } from './ConfirmCard';

/**
 * ConfirmCardArea — renders ask_user_confirm_card cards at the tail of the
 * message flow, right above the composer (issue #646).
 *
 * - The pending card (blue, strongest) blocks the turn until the user picks.
 * - Resolved cards stay in the flow, de-emphasized, as a traceable record of
 *   what the user confirmed/cancelled (v5 semantics: cancelled is a neutral
 *   end state, NOT an error).
 * - Resolved history collapses beyond 3 entries ("已处理 N 张确认卡") to avoid
 *   stacking noise during adjust loops.
 */
export function ConfirmCardArea() {
  const { pending, resolved, resolve, timeoutCard } = useUserInput();
  const [historyExpanded, setHistoryExpanded] = useState(false);

  const pendingIds = Object.keys(pending);
  const resolvedIds = Object.keys(resolved);

  const MAX_VISIBLE_RESOLVED = 3;
  const collapsed = resolvedIds.length > MAX_VISIBLE_RESOLVED && !historyExpanded;
  const visibleResolved = collapsed ? resolvedIds.slice(-MAX_VISIBLE_RESOLVED) : resolvedIds;

  if (pendingIds.length === 0 && resolvedIds.length === 0) return null;

  return (
    <div className="w-full flex flex-col gap-2" data-testid="confirm-card-area">
      {/* Active card(s) — full interactive ConfirmCard */}
      {pendingIds.map((id) => {
        const entry = pending[id];
        return (
          <div key={id} className="flex gap-2.5 animate-[msgIn_.35s_cubic-bezier(.22,.8,.32,1)]">
            <span
              className="w-8 h-8 rounded-[9px] mt-0.5 flex items-center justify-center text-xs shrink-0"
              style={{
                background: 'linear-gradient(135deg, var(--accent), var(--accent-hover))',
                color: '#fff',
                boxShadow: '0 1px 2px rgba(18,18,18,.04),0 2px 10px rgba(18,18,18,.06)',
              }}
            >
              AI
            </span>
            <div className="min-w-0">
              <ConfirmCard
                entry={entry}
                onResolve={(choiceId, choiceLabel, remember) =>
                  resolve(id, choiceId, choiceLabel, remember)
                }
                onTimeout={() => timeoutCard(id)}
              />
            </div>
          </div>
        );
      })}

      {/* Resolved history — compact, de-emphasized, collapses beyond 3 */}
      {resolvedIds.length > 0 && (
        <div className="flex flex-col gap-1.5 mt-1" data-testid="confirm-card-resolved">
          {collapsed && (
            <button
              onClick={() => setHistoryExpanded(true)}
              className="text-[11.5px] cursor-pointer hover:underline self-start px-1"
              style={{
                color: 'var(--text-faint)',
                background: 'none',
                border: 'none',
                fontFamily: 'inherit',
              }}
            >
              已处理 {resolvedIds.length} 张确认卡（点击展开）
            </button>
          )}
          {visibleResolved.map((id) => {
            const entry = resolved[id];
            const cancelled = entry.state === 'cancelled';
            return (
              <div
                key={id}
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-[12px] max-w-[560px]"
                style={{
                  border: '1px solid var(--border-subtle)',
                  background: 'var(--surface-muted)',
                  opacity: 0.85,
                }}
              >
                <span className="shrink-0 text-[11px]">
                  {entry.backendReleased ? '⏹' : entry.timedOut ? '⏱' : cancelled ? '○' : '✓'}
                </span>
                <span className="font-medium truncate" style={{ color: 'var(--text-muted)' }}>
                  {entry.request.title}
                </span>
                <span
                  className="font-semibold ml-auto shrink-0"
                  style={{ color: cancelled ? 'var(--text-muted)' : 'var(--success-text)' }}
                >
                  {entry.backendReleased
                    ? '已关闭（后端已释放）'
                    : entry.timedOut
                      ? '已超时'
                      : cancelled
                        ? '已取消'
                        : `已选择「${entry.choiceLabel ?? '确认'}」`}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
