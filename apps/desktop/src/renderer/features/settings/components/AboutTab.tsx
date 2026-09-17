import { useState } from 'react';
import { Download, RefreshCw } from 'lucide-react';
import { Button } from '../../../components/ui/Button';
import { useUpdateStatus } from '../../../hooks/useUpdateStatus';
import { sanitizeUiMessage } from '../../../lib/sanitizeUiMessage';
import type { UpdateSnapshot } from '../../../../shared/ipc';

/** 关于页的状态文案（纯函数，便于单测）。 */
export function describeUpdateState(snapshot: UpdateSnapshot | null): string {
  if (!snapshot) return '正在读取更新状态…';
  switch (snapshot.state) {
    case 'unsupported':
      return '当前环境不支持自动更新（仅 Windows 安装版可用，开发环境不可用）';
    case 'idle':
      return '尚未检查更新';
    case 'checking':
      return '正在检查更新…';
    case 'available':
      return `发现新版本 v${snapshot.version}，正在下载…`;
    case 'downloading':
      return `正在下载 v${snapshot.version}… ${snapshot.percent ?? 0}%`;
    case 'downloaded':
      return `新版本 v${snapshot.version} 已下载，重启应用后完成更新`;
    case 'up-to-date':
      return '已是最新版本';
    case 'error':
      return `检查更新失败：${sanitizeUiMessage(snapshot.error ?? '未知错误')}`;
  }
}

/**
 * 设置 → 关于（#1124）：版本号、更新状态与手动检查入口。
 * 更新包来自 GitHub Releases（latest.yml），仅 NSIS 安装版可用。
 */
export function AboutTab() {
  const snapshot = useUpdateStatus();
  const [checking, setChecking] = useState(false);

  const canCheck = !checking && snapshot?.state !== 'checking' && snapshot?.state !== 'unsupported';

  const handleCheck = async () => {
    setChecking(true);
    try {
      await window.miqi.update.check();
    } catch {
      /* 失败状态由事件快照呈现 */
    }
    setChecking(false);
  };

  return (
    <div className="p-6 max-w-lg flex flex-col gap-4">
      <h3 className="text-subheading text-[var(--text)]">关于 MiQroForge Desktop</h3>

      <div className="flex flex-col gap-1.5">
        <span className="text-size-sm font-medium text-[var(--text-muted)]">当前版本</span>
        <span className="font-mono text-size-sm text-[var(--text)]" data-testid="about-version">
          v{snapshot?.currentVersion ?? '—'}
        </span>
      </div>

      <div className="pt-4 border-t border-[var(--border-subtle)] flex flex-col gap-3">
        <div>
          <h4 className="text-size-sm font-medium text-[var(--text)]">软件更新</h4>
          <p
            className="text-size-xs text-[var(--text-muted)] mt-1"
            data-testid="about-update-state"
          >
            {describeUpdateState(snapshot)}
          </p>
          <p className="text-size-2xs text-[var(--text-faint)] mt-1">
            新版本会自动下载，完成后提示重启安装；也可手动检查。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleCheck}
            disabled={!canCheck}
            data-testid="about-check-update"
          >
            <RefreshCw size={14} className={snapshot?.state === 'checking' ? 'animate-spin' : ''} />
            检查更新
          </Button>
          {snapshot?.state === 'downloaded' && (
            <Button
              size="sm"
              onClick={() => {
                void window.miqi.update.install();
              }}
              data-testid="about-restart-install"
            >
              <Download size={14} />
              重启安装 v{snapshot.version}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
