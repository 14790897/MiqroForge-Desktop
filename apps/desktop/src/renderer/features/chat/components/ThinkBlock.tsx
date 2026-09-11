import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { MarkdownContent } from './MarkdownContent';

interface ThinkBlockProps {
  /** The model's chain-of-thought text (markdown). */
  reasoning: string;
  /** When true the block starts expanded (e.g. live streaming). */
  defaultOpen?: boolean;
  /** Optional header override; defaults to 思考中…/已深度思考 · X 秒. */
  header?: string;
  children?: ReactNode;
  /** Elapsed seconds for the "· X 秒" label (0 = omit). */
  elapsedSeconds?: number;
  /** Streaming state: shows a live second counter and a subtle pulse. */
  live?: boolean;
  /** Reasoning mode (issue #680): the thinking-block icon follows the mode —
   *  🚀 fast / 🧠 think — so the mode badge never duplicates. */
  mode?: 'fast' | 'think';
}

/**
 * DeepSeek-style thinking block: 🧠 flush-left, a quiet vertical rule under
 * it, then plain reasoning text — no background, no border box. While
 * streaming the header counts live seconds ("思考中… · 12 秒"); after the
 * turn it shows "已深度思考 · X 秒" and auto-folds.
 */
export function ThinkBlock({
  reasoning,
  defaultOpen = false,
  header,
  children,
  elapsedSeconds,
  live = false,
  mode = 'think',
}: ThinkBlockProps) {
  const [open, setOpen] = useState(defaultOpen);
  const wasLiveRef = useRef(live);
  const collapseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [liveSeconds, setLiveSeconds] = useState(0);
  // 思考块图标跟随模式（🚀 fast / 🧠 think）——模式标不重复（#680 跟进）
  const icon = mode === 'fast' ? '🚀' : '🧠';

  // Live second counter while streaming.
  useEffect(() => {
    if (!live) return;
    const start = Date.now();
    setLiveSeconds(0);
    const t = setInterval(() => setLiveSeconds(Math.round((Date.now() - start) / 1000)), 1000);
    return () => clearInterval(t);
  }, [live]);

  // Auto-fold shortly after streaming finishes (only once per stream).
  useEffect(() => {
    if (wasLiveRef.current && !live && open) {
      collapseTimerRef.current = setTimeout(() => setOpen(false), 700);
      return () => {
        if (collapseTimerRef.current) clearTimeout(collapseTimerRef.current);
      };
    }
    wasLiveRef.current = live;
  }, [live, open]);

  const toggle = () => {
    if (collapseTimerRef.current) {
      clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
    setOpen((v) => !v);
  };

  if (!reasoning && !children) return null;

  const label =
    header ??
    (live
      ? `${mode === 'fast' ? '快速思考' : '深度思考'}… · ${liveSeconds} 秒`
      : elapsedSeconds !== undefined
        ? `${mode === 'fast' ? '快速思考' : '深度思考'} · ${elapsedSeconds} 秒`
        : mode === 'fast'
          ? '快速思考'
          : '深度思考');

  return (
    <div className="my-0.5 flex min-w-0 pl-2">
      <div className="flex w-4 flex-col items-center self-stretch">
        <span
          className={live ? 'text-[13px] leading-none animate-pulse' : 'text-[13px] leading-none'}
        >
          {icon}
        </span>
        <span
          className="mt-0.5 w-[2px] flex-1 min-h-2 rounded-full"
          style={{ background: 'var(--border-subtle)' }}
        />
      </div>
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={toggle}
          className="flex items-center gap-1 py-0.5 text-sm cursor-pointer select-none transition-opacity hover:opacity-75"
          style={{ color: 'var(--text-muted)' }}
          aria-expanded={open}
        >
          <span>{label}</span>
          <ChevronDown
            size={11}
            className="shrink-0 transition-transform opacity-60"
            style={{ transform: open ? 'none' : 'rotate(-90deg)' }}
          />
        </button>
        <div
          className="grid transition-[grid-template-rows] duration-300 ease-out"
          style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="text-[13px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              {/* Render markdown so structured thinking (1./•/** lists) shows
               *  like DeepSeek's, not as raw text. The global `pre` grey box
               *  is avoided since MarkdownContent has no plain-<pre> wrapper. */}
              {children ?? <MarkdownContent content={reasoning} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
