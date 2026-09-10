/**
 * 隐私协议 → 登录衔接页（issue #1000）。
 *
 * 首次启动（或协议版本更新）用户在隐私协议确认门点击「同意并继续」后
 * 直接进入本页：协议 → 登录一气呵成，登录入口不再藏在设置页深处。
 * 可「暂不登录」跳过，进入应用后首屏仍有登录卡片入口。
 *
 * 已登录（或登录完成后状态事件到达）时展示成功态与「进入应用」按钮；
 * 跳过/进入都经 onDone 返回 AppShell 继续 setup 探测与主界面加载。
 */

import { CheckCircle2, Globe, ShieldCheck, Sparkles } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { useQraftStatus } from '../../hooks/useQraftStatus';
import { QraftLoginButton } from '../settings/components/QraftLoginCard';
import { MiQroForgeLogo } from '../../components/MiQroForgeLogo';

const BENEFITS: Array<{ icon: typeof Globe; text: string }> = [
  { icon: Sparkles, text: '平台内置模型：登录后模型调用经平台 AI 网关转发，免配置 API Key。' },
  { icon: ShieldCheck, text: '凭据安全：OAuth 授权码流程，token 加密存储、到期自动刷新。' },
  { icon: Globe, text: '平台计费作业：Slurm 计算任务按积分计费，登录后即可发起。' },
];

export function QraftLoginStep({ onDone }: { onDone: () => void }) {
  const { status, loggedIn } = useQraftStatus();
  const account = status?.account;

  return (
    <div
      className="flex h-screen flex-col items-center justify-center px-6 py-8"
      style={{ background: 'var(--background)' }}
      data-testid="login-step"
    >
      <div className="flex w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface)] shadow-xl">
        {/* Header */}
        <div className="flex items-center gap-3 border-b border-[var(--border-subtle)] px-6 py-4">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent)]">
            <MiQroForgeLogo size={22} />
          </div>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-[var(--text)]">登录 MiQroForge 账号</h1>
            <p className="mt-0.5 text-xs text-[var(--text-muted)]">
              登录平台账号以使用平台内置模型与计费作业
            </p>
          </div>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-4 px-6 py-5">
          {loggedIn ? (
            <div className="flex flex-col items-center gap-3 py-2 text-center">
              <div className="flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-medium text-emerald-600">
                <CheckCircle2 size={13} />
                已登录{account?.nickname ? `：${account.nickname}` : ''}
              </div>
              <Button size="lg" onClick={onDone} data-testid="login-step-enter" className="w-full">
                进入应用
              </Button>
            </div>
          ) : (
            <>
              <ul className="flex flex-col gap-2.5">
                {BENEFITS.map((b) => (
                  <li key={b.text} className="flex items-start gap-2.5 text-xs leading-relaxed">
                    <b.icon
                      size={14}
                      className="mt-0.5 shrink-0 text-[var(--accent)]"
                      aria-hidden
                    />
                    <span className="text-[var(--text-muted)]">{b.text}</span>
                  </li>
                ))}
              </ul>
              <QraftLoginButton testId="login-step-login-btn" size="lg" className="w-full" />
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)]/40 px-6 py-4">
          <p className="min-w-0 text-xs text-[var(--text-faint)]">
            登录后可在 设置 → MiQroForge 平台 随时退出或刷新登录态。
          </p>
          {!loggedIn && (
            <Button variant="ghost" size="sm" onClick={onDone} data-testid="login-step-skip">
              暂不登录，进入应用
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
