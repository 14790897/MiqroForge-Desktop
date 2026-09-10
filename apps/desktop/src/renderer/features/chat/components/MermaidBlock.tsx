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
