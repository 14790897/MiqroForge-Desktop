import { Loader2 } from 'lucide-react';

import type { UserInputCardRequest } from '../../../../shared/ipc';
import { HermesToolRow } from './HermesToolRow';

export interface TimelineEntry {
  title: string;
  goal: string;
  steps: { name: string; tools?: string[] }[];
  permissions: string[];
  phase?: string;
  stepStatus?: Record<string, string>;
  todoItems?: { id: string; title: string; status: string }[];
  todoRevision?: number;
}

const PERM_META: Record<string, { icon: string; label: string }> = {
  network_read: { icon: '🌐', label: '网络访问' },
  workspace_write: { icon: '📄', label: '创建/修改文件' },
  exec: { icon: '⚙️', label: '执行命令' },
  external_upload: { icon: '⬆️', label: '外部上传' },
  // 权限语义细分（外部复核 9-11）：删除/支付/外发/进程不再统一标"外部上传"
  external_delete: { icon: '🗑️', label: '删除文件' },
  external_message: { icon: '✉️', label: '外发消息' },
  payment: { icon: '💳', label: '支付' },
  process_spawn: { icon: '🚀', label: '启动进程' },
  external_other: { icon: '🌐', label: '外部操作' },
};

function StepList({ entry }: { entry: TimelineEntry }) {
  const items =
    entry.todoItems ??
    entry.steps.map((s, i) => ({
      id: `${i}`,
      title: s.name,
      status:
        entry.stepStatus?.[s.name] === 'done'
          ? 'completed'
          : entry.stepStatus?.[s.name] === 'running'
            ? 'in_progress'
            : 'queued',
    }));

  return (
    <div className="flex min-w-0 flex-col gap-0.5 py-1">
      {items.map((item, index) => {
        const done = item.status === 'completed';
        const active = item.status === 'in_progress';
        const blocked = item.status === 'blocked';
        const cancelled = item.status === 'cancelled';
        return (
          <div
            key={item.id || index}
            className="flex min-w-0 items-center gap-2 py-0.5 text-[12px]"
          >
            <span
              className="grid size-4 shrink-0 place-items-center text-[10px]"
              style={{
                color: done
                  ? '#2ea45f'
                  : active
                    ? 'var(--accent, #2a7de1)'
                    : blocked
                      ? '#b7791f'
                      : '#a0a6b0',
              }}
            >
              {done ? (
                '✓'
              ) : cancelled ? (
                '×'
              ) : blocked ? (
                '!'
              ) : active ? (
                <Loader2 size={11} className="animate-spin" />
              ) : (
                String(index + 1)
              )}
            </span>
            <span
              className="min-w-0 flex-1 truncate"
              title={item.title}
              style={{
                color: done || cancelled ? 'var(--text-faint, #9aa0a8)' : 'var(--text, #1d2129)',
                fontWeight: active ? 600 : 400,
              }}
            >
              {item.title}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function Timeline({ entry }: { entry: TimelineEntry }) {
  const running = entry.phase !== 'completed' && entry.phase !== 'cancelled';
  const permissionNodes = entry.permissions.map((p) => {
    const meta = PERM_META[p] ?? { icon: '🔐', label: p };
    return (
      <span
        key={p}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px]"
        style={{ background: 'var(--surface-3, #f1f2f4)', color: 'var(--text-muted, #6b7280)' }}
      >
        {meta.icon} {meta.label}
      </span>
    );
  });

  return (
    <div data-testid="timeline" className="w-full min-w-0">
      <HermesToolRow
        title={<span className="min-w-0 truncate">{entry.title}</span>}
        status={running ? 'pending' : 'success'}
        meta={running ? '执行中' : entry.phase === 'cancelled' ? '已取消' : '已完成'}
        defaultOpen={running}
      >
        <div className="w-full min-w-0 pl-5">
          {entry.goal && (
            <div
              className="mb-1 truncate text-[11.5px] text-(--conversation-scaffold-text, #6b7280)"
              title={entry.goal}
            >
              {entry.goal}
            </div>
          )}
          <StepList entry={entry} />
          {permissionNodes.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">{permissionNodes}</div>
          )}
        </div>
      </HermesToolRow>
    </div>
  );
}

export function isTimelineRequest(
  data: UserInputCardRequest | undefined
): data is UserInputCardRequest {
  return data?.display === 'timeline';
}
