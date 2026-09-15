import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * 法律文件正文渲染（设置 → 法律文件、首次启动确认门的文本查看弹窗共用）。
 *
 * 文本是律师定稿的 Markdown（assets/legal/*.zh-CN.md），只做只读展示：
 * 标题/段落/列表/表格四类结构，表格横向可滚动，链接在新窗口打开。
 */
const components: Components = {
  h1: ({ children }) => (
    <h1 className="mb-3 text-[15px] font-semibold leading-snug text-[var(--text)]">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-5 text-[14px] font-semibold leading-snug text-[var(--text)]">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-4 text-[13px] font-semibold leading-snug text-[var(--text)]">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mb-1.5 mt-3 text-[13px] font-medium leading-snug text-[var(--text)]">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="mb-2.5 text-[13px] leading-relaxed text-[var(--text)]">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="mb-2.5 list-disc space-y-1 pl-5 text-[13px] leading-relaxed text-[var(--text)]">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="mb-2.5 list-decimal space-y-1 pl-5 text-[13px] leading-relaxed text-[var(--text)]">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="break-words text-[var(--accent)] underline"
    >
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="mb-3 overflow-x-auto rounded-lg border border-[var(--border-subtle)]">
      <table className="w-full min-w-[720px] border-collapse text-[12px] leading-relaxed">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[var(--surface-muted)]">{children}</thead>,
  th: ({ children }) => (
    <th className="whitespace-nowrap border-b border-[var(--border-subtle)] px-2.5 py-1.5 text-left font-medium text-[var(--text)]">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="break-words border-b border-[var(--border-subtle)] px-2.5 py-1.5 align-top text-[var(--text-muted)]">
      {children}
    </td>
  ),
};

export function LegalDocContent({ text, testId }: { text: string; testId?: string }) {
  return (
    <div data-testid={testId}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
