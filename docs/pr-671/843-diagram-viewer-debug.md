# MiQroForge #843 图表（mermaid）查看器疑难 — 求诊文档

> 项目：MiQroForge Desktop（Electron + React 19 + react-markdown + mermaid v11）
> 分支：feature/671-mermaid-visualization（PR #843）
> 生成时间：2026-09-10。用途：把本文件完整交给 ChatGPT 等分析，寻找根因或给出正确实现。

## 1. 功能是什么

AI 回答里的 mermaid 代码块渲染为「图库卡」（缩略图卡 + 图名行），点击打开「浅色文件查看器」：
- 查看器：全屏浅灰画布；白卡上显示图；拖拽平移、滚轮缩放、双击 2x/复位；
- 顶部工具条：图名·N/M | − % + | 适应窗口 1:1 | 旋转 复制 下载 | ✕
- 多图：左右箭头 + 底部胶片缩略条切换
- 右下角「鸟瞰图」小地图：整图缩略 + 橙色视野框（随缩放/平移实时）+ 点击跳视野

## 2. 用户实测遇到的症状（Windows 11 真机，最新构建）

### S1（核心）：鸟瞰图/卡片显示不完整，「只有一部分 / 约 90%」
- 用户原话：「鸟瞰图不能放下所有内容」「下面这几个图的完全就是一个局部的」
- 关键事实：mermaid 生成的 svg 自身 viewBox 常不包含全部内容，例如 sequenceDiagram 生成 viewBox="-50 -10 977 431"（负坐标起点/内容超出 viewBox 是 mermaid 已知行为）
- 被裁掉的部分在 svg 剪裁区外，浏览器根本不渲染，拖也拖不出来

### S2：放大后无法移动到最上面/最下面
- 用户原话：「我也无法移动到最上面！最下面也不行大概」
- 推测：拖拽 clamp 使用的「内容尺寸」与「含全部内容的渲染尺寸」不一致

### S3（历史，部分已修）
- 卡顿 → 已改命令式 DOM（拖拽零 React 渲染）
- 放大模糊 → 已移除 will-change
- 视野框偏半幅 → 已修（漏加 dispW/2）
- 切图后鸟瞰不同步 → 已修（去掉防重上报标记）

## 3. 当前实现机制

1. 渲染：mermaid.render() → SVG 字符串 → normalizeSvgSize()（% 宽高换算成 viewBox 像素、删 inline max-width）
2. 看图：查看器内 SvgBody（memo）内嵌 dangerouslySetInnerHTML；mount 后 useEffect 用 getBBox() 读内容真实边界，重写 viewBox/width/height（+8px），并把修正后的 outerHTML 上报 → 鸟瞰 background-image（data URI + background-size: contain）使用修正版
3. 拖拽 clamp：maxX=(cw*s - vw)/2, maxY=(ch*s - vh)/2（cw/ch = svg DOM 布局尺寸, s=scale, vw/vh=视口）
4. 鸟瞰视野框：框宽=(vw/s)*r、框中心=图中心+视野中心内容坐标偏移

## 4. E2E 已验证（真实 LLM 回合 + Electron，多次全绿 28-45s）

- 内容 bbox ⊆ 修正后 viewBox：vb="-8,-8,613,374" ⊇ bb="0,0,598,468" OK
- 鸟瞰背景 viewBox == 主图修正后 viewBox（bird-sync）OK
- 中心放大视野框偏差 <3px / 拖拽同步 / 旋转隐藏显示 / 点击跳转 OK
- 但用户真机仍复现 S1/S2 —— 存在未识别差异（E2E 与用户手中的图可能不同类）

## 5. 核心疑点（请重点分析）

1. getBBox() 对带负坐标/超出 viewBox 的 mermaid svg 是否覆盖所有可见内容（stroke、文本字体度量、transform 群组）？有无更稳的「完整包住内容」方案？
2. 「修正后用户仍看到不全」：可能 (a) 修正对该类 svg 部分失效 (b) 鸟瞰 data URI 与主图 DOM 不同源 (c) 用户真机构建错乱。请给不依赖运行期测量的稳健方案（如统一 preserveAspectRatio + 外层容器 contain，或 GitHub README 式 mermaid 渲染做法）。
3. 拖拽边界(S2)：clamp 应基于什么尺寸才能保证内容所有角落都能拖到？
4. 是否应放弃内嵌 svg、统一 img + object-fit: contain？（试过一轮引入新问题已回退）

## 6. 已尝试修复历史

