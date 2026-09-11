/**
 * Shared agent status mappings used by AgentPanel and SessionExplorer.
 */

export type AgentStatus = 'idle' | 'thinking' | 'executing' | 'completed' | 'error' | 'aborted';

/** Tailwind background color class for the status dot. */
export function agentStatusColor(status: string): string {
  switch (status) {
    case 'idle':
      return 'bg-[var(--text-faint)]';
    case 'thinking':
      return 'bg-[var(--warning)] animate-pulse';
    case 'executing':
      return 'bg-[var(--info)] animate-pulse';
    case 'completed':
      return 'bg-[var(--success)]';
    case 'error':
      return 'bg-[var(--danger)]';
    case 'aborted':
      return 'bg-[var(--warning)]';
    default:
      return 'bg-[var(--text-faint)]';
  }
}

/** Human-readable Chinese label for the status. */
export function agentStatusLabel(status: string): string {
  switch (status) {
    case 'idle':
      return '空闲';
    case 'thinking':
      return '思考中';
    case 'executing':
      return '执行中';
    case 'completed':
      return '已完成';
    case 'error':
      return '错误';
    case 'aborted':
      return '已中止';
    default:
      return status;
  }
}
