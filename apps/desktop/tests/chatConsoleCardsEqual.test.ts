import { describe, expect, it } from 'vitest';
import { cardsEqual } from '../src/renderer/features/chat/ChatConsole';
import type { UserInputCardEntry } from '../src/renderer/contexts/UserInputContext';

/**
 * #1071 review P1（2026-09-16）回归锁：`areMessageBubblePropsEqual` 必须把
 * `cards` 纳入比较。
 *
 * 背景：MessageBubble 是 memo 的，比较器原先不看 `cards`，于是
 *  · 卡晚到时气泡不重渲染 → 内联区不画；而 `inlineCardIds`（ChatConsole.tsx:7064）
 *    在 memo 之外算、兜底区已把该卡排除 → 这张卡**哪里都不显示**；
 *  · pending → confirmed/cancelled/modify 的卡保持旧阶段。
 *
 * 不变量（本文件锁语义）：
 *  1. 逐项比身份，**不是**比数组引用——渲染点 `cards={inlineCardsForGroup(group)}`
 *     （L8097）每次都 `filter` 出新数组，比引用恒 false 会废掉 #538 的 memo 优化；
 *  2. 内容不变 → 相等（气泡可跳过渲染）；换长度 / 换条目 / 条目换身份 → 不等。
 */

const card = (id: string): UserInputCardEntry =>
  ({ request: { input_id: id } }) as unknown as UserInputCardEntry;

describe('cardsEqual (#1071 review P1)', () => {
  it('同一引用、同为 undefined、undefined 与 [] 都算相等', () => {
    const same = [card('a')];
    expect(cardsEqual(same, same)).toBe(true);
    expect(cardsEqual(undefined, undefined)).toBe(true);
    // undefined 与 [] 同义（渲染点恒传数组，此处是防御口径）
    expect(cardsEqual(undefined, [])).toBe(true);
    expect(cardsEqual([], undefined)).toBe(true);
    expect(cardsEqual([], [])).toBe(true);
  });

  it('内容不变但数组是新引用时仍算相等（保住 #538 memo）', () => {
    const a = [card('a'), card('b')];
    // 模拟 inlineCardsForGroup 每次调用都返回新数组：元素身份相同
    const b = [a[0], a[1]];
    expect(b).not.toBe(a);
    expect(cardsEqual(a, b)).toBe(true);
  });

  it('长度不同 / 条目身份变化 → 不相等（卡晚到、卡换阶段要能触发重渲染）', () => {
    const first = card('a');
    expect(cardsEqual([first], [])).toBe(false);
    expect(cardsEqual([], [first])).toBe(false);
    // 卡晚到：0 → 1 张
    expect(cardsEqual([], [card('a')])).toBe(false);
    // 卡换阶段：同 id 但是新条目对象
    expect(cardsEqual([card('a')], [card('a')])).toBe(false);
    expect(cardsEqual([first], [first, card('b')])).toBe(false);
  });
});
