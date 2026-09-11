/**
 * ThinkBlock 回归测试（#858 → #905）：fast/think 两种模式下都渲染
 * 思考内容 —— 对应 #783「极速/深度都展示思考过程」的回归验证。
 */
import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ThinkBlock } from './ThinkBlock';

describe('ThinkBlock regression check (#858 gate removal)', () => {
  it('fast 模式：🚀 图标 + 快速思考标题 + 思考内容都渲染', () => {
    const markup = renderToStaticMarkup(
      createElement(ThinkBlock, {
        reasoning: '1. 理解需求\n- 要点一\n- 要点二',
        mode: 'fast',
        live: true,
      })
    );
    expect(markup).toContain('🚀');
    expect(markup).toContain('快速思考');
    expect(markup).toContain('理解需求');
    expect(markup).toContain('要点一');
  });

  it('think 模式：🧠 图标 + 深度思考标题 + 思考内容都渲染', () => {
    const markup = renderToStaticMarkup(
      createElement(ThinkBlock, { reasoning: '1. 理解需求', mode: 'think', live: true })
    );
    expect(markup).toContain('🧠');
    expect(markup).toContain('深度思考');
    expect(markup).toContain('理解需求');
  });

  it('空 reasoning 时不渲染（原有契约不变）', () => {
    const markup = renderToStaticMarkup(createElement(ThinkBlock, { reasoning: '', mode: 'fast' }));
    expect(markup).toBe('');
  });
});
