import { describe, expect, it } from 'vitest';

import { describeEditFailure } from './ChatConsole';

describe('describeEditFailure', () => {
  it('maps each named reason to its own user-facing line', () => {
    expect(describeEditFailure('streaming')).toBe('模型还在生成，先停止再编辑');
    expect(describeEditFailure('pending')).toBe('上一条消息还没发出去，稍后再试');
    expect(describeEditFailure('truncate-failed')).toBe('删除历史失败，改动未提交');
    expect(describeEditFailure('session-switched')).toBe('已切换会话，改动未提交');
  });

  it('falls back to a generic line for empty/stale/rejected/undefined', () => {
    for (const reason of ['empty', 'stale', 'rejected', undefined] as const) {
      expect(describeEditFailure(reason)).toBe('改动未提交，请重试');
    }
  });
});
