/**
 * #1034 分段渲染等价性：`segmented render ≈ 完整 Markdown render`。
 *
 * 上一版测试（thinkBlockSegments.test.ts）只锁「想到的特例」——围栏、列表续号、
 * 缩进续行各自一条断言。那种写法只能证明**已知**的坑被避开了，证明不了
 * 「切段没有引入其它差异」：真出问题时，是一段构造还没被想到。
 *
 * 这里换成整体口径：对每个 markdown 构造，分别算出
 *   1) 整篇一次解析（终态路径 `<MarkdownContent content={整篇}>`）；
 *   2) 分段逐个解析（流式路径 `<SegmentedReasoning>` 的渲染模型，逐段
 *      `<MarkdownContent content={段}>` 后拼接）；
 * 再比较两者的「结构 + 文本形状」，而不是要求 HTML 逐字节相等——分段会在段与段
 * 之间插入各自的包裹 div（`min-w-0 break-words`），那是分段机制的产物而非内容，
 * 用它做判据会把 26 个真正等价的构造全判成差异（实测过）。
 *
 * 比较器（`shape`）：
 *  - 去掉包裹 div 的「接缝」；
 *  - 标签原样保留（多一个 / 少一个块级标签必须被发现）；
 *  - 文本节点把空白折叠成单空格并 trim（块边界处多一个换行在 HTML 里不可见，
 *    不该判为差异）。
 * 已知盲点：文本节点**内部**贴着行内标签的空白差异会被抹掉。这已由「标签序列」
 * 一侧兜住大半（丢一个块级标签必然可见），剩下的空白差异不影响渲染。
 *
 * 分两节：
 *  - 「等价」：断言两条路径形状完全相同。谁把分段改成「见空行就切」，列表 /
 *    围栏 / 表格几条会立刻变红。
 *  - 「已知差异」：断言**具体的差异症状**，把差异清单钉在用例里——它不会随着
 *    某次改动静默变大或变小，改动者必须回来改这里。
 */
import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { splitReasoningSegments } from './ThinkBlock';
import { MarkdownContent } from './MarkdownContent';

/** 渲染一段 markdown，与 ThinkBlock 内部（含分段器）用同一组件、同一参数。 */
const render = (text: string): string =>
  renderToStaticMarkup(createElement(MarkdownContent, { content: text, disableDiagrams: true }));

const SEAM = '</div><div class="min-w-0 break-words" style="overflow-wrap:anywhere">';
const OPEN = '<div class="min-w-0 break-words" style="overflow-wrap:anywhere">';
/** 标签或文本节点。 */
const TOKEN = /<[^>]+>|[^<]+/g;

/** 去掉包裹 div（含段与段之间的接缝），返回标签序列 + 文本形状。 */
function shape(html: string): string {
  return (
    html
      .split(SEAM)
      .join('')
      .replace(OPEN, '')
      .replace(/<\/div>$/, '')
      .match(TOKEN) ?? []
  )
    .map((token) => (token.startsWith('<') ? token : token.replace(/\s+/g, ' ').trim()))
    .filter((token) => token !== '')
    .join('|');
}

/** 流式路径的渲染模型：逐段渲染再拼接。 */
function segmentedShape(text: string): string {
  return shape(splitReasoningSegments(text).map(render).join(''));
}

const fullShape = (text: string): string => shape(render(text));

// ── 等价构造 ─────────────────────────────────────────────────────────
// 每条都覆盖一类「空行到底是不是块边界」的判断，名字即契约。
const EQUIVALENT: Array<[string, string]> = [
  // 引用：空行确实分隔两个 blockquote（CommonMark 语义），切段不影响结果
  ['blockquote 两段', '> 引用第一段\n\n> 引用第二段'],
  ['blockquote 懒续行', '> 引用第一段\n\n引用续行'],
  ['blockquote 套列表', '> - a\n> - b\n\n> - c'],
  ['blockquote 套引用', '> > a\n\n> > b'],
  // 列表：空行不能切（会重编号 / 丢 loose 间距），整段留在一个 segment
  ['嵌套列表', '- a\n  - b\n    - c\n\n- d'],
  ['loose list', '- 甲\n\n- 乙\n\n- 丙'],
  ['有序 loose list', '1. 第一步\n\n2. 第二步'],
  ['GFM 任务列表', '- [ ] 未完成\n\n- [x] 已完成'],
  ['列表内含表格', '- 项\n\n  | a | b |\n  |---|---|\n  | 1 | 2 |'],
  ['列表内引用', '- item\n\n  > quote inside'],
  ['紧凑列表缩进续行', '1. step\n\n   detail'],
  ['列表后的围栏块', '- a\n\n```\nx\n\n y\n```'],
  // 代码：围栏内的空行是内容，不是边界
  ['缩进代码块', '段落\n\n    indented code\n    second line'],
  ['缩进代码续行', '    code\n\n    more code'],
  [
    '围栏代码块（```，内含空行）',
    '先看代码：\n\n```python\ndef f():\n\n    return 1\n```\n\n再看结论',
  ],
  ['围栏代码块（~~~，内含空行）', '~~~\na\n\nb\n~~~\n\n后面'],
  ['未闭合围栏', '```\na\n\nb'],
  // 表格
  ['GFM 表格', '段落\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n表后'],
  // 标题 / 分隔线
  ['ATX 标题', '# 标题\n\n正文\n\n## 二级'],
  ['setext 标题', '标题\n===\n\n正文'],
  ['thematic break', '上面\n\n---\n\n下面'],
  // 行内
  ['硬换行', '第一行  \n第二行\n\n下一段'],
  ['裸链接与 autolink', '见 https://example.com 与 <https://a.b>\n\n后面'],
  // HTML：type 6/7 块本身以空行结束，切段与整篇语义一致
  ['HTML 块（div）', '<div>\nfoo\n\nbar\n</div>'],
  ['HTML 注释', '<!-- c -->\n\n文本'],
];

