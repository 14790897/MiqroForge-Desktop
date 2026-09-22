import { describe, expect, it } from 'vitest';
import { decidePointsRetry, isPermanentPointsError, nextPointsRetryDelayMs } from './StatusBar';

describe('isPermanentPointsError（issue #1160）', () => {
  it('会话级永久失败不再重试', () => {
    expect(isPermanentPointsError('SESSION_EXPIRED')).toBe(true);
    expect(isPermanentPointsError('REFRESH_TOKEN_INVALID')).toBe(true);
    expect(isPermanentPointsError('INVALID_CONFIG')).toBe(true);
  });

  it('瞬时失败（网络/平台暂不可达）可重试', () => {
    expect(isPermanentPointsError('POINTS_FAILED')).toBe(false);
    expect(isPermanentPointsError('REFRESH_FAILED')).toBe(false);
    expect(isPermanentPointsError('INTERNAL')).toBe(false);
    expect(isPermanentPointsError(undefined)).toBe(false);
  });
});

describe('nextPointsRetryDelayMs（issue #1160）', () => {
  it('30 秒起步翻倍，封顶 5 分钟', () => {
    expect(nextPointsRetryDelayMs(1)).toBe(30_000);
    expect(nextPointsRetryDelayMs(2)).toBe(60_000);
    expect(nextPointsRetryDelayMs(3)).toBe(120_000);
    expect(nextPointsRetryDelayMs(4)).toBe(240_000);
    expect(nextPointsRetryDelayMs(5)).toBe(5 * 60_000);
    expect(nextPointsRetryDelayMs(9)).toBe(5 * 60_000);
  });
});

describe('decidePointsRetry（issue #1160）', () => {
  it('成功即停（主进程已缓存并推送 status.points）', () => {
    expect(decidePointsRetry({ ok: true }, 0).retry).toBe(false);
  });

  it('SESSION_EXPIRED / REFRESH_TOKEN_INVALID：不重试，等重新登录后自然重拉', () => {
    expect(decidePointsRetry({ ok: false, code: 'SESSION_EXPIRED' }, 1).retry).toBe(false);
    expect(decidePointsRetry({ ok: false, code: 'REFRESH_TOKEN_INVALID' }, 1).retry).toBe(false);
    expect(decidePointsRetry({ ok: false, code: 'INVALID_CONFIG' }, 1).retry).toBe(false);
  });

  it('瞬时失败：退避重试，最多 5 次尝试', () => {
    expect(decidePointsRetry({ ok: false, code: 'POINTS_FAILED' }, 1)).toEqual({
      retry: true,
      delayMs: 30_000,
    });
    expect(decidePointsRetry({ ok: false, code: 'POINTS_FAILED' }, 2)).toEqual({
      retry: true,
      delayMs: 60_000,
    });
    expect(decidePointsRetry({ ok: false, code: 'POINTS_FAILED' }, 4)).toEqual({
      retry: true,
      delayMs: 240_000,
    });
    // 第 5 次尝试仍失败：次数用尽，不再重试（避免长期刷屏）
    expect(decidePointsRetry({ ok: false, code: 'POINTS_FAILED' }, 5).retry).toBe(false);
  });

  it('IPC 调用异常（result 为 undefined）：按瞬时失败有限重试', () => {
    expect(decidePointsRetry(undefined, 1)).toEqual({ retry: true, delayMs: 30_000 });
    expect(decidePointsRetry(undefined, 5).retry).toBe(false);
  });
});
