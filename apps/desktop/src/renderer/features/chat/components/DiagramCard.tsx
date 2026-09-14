import { useCallback, useEffect, useRef, useState } from 'react';
import { Copy, ImageIcon } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { copySvgAsPng, downloadSvgAsPng } from '../../../lib/svgImage';
import { newDiagramId, useDiagramGallery } from './DiagramGallery';
import { DiagramViewer, SvgBody, type GalleryFig } from './DiagramViewer';

/**
 * 统一图表卡（issue #671 QQ 邮箱选图/看图语言）。
 * - 卡式呈现：细边框圆角卡 + 缩略图 + 底部图名行；hover 边框高亮 +
 *   右侧浮现「复制 PNG」圆钮；
 * - 点击卡体 → 注册进 DiagramGalleryProvider 并打开图集查看器；
 * - 无 provider 兜底（组件未包在 MarkdownContent 内）：退化为单图本地查看器。
 *
 * 单一 effective source（2026-09-10 外部分析定稿）：
 * 缩略图经 SvgBody 渲染并把 getBBox 修正后的完整 svg 回写 displaySvg，
 * Gallery（进而查看器/鸟瞰/复制/下载）全部使用修正版——不再出现
 * Card=原始 / Viewer=修正 的分裂。
 *
 * 稳定 callback 订阅（同上分析）：effect 依赖解构后的 upsert/unregister
 * 函数 identity（context value 中它们稳定），不依赖整个 gallery value——
 * 否则 figs 更新 → value 变 → effect 重跑 → 再 upsert 的循环风险仍在。
 */
interface DiagramCardProps {
  /** 已消毒的 SVG 字符串 */
  svg: string;
  /** 图名（如「合成流程图」），缺省「流程图」 */
  label?: string;
  /** hover 复制动作；接收**修正后**的 SVG（displaySvg）——调用方不得
   *  用闭包里的原始 svg 绕过（审查 R6 P1：卡片复制曾绕过修正版导致 PNG
   *  仍被 bbox/viewBox 裁切）。缺省走 copySvgAsPng(displaySvg)。 */
  onCopy?: (svg: string) => Promise<boolean>;
}

export function DiagramCard({ svg, label = '流程图', onCopy }: DiagramCardProps) {
  const gallery = useDiagramGallery();
  const upsert = gallery?.upsert;
  const unregister = gallery?.unregister;
  const open = gallery?.open;

  // 稳定 id：一次生成（换 id 会触发 provider 循环重注册）
  const regId = useRef<string | null>(null);
  if (regId.current === null) regId.current = newDiagramId();

  const [copied, setCopied] = useState(false);
  // 修正缓存（审查 R4 Major）：记录「哪个源 svg 被修正为完整版」——
  // 仅当源变化时失效。原先 svg 变化即 setDisplaySvg(raw) 的同步会在
  // SvgBody.onFixed 之后把修正值覆盖回原始 svg（gallery/local viewer
  // 拿到未修正版）。派生值替代 state，无需 reset effect。
  const [fixed, setFixed] = useState<{ src: string; svg: string } | null>(null);
  const displaySvg = fixed && fixed.src === svg ? fixed.svg : svg;
  const handleFixed = useCallback(
    (f: string) =>
      setFixed((prev) =>
        prev && prev.src === svg && prev.svg === f ? prev : { src: svg, svg: f }
      ),
    [svg]
  );

  // 注册/更新（依赖稳定 callback；无变化时 provider 内部不触发 state）
  useEffect(() => {
    if (!upsert) return;
    upsert(regId.current!, { svg: displaySvg, label });
  }, [upsert, displaySvg, label]);

  // 卸载注销（独立 effect，仅随 unregister 变化）
  useEffect(() => {
    if (!unregister) return;
    const id = regId.current!;
    return () => unregister(id);
  }, [unregister]);

  // 无 provider 兜底：单图本地查看器
  const [localOpen, setLocalOpen] = useState(false);
  const selfFig: GalleryFig = { id: regId.current, label, svg: displaySvg };

  const openViewer = () => {
    if (regId.current && open) open(regId.current);
    else setLocalOpen(true);
  };

  const quickCopy = async () => {
    const ok = onCopy ? await onCopy(displaySvg) : await copySvgAsPng(displaySvg);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <>
      {/* QQ 选图式卡片 */}
      <div
        role="button"
        tabIndex={0}
        aria-label={`打开${label}预览`}
        data-testid="diagram-card"
        onClick={openViewer}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openViewer();
          }
        }}
        className="group/flow my-2 w-full cursor-zoom-in overflow-hidden rounded-[10px] border border-[var(--border-subtle)] bg-[var(--surface-elevated)] transition-colors hover:border-[var(--accent-strong)] hover:shadow-[0_2px_12px_rgba(0,0,0,0.06)]"
      >
        {/* 缩略图区（SvgBody 内部完成 viewBox 修正并回写 displaySvg） */}
        <div className="flex items-center justify-center p-2.5 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-h-[46vh] [&_svg]:max-w-full [&_svg]:pointer-events-none">
          <SvgBody svg={svg} onFixed={handleFixed} />
        </div>
        {/* 图名行 */}
        <div className="flex h-8 items-center gap-1.5 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)]/40 px-3">
          <ImageIcon size={12} className="text-[var(--text-faint)]" aria-hidden />
          <span className="truncate text-[11.5px] font-medium text-[var(--text-muted)]">
            {label}
          </span>
          <span className="ml-auto text-[10.5px] text-[var(--text-faint)] opacity-0 transition-opacity group-hover/flow:opacity-100">
            点击放大
          </span>
          <span
            role="button"
            tabIndex={-1}
            title="复制 PNG"
            aria-label="复制 PNG"
            className={cn(
              'ml-1.5 grid size-6 cursor-pointer place-items-center rounded-full border border-[var(--border-subtle)] bg-[var(--surface)] text-[var(--text-muted)] opacity-0 shadow-sm transition-opacity group-hover/flow:opacity-100',
              copied && 'border-transparent text-[var(--success)]'
            )}
            onClick={(e) => {
              e.stopPropagation();
              void quickCopy();
            }}
          >
            {copied ? (
              <span className="text-[9px] font-semibold text-[var(--success)]">✓</span>
            ) : (
              <Copy size={11} strokeWidth={2} />
            )}
          </span>
        </div>
      </div>

      {/* 无 provider 兜底：单图查看器 */}
      {!gallery && localOpen && (
        <DiagramViewer
          figs={[selfFig]}
          index={0}
          onSelect={() => undefined}
          onClose={() => setLocalOpen(false)}
          onCopy={async (s) => copySvgAsPng(s)}
          onDownload={async (s) => downloadSvgAsPng(s, `diagram-${Date.now()}.png`)}
        />
      )}
    </>
  );
}