| 修复 | 结果 |
|---|---|
| 内嵌 svg + CSS max-h/max-w contain | 基础正常；超 viewBox 的图不完整 |
| 全链路改 img + 字符串层 ensureViewBox | 引入拖拽失效/新混乱，已回退 |
| DOM getBBox 修正 viewBox + 鸟瞰字符串同步 | E2E 全绿；用户仍报 S1/S2 |
| useZoomPan(React state) → 命令式 DOM | 卡顿解决 |

---

# 附：完整相关代码

## 查看器（核心） — apps/desktop/src/renderer/features/chat/components/DiagramViewer.tsx

```tsx
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
 * 图集查看器（issue #671 · QQ 邮箱看图 → 浅色文件查看模式，2026-09-08 用户定）。
 * 性能关键（用户两次实测"卡"）：拖拽平移/滚轮缩放全程**命令式 DOM 更新**——
 * transform/百分比/鸟瞰视野框直接写 ref 对应元素，不走 React state；
 * React 渲染只在离散动作（开/关/切图/旋转/复制）发生。大 SVG 内容 memo 化。
 *
 *   - 全屏浅灰画布；顶部白工具条（图名 · N/M ｜ − % + ｜ 适应窗口 1:1 ｜
 *     旋转 复制 下载 ｜ ✕）；
 *   - 图白卡居中；拖拽平移（clamp）、滚轮中心缩放、双击 2×/复位；
 *   - 多图：左右箭头 + 底部白胶片；右下角鸟瞰小地图（深玻璃、点击跳视野）；
 *   - Esc / ✕ 关闭。
 */
export interface GalleryFig {
  id: string;
  /** 图名（如「合成流程图」） */
  label: string;
  /** 已消毒的 SVG */
  svg: string;
}

/**
 * SVG 内容体（memo）：svg 引用不变即跳过整棵重渲（含数百行主题 CSS）。
 * mount 后按内容真实边界（getBBox）重写 viewBox——mermaid 部分图表内容
 * 会超出自身 viewBox（如负坐标起点/底部 label 被剪），显示与导出只剩约
 * 90%（用户实测）；用内容 bbox 重写后整图完整（主图/鸟瞰/胶片同源生效）。
 */
const SvgBody = memo(function SvgBody({
  svg,
  onFixed,
}: {
  svg: string;
  onFixed?: (fixedSvg: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector('svg') as SVGSVGElement | null;
    if (!el) return;
    try {
      const b = el.getBBox();
      if (!(b.width > 0 && b.height > 0)) return;
      const pad = 8;
      const x = b.x - pad;
      const y = b.y - pad;
      const w = b.width + pad * 2;
      const h = b.height + pad * 2;
      el.setAttribute('viewBox', `${x} ${y} ${w} ${h}`);
      el.setAttribute('width', String(w));
      el.setAttribute('height', String(h));
      // 内容尺寸变了 → 通知查看器重新 measure（父组件初次量的是旧 viewBox）
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
      // 修正结果回传：鸟瞰 background 走字符串路径，必须用修正版（含全部内容）。
      // 每次 svg 变化（切图/换图）都上报——不得用防重标记：组件实例不
      // remount，标记残留会让切图后的鸟瞰停留在旧字符串（用户实测）。
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
/** 鸟瞰图图区（svg 显示区 = 面板内减去 inset 6×2；视野框/跳转换算共用） */
const MINIMAP = { W: 178, H: 104 };

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
  // 主图 DOM 修正后的完整 svg（鸟瞰 background 使用；原始字符串 viewBox 会裁内容）
  const [fixedSvg, setFixedSvg] = useState<string | null>(null);
  const reportFixed = useCallback((s: string) => setFixedSvg(s), []);

  const stageRef = useRef<HTMLDivElement>(null);
  const tfElRef = useRef<HTMLDivElement>(null); // transform 应用层
  const contentRef = useRef<HTMLDivElement>(null); // svg 测量
  const pctRef = useRef<HTMLSpanElement>(null);
  const mmBoxRef = useRef<HTMLDivElement>(null); // 鸟瞰视野框（命令式）
  const rotRef = useRef(0);

  // 命令式变换状态（不触发 React 渲染）
  const tf = useRef({ x: 0, y: 0, s: 1 });
  const geo = useRef({ vw: 0, vh: 0, cw: 1, ch: 1 }); // 视口 + 内容布局尺寸
  const dragRef = useRef<{ sx: number; sy: number; px: number; py: number } | null>(null);
  const rafRef = useRef(0);

  const svgW = useMemo(() => svgSize(fig.svg).width || 1, [fig.svg]);

  // 鸟瞰背景图 data URL（encodeURIComponent 全转义，CSS 内无解析问题）
  const mmDataUrl = useMemo(() => {
    try {
      return `url("data:image/svg+xml,${encodeURIComponent(fixedSvg ?? fig.svg)}")`;
    } catch {
      return undefined;
    }
  }, [fixedSvg, fig.svg]);

  // ── 命令式视觉同步：transform / 百分比 / 鸟瞰框 ────────────────────
  const applyVisuals = useCallback(() => {
    const { x, y, s } = tf.current;
    const { vw, vh, cw, ch } = geo.current;
    if (tfElRef.current) {
      tfElRef.current.style.transform = `translate(${x}px, ${y}px) scale(${s}) rotate(${rotRef.current}deg)`;
    }
    if (pctRef.current) {
      pctRef.current.textContent = `${Math.round(((s * cw) / svgW) * 100)}%`;
    }
    if (mmBoxRef.current) {
      const AREA_W = MINIMAP.W;
      const AREA_H = MINIMAP.H;
      const r = Math.min(AREA_W / cw, AREA_H / ch);
      const dispW = cw * r;
      const dispH = ch * r;
      const offX = (AREA_W - dispW) / 2;
      const offY = (AREA_H - dispH) / 2;
      // 视野中心投影：内容坐标 cx=-x/s → 面板像素 = offX + dispW/2 + cx*r；
      // 框中心 = 视野中心，框宽 = (vw/s)*r → left = 中心 - 宽/2。
      // （修复：此前漏加 dispW/2，框整体偏左上半个图宽——放大后偏移显性，
      //   用户实测"中心放大后框偏到一边"）
      const vwBox = Math.min(AREA_W, Math.max(14, (vw / s) * r));
      const vhBox = Math.min(AREA_H, Math.max(10, (vh / s) * r));
      const box = {
        left: Math.min(AREA_W, Math.max(0, offX + dispW / 2 - vwBox / 2 + (-x / s) * r)),
        top: Math.min(AREA_H, Math.max(0, offY + dispH / 2 - vhBox / 2 + (-y / s) * r)),
        width: vwBox,
        height: vhBox,
      };
      mmBoxRef.current.style.left = `${box.left + 6}px`; // +inset 6（svg 显示区在图区内偏移）
      mmBoxRef.current.style.top = `${box.top + 6}px`;
      mmBoxRef.current.style.width = `${box.width}px`;
      mmBoxRef.current.style.height = `${box.height}px`;
    }
  }, [svgW]);

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
    if (!stage) return;
    geo.current.vw = stage.clientWidth;
    geo.current.vh = stage.clientHeight;
    const svgEl = contentRef.current?.querySelector('svg');
    if (svgEl) {
      const rect = svgEl.getBoundingClientRect();
      const s = Math.max(0.01, tf.current.s);
      geo.current.cw = Math.max(1, rect.width / s);
      geo.current.ch = Math.max(1, rect.height / s);
    }
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
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        const prev = tf.current;
        tf.current = clampT({
          x: d.sx + (e.clientX - d.px),
          y: d.sy + (e.clientY - d.py),
          s: prev.s,
        });
        applyVisuals();
      });
    },
    [clampT, applyVisuals]
  );

  const endPan = useCallback((e: RPointerEvent<HTMLDivElement>) => {
    dragRef.current = null;
    e.currentTarget.style.cursor = 'grab';
  }, []);

  const onWheel = useCallback(
    (e: RWheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      const prev = tf.current;
      const s = Math.min(MAX_S, Math.max(MIN_S, prev.s * factor));
      const k = s / prev.s;
      setT({ x: prev.x * k, y: prev.y * k, s });
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
    if (nr % 180 !== 0) {
      const { cw, ch } = geo.current;
      geo.current = { ...geo.current, cw: ch, ch: cw };
    }
    tf.current = { x: 0, y: 0, s: 1 };
    setRot(nr); // 鸟瞰显隐等
    applyVisuals();
  }, [applyVisuals]);

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
    const target = (rotRef.current % 180 === 0 ? geo.current.cw : geo.current.ch) || 1;
    zoomTo(svgW / target);
  }, [svgW, zoomTo]);
  const fitWindow = useCallback(() => {
    rotRef.current = 0;
    setRot(0);
    resetT();
  }, [resetT]);

  const copyPng = useCallback(async () => {
    const ok = await onCopy(fig.svg);
    if (ok) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }, [fig.svg, onCopy]);

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

  // 鸟瞰点击跳转（命令式换算）
  const jumpMinimap = useCallback(
    (e: RPointerEvent<HTMLDivElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const AREA_W = 178;
      const AREA_H = 100;
      const { cw, ch } = geo.current;
      const r = Math.min(AREA_W / cw, AREA_H / ch);
      if (r <= 0) return;
      const offX = (AREA_W - cw * r) / 2;
      const offY = (AREA_H - ch * r) / 2;
      const px = e.clientX - rect.left - 6 - offX;
      const py = e.clientY - rect.top - 6 - offY;
      const s = tf.current.s;
      setT({ x: -(px / r) * s, y: -(py / r) * s, s });
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
            onClick={() => void onDownload(fig.svg)}
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

        {/* 画布：命令式 transform 层（拖拽/缩放零 React 渲染） */}
        <div
          ref={stageRef}
          className="absolute inset-0 touch-none overflow-hidden"
          style={{ cursor: 'grab' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endPan}
          onPointerLeave={endPan}
          onWheel={onWheel}
          onDoubleClick={onDoubleClick}
        >
          <div className="absolute inset-0 grid place-items-center">
            {/* 命令式 transform 层：不设 will-change——合成层位图在放大时
                只做位图拉伸会糊（用户实测"图不清晰"）；去掉后每次 transform
                变化浏览器重新栅格化，矢量 SVG 放大保持锐利 */}
            <div ref={tfElRef} data-testid="diagram-tf" className="h-full w-full origin-center">
              <div className="flex h-full w-full items-center justify-center px-24 pb-20 pt-10">
                <div
                  className="rounded-xl bg-white p-5 shadow-[0_2px_4px_rgba(0,0,0,0.03),0_18px_48px_rgba(20,24,30,0.12)]"
                  style={{ maxWidth: '94%' }}
                >
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
        </div>

        {/* 底部白色缩略横条（多图，QQ 看图胶片） */}
        {multi && (
          <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center">
            <div className="pointer-events-auto flex items-center gap-1.5 rounded-xl border border-[#e4e6ea] bg-white/95 px-2.5 py-1.5 shadow-[0_4px_18px_rgba(0,0,0,0.08)]">
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

        {/* 鸟瞰图（右下角）：一体面板——顶部标题栏「鸟瞰图」水平居中，
            下方整图缩略 + 橙框视野 + 点击跳转（用户定，勿再改形态/位置） */}
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
              data-testid="diagram-minimap"
            >
              {/* 整图缩略：background-image + size:contain——浏览器强制
                  "一整张图等比适配"，不受 svg 自身 width/height 属性/嵌套
                  影响，从机制上杜绝裁切（用户多轮实测"只有一部分"，2026-09-08 定） */}
              <div
                className="absolute"
                style={{
                  inset: 6,
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

```

