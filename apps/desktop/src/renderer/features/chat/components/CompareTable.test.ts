import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CompareTable } from './CompareTable';
import type { CompareData } from './compareData';

const sample: CompareData = {
  title: 'MOF 造粒工艺对比',
  schemes: ['路径A 喷雾干燥', '路径B 挤出滚圆'],
  parameters: [
    { name: '压力', unit: 'MPa', range: '2–4', source: 'ref-1', values: ['2–4', '5–8'] },
    { name: '粘结剂比例', unit: '%', values: ['3–5', '2–4'] },
  ],
  citations: [{ id: 'ref-1', title: '造粒工艺综述', url: 'https://example.com/x' }],
};

describe('CompareTable', () => {
  it('renders title, scheme column headers and parameter rows', () => {
    const markup = renderToStaticMarkup(createElement(CompareTable, { data: sample }));
    expect(markup).toContain('MOF 造粒工艺对比');
    expect(markup).toContain('路径A 喷雾干燥');
    expect(markup).toContain('路径B 挤出滚圆');
    expect(markup).toContain('压力');
    expect(markup).toContain('粘结剂比例');
    expect(markup).toContain('2–4');
  });

  it('renders a copy button (issue #878 复制)', () => {
    const markup = renderToStaticMarkup(createElement(CompareTable, { data: sample }));
    expect(markup).toContain('复制');
  });

  it('renders the citation title for a sourced parameter', () => {
    const markup = renderToStaticMarkup(createElement(CompareTable, { data: sample }));
    expect(markup).toContain('造粒工艺综述');
  });

  it('renders 「未标注」 for a parameter without a source', () => {
    const markup = renderToStaticMarkup(createElement(CompareTable, { data: sample }));
    expect(markup).toContain('未标注');
  });

  it('shows a placeholder when there are no parameters', () => {
    const empty: CompareData = { schemes: ['A', 'B'], parameters: [] };
    const markup = renderToStaticMarkup(createElement(CompareTable, { data: empty }));
    expect(markup).toContain('（无对比数据）');
  });
});
