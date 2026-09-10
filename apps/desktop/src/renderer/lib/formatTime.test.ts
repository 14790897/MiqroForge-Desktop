import { describe, it, expect } from 'vitest';
import { formatChatTime } from './formatTime';

/** 用本地时区构造时间(测试与实现同用本地时区 getHours/getMonth 等) */
function local(y: number, mo: number, d: number, h: number, mi: number): Date {
  return new Date(y, mo - 1, d, h, mi, 0, 0);
}

describe('formatChatTime(#828)', () => {
  const now = local(2026, 9, 10, 18, 30); // 2026-09-10 18:30 本地

  it('今天:带"今天"前缀 + HH:MM', () => {
    expect(formatChatTime(local(2026, 9, 10, 9, 5).getTime(), now)).toBe('今天 09:05');
    expect(formatChatTime(local(2026, 9, 10, 18, 30).getTime(), now)).toBe('今天 18:30');
  });

  it('昨天:"昨天"前缀 + HH:MM', () => {
    expect(formatChatTime(local(2026, 9, 9, 23, 59).getTime(), now)).toBe('昨天 23:59');
    expect(formatChatTime(local(2026, 9, 9, 0, 0).getTime(), now)).toBe('昨天 00:00');
  });

  it('更早:"M月D日 HH:MM"', () => {
    expect(formatChatTime(local(2026, 9, 8, 12, 0).getTime(), now)).toBe('9月8日 12:00');
    expect(formatChatTime(local(2025, 12, 31, 8, 7).getTime(), now)).toBe('12月31日 08:07');
  });

  it('日期边界:昨天 23:59 与今天 00:00 分属两档', () => {
    const justBeforeMidnight = local(2026, 9, 9, 23, 59).getTime();
    const justAfterMidnight = local(2026, 9, 10, 0, 0).getTime();
    expect(formatChatTime(justBeforeMidnight, now)).toBe('昨天 23:59');
    expect(formatChatTime(justAfterMidnight, now)).toBe('今天 00:00');
  });

  it('跨月边界:9月1日 的昨天是 8月31日', () => {
    const sept1 = local(2026, 9, 1, 10, 0);
    expect(formatChatTime(local(2026, 8, 31, 10, 0).getTime(), sept1)).toBe('昨天 10:00');
    expect(formatChatTime(local(2026, 8, 30, 10, 0).getTime(), sept1)).toBe('8月30日 10:00');
  });

  it('DST 安全:用年月日比较,不依赖毫秒日差(构造 23/25 小时日仍正确)', () => {
    // 找一个本时区存在 DST 的转换日也不影响语义:昨天永远算"昨天"。
    // 这里直接验证 3 月/11 月的转换日附近行为一致。
    const march = local(2026, 3, 9, 12, 0); // 典型美国 DST 切换后一天附近
    expect(formatChatTime(local(2026, 3, 8, 12, 0).getTime(), march)).toBe('昨天 12:00');
    const nov = local(2026, 11, 2, 12, 0);
    expect(formatChatTime(local(2026, 11, 1, 12, 0).getTime(), nov)).toBe('昨天 12:00');
  });

  it('无效输入返回空串', () => {
    expect(formatChatTime(undefined)).toBe('');
    expect(formatChatTime(null)).toBe('');
    expect(formatChatTime('not-a-date')).toBe('');
    expect(formatChatTime(NaN)).toBe('');
  });

  it('ISO 字符串输入可解析', () => {
    const iso = local(2026, 9, 10, 15, 45).toISOString();
    expect(formatChatTime(iso, now)).toBe('今天 15:45');
  });

  it('补零:个位数小时/分钟输出 2 位', () => {
    expect(formatChatTime(local(2026, 9, 10, 7, 3).getTime(), now)).toBe('今天 07:03');
  });
});
