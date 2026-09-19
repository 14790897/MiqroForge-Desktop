/**
 * #1118（#1035 移植）：启动恢复 lastSession 的幽灵会话判定。
 *
 * 这个判定的价值在于「用户看到的是欢迎页，但当前会话 key 其实不存在」这种
 * 静默状态：bridge 的 sessions.get 对未知 key 是 get_or_create，不会报错，
 * 所以只有启动时拿 sessions.list 对照才能发现。
 */
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_SESSION_KEY,
  shouldFallbackToDefaultSession,
  shouldVerifyRestoredSession,
} from './sessionRestore';

describe('shouldFallbackToDefaultSession', () => {
  it('会话已不存在（store 里查无此 key）→ 回退', () => {
    expect(shouldFallbackToDefaultSession('desktop:1789704154596', ['desktop:123'])).toBe(true);
  });

  it('会话仍在 → 不回退（保持恢复出来的会话）', () => {
    expect(
      shouldFallbackToDefaultSession('desktop:1789704154596', [
        'desktop:123',
        'desktop:1789704154596',
      ])
    ).toBe(false);
  });

  it('store 为空（全新 profile + 幽灵 key）→ 回退', () => {
    // 这是 flake 的形状：共享 profile 残留上一轮的 lastSession，
    // 本轮 store 里根本没有那个会话。
    expect(shouldFallbackToDefaultSession('desktop:1789704154596', [])).toBe(true);
  });

  it('默认态哨兵不回退（它就是回退目标）', () => {
    expect(shouldFallbackToDefaultSession(DEFAULT_SESSION_KEY, [])).toBe(false);
    expect(shouldFallbackToDefaultSession(DEFAULT_SESSION_KEY, ['desktop:123'])).toBe(false);
  });

  it('读不到 lastSession（null / undefined / 空串）→ 不动', () => {
    expect(shouldFallbackToDefaultSession(null, ['desktop:123'])).toBe(false);
    expect(shouldFallbackToDefaultSession(undefined, [])).toBe(false);
    expect(shouldFallbackToDefaultSession('', [])).toBe(false);
  });

  it('自定义默认 key 时同样成立', () => {
    expect(shouldFallbackToDefaultSession('desktop:9', [], 'desktop:my-default')).toBe(true);
    expect(shouldFallbackToDefaultSession('desktop:my-default', [], 'desktop:my-default')).toBe(
      false
    );
  });
});

/**
 * 第八轮两阶段启动的门：非默认哨兵的恢复 key 必须先校验存在性，ChatConsole 才能
 * 挂载（否则它会用 get-or-create 的 sessions.get 先把幽灵 key 摸一遍——E2E 实测
 * 抓到 4 次 ghost get + 1 次 ghost delete）。
 */
describe('shouldVerifyRestoredSession', () => {
  it('恢复出来的是普通会话 key → 必须先校验', () => {
    expect(shouldVerifyRestoredSession('desktop:1789704154596')).toBe(true);
    expect(shouldVerifyRestoredSession('doc:月度报告')).toBe(true);
  });

  it('默认哨兵不用校验（它就是要回退到的目标）', () => {
    expect(shouldVerifyRestoredSession(DEFAULT_SESSION_KEY)).toBe(false);
  });

  it('读不到 lastSession（null / undefined / 空串）→ 不用校验', () => {
    expect(shouldVerifyRestoredSession(null)).toBe(false);
    expect(shouldVerifyRestoredSession(undefined)).toBe(false);
    expect(shouldVerifyRestoredSession('')).toBe(false);
  });

  it('自定义默认 key 时同样成立', () => {
    expect(shouldVerifyRestoredSession('desktop:9', 'desktop:my-default')).toBe(true);
    expect(shouldVerifyRestoredSession('desktop:my-default', 'desktop:my-default')).toBe(false);
  });
});
