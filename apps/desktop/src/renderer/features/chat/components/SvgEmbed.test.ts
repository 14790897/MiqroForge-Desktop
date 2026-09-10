// @vitest-environment jsdom
import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { SvgEmbed } from './SvgEmbed';

describe('SvgEmbed sanitization (CodeRabbit security regression)', () => {
  it('strips external resource elements (image/use/feImage) — no outbound request path', () => {
    const code = `<svg viewBox="0 0 100 50" xmlns="http://www.w3.org/2000/svg">
      <image href="https://evil.example/x.png" x="0" y="0" width="50" height="50" />
      <use href="https://evil.example/defs.svg#a" />
      <filter id="f"><feImage href="https://evil.example/f.png" /></filter>
      <rect x="10" y="10" width="20" height="20" />
    </svg>`;
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code }));
    expect(markup).not.toContain('evil.example');
    expect(markup).not.toContain('<image');
    expect(markup).not.toContain('<use');
    expect(markup).not.toContain('feImage');
    expect(markup).toContain('<rect');
  });

  it('strips external url() and href references but keeps local fragment references', () => {
    const code = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 60">
      <defs>
        <linearGradient id="safeGradient"><stop offset="0" stop-color="#fff" /></linearGradient>
        <marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" />
        </marker>
      </defs>
      <rect width="80" height="40"
        fill="url(#safeGradient)"
        filter="url(https://evil.example/filter)"
        clip-path="url(\"https://evil.example/clip\")" />
      <path d="M5 20 L70 20" stroke="#333" marker-end="url(#arrowhead)" />
      <a href="#localTarget"><rect id="localTarget" x="5" y="5" width="8" height="8" /></a>
      <a href="https://evil.example/page"><rect x="20" y="5" width="8" height="8" /></a>
      <rect width="10" height="10" fill="url(#safeGradient)" />
    </svg>`;
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code }));

    expect(markup).not.toContain('evil.example');
    expect(markup).toContain('fill="url(#safeGradient)"');
    expect(markup).toContain('marker-end="url(#arrowhead)"');
    expect(markup).toContain('href="#localTarget"');
    expect(markup).not.toContain('filter=');
    expect(markup).not.toContain('clip-path=');
  });

  it('strips script / event handlers / javascript: href', () => {
    const code = `<svg xmlns="http://www.w3.org/2000/svg">
      <script>alert(1)</script>
      <rect onclick="alert(2)" href="javascript:alert(3)" x="0" y="0" width="10" height="10" />
    </svg>`;
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code }));
    expect(markup).not.toContain('script');
    expect(markup).not.toContain('alert');
    expect(markup).not.toContain('onclick');
    expect(markup).not.toContain('javascript:');
  });

  it('keeps benign internal shapes', () => {
    const code = `<svg viewBox="0 0 100 50" xmlns="http://www.w3.org/2000/svg"><rect x="10" y="10" width="40" height="20" fill="#4f8" /></svg>`;
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code }));
    expect(markup).toContain('<rect');
    expect(markup).toContain('fill="#4f8"');
  });

  it('falls back to source code when sanitization strips everything (审查 P3)', () => {
    // script-only / 纯事件处理器被整块剥离（含空壳 <svg>）→ 显示源码
    // 而非空白卡：用户能看到模型输出了什么（源码文本被转义，无执行面）
    const code = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>';
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code }));
    expect(markup).toContain('<pre');
    expect(markup).not.toContain('<script>alert'); // 未转义的原始标签不存在
    expect(markup).not.toContain('diagram-card'); // 不渲染空壳卡
  });

  it('renders empty input as source fallback (no blank card)', () => {
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code: '' }));
    expect(markup).toContain('<pre');
  });

  it('strips style attribute carrying external url() (审查 R5 P1)', () => {
    // DOMPurify 非 CSS sanitizer——style 属性可携带 url(https://…) 触发
    // 外部请求/数据外带；FORBID_ATTR: ['style'] 必须剥掉整个属性
    const code =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60">' +
      '<rect width="50" height="50" style="fill:url(https://evil.example/x)"/>' +
      '<circle cx="10" cy="10" r="5" style="stroke:red"/>' +
      '<path d="M0 0 L10 10" stroke="#333"/>' +
      '</svg>';
    const markup = renderToStaticMarkup(createElement(SvgEmbed, { code }));
    expect(markup).not.toContain('evil.example');
    expect(markup).not.toContain('style=');
    // 元素与合法属性保留（图仍渲染）
    expect(markup).toContain('<rect');
    expect(markup).toContain('<circle');
  });
});
