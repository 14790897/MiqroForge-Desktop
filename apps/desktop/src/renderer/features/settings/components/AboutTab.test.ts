import { describe, expect, it } from 'vitest';
import { describeUpdateState } from './AboutTab';
import type { UpdateSnapshot } from '../../../../shared/ipc';

const base: UpdateSnapshot = { state: 'idle', currentVersion: '1.0.0' };

describe('describeUpdateState', () => {
  it('快照未到达时给出加载中文案', () => {
    expect(describeUpdateState(null)).toBe('正在读取更新状态…');
  });

  it('开发环境（unsupported）说明不检查更新', () => {
    expect(describeUpdateState({ ...base, state: 'unsupported' })).toContain('开发环境');
  });

  it('downloading 带版本号与百分比', () => {
    const text = describeUpdateState({
      ...base,
      state: 'downloading',
      version: '1.1.0',
      percent: 42,
    });
    expect(text).toContain('v1.1.0');
    expect(text).toContain('42%');
  });

  it('percent 缺失时按 0% 显示而不是 undefined', () => {
    const text = describeUpdateState({ ...base, state: 'downloading', version: '1.1.0' });
    expect(text).toContain('0%');
  });

  it('downloaded 引导重启完成更新', () => {
    expect(describeUpdateState({ ...base, state: 'downloaded', version: '1.1.0' })).toContain(
      '重启'
    );
  });

  it('error 展示失败原因', () => {
    expect(describeUpdateState({ ...base, state: 'error', error: '404 latest.yml' })).toContain(
      '404 latest.yml'
    );
  });

  it('error 且无信息时兜底为「未知错误」', () => {
    expect(describeUpdateState({ ...base, state: 'error' })).toContain('未知错误');
  });
});