## 图库卡 — apps/desktop/src/renderer/features/chat/components/DiagramCard.tsx

```tsx
import { useEffect, useRef, useState } from 'react';
import { Copy, ImageIcon } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { copySvgAsPng, downloadSvgAsPng } from '../../../lib/svgImage';
import { newDiagramId, useDiagramGallery } from './DiagramGallery';
import { DiagramViewer, type GalleryFig } from './DiagramViewer';

/**
 * 统一图表卡（issue #671 QQ 邮箱选图/看图语言）。
 * - 卡式呈现：细边框圆角卡 + 缩略图 + 底部图名行；hover 边框高亮 +
 *   右侧浮现「复制 PNG」圆钮；
 * - 点击卡体 → 注册进 DiagramGalleryProvider 并打开图集查看器
 *   （多图 ←/→ + 胶片；缩放/旋转/1:1/复制/下载见 DiagramViewer）；
 * - 无 provider 兜底（组件未包在 MarkdownContent 内）：退化为单图本地查看器。
 */
interface DiagramCardProps {
  /** 已消毒的 SVG 字符串 */
  svg: string;
  /** 图名（如「合成流程图」），缺省「流程图」 */
  label?: string;
  /** hover 复制动作（返回是否成功）；缺省走 copySvgAsPng */
  onCopy?: () => Promise<boolean>;
}

export function DiagramCard({ svg, label = '流程图', onCopy }: DiagramCardProps) {
  const gallery = useDiagramGallery();
  // 稳定 id：一次生成，内容变化走 upsert（换 id 会触发 provider 循环重注册）
  const regId = useRef<string | null>(null);
  if (regId.current === null) regId.current = newDiagramId();
  const [copied, setCopied] = useState(false);
  // 无 provider 兜底：单图本地查看器
  const [localOpen, setLocalOpen] = useState(false);
  const selfFig: GalleryFig = { id: regId.current, label, svg };

  // 注册/更新（幂等：svg/label 无变化不触发 provider state）
  useEffect(() => {
    if (!gallery) return;
    gallery.upsert(regId.current!, { svg, label });
    return () => gallery.unregister(regId.current!);
  }, [svg, label, gallery]);

  const openViewer = () => {
    if (regId.current && gallery) gallery.open(regId.current);
    else setLocalOpen(true);
  };

  const quickCopy = async () => {
    const ok = onCopy ? await onCopy() : await copySvgAsPng(svg);
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
        {/* 缩略图区：图居中，限高 */}
        <div className="flex items-center justify-center p-2.5 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-h-[46vh] [&_svg]:max-w-full [&_svg]:pointer-events-none">
          <div dangerouslySetInnerHTML={{ __html: svg }} />
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

```

