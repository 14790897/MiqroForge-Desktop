import { describe, expect, it } from 'vitest';
import { resolveSessionsList } from './sessionsList';
import type { SessionInfo } from '../../shared/ipc';

const fake = (key: string) => ({ key }) as unknown as SessionInfo;

describe('resolveSessionsList（#1191 / #1202）', () => {
  it('拿不到（null）：保留现有列表，不用空数组覆盖', () => {
    expect(resolveSessionsList(null)).toBeNull();
  });

  it('拿不到（undefined / 响应形状不对）：同样保留', () => {
    expect(resolveSessionsList(undefined)).toBeNull();
    expect(resolveSessionsList({} as never)).toBeNull();
    expect(resolveSessionsList({ sessions: 'nope' } as never)).toBeNull();
  });

  it('真实的空列表照常覆盖——「确实没有会话」与「拿不到」必须分得开', () => {
    expect(resolveSessionsList({ sessions: [] })).toEqual([]);
  });

  it('有内容时返回该列表', () => {
    const list = [fake('a'), fake('b')];
    expect(resolveSessionsList({ sessions: list })).toEqual(list);
  });
});
