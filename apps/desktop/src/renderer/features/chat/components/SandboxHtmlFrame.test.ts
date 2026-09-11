import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { SandboxHtmlFrame, HtmlRenderFallback } from './SandboxHtmlFrame';

describe('SandboxHtmlFrame', () => {
  it('renders a sandboxed iframe with the html as srcdoc (happy path)', () => {
    const markup = renderToStaticMarkup(
      createElement(SandboxHtmlFrame, { html: '<h1>Hello</h1>' })
    );
    expect(markup).toContain('<iframe');
    expect(markup).toContain('sandbox="allow-scripts"');
    expect(markup).toContain('srcDoc=');
    expect(markup).toContain('HTML 预览');
  });
});

describe('HtmlRenderFallback', () => {
  it('renders the failure card with view-source / open / copy actions', () => {
    const markup = renderToStaticMarkup(
      createElement(HtmlRenderFallback, {
        copied: false,
        onViewSource: () => {},
        onOpenBrowser: () => {},
        onCopy: () => {},
      })
    );
    expect(markup).toContain('HTML 渲染失败');
    expect(markup).toContain('查看源码');
    expect(markup).toContain('浏览器打开');
    expect(markup).toContain('复制内容');
  });

  it('shows "已复制" when the copied flag is set', () => {
    const markup = renderToStaticMarkup(
      createElement(HtmlRenderFallback, {
        copied: true,
        onViewSource: () => {},
        onOpenBrowser: () => {},
        onCopy: () => {},
      })
    );
    expect(markup).toContain('已复制');
    expect(markup).not.toContain('复制内容');
  });
});
