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
  return goal.replace(/[，,]\s*上传到\s+(?:Qraft|MiqroForge|MiQroForge)\s*$/, '').trim();
}

/**
 * PlanCard is intentionally a part of the agent work stream, not a modal-like
 * permission surface. The plan explains intent, can be edited inline, and then
 * disappears into the normal execution history once the user decides.
 *
 * 2026-09-15 定稿（用户：学 Hermes「就是一张卡片」+ 按钮学 WorkBuddy 做大）：
 * 整张卡是一个白底描边圆角容器，标题/状态/步骤/按钮都在卡内，按钮 36px 高。
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
  // #646-v2 UI 定稿：执行中可收起步骤块（大卡里的子项行折起来），状态行仍报进度。
  const [collapsed, setCollapsed] = useState(false);

  // 进度数字只在后端真的给了步骤状态时才显示：`stepStatus` 目前无人填充
  // （全仓 py 零命中，后端只发 steps[].tools），无条件显示会永远停在
  // 「执行中 0/N」——比不显示更误导。填充后数字会自动出现。
  const hasStepProgress = Object.keys(entry.stepStatus ?? {}).length > 0;
  const doneCount = entry.steps.filter((s) => entry.stepStatus?.[s.name] === 'done').length;
  const statusLabel = done
    ? '已完成'
    : cancelled
      ? '已取消'
      : modified
        ? '已调整'
        : running
          ? hasStepProgress
            ? `执行中 ${doneCount}/${entry.steps.length}`
            : '执行中'
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
    <section data-testid="plan-card" className="w-full max-w-[720px]" aria-label="任务计划">
      {/* 一张卡：白底 + 细描边 + 圆角，标题/步骤/按钮全部收进卡内（Hermes 式）。 */}
      <div
        className="rounded-xl border px-4 py-3.5"
        style={{
          background: 'var(--surface, #ffffff)',
          borderColor: 'var(--border-subtle, #e4e5e8)',
        }}
      >
        {/* 头部行：图标 + 标题 + 状态 */}
        <div className="flex items-center gap-2">
          <span className="grid size-[18px] shrink-0 place-items-center" aria-hidden="true">
            {running ? (
              <Loader2
                size={15}
                className="animate-spin"
                style={{ color: 'var(--accent, #ea653d)' }}
              />
            ) : done ? (
              <Check size={15} style={{ color: '#2ea45f' }} />
            ) : modified ? (
              <PencilLine size={15} style={{ color: 'var(--accent, #ea653d)' }} />
            ) : cancelled ? (
              <X size={15} style={{ color: 'var(--text-faint, #7c7c84)' }} />
            ) : (
              <MessageSquareText size={15} style={{ color: 'var(--accent, #ea653d)' }} />
            )}
          </span>
          <h3
            className="min-w-0 flex-1 truncate text-[13.5px] font-semibold leading-6"
            style={{ color: 'var(--text, #17171a)' }}
          >
            {entry.title || '任务计划'}
          </h3>
          <span
            className="shrink-0 text-[11.5px] leading-5"
            style={{ color: done ? '#2ea45f' : 'var(--text-faint, #7c7c84)' }}
          >
            {statusLabel}
          </span>
          {running && (
            <button
              type="button"
              data-testid="plan-collapse"
              onClick={() => setCollapsed((value) => !value)}
              className="shrink-0 text-[11.5px] leading-5"
              style={{ color: 'var(--text-faint, #7c7c84)' }}
            >
              {collapsed ? '展开' : '收起'}
            </button>
          )}
        </div>

        {goal && (
          <p
            className="mt-1.5 text-[12.5px] leading-5"
            style={{ color: 'var(--text-muted, #4a4a52)' }}
          >
            {goal}
          </p>
        )}

        {/* WorkBuddy 风格：一大卡内是子项行——不是每步一个小卡。 */}
        {shouldShowDetails && !(running && collapsed) && entry.steps.length > 0 && (
          <div
            className="mt-3 rounded-lg px-3 py-2.5"
            style={{ background: 'var(--surface-muted, #f2f3f5)' }}
          >
            <div className="space-y-2">
              {entry.steps.map((step, index) => {
                const stepState =
                  running || done ? (entry.stepStatus?.[step.name] ?? 'pending') : 'pending';
                return (
                  <div
                    key={`${step.name}-${index}`}
                    className="flex items-start gap-2.5 text-[12.5px] leading-5"
                  >
                    <span className="mt-0.5 grid size-4 shrink-0 place-items-center">
                      {stepState === 'done' ? (
                        <Check size={13} style={{ color: '#2ea45f' }} />
                      ) : stepState === 'failed' ? (
                        <X size={12} style={{ color: '#d4544a' }} />
                      ) : stepState === 'running' ? (
                        <Loader2
                          size={13}
                          className="animate-spin"
                          style={{ color: 'var(--accent, #ea653d)' }}
                        />
                      ) : (
                        <Circle size={9} style={{ color: 'var(--text-faint, #b4bac3)' }} />
                      )}
                    </span>
                    <span
                      className="min-w-0 flex-1 break-words"
                      style={{
                        color:
                          stepState === 'done'
                            ? 'var(--text-muted, #4a4a52)'
                            : 'var(--text, #30343b)',
                      }}
                    >
                      {step.name}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {waiting && permissions.length > 0 && (
          <div
            className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[11.5px] leading-5"
            style={{ color: 'var(--text-faint, #7c7c84)' }}
          >
            <span>涉及</span>
            {permissions.map((permission) => (
              <span key={permission}>{permission}</span>
            ))}
          </div>
        )}

        {waiting && !editing && (
          <div className="mt-3.5 flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="plan-confirm"
              onClick={() => onResolve('confirm', '按当前方案执行')}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg px-4 text-[13px] font-medium transition-colors"
              style={{
                background: 'var(--accent, #ea653d)',
                border: '1px solid var(--accent, #ea653d)',
                color: '#fff',
              }}
            >
              按当前方案执行
              <ArrowRight size={14} />
            </button>
            <button
              type="button"
              data-testid="plan-modify"
              onClick={() => setEditing(true)}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg px-4 text-[13px] font-medium transition-colors"
              style={{
                background: 'transparent',
                border: '1px solid var(--border, #dcdde0)',
                color: 'var(--text, #30343b)',
              }}
            >
              调整方案
            </button>
            <button
              type="button"
              data-testid="plan-cancel"
              onClick={() => onResolve('cancel', '取消任务')}
              className="h-9 px-2 text-[13px]"
              style={{ color: 'var(--text-faint, #7c7c84)' }}
            >
              取消
            </button>
          </div>
        )}

        {waiting && editing && (
          <div
            className="mt-3 rounded-lg border p-3"
            style={{
              borderColor: 'var(--border-subtle, #e4e5e8)',
              background: 'var(--surface-muted, #f2f3f5)',
            }}
          >
            <label
              htmlFor="plan-adjustment"
              className="flex items-center gap-1.5 text-[12.5px] font-medium"
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
              placeholder="例如：不要上传 MiqroForge；先完成本地报告，再让我决定是否上传。"
              rows={3}
              className="mt-2 w-full resize-none rounded-md border bg-transparent px-2.5 py-2 text-[12.5px] leading-5 outline-none"
              style={{ borderColor: 'var(--border, #dcdde0)', color: 'var(--text, #30343b)' }}
            />
            <div className="mt-2.5 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  setAdjustment('');
                  setEditing(false);
                }}
                className="text-[12px]"
                style={{ color: 'var(--text-faint, #7c7c84)' }}
              >
                返回
              </button>
              <button
                type="button"
                data-testid="plan-submit-adjustment"
                onClick={submitAdjustment}
                disabled={!adjustment.trim()}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg px-4 text-[13px] font-medium disabled:opacity-40"
                style={{
                  background: 'var(--accent, #ea653d)',
                  border: '1px solid var(--accent, #ea653d)',
                  color: '#fff',
                }}
              >
                提交调整
                <ArrowRight size={14} />
              </button>
            </div>
            <div className="mt-1.5 text-[10.5px]" style={{ color: 'var(--text-faint, #7c7c84)' }}>
              Ctrl/⌘ + Enter 提交
            </div>
          </div>
        )}

        {modified && (
          <div
            className="mt-2 text-[12px] leading-5"
            style={{ color: 'var(--text-muted, #4a4a52)' }}
          >
            已把你的调整意见交给 Agent，它会基于新约束重新规划。
          </div>
        )}
      </div>
    </section>
  );
}
