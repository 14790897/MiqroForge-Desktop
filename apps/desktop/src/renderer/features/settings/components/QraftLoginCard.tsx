/**
 * 登录入口共享组件（issue #1000）。
 *
 * QraftLoginButton：一键浏览器 OAuth 登录 —— 点击直接打开 MiQroForge
 * 授权页（不再要求先跳设置页），自管理忙碌/取消/错误反馈。各入口
 * （首屏卡片、未登录拦截、顶栏、设置页）复用同一实现，避免文案漂移。
 *
 * QraftLoginCard：首屏空会话欢迎区的显著登录卡片（居中、大按钮）。
 * 已登录时整体隐藏（首屏不打扰已登录用户）。
 */

import { useState } from 'react';
import { Globe, RefreshCw, TriangleAlert } from 'lucide-react';
import { Button } from '../../../components/ui/Button';
import { cn } from '../../../lib/utils';
import { useQraftStatus } from '../../../hooks/useQraftStatus';
import { qraftErrorText } from '../../../lib/qraftErrors';

export interface QraftLoginButtonProps {
  /** 数据测试 ID（各入口区分，如 chat-hero-login-btn / topbar-login-btn）。 */
  testId: string;
  /** 登录成功后回调（状态事件会自动更新各 useQraftStatus 订阅方）。 */
  onLoggedIn?: () => void;
  /** 等待授权中的按钮文案；默认完整提示，紧凑场景传短文案。 */
  busyLabel?: string;
  /** 登录按钮尺寸。 */
  size?: 'sm' | 'md' | 'lg';
  /** 取消/错误反馈是否内联渲染在按钮下方；关闭时只写进 title（紧凑场景）。 */
  inlineFeedback?: boolean;
  className?: string;
}

/** 一键浏览器登录按钮：点击直接走 OAuth 浏览器登录，无需先跳设置页。 */
export function QraftLoginButton({
  testId,
  onLoggedIn,
  busyLabel = '等待授权中…（请在 MiQroForge 页面完成登录）',
  size = 'md',
  inlineFeedback = true,
  className,
}: QraftLoginButtonProps) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: 'error' | 'notice'; text: string } | null>(null);

  const handleLogin = async () => {
    setBusy(true);
    setFeedback(null);
    try {
      if (typeof window.miqi?.qraft?.browserLogin !== 'function') {
        setFeedback({ kind: 'error', text: '登录功能不可用：预加载桥接缺失，请重启应用后重试。' });
        return;
      }
      // 不传 env：主进程 resolveConfig 回退到上次登录存储的环境
      //（stored.env ?? 'test'），硬编码 'test' 会覆盖存量生产环境登录
      //（CodeRabbit #1010）。
      const result = await window.miqi.qraft.browserLogin({});
      if (result.ok) {
        // 登录成功即由主进程 persistLogin 推送 qraft:statusChanged 事件，
        // 各 useQraftStatus 订阅方随之更新，无需再主动拉一次快照。
        onLoggedIn?.();
      } else if (result.code === 'LOGIN_CANCELLED') {
        setFeedback({ kind: 'notice', text: qraftErrorText(result, '已取消浏览器登录') });
      } else {
        setFeedback({ kind: 'error', text: qraftErrorText(result, '浏览器登录失败') });
      }
    } catch (e) {
      setFeedback({ kind: 'error', text: e instanceof Error ? e.message : 'IPC 调用失败' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <Button
        type="button"
        variant="default"
        size={size}
        onClick={handleLogin}
        disabled={busy}
        className="justify-center"
        data-testid={testId}
        title={inlineFeedback ? undefined : (feedback?.text ?? '登录 MiQroForge 账号')}
      >
        {busy ? <RefreshCw size={14} className="animate-spin" /> : <Globe size={14} />}
        {busy ? busyLabel : '登录 MiQroForge 账号'}
      </Button>
      {inlineFeedback && feedback && (
        <p
          className={cn(
            'text-size-2xs leading-relaxed',
            feedback.kind === 'error' ? 'text-[var(--danger)]' : 'text-[var(--text-faint)]'
          )}
          data-testid={`${testId}-feedback`}
        >
          {feedback.kind === 'error' && <TriangleAlert size={11} className="mr-1 inline-block" />}
          {feedback.text}
        </p>
      )}
    </div>
  );
}

export interface QraftLoginCardProps {
  /** 卡片标题；默认「登录 MiQroForge 账号」。 */
  title?: string;
  /** 卡片描述。 */
  description?: string;
  /** 次级入口「查看平台账号」（跳设置页 MiQroForge 平台 tab）。 */
  onGoToQraft?: () => void;
}

const DEFAULT_DESCRIPTION =
  '登录后可使用平台内置模型，模型调用经平台 AI 网关转发，Slurm 计算作业按积分计费。';

/** 首屏登录引导卡片（issue #1000）：未登录时展示入口，已登录时整体隐藏。 */
export function QraftLoginCard({
  title = '登录 MiQroForge 账号',
  description = DEFAULT_DESCRIPTION,
  onGoToQraft,
}: QraftLoginCardProps) {
  const { loggedIn } = useQraftStatus();

  if (loggedIn) return null;

  return (
    <div
      className="relative w-full max-w-[560px] rounded-2xl border border-[var(--accent)]/40 bg-[var(--surface)] px-6 py-5 shadow-sm"
      data-testid="chat-hero-login-card"
    >
      <div className="flex flex-col items-center gap-2 text-center">
        <h2 className="text-sm font-semibold text-[var(--text)]">{title}</h2>
        <p className="text-xs leading-relaxed text-[var(--text-muted)]">{description}</p>
        <QraftLoginButton
          testId="chat-hero-login-btn"
          size="lg"
          className="mt-1 w-full max-w-[320px]"
        />
        {onGoToQraft && (
          <button
            type="button"
            onClick={onGoToQraft}
            className="text-size-2xs text-[var(--text-faint)] hover:text-[var(--text-muted)]"
            data-testid="chat-hero-open-qraft"
          >
            查看平台账号（设置 → MiQroForge 平台）
          </button>
        )}
      </div>
    </div>
  );
}
