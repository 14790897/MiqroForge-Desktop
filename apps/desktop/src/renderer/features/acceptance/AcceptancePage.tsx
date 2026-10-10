/**
 * 阶段 3:验收(SURE)页面——选项目 → 原生核查 → 结构化报告。
 *
 * 数据路径:后端 `sure.check.start` 以原生 spawn 运行
 * `sure check "<项目绝对路径>" --format json`;进度(心跳)/报告/失败/取消
 * 经 `sure_check_*` 事件到达(桥侧孤儿事件自动转发)。本页只驱动与呈现,
 * 不复制任何核查逻辑——结论一律以 SURE 为准,状态如实呈现不美化。
 */

import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, FolderOpen, Loader2, Play, RefreshCw, XCircle } from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { ReportView } from './ReportView';
import {
  formatElapsed,
  type SureApiResult,
  type SureCheckEnvelope,
  type SureCheckFailure,
  type SureCheckStatus,
  type SureHealth,
} from '../../../shared/sureReport';

type Phase = 'idle' | 'running' | 'report' | 'failed' | 'cancelled';

export function AcceptancePage() {
  const [health, setHealth] = useState<SureHealth | null>(null);
  const [healthChecking, setHealthChecking] = useState(true);
  const [healthError, setHealthError] = useState<string | null>(null);

  const [project, setProject] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [envelope, setEnvelope] = useState<SureCheckEnvelope | null>(null);
  const [failure, setFailure] = useState<SureCheckFailure | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const refreshHealth = useCallback(async () => {
    setHealthChecking(true);
    setHealthError(null);
    try {
      const res = (await window.miqi.sure.health()) as SureApiResult<SureHealth> | null;
      if (res && res.ok) setHealth(res.value);
      else if (res && !res.ok) setHealthError(res.error);
      else setHealthError('健康检查失败(桥可能未运行)');
    } catch (e) {
      setHealthError(e instanceof Error ? e.message : String(e));
    } finally {
      setHealthChecking(false);
    }
  }, []);

  // 挂载:健康检查 + 运行中任务对账(页面切换/重开不丢"运行中"状态)
  useEffect(() => {
    void refreshHealth();
    let alive = true;
    void (async () => {
      try {
        const res = (await window.miqi.sure.status()) as SureApiResult<SureCheckStatus> | null;
        if (alive && res?.ok && res.value.task) {
          setProject(res.value.task.project);
          setStartedAt(res.value.task.startedAt);
          setElapsedMs(res.value.task.elapsedMs);
          setPhase('running');
        }
      } catch {
        /* 旧版 preload 可能缺 sure 命名空间——忽略 */
      }
    })();
    return () => {
      alive = false;
    };
  }, [refreshHealth]);

  // 事件订阅(清理照 useQraftStatus 模式:逐一 unsubscribe + try/catch 兜底)
  useEffect(() => {
    const unsubs: Array<() => void> = [];
    try {
      unsubs.push(
        window.miqi.sure.onProgress((d) => {
          setElapsedMs(d.elapsedMs);
          setStartedAt((s) => s ?? Date.now() - d.elapsedMs);
          setPhase('running');
          setActionError(null);
        })
      );
      unsubs.push(
        window.miqi.sure.onReport((d) => {
          setEnvelope(d.envelope);
          setFailure(null);
          setPhase('report');
          void refreshHealth(); // 报告里带 sure_version,顺手刷新健康行
        })
      );
      unsubs.push(
        window.miqi.sure.onFailed((f) => {
          setFailure(f);
          setPhase('failed');
        })
      );
      unsubs.push(
        window.miqi.sure.onCancelled(() => {
          setPhase('cancelled');
        })
      );
    } catch {
      /* preload 无 sure 命名空间时可忽略(旧构建) */
    }
    return () => unsubs.forEach((u) => u());
  }, [refreshHealth]);

  // 运行中:本地秒表(心跳间隔之间平滑显示;心跳事件覆盖校准)
  useEffect(() => {
    if (phase !== 'running' || startedAt === null) return;
    const timer = setInterval(() => setElapsedMs(Date.now() - startedAt), 500);
    return () => clearInterval(timer);
  }, [phase, startedAt]);

  const startCheck = useCallback(async () => {
    setActionError(null);
    setEnvelope(null);
    setFailure(null);
    try {
      const res = (await window.miqi.sure.startCheck(project.trim())) as SureApiResult<{
        taskId: string;
        project: string;
      }> | null;
      if (!res || !res.ok) {
        setActionError(res ? res.error : '启动核查失败(桥可能未运行)');
        return;
      }
      setStartedAt(Date.now());
      setElapsedMs(0);
      setPhase('running');
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }, [project]);

  const cancelCheck = useCallback(async () => {
    try {
      const res = (await window.miqi.sure.cancelCheck()) as SureApiResult<{ ok: boolean }> | null;
      if (!res || !res.ok || !res.value.ok) {
        setActionError(res && !res.ok ? res.error : '取消失败(可能已结束)');
      }
      // 成功时不改状态——由 sure_check_cancelled 事件置位,避免与真实进程状态脱节
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const pickDirectory = useCallback(async () => {
    try {
      const dir = await window.miqi.dialog.openDirectory();
      if (dir) setProject(dir);
    } catch {
      /* 旧版 preload 兜底 */
    }
  }, []);

  const sureReady = Boolean(health?.installed);

  return (
    <div className="p-6 max-w-3xl space-y-5">
      <header>
        <h2 className="text-base font-semibold text-[var(--text)]">验收(SURE)</h2>
        <p className="text-xs text-[var(--text-muted)] mt-0.5">
          对项目做只读核查:已核查 / 未核查 / 发现的问题——结论一律以 SURE 为准,不做美化。
        </p>
      </header>

      {/* SURE 可用性(未安装 → 引导安装,而非静默失败) */}
      {healthChecking ? (
        <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
          <Loader2 size={13} className="animate-spin" /> 正在检测 SURE…
        </div>
      ) : !sureReady ? (
        <div className="rounded-md border border-[var(--warning)] bg-[var(--warning-bg)] p-3 space-y-2">
          <div className="flex items-center gap-2 text-xs text-[var(--warning)]">
            <AlertTriangle size={14} />
            <span>
              未检测到可用的 SURE
              {healthError ? `:${healthError}` : health?.error ? `:${health.error}` : ''}
            </span>
          </div>
          <p className="text-size-2xs text-[var(--text-muted)]">
            安装 SURE(≥0.1.2)后回到本页点击「重新检测」。支持三种就位方式: 官方安装器(per-user)/
            PATH 上的 sure / 环境变量 SURE_BIN 指向可执行文件。
          </p>
          <div className="flex items-center gap-2">
            <a
              href="https://github.com/lichman0405/SURE"
              target="_blank"
              rel="noreferrer"
              className="text-xs text-[var(--accent)] hover:underline"
            >
              SURE 项目主页与安装说明 ↗
            </a>
            <Button variant="secondary" size="sm" onClick={() => void refreshHealth()}>
              <RefreshCw size={13} className="mr-1" /> 重新检测
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-size-2xs text-[var(--text-faint)] font-mono truncate">
          SURE {health?.version ?? '版本未知'} · {health?.binary}
        </p>
      )}

      {actionError ? (
        <div className="rounded-md border border-[var(--danger)] bg-[var(--danger-bg)] px-3 py-2 text-xs text-[var(--danger)]">
          {actionError}
        </div>
      ) : null}

      {/* 选项目 + 开始 */}
      {phase === 'idle' || phase === 'running' ? (
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            placeholder="项目绝对路径,例如 D:\\my-project"
            disabled={phase === 'running'}
            className="flex-1 rounded-md border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text)] placeholder:text-[var(--text-faint)] focus:outline-none focus:border-[var(--accent)] disabled:opacity-60"
            data-testid="acceptance-project-input"
          />
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void pickDirectory()}
            disabled={phase === 'running'}
          >
            <FolderOpen size={13} className="mr-1" /> 选择目录
          </Button>
        </div>
      ) : null}

      {phase === 'idle' ? (
        <Button
          size="sm"
          onClick={() => void startCheck()}
          disabled={!sureReady || !project.trim()}
          data-testid="acceptance-start"
        >
          <Play size={13} className="mr-1" /> 开始核查
        </Button>
      ) : null}

      {/* 运行中 */}
      {phase === 'running' ? (
        <div
          className="flex items-center gap-3 rounded-md border border-[var(--border)] bg-[var(--surface)] px-4 py-3"
          data-testid="acceptance-running"
        >
          <Loader2 size={16} className="animate-spin text-[var(--info)]" />
          <div className="flex-1 min-w-0">
            <p className="text-xs text-[var(--text)]">
              核查中 · 已耗时 <span className="font-mono">{formatElapsed(elapsedMs)}</span>
            </p>
            <p className="text-size-2xs text-[var(--text-faint)] font-mono truncate">{project}</p>
          </div>
          <Button variant="danger" size="sm" onClick={() => void cancelCheck()}>
            <XCircle size={13} className="mr-1" /> 取消核查
          </Button>
        </div>
      ) : null}

      {/* 报告 */}
      {phase === 'report' && envelope ? (
        <div className="space-y-4" data-testid="acceptance-report">
          <ReportView envelope={envelope} />
          <div className="pt-2 border-t border-[var(--border)]">
            <Button variant="secondary" size="sm" onClick={() => void startCheck()}>
              <RefreshCw size={13} className="mr-1" /> 再次核查
            </Button>
          </div>
        </div>
      ) : null}

      {/* 失败(如实呈现:message + code + stderr 尾巴) */}
      {phase === 'failed' && failure ? (
        <div
          className="rounded-md border border-[var(--danger)] bg-[var(--danger-bg)] p-3 space-y-2"
          data-testid="acceptance-failed"
        >
          <div className="flex items-center gap-2 text-xs text-[var(--danger)]">
            <AlertTriangle size={14} />
            <span>{failure.message}</span>
            <span className="font-mono text-size-2xs opacity-70">{failure.code}</span>
          </div>
          {failure.stderrTail ? (
            <pre className="max-h-32 overflow-auto rounded bg-[var(--surface)] p-2 text-size-2xs font-mono text-[var(--text-muted)] whitespace-pre-wrap">
              {failure.stderrTail}
            </pre>
          ) : null}
          <Button variant="secondary" size="sm" onClick={() => void startCheck()}>
            <RefreshCw size={13} className="mr-1" /> 重试
          </Button>
        </div>
      ) : null}

      {/* 已取消 */}
      {phase === 'cancelled' ? (
        <div
          className="rounded-md border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 space-y-2"
          data-testid="acceptance-cancelled"
        >
          <p className="text-xs text-[var(--text-muted)]">
            已取消核查——取消不产出报告(与 SURE CLI 语义一致:报告只在正常结束时生成)。
          </p>
          <Button variant="secondary" size="sm" onClick={() => setPhase('idle')}>
            重新开始
          </Button>
        </div>
      ) : null}
    </div>
  );
}
