import { useEffect, useRef, useState } from 'react';
import { Check, Languages, X } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { cn } from '../../lib/utils';
import {
  PRIVACY_TEXTS,
  PRIVACY_VERSION,
  detectPrivacyLanguage,
  type PrivacyLanguage,
} from '../../lib/privacy';

/** 距底部多少像素内视为「已滚动到底」。 */
const BOTTOM_THRESHOLD_PX = 8;
/** 到底后需停留的时长（毫秒），期间滚离底部则重新计时。 */
const HOLD_AT_BOTTOM_MS = 3000;
/** 停留倒计时的刷新间隔（毫秒）。 */
const COUNTDOWN_TICK_MS = 100;

/**
 * 首次启动的隐私协议确认门 (#837)。
 *
 * 无安装向导的分发形式（portable/zip）与升级用户在应用内看到本页；
 * 同意状态写入 localStorage，协议版本更新时（PRIVACY_VERSION 递增）
 * 会再次要求确认。拒绝则退出应用。
 *
 * 同意前置条件（下拉到底并停留确认）：协议文本需滚动到底部并在底部
 * 停留满 HOLD_AT_BOTTOM_MS 后「同意并继续」才启用，停留期间同意按钮
 * 上显示剩余时间倒计时；文本不足一屏（无溢出）时视为已完整展示，直接
 * 放行；切换语言后重新确认。
 */
