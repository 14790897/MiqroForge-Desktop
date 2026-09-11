import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { DocxPreview } from './DocxPreview';

describe('DocxPreview', () => {
  it('renders headings with the level applied to the class', () => {
    const html = renderToStaticMarkup(
      createElement(DocxPreview, {
        blocks: [{ type: 'heading', level: 1, text: '一级标题' }],
      })
    );
    expect(html).toContain('一级标题');
    expect(html).toContain('text-lg');
  });

  it('renders tables with the first row emphasized', () => {
    const html = renderToStaticMarkup(
      createElement(DocxPreview, {
        blocks: [
          {
            type: 'table',
            rows: [
              ['H1', 'H2'],
              ['v1', 'v2'],
            ],
          },
        ],
      })
    );
    expect(html).toContain('<table');
    expect(html).toContain('H1');
    expect(html).toContain('v2');
  });

  it('renders inline images from data URLs', () => {
    const html = renderToStaticMarkup(
      createElement(DocxPreview, {
        blocks: [{ type: 'image', data_url: 'data:image/png;base64,AAAA' }],
      })
    );
    expect(html).toContain('<img');
    expect(html).toContain('data:image/png;base64,AAAA');
  });

  it('shows an empty placeholder when there are no blocks', () => {
    const html = renderToStaticMarkup(createElement(DocxPreview, { blocks: [] }));
    expect(html).toContain('无内容');
  });
});
