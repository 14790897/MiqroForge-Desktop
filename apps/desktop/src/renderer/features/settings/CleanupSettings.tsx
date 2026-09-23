/**
 * 清理应用数据（issue #1177）——Settings → 系统 → 清理应用数据。
 *
 * 边界（与卸载器不同）：本页只清数据、**不卸载软件**；需要连同程序一起
 * 删除时使用卸载器并勾选「删除应用数据」。含 userData 的清理必须退出
 * 应用执行（Windows 文件锁），退出前确认、结果写入日志文件、不自动重启。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CircleAlert, CircleCheck, Database, Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '../../components/ui/Dialog';
import { cn } from '../../lib/utils';
import type { CleanupRunReport, CleanupScanItem } from '../../../shared/ipc';

type Step = 'select' | 'confirm' | 'running' | 'report';

function formatSize(bytes: number | null): string {
  if (bytes == null) return '未知';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function CleanupSettings() {
  const [items, setItems] = useState<CleanupScanItem[] | null>(null);
  const [available, setAvailable] = useState(true);
  const [reason, setReason] = useState<string | undefined>();
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [step, setStep] = useState<Step>('select');
  const [report, setReport] = useState<CleanupRunReport | null>(null);
  const [confirmDialogOpen, setConfirmDialogOpen] = useState(false);

  const rescan = useCallback(async () => {
    setStep('select');
    try {
      const r = await window.miqi.cleanup.scan();
      setAvailable(r.available);
      setReason(r.reason);
      setItems(r.items);
      setChecked(
        new Set(r.items.filter((i) => i.exists !== false && i.defaultChecked).map((i) => i.id))
      );
    } catch (err) {
      // 扫描失败不能停在「正在扫描」的假加载态：显示错误 + 重试入口。
      setItems([]);
      setAvailable(false);
      setReason(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void rescan();
  }, [rescan]);

  const selected = useMemo(() => (items ?? []).filter((i) => checked.has(i.id)), [items, checked]);
  const needsQuit = selected.some((i) => !i.deletableNow);

  const toggle = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const doRun = useCallback(async () => {
    setStep('running');
    try {
      const r = await window.miqi.cleanup.run(selected.map((i) => i.id));
      setReport(r);
      setStep('report');
    } catch (err) {
      setReport({
        cleaned: [],
        failed: [
          {
            id: 'data-root:rest',
            label: '清理',
            reason: err instanceof Error ? err.message : String(err),
          },
        ],
        logPath: '',
      });
      setStep('report');
    }
  }, [selected]);

  const doQuitAndClean = useCallback(async () => {
    setConfirmDialogOpen(false);
    // 启动失败/拒绝时应用不会退出：把原因落到报告页，避免静默无反馈。
    let reason: string | undefined;
    try {
      const r = await window.miqi.cleanup.quitAndClean(selected.map((i) => i.id));
      if (r.ok) return; // 应用即将退出，无 UI 后续。
      reason = r.reason ?? '无法启动清理进程';
    } catch (err) {
      reason = err instanceof Error ? err.message : String(err);
    }
    setReport({
      cleaned: [],
      failed: [{ id: 'user-data', label: '退出并清理', reason }],
      logPath: '',
    });
    setStep('report');
  }, [selected]);

  if (items === null) {
    return (
      <div className="p-6 text-sm text-[var(--text-muted)]">
        <Loader2 size={16} className="inline-block animate-spin mr-2" />
        正在扫描占用…
      </div>
    );
  }

  if (!available) {
    return (
      <div className="p-6 space-y-4">
        <div className="flex items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
          <CircleAlert size={18} className="mt-0.5 shrink-0" style={{ color: 'var(--warning)' }} />
          <div className="text-sm text-[var(--text-muted)]">
            {reason ?? '应用数据清理不可用'}
            <div className="mt-1 text-xs text-[var(--text-faint)]">
              打包版支持应用内清理；卸载时可在卸载器勾选「删除应用数据」清除全部残留。
            </div>
          </div>
        </div>
        <Button variant="secondary" size="sm" onClick={() => void rescan()}>
          <RefreshCw size={14} className="mr-1.5" />
          重新扫描
        </Button>
      </div>
    );
  }

  if (step === 'report' && report) {
    return (
      <div className="p-6 space-y-4">
        <div className="flex items-center gap-2 text-sm font-medium text-[var(--text)]">
          <CircleCheck
            size={16}
            style={{ color: report.failed.length === 0 ? 'var(--accent)' : 'var(--warning)' }}
          />
          清理完成：成功 {report.cleaned.length} 项
          {report.failed.length > 0 && `，失败 ${report.failed.length} 项`}
        </div>
        {report.failed.length > 0 && (
          <div className="space-y-2">
            {report.failed.map((f) => (
              <div
                key={f.id + f.label}
                className="rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 text-xs"
              >
                <div className="font-medium text-[var(--danger)]">{f.label}</div>
                <div className="mt-1 text-[var(--text-muted)]">{f.reason}</div>
              </div>
            ))}
          </div>
        )}
        <div className="text-xs text-[var(--text-faint)]">
          清理日志：
          <span className="font-mono text-[var(--text-muted)]">{report.logPath}</span>
        </div>
        <Button variant="secondary" size="sm" onClick={() => void rescan()}>
          <RefreshCw size={14} className="mr-1.5" />
          重新扫描
        </Button>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5">
      <div className="text-xs text-[var(--text-faint)]">
        此处仅清除应用数据，<span className="text-[var(--text-muted)]">不会卸载软件</span>
        。如需连同程序一起删除，请使用卸载器并勾选「删除应用数据」。默认保留工作目录（用户文档）。
      </div>

      <div className="space-y-2">
        {items.map((item) => {
          const disabled = item.exists === false;
          const isChecked = checked.has(item.id);
          return (
            <label
              key={item.id}
              className={cn(
                'flex items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-3 transition-colors',
                disabled ? 'opacity-50' : 'cursor-pointer hover:border-[var(--accent)]',
                isChecked && 'border-[var(--accent)]'
              )}
            >
              <input
                type="checkbox"
                className="mt-1 accent-[var(--accent)]"
                disabled={disabled || step !== 'select'}
                checked={isChecked}
                onChange={() => toggle(item.id)}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-[var(--text)]">
                    {item.label}
                    {item.deletableNow === false && (
                      <span className="ml-2 rounded bg-[var(--warning)]/10 px-1.5 py-0.5 text-size-2xs text-[var(--warning)]">
                        需退出应用
                      </span>
                    )}
                  </div>
                  <div className="shrink-0 text-xs text-[var(--text-muted)]">
                    {item.exists === false ? '不存在' : formatSize(item.sizeBytes)}
                  </div>
                </div>
                <div className="mt-0.5 text-xs text-[var(--text-muted)]">{item.description}</div>
                {item.path && (
                  <div className="mt-1 truncate font-mono text-size-2xs text-[var(--text-faint)]">
                    {item.path}
                  </div>
                )}
                {item.detail && (
                  <div className="mt-1 text-size-2xs text-[var(--text-faint)]">{item.detail}</div>
                )}
              </div>
            </label>
          );
        })}
      </div>

      {step === 'confirm' ? (
        <div className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--text)]">
            <CircleAlert size={16} style={{ color: 'var(--warning)' }} />
            再次确认删除
          </div>
          <div className="space-y-1 text-xs text-[var(--text-muted)]">
            {selected.map((i) => (
              <div key={i.id} className="flex justify-between gap-3">
                <span className="truncate">{i.label}</span>
                <span className="shrink-0 font-mono text-[var(--text-faint)]">
                  {formatSize(i.sizeBytes)}
                </span>
              </div>
            ))}
          </div>
          {needsQuit && (
            <div className="text-xs text-[var(--warning)]">
              所选项目包含应用用户数据：点击确认后应用将关闭执行清理，结果写入日志文件，
              下次启动为全新初始状态（不会自动重启）。
            </div>
          )}
          <div className="flex gap-2">
            <Button
              variant="danger"
              size="sm"
              disabled={selected.length === 0}
              onClick={() => (needsQuit ? setConfirmDialogOpen(true) : void doRun())}
            >
              <Trash2 size={14} className="mr-1.5" />
              {needsQuit ? '退出并清理' : '确认清理'}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setStep('select')}>
              返回选择
            </Button>
          </div>
        </div>
      ) : step === 'running' ? (
        <div className="flex items-center gap-2 text-sm text-[var(--text-muted)]">
          <Loader2 size={16} className="animate-spin" />
          正在清理…
        </div>
      ) : (
        <div className="flex gap-2">
          <Button size="sm" disabled={selected.length === 0} onClick={() => setStep('confirm')}>
            <Database size={14} className="mr-1.5" />
            清理所选项目（{selected.length}）
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void rescan()}>
            <RefreshCw size={14} className="mr-1.5" />
            重新扫描
          </Button>
        </div>
      )}

      <Dialog open={confirmDialogOpen} onOpenChange={setConfirmDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>退出并清理应用数据</DialogTitle>
            <DialogDescription>
              应用将立即关闭，并删除所选数据（含应用用户数据）。清理结果写入日志文件，
              不会自动重启应用；下次启动为全新初始状态。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setConfirmDialogOpen(false)}>
              取消
            </Button>
            <Button variant="danger" size="sm" onClick={() => void doQuitAndClean()}>
              <Trash2 size={14} className="mr-1.5" />
              关闭并清理
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
