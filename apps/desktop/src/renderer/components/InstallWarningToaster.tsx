import { useEffect, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { INSTALL_WARNING_EVENT, type InstallWarningKind } from '../features/chat/ChatConsole';

/**
 * #875 D1（外部评估 P0/A1）：系统包安装的「允许并记住」持久化/运行时失败
 * 必须对用户可见——工具输出里的 [提示] 可能被模型摘要掉，用户会误以为授权
 * 已永久保存。ChatConsole 扫描到失败标记后派发 window 事件，本组件以
 * #833 config_updated toast 同款样式呈现。三个事实必须同时明确：
 *   本次：执行结果（另见工具输出）
 *   永久：保存失败 / 未生效
 *   后续：仍需再次确认 / 重启后生效
 */
const WARNING_TEXT: Record<InstallWarningKind, { title: string; body: string }> = {
  persist: {
    title: '授权未能保存',
    body: '安装已完成，但「允许并记住」未保存——本次授权有效，后续安装仍需要再次确认。',
  },
  runtime: {
    title: '运行时未立即生效',
    body: '「允许并记住」已保存到配置，但当前运行时未立即生效——重启后系统包安装将自动以 root 执行。',
  },
};

export function InstallWarningToaster() {
  const [warn, setWarn] = useState<{ kind: InstallWarningKind; at: number } | null>(null);

  useEffect(() => {
    const onWarn = (e: Event) => {
      const kind = (e as CustomEvent).detail as InstallWarningKind;
      setWarn({ kind, at: Date.now() });
    };
    window.addEventListener(INSTALL_WARNING_EVENT, onWarn);
    return () => window.removeEventListener(INSTALL_WARNING_EVENT, onWarn);
  }, []);

  useEffect(() => {
    if (!warn) return;
    const t = window.setTimeout(() => setWarn(null), 8000);
    return () => window.clearTimeout(t);
  }, [warn]);

  if (!warn) return null;
  const { title, body } = WARNING_TEXT[warn.kind];

  return (
    <div
      data-testid="install-warning-toast"
      role="alert"
      className="fixed bottom-[250px] left-1/2 z-[150] flex max-w-[520px] -translate-x-1/2 items-start gap-2.5 rounded-xl px-4 py-3 text-xs font-medium shadow-[0_4px_20px_rgba(0,0,0,0.18)]"
      style={{
        background: 'var(--surface-elevated)',
        border: '1px solid var(--border-subtle)',
        color: 'var(--text)',
      }}
    >
      <AlertTriangle size={15} className="mt-0.5 shrink-0" style={{ color: 'var(--warning)' }} />
      <div className="flex flex-col gap-0.5 leading-relaxed">
        <span className="font-semibold" style={{ color: 'var(--warning)' }}>
          {title}
        </span>
        <span>{body}</span>
      </div>
    </div>
  );
}
