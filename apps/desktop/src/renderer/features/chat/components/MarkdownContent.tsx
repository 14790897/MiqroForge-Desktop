import { useState, useMemo, type ReactNode } from 'react';
import { Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { cn } from '../../../lib/utils';
import { HtmlPreviewCard, detectHtmlDocument } from './HtmlPreviewCard';
import { CompareTable } from './CompareTable';
import { isCompareLang, parseCompareJson } from './compareData';
import { DiagramGalleryProvider } from './DiagramGallery';
import { MermaidBlock } from './MermaidBlock';
import { SvgEmbed } from './SvgEmbed';
/** Strip <think>...</think> reasoning blocks before rendering. */
function stripThinkBlocks(text: string): string {
  let result = text.replace(/<\/?think>/gi, '');
  return result.trim();
}

const LANG_LABELS: Record<string, string> = {
  ts: 'TypeScript',
  tsx: 'TSX',
  js: 'JavaScript',
  jsx: 'JSX',
  py: 'Python',
  html: 'HTML',
  htm: 'HTML',
  css: 'CSS',
  scss: 'SCSS',
  less: 'Less',
  json: 'JSON',
  yaml: 'YAML',
  yml: 'YAML',
  toml: 'TOML',
  md: 'Markdown',
  markdown: 'Markdown',
  go: 'Go',
  rs: 'Rust',
  rust: 'Rust',
  java: 'Java',
  kt: 'Kotlin',
  swift: 'Swift',
  c: 'C',
  cpp: 'C++',
  cs: 'C#',
  sh: 'Shell',
  bash: 'Bash',
  zsh: 'Zsh',
  powershell: 'PowerShell',
  ps1: 'PowerShell',
  sql: 'SQL',
  xml: 'XML',
  svg: 'SVG',
  diff: 'Diff',
  dockerfile: 'Dockerfile',
  makefile: 'Makefile',
  ini: 'INI',
  env: 'ENV',
  plaintext: 'Plain text',
  text: 'Plain text',
  // issue #671：mermaid 流程图自定义标签
  mermaid: 'mermaid 流程图',
};

function extractText(node: unknown): string {
  if (node == null) return '';
  if (typeof node === 'string') return node;
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (
    node &&
    typeof node === 'object' &&
    'props' in node &&
    (node as any).props?.children != null
  ) {
    return extractText((node as any).props.children);
  }
  return '';
}

export function MarkdownContent({
  content,
  streaming,
  disableDiagrams,
}: {
  content: string;
  streaming?: boolean;
  disableDiagrams?: boolean;
}) {
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const displayContent = stripThinkBlocks(content);
  const htmlDoc = detectHtmlDocument(displayContent);

  const handleCopyCode = (code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  const components = useMemo(
    () => ({
      p: ({ children }: any) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
      h1: ({ children }: any) => (
        <h1 className="text-[17px] font-bold mt-4 mb-2 first:mt-0">{children}</h1>
      ),
      h2: ({ children }: any) => (
        <h2 className="text-[15px] font-bold mt-4 mb-1.5 first:mt-0">{children}</h2>
      ),
      h3: ({ children }: any) => (
        <h3 className="text-sm font-semibold mt-3 mb-1 first:mt-0">{children}</h3>
      ),
      ul: ({ children }: any) => (
        <ul className="list-disc pl-5 my-2 space-y-1 first:mt-0 last:mb-0">{children}</ul>
      ),
      ol: ({ children }: any) => (
        <ol className="list-decimal pl-5 my-2 space-y-1 first:mt-0 last:mb-0">{children}</ol>
      ),
      li: ({ children }: any) => <li>{children}</li>,
      blockquote: ({ children }: any) => (
        <blockquote
          className="border-l-2 pl-3 my-3 first:mt-0 last:mb-0 italic"
          style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        >
          {children}
        </blockquote>
      ),
      strong: ({ children }: any) => <strong className="font-semibold">{children}</strong>,
      em: ({ children }: any) => <em className="italic">{children}</em>,
      hr: () => <hr className="my-4" style={{ borderColor: 'var(--border-subtle)' }} />,
      img: ({ src, alt }: any) => (
        <img src={src} alt={alt ?? ''} className="max-w-full h-auto rounded-lg my-2" />
      ),
      a: ({ href, children }: any) => (
        <a
          href={href}
          className="underline cursor-pointer break-words"
          style={{ color: 'var(--accent)' }}
          onClick={(e) => {
            e.preventDefault();
            if (href) window.open(href, '_blank');
          }}
        >
          {children}
        </a>
      ),
      table: ({ children }: any) => (
        <div
          className="overflow-x-auto my-2 rounded-[10px]"
          style={{ border: '1px solid var(--table-border)' }}
        >
          <table className="text-xs w-full border-collapse">{children}</table>
        </div>
      ),
      th: ({ children }: any) => (
        <th
          className="px-3 py-2 text-left font-semibold"
          style={{ background: 'var(--table-head-bg)' }}
        >
          {children}
        </th>
      ),
      td: ({ children }: any) => <td className="px-3 py-2">{children}</td>,
      pre: ({ children }: any) => {
        // Mermaid 流程图（issue #671）：pre 层拦截，不走代码块容器
        const child = Array.isArray(children) ? children[0] : children;
        const codeProps =
          child && typeof child === 'object' && 'props' in child
            ? ((child as any).props ?? {})
            : {};
        const lang = (
          (codeProps.className ?? '').match(/language-([\w+-]+)/)?.[1] ?? ''
        ).toLowerCase();
        const codeText = extractText(codeProps.children).replace(/\n$/, '');

        // ```compare 结构化对比数据（issue #878）：解析成功渲染对比表，
        // 失败则回落到下方普通代码块展示。
        if (isCompareLang(lang)) {
          const data = parseCompareJson(codeText);
          if (data) return <CompareTable data={data} rawText={codeText} />;
        }

        if (lang === 'mermaid' && !disableDiagrams) {
          return (
            <MermaidBlock
              code={codeText}
              streaming={streaming}
              fallback={
                <div
                  className="group my-2 overflow-hidden rounded-lg"
                  style={{
                    background: 'var(--code-bg)',
                    border: '1px solid var(--border-subtle)',
                  }}
                >
                  <div
                    className="flex items-center gap-2 pl-3 pr-2 h-8"
                    style={{ borderBottom: '1px solid var(--border-subtle)' }}
                  >
                    <span
                      className="text-[11px] font-medium select-none"
                      style={{ color: 'var(--text-faint)' }}
                    >
                      mermaid
                    </span>
                    <button
                      onClick={() => handleCopyCode(codeText)}
                      className="ml-auto rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 hover:opacity-100"
                      style={{
                        color: copiedCode === codeText ? 'var(--success)' : 'var(--text-muted)',
                      }}
                      aria-label="复制代码"
                      title="复制"
                    >
                      {copiedCode === codeText ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                  </div>
                  <pre
                    className="m-0 overflow-x-auto max-w-full"
                    style={{ background: 'transparent', border: 0, padding: 0 }}
                  >
                    {children}
                  </pre>
                </div>
              }
            />
          );
        }
        // Codex-style block header: language left, copy right, a divider under
        // the header; the code body scrolls in the inner <pre> below it.
        const langLabel = LANG_LABELS[lang] ?? lang;        return (
          <div
            className="group my-2 overflow-hidden rounded-lg"
            style={{ background: 'var(--code-bg)', border: '1px solid var(--border-subtle)' }}
          >
            <div
              className="flex items-center gap-2 pl-3 pr-2 h-8"
              style={{ borderBottom: '1px solid var(--border-subtle)' }}
            >
              {lang && (
                <span
                  className="text-[11px] font-medium select-none"
                  style={{ color: 'var(--text-faint)' }}
                >
                  {langLabel}
                </span>
              )}
              <button
                onClick={() => handleCopyCode(codeText)}
                className="ml-auto rounded p-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 hover:opacity-100"
                style={{ color: copiedCode === codeText ? 'var(--success)' : 'var(--text-muted)' }}
                aria-label="复制代码"
                title="复制"
              >
                {copiedCode === codeText ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <pre
              className="m-0 overflow-x-auto max-w-full"
              style={{ background: 'transparent', border: 0, padding: 0 }}
            >
              {children}
            </pre>
          </div>
        );
      },
      code: ({ className, children, ...props }: any) => {
        const cls = className ?? '';
        const isBlock =
          /language-[\w+-]+/.test(cls) || (typeof children === 'string' && children.endsWith('\n'));
        if (isBlock) {
          // ```svg 代码块 → SvgEmbed 渲染（issue #671）。
          // 高亮后 children 是 span 树——extractText 还原纯文本（审查：
          // String(children) 会输出 "[object Object]" 导致 svg 内容丢失）
          if (cls.includes('language-svg') && !disableDiagrams) {
            return <SvgEmbed code={extractText(children).replace(/\n$/, '')} />;
          }
          return (
            <code className={cn('block text-[13px] leading-[1.6] font-mono p-3', cls)} {...props}>
              {children}
            </code>
          );
        }
        return (
          <code
            className="font-mono text-[0.9em] leading-none px-1.5 py-[2px] rounded"
            style={{ background: 'rgba(0,0,0,0.08)' }}
            {...props}
          >
            {children}
          </code>
        );
      },
    }),
    [copiedCode, streaming, disableDiagrams]
  );

  // All hooks above run unconditionally — this early return must come after
  // them, or the hook count changes between renders (partial → full content
  // during streaming) and React throws.
  if (htmlDoc) {
    return <HtmlPreviewCard html={htmlDoc} />;
  }

  // DiagramGalleryProvider：#671 图集——本条消息内的所有 mermaid/svg 图
  // 注册到 provider，点卡打开图集查看器（多图 ←/→ + 胶片切换）
  return (
    <DiagramGalleryProvider>
      <div className="min-w-0 break-words" style={{ overflowWrap: 'anywhere' }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[[rehypeHighlight, { plainText: ['compare', 'compare-json'] }]]}
          components={components}
        >
          {displayContent}
        </ReactMarkdown>
      </div>
    </DiagramGalleryProvider>  );
}
