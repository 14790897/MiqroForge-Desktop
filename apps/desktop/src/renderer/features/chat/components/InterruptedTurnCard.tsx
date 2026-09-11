import { useState } from 'react';
import { ThinkBlock } from './ThinkBlock';

/**
 * #740: 中断的 turn 恢复卡片。
 *
 * 渲染一个被打断的 turn 的半截状态：进度摘要（中断卡）+ 已生成的
 * 思考块 + 半截回答，以及「继续执行 / 重新开始」两个动作。
 * 样式遵循 ChatConsole 消息气泡语言（白底 14px 圆角、accent 主按钮）。
 */
export function InterruptedTurnCard({
  meta,
  reasoning,
  content,
  elapsedSeconds,
  mode,
  onResume,
  onRestart,
}: {
  meta: {
    turnId: string;
    status: string;
    tokenEstimate?: number;
  };
  reasoning?: string;
  content: string;
  /** #834: server-measured thinking proxy from the snapshot; falls back to
   *  1s only when no measurement was ever taken. */
  elapsedSeconds?: number;
  /** #905: reasoning mode persisted on the snapshot — fast rounds restore
   *  as 🚀/快速思考, not ThinkBlock's default 🧠/深度思考. */
  mode?: 'fast' | 'think';
  onResume?: () => void;
  onRestart?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirmingRestart, setConfirmingRestart] = useState(false);
  // Note: snapshots are persisted only as running/interrupted/completed —
  // there is no 'aborted' state, so the card always renders the interrupted
  // (recoverable) presentation.

  const handleResume = () => {
    if (busy) return;
    setBusy(true);
    onResume?.();
    // 恢复请求已发出——流式事件会接管渲染；若回调未触发（无后端），
    // 短延迟后复位按钮以免永久卡在"继续中"。
    setTimeout(() => setBusy(false), 1500);
  };

  const handleRestart = () => {
    if (confirmingRestart) {
      onRestart?.();
      setConfirmingRestart(false);
    } else {
      setConfirmingRestart(true);
      setTimeout(() => setConfirmingRestart(false), 2500);
    }
  };

  return (
    <div className="my-1 flex min-w-0">
      <div className="w-4 flex flex-col items-center self-stretch" aria-hidden>
        <span className="text-[13px] leading-[1.2]">⚠️</span>
        <span
          className="mt-[3px] w-[2px] flex-1 min-h-2 rounded-full"
          style={{ background: 'var(--border-subtle)' }}
        />
      </div>
      <div className="min-w-0 flex-1 pl-2">
        {/* 中断卡：进度摘要 */}
        <div
          className="mb-2 max-w-[600px] rounded-xl border p-3.5"
          style={{ borderColor: 'var(--border-subtle)', background: 'var(--surface)' }}
        >
          <div
            className="flex items-center gap-2 text-[13.5px] font-bold"
            style={{ color: 'var(--text)' }}
          >
            <span>⚠️ 任务被中断</span>
            <span
              className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-semibold"
              style={{ background: 'rgba(245,158,11,0.12)', color: 'var(--warning)' }}
            >
              <span
                className="inline-block h-[6px] w-[6px] rounded-full"
                style={{ background: '#b45309' }}
              />
              未完成
            </span>
          </div>
          {meta.tokenEstimate ? (
            <div className="mt-1.5 text-[11.5px]" style={{ color: 'var(--text-faint)' }}>
              已生成约 {meta.tokenEstimate} tokens
            </div>
          ) : null}
          <div className="mt-2 flex gap-2">
            <button
              className="btn-resume inline-flex items-center gap-1.5 rounded-lg px-3.5 py-1.5 text-[12.5px] font-semibold text-white disabled:opacity-55"
              style={{ background: 'var(--accent)' }}
              onClick={handleResume}
              disabled={busy}
            >
              {busy ? (
                <>
                  <span
                    className="inline-block h-[11px] w-[11px] rounded-full border-2 border-white/40 border-t-white"
                    style={{ animation: 'turn-spin .8s linear infinite' }}
                  />
                  继续中…
                </>
              ) : (
                <>▶ 继续执行</>
              )}
            </button>
            <button
              className="inline-flex items-center rounded-lg border px-3.5 py-1.5 text-[12.5px] font-semibold"
              style={{
                background: 'var(--surface-muted)',
                color: 'var(--text-muted)',
                borderColor: 'var(--border-subtle)',
              }}
              onClick={handleRestart}
            >
              {confirmingRestart ? '确认重新开始？再点一次' : '重新开始'}
            </button>
          </div>
        </div>

        {/* 已生成的思考块（可展开） */}
        {reasoning ? (
          <ThinkBlock
            reasoning={reasoning}
            defaultOpen={false}
            elapsedSeconds={elapsedSeconds}
            mode={mode}
          />
        ) : null}

        {/* 半截回答 */}
        {content ? (
          <div
            className="text-[13.5px] leading-relaxed whitespace-pre-wrap break-words"
            style={{ color: 'var(--text)' }}
          >
            {content}
            <span
              className="ml-0.5 inline-block h-[14px] w-[2px] animate-pulse rounded-sm align-[-2px]"
              style={{ background: 'var(--accent)' }}
            />
          </div>
        ) : (
          <div className="text-[13px]" style={{ color: 'var(--text-faint)' }}>
            回答尚未开始生成。
          </div>
        )}
      </div>
      <style>{`@keyframes turn-spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
