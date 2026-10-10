import { describe, expect, it } from 'vitest';
import {
  aggregateLabel,
  aggregateTone,
  findingStatusLabel,
  findingTone,
  formatElapsed,
  rawOutcomeLabel,
  stageOutcomeLabel,
  stageTone,
} from './sureReport';

describe('aggregateTone / aggregateLabel', () => {
  it('四值映射,not_enough_checked 与 not_ready 明显区分', () => {
    expect(aggregateTone('green')).toBe('ok');
    expect(aggregateTone('needs_attention')).toBe('warn');
    expect(aggregateTone('not_ready')).toBe('danger');
    expect(aggregateTone('not_enough_checked')).toBe('muted');
    expect(aggregateTone('not_ready')).not.toBe(aggregateTone('not_enough_checked'));
  });

  it('未知 severity 回退 muted + 原样显示', () => {
    expect(aggregateTone('something_new')).toBe('muted');
    expect(aggregateLabel('something_new')).toBe('something_new');
  });

  it('标签映射已知值', () => {
    expect(aggregateLabel('not_enough_checked')).toBe('检查不足,无法判断');
    expect(aggregateLabel('not_ready')).toBe('尚未就绪');
  });
});

describe('findingTone / findingStatusLabel', () => {
  it('展示形态与 snake_case 两种写法都映射(CLI 输出为 "Must fix")', () => {
    expect(findingTone('Must fix')).toBe('danger');
    expect(findingTone('must_fix')).toBe('danger');
    expect(findingTone('Should fix first')).toBe('warn');
    expect(findingTone('Can fix later')).toBe('info');
    expect(findingTone('Note')).toBe('muted');
  });

  it('未知 severity 回退 muted', () => {
    expect(findingTone('catastrophic')).toBe('muted');
  });

  it('status 标签:已知映射、未知原样、展示形态容忍', () => {
    expect(findingStatusLabel('Cannot confirm')).toBe('无法确认');
    expect(findingStatusLabel('open')).toBe('未解决');
    expect(findingStatusLabel('weird-status')).toBe('weird-status');
  });
});

describe('stageTone / stageOutcomeLabel', () => {
  it('ran 不代表通过:info 而非 ok;not_run=warn;其余 muted', () => {
    expect(stageTone('ran')).toBe('info');
    expect(stageTone('not_run')).toBe('warn');
    expect(stageTone('not_part_of_work')).toBe('muted');
    expect(stageTone('landed_on_mars')).toBe('muted');
  });

  it('标签与未知回退', () => {
    expect(stageOutcomeLabel('ran')).toBe('已执行');
    expect(stageOutcomeLabel('not_part_of_work')).toBe('与本次核查无关');
    expect(stageOutcomeLabel('landed_on_mars')).toBe('landed_on_mars');
  });
});

describe('rawOutcomeLabel', () => {
  it('如实呈现 outcome 与 exit_code,不折算', () => {
    expect(rawOutcomeLabel({ outcome: 'not_green', exit_code: 1 })).toBe('not_green · exit 1');
  });
});

describe('formatElapsed', () => {
  it('m:ss 与 h:mm:ss,负数归零', () => {
    expect(formatElapsed(0)).toBe('0:00');
    expect(formatElapsed(7_400)).toBe('0:07');
    expect(formatElapsed(83_000)).toBe('1:23');
    expect(formatElapsed(3_723_000)).toBe('1:02:03');
    expect(formatElapsed(-5)).toBe('0:00');
  });
});
