import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ReportView } from './ReportView';
import type { SureCheckEnvelope } from '../../../shared/sureReport';

function makeEnvelope(overrides?: {
  severity?: string;
  findings?: unknown[];
  notChecked?: unknown[];
}): SureCheckEnvelope {
  return {
    command: 'check',
    exit_code: 1,
    outcome: 'not_green',
    protocol_version: 1,
    sure_version: '0.1.2',
    details: {
      project: 'D:\\demo',
      purpose: 'check',
      mode: 'inspect_only',
      state: 'finished',
      report: {
        schema_version: 3,
        project_fingerprint: 'fp_test',
        aggregate: {
          severity: overrides?.severity ?? 'not_enough_checked',
          headline: 'Not enough could be checked to say whether this is ready.',
          is_green: false,
        },
        ready_for_hand_off: false,
        must_caveat_requirements: true,
        capability: { tier: 0, summary: 'snapshot only', blind_spots: [] },
        caveat: '无法核对原始需求(未提供)。',
        findings: (overrides?.findings ?? [
          {
            id: 'fnd_1',
            title: '项目在生产代码里包含假支付令牌',
            what: '发现了测试密钥。',
            impact: '可能误导为真实支付。',
            severity: 'Must fix',
            status: 'Cannot confirm',
            next_action: '替换为真实支付调用或明确标注演示。',
            is_model_only: false,
            evidence_anchors: [
              { location: 'src/payments.js', locator: 'line 9', subject: 'line_range' },
            ],
          },
        ]) as SureCheckEnvelope['details']['report']['findings'],
        not_checked: (overrides?.notChecked ?? [
          {
            id: 'chk_1',
            title: '项目包含假支付令牌',
            reason: '该检查被计划但未运行。',
            is_critical: false,
          },
        ]) as SureCheckEnvelope['details']['report']['not_checked'],
        totals: { checked: 0, skipped: 0, could_not_run: 5, open_findings: 5 },
      },
      stages: [
        {
          number: 1,
          stage: 'discover',
          title: "Find the project's parts",
          outcome: 'ran',
          detail: 'node (level B)',
        },
        {
          number: 8,
          stage: 'model-assessment',
          title: 'Ask a model to assess the project',
          outcome: 'not_run',
          reason: 'analysis_provider_disabled',
          reason_explained: '未配置分析 provider。',
        },
      ],
    },
  };
}

function render(env: SureCheckEnvelope): string {
  return renderToStaticMarkup(createElement(ReportView, { envelope: env }));
}

describe('ReportView', () => {
  it('总体结论:not_enough_checked 显示灰色标签与原文 outcome/exit', () => {
    const html = render(makeEnvelope({ severity: 'not_enough_checked' }));
    expect(html).toContain('检查不足,无法判断');
    expect(html).toContain('not_green · exit 1');
    expect(html).toContain('SURE 0.1.2');
  });

  it('not_ready 与 not_enough_checked 渲染不同标签(不美化不混淆)', () => {
    const notReady = render(makeEnvelope({ severity: 'not_ready' }));
    expect(notReady).toContain('尚未就绪');
    expect(notReady).not.toContain('检查不足,无法判断');
  });

  it('findings 完整渲染:标题/what/影响/下一步/锚点/状态', () => {
    const html = render(makeEnvelope());
    expect(html).toContain('项目在生产代码里包含假支付令牌');
    expect(html).toContain('发现了测试密钥。');
    expect(html).toContain('可能误导为真实支付。');
    expect(html).toContain('替换为真实支付调用或明确标注演示。');
    expect(html).toContain('Must fix');
    expect(html).toContain('无法确认');
    expect(html).toContain('src/payments.js');
    expect(html).toContain('line 9');
  });

  it('尚未验证的项目逐条可见(不折叠不隐瞒)', () => {
    const html = render(makeEnvelope());
    expect(html).toContain('尚未验证的项目(1)');
    expect(html).toContain('该检查被计划但未运行。');
  });

  it('阶段表:ran/not_run 标签与 reason_explained 可见', () => {
    const html = render(makeEnvelope());
    expect(html).toContain('已执行');
    expect(html).toContain('未运行');
    expect(html).toContain('未配置分析 provider。');
    expect(html).toContain('fp_test');
  });
});
