import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { ConfirmChoice, ConfirmStep, UserInputCardRequest } from '../../../../shared/ipc';
import type { StepExecStatus, UserInputCardEntry } from '../../../contexts/UserInputContext';

/**
 * ConfirmCard — AI-initiated human-in-the-loop card (issue #646).
 *
 * Rendered inline in the message flow (NOT a modal). The model calls
 * ask_user_confirm_card → the card appears in WAITING state and the turn
 * pauses; the user picks a choice → the choice is returned to the backend as
 * a tool result → the card stays in the flow as a resolved record.
 *
 * States follow the v5 prototype semantics:
 *   pending   → blue accent, choices + remember + countdown (strongest)
 *   confirmed → light green, "已选择「xxx」· time" (medium)
 *   cancelled → grey, neutral end state, NOT an error (weakest)
 */
export function ConfirmCard({
  entry,
  onResolve,
  onTimeout,
  nowFn = () => new Date().toLocaleTimeString('zh-CN', { hour12: false }),
}: {
  entry: UserInputCardEntry;
  onResolve: (choiceId: string, choiceLabel: string, remember: boolean) => void;
  /** Fired once when the local countdown hits zero (legacy path). */
  onTimeout?: (inputId: string) => void;
  nowFn?: () => string;
}) {
  const req = entry.request;
  const state = entry.state;
  const isWaiting = state === 'pending';

  const choices: ConfirmChoice[] =
    req.choices && req.choices.length > 0 ? req.choices : DEFAULT_CHOICES;
  const steps: ConfirmStep[] = req.steps ?? [];

  // ── countdown (pending only) ────────────────────────────────────
  const timeout = req.timeout_seconds ?? 120;
  const [remaining, setRemaining] = useState(timeout);
  const [countdownDone, setCountdownDone] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!isWaiting) return;
    setRemaining(timeout);
    setCountdownDone(false);
    timerRef.current = setInterval(() => {
      setRemaining((r) => {
        const next = Math.max(0, r - 1);
        if (next <= 0) {
          if (timerRef.current) clearInterval(timerRef.current);
          setCountdownDone(true);
        }
        return next;
      });
    }, 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isWaiting, timeout]);

  // 超时：通知 context 把卡移到 resolved（legacy 路径无 resolved 事件）
  const timedOutNotified = useRef(false);
  useEffect(() => {
    if (countdownDone && !timedOutNotified.current) {
      timedOutNotified.current = true;
      onTimeout?.(req.input_id);
    }
  }, [countdownDone, onTimeout, req.input_id]);

  // 超时：前端本地态（后端 gate 也会超时发 resolved 事件，这里先展示）
  const timedOut = isWaiting && countdownDone;
  const effectiveState = timedOut ? 'cancelled' : state;
  const effectiveWaiting = isWaiting && !timedOut;

  // ── remember choice (pending only) ──────────────────────────────
  const [remember, setRemember] = useState(false);

  // ── steps collapse: <=5 全展示，>5 折叠（AI review: 渐进式披露） ─
  const MAX_VISIBLE_STEPS = 5;
  const [stepsExpanded, setStepsExpanded] = useState(false);
  const stepsCollapsed = steps.length > MAX_VISIBLE_STEPS && !stepsExpanded;
  const visibleSteps = stepsCollapsed ? steps.slice(0, MAX_VISIBLE_STEPS) : steps;

  const badgeStyle =
    effectiveState === 'pending'
      ? { background: 'var(--accent-soft)', color: 'var(--accent-hover)' }
      : effectiveState === 'confirmed'
        ? { background: 'var(--success-bg)', color: 'var(--success-text)' }
        : { background: 'var(--surface-3)', color: 'var(--text-muted)' };
  // P0 (AI review): pending = 边框强调 + 弱 shadow（不是蓝 glow 呼吸灯），
  // confirmed 保持中性背景（只有 badge 变绿），cancelled 中性灰。
  const borderClass = effectiveWaiting
    ? { borderColor: 'var(--accent)', boxShadow: '0 2px 14px rgba(51,156,255,.10)' }
    : effectiveState === 'confirmed'
      ? { borderColor: 'var(--border-subtle)', boxShadow: 'none' }
      : {
          borderColor: 'var(--border-subtle)',
          boxShadow: 'none',
          background: 'var(--surface-muted)',
          opacity: 0.85,
        };

  const doneCount = steps.filter((x) => entry.stepsStatus?.[x.id]?.status === 'success').length;
  const progressPct = steps.length > 0 ? Math.round((doneCount / steps.length) * 100) : 0;

  return (
    <div
      className="rounded-xl p-4 max-w-[600px] relative overflow-hidden"
      style={{
        border: '1px solid var(--border-subtle)',
        ...borderClass,
        transition: 'all .35s cubic-bezier(.22,.8,.32,1)',
      }}
    >
      {/* top accent line (pending: 细条呼吸，非 glow) */}
      <div
        className="absolute top-0 left-0 right-0"
        style={{
          height: 2,
          background:
            effectiveState === 'pending'
              ? 'linear-gradient(90deg, var(--accent), transparent)'
              : effectiveState === 'confirmed'
                ? 'linear-gradient(90deg, var(--success), transparent)'
                : 'none',
          animation: effectiveWaiting ? 'accent-pulse 1.8s ease-in-out infinite' : 'none',
        }}
      />
      {/* head */}
      <div className="flex items-center gap-2.5 mb-2">
        <span
          className="w-[26px] h-[26px] rounded-lg flex items-center justify-center text-sm shrink-0"
          style={{
            background: effectiveWaiting
              ? 'var(--accent-soft)'
              : effectiveState === 'confirmed'
                ? 'var(--success-bg)'
                : 'var(--surface-3)',
            color: effectiveWaiting
              ? 'var(--accent-hover)'
              : effectiveState === 'confirmed'
                ? 'var(--success-text)'
                : 'var(--text-faint)',
          }}
        >
          {req.title?.includes('上传') ? '📤' : req.title?.includes('补充') ? '❓' : '📋'}
        </span>
        <span
          className="text-[14.5px] font-semibold tracking-[.01em]"
          style={{
            color: effectiveState === 'cancelled' || timedOut ? 'var(--text-muted)' : 'inherit',
          }}
        >
          {req.title}
        </span>
        <span
          className="ml-auto text-[11px] font-semibold rounded-full px-2.5 py-0.5 whitespace-nowrap inline-flex items-center gap-1.5"
          style={badgeStyle}
        >
          {effectiveWaiting && (
            <span
              className="w-[6px] h-[6px] rounded-full inline-block"
              style={{
                background: 'var(--accent)',
                animation: 'turn-pulse 1.1s ease-in-out infinite',
              }}
            />
          )}
          {timedOut
            ? '⏱ 已超时'
            : entry.backendReleased
              ? '⏹ 已关闭'
              : effectiveState === 'pending'
                ? '等待你的选择'
                : effectiveState === 'confirmed'
                  ? '✓ 已确认'
                  : '已取消'}
        </span>
      </div>

      {/* message */}
      <div className="text-[13px] mb-3" style={{ color: 'var(--text-muted)' }}>
        {req.message}
      </div>

      {/* steps: plan 态（数字圆点，pending） / live 态（✓⟳○ + 详情，confirmed 后） */}
      {steps.length > 0 && effectiveState === 'pending' && (
        <div className="flex flex-col gap-1.5 mb-3">
          {visibleSteps.map((s, i) => (
            <div key={s.id} className="flex gap-2.5 items-baseline text-[13px] py-0.5">
              <span
                className="w-[18px] h-[18px] rounded-full flex items-center justify-center text-[10.5px] font-semibold shrink-0"
                style={{ background: 'var(--surface-3)', color: 'var(--text-faint)' }}
              >
                {i + 1}
              </span>
              <span className="flex-1">{s.title}</span>
            </div>
          ))}
          {stepsCollapsed && (
            <button
              onClick={() => setStepsExpanded(true)}
              className="text-[11.5px] cursor-pointer hover:underline self-center mt-1"
              style={{
                color: 'var(--accent-hover)',
                background: 'none',
                border: 'none',
                fontFamily: 'inherit',
              }}
            >
              展开全部 {steps.length} 个步骤
            </button>
          )}
        </div>
      )}

      {/* steps: live 态（确认后同卡转执行态，v5 + 整体进度条） */}
      {steps.length > 0 && effectiveState === 'confirmed' && !timedOut && (
        <div className="flex flex-col gap-1.5 mb-3" data-testid="steps-live">
          {/* 整体进度条（AI review: 进度一目了然） */}
          <div
            className="h-[3px] rounded-full overflow-hidden mb-1.5"
            style={{ background: 'var(--surface-3)' }}
          >
            <div
              className="h-full rounded-full"
              style={{
                width: `${progressPct}%`,
                background: 'var(--accent)',
                transition: 'width .5s cubic-bezier(.22,.8,.32,1)',
              }}
            />
          </div>
          {steps.map((s, i) => {
            const st = entry.stepsStatus?.[s.id] ?? { status: 'pending' };
            const ico =
              st.status === 'running'
                ? '⟳'
                : st.status === 'success'
                  ? '✓'
                  : st.status === 'failed'
                    ? '!'
                    : '○';
            const sub =
              st.status === 'running' ? (
                <span className="text-[11px]" style={{ color: 'var(--accent-hover)' }}>
                  正在执行…
                </span>
              ) : st.status === 'success' ? (
                <span className="text-[11px]" style={{ color: 'var(--success-text)' }}>
                  <span style={{ color: 'var(--success)' }}>✓</span> {st.result ?? '已完成'}
                  {st.dur ? (
                    <span style={{ color: 'var(--text-faint)' }}> · ⏱ {st.dur}</span>
                  ) : null}
                </span>
              ) : st.status === 'failed' ? (
                <span className="text-[11px]" style={{ color: 'var(--danger)' }}>
                  执行失败
                </span>
              ) : (
                <span className="text-[11px]" style={{ color: 'var(--text-faint)' }}>
                  等待执行
                </span>
              );
            return <StepLiveRow key={s.id} index={i} step={s} st={st} sub={sub} ico={ico} />;
          })}
          <span className="text-[11px] tabular-nums mt-0.5" style={{ color: 'var(--text-faint)' }}>
            已完成 {steps.filter((x) => entry.stepsStatus?.[x.id]?.status === 'success').length} /{' '}
            {steps.length}
          </span>
        </div>
      )}

      {/* pending-only: choices（主/次按钮：确认=实心，调整=描边，取消=文字）。
          Variant by semantic role with the literal id as fallback so a
          backend {id:'abort', role:'cancel'} renders as a cancel (#646). */}
      {effectiveWaiting && (
        <div className="flex gap-2 flex-wrap items-center">
          {choices.map((c) => {
            const role =
              c.role ?? (c.id === 'cancel' ? 'cancel' : c.id === 'adjust' ? 'adjust' : undefined);
            if (role === 'cancel') {
              return (
                <button
                  key={c.id}
                  data-testid="confirm-card-choice"
                  onClick={() => onResolve(c.id, c.label, remember)}
                  className="text-[12.5px] font-medium px-2.5 py-1.5 cursor-pointer transition-all rounded-lg"
                  style={{
                    background: 'none',
                    border: 'none',
                    color: 'var(--text-faint)',
                    fontFamily: 'inherit',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.color = 'var(--text-muted)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.color = 'var(--text-faint)';
                  }}
                >
                  {c.label}
                </button>
              );
            }
            if (role === 'adjust') {
              return (
                <button
                  key={c.id}
                  data-testid="confirm-card-choice"
                  onClick={() => onResolve(c.id, c.label, remember)}
                  className="text-[12.5px] font-medium rounded-lg px-3.5 py-1.5 cursor-pointer transition-all"
                  style={{
                    border: '1px solid var(--border)',
                    background: 'var(--surface)',
                    color: 'var(--text-muted)',
                    fontFamily: 'inherit',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = 'var(--accent)';
                    e.currentTarget.style.color = 'var(--accent-hover)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = 'var(--border)';
                    e.currentTarget.style.color = 'var(--text-muted)';
                  }}
                >
                  {c.label}
                </button>
              );
            }
            return (
              <button
                key={c.id}
                data-testid="confirm-card-primary"
                onClick={() => onResolve(c.id, c.label, remember)}
                className="text-[13px] font-semibold rounded-lg px-4 py-1.5 cursor-pointer transition-all"
                style={{
                  border: '1px solid var(--accent)',
                  background: 'var(--accent)',
                  color: '#fff',
                  fontFamily: 'inherit',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--accent-hover)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'var(--accent)';
                }}
              >
                {c.label}
              </button>
            );
          })}
        </div>
      )}

      {/* pending-only: remember + countdown */}
      {effectiveWaiting && (
        <div
          className="flex items-center gap-3.5 mt-3 text-xs flex-wrap"
          style={{ color: 'var(--text-faint)' }}
        >
          {req.allow_remember_choice && (
            <label
              className="flex items-center gap-1.5 cursor-pointer select-none"
              style={{ color: 'var(--text-muted)' }}
            >
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              以后自动处理类似操作
            </label>
          )}
          <div className="flex items-center gap-2 flex-1 min-w-[140px]">
            <div
              className="flex-1 h-1 rounded-sm overflow-hidden"
              style={{ background: 'var(--surface-3)' }}
            >
              <div
                className="h-full rounded-sm"
                style={{
                  width: `${(remaining / timeout) * 100}%`,
                  background: remaining <= 5 ? 'var(--danger)' : 'var(--accent)',
                  transition: 'width 1s linear',
                }}
              />
            </div>
            <span
              className="text-[11px] tabular-nums w-[30px] text-right"
              style={{ color: remaining <= 5 ? 'var(--danger)' : 'var(--text-muted)' }}
            >
              {remaining}s
            </span>
          </div>
        </div>
      )}

      {/* resolved / timed-out: what the user picked (chip, v5-style) */}
      {!effectiveWaiting && (
        <div
          className="flex items-center gap-2.5 mt-3 pt-3"
          style={{
            borderTop: '1px dashed var(--border-subtle)',
            animation: 'msgIn .3s cubic-bezier(.22,.8,.32,1)',
          }}
        >
          {entry.backendReleased ? (
            <span
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-[12px] font-semibold"
              style={{ background: 'var(--surface-3)', color: 'var(--text-muted)' }}
            >
              <span style={{ fontSize: 11 }}>⏹</span>
              已关闭 · 后端已释放该确认
            </span>
          ) : timedOut ? (
            <span
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-[12px] font-semibold"
              style={{ background: 'var(--surface-3)', color: 'var(--text-muted)' }}
            >
              <span style={{ fontSize: 11 }}>⏱</span>
              等待超时，已自动取消
            </span>
          ) : (
            <span
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-[12px] font-semibold"
              style={{
                background: state === 'confirmed' ? 'var(--success-bg)' : 'var(--surface-3)',
                color: state === 'confirmed' ? 'var(--success-text)' : 'var(--text-muted)',
              }}
            >
              <span style={{ fontSize: 11 }}>{state === 'confirmed' ? '✓' : '○'}</span>
              已选择「{entry.choiceLabel ?? (state === 'cancelled' ? '取消' : '确认')}」
            </span>
          )}
          <span className="text-[11px] tabular-nums" style={{ color: 'var(--text-faint)' }}>
            {entry.resolvedAt
              ? new Date(entry.resolvedAt).toLocaleTimeString('zh-CN', { hour12: false })
              : nowFn()}
          </span>
        </div>
      )}

      {/* lock note (v5): resolved cards are immutable history */}
      {!effectiveWaiting && (
        <div className="mt-2 text-[10.5px]" style={{ color: 'var(--text-faint)' }}>
          {entry.backendReleased
            ? '🔒 已关闭 · 后端已释放该请求（超时或回合已结束）'
            : '🔒 已完成 · 本次选择已记录'}
        </div>
      )}
    </div>
  );
}

