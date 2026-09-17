/**
 * #1034 思考块增量渲染：分段 + 逐段记忆化。
 *
 * 原来 ThinkBlock 每次 flush 都把**整段** reasoning 交给 <MarkdownContent>
 * 重新解析（measure 报告 §5 第 3 条：最可疑的放大器）。改成按空行切段，
 * 每段一个 memo 组件：只有还在增长的最后一段重新解析，已渲染的头部段落
 * 因 props 未变而跳过。
 *
 * 分段点必须保持 CommonMark 语义：
 *  - 围栏代码块内的空行不是分段点（代码块会被切碎）；
 *  - 空行后若是列表项，也不分段（`1. a\n\n2. b` 拆开会让第二段重新从 1 编号）。
 */
import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ThinkBlock, splitReasoningSegments } from './ThinkBlock';

describe('#1034 splitReasoningSegments', () => {
  it('空文本 → 无分段', () => {
    expect(splitReasoningSegments('')).toEqual([]);
  });

  it('无空行 → 单段', () => {
    expect(splitReasoningSegments('1. 理解需求\n- 要点一')).toEqual(['1. 理解需求\n- 要点一']);
  });

  it('空行分段，且不留空段', () => {
    expect(splitReasoningSegments('第一段\n\n第二段\n\n第三段')).toEqual([
      '第一段',
      '第二段',
      '第三段',
    ]);
    expect(splitReasoningSegments('第一段\n\n\n\n第二段\n\n')).toEqual(['第一段', '第二段']);
  });

  it('围栏代码块内的空行不分段', () => {
    const text = [
      '先看代码：',
      '',
      '```python',
      'def f():',
      '',
      '    return 1',
      '```',
      '',
      '再看结论',
    ].join('\n');
    expect(splitReasoningSegments(text)).toEqual([
      '先看代码：',
      '```python\ndef f():\n\n    return 1\n```',
      '再看结论',
    ]);
  });

  it('空行后是列表项时不分段（保住有序列表续号）', () => {
    expect(splitReasoningSegments('1. 第一步\n\n2. 第二步')).toEqual(['1. 第一步\n\n2. 第二步']);
    expect(splitReasoningSegments('- 甲\n\n- 乙')).toEqual(['- 甲\n\n- 乙']);
  });

  it('空行后是缩进续行时不分段（loose list 的段落续行、缩进代码）', () => {
    // 切开会让 `1. step` 与它的缩进续行变成两个块，缩进语义丢失
    expect(splitReasoningSegments('1. step\n\n   detail')).toEqual(['1. step\n\n   detail']);
    expect(splitReasoningSegments('1. step\n\n     detail')).toEqual(['1. step\n\n     detail']);
    expect(splitReasoningSegments('- 甲\n\n  续行文字')).toEqual(['- 甲\n\n  续行文字']);
    // 缩进不足 2 空格依旧是普通块边界，切段行为不变
    expect(splitReasoningSegments('第一段\n\n 缩进一格的段')).toEqual(['第一段', ' 缩进一格的段']);
  });

  it('尾随空行不产生空段', () => {
    expect(splitReasoningSegments('只有一段\n')).toEqual(['只有一段']);
  });

  it('占位符（#1034 省略行）自成分段', () => {
    const segments = splitReasoningSegments('…已省略 1234 字\n\n后面的思考');
    expect(segments).toEqual(['…已省略 1234 字', '后面的思考']);
  });
});

describe('#1034 ThinkBlock 分段渲染保持既有契约', () => {
  it('所有分段的文本都被渲染出来', () => {
    const markup = renderToStaticMarkup(
      createElement(ThinkBlock, {
        reasoning: '第一段要点\n\n第二段要点',
        mode: 'think',
        live: true,
      })
    );
    expect(markup).toContain('第一段要点');
    expect(markup).toContain('第二段要点');
    expect(markup).toContain('深度思考');
  });

  it('空 reasoning 仍然完全不渲染（原有契约）', () => {
    const markup = renderToStaticMarkup(createElement(ThinkBlock, { reasoning: '', mode: 'fast' }));
    expect(markup).toBe('');
  });
});

describe('#1034 分段只用于流式：终态恢复整篇 CommonMark 语义', () => {
  /** 链接引用定义跨空行生效——分段渲染会把它切成两个独立文档，定义失效。 */
  const CROSS_BLOCK = '[ref]: https://example.com "定义"\n\n见 [ref] 与 `code`';

  const render = (live: boolean) =>
    renderToStaticMarkup(
      createElement(ThinkBlock, { reasoning: CROSS_BLOCK, live, defaultOpen: true })
    );

  it('live：分段渲染，引用定义跨不了段（`[ref]` 保持字面量）', () => {
    // 这是分段模式的已知代价，接受它是因为流式期间每 60ms 只重解析最后一段；
    // 该用例把代价写进契约，防止有人误以为分段是「等价」渲染。
    const markup = render(true);
    expect(markup).toContain('[ref]');
    expect(markup).not.toContain('href="https://example.com"');
  });

  it('终态：整篇一次解析，引用定义生效（不再分段）', () => {
    const markup = render(false);
    expect(markup).toContain('href="https://example.com"');
    expect(markup).not.toContain('[ref]');
  });

  it('终态与 live 都不丢内容：两段文本都在', () => {
    for (const live of [true, false]) {
      const markup = render(live);
      expect(markup).toContain('见');
      expect(markup).toContain('code');
      expect(markup).toContain('深度思考');
    }
  });
});