export function PrivacyConsentGate({ onAgree }: { onAgree: () => void }) {
  const [language, setLanguage] = useState<PrivacyLanguage>(() => detectPrivacyLanguage());
  const scrollRef = useRef<HTMLDivElement>(null);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const holdTick = useRef<ReturnType<typeof setInterval> | null>(null);
  // 一旦满足条件即启用（滚离底部不重新禁用），语言切换时整体重置
  const satisfiedRef = useRef(false);
  const [reachedBottom, setReachedBottom] = useState(false);
  const [holdElapsed, setHoldElapsed] = useState(false);
  // 停留倒计时剩余毫秒数；null 表示当前无倒计时（未到底或已放行）
  const [holdRemainingMs, setHoldRemainingMs] = useState<number | null>(null);

  const canAgree = holdElapsed;
  // 停留倒计时剩余秒数（1 位小数），无倒计时时为 null
  const countdownSeconds = holdRemainingMs === null ? null : (holdRemainingMs / 1000).toFixed(1);

  const clearHold = () => {
    if (holdTimer.current !== null) {
      clearTimeout(holdTimer.current);
      holdTimer.current = null;
    }
    if (holdTick.current !== null) {
      clearInterval(holdTick.current);
      holdTick.current = null;
    }
    setHoldRemainingMs(null);
  };

  useEffect(() => clearHold, []);

  // 统一评估「是否到底」：在底部则启动停留计时（已在计时则保持），
  // 不在底部则清除计时。scroll 事件与尺寸变化共用此路径。
  const evaluateBottom = () => {
    if (satisfiedRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - BOTTOM_THRESHOLD_PX;
    setReachedBottom(atBottom);
    if (atBottom) {
      if (holdTimer.current === null) {
        // 停留期间在同意按钮上显示剩余时间倒计时
        setHoldRemainingMs(HOLD_AT_BOTTOM_MS);
        holdTick.current = setInterval(() => {
          setHoldRemainingMs((prev) =>
            prev === null ? prev : Math.max(0, prev - COUNTDOWN_TICK_MS)
          );
        }, COUNTDOWN_TICK_MS);
        holdTimer.current = setTimeout(() => {
          clearHold();
          // timer 与 scroll 回调无跨源顺序保证：到期时重新检查几何位置，
          // 用户已滚离底部则不启用（CodeRabbit 评审场景）
          const elNow = scrollRef.current;
          if (!elNow) return;
          const stillAtBottom =
            elNow.scrollTop + elNow.clientHeight >= elNow.scrollHeight - BOTTOM_THRESHOLD_PX;
          if (!stillAtBottom) {
            setReachedBottom(false);
            return;
          }
          satisfiedRef.current = true;
          setHoldElapsed(true);
        }, HOLD_AT_BOTTOM_MS);
      }
    } else {
      clearHold();
      setHoldElapsed(false);
    }
  };

  const handleScroll = () => {
    evaluateBottom();
  };

  // 内容不足一屏（无滚动空间）时视为已完整展示，直接放行；窗口/字体
  // 尺寸变化导致高度改变时重新评估。仍有溢出时重置停留状态——resize
  // 可能让此前「到底」失效（内容变多），不能靠旧计时放行同意。
  // 语言切换后重新检测。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const checkOverflow = () => {
      if (satisfiedRef.current) return;
      if (el.scrollHeight - el.clientHeight <= 1) {
        satisfiedRef.current = true;
        clearHold();
        setReachedBottom(true);
        setHoldElapsed(true);
        return;
      }
      // 仍有溢出：重置后重新评估——尺寸对称恢复时 scrollTop 会被浏览器
      // 钳制回底部，但不会产生 scroll 事件，需在此重启停留计时。
      clearHold();
      setHoldElapsed(false);
      evaluateBottom();
    };
    checkOverflow();
    const observer = new ResizeObserver(checkOverflow);
    observer.observe(el);
    // 文本自身高度变化（字体加载、内容换行）同样影响溢出量
    const textEl = el.querySelector('pre');
    if (textEl) observer.observe(textEl);
    return () => observer.disconnect();
  }, [language]);

  const switchLanguage = (lang: PrivacyLanguage) => {
    if (lang === language) return;
    setLanguage(lang);
    clearHold();
    satisfiedRef.current = false;
    setReachedBottom(false);
    setHoldElapsed(false);
  };

  const decline = () => {
    // 走主进程 app.quit()——macOS 上 window.close() 不终止应用（#837 评审）。
    window.miqi.app.quit().catch(() => {
      // 兜底：主进程 IPC 不可用时退回关闭窗口（非 macOS 仍会退出）
      window.close();
    });
  };

  return (
    <div
      className="flex h-screen flex-col items-center justify-center px-6 py-8"
      style={{ background: 'var(--background)' }}
      data-testid="privacy-consent-gate"
    >
      <div className="flex w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface)] shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-6 py-4">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-[var(--text)]">
              {language === 'zh-CN' ? '隐私协议' : 'Privacy Agreement'}
            </h1>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              {language === 'zh-CN' ? '版本 ' : 'Version '}
              {PRIVACY_VERSION}
              <span className="mx-1.5 text-[var(--text-faint)]">·</span>
              {language === 'zh-CN'
                ? '使用 MiQroForge Desktop 前，请阅读并同意本协议'
                : 'Please read and accept this agreement before using MiQroForge Desktop'}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-0.5 rounded-lg border border-[var(--border)] bg-[var(--surface-muted)]/60 p-0.5">
            {(['zh-CN', 'en-US'] as const).map((lang) => (
              <button
                key={lang}
                onClick={() => switchLanguage(lang)}
                aria-pressed={language === lang}
                className={cn(
                  'rounded-md px-2 py-1 text-xs font-medium transition-colors',
                  language === lang
                    ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                )}
              >
                {lang === 'zh-CN' ? '中文' : 'English'}
              </button>
            ))}
            <Languages size={12} className="mr-1 text-[var(--text-faint)]" />
          </div>
        </div>

        {/* Scrollable agreement text */}
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="max-h-[52vh] overflow-y-auto px-6 py-4"
          data-testid="privacy-consent-scroll"
        >
          <pre
            className="whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed text-[var(--text)]"
            data-testid="privacy-consent-text"
          >
            {PRIVACY_TEXTS[language]}
          </pre>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)]/40 px-6 py-4">
          <p
            className="min-w-0 text-xs text-[var(--text-faint)]"
            data-testid="privacy-consent-hint"
          >
            {canAgree
              ? language === 'zh-CN'
                ? '同意状态保存在本机，协议更新时需重新确认。'
                : 'Consent is stored locally; re-confirmation is required when the agreement is updated.'
              : language === 'zh-CN'
                ? reachedBottom
                  ? '请停留在协议底部片刻后继续。'
                  : '请滚动阅读完整协议后继续。'
                : reachedBottom
                  ? 'Please hold at the bottom of the agreement to continue.'
                  : 'Please scroll through the full agreement to continue.'}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={decline}
              data-testid="privacy-consent-decline"
            >
              <X size={14} />
              {language === 'zh-CN' ? '拒绝并退出' : 'Decline and Exit'}
            </Button>
            <Button
              size="sm"
              onClick={onAgree}
              disabled={!canAgree}
              data-testid="privacy-consent-agree"
            >
              <Check size={14} />
              {language === 'zh-CN' ? '同意并继续' : 'Agree and Continue'}
              {countdownSeconds !== null && (
                <span className="ml-1 tabular-nums">({countdownSeconds}s)</span>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
