/** Simple markdown-ish text renderer — code blocks, bold, inline code. */
export function renderContent(text: string) {
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const inner = part.slice(3, -3);
      const langEnd = inner.indexOf('\n');
      const code = langEnd >= 0 ? inner.slice(langEnd + 1) : inner;
      return (
        <pre
          key={i}
          className="my-2 text-[13px] leading-[1.6] rounded-lg px-3 py-2.5 overflow-x-auto max-w-full"
          style={{ background: 'var(--code-bg)' }}
        >
          <code>{code}</code>
        </pre>
      );
    }
    const segments = part.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
    return (
      <span key={i}>
        {segments.map((seg, j) => {
          if (seg.startsWith('**') && seg.endsWith('**'))
            return <strong key={j}>{seg.slice(2, -2)}</strong>;
          if (seg.startsWith('`') && seg.endsWith('`'))
            return (
              <code
                key={j}
                className="font-mono text-[0.9em] leading-none px-1.5 py-[2px] rounded"
                style={{ background: 'rgba(0,0,0,0.08)' }}
              >
                {seg.slice(1, -1)}
              </code>
            );
          return (
            <span key={j}>
              {seg.split('\n').map((line, k, arr) => (
                <span key={k}>
                  {line}
                  {k < arr.length - 1 && <br />}
                </span>
              ))}
            </span>
          );
        })}
      </span>
    );
  });
}
