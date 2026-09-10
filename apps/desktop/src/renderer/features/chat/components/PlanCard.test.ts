import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { PlanCard, type PlanCardEntry } from './PlanCard';

function entry(overrides: Partial<PlanCardEntry> = {}): PlanCardEntry {
  return {
    title: '生成 MOF 实验报告',
    goal: '整理 5 篇论文并生成 Workflow',
    steps: [
      { name: '论文检索', tools: ['web_search'] },
      { name: '生成报告', tools: ['write_file'] },
      { name: '上传 Qraft', tools: ['upload'] },
    ],
    permissions: ['network_read', 'workspace_write', 'external_upload'],
    phase: 'wait_confirm',
    ...overrides,
  };
}

function render(e: PlanCardEntry): string {
  return renderToStaticMarkup(createElement(PlanCard, { entry: e, onResolve: () => {} }));
}

describe('PlanCard (#646-v2)', () => {
  it('wait_confirm: renders a lightweight workstream with decision actions', () => {
    const html = render(entry());
    expect(html).toContain('生成 MOF 实验报告');
    expect(html).toContain('论文检索');
    expect(html).toContain('生成报告');
    expect(html).toContain('涉及');
    expect(html).toContain('网络');
    expect(html).toContain('外部');
    expect(html).toContain('按当前方案执行');
    expect(html).toContain('调整方案');
    expect(html).toContain('取消');
  });

  it('running: shows step progress without decision controls', () => {
    const e = entry({ phase: 'running', stepStatus: { '论文检索': 'done', '生成报告': 'running' } });
    const html = render(e);
    expect(html).toContain('执行中');
    expect(html).toContain('论文检索');
    expect(html).not.toContain('按当前方案执行');
    expect(html).not.toContain('调整方案');
    expect(html).not.toContain('涉及');
    expect(html).not.toContain('网络');
  });

  it('completed / cancelled: summary states remain compact', () => {
    expect(render(entry({ phase: 'completed' }))).toContain('已完成');
    expect(render(entry({ phase: 'cancelled' }))).toContain('已取消');
  });
});
