import { HermesConfirmBar, type HermesConfirmChoice } from './HermesConfirmBar';
import { HermesToolRow, TOOL_PRE_CLASS } from './HermesToolRow';

/**
 * ActionCard — final confirmation for actions with external or destructive
 * effects. The approval is intentionally explicit and cannot be silently
 * converted into a session-wide allow rule.
 */
interface ActionCardProps {
  entry: {
    action: string;
    target: string;
    fileName?: string;
    sizeBytes?: number;
    sha256?: string;
    description?: string;
  };
  /** #1071 G7 P1：与 ConfirmCard 同步——把调用方 Promise 透传给 HermesConfirmBar。 */
  onResolve: (
    choiceId: string,
    rememberMode?: 'session' | 'always' | null
  ) => void | Promise<boolean | void>;
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const ACTION_META: Record<string, { icon: string; title: string; tone: 'normal' | 'danger' }> = {
  upload: { icon: '☁', title: '上传', tone: 'normal' },
  payment: { icon: '💳', title: '支付', tone: 'danger' },
  external: { icon: '↗', title: '对外发送', tone: 'danger' },
  external_send: { icon: '↗', title: '对外发送', tone: 'danger' },
  delete: { icon: '⌫', title: '删除', tone: 'danger' },
};

export function ActionCard({ entry, onResolve }: ActionCardProps) {
  const meta = ACTION_META[entry.action] ?? {
    icon: '⚠',
    title: '高风险操作',
    tone: 'danger' as const,
  };

  /**
   * #1071 G7 P1（外部评审）：这里**不再**自持提交锁。此前 ActionCard 自己拿一个
   * `submitting` state 当闸门，而它从不在失败时释放——resolve 失败、卡片被回滚成
   * pending 后，这张卡永久点不动；同时它与下面 HermesConfirmBar 内部的锁各管各的，
   * 两把锁语义还不一致（bar 的锁认 Promise，这张的锁只认点击）。
   * 现在唯一的锁在 HermesConfirmBar.resolveOnce（ref + state），本组件只做 id 映射，
   * 并把 Promise 原样 return 上去，由 bar 决定失败时是否解锁。
   */
  const handleResolve = (
    choice: HermesConfirmChoice,
    rememberMode?: 'session' | 'always' | null
  ) => {
    if (choice === 'deny') return onResolve('cancel');
    if (choice === 'session') return onResolve('confirm', 'session');
    if (choice === 'always') return onResolve('confirm', 'always');
    return onResolve(choice, rememberMode ?? null);
  };

  const dangerAccent = meta.tone === 'danger' ? '#c0392b' : 'rgba(0,0,0,.12)';
  const target = entry.target.length > 120 ? `${entry.target.slice(0, 117)}…` : entry.target;

  return (
    <div
      className="w-full min-w-0 max-w-full"
      data-testid="action-card"
      style={{ borderLeft: `2px solid ${dangerAccent}`, paddingLeft: 8 }}
    >
      <HermesToolRow
        title={
          <span
            className="min-w-0 truncate"
            title={entry.target}
            style={{ color: meta.tone === 'danger' ? '#c0392b' : '#333' }}
          >
            {meta.icon} {meta.title}：{target}
          </span>
        }
        status="pending"
        meta={
          <span className="shrink-0 break-all">
            {entry.fileName
              ? `${entry.fileName}${formatSize(entry.sizeBytes) ? ` · ${formatSize(entry.sizeBytes)}` : ''}`
              : formatSize(entry.sizeBytes) || ''}
            {entry.sha256 ? ` · ${entry.sha256.slice(0, 12)}…` : ''}
          </span>
        }
        approval={
          <div className="pl-5 pt-1">
            <HermesConfirmBar
              tone={meta.tone === 'danger' ? 'danger' : 'accent'}
              runLabel={
                entry.action === 'upload'
                  ? '确认上传'
                  : entry.action === 'payment'
                    ? '确认支付'
                    : entry.action === 'delete' ||
                        entry.action === 'external_send' ||
                        entry.action === 'external'
                      ? `确认${meta.title}`
                      : '确认执行'
              }
              allowSession={false}
              allowAlways={false}
              onResolve={handleResolve}
              description={entry.description || `${meta.title}：${entry.target}`}
              expandableText={
                entry.sha256 || entry.fileName || entry.sizeBytes
                  ? `目标：${entry.target}\n文件：${entry.fileName ?? ''}${formatSize(entry.sizeBytes) ? ` · ${formatSize(entry.sizeBytes)}` : ''}${entry.sha256 ? `\n指纹：${entry.sha256}` : ''}`
                  : undefined
              }
              expandLabel="详情"
            />
          </div>
        }
      >
        {entry.sha256 && <pre className={TOOL_PRE_CLASS}>指纹：{entry.sha256}</pre>}
      </HermesToolRow>
    </div>
  );
}
