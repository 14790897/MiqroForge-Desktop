import { describe, expect, it } from 'vitest';
import { nextReloginNotifyState, reloginNotifyCopy } from './QraftReloginNotifier';

describe('reloginNotifyCopy', () => {
  it('REFRESH_TOKEN_INVALID → 永久失效文案，引导重新登录', () => {
    expect(reloginNotifyCopy('REFRESH_TOKEN_INVALID')).toEqual({
      text: 'MiQroForge 平台登录已失效，请重新登录恢复平台功能。',
      action: '去重新登录',
    });
  });

  it('瞬时失败（REFRESH_FAILED / 未分类）→ 自动重试文案', () => {
    expect(reloginNotifyCopy('REFRESH_FAILED')).toEqual({
      text: 'MiQroForge 平台登录刷新失败，部分平台功能暂不可用（将自动重试）。',
      action: '去查看',
    });
    expect(reloginNotifyCopy(undefined)).toEqual({
      text: 'MiQroForge 平台登录刷新失败，部分平台功能暂不可用（将自动重试）。',
      action: '去查看',
    });
  });
});

describe('nextReloginNotifyState', () => {
  const idle = { notified: false, visible: false };

  it('false → true 转变：弹横幅并标记已告知', () => {
    expect(nextReloginNotifyState(idle, true)).toEqual({ notified: true, visible: true });
  });

  it('初始快照即失效（notified=false, requiresRelogin=true）：同样弹横幅', () => {
    expect(nextReloginNotifyState(idle, true)).toEqual({ notified: true, visible: true });
  });

  it('已告知期间（含用户关闭横幅）不重复弹', () => {
    const dismissed = { notified: true, visible: false };
    expect(nextReloginNotifyState(dismissed, true)).toEqual(dismissed);
    expect(nextReloginNotifyState({ notified: true, visible: true }, true)).toEqual({
      notified: true,
      visible: true,
    });
  });

  it('requiresRelogin 回 false（重新登录/登出）→ 复位，下次失效再告知', () => {
    expect(nextReloginNotifyState({ notified: true, visible: true }, false)).toEqual(idle);
    expect(nextReloginNotifyState({ notified: true, visible: false }, false)).toEqual(idle);
    // 复位后再次失效 → 重新弹横幅
    expect(nextReloginNotifyState(idle, true).visible).toBe(true);
  });
});
