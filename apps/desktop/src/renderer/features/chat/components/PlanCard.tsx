import { useState } from 'react';
import { ArrowRight, Check, Circle, Loader2, MessageSquareText, PencilLine, X } from 'lucide-react';

export interface PlanCardEntry {
  title: string;
  goal?: string;
  steps: { name: string; tools?: string[] }[];
  permissions: string[];
  phase: 'wait_confirm' | 'running' | 'completed' | 'cancelled' | 'wait_dangerous' | 'modified';
  stepStatus?: Record<string, 'pending' | 'running' | 'done' | 'failed'>;
}

const PERM_LABELS: Record<string, string> = {
  network: '网络',
  network_read: '网络',
  file_write: '文件',
  workspace_write: '工作区',
  shell: '命令',
  exec: '命令',
  external_upload: '外部',
  external_delete: '删除',
  external_message: '外发',
  external_send: '外发',
  payment: '支付',
  process_spawn: '启动进程',
  external_other: '外部',
};

function compactPermissions(permissions: string[]): string[] {
  return [...new Set(permissions.map((p) => PERM_LABELS[p] ?? p).filter(Boolean))];
}

function displayGoal(goal: string): string {
  // Keep the destination in the step list instead of repeating it in the goal.
  return goal.replace(/[，,]\s*上传到\s+Qraft\s*$/, '').trim();
}

/**
 * PlanCard is intentionally a part of the agent work stream, not a modal-like
 * permission surface. The plan explains intent, can be edited inline, and then
 * disappears into the normal execution history once the user decides.
 */
