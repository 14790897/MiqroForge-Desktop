import { useState, type ReactNode } from 'react';
import { Loader2, ChevronRight, AlertCircle } from 'lucide-react';

/**
 * HermesToolRow — 照抄 Hermes fallback.tsx 的 ToolEntry 结构（GitHub
 * NousResearch/hermes-agent，2026-08-26 用户要求"抄他的"）。
 *
 * Hermes 工具行 = ScaffoldRow（可折叠行：14px 状态字形 + 小字标题 + meta
 * 时间）+ 行下审批条（PendingToolApproval）+ 展开等宽区（pre）。成功静默
 * （success is silent——无字形），失败/警告才有字形与颜色。
 */
export type ToolRowStatus = 'pending' | 'running' | 'success' | 'error' | 'warning' | 'cancelled' | 'modified';

export interface HermesToolRowProps {
  /** 行标题（工具名/任务名——SCAFFOLD_LABEL 小字） */
  title: ReactNode;
  /** 状态（字形 + 颜色） */
  status?: ToolRowStatus;
  /** meta 小字（右侧，如 "· 2.3s"） */
  meta?: ReactNode;
  /** 展开区内容（等宽 pre 风格由调用方控制） */
  children?: ReactNode;
  /** 展开区右侧提示（如 copy） */
  trailing?: ReactNode;
  /** 行下审批条（Hermes: isPending 时渲染 PendingToolApproval） */
  approval?: ReactNode;
  /** 默认展开 */
  defaultOpen?: boolean;
  /** 数据测试 id */
  testid?: string;
}

// Hermes SCAFFOLD_LABEL_CLASS 等价（--conversation-tool-font-size 灰）
const LABEL_CLASS = 'text-[12.5px] leading-[1.5] text-(--conversation-scaffold-text, #6b7280)';
const META_CLASS = 'shrink-0 text-[0.625rem] tabular-nums text-(--conversation-scaffold-meta, #a0a6b0)';
const GLYPH_WRAP_CLASS = 'grid size-3.5 shrink-0 place-items-center self-center';

function StatusGlyph({ status }: { status: NonNullable<HermesToolRowProps['status']> }) {
  // Hermes：success is silent（成功无字形）；只有 pending/running/error/warning 有字形
  if (status === 'pending' || status === 'running') {
    return <Loader2 size={13} className="animate-spin" style={{ color: 'var(--conversation-scaffold-meta, #a0a6b0)' }} />;
  }
  if (status === 'error') {
    return <AlertCircle size={13} style={{ color: '#d64545' }} />;
  }
  if (status === 'warning') {
    return <AlertCircle size={13} style={{ color: '#b7791f' }} />;
  }
  return null; // success / cancelled / modified：静默
}

/**
 * HermesToolRow — Hermes 工具行结构（可折叠行 + 行下审批条 + 展开区）。
 */
export function HermesToolRow({
  title,
  status,
  meta,
  children,
  trailing,
  approval,
  defaultOpen = false,
  testid,
}: HermesToolRowProps) {
  const [open, setOpen] = useState(defaultOpen);

  const hasContent = children != null;
  const chevron = hasContent || approval ? (
    <ChevronRight
      size={12}
      className="shrink-0 transition-transform duration-150 opacity-60"
      style={{ transform: open ? 'rotate(90deg)' : 'none' }}
    />
  ) : null;

  return (
    <div className="w-full min-w-0 max-w-full" data-testid={testid}>
      {/* DisclosureRow：glyph + 标题 + meta + caret */}
      <button
        type="button"
        onClick={hasContent ? () => setOpen((v) => !v) : undefined}
        className="group flex w-full min-w-0 items-center gap-1.5 py-0.5 text-left cursor-pointer hover:opacity-80"
        style={{ background: 'none', border: 'none', fontFamily: 'inherit', color: 'inherit' }}
      >
        <span className={GLYPH_WRAP_CLASS}>{status ? <StatusGlyph status={status} /> : null}</span>
        <span className={LABEL_CLASS}>{title}</span>
        {status === 'pending' && testid === 'confirm-card' && (
          <span className={META_CLASS}>等待你的选择</span>
        )}
        {meta && <span className={META_CLASS}>{meta}</span>}
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          {trailing}
          {chevron}
        </span>
      </button>

      {/* Hermes：审批条是行的直接子元素（isPending 时渲染在行下） */}
      {approval}

      {/* 展开区：grid min-w-0 max-w-full overflow-hidden */}
      {open && hasContent && (
        <div className="grid w-full min-w-0 max-w-full gap-1.5 overflow-hidden p-1.5 pt-0.5">
          {children}
        </div>
      )}
    </div>
  );
}

/** Hermes TOOL_SECTION_PRE_CLASS 等价：等宽小字 + 换行 */
export const TOOL_PRE_CLASS =
  'whitespace-pre-wrap break-all font-mono text-[0.7rem] leading-[1.55] text-(--conversation-scaffold-text, #6b7280)';