## 图集 provider — apps/desktop/src/renderer/features/chat/components/DiagramGallery.tsx

```tsx
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { copySvgAsPng, downloadSvgAsPng } from '../../../lib/svgImage';
import { DiagramViewer, type GalleryFig } from './DiagramViewer';

/**
 * 图集 Gallery（issue #671 QQ 邮箱看图语言）。
 * 同一消息（一个 MarkdownContent 实例）内的所有流程图/svg 图注册到
 * provider：点击任一卡 → 打开图集查看器，多图可 ←/→、胶片切换。
 * 复制/下载动作统一走 svgImage（copySvgAsPng/downloadSvgAsPng）。
 *
 * 稳定 id 约定：注册方（DiagramCard）自持一次生成的 id，内容变化走
 * upsert(id, fig)；**禁止** unregister+register 换 id——否则 provider
 * value（含 figs）变化会触发卡 effect 重跑 → 再换 id → 无限循环（#843
 * E2E 实测胶片顺序错乱）。
 */
interface GalleryCtx {
  figs: GalleryFig[];
  /** 注册/更新一张图（同 id 幂等：内容无变化不触发 state 变更） */
  upsert: (id: string, fig: Omit<GalleryFig, 'id'>) => void;
  /** 卸载一张图（组件卸载时调用） */
  unregister: (id: string) => void;
  /** 打开以 id 命中的图的查看器 */
  open: (id: string) => void;
}

const Ctx = createContext<GalleryCtx | null>(null);

/** 在 DiagramGalleryProvider 内取图集能力；provider 外为 null（调用方兜底） */
export function useDiagramGallery(): GalleryCtx | null {
  return useContext(Ctx);
}

/** 生成一次性的稳定注册 id（模块级，跨 provider 实例唯一） */
export function newDiagramId(): string {
  return `diag-${++uid}`;
}

let uid = 0;

export function DiagramGalleryProvider({ children }: { children: ReactNode }) {
  const [figs, setFigs] = useState<GalleryFig[]>([]);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);

  const upsert = useCallback((id: string, fig: Omit<GalleryFig, 'id'>) => {
    setFigs((prev) => {
      const idx = prev.findIndex((f) => f.id === id);
      if (idx >= 0) {
        const cur = prev[idx];
        if (cur.svg === fig.svg && cur.label === fig.label) return prev; // 无变化
        const next = prev.slice();
        next[idx] = { ...fig, id };
        return next;
      }
      return [...prev, { ...fig, id }];
    });
  }, []);

  const unregister = useCallback((id: string) => {
    setFigs((prev) => {
      const next = prev.filter((f) => f.id !== id);
      return next.length === prev.length ? prev : next;
    });
  }, []);

  const open = useCallback((id: string) => {
    setFigs((prev) => {
      const i = prev.findIndex((f) => f.id === id);
      if (i >= 0) setViewerIndex(i);
      return prev;
    });
  }, []);

  const value = useMemo<GalleryCtx>(
    () => ({ figs, upsert, unregister, open }),
    [figs, upsert, unregister, open]
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {viewerIndex !== null && figs.length > 0 && (
        <DiagramViewer
          figs={figs}
          index={Math.min(viewerIndex, figs.length - 1)}
          onSelect={setViewerIndex}
          onClose={() => setViewerIndex(null)}
          onCopy={async (svg) => copySvgAsPng(svg)}
          onDownload={async (svg) => downloadSvgAsPng(svg, `diagram-${Date.now()}.png`)}
        />
      )}
    </Ctx.Provider>
  );
}

export type { GalleryFig };

```

