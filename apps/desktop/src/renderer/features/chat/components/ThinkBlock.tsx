import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';
import { MarkdownContent } from './MarkdownContent';

/** Lines that start a new list item — a blank line before one of these is
 *  NOT a safe segmentation point (CommonMark would restart ordered-list
 *  numbering and drop the loose-list spacing). */
const LIST_ITEM_RE = /^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s/;
/** 空行后缩进 ≥2 空格的续行（loose list 的段落、缩进代码块），不是分段边界。 */
const INDENTED_CONT_RE = /^\s{2,}\S/;
/** Fence openers/closers (``` or ~~~, indented at most 3 spaces). */
const FENCE_RE = /^\s{0,3}(`{3,}|~{3,})\s*$/;
const FENCE_OPEN_RE = /^\s{0,3}(`{3,}|~{3,})/;

/**
 * (#1034) Split streaming reasoning into independent markdown blocks so a
 * flush only re-parses the block that is still growing.
 *
 * Before this, every 60ms flush handed the WHOLE accumulated reasoning to
 * <MarkdownContent>, i.e. a full remarkdown parse of an ever-growing string —
 * the most suspicious amplifier in the measurement report (§5.3).  Splitting
 * on blank lines (block boundaries in CommonMark) plus memoizing each block
 * makes the per-flush cost proportional to the newest block only.
 *
 * Not every blank line is a boundary: blank lines inside fenced code blocks
 * are content, a blank line before a list item belongs to the same list, and
 * an indented continuation line (loose-list paragraph, indented code) still
 * belongs to the block it continues.  These cases stay in one segment, so the
 * rendered markdown is unchanged.
 * A segment's rendered output is identical to rendering the whole text, since
 * a blank line is a block separator in CommonMark.
 */
export function splitReasoningSegments(text: string): string[] {
  if (!text) return [];
  const segments: string[] = [];
  let current: string[] = [];
  /** Blank lines held back: they become a boundary or belong to this block. */
  let pending: string[] = [];
  let fenceChar: string | null = null;
  let fenceLen = 0;

  const flush = () => {
    if (current.length > 0) segments.push(current.join('\n'));
    current = [];
    pending = [];
  };

  for (const line of text.split('\n')) {
    if (fenceChar) {
      current.push(line);
      const close = FENCE_RE.exec(line);
      if (close && close[1][0] === fenceChar && close[1].length >= fenceLen) fenceChar = null;
      continue;
    }
    if (line.trim() === '') {
      if (current.length > 0) pending.push(line);
      continue;
    }
    if (pending.length > 0 && !LIST_ITEM_RE.test(line) && !INDENTED_CONT_RE.test(line)) flush();
    current.push(...pending);
    pending = [];
    current.push(line);
    const open = FENCE_OPEN_RE.exec(line);
    if (open) {
      fenceChar = open[1][0];
      fenceLen = open[1].length;
    }
  }
  flush();
  return segments;
}

/** One memoized markdown block.  `content` is a plain string, so React's
 *  default shallow compare means an unchanged header block never re-renders
 *  — only the streaming tail does. */
const MarkdownSegment = memo(function MarkdownSegment({ content }: { content: string }) {
  return <MarkdownContent content={content} disableDiagrams />;
});

function SegmentedReasoning({ text }: { text: string }) {
  const segments = useMemo(() => splitReasoningSegments(text), [text]);
  return (
    <>
      {segments.map((segment, index) => (
        // Index keys are stable for an append-only stream: block i keeps its
        // identity, which is exactly what lets the memo above skip it.
        <MarkdownSegment key={index} content={segment} />
      ))}
    </>
  );
}

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
               *  is avoided since MarkdownContent has no plain-<pre> wrapper.
               *  #1034: rendered block-by-block (memoized) instead of one
               *  full-document parse per 60ms flush; the full text still
               *  renders once streaming ends. */}
              {children ?? <SegmentedReasoning text={reasoning} />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
