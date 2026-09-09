/**
 * Unified time formatting utilities for the MiQroForge renderer.
 *
 * Replaces ~9 scattered implementations of formatTime / relativeTime / relativeTimeLabel
 * across 7 files (Sidebar, FeedbackPage, ChatConsole, MemoryPage, ApprovalsPage,
 * SettingsPage, SessionExplorer).
 *
 * Labels follow the active UI language (see ../i18n). While zh-CN is active,
 * text values stay byte-identical to the pre-i18n hardcoded copy.
 */

import { i18n } from '../i18n';

/** Intl locale matching the active UI language (pilot: zh-CN / en). */
function locale(): string {
  return i18n.language === 'en' ? 'en-US' : 'zh-CN';
}

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
 * Human-readable relative time in the active UI language.
 *
 * @example
 *   formatRelativeTime(Date.now() - 30_000)         // zh: "刚刚" / en: "just now"
 *   formatRelativeTime(Date.now() - 5 * 60_000)     // zh: "5 分钟前" / en: "5 minutes ago"
 *   formatRelativeTime(Date.now() - 3 * 3600_000)   // zh: "3 小时前" / en: "3 hours ago"
 *   formatRelativeTime(Date.now() - 2 * 86400_000)  // "昨天" / "yesterday"
 *   formatRelativeTime(Date.now() - 7 * 86400_000)  // "7月19日" / "Jul 19"
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

  const t = i18n.t.bind(i18n);
  const diff = now - ts;
  if (diff < 60_000) return t('relative.justNow') + suffix;
  if (diff < 3_600_000)
    return t('relative.minutesAgo', { count: Math.floor(diff / 60_000) }) + suffix;
  if (diff < 86_400_000)
    return t('relative.hoursAgo', { count: Math.floor(diff / 3_600_000) }) + suffix;
  if (diff < 2 * 86_400_000) return t('relative.yesterday') + suffix;

  const d = new Date(ts);
  const label = d.toLocaleDateString(locale(), { month: 'short', day: 'numeric' });
  return `${label}${suffix}`;
}

/**
 * Absolute date+time in the active UI language: zh "2026/7/26 18:52:30",
 * en-US "7/26/2026, 6:52:30 PM".
 */
export function formatAbsoluteTime(date: string | number | Date | null | undefined): string {
  if (date === null || date === undefined) return '';
  const ts = toTimestamp(date);
  if (!Number.isFinite(ts)) return String(date);

  const d = new Date(ts);
  const datePart = d.toLocaleDateString(locale(), {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  });
  const timePart = d.toLocaleTimeString(locale(), { hour12: false });
  return `${datePart} ${timePart}`;
}

/**
 * Short date+time format: zh "7月26日 18:52" / en "Jul 26, 18:52".
 * Used by Sidebar session key formatting and SettingsPage ArchivedTab.
 */
export function formatShortDateTime(date: string | number | Date | null | undefined): string {
  if (date === null || date === undefined) return '';
  const ts = toTimestamp(date);
  if (!Number.isFinite(ts)) return String(date);

  return new Intl.DateTimeFormat(locale(), {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ts));
}