## mermaid 渲染块 — apps/desktop/src/renderer/features/chat/components/MermaidBlock.tsx

```tsx
import { useEffect, useState, type ReactNode } from 'react';
import { copySvgAsPng, normalizeSvgSize } from '../../../lib/svgImage';
import { DiagramCard } from './DiagramCard';

/**
 * Mermaid 流程图渲染（issue #671）。
 * 核心逻辑对齐 Hermes Desktop 的 mermaid-embed（apps/desktop/src/components/
 * assistant-ui/embeds/mermaid-embed.tsx）：
 * - streaming 期间不渲染（流式输出的部分语法必然解析失败），显示源码；
 * - 首次使用/主题切换才 initialize（模块级缓存）；
 * - securityLevel: 'strict' 安全渲染；失败降级为源码；
 * - SVG 尺寸规范化（% 宽度 → viewBox 像素，防止缩放容器塌陷）。
 * 展示统一走 DiagramCard（QQ 图库卡：缩略 + 图名行 + 点击进图集查看器）。
 */

let lastTheme: 'dark' | 'default' | null = null;
let mermaidPromise: Promise<typeof import('mermaid')> | null = null;

function loadMermaid() {
  if (!mermaidPromise) mermaidPromise = import('mermaid');
  return mermaidPromise;
}

function detectDark(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const t = localStorage.getItem('miqi-theme');
    if (t === 'dark') return true;
    if (t === 'light') return false;
  } catch {
    /* noop */
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

function useIsDark(): boolean {
  const [dark, setDark] = useState(detectDark);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const update = () => setDark(detectDark());
    update();
    mq.addEventListener('change', update);
    window.addEventListener('storage', update);
    return () => {
      mq.removeEventListener('change', update);
      window.removeEventListener('storage', update);
    };
  }, []);
  return dark;
}

/** 按 mermaid 图类型给中文图名（图库卡图名行 / 查看器标题） */
export function diagramTypeLabel(code: string): string {
  const first = code.trim().split(/\s+/)[0] ?? '';
  switch (first) {
    case 'flowchart':
    case 'graph':
      return '流程图';
    case 'sequenceDiagram':
      return '时序图';
    case 'classDiagram':
      return '类图';
    case 'stateDiagram':
    case 'stateDiagram-v2':
      return '状态图';
    case 'erDiagram':
      return '实体关系图';
    case 'pie':
      return '饼图';
    case 'gantt':
      return '甘特图';
    case 'journey':
      return '用户旅程图';
    case 'gitGraph':
      return 'Git 图';
    case 'mindmap':
      return '思维导图';
    case 'quadrantChart':
      return '象限图';
    case 'requirementDiagram':
      return '需求图';
    case 'timeline':
      return '时间线';
    case 'C4Context':
    case 'C4Container':
    case 'C4Component':
      return 'C4 架构图';
    default:
      return first ? `${first} 图` : '流程图';
  }
}

// ── 源码展示（流式中/加载中 muted；解析失败正常色）──────────────────────
function SourcePreview({ code, muted }: { code: string; muted?: boolean }) {
  return (
    <pre
      className="my-2 rounded-lg overflow-x-auto max-w-full px-3 py-2 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words"
      style={{
        background: 'rgba(0,0,0,0.06)',
        color: muted ? 'var(--text-faint)' : 'var(--text-muted)',
      }}
    >
      {code}
    </pre>
  );
}

interface MermaidBlockProps {
  code: string;
  /** True while the surrounding message is still streaming —— 流式期间不渲染 */
  streaming?: boolean;
  /** 渲染失败时的降级内容（原代码块） */
  fallback: ReactNode;
}

export function MermaidBlock({ code, streaming, fallback }: MermaidBlockProps) {
  const isDark = useIsDark();
  const [svg, setSvg] = useState('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (streaming) return;
    let cancelled = false;
    setFailed(false);
    setSvg('');
    void (async () => {
      try {
        const mod = await loadMermaid();
        const theme = isDark ? 'dark' : 'default';
        if (theme !== lastTheme) {
          mod.default.initialize({
            fontFamily: 'inherit',
            securityLevel: 'strict',
            startOnLoad: false,
            theme,
            // 审查 P3：themeCSS 不在 strict 的保护名单里——%%{init:{themeCSS:
            // "..."}}%% 指令会把原始 CSS 原样嵌入渲染 SVG 的 <style>，内联
            // 渲染时作用于整个文档（外链 url() 探测/UI 伪造）。secure 数组
            // 是 mermaid 官方"禁止被 %%{init}%% 覆盖"的机制。
            secure: ['themeCSS'],
          });
          lastTheme = theme;
        }
        const id = `mmd-${Math.random().toString(36).slice(2)}`;
        const result = await mod.default.render(id, code);
        if (!cancelled) setSvg(normalizeSvgSize(result.svg));
      } catch {
        if (!cancelled) {
          setFailed(true);
          setSvg('');
        }
        // chunk 加载失败不永久缓存 promise，下次尝试重新 import（审查 P3）
        mermaidPromise = null;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, isDark, streaming]);

  if (streaming) return <SourcePreview code={code} muted />;
  if (failed) return <>{fallback}</>;
  if (!svg) return <SourcePreview code={code} muted />;

  return (
    <DiagramCard svg={svg} label={diagramTypeLabel(code)} onCopy={async () => copySvgAsPng(svg)} />
  );
}

```

