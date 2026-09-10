import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as RPointerEvent,
  type WheelEvent as RWheelEvent,
} from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  ImageIcon,
  Minus,
  Plus,
  RotateCw,
  X,
} from 'lucide-react';
import { svgSize } from '../../../lib/svgImage';
import { cn } from '../../../lib/utils';

/**
 * 图集查看器（issue #671 · QQ 邮箱看图 → 浅色文件查看模式）。
 *
 * 几何模型（2026-09-10 外部分析定稿，勿再退化）：
 * - transform 层（tfElRef）**只包内容**（白卡 + svg），不是 viewport——
 *   clamp 的 cw/ch = tfEl.offsetWidth/Height（真正被 transform 的尺寸），
 *   结构上保证 ±(content*s - viewport)/2 的边界公式成立；
 * - svg 视觉完整：SvgBody 用「drawable descendants getBBox(stroke/markers)
 *   + getCTM 映射 + union」求完整 bbox（root.getBBox 不含 stroke/markers/
 *   自身 transform，会漏内容）；useLayoutEffect 在 paint 前修正 viewBox；
 * - minimap 投影用 sw/sh（svg 基准布局尺寸），pan/clamp 用 cw/ch（卡片），
 *   两套尺寸不再混用；MINIMAP 常量单一来源；
 * - 拖拽/滚轮命令式 DOM 更新（零 React 渲染）；滚轮朝光标缩放。
 */
export interface GalleryFig {
  id: string;
  /** 图名（如「合成流程图」） */
  label: string;
  /** 已消毒的 SVG */
  svg: string;
}

/** 参与视觉 bbox 的元素（defs/clip/marker 等非绘制内容除外） */
const DRAWABLE_TAGS = new Set([
  'path',
  'rect',
  'circle',
  'ellipse',
  'line',
  'polyline',
  'polygon',
  'text',
  'use',
  'image',
  'foreignobject',
]);

/**
 * 完整视觉 bbox：遍历可绘制后代，getBBox() 取局部盒并**手动外扩 stroke 与
 * marker**（Chromium 实测 getBBox(options)/默认均不含 stroke；mermaid 大量
 * 使用 marker-end 箭头），getCTM 映射到根用户坐标系后 union。
 * root.getBBox() 不含 stroke/markers、不考虑自身 transform —— 直接用它
 * 无法保证"内容全进 viewBox"（fixture E2E 实证）。
 */