describe('#1034 分段渲染与整篇渲染等价（构造清单）', () => {
  for (const [name, text] of EQUIVALENT) {
    it(name, () => {
      expect(segmentedShape(text)).toBe(fullShape(text));
    });
  }

  it('清单本身非空洞：等价用例都真的切成了多段或明确单段', () => {
    // 防止有人把语料改写成「整篇只有一段」——那样等价断言恒真。
    const segmented = EQUIVALENT.map(([, text]) => splitReasoningSegments(text).length);
    expect(segmented.filter((n) => n > 1).length).toBeGreaterThanOrEqual(15);
    expect(segmented.filter((n) => n === 1).length).toBeGreaterThanOrEqual(8);
  });
});

// ── 已知差异清单 ─────────────────────────────────────────────────────
// 这几条**真的不等价**。它们不是「分段策略没写好」，而是分段这件事本身的代价：
// 每个 segment 是一份独立的 CommonMark 文档，凡是「跨块才有意义」的构造都会失效。
// 之所以可以接受：分段只用于**流式期间**，终态走整篇解析（见 ThinkBlock 注释与
// thinkBlockSegments.test.ts 的 live/终态对比）。
describe('#1034 分段渲染不等价的构造（差异清单，逐条钉住症状）', () => {
  it('链接引用定义：定义不跨段，`[ref]` 保持字面量', () => {
    const text = '[ref]: https://example.com "定义"\n\n见 [ref] 与 `code`';
    expect(splitReasoningSegments(text)).toHaveLength(2);
    // 整篇：定义生效
    expect(fullShape(text)).toContain('href="https://example.com"');
    // 分段：定义留在第 1 段（渲染为空），第 2 段只剩字面量
    expect(segmentedShape(text)).not.toContain('href="https://example.com"');
    expect(segmentedShape(text)).toContain('[ref]');
  });

  it('GFM 脚注定义：同一根因——脚注不跨段，脚注区整体消失', () => {
    const text = '文本[^1]\n\n[^1]: 脚注内容';
    expect(splitReasoningSegments(text)).toHaveLength(2);
    // 整篇：<sup> 角标 + 文末 footnotes 区
    expect(fullShape(text)).toContain('data-footnotes');
    // 分段：`文本[^1]` 原样输出，脚注区不存在
    expect(segmentedShape(text)).not.toContain('data-footnotes');
    expect(segmentedShape(text)).toContain('文本[^1]');
  });

  it('HTML 块 type 1（<pre>）：块内空行是内容，切段后尾段被段落化', () => {
    const text = '<pre>\nfoo\n\nbar\n</pre>';
    expect(splitReasoningSegments(text)).toHaveLength(2);
    // 整篇：整块是原始 HTML 文本，中间空行原样保留（不产生 <p>）
    expect(fullShape(text)).not.toContain('<p');
    // 分段：后半段 `bar\n</pre>` 变成普通段落 → 多出 <p>，空行同时丢失
    expect(segmentedShape(text)).toContain('<p');
    expect(segmentedShape(text)).not.toBe(fullShape(text));
  });

  it('HTML 块 type 1 之后仍有正文时同样偏一段', () => {
    const text = '<pre>\nfoo\n\nbar\n</pre>\n\n后面';
    expect(splitReasoningSegments(text)).toHaveLength(3);
    expect(segmentedShape(text)).toContain('<p');
    expect(segmentedShape(text)).not.toBe(fullShape(text));
  });
});