/** 单步 live 行：状态图标 + 子状态 + 可展开 Tool 详情（v5） */
function StepLiveRow({
  index,
  step,
  st,
  sub,
  ico,
}: {
  index: number;
  step: ConfirmStep;
  st: StepExecStatus;
  sub: ReactNode;
  ico: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex gap-2.5 text-[13px] py-0.5">
      <span
        className="w-[18px] h-[18px] rounded-full flex items-center justify-center text-[10.5px] font-semibold shrink-0 mt-0.5"
        style={{
          background:
            st.status === 'success'
              ? 'var(--success-bg)'
              : st.status === 'failed'
                ? 'var(--danger-bg)'
                : 'var(--surface-3)',
          color:
            st.status === 'success'
              ? 'var(--success-text)'
              : st.status === 'failed'
                ? 'var(--danger)'
                : st.status === 'running'
                  ? 'var(--accent-hover)'
                  : 'var(--text-faint)',
        }}
      >
        {ico}
      </span>
      <div className="flex-1 min-w-0">
        <div className="font-medium">
          {index + 1}. {step.title}
        </div>
        {sub}
        {st.status !== 'pending' && (
          <div className="mt-0.5">
            <button
              onClick={() => setOpen(!open)}
              className="text-[10.5px] cursor-pointer hover:underline"
              style={{
                color: 'var(--text-faint)',
                background: 'none',
                border: 'none',
                fontFamily: 'inherit',
              }}
            >
              <span
                style={{
                  display: 'inline-block',
                  transform: open ? 'rotate(90deg)' : 'none',
                  transition: 'transform .2s',
                }}
              >
                ▸
              </span>{' '}
              技术详情
            </button>
            {open && (
              <div
                className="mt-1 rounded-md p-2 text-[11px] flex flex-col gap-1"
                style={{ background: 'var(--surface-3)' }}
              >
                <div>
                  <span className="font-semibold mr-2" style={{ color: 'var(--text-faint)' }}>
                    Tool
                  </span>
                  <span className="font-mono">{st.tool ?? '-'}</span>
                </div>
                <div>
                  <span className="font-semibold mr-2" style={{ color: 'var(--text-faint)' }}>
                    参数
                  </span>
                  <span className="font-mono break-all">{st.param ?? '-'}</span>
                </div>
                <div>
                  <span className="font-semibold mr-2" style={{ color: 'var(--text-faint)' }}>
                    结果
                  </span>
                  <span className="break-all">{st.result ?? '-'}</span>
                </div>
                {st.dur ? (
                  <div>
                    <span className="font-semibold mr-2" style={{ color: 'var(--text-faint)' }}>
                      耗时
                    </span>
                    <span>{st.dur}</span>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const DEFAULT_CHOICES: ConfirmChoice[] = [
  { id: 'confirm', label: '确认执行' },
  { id: 'adjust', label: '调整方案', role: 'adjust' },
  { id: 'cancel', label: '取消', role: 'cancel' },
];
