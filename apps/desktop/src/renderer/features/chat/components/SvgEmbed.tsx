import DOMPurifyImport from 'dompurify';
import { useMemo } from 'react';
import { DiagramCard } from './DiagramCard';

// vite/node 下 dompurify 的 default 导出可能是嵌套的（ESM/CJS interop）
const DOMPurify =
  (DOMPurifyImport as unknown as { default?: typeof DOMPurifyImport }).default ?? DOMPurifyImport;

const LOCAL_FRAGMENT_RE = /^#[A-Za-z_][\w:.-]*$/;
const LOCAL_URL_RE = /^url\(\s*#[A-Za-z_][\w:.-]*\s*\)$/i;
const URL_REF_RE = /url\s*\(/i;

// 外部资源引用边界（审查 P3 + CodeRabbit）：DOMPurify 默认只防 XSS——https/
// mailto 等 scheme 在其 ALLOWED_URI_REGEXP 白名单内，外链不会被剥。这里在
// 消毒后逐属性收紧：href/xlink:href 只允许 #fragment；任何含 url(...) 的属性
// 只允许完整值 url(#id)；含 CSS 转义特征（反斜杠+括号，如 u\72l(...)）的值
// 一并拒绝——CSS 转义可伪装 url( 绕过上面的正则。
//
// 两个反例约束（均有实证）：
// - 不能用原始字符串的 XML 预解析代替本 hook：畸形输入（如属性值内嵌引号）
//   会让 DOMParser 整体失败并原样放行；DOMPurify 的解析器总能产出 DOM。
// - 不能收紧全局 ALLOWED_URI_REGEXP：DOMPurify 对除 URI_SAFE 名单（id/xmlns
//   等）外所有属性的值都跑该正则，`^#...$` 会把 x/y/fill/viewBox/d 等普通值
//   整批删掉（CI 实证：rect 被剥成空壳）。
//
// node 环境（无 window）下 dompurify 导出的是未启用实例，无 addHook；与组件
// 内 SSR 守卫同理跳过。渲染器/测试（jsdom）中正常注册。
if (typeof window !== 'undefined') {
  DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    for (const attr of Array.from(node.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim();

      if (name === 'href' || name === 'xlink:href') {
        if (value && !LOCAL_FRAGMENT_RE.test(value)) {
          if (node.tagName.toLowerCase() === 'use') {
            node.remove();
            break;
          }
          node.removeAttribute(attr.name);
        }
        continue;
      }

      if (
        (URL_REF_RE.test(value) && !LOCAL_URL_RE.test(value)) ||
        (value.includes('\\') && value.includes('('))
      ) {
        node.removeAttribute(attr.name);
      }
    }
  });
}

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
      // 禁外部资源元素：feImage/image 可携带 href 引用外部 URL，渲染时触发
      // 对外请求（IP/网络探测）；href/url() 的边界另由属性级 hook 收紧。
      // style 也必须禁（审查 P3 实证）：DOMPurify 的 CSS 过滤只剥
      // @import/javascript:/expression() 等，任意选择器和 url() 探测放行
      // ——内联 style 的 CSS 作用于整个文档（非 SVG 局部），模型输出可
      // 隐藏/伪造 UI（body{display:none}）或经属性选择器外带输入值。
      // 流程图不需要内嵌 CSS，直接禁掉整个 style 元素。
      FORBID_TAGS: ['feImage', 'image', 'style'],
      // DOMPurify 的 svg profile 默认整体禁 <use>（内部 svgDisallowed 名单，仅移出
      // FORBID_TAGS 无效）——这里显式放回：本地 fragment 引用（symbol/marker 复用）
      // 是流程图刚需，外链/`data:` href 由 afterSanitizeAttributes hook 拦死
      // （非 fragment 的 use 整个元素移除）。
      ADD_TAGS: ['use'],
      // style 属性同样封死（审查 R5 P1）：DOMPurify 非 CSS sanitizer，
      // style="fill:url(https://evil.example/x)" 会触发外部资源请求/数据
      // 外带，不在其默认防护内——流程图不需要任意 CSS，整属性剥掉。
      FORBID_ATTR: ['style'],
      // 不要收紧 ALLOWED_URI_REGEXP：DOMPurify 对除 URI_SAFE 名单（id/xmlns 等）
      // 外所有属性的值都跑这个正则，`^#...$` 会把 x/y/fill/viewBox/d 等普通值
      // 整批删掉（CI 实证：rect 被剥成空壳）。fragment-only 引用边界在
      // afterSanitizeAttributes hook 里保证。
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

  // 审查 R6：不传 onCopy——卡片内部统一用修正后的 displaySvg 复制
  return <DiagramCard svg={clean} label="SVG 图" />;
}
