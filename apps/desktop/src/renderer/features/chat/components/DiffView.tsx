export function DiffView({ diff }: { diff: string }) {
  const lines = diff.split('\n');
  const isNewFile = lines.some((l) => l.startsWith('--- /dev/null'));
  return (
    <div
      className="overflow-x-auto text-xs font-mono leading-5"
      style={{ background: 'var(--surface)' }}
    >
      {lines.map((line, i) => {
        let bg = 'transparent';
        let color = 'var(--text-muted)';
        if (line.startsWith('+++ b/')) {
          bg = 'rgba(16,185,129,0.08)';
          color = 'var(--success)';
        } else if (line.startsWith('--- /dev/null')) {
          color = 'var(--text-faint)';
        } else if (line.startsWith('---')) {
          color = 'var(--text-faint)';
        } else if (line.startsWith('@@')) {
          bg = isNewFile ? 'rgba(16,185,129,0.08)' : 'rgba(59,130,246,0.08)';
          color = isNewFile ? 'var(--success)' : 'var(--info)';
        } else if (line.startsWith('+')) {
          bg = 'rgba(16,185,129,0.10)';
          color = 'var(--success)';
        } else if (line.startsWith('-')) {
          bg = 'rgba(255,97,97,0.10)';
          color = 'var(--danger)';
        }
        return (
          <div
            key={i}
            style={{
              background: bg,
              color,
              paddingLeft: 12,
              paddingRight: 12,
              whiteSpace: 'pre',
              minWidth: '100%',
              display: 'block',
            }}
          >
            {line || '\u00a0'}
          </div>
        );
      })}
    </div>
  );
}
