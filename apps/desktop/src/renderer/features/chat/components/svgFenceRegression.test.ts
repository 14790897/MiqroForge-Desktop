// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { MarkdownContent } from './MarkdownContent';

/**
 * ```svg fence → SvgEmbed → 图库卡 回归（issue #843 审查 #9-02-1）：
 * code renderer 对 svg 语言分支必须走 extractText 还原纯文本（高亮后
 * children 是 span 树，String(children) 会输出 "[object Object]"，
 * SvgEmbed 收到垃圾 → DOMPurify 清空 → 图丢失）。
 */
describe('MarkdownContent svg fence → 图库卡（审查回归）', () => {
  it('renders a ```svg fenced block into the diagram card', () => {
    const content =
      '```svg\n' +
      '<svg viewBox="0 0 200 100" xmlns="http://www.w3.org/2000/svg">' +
      '<rect x="10" y="10" width="80" height="40" fill="#ECECFF" stroke="#9370DB"/>' +
      '</svg>\n' +
      '```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content }));
    // DiagramCard 图库卡渲染（svg 内容完整进入，未被 [object Object] 污染）
    expect(markup).toContain('diagram-card');
    expect(markup).toContain('<rect');
    expect(markup).not.toContain('[object Object]');
  });

  it('keeps the fence content intact when svg has trailing newline', () => {
    const content =
      '```svg\n<svg viewBox="0 0 50 50"><circle cx="25" cy="25" r="20" fill="#fff"/></svg>\n\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content }));
    expect(markup).toContain('diagram-card');
    expect(markup).toContain('<circle');
  });
});
