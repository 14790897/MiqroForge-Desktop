import { useEffect, useRef, useState } from 'react';
import { Check, X } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../components/ui/Dialog';
import {
  CONSENT_NOTICE_TEXT,
  PRIVACY_VERSION,
  getLegalDocument,
  type LegalDocumentId,
} from '../../lib/privacy';
import { LegalDocContent } from '../legal/LegalDocContent';

/** 同意按钮启用前的停留倒计时（毫秒）。 */
const COUNTDOWN_MS = 3000;
/** 倒计时刷新间隔（毫秒）。 */
const TICK_MS = 100;

/**
 * 首次启动的法律文件确认门（律师设计稿 2026-09-11，承接 #837）。
 *
 * 弹窗内容为律师定稿的《温馨提示》，《隐私政策》与《用户协议》在文内可点击
 * 查看全文；「同意」在倒计时结束后启用（沿用 #837 的计时设计），「不同意，退出」
 * 走主进程 app.quit()。同意状态写入 localStorage，协议版本更新时（PRIVACY_VERSION
 * 递增）会再次要求确认。无安装向导的分发形式（portable/zip）与升级用户在应用内
 * 看到本页；NSIS 安装流程另有协议页。
 */
export function PrivacyConsentGate({ onAgree }: { onAgree: () => void }) {
  const [remainingMs, setRemainingMs] = useState(COUNTDOWN_MS);
  const [viewing, setViewing] = useState<LegalDocumentId | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    tickRef.current = setInterval(() => {
      setRemainingMs((prev) => {
        const next = Math.max(0, prev - TICK_MS);
        if (next === 0 && tickRef.current !== null) {
          clearInterval(tickRef.current);
          tickRef.current = null;
        }
        return next;
      });
    }, TICK_MS);
    return () => {
      if (tickRef.current !== null) clearInterval(tickRef.current);
    };
  }, []);

  const canAgree = remainingMs === 0;
  const countdownSeconds = (remainingMs / 1000).toFixed(1);

  const decline = () => {
    // 走主进程 app.quit()——macOS 上 window.close() 不终止应用（#837 评审）。
    window.miqi.app.quit().catch(() => {
      // 兜底：主进程 IPC 不可用时退回关闭窗口（非 macOS 仍会退出）
      window.close();
    });
  };

  const docLink = (id: LegalDocumentId, label: string) => (
    <button
      key={id}
      type="button"
      onClick={() => setViewing(id)}
      data-testid={`privacy-consent-doc-${id}`}
      className="rounded text-[var(--accent)] underline underline-offset-2 transition-opacity hover:opacity-80"
    >
      {label}
    </button>
  );

  /** 按占位符把提示文案切成文本/链接片段。 */
  const noticeSegments = CONSENT_NOTICE_TEXT.split(/(\{\{privacy\}\}|\{\{terms\}\})/).map(
    (part, index) => {
      if (part === '{{privacy}}') return docLink('privacy', '《隐私政策》');
      if (part === '{{terms}}') return docLink('terms', '《用户协议》');
      return <span key={index}>{part}</span>;
    }
  );

  const viewingDoc = viewing === null ? null : getLegalDocument(viewing);

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
            <h1 className="text-lg font-semibold text-[var(--text)]">温馨提示</h1>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              MiQroForge DeskTop
              <span className="mx-1.5 text-[var(--text-faint)]">·</span>
              版本 {PRIVACY_VERSION}
            </p>
          </div>
        </div>

        {/* Notice text */}
        <div className="px-6 py-5" data-testid="privacy-consent-text">
          <p className="text-[13px] leading-relaxed text-[var(--text)]">{noticeSegments}</p>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)]/40 px-6 py-4">
          <p
            className="min-w-0 text-xs text-[var(--text-faint)]"
            data-testid="privacy-consent-hint"
          >
            {canAgree ? '同意状态保存在本机，协议更新时需重新确认。' : '请阅读上方提示后继续。'}
          </p>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={decline}
              data-testid="privacy-consent-decline"
            >
              <X size={14} />
              不同意，退出
            </Button>
            <Button
              size="sm"
              onClick={onAgree}
              disabled={!canAgree}
              data-testid="privacy-consent-agree"
            >
              <Check size={14} />
              同意
              {!canAgree && <span className="ml-1 tabular-nums">({countdownSeconds}s)</span>}
            </Button>
          </div>
        </div>
      </div>

      <Dialog open={viewingDoc !== null} onOpenChange={(open) => !open && setViewing(null)}>
        <DialogContent className="flex h-[72vh] w-full max-w-2xl flex-col">
          <DialogTitle>{viewingDoc?.title}</DialogTitle>
          <DialogDescription className="mt-1 text-xs text-[var(--text-faint)]">
            版本 {PRIVACY_VERSION}
          </DialogDescription>
          <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-lg border border-[var(--border-subtle)] px-4 py-3">
            <LegalDocContent text={viewingDoc?.text ?? ''} testId="privacy-consent-doc-content" />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
