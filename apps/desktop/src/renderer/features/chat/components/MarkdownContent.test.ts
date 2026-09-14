import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MarkdownContent } from './MarkdownContent';

describe('MarkdownContent HTML preview swap', () => {
  it('renders the HtmlPreviewCard instead of markdown when content is a full HTML document', () => {
    const html =
      '<!doctype html><html><head><meta charset="utf-8"></head><body><h1>销售仪表盘</h1></body></html>';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: html }));
    expect(markup).toContain('HTML 预览');
    expect(markup).toContain('<iframe');
    expect(markup).toContain('sandbox');
    expect(markup).toContain('销售仪表盘');
  });

  it('renders the HTML inside a ```html fenced block as a preview card', () => {
    const fenced = '```html\n<html><body><p>ok</p></body></html>\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: fenced }));
    expect(markup).toContain('<iframe');
  });

  it('keeps normal markdown rendering for non-HTML content', () => {
    const markup = renderToStaticMarkup(
      createElement(MarkdownContent, { content: '**加粗** 一段普通文本' })
    );
    expect(markup).not.toContain('<iframe');
    expect(markup).toContain('加粗');
  });

  it('renders mermaid fenced block with MermaidBlock (issue #671)', () => {
    const markup = renderToStaticMarkup(
      createElement(MarkdownContent, {
        content: '```mermaid\nflowchart TD\nA[开始] --> B[成型]\n```',
      })
    );
    // SSR 下 useEffect 不执行 → 未渲染前显示源码（muted 预览）
    expect(markup).toContain('flowchart TD');
    expect(markup).toContain('A[开始]');
  });

  it('shows mermaid source while streaming (no render attempt, issue #671)', () => {
    // streaming 期间 MermaidBlock 直接显示源码，不尝试渲染（流式部分语法必失败）
    const markup = renderToStaticMarkup(
      createElement(MarkdownContent, {
        content: '```mermaid\nflowchart TD\nA-->B\n```',
        streaming: true,
      })
    );
    expect(markup).toContain('flowchart TD');
    expect(markup).toContain('A--&gt;B');
  });

  it('keeps normal code block for non-mermaid fenced blocks', () => {
    const markup = renderToStaticMarkup(
      createElement(MarkdownContent, { content: '```python\nprint(1)\n```' })
    );
    // develop 版 pre 是 Codex-style header + rehype-highlight 拆分文本
    expect(markup).toContain('language-python');
    expect(markup).toContain('print');
    expect(markup).toContain('hljs');
  });

  it('handles svg fenced block safely in SSR (renders in browser, issue #671)', () => {
    // SSR 无 window → SvgEmbed 返回空（渲染器在浏览器执行 DOMPurify 消毒）
    const markup = renderToStaticMarkup(
      createElement(MarkdownContent, {
        content:
          '```svg\n<svg viewBox="0 0 100 50" xmlns="http://www.w3.org/2000/svg"><rect x="10" y="10" width="40" height="20" /></svg>\n```',
      })
    );
    expect(markup).not.toContain('viewBox="0 0 100 50"');
  });
});

describe('MarkdownContent syntax highlighting', () => {
  it('adds a language label and hljs token spans to a fenced code block', () => {
    const md = '```ts\nconst x: number = 1;\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: md }));
    expect(markup).toContain('>TypeScript</span>');
    expect(markup).toContain('hljs-keyword');
    expect(markup).toContain('hljs-built_in');
    expect(markup).toContain('hljs-number');
  });

  it('keeps plain text (no label, no hljs) for a code block without a language', () => {
    const md = '```\nplain text\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: md }));
    expect(markup).toContain('plain text');
    expect(markup).not.toContain('hljs-keyword');
  });
});

describe('MarkdownContent compare table (issue #878)', () => {
  it('renders the CompareTable for a valid ```compare JSON block', () => {
    const json = JSON.stringify({
      schemes: ['路径A', '路径B'],
      parameters: [{ name: '压力', values: ['2–4', '5–8'] }],
    });
    const md = '```compare\n' + json + '\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: md }));
    expect(markup).toContain('路径A');
    expect(markup).toContain('压力');
    expect(markup).toContain('浅色底纹');
  });

  it('falls back to a code block when the compare JSON is invalid', () => {
    const md = '```compare\n{not valid json\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: md }));
    expect(markup).toContain('{not valid json');
    expect(markup).not.toContain('浅色底纹');
  });

  it('leaves a normal json fenced block untouched', () => {
    const md = '```json\n{"a": 1}\n```';
    const markup = renderToStaticMarkup(createElement(MarkdownContent, { content: md }));
    expect(markup).toContain('JSON');
    expect(markup).not.toContain('浅色底纹');
  });
});
