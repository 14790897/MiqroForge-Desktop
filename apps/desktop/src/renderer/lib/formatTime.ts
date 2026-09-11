/**
 * Unified time formatting utilities for the MiQroForge renderer.
 *
 * Replaces ~9 scattered implementations of formatTime / relativeTime / relativeTimeLabel
 * across 7 files (Sidebar, FeedbackPage, ChatConsole, MemoryPage, ApprovalsPage,
 * SettingsPage, SessionExplorer).
 */

/**
 * Parse a date input (string ISO, epoch ms number, or Date) into a timestamp.
 * Returns NaN for invalid inputs.
 */
function toTimestamp(date: string | number | Date): number {
  if (date instanceof Date) return date.getTime();
  if (typeof date === 'number') return date;
  return Date.parse(date);
}

/**
 * Human-readable relative time in Chinese.
 *
 * @example
 *   formatRelativeTime(Date.now() - 30_000)         // "刚刚"
 *   formatRelativeTime(Date.now() - 5 * 60_000)     // "5 分钟前"
 *   formatRelativeTime(Date.now() - 3 * 3600_000)   // "3 小时前"
 *   formatRelativeTime(Date.now() - 2 * 86400_000)  // "昨天"
 *   formatRelativeTime(Date.now() - 7 * 86400_000)  // "7月19日"
 *
 * @param date    ISO string, epoch ms, Date, null, or undefined
 * @param options.suffix    Appended to the label (e.g. "更新" → "刚刚更新")
 * @param options.nullLabel Returned when date is null/undefined (default: "")
 * @param options.now       Reference timestamp (default: Date.now())
 */
export function formatRelativeTime(
  date: string | number | Date | null | undefined,
  options?: { suffix?: string; nullLabel?: string; now?: number }
): string {
  const { suffix = '', nullLabel = '', now = Date.now() } = options ?? {};
  if (date === null || date === undefined) return nullLabel;

  const ts = toTimestamp(date);
  if (!Number.isFinite(ts)) return nullLabel;

  const diff = now - ts;
  if (diff < 60_000) return `刚刚${suffix}`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前${suffix}`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前${suffix}`;
  if (diff < 2 * 86_400_000) return `昨天${suffix}`;

  const d = new Date(ts);
  const label = d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  return `${label}${suffix}`;
}

/**
 * Absolute date+time in zh-CN locale: "2026/7/26 18:52:30"
 */
export function formatAbsoluteTime(date: string | number | Date | null | undefined): string {
  if (date === null || date === undefined) return '';
  const ts = toTimestamp(date);
  if (!Number.isFinite(ts)) return String(date);

  const d = new Date(ts);
  const datePart = d.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const timePart = d.toLocaleTimeString('zh-CN', { hour12: false });
  return `${datePart} ${timePart}`;
}

/**
 * Short date+time format: "7月26日 18:52"
 * Used by Sidebar session key formatting and SettingsPage ArchivedTab.
 */
export function formatShortDateTime(date: string | number | Date | null | undefined): string {
  if (date === null || date === undefined) return '';
  const ts = toTimestamp(date);
  if (!Number.isFinite(ts)) return String(date);

  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ts));
}