export function getCompleteSvgBBox(
  svg: SVGSVGElement
): { x: number; y: number; width: number; height: number } | null {
  const rootCtm = svg.getCTM();
  const rootInv = rootCtm ? rootCtm.inverse() : null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  const nodes = svg.querySelectorAll('*');
  for (const node of nodes) {
    const tag = node.tagName.toLowerCase();
    if (!DRAWABLE_TAGS.has(tag)) continue;
    if (node.closest('defs,clipPath,mask,marker,pattern,filter,symbol')) continue;
    const g = node as SVGGraphicsElement;
    let b: DOMRect | SVGRect;
    try {
      b = g.getBBox();
    } catch {
      continue;
    }
    if (!(b.width > 0 && b.height > 0)) continue;
    // 手动计入 stroke（半宽外扩）与 marker 箭头（近似 8px）——不依赖
    // getBBox(options) 的浏览器支持
    let ext = 0;
    try {
      const cs = getComputedStyle(node);
      const strokeW = Number.parseFloat(cs.strokeWidth || '0');
      if (cs.stroke && cs.stroke !== 'none' && Number.isFinite(strokeW)) ext += strokeW / 2;
    } catch {
      /* 无 computed style 时忽略 */
    }
    if (
      node.hasAttribute('marker-start') ||
      node.hasAttribute('marker-mid') ||
      node.hasAttribute('marker-end')
    ) {
      ext += 8;
    }
    const ctm = g.getCTM();
    if (!ctm) continue;
    const m = rootInv ? rootInv.multiply(ctm) : ctm;
    const bx = b.x - ext;
    const by = b.y - ext;
    const bw = b.width + ext * 2;
    const bh = b.height + ext * 2;
    const pts = [
      new DOMPoint(bx, by),
      new DOMPoint(bx + bw, by),
      new DOMPoint(bx, by + bh),
      new DOMPoint(bx + bw, by + bh),
    ];
    for (const p of pts) {
      const t = p.matrixTransform(m);
      minX = Math.min(minX, t.x);
      minY = Math.min(minY, t.y);
      maxX = Math.max(maxX, t.x);
      maxY = Math.max(maxY, t.y);
    }
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

// E2E 确定性几何测试入口（fixture bbox 断言用；无副作用）
if (typeof window !== 'undefined') {
  (window as unknown as Record<string, unknown>).__miqiDiagramDebug = { getCompleteSvgBBox };
}

/**
 * SVG 内容体（memo）：渲染后 useLayoutEffect（paint 前）按完整 bbox 重写
 * viewBox/width/height（+8px padding）；onFixed 回传修正后 outerHTML——
 * 卡片/Gallery/鸟瞰/复制/下载必须统一使用该修正版（单一 effective source）。
 */
export const SvgBody = memo(function SvgBody({
  svg,
  onFixed,
}: {
  svg: string;
  onFixed?: (fixedSvg: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current?.querySelector('svg') as SVGSVGElement | null;
    if (!el) return;
    try {
      const b = getCompleteSvgBBox(el);
      if (!b || !(b.width > 0 && b.height > 0)) return;
      const pad = 8;
      const x = b.x - pad;
      const y = b.y - pad;
      const w = b.width + pad * 2;
      const h = b.height + pad * 2;
      el.setAttribute('viewBox', `${x} ${y} ${w} ${h}`);
      el.setAttribute('width', String(w));
      el.setAttribute('height', String(h));
      // 内容尺寸变了 → 通知查看器重新 measure（rAF 后布局稳定）
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
      // 每次 svg 变化都上报（切图不 remount，防重标记会残留下报失效）
      if (onFixed) onFixed(el.outerHTML);
    } catch {
      /* 非 svg 或无布局时忽略 */
    }
  }, [svg, onFixed]);
  return <div ref={ref} dangerouslySetInnerHTML={{ __html: svg }} />;
});

const TOOL =
  'inline-flex h-[30px] min-w-[30px] items-center justify-center gap-1.5 rounded-lg border border-[#e2e5e9] bg-white px-2 text-[12px] font-medium text-[#4a4a52] transition-colors hover:bg-[#f7f8fa]';
const TOOL_ICON =
  'inline-flex h-[30px] min-w-[30px] items-center justify-center rounded-lg border-0 bg-transparent px-1.5 text-[#4a4a52] transition-colors hover:bg-[#f0f2f4]';
const SEP = 'mx-1.5 h-[18px] w-px bg-[#e8eaee]';

const MIN_S = 1;
const MAX_S = 8;
/** 鸟瞰图区尺寸（面板 190x116 减 inset 6x2）——单一来源：框投影与点击换算共用 */
const MINIMAP = { W: 178, H: 104, INSET: 6 };

interface DiagramViewerProps {
  figs: GalleryFig[];
  index: number;
  onSelect: (i: number) => void;
  onClose: () => void;
  onCopy: (svg: string) => Promise<boolean>;
  onDownload: (svg: string) => Promise<boolean>;
}

export function DiagramViewer({
  figs,
  index,
  onSelect,
  onClose,
  onCopy,
  onDownload,
}: DiagramViewerProps) {
  const fig = figs[index];
  const total = figs.length;
  const multi = total > 1;
  const label = fig.label || '流程图';

  const [copied, setCopied] = useState(false);
  const [rot, setRot] = useState(0); // 仅控制鸟瞰显隐等低频渲染；transform 走 rotRef
  // 主图 DOM 修正后的完整 svg —— **唯一 effective source**：
  // 鸟瞰背景 / 复制 / 下载全部用它（原始字符串 viewBox 会裁内容）
  const [fixedSvg, setFixedSvg] = useState<string | null>(null);
  const reportFixed = useCallback((s: string) => setFixedSvg(s), []);
  const effectiveSvg = fixedSvg ?? fig.svg;

  const stageRef = useRef<HTMLDivElement>(null);
  const tfElRef = useRef<HTMLDivElement>(null); // transform 层（只包内容）
  const contentRef = useRef<HTMLDivElement>(null); // svg 测量
  const pctRef = useRef<HTMLSpanElement>(null);
  const mmBoxRef = useRef<HTMLDivElement>(null); // 鸟瞰视野框（命令式）
  const rotRef = useRef(0);

  // 命令式变换状态（不触发 React 渲染）
  const tf = useRef({ x: 0, y: 0, s: 1 });
  // geo：vw/vh=视口；cw/ch=transform 内容尺寸（clamp 用）；sw/sh=svg 基准布局尺寸（鸟瞰投影用）
  const geo = useRef({ vw: 0, vh: 0, cw: 1, ch: 1, sw: 1, sh: 1 });
  const dragRef = useRef<{ sx: number; sy: number; px: number; py: number } | null>(null);
  const rafRef = useRef(0);

  const svgW = useMemo(() => svgSize(effectiveSvg).width || 1, [effectiveSvg]);

  // 鸟瞰背景图 data URL（用修正版；encodeURIComponent 全转义）
  const mmDataUrl = useMemo(() => {
    try {
      return `url("data:image/svg+xml,${encodeURIComponent(effectiveSvg)}")`;
    } catch {
      return undefined;
    }
  }, [effectiveSvg]);

  // ── 命令式视觉同步：transform / 百分比 / 鸟瞰框 ────────────────────
  const applyVisuals = useCallback(() => {
    const { x, y, s } = tf.current;
    const { vw, vh, sw, sh } = geo.current;
    if (tfElRef.current) {
      tfElRef.current.style.transform = `translate(${x}px, ${y}px) scale(${s}) rotate(${rotRef.current}deg)`;
    }
    if (pctRef.current) {
      pctRef.current.textContent = `${Math.round(((s * sw) / svgW) * 100)}%`;
    }
    if (mmBoxRef.current) {
      const { W, H, INSET } = MINIMAP;
      const r = Math.min(W / sw, H / sh);
      const dispW = sw * r;
      const dispH = sh * r;
      const offX = (W - dispW) / 2;
      const offY = (H - dispH) / 2;
      // 视野中心内容坐标（原点=内容中心）= (-x/s, -y/s)；框中心 = 中心投影
      const vwBox = Math.min(W, Math.max(14, (vw / s) * r));
      const vhBox = Math.min(H, Math.max(10, (vh / s) * r));
      const box = {
        left: Math.min(W, Math.max(0, offX + dispW / 2 - vwBox / 2 + (-x / s) * r)),
        top: Math.min(H, Math.max(0, offY + dispH / 2 - vhBox / 2 + (-y / s) * r)),
        width: vwBox,
        height: vhBox,
      };
      mmBoxRef.current.style.left = `${box.left + INSET}px`;
      mmBoxRef.current.style.top = `${box.top + INSET}px`;
      mmBoxRef.current.style.width = `${box.width}px`;
      mmBoxRef.current.style.height = `${box.height}px`;
    }
  }, [svgW]);

  // clamp：基于 transform 内容尺寸（cw/ch = tfEl 尺寸）——结构与公式闭合
  const clampT = useCallback((t: { x: number; y: number; s: number }) => {
    const { vw, vh, cw, ch } = geo.current;
    const cw2 = cw * t.s;
    const ch2 = ch * t.s;
    const maxX = Math.max(0, (cw2 - vw) / 2);
    const maxY = Math.max(0, (ch2 - vh) / 2);
    return {
      s: Math.min(MAX_S, Math.max(MIN_S, t.s)),
      x: Math.min(maxX, Math.max(-maxX, t.x)),
      y: Math.min(maxY, Math.max(-maxY, t.y)),
    };
  }, []);

  const setT = useCallback(
    (t: { x: number; y: number; s: number }) => {
      tf.current = clampT(t);
      applyVisuals();
    },
    [clampT, applyVisuals]
  );

  const zoomBy = useCallback(
    (factor: number) => {
      const prev = tf.current;
      setT({ x: prev.x * factor, y: prev.y * factor, s: prev.s * factor });
    },
    [setT]
  );

  const resetT = useCallback(() => {
    tf.current = { x: 0, y: 0, s: 1 };
    applyVisuals();
  }, [applyVisuals]);

  // ── 测量（命令式写 geo；ResizeObserver/图切换时低频触发）───────────
  const measure = useCallback(() => {
    const stage = stageRef.current;
    const tfEl = tfElRef.current;
    if (!stage || !tfEl) return;
    const vw = stage.clientWidth;
    const vh = stage.clientHeight;
    const s = Math.max(0.01, tf.current.s);
    // 卡片尺寸（被 transform 的实体）
    const cw0 = Math.max(1, tfEl.offsetWidth);
    const ch0 = Math.max(1, tfEl.offsetHeight);
    // svg 基准布局尺寸（换算回 s=1 的基准）
    const svgEl = contentRef.current?.querySelector('svg');
    const svgRect = svgEl?.getBoundingClientRect();
    const sw0 = Math.max(1, (svgRect?.width ?? cw0) / s);
    const sh0 = Math.max(1, (svgRect?.height ?? ch0) / s);
    const rotated = rotRef.current % 180 !== 0;
    geo.current = {
      vw,
      vh,
      cw: rotated ? ch0 : cw0,
      ch: rotated ? cw0 : ch0,
      sw: rotated ? sh0 : sw0,
      sh: rotated ? sw0 : sh0,
    };
    applyVisuals();
  }, [applyVisuals]);

  useLayoutEffect(() => {
    measure();
    const stage = stageRef.current;
    if (!stage) return;
    const ro = new ResizeObserver(() => measure());
    ro.observe(stage);
    window.addEventListener('resize', measure);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [measure, fig.id, index]);

  // 切图/打开：复位
  useLayoutEffect(() => {
    rotRef.current = 0;
    setRot(0);
    setFixedSvg(null);
    tf.current = { x: 0, y: 0, s: 1 };
    measure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, fig.id]);

  // 卸载清理：取消挂起的 rAF
  useEffect(
    () => () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    },
    []
  );

  // ── 平移（命令式；cursor 直接改 style，不渲染）─────────────────────
  const onPointerDown = useCallback((e: RPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const t = tf.current;
    dragRef.current = { sx: t.x, sy: t.y, px: e.clientX, py: e.clientY };
    e.currentTarget.style.cursor = 'grabbing';
  }, []);

  const onPointerMove = useCallback(
    (e: RPointerEvent<HTMLDivElement>) => {
      const d = dragRef.current;
      if (!d) return;
      const clientX = e.clientX;
      const clientY = e.clientY;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        const prev = tf.current;
        tf.current = clampT({
          x: d.sx + (clientX - d.px),
          y: d.sy + (clientY - d.py),
          s: prev.s,
        });
        applyVisuals();
      });
    },
    [clampT, applyVisuals]
  );

  const endPan = useCallback((e: RPointerEvent<HTMLDivElement>) => {
    dragRef.current = null;
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    e.currentTarget.style.cursor = 'grab';
  }, []);

  // 滚轮：朝光标缩放（标准看图器行为）
  const onWheel = useCallback(
    (e: RWheelEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      const stage = stageRef.current;
      if (!stage) return;
      const rect = stage.getBoundingClientRect();
      const cx = e.clientX - (rect.left + rect.width / 2);
      const cy = e.clientY - (rect.top + rect.height / 2);
      const prev = tf.current;
      const nextScale = Math.min(MAX_S, Math.max(MIN_S, prev.s * (e.deltaY < 0 ? 1.1 : 1 / 1.1)));
      const k = nextScale / prev.s;
      setT({ s: nextScale, x: cx - k * (cx - prev.x), y: cy - k * (cy - prev.y) });
    },
    [setT]
  );

  const onDoubleClick = useCallback(() => {
    if (tf.current.s > 1.1) resetT();
    else zoomBy(2);
  }, [resetT, zoomBy]);

  const rotate = useCallback(() => {
    const nr = (rotRef.current + 90) % 360;
    rotRef.current = nr;
    tf.current = { x: 0, y: 0, s: 1 };
    setRot(nr);
    measure(); // 旋转后 cw/ch、sw/sh 按 swap 重新测量
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [measure]);

  const zoomTo = useCallback(
    (target: number) => {
      const prev = tf.current;
      const s = Math.min(MAX_S, Math.max(MIN_S, target));
      const k = s / prev.s;
      setT({ x: prev.x * k, y: prev.y * k, s });
    },
    [setT]
  );

  const toOriginalSize = useCallback(() => {
    zoomTo(svgW / (geo.current.sw || 1));
  }, [svgW, zoomTo]);

  const fitWindow = useCallback(() => {
    rotRef.current = 0;
    setRot(0);
    resetT();
    measure();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetT, measure]);

  const copyPng = useCallback(async () => {
    const ok = await onCopy(effectiveSvg);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }, [effectiveSvg, onCopy]);

  // Esc 关闭 + 锁背景滚动 + ←/→ 切图
  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowRight' && multi) onSelect((index + 1) % total);
      if (e.key === 'ArrowLeft' && multi) onSelect((index - 1 + total) % total);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [index, total, multi, onClose, onSelect]);

  // 鸟瞰点击跳转：内容坐标 px → x=(sw/2-px)*s（内容中心=视口中心）
  const jumpMinimap = useCallback(
    (e: RPointerEvent<HTMLDivElement>) => {
      e.stopPropagation();
      const rect = e.currentTarget.getBoundingClientRect();
      const { sw, sh } = geo.current;
      const s = tf.current.s;
      const { W, H, INSET } = MINIMAP;
      const r = Math.min(W / sw, H / sh);
      if (!(r > 0)) return;
      const offX = (W - sw * r) / 2;
      const offY = (H - sh * r) / 2;
      const px = Math.max(0, Math.min(sw, (e.clientX - rect.left - INSET - offX) / r));
      const py = Math.max(0, Math.min(sh, (e.clientY - rect.top - INSET - offY) / r));
      setT({ x: (sw / 2 - px) * s, y: (sh / 2 - py) * s, s });
    },
    [setT]
  );

  return (
    <div
      className="fixed inset-0 z-[70] flex select-none flex-col"
      style={{ background: '#f2f3f5' }}
      role="dialog"
      aria-modal="true"
      aria-label={`${label} 预览`}
      data-testid="diagram-viewer"
    >
      {/* 顶部工具条 */}
      <div className="flex h-[52px] flex-none items-center gap-2 border-b border-[#e4e6ea] bg-white px-4">
        <span className="flex items-center gap-2 text-[13px] font-semibold text-[#2a2f36]">
          <ImageIcon size={15} className="text-[#9aa0a8]" aria-hidden />
          {label}
          {multi && (
            <span className="text-[11px] font-normal text-[#9aa0a8] tabular-nums">
              {index + 1} / {total}
            </span>
          )}
        </span>

        <span className="ml-auto flex max-w-[calc(100%-180px)] items-center gap-0.5 overflow-x-auto">
          <button
            type="button"
            title="缩小"
            aria-label="缩小"
            className={TOOL_ICON}
            onClick={() => zoomBy(1 / 1.25)}
          >
            <Minus size={15} strokeWidth={2} />
          </button>
          <span
            ref={pctRef}
            data-testid="diagram-viewer-pct"
            className="min-w-[46px] text-center text-[11.5px] tabular-nums text-[#8a93a0]"
          >
            100%
          </span>
          <button
            type="button"
            title="放大"
            aria-label="放大"
            className={TOOL_ICON}
            onClick={() => zoomBy(1.25)}
          >
            <Plus size={15} strokeWidth={2} />
          </button>
          <span className={SEP} />
          <button type="button" className={TOOL} onClick={fitWindow}>
            适应窗口
          </button>
          <button type="button" className={TOOL} onClick={toOriginalSize}>
            1:1
          </button>
          <span className={SEP} />
          <button
            type="button"
            title="旋转 90°"
            aria-label="旋转 90°"
            className={TOOL_ICON}
            onClick={rotate}
          >
            <RotateCw size={14} strokeWidth={1.75} />
          </button>
          <button
            type="button"
            title="复制 PNG"
            aria-label="复制 PNG"
            className={TOOL_ICON}
            onClick={() => void copyPng()}
          >
            {copied ? (
              <Check size={15} className="text-[#0a7d4f]" />
            ) : (
              <Copy size={15} strokeWidth={1.75} />
            )}
          </button>
          <button
            type="button"
            title="下载 PNG"
            aria-label="下载 PNG"
            className={TOOL_ICON}
            onClick={() => void onDownload(effectiveSvg)}
          >
            <Download size={15} strokeWidth={1.75} />
          </button>
          <span className={SEP} />
          <button
            type="button"
            title="关闭"
            aria-label="关闭"
            className={cn(TOOL_ICON, 'ml-1')}
            onClick={onClose}
          >
            <X size={17} strokeWidth={1.75} />
          </button>
        </span>
      </div>

      {/* 舞台 */}
      <div className="relative flex-1 overflow-hidden">
        {/* 左右箭头（多图） */}
        {multi && (
          <>
            <button
              type="button"
              aria-label="上一张"
              title="上一张（←）"
              onClick={() => onSelect((index - 1 + total) % total)}
              className="absolute left-4 top-1/2 z-10 grid size-10 -translate-y-1/2 place-items-center rounded-full border border-[#e2e5e9] bg-white/90 text-[#4a4a52] shadow-sm transition-colors hover:bg-white"
            >
              <ChevronLeft size={20} strokeWidth={1.75} />
            </button>
            <button
              type="button"
              aria-label="下一张"
              title="下一张（→）"
              onClick={() => onSelect((index + 1) % total)}
              className="absolute right-4 top-1/2 z-10 grid size-10 -translate-y-1/2 place-items-center rounded-full border border-[#e2e5e9] bg-white/90 text-[#4a4a52] shadow-sm transition-colors hover:bg-white"
            >
              <ChevronRight size={20} strokeWidth={1.75} />
            </button>
          </>
        )}

        {/* 画布：transform 层只包内容（clamp 的 cw/ch 即其尺寸——
            几何模型闭合；见文件头注释） */}
        <div
          ref={stageRef}
          className="absolute inset-0 touch-none overflow-hidden"
          style={{ cursor: 'grab' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endPan}
          onPointerCancel={endPan}
          onPointerLeave={endPan}
          onWheel={onWheel}
          onDoubleClick={onDoubleClick}
        >
          <div className="absolute inset-0 flex items-center justify-center p-10">
            <div ref={tfElRef} data-testid="diagram-tf" className="origin-center">
              <div className="rounded-xl bg-white p-5 shadow-[0_2px_4px_rgba(0,0,0,0.03),0_18px_48px_rgba(20,24,30,0.12)]">
                <div
                  ref={contentRef}
                  className="[&_svg]:mx-auto [&_svg]:block [&_svg]:h-auto [&_svg]:max-h-[70vh] [&_svg]:max-w-[80vw] [&_svg]:pointer-events-none"
                >
                  <SvgBody svg={fig.svg} onFixed={reportFixed} />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 底部白色缩略横条（多图，QQ 看图胶片）——事件不冒泡进 stage */}
        {multi && (
          <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center">
            <div
              className="pointer-events-auto flex items-center gap-1.5 rounded-xl border border-[#e4e6ea] bg-white/95 px-2.5 py-1.5 shadow-[0_4px_18px_rgba(0,0,0,0.08)]"
              onPointerDown={(e) => e.stopPropagation()}
              onWheel={(e) => e.stopPropagation()}
            >
              {figs.map((f, i) => (
                <button
                  key={f.id}
                  type="button"
                  aria-label={`查看第 ${i + 1} 张`}
                  title={f.label}
                  onClick={() => onSelect(i)}
                  className={cn(
                    'h-[54px] w-[92px] shrink-0 overflow-hidden rounded-lg border-[1.5px] bg-white p-1 transition-all',
                    i === index
                      ? 'border-[#ea653d]'
                      : 'border-[#e6e8ec] opacity-60 hover:opacity-100'
                  )}
                >
                  <div className="h-full w-full [&_svg]:h-full [&_svg]:w-full [&_svg]:object-contain">
                    <SvgBody svg={f.svg} />
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* 鸟瞰图（右下角）：一体面板——标题「鸟瞰图」居中，整图缩略 + 橙框。
            事件 stopPropagation，避免点击变成 stage 拖拽 */}
        {rot % 180 === 0 && (
          <div className="pointer-events-auto absolute bottom-4 right-4 z-20 overflow-hidden rounded-[10px] border border-[#e2e5e9] bg-white shadow-[0_8px_28px_rgba(20,24,30,0.18)]">
            <div className="flex h-[26px] select-none items-center justify-center border-b border-[#eef0f2] bg-[#fafbfc]">
              <span className="text-[11px] font-semibold tracking-[0.3em] text-[#7c7c84]">
                鸟瞰图
              </span>
            </div>
            <div
              className="relative cursor-crosshair overflow-hidden bg-white"
              style={{ width: 190, height: 116 }}
              onPointerDown={jumpMinimap}
              onWheel={(e) => e.stopPropagation()}
              data-testid="diagram-minimap"
            >
              {/* 整图缩略：修正版 data-URI + contain（viewBox 已含全部内容） */}
              <div
                className="absolute"
                style={{
                  inset: MINIMAP.INSET,
                  pointerEvents: 'none',
                  backgroundImage: mmDataUrl,
                  backgroundSize: 'contain',
                  backgroundRepeat: 'no-repeat',
                  backgroundPosition: 'center',
                }}
              />
              {/* 视野框（命令式更新） */}
              <div
                ref={mmBoxRef}
                data-testid="diagram-mm-box"
                className="pointer-events-none absolute rounded-[2px] border border-[#ea653d]"
                style={{ boxShadow: '0 0 0 1px rgba(234,101,61,0.35)' }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
