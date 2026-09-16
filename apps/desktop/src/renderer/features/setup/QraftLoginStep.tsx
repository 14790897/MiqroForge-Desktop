/**
 * 登录门（issue #1000 首次落地，issue #1095 收口为强制登录）。
 *
 * 未登录用户启动时停在本页，不再提供「暂不登录，进入应用」：平台模型调用、
 * Slurm 计费作业、反馈归属都要平台账号，跳过登录进应用后核心功能全是死路。
 * 未登录时的唯一出口是退出应用——退出前给出明确原因提示，避免用户误以为
 * 应用崩溃（issue #1095 需求 2）。
 *
 * 登录成功由主进程推送 qraft:statusChanged，AppShell 的登录门随之放行，
 * 本页自动卸载（无需「进入应用」按钮）。
 */

import { useState } from 'react';
import { Globe, LogOut, ShieldCheck, Sparkles } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../components/ui/Dialog';
import { QraftLoginButton } from '../settings/components/QraftLoginCard';
import { MiQroForgeLogo } from '../../components/MiQroForgeLogo';

const BENEFITS: Array<{ icon: typeof Globe; text: string }> = [
  { icon: Sparkles, text: '平台内置模型：模型调用经平台 AI 网关转发，免配置 API Key。' },
  { icon: ShieldCheck, text: '凭据安全：OAuth 授权码流程，token 加密存储、到期自动刷新。' },
  { icon: Globe, text: '平台计费作业：Slurm 计算任务按积分计费，登录后即可发起。' },
];

export function QraftLoginStep() {
  const [confirmingQuit, setConfirmingQuit] = useState(false);

  const quit = () => {
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
              需要登录 MiQroForge 账号后才能使用
            </p>
          </div>
        </div>

        {/* Body */}
        <div className="flex flex-col gap-4 px-6 py-5">
          <ul className="flex flex-col gap-2.5">
            {BENEFITS.map((b) => (
              <li key={b.text} className="flex items-start gap-2.5 text-xs leading-relaxed">
                <b.icon size={14} className="mt-0.5 shrink-0 text-[var(--accent)]" aria-hidden />
                <span className="text-[var(--text-muted)]">{b.text}</span>
              </li>
            ))}
          </ul>
          <QraftLoginButton testId="login-step-login-btn" size="lg" className="w-full" />
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 border-t border-[var(--border-subtle)] bg-[var(--surface-muted)]/40 px-6 py-4">
          <p className="min-w-0 text-xs text-[var(--text-faint)]">
            登录后可在 设置 → MiQroForge 平台 随时退出或刷新登录态。
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirmingQuit(true)}
            data-testid="login-step-quit"
            className="shrink-0"
          >
            <LogOut size={14} />
            退出应用
          </Button>
        </div>
      </div>

      <Dialog open={confirmingQuit} onOpenChange={setConfirmingQuit}>
        <DialogContent data-testid="login-step-quit-dialog">
          <DialogTitle>需要登录 MiQroForge 账号后才能使用</DialogTitle>
          <DialogDescription className="mt-2 text-xs leading-relaxed text-[var(--text-muted)]">
            平台内置模型、Slurm 计费作业等功能都需要账号登录。退出应用后本次运行结束，
            下次启动仍会回到本页，直到完成登录。
          </DialogDescription>
          <div className="mt-5 flex items-center justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmingQuit(false)}
              data-testid="login-step-quit-cancel"
            >
              返回登录
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={quit}
              data-testid="login-step-quit-confirm"
            >
              <LogOut size={14} />
              退出应用
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
