import { useState } from 'react';
import { SandboxHtmlFrame } from './SandboxHtmlFrame';

/** True when the text looks like a complete HTML page (root element + closing tag). */
function looksLikeHtmlDoc(text: string): boolean {
  const lower = text.toLowerCase();
  const hasRoot = /<!doctype html/i.test(lower) || /<html[\s>]/i.test(lower);
  const hasClose = /<\/html>|<\/body>/i.test(lower);
  return hasRoot && hasClose;
}

/**
 * Extract a complete HTML document from message text, or null if none.
 * Handles both raw HTML output and a ```html fenced code block.
 */
export function detectHtmlDocument(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  const fenceMatch = trimmed.match(/```(?:html|html5)?\s*\r?\n([\s\S]*?)```/i);
  if (fenceMatch && looksLikeHtmlDoc(fenceMatch[1])) return fenceMatch[1].trim();

  if (looksLikeHtmlDoc(trimmed)) return trimmed;
  return null;
}

/**
 * Render an AI-generated HTML page in a sandboxed iframe (scripts disabled),
 * with a 预览/源码 toggle. Scripts are blocked so untrusted AI output cannot
 * run arbitrary code in the app's Electron renderer.
 */
export function HtmlPreviewCard({ html }: { html: string }) {
  const [mode, setMode] = useState<'preview' | 'source'>('preview');

  return (
    <div
      className="my-2 rounded-lg border overflow-hidden"
      style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}
    >
      <div
        className="flex items-center justify-between px-3 py-1.5 border-b"
        style={{ borderColor: 'var(--border)' }}
      >
        <span className="text-xs font-medium text-[var(--text-muted)]">HTML 预览</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMode('preview')}
            className={`px-2 py-0.5 text-xs rounded ${
              mode === 'preview'
                ? 'bg-[var(--accent)] text-white'
                : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'
            }`}
          >
            预览
          </button>
          <button
            type="button"
            onClick={() => setMode('source')}
            className={`px-2 py-0.5 text-xs rounded ${
              mode === 'source'
                ? 'bg-[var(--accent)] text-white'
                : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'
            }`}
          >
            源码
          </button>
          <button
            type="button"
            onClick={() => window.miqi.html.openInBrowser(html)}
            className="px-2 py-0.5 text-xs rounded text-[var(--text-muted)] hover:bg-[var(--surface-muted)]"
            title="用系统默认浏览器打开完整页面（脚本可运行）"
          >
            浏览器打开
          </button>
        </div>
      </div>
      {mode === 'preview' ? (
        <SandboxHtmlFrame html={html} className="w-full border-0" maxHeight="420px" />
      ) : (
        <pre className="max-h-[420px] overflow-auto p-3 text-xs font-mono leading-relaxed">
          {html}
        </pre>
      )}
    </div>
  );
}
