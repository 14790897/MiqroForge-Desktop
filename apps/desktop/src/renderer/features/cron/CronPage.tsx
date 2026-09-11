import { useState, useEffect, useCallback } from 'react';
import {
  Clock,
  Plus,
  RefreshCw,
  Loader2,
  Trash2,
  Play,
  Pause,
  Power,
  PowerOff,
  ChevronDown,
  ChevronRight,
  X,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import type { CronJob, CronRunEntry } from '../../../shared/ipc';

type ScheduleKind = 'at' | 'every' | 'cron';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatMs(ms: number | null): string {
  if (!ms) return '—';
  const d = new Date(ms);
  return d.toLocaleString();
}

function durationMs(startMs: number): string {
  const elapsed = Date.now() - startMs;
  if (elapsed < 1000) return `${elapsed}ms`;
  if (elapsed < 60000) return `${(elapsed / 1000).toFixed(1)}s`;
  return `${(elapsed / 60000).toFixed(1)}m`;
}

function scheduleLabel(job: CronJob): string {
  const s = job.schedule;
  if (s.kind === 'at') return `At ${formatMs(s.atMs)}`;
  if (s.kind === 'every') return s.everyMs ? `Every ${(s.everyMs / 1000).toFixed(0)}s` : 'Every —';
  if (s.kind === 'cron') return s.expr ?? 'cron —';
  return s.kind;
}

// ---------------------------------------------------------------------------
// Create / Edit modal
// ---------------------------------------------------------------------------

interface JobFormData {
  name: string;
  scheduleKind: ScheduleKind;
  atMs: string;
  everyMs: string;
  expr: string;
  tz: string;
  message: string;
}

function emptyForm(): JobFormData {
  return {
    name: '',
    scheduleKind: 'every',
    atMs: '',
    everyMs: '60000',
    expr: '',
    tz: '',
    message: '',
  };
}

import { Modal } from '../../components/shared';

function CreateEditModal({
  job,
  onClose,
  onSaved,
}: {
  job?: CronJob;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!job;
  const [form, setForm] = useState<JobFormData>(() => {
    if (job) {
      return {
        name: job.name,
        scheduleKind: job.schedule.kind,
        atMs: job.schedule.atMs ? String(job.schedule.atMs) : '',
        everyMs: job.schedule.everyMs ? String(job.schedule.everyMs) : '',
        expr: job.schedule.expr ?? '',
        tz: job.schedule.tz ?? '',
        message: job.payload.message ?? '',
      };
    }
    return emptyForm();
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: keyof JobFormData, v: string) => setForm((prev) => ({ ...prev, [k]: v }));

  const handleSubmit = async () => {
    if (!form.name.trim()) {
      setError('请填写任务名称');
      return;
    }
    setSaving(true);
    setError(null);

    const payload: Record<string, unknown> = {
      name: form.name.trim(),
      scheduleKind: form.scheduleKind,
      message: form.message,
    };
    if (form.scheduleKind === 'at') {
      const ms = parseInt(form.atMs, 10);
      if (!ms || ms <= Date.now()) {
        setError('请填写一个未来的毫秒时间戳');
        setSaving(false);
        return;
      }
      payload.atMs = ms;
    } else if (form.scheduleKind === 'every') {
      const ms = parseInt(form.everyMs, 10);
      if (!ms || ms < 1000) {
        setError('间隔至少 1000 毫秒（1秒）');
        setSaving(false);
        return;
      }
      payload.everyMs = ms;
    } else if (form.scheduleKind === 'cron') {
      if (!form.expr.trim()) {
        setError('Cron 调度必须填写表达式');
        setSaving(false);
        return;
      }
      payload.expr = form.expr.trim();
      if (form.tz.trim()) payload.tz = form.tz.trim();
    }

    try {
      if (isEdit) {
        await window.miqi.cron.update({ jobId: job!.id, ...payload });
      } else {
        await window.miqi.cron.create(payload);
      }
      onSaved();
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open onOpenChange={onClose} hideClose>
      <div className="bg-[var(--surface-elevated)] border border-[var(--border)] rounded-xl shadow-xl w-[520px] max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-subtle)]">
          <h2 className="text-sm font-semibold text-[var(--text)]">
            {isEdit ? '编辑任务' : '创建任务'}
          </h2>
          <button
            onClick={onClose}
            className="text-[var(--text-faint)] hover:text-[var(--text)] transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 flex flex-col gap-4">
          {/* Name */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
              名称
            </label>
            <input
              type="text"
              value={form.name}
              onChange={(e) => set('name', e.target.value)}
              placeholder="例如：每日报告"
              className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)]"
            />
          </div>

          {/* Schedule kind */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
              调度类型
            </label>
            <div className="flex gap-1.5">
              {(['at', 'every', 'cron'] as ScheduleKind[]).map((k) => (
                <button
                  key={k}
                  onClick={() => set('scheduleKind', k)}
                  className={cn(
                    'px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
                    form.scheduleKind === k
                      ? 'bg-[var(--accent)] text-white'
                      : 'bg-[var(--surface-muted)] text-[var(--text-muted)] hover:text-[var(--text)]'
                  )}
                >
                  {k}
                </button>
              ))}
            </div>
          </div>

          {/* Schedule expression */}
          {form.scheduleKind === 'at' && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
                指定时间（毫秒时间戳）
              </label>
              <input
                type="number"
                value={form.atMs}
                onChange={(e) => set('atMs', e.target.value)}
                placeholder={String(Date.now() + 60000)}
                className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono"
              />
            </div>
          )}
          {form.scheduleKind === 'every' && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
                间隔执行（毫秒）
              </label>
              <input
                type="number"
                value={form.everyMs}
                onChange={(e) => set('everyMs', e.target.value)}
                placeholder="60000"
                className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono"
              />
              <p className="text-xs text-[var(--text-faint)]">
                {form.everyMs ? `${(parseInt(form.everyMs) / 1000).toFixed(0)}s` : '—'}
              </p>
            </div>
          )}
          {form.scheduleKind === 'cron' && (
            <>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
                  Cron 表达式
                </label>
                <input
                  type="text"
                  value={form.expr}
                  onChange={(e) => set('expr', e.target.value)}
                  placeholder="0 9 * * *"
                  className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
                  时区{' '}
                  <span className="font-normal text-[var(--text-faint)]">
                    （可选，例如 Asia/Shanghai）
                  </span>
                </label>
                <input
                  type="text"
                  value={form.tz}
                  onChange={(e) => set('tz', e.target.value)}
                  placeholder="UTC"
                  className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono"
                />
              </div>
            </>
          )}

          {/* Message / Prompt */}
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
              消息 / Prompt
            </label>
            <textarea
              value={form.message}
              onChange={(e) => set('message', e.target.value)}
              placeholder="任务触发时 Agent 应执行的操作…"
              rows={3}
              className="w-full px-3 py-2 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] resize-none"
            />
          </div>

          {error && (
            <div className="rounded-lg px-3 py-2 bg-[var(--accent-soft)] text-xs text-[var(--danger)]">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-t border-[var(--border-subtle)]">
          <span className="text-xs text-[var(--text-faint)]">
            {isEdit ? '更新此定时任务' : '任务在 MiQroForge 运行时中执行'}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
            >
              取消
            </button>
            <button
              onClick={handleSubmit}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-sm font-medium transition-colors disabled:opacity-50"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              {isEdit ? '保存' : '创建'}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [runs, setRuns] = useState<CronRunEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editJob, setEditJob] = useState<CronJob | null>(null);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [jr, rr] = await Promise.all([window.miqi.cron.list(), window.miqi.cron.runs()]);
      setJobs(jr.jobs);
      setRuns(rr.runs);
      setActionError(null);
    } catch {
      // runtime not running
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleToggle = async (job: CronJob) => {
    setTogglingId(job.id);
    try {
      await window.miqi.cron.toggle(job.id, !job.enabled);
      await load();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : '切换失败');
    } finally {
      setTogglingId(null);
    }
  };

  const handleRun = async (job: CronJob) => {
    setRunningId(job.id);
    try {
      await window.miqi.cron.run(job.id);
      await load();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : '执行失败');
    } finally {
      setRunningId(null);
    }
  };

  const handleDelete = async (job: CronJob) => {
    setDeletingId(job.id);
    try {
      await window.miqi.cron.delete(job.id);
      await load();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : '删除失败');
    } finally {
      setDeletingId(null);
    }
  };

  const handleEdit = (job: CronJob) => {
    setEditJob(job);
  };

  return (
    <div className="flex flex-col h-full bg-[var(--background)]">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0">
        <div>
          <h1 className="text-base font-semibold text-[var(--text)]">定时任务</h1>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">
            {loading ? '加载中…' : `${jobs.length} 个任务，${runs.length} 条执行记录`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="text-xs text-[var(--text-faint)] hover:text-[var(--text-muted)] transition-colors px-2 py-1 rounded"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={() => {
              setShowCreate(true);
              setEditJob(null);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-sm font-medium transition-colors"
          >
            <Plus size={14} />
            创建任务
          </button>
        </div>
      </div>

      {/* Error toast */}
      {actionError && (
        <div className="mx-6 mt-3 flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--accent-soft)] text-xs text-[var(--danger)]">
          <span className="flex-1">{actionError}</span>
          <button
            onClick={() => setActionError(null)}
            className="text-[var(--text-faint)] hover:text-[var(--text)]"
          >
            <X size={12} />
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-6">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-sm text-[var(--text-faint)]">
            <Loader2 size={16} className="animate-spin mr-2" /> 正在加载任务…
          </div>
        ) : jobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-3 text-sm text-[var(--text-faint)]">
            <Clock size={28} />
            <span>暂无定时任务，创建第一个吧</span>
          </div>
        ) : (
          <>
            {/* Jobs table */}
            <div className="bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
              <div className="flex items-center gap-3 px-5 py-2.5 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)] text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                <span className="flex-1">任务</span>
                <span className="w-[120px] shrink-0">调度</span>
                <span className="w-[140px] shrink-0">下次运行</span>
                <span className="w-[100px] shrink-0">最近状态</span>
                <span className="w-[140px] shrink-0">操作</span>
              </div>
              <div className="divide-y divide-[var(--border-subtle)]">
                {jobs.map((job) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    isExpanded={expandedJob === job.id}
                    onToggleExpand={() => setExpandedJob(expandedJob === job.id ? null : job.id)}
                    onToggle={() => handleToggle(job)}
                    onRun={() => handleRun(job)}
                    onDelete={() => handleDelete(job)}
                    onEdit={() => handleEdit(job)}
                    toggling={togglingId === job.id}
                    running={runningId === job.id}
                    deleting={deletingId === job.id}
                  />
                ))}
              </div>
            </div>

            {/* Recent runs */}
            {runs.length > 0 && (
              <div className="bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
                <div className="flex items-center px-5 py-2.5 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)] text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                  最近执行记录
                  <span className="ml-auto font-normal normal-case tracking-normal">
                    {runs.length}
                  </span>
                </div>
                <div className="divide-y divide-[var(--border-subtle)]">
                  {runs.slice(0, 20).map((r, i) => (
                    <div
                      key={`${r.jobId}-${r.startedAtMs}-${i}`}
                      className="flex items-center gap-3 px-5 py-2.5 hover:bg-[var(--surface-muted)] transition-colors"
                    >
                      <span className="flex-1 text-sm text-[var(--text)]">{r.jobName}</span>
                      <span className="w-[160px] shrink-0 text-xs text-[var(--text-faint)] font-mono">
                        {formatMs(r.startedAtMs)}
                      </span>
                      <span
                        className={cn(
                          'w-[70px] shrink-0 text-xs font-medium',
                          r.status === 'ok'
                            ? 'text-[var(--success)]'
                            : r.status === 'error'
                              ? 'text-[var(--danger)]'
                              : 'text-[var(--text-faint)]'
                        )}
                      >
                        {r.status ?? '—'}
                      </span>
                      {r.error && (
                        <span
                          className="w-[200px] shrink-0 text-xs text-[var(--danger)] truncate"
                          title={r.error}
                        >
                          {r.error}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* Modals */}
      {showCreate && <CreateEditModal onClose={() => setShowCreate(false)} onSaved={load} />}
      {editJob && <CreateEditModal job={editJob} onClose={() => setEditJob(null)} onSaved={load} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single job row
// ---------------------------------------------------------------------------

interface JobRowProps {
  job: CronJob;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onToggle: () => void;
  onRun: () => void;
  onDelete: () => void;
  onEdit: () => void;
  toggling: boolean;
  running: boolean;
  deleting: boolean;
}

function JobRow({
  job,
  isExpanded,
  onToggleExpand,
  onToggle,
  onRun,
  onDelete,
  onEdit,
  toggling,
  running,
  deleting,
}: JobRowProps) {
  const statusColor = job.enabled ? 'text-[var(--success)]' : 'text-[var(--text-faint)]';

  return (
    <>
      <div
        data-testid={`cron-job-row-${job.name}`}
        className="flex items-center gap-3 px-5 py-2.5 hover:bg-[var(--surface-muted)] transition-colors group"
      >
        <button
          onClick={onToggleExpand}
          className="shrink-0 text-[var(--text-faint)] hover:text-[var(--text)] transition-colors"
        >
          {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <button onClick={onEdit} className="flex-1 text-left min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--text)]">{job.name}</span>
            <span className={cn('text-xs', statusColor)}>{job.enabled ? '运行中' : '已禁用'}</span>
          </div>
        </button>
        <span className="w-[120px] shrink-0 text-xs text-[var(--text-faint)] font-mono">
          {scheduleLabel(job)}
        </span>
        <span className="w-[140px] shrink-0 text-xs text-[var(--text-faint)] font-mono">
          {job.state.nextRunAtMs ? formatMs(job.state.nextRunAtMs) : '—'}
        </span>
        <span
          className={cn(
            'w-[100px] shrink-0 text-xs font-medium',
            job.state.lastStatus === 'ok'
              ? 'text-[var(--success)]'
              : job.state.lastStatus === 'error'
                ? 'text-[var(--danger)]'
                : 'text-[var(--text-faint)]'
          )}
        >
          {job.state.lastStatus ?? '—'}
        </span>
        <div className="w-[140px] shrink-0 flex items-center gap-1">
          <button
            onClick={onToggle}
            disabled={toggling}
            title={job.enabled ? '禁用' : '启用'}
            className="p-1.5 rounded-md text-[var(--text-faint)] hover:text-[var(--text)] hover:bg-[var(--surface-muted)] transition-colors disabled:opacity-40"
          >
            {toggling ? (
              <Loader2 size={14} className="animate-spin" />
            ) : job.enabled ? (
              <Pause size={14} />
            ) : (
              <Play size={14} />
            )}
          </button>
          <button
            onClick={onRun}
            disabled={running}
            title="立即执行"
            className="p-1.5 rounded-md text-[var(--text-faint)] hover:text-[var(--accent)] hover:bg-[var(--accent-soft)] transition-colors disabled:opacity-40"
          >
            {running ? <Loader2 size={14} className="animate-spin" /> : <Power size={14} />}
          </button>
          <button
            onClick={onDelete}
            disabled={deleting}
            title="删除"
            className="p-1.5 rounded-md text-[var(--text-faint)] hover:text-[var(--danger)] hover:bg-[var(--surface-muted)] transition-colors disabled:opacity-40"
          >
            {deleting ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
          </button>
        </div>
      </div>
      {/* Expanded detail */}
      {isExpanded && (
        <div className="px-10 py-3 bg-[var(--surface-muted)] text-xs text-[var(--text-muted)] flex flex-col gap-1 border-b border-[var(--border-subtle)]">
          <div>
            <span className="text-[var(--text-faint)]">ID:</span> {job.id}
          </div>
          <div>
            <span className="text-[var(--text-faint)]">创建于：</span> {formatMs(job.createdAtMs)}
          </div>
          <div>
            <span className="text-[var(--text-faint)]">消息：</span>{' '}
            {job.payload.message || '（空）'}
          </div>
          {job.state.lastError && (
            <div className="text-[var(--danger)]">最后错误： {job.state.lastError}</div>
          )}
        </div>
      )}
    </>
  );
}
