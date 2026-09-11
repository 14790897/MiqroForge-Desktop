import type { DocBlock } from '../../../../shared/ipc';

const HEADING_CLASSES: Record<number, string> = {
  1: 'text-lg font-bold text-text mt-4 mb-2',
  2: 'text-base font-semibold text-text mt-3 mb-1.5',
  3: 'text-sm font-semibold text-text mt-2 mb-1',
  4: 'text-xs font-semibold text-text mt-2 mb-1',
};

interface Props {
  blocks: DocBlock[];
}

/** 只读富文本文档预览（DOCX）：标题层级 + 表格 + 内嵌图片。 */
export function DocxPreview({ blocks }: Props) {
  return (
    <div
      className="p-4 text-sm leading-relaxed text-text-muted"
      style={{ background: 'var(--surface)' }}
    >
      {blocks.map((block, i) => {
        switch (block.type) {
          case 'heading':
            return (
              <h3 key={i} className={HEADING_CLASSES[block.level] ?? HEADING_CLASSES[3]}>
                {block.text}
              </h3>
            );
          case 'paragraph':
            return (
              <p key={i} className="whitespace-pre-wrap my-1.5 text-text-muted">
                {block.text}
              </p>
            );
          case 'table':
            return (
              <div key={i} className="overflow-x-auto my-3">
                <table
                  className="border-collapse text-xs"
                  style={{ border: '1px solid var(--border-subtle)' }}
                >
                  <tbody>
                    {block.rows.map((row, r) => (
                      <tr key={r}>
                        {row.map((cell, c) => (
                          <td
                            key={c}
                            className="px-2 py-1 border align-top whitespace-pre-wrap break-words"
                            style={{
                              borderColor: 'var(--border-subtle)',
                              background: r === 0 ? 'var(--surface-muted)' : 'transparent',
                              fontWeight: r === 0 ? 600 : 400,
                              maxWidth: 320,
                              minWidth: 40,
                            }}
                          >
                            {cell === '' ? ' ' : cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case 'image':
            return (
              <img
                key={i}
                src={block.data_url}
                alt=""
                className="max-w-full rounded-md my-2"
                style={{ border: '1px solid var(--border-subtle)' }}
              />
            );
          default:
            return null;
        }
      })}
      {blocks.length === 0 && <p className="text-xs text-text-faint">（无内容）</p>}
    </div>
  );
}
