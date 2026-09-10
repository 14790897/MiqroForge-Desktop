import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { IPC, IPC_EVENTS } from '../../../../shared/ipc';
import type { UserInputCardEntry } from '../../../contexts/UserInputContext';
import { ConfirmCard } from './ConfirmCard';

function entry(overrides: Partial<UserInputCardEntry> = {}): UserInputCardEntry {
  return {
    request: {
      input_id: 'user_input_abc123',
      title: '确认执行方案？',
      message: '我将执行 4 个步骤，包含：',
      steps: [
        { id: 'search_papers', title: '搜索并下载相关论文' },
        { id: 'query_price', title: '查询供应商价格（国内）' },
      ],
      choices: [
        { id: 'confirm', label: '确认执行' },
        { id: 'adjust', label: '调整方案', role: 'adjust' },
        { id: 'cancel', label: '取消', role: 'cancel' },
      ],
      timeout_seconds: 120,
      allow_remember_choice: true,
    },
    state: 'pending',
    ...overrides,
  };
}

function render(entry_: UserInputCardEntry, opts?: { initialExpanded?: boolean }): string {
  return renderToStaticMarkup(
    createElement(ConfirmCard, {
      entry: entry_,
      onResolve: () => {},
      initialExpanded: opts?.initialExpanded,
    }),
  );
}

describe('IPC channels (issue #646)', () => {
  it('defines resolve channel + renderer event names', () => {
    expect(IPC.USER_INPUT_RESOLVE).toBe('userInput:resolve');
    expect(IPC_EVENTS.USER_INPUT_REQUEST).toBe('userInput:request');
    expect(IPC_EVENTS.USER_INPUT_RESOLVED).toBe('userInput:resolved');
  });
});

describe('ConfirmCard (Hermes 工具行式, 2026-08-27)', () => {
  it('pending: renders title, countdown meta, run bar (steps hidden until expand)', () => {
    const html = render(entry());
    // 标题（小字）+ 倒计时 meta
    expect(html).toContain('确认执行方案？');
    expect(html).toContain('后自动取消');
    // HermesConfirmBar：Run=确认执行 / 修改计划 / 取消
    expect(html).toContain('确认执行');
    expect(html).toContain('修改计划');
    expect(html).toContain('取消');
    // Hermes 展开区默认收起——步骤不可见（initialExpanded 时可见）
    expect(html).not.toContain('搜索并下载相关论文');
  });

  it('pending with initialExpanded shows message + steps immediately', () => {
    const html = render(entry(), { initialExpanded: true });
    expect(html).toContain('我将执行 4 个步骤，包含：');
    expect(html).toContain('搜索并下载相关论文');
    expect(html).toContain('查询供应商价格（国内）');
  });

  it('confirmed: receipt — 已确认 + title, no countdown', () => {
    const e = entry({
      state: 'confirmed',
      choiceId: 'confirm',
      choiceLabel: '确认执行',
      resolvedAt: new Date('2026-08-11T12:03:21').getTime(),
    });
    const html = render(e);
    expect(html).toContain('已确认');
    expect(html).toContain('已确认执行方案');
    expect(html).not.toContain('后自动取消');
  });

  it('cancelled: title becomes 已取消', () => {
    const e = entry({
      state: 'cancelled',
      choiceId: 'cancel',
      choiceLabel: '取消',
      resolvedAt: new Date('2026-08-11T12:03:21').getTime(),
    });
    const html = render(e);
    expect(html).toContain('已取消执行方案');
  });

  it('modify: title becomes 已修改', () => {
    const e = entry({
      state: 'modify',
      choiceId: 'adjust',
      choiceLabel: '调整方案',
      resolvedAt: new Date('2026-08-11T12:03:21').getTime(),
    });
    const html = render(e);
    expect(html).toContain('已修改执行方案');
  });

  it('custom choices render as buttons in expand area', () => {
    const e = entry();
    e.request.choices = [
      { id: 'confirm', label: '确认执行' },
      { id: 'custom_a', label: '自定义选项A' },
    ];
    const html = render(e, { initialExpanded: true });
    expect(html).toContain('自定义选项A');
  });

  it('live step status renders progress glyphs', () => {
    const e = entry({
      stepsStatus: { search_papers: { status: 'success' }, query_price: { status: 'running' } },
    });
    const html = render(e, { initialExpanded: true });
    expect(html).toContain('✓');
    expect(html).toContain('◌');
  });
});
