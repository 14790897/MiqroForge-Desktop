import { useEffect, useState } from 'react';
import { Settings, X } from 'lucide-react';
import { cn } from '../lib/utils';
import { INSTALL_WARNING_EVENT, type InstallWarningKind } from '../features/chat/ChatConsole';

/**
 * #875 D1：系统包安装「允许并记住」的保存/生效失败提示——交互式模态。
 *
 * 产品裁定（2026-09-04）：这类状态比普通 toast 重要——用户必须确认看到，
 * 且要知道去哪解决。模态不自动消失，需点按钮关闭。
 *
 * 视觉 v5（2026-09-04 验收定稿）：方案 B · Vercel 对话框 1:1——
 *   毛玻璃遮罩（black/30 + blur 3px）；无边框分隔的单卡（340px / 圆角 12 / 22px padding）；
 *   标题纯文字（无图标块），右上角圆形 X（30px 圆钮、常显灰底）；
 *   正文长文案自然叙述；路径用浅灰信息条（灰底 + 细边 + 圆角，⚙ 开头）；
 *   主按钮近黑 #17171a（浅色）/ 自动反转（暗色），次按钮白底细边。
 *   所有颜色经应用令牌实现，暗色主题自动换肤；浅色观感即 mock 观感。
 */
const DIALOG_COPY: Record<InstallWarningKind, { title: string; body: string; action: string }> = {
  persist: {
    title: '授权未能保存',
    body: '安装已完成，但「允许并记住」未保存——本次安装已放行，下次安装仍会询问你。如需长期免确认，请在设置中重新开启「允许系统包安装」。',
    action: '去设置开启',
  },
  runtime: {
    title: '设置已保存，重启后生效',
    body: '「允许并记住」已保存到配置，但需重启应用后才生效——重启后系统包安装不再询问。若重启后仍弹卡，请到设置中检查「允许系统包安装」开关。',
    action: '去设置检查',
  },
};

interface Props {
  /** 跳到 设置 > 沙箱隔离（常规页） */
  onOpenSandboxSettings: () => void;
}

export function InstallWarningToaster({ onOpenSandboxSettings }: Props) {
  const [warn, setWarn] = useState<InstallWarningKind | null>(null);

  useEffect(() => {
    const onWarn = (e: Event) => {
      const kind = (e as CustomEvent).detail as InstallWarningKind;
      setWarn(kind);
    };
    window.addEventListener(INSTALL_WARNING_EVENT, onWarn);
    return () => window.removeEventListener(INSTALL_WARNING_EVENT, onWarn);
  }, []);

  if (!warn) return null;
  const { title, body, action } = DIALOG_COPY[warn];
  const close = () => setWarn(null);
  const goSettings = () => {
    setWarn(null);
    onOpenSandboxSettings();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-[3px]"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      data-testid="install-warning-dialog"
    >
      {/* 方案 B 卡体：无边框（边缘靠遮罩对比），无头带/底带 */}
      <div className="relative w-[340px] rounded-xl bg-[var(--surface-elevated)] p-[22px] shadow-[0_8px_40px_rgba(0,0,0,0.16)]">
        {/* 右上圆形 X：常显灰底，hover 加深 */}
        <button
          type="button"
          onClick={close}
          aria-label="关闭"
          className="absolute right-[14px] top-[14px] grid h-[30px] w-[30px] place-items-center rounded-full bg-[var(--surface-hover)] text-[var(--text-faint)] transition-colors hover:bg-[var(--border)] hover:text-[var(--text)]"
        >
          <X size={15} strokeWidth={2} />
        </button>

        {/* 标题：纯文字，无图标块 */}
        <h2 className="pr-8 text-sm font-semibold text-[var(--text)]">{title}</h2>

        {/* 正文：长文案自然叙述，不加粗锚点 */}
        <p className="mt-[10px] text-[12.5px] leading-[1.6] text-[var(--text-muted)]">{body}</p>

        {/* 路径：浅灰信息条（灰底 + 细边 + 圆角） */}
        <div className="mt-2 flex items-center gap-[6px] rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-[10px] py-2 text-[11.5px] text-[var(--text-faint)]">
          <Settings size={12} aria-hidden />
          设置 → 沙箱隔离 → 允许系统包安装
        </div>

        {/* 操作区：白底细边次按钮 + 近黑主按钮 */}
        <div className="mt-4 flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={close}
            className="rounded-md border border-[var(--border-subtle)] bg-transparent px-[14px] py-[6px] text-[12.5px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text)]"
          >
            知道了
          </button>
          <button
            type="button"
            data-testid="install-warning-action"
            onClick={goSettings}
            className={cn(
              'rounded-md border border-[var(--text)] bg-[var(--text)] px-[14px] py-[6px] text-[12.5px] font-semibold text-[var(--background)] transition-opacity hover:opacity-85'
            )}
          >
            {action}
          </button>
        </div>
      </div>
    </div>
  );
}
