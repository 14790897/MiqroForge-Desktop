import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { AlertCircle, Check, Copy, ExternalLink, FileText } from 'lucide-react';

/**
 * Inject a sizing script into the HTML so the (opaque-origin, sandboxed)
 * iframe reports its content height to the parent via postMessage. Lets the
 * frame hug its content instead of leaving a fixed-height white void below a
 * short page.
 */
const SIZER_SCRIPT = `<script>(function(){function s(){var d=Math.max(document.body?document.body.scrollHeight:0,document.documentElement?document.documentElement.scrollHeight:0);parent.postMessage({__miqiHtmlH:d},'*')}window.addEventListener('load',s);window.addEventListener('resize',s);setTimeout(s,400)})()<\/script>`;

function withSizer(html: string): string {
  if (/<\/body>/i.test(html)) return html.replace(/<\/body>/i, SIZER_SCRIPT + '</body>');
  return html + SIZER_SCRIPT;
}

interface Props {
  html: string;
  className?: string;
  /** Caps the fitted height (CSS length). The frame scrolls if content is taller. */
  maxHeight?: string;
}

interface HtmlRenderFallbackProps {
  copied: boolean;
  onViewSource: () => void;
  onOpenBrowser: () => void;
  onCopy: () => void;
}

/** #880: HTML 渲染失败时的降级卡片（查看源码 / 浏览器打开 / 复制内容）。 */
export function HtmlRenderFallback({
  copied,
  onViewSource,
  onOpenBrowser,
  onCopy,
}: HtmlRenderFallbackProps) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-6"
      style={{ borderColor: 'var(--warning)', background: 'var(--surface-muted)' }}
    >
      <AlertCircle size={18} style={{ color: 'var(--warning)' }} />
      <span className="text-xs text-[var(--text-muted)]">HTML 渲染失败</span>
      <div className="flex items-center gap-2 mt-1">
        <button
          type="button"
          onClick={onViewSource}
          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--text-muted)] hover:bg-[var(--surface)] transition-colors"
        >
          <FileText size={12} />
          查看源码
        </button>
        <button
          type="button"
          onClick={onOpenBrowser}
          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--text-muted)] hover:bg-[var(--surface)] transition-colors"
        >
          <ExternalLink size={12} />
          浏览器打开
        </button>
        <button
          type="button"
          onClick={onCopy}
          className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--text-muted)] hover:bg-[var(--surface)] transition-colors"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? '已复制' : '复制内容'}
        </button>
      </div>
    </div>
  );
}

/** Sandboxed HTML preview frame (scripts allowed, opaque origin) that
 *  auto-fits its height to the page content. On load failure (onerror or
 *  timeout without a height report), falls back to a failure card with
 *  "view source / open in browser / copy" (issue #880). */
export function SandboxHtmlFrame({ html, className = '', maxHeight }: Props) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [contentHeight, setContentHeight] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setContentHeight(null);
    setFailed(false);
    setShowSource(false);

    let received = false;
    const onMsg = (e: MessageEvent) => {
      if (e.source !== ref.current?.contentWindow) return;
      if (e.data && typeof e.data.__miqiHtmlH === 'number') {
        received = true;
        setContentHeight(e.data.__miqiHtmlH);
      }
    };
    window.addEventListener('message', onMsg);

    // 加载失败检测（#880）：5 秒内未收到高度上报，判定渲染失败
    const timer = setTimeout(() => {
      if (!received) setFailed(true);
    }, 5000);

    return () => {
      window.removeEventListener('message', onMsg);
      clearTimeout(timer);
    };
  }, [html]);

  // 查看源码（降级态）
  if (showSource) {
    return (
      <pre
        className="p-4 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all text-[var(--text-muted)] overflow-auto"
        style={{ maxHeight: maxHeight ?? '70vh' }}
      >
        {html}
      </pre>
    );
  }

  // 渲染失败兜底卡片（降级态）
  if (failed) {
    return (
      <HtmlRenderFallback
        copied={copied}
        onViewSource={() => setShowSource(true)}
        onOpenBrowser={() => window.miqi.files.openInBrowser(html)}
        onCopy={async () => {
          try {
            const result = await window.miqi.clipboard.writeText(html);
            if (!result?.ok) return;
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            /* clipboard unavailable */
          }
        }}
      />
    );
  }

  const style: CSSProperties = { background: '#fff' };
  if (contentHeight) {
    style.height = maxHeight ? `min(${contentHeight}px, ${maxHeight})` : `${contentHeight}px`;
  }

  return (
    <iframe
      ref={ref}
      sandbox="allow-scripts"
      srcDoc={withSizer(html)}
      className={className}
      style={style}
      title="HTML 预览"
      onError={() => setFailed(true)}
    />
  );
}
