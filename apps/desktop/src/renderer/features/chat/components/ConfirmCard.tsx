import React, { useEffect, useMemo, useRef } from 'react';
import { Check, X } from 'lucide-react';
import type { ConfirmChoice, ConfirmStep, UserInputCardRequest } from '../../../../shared/ipc';
import type { StepExecStatus, UserInputCardEntry } from '../../../contexts/UserInputContext';
import { HermesConfirmBar, type HermesConfirmChoice } from './HermesConfirmBar';
import { HermesToolRow, type ToolRowStatus } from './HermesToolRow';

/**
 * ConfirmCard — 确认卡（Hermes 工具行式，2026-08-27 用户"参考 hermes"）。
 *
 * Hermes fallback.tsx ToolEntry：状态字形 + 小字标题 + meta + 行下审批条 +
 * 展开区。无白卡容器（成功静默）。choices 映射：confirm→Run / adjust→修改
 * 计划 / cancel→拒绝；自定义 choices 渲染在展开区按钮行。
 */
export function ConfirmCard({
  entry,
  onResolve,
  onTimeout,
  initialExpanded,
}: {
  entry: UserInputCardEntry;
  onResolve: (choiceId: string, rememberMode?: 'session' | 'always' | null) => void;
  onTimeout?: (inputId: string) => void;
  initialExpanded?: boolean;
}) {
  const req = entry.request as unknown as UserInputCardRequest;
  const state = entry.state;
  const steps: ConfirmStep[] = Array.isArray(req.steps) ? req.steps : [];
  const choices: ConfirmChoice[] = Array.isArray(req.choices) ? req.choices : [];
  const timeout = typeof req.timeout_seconds === 'number' ? req.timeout_seconds : 60;
  const isWaiting = state === 'pending';

  const [remaining, setRemaining] = React.useState(timeout);
  const [countdownDone, setCountdownDone] = React.useState(false);
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

  const timedOutNotified = useRef(false);
  useEffect(() => {
    if (countdownDone && !timedOutNotified.current) {
      timedOutNotified.current = true;
      onTimeout?.(req.input_id);
    }
  }, [countdownDone, onTimeout, req.input_id]);

  const timedOut = isWaiting && countdownDone;
  const effectiveState = timedOut ? 'cancelled' : state;
  const effectiveWaiting = isWaiting && !timedOut;

  const fmtCountdown = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, '0')}`;
  };

  const status: ToolRowStatus = effectiveWaiting ? 'pending' : 'success';
  const resolvedTitle =
    effectiveState === 'pending'
      ? req.title
      : timedOut
        ? req.title.replace(/^确认/, '已超时').replace(/[？?]$/, '')
        : effectiveState === 'modify'
          ? req.title.replace(/^确认/, '已修改').replace(/[？?]$/, '')
          : req.title
              .replace(/^确认/, effectiveState === 'confirmed' ? '已确认' : '已取消')
              .replace(/[？?]$/, '');

  const receiptJsx = !effectiveWaiting ? (
    <div
      className="flex w-full items-center gap-3 rounded-xl border px-4 py-2.5"
      style={{ background: 'rgba(255,255,255,0.6)', borderColor: '#e0e0e0' }}
      data-testid="confirm-card"
      data-receipt="true"
      role="status"
    >
      <span
        className="flex size-7 shrink-0 items-center justify-center rounded-full"
        style={{ background: effectiveState === 'confirmed' ? 'rgba(46,164,95,0.12)' : '#f0f0f0' }}
      >
        {effectiveState === 'confirmed' ? (
          <Check size={14} style={{ color: '#2ea45f' }} />
        ) : (
          <X size={14} style={{ color: '#a0a6b0' }} />
        )}
      </span>
      <div className="flex min-w-0 flex-col">
        <span className="text-[13px] font-medium leading-tight" style={{ color: '#333' }}>
          {effectiveState === 'confirmed'
            ? '已确认'
            : effectiveState === 'modify'
              ? '已修改'
              : timedOut
                ? '已超时'
                : '已取消'}
        </span>
        <span className="text-[12px] break-words" style={{ color: '#6b7280' }}>
          {resolvedTitle}
        </span>
      </div>
    </div>
  ) : null;

  const meta = (
    <span className="tabular-nums" style={{ color: '#a0a6b0' }}>
      ⏱ {fmtCountdown(remaining)} 后自动取消
    </span>
  );

  const roleOf = (r: string | undefined) => r as 'adjust' | 'cancel' | undefined;
  const isAdjust = (c: { id: string; role?: string }) =>
    roleOf(c.role) === 'adjust' || c.id === 'adjust';
  const isCancel = (c: { id: string; role?: string }) =>
    roleOf(c.role) === 'cancel' || c.id === 'cancel';
  const adjustChoice = choices.find(isAdjust);
  const cancelChoice = choices.find(isCancel);
  const confirmChoice = choices.find((c) => !isAdjust(c) && !isCancel(c));
  const customChoices = choices.filter(
    (c) => c.id !== confirmChoice?.id && c.id !== adjustChoice?.id && c.id !== cancelChoice?.id
  );

  const handleBarResolve = (
    choice: HermesConfirmChoice,
    rememberMode?: 'session' | 'always' | null
  ) => {
    if (choice === 'confirm' || choice === 'session' || choice === 'always') {
      onResolve(confirmChoice?.id ?? choices[0]?.id ?? '', rememberMode);
    } else if (choice === 'modify') {
      onResolve(adjustChoice?.id ?? 'modify', rememberMode);
    } else if (choice === 'deny') {
      onResolve(cancelChoice?.id ?? 'cancel', rememberMode);
    }
  };

  const runLabel = confirmChoice?.label ?? '确认';
  const stepsStatusMap = (entry.stepsStatus ?? {}) as Record<string, StepExecStatus | { status?: string }>;

  if (receiptJsx) return receiptJsx;

  return (
    <HermesToolRow
      title={resolvedTitle}
      status={status}
      meta={meta}
      testid="confirm-card"
      defaultOpen={initialExpanded}
      approval={
        effectiveWaiting ? (
          <HermesConfirmBar
            runLabel={runLabel}
            onResolve={handleBarResolve}
            allowModify={!!adjustChoice}
            denyLabel={cancelChoice?.label ?? '取消'}
            description={req.message || undefined}
          />
        ) : undefined
      }
    >
      <div className="flex flex-col gap-1.5 w-full min-w-0">
        {req.message && (
          <div className="text-[11.5px] leading-[1.6] break-words" style={{ color: '#6b7280' }}>
            {req.message}
          </div>
        )}
        {steps.length > 0 && (
          <div className="flex flex-col gap-0.5">
            {steps.map((s, i) => (
              <div key={s.id} className="flex items-center gap-1.5 min-w-0">
                <span className="shrink-0 text-[11px] tabular-nums" style={{ color: '#a0a6b0' }}>
                  {(stepsStatusMap[s.id]?.status === 'running' ? '◌' : stepsStatusMap[s.id]?.status === 'done' || stepsStatusMap[s.id]?.status === 'success' ? '✓' : stepsStatusMap[s.id]?.status === 'failed' ? '✕' : '·')} {String(i + 1).padStart(2, '0')}
                </span>
                <span className="text-[11.5px] break-words" style={{ color: '#333' }}>
                  {(s as { name?: string }).name ?? (s as { title?: string }).title ?? ''}
                </span>
              </div>
            ))}
          </div>
        )}
        {customChoices.length > 0 && (
          <div className="flex flex-col gap-1 pt-1">
            {customChoices.map((c) => (
              <button
                key={c.id}
                onClick={() => onResolve(c.id)}
                className="text-left text-[12px] px-2 py-1.5 rounded-[6px] transition-colors hover:bg-[var(--surface-muted)]"
                style={{ background: '#f5f5f5', border: '1px solid #e0e0e0', color: '#333' }}
              >
                {c.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </HermesToolRow>
  );
}