export function PlanCard({
  entry,
  onResolve,
  initialExpanded,
}: {
  entry: PlanCardEntry;
  onResolve: (choiceId: string, choiceLabel?: string) => void;
  initialExpanded?: boolean;
}) {
  const waiting = entry.phase === 'wait_confirm';
  const running = entry.phase === 'running';
  const done = entry.phase === 'completed';
  const cancelled = entry.phase === 'cancelled';
  const modified = entry.phase === 'modified';
  const [editing, setEditing] = useState(false);
  const [adjustment, setAdjustment] = useState('');

  const statusLabel = done
    ? '已完成'
    : cancelled
      ? '已取消'
      : modified
        ? '已调整'
        : running
          ? '执行中'
          : '等待你的决定';

  const permissions = compactPermissions(entry.permissions);
  const shouldShowDetails = waiting || running || initialExpanded;

  const submitAdjustment = () => {
    const text = adjustment.trim();
    if (!text) return;
    onResolve('modify', text);
  };

  const goal = entry.goal ? displayGoal(entry.goal) : '';

  return (
    <section
      data-testid="plan-card"
      className="w-full max-w-[720px] rounded-xl border px-4 py-3"
      style={{
        background: 'var(--surface, #fff)',
        borderColor: 'var(--border, #e5e7eb)',
        boxShadow: '0 1px 2px rgba(0,0,0,.03)',
      }}
      aria-label="任务计划"
    >
      <div className="flex items-start gap-3">
        <div
          className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg"
          style={{
            background: waiting ? 'var(--accent-soft, #eef5ff)' : 'var(--surface-muted, #f5f6f8)',
          }}
        >
          {running ? (
            <Loader2
              size={15}
              className="animate-spin"
              style={{ color: 'var(--accent, #2a7de1)' }}
            />
          ) : done ? (
            <Check size={15} style={{ color: '#2ea45f' }} />
          ) : modified ? (
            <PencilLine size={15} style={{ color: 'var(--accent, #2a7de1)' }} />
          ) : cancelled ? (
            <X size={15} style={{ color: 'var(--text-faint, #9aa0a8)' }} />
          ) : (
            <MessageSquareText size={15} style={{ color: 'var(--accent, #2a7de1)' }} />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3
              className="min-w-0 flex-1 text-[13px] font-semibold leading-5"
              style={{ color: 'var(--text, #1f2328)' }}
            >
              {entry.title || '任务计划'}
            </h3>
            <span
              className="shrink-0 text-[11px]"
              style={{ color: done ? '#2ea45f' : 'var(--text-faint, #9aa0a8)' }}
            >
              {statusLabel}
            </span>
          </div>

          {goal && (
            <p
              className="mt-1 text-[12px] leading-5"
              style={{ color: 'var(--text-muted, #6b7280)' }}
            >
              {goal}
            </p>
          )}

          {shouldShowDetails && (
            <div className="mt-2.5 space-y-1.5">
              {entry.steps.map((step, index) => {
                const stepState =
                  running || done ? (entry.stepStatus?.[step.name] ?? 'pending') : 'pending';
                return (
                  <div
                    key={`${step.name}-${index}`}
                    className="flex items-start gap-2.5 text-[12px] leading-5"
                  >
                    <span className="mt-0.5 grid size-4 shrink-0 place-items-center">
                      {stepState === 'done' ? (
                        <Check size={13} style={{ color: '#2ea45f' }} />
                      ) : stepState === 'running' ? (
                        <Loader2
                          size={13}
                          className="animate-spin"
                          style={{ color: 'var(--accent, #2a7de1)' }}
                        />
                      ) : (
                        <Circle size={10} style={{ color: 'var(--text-faint, #b4bac3)' }} />
                      )}
                    </span>
                    <span
                      className="min-w-0 flex-1 break-words"
                      style={{
                        color:
                          stepState === 'done'
                            ? 'var(--text-muted, #6b7280)'
                            : 'var(--text, #30343b)',
                      }}
                    >
                      {step.name}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {waiting && permissions.length > 0 && (
            <div
              className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11px]"
              style={{ color: 'var(--text-faint, #8d949d)' }}
            >
              <span>涉及</span>
              {permissions.map((permission) => (
                <span key={permission}>{permission}</span>
              ))}
            </div>
          )}

          {waiting && !editing && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                data-testid="plan-confirm"
                onClick={() => onResolve('confirm', '按当前方案执行')}
                className="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium"
                style={{
                  background: 'var(--accent, #2a7de1)',
                  border: '1px solid var(--accent, #2a7de1)',
                  color: '#fff',
                }}
              >
                按当前方案执行
                <ArrowRight size={12} />
              </button>
              <button
                type="button"
                data-testid="plan-modify"
                onClick={() => setEditing(true)}
                className="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium"
                style={{
                  background: 'transparent',
                  border: '1px solid var(--border, #e1e5ea)',
                  color: 'var(--text, #30343b)',
                }}
              >
                调整方案
              </button>
              <button
                type="button"
                data-testid="plan-cancel"
                onClick={() => onResolve('cancel', '取消任务')}
                className="h-7 px-1.5 text-[11.5px]"
                style={{ color: 'var(--text-faint, #9aa0a8)' }}
              >
                取消
              </button>
            </div>
          )}

          {waiting && editing && (
            <div
              className="mt-3 rounded-lg border p-2.5"
              style={{
                borderColor: 'var(--border, #e1e5ea)',
                background: 'var(--surface-muted, #f8f9fb)',
              }}
            >
              <label
                htmlFor="plan-adjustment"
                className="flex items-center gap-1.5 text-[11.5px] font-medium"
                style={{ color: 'var(--text, #30343b)' }}
              >
                <PencilLine size={13} />
                你希望怎么调整？
              </label>
              <textarea
                id="plan-adjustment"
                data-testid="plan-adjustment-input"
                autoFocus
                value={adjustment}
                onChange={(event) => setAdjustment(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                    event.preventDefault();
                    submitAdjustment();
                  }
                }}
                placeholder="例如：不要上传 Qraft；先完成本地报告，再让我决定是否上传。"
                rows={3}
                className="mt-2 w-full resize-none rounded-md border bg-transparent px-2.5 py-2 text-[12px] leading-5 outline-none"
                style={{ borderColor: 'var(--border, #dfe3e8)', color: 'var(--text, #30343b)' }}
              />
              <div className="mt-2 flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setAdjustment('');
                    setEditing(false);
                  }}
                  className="text-[11px]"
                  style={{ color: 'var(--text-faint, #9aa0a8)' }}
                >
                  返回
                </button>
                <button
                  type="button"
                  data-testid="plan-submit-adjustment"
                  onClick={submitAdjustment}
                  disabled={!adjustment.trim()}
                  className="inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[11.5px] font-medium disabled:opacity-40"
                  style={{
                    background: 'var(--accent, #2a7de1)',
                    border: '1px solid var(--accent, #2a7de1)',
                    color: '#fff',
                  }}
                >
                  提交调整
                  <ArrowRight size={12} />
                </button>
              </div>
              <div className="mt-1 text-[10px]" style={{ color: 'var(--text-faint, #9aa0a8)' }}>
                Ctrl/⌘ + Enter 提交
              </div>
            </div>
          )}

          {modified && (
            <div className="mt-2 text-[11.5px]" style={{ color: 'var(--text-muted, #6b7280)' }}>
              已把你的调整意见交给 Agent，它会基于新约束重新规划。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