## svg 工具（normalize/size/导出） — apps/desktop/src/renderer/lib/svgImage.ts

```tsx
/**
 * SVG 工具（逻辑照抄 Hermes lib/svg-image.ts）——mermaid 与 svg embed 共用。
 */

// Mermaid 输出 width="100%" + viewBox；百分比不是固有尺寸，缩放容器会塌陷。
// 用 viewBox 像素替换百分比宽高；无百分比时原样返回。
// mermaid 输出含 &nbsp; 等 HTML 命名实体 → image/svg+xml 解析失败，
// 回退 text/html 解析（HTML 解析器容忍 HTML 实体）——否则 width="100%"
// 保留，dialog/缩放容器里 shrink-to-fit 链路会塌陷成 300px 默认宽。
export function normalizeSvgSize(svg: string): string {
  let el: Element | null = null;
  try {
    const doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
    if (doc.documentElement.tagName === 'svg') el = doc.documentElement;
  } catch {
    el = null;
  }
  if (!el) {
    const doc = new DOMParser().parseFromString(svg, 'text/html');
    el = doc.querySelector('svg');
  }
  if (!el) return svg;
  const width = el.getAttribute('width');
  const height = el.getAttribute('height');
  const widthPct = Boolean(width?.trim().endsWith('%'));
  const heightPct = Boolean(height?.trim().endsWith('%'));
  if (!widthPct && !heightPct) return svg;
  const [, , vbW, vbH] = (el.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
  if (!(vbW > 0 && vbH > 0)) return svg;
  if (widthPct) el.setAttribute('width', String(vbW));
  if (heightPct || (widthPct && !height)) el.setAttribute('height', String(vbH));
  // mermaid 渲染时会给 svg 写 inline style="max-width: <svg宽>px"——inline 优先级
  // 高于 class，容器里的 max-w-full/缩放都压不住它 → 删除
  const style = (el as unknown as { style?: CSSStyleDeclaration }).style;
  style?.removeProperty?.('max-width');
  return new XMLSerializer().serializeToString(el);
}

export function svgSize(svg: string): { height: number; width: number } {
  const el = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement;
  if (el.tagName !== 'svg') {
    // mermaid 输出含 HTML 实体 → XML 解析失败，用 HTML 解析兜底
    const doc = new DOMParser().parseFromString(svg, 'text/html');
    const svgEl = doc.querySelector('svg');
    if (!svgEl) return { height: 600, width: 800 };
    const [, , vbW2, vbH2] = (svgEl.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    return vbW2 > 0 && vbH2 > 0 ? { height: vbH2, width: vbW2 } : { height: 600, width: 800 };
  }
  const wRaw = el.getAttribute('width') || '';
  const hRaw = el.getAttribute('height') || '';
  // % 宽度不是固有尺寸（如 mermaid 的 width="100%"），回退 viewBox
  const w = wRaw && !wRaw.trim().endsWith('%') ? Number.parseFloat(wRaw) : NaN;
  const h = hRaw && !hRaw.trim().endsWith('%') ? Number.parseFloat(hRaw) : NaN;
  if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) return { height: h, width: w };
  const [, , vbW, vbH] = (el.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
  return vbW > 0 && vbH > 0 ? { height: vbH, width: vbW } : { height: 600, width: 800 };
}

/**
 * 把 SVG 字符串转成 XML 合法形式（mermaid 输出含 &nbsp; 等 HTML 命名实体，
 * 直接放 data URL 时 img 用 XML 解析会失败 → PNG 生成失败）。
 * 用 HTML 解析器读入（容忍 HTML 实体），再用 XMLSerializer 输出
 * （自动转成 XML 合法实体 &#160; 等）。
 */
export function svgToXmlSafe(svg: string): string {
  const doc = new DOMParser().parseFromString(svg, 'text/html');
  const svgEl = doc.querySelector('svg');
  if (!svgEl) return svg;
  return new XMLSerializer().serializeToString(svgEl);
}

/** 把 SVG 渲染成 2x PNG Blob（copy / download 共用）。
 *  用 canvg（GitHub 主流 svg→canvas 引擎，9k stars）渲染：
 *  解析容错（HTML 实体/非标准 SVG）、不依赖 <img> 加载（更稳定）；
 *  先 svgToXmlSafe 清洗实体双保险。动态 import——SSR 环境不加载。 */
export async function svgToPngBlob(svg: string, scale = 2): Promise<Blob> {
  const { height, width } = svgSize(svg);
  const xml = svgToXmlSafe(svg);
  const canvas = document.createElement('canvas');
  // 大模型提供的 SVG 无尺寸上限（审查 Major）：限制 canvas 分配——
  // 单边 > MAX_SIDE 时整体等比例降采样（保留宽高比），最小 1px/维。
  const MAX_SIDE = 8192; // 浏览器 canvas 安全上限内
  let eff = scale;
  if (Math.max(width, height) * eff > MAX_SIDE) eff = MAX_SIDE / Math.max(width, height);
  canvas.width = Math.max(1, Math.round(width * eff));
  canvas.height = Math.max(1, Math.round(height * eff));
  const ctx = canvas.getContext('2d');
  if (!ctx) return Promise.reject(new Error('no 2d context'));
  // mermaid SVG 背景透明——PNG 导出铺白底（用户反馈"后面全是透明的"）
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const { Canvg } = await import('canvg');
  // canvg v4：忽略 svg 自带尺寸，按目标画布尺寸（2x）等比渲染；
  // ignoreClear——不清空画布（否则白底被擦掉）
  const v = await Canvg.from(ctx, xml, {
    ignoreDimensions: true,
    scaleWidth: canvas.width,
    scaleHeight: canvas.height,
    ignoreClear: true,
  });
  await v.render();
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob failed'))), 'image/png')
  );
}

/** Blob → base64（走 files.saveAs 的保存对话框管线）。 */
function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error('blobToBase64 failed'));
    reader.readAsDataURL(blob);
  });
}

/** 把 SVG 导出为 PNG 文件下载（图表用户保存场景）。
 *  走应用统一下载管线 files.saveAs（#696/#877：保存对话框选位置，
 *  取消不误报成功）——不再静默 a.click() 到默认下载目录（审查 P3）。 */
export async function downloadSvgAsPng(svg: string, filename = 'diagram.png'): Promise<boolean> {
  try {
    const blob = await svgToPngBlob(svg);
    const dataUrl = await blobToBase64(blob);
    const result = await window.miqi.files.saveAs(filename, dataUrl.split(',')[1] ?? '');
    // 取消（canceled）或失败都不报成功——UI 不会误显「已下载」
    return result.saved === true;
  } catch {
    return false;
  }
}

/** 把 SVG 渲染成 PNG 并复制到剪贴板。
 *  失败时的 writeText 兜底是"降级为复制源码文本"，并非 PNG 复制成功——
 *  必须返回 false，否则 UI 会误显「已复制 PNG」而剪贴板里是文本（审查 P3）。 */
export async function copySvgAsPng(svg: string): Promise<boolean> {
  try {
    const blob = await svgToPngBlob(svg);
    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
    return true;
  } catch {
    try {
      // 降级：复制 SVG 源码文本（仍有用，但如实报告——不是 PNG）
      await navigator.clipboard.writeText(svg);
      return false;
    } catch {
      return false;
    }
  }
}

```

## MarkdownContent（pre/code 拦截） — apps/desktop/src/renderer/features/chat/components/MarkdownContent.tsx

```tsx
import { useState, useMemo, type ReactNode } from 'react';
import { Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { cn } from '../../../lib/utils';
import { HtmlPreviewCard, detectHtmlDocument } from './HtmlPreviewCard';
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
        const langLabel = LANG_LABELS[lang] ?? lang;
        return (
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
          rehypePlugins={[rehypeHighlight]}
          components={components}
        >
          {displayContent}
        </ReactMarkdown>
      </div>
    </DiagramGalleryProvider>
  );
}

```

---

# 附：环境与复现

- 本地 worktree：C:/Users/admin/miqroforge-891-review（分支 feature/671-mermaid-visualization）
- 启动：npm run build && npx electron-vite preview（或 npm run dev）
- E2E：cd apps/desktop && PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test --config=playwright.config.ts --project=electron diagram-gallery-mermaid.spec.ts（需真实 LLM provider key）
