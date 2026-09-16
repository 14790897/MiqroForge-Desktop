import { describe, expect, it } from 'vitest';
import { extractDoi, parseReferenceList, remarkCitations } from './references';

describe('extractDoi', () => {
  it('extracts DOI from doi.org / dx.doi.org / bare doi:', () => {
    expect(extractDoi('https://doi.org/10.1016/j.matt.2023.01.001')).toBe(
      '10.1016/j.matt.2023.01.001'
    );
    expect(extractDoi('https://dx.doi.org/10.1000/abc')).toBe('10.1000/abc');
    expect(extractDoi('doi:10.1000/abc')).toBe('10.1000/abc');
  });

  it('returns undefined for non-DOI URLs', () => {
    expect(extractDoi('https://example.com/paper')).toBeUndefined();
    expect(extractDoi('')).toBeUndefined();
  });
});

describe('parseReferenceList', () => {
  it('parses the #671 new semicolon format into structured fields', () => {
    const md = [
      '结论 [1]。',
      '',
      '## 参考文献',
      '[1] 张三, 李四；MOF 造粒工艺综述；材料学报；2023；https://doi.org/10.1016/j.matt.2023.01.001',
      '[2] 王五；介孔氧化铝成型损失研究；化工进展；2020；https://example.com/paper2',
    ].join('\n');
    const refs = parseReferenceList(md);
    expect(refs).toEqual([
      {
        num: 1,
        authors: '张三, 李四',
        title: 'MOF 造粒工艺综述',
        journal: '材料学报',
        year: '2023',
        url: 'https://doi.org/10.1016/j.matt.2023.01.001',
        doi: '10.1016/j.matt.2023.01.001',
      },
      {
        num: 2,
        authors: '王五',
        title: '介孔氧化铝成型损失研究',
        journal: '化工进展',
        year: '2020',
        url: 'https://example.com/paper2',
        doi: undefined,
      },
    ]);
  });

  it('still parses the old comma format (no author)', () => {
    const refs = parseReferenceList(
      '[1] MOF 造粒工艺综述, 材料学报, 2023, https://doi.org/10.1016/j.matt.2023.01.001'
    );
    expect(refs).toEqual([
      {
        num: 1,
        title: 'MOF 造粒工艺综述',
        journal: '材料学报',
        year: '2023',
        url: 'https://doi.org/10.1016/j.matt.2023.01.001',
        doi: '10.1016/j.matt.2023.01.001',
      },
    ]);
  });

  it('returns [] when there is no reference list', () => {
    expect(parseReferenceList('普通回答，没有参考文献。')).toEqual([]);
    expect(parseReferenceList('')).toEqual([]);
  });

  it('handles an entry with only a title', () => {
    const refs = parseReferenceList('[3] 只有标题 https://example.com/x');
    expect(refs).toEqual([
      { num: 3, title: '只有标题', url: 'https://example.com/x', doi: undefined },
    ]);
  });

  it('ignores markdown link-reference definitions like `[1]: http://…`', () => {
    expect(parseReferenceList('[1]: https://example.com/x')).toEqual([]);
  });

  it('rejects an entry whose URL is not at the end of the line', () => {
    // `[1] See https://example.com for details` 的 URL 后还有文本，不应被当参考文献
    expect(parseReferenceList('[1] See https://example.com for details')).toEqual([]);
  });
});

describe('remarkCitations', () => {
  it('splits valid [n] text into links and leaves code blocks untouched', () => {
    const tree: any = {
      type: 'root',
      children: [
        { type: 'paragraph', children: [{ type: 'text', value: '结论 [1] 与 [2] 但 [3] 不是' }] },
        { type: 'code', lang: 'md', value: '代码 [1] 不转' },
      ],
    };
    remarkCitations(new Set([1, 2]))(tree);

    const para = tree.children[0].children;
    expect(para.map((n: any) => n.type)).toEqual(['text', 'link', 'text', 'link', 'text']);
    expect(para[1]).toEqual({
      type: 'link',
      url: '#citation-1',
      children: [{ type: 'text', value: '[1]' }],
    });
    expect(para[3].url).toBe('#citation-2');
    // [3] 不在 validNums 里 → 留在末尾文本中，不被转成链接
    expect(para[4].value).toBe(' 但 [3] 不是');
    // code 节点不被触碰
    expect(tree.children[1].value).toBe('代码 [1] 不转');
  });

  it('does not transform [n] inside existing link labels', () => {
    const tree: any = {
      type: 'root',
      children: [
        {
          type: 'paragraph',
          children: [
            {
              type: 'link',
              url: 'https://example.com',
              children: [{ type: 'text', value: 'report [1]' }],
            },
          ],
        },
      ],
    };
    remarkCitations(new Set([1]))(tree);
    // 链接内的 [1] 保持不变，不生成嵌套 citation 链接
    expect(tree.children[0].children[0].children).toEqual([{ type: 'text', value: 'report [1]' }]);
  });
});
