import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Check,
  Folder,
  Loader2,
  Monitor,
  RefreshCw,
  Terminal,
  X,
  Zap,
} from 'lucide-react';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import { cn } from '../../lib/utils';
import type { WslCheckResult, WslInstallProgress } from '../../../shared/ipc';

type Step = 'welcome';
type CheckState<T> = {
  status: 'idle' | 'checking' | 'ok' | 'warning' | 'error';
  result?: T;
  error?: string;
};

interface PythonStatus {
  ok: boolean;
  python_version: string;
  issues: string[];
  config_exists: boolean;
}

const DEFAULT_WORKSPACE = '~/.miqi/workspace';

export function SetupWizard({
  onComplete,
  onExit,
}: {
  onComplete: () => void;
  onExit?: () => void;
}) {
  const [step, setStep] = useState<Step>('welcome');
  const [workspace, setWorkspace] = useState(DEFAULT_WORKSPACE);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [pythonCheck, setPythonCheck] = useState<CheckState<PythonStatus>>({ status: 'idle' });
  const [wslCheck, setWslCheck] = useState<CheckState<WslCheckResult>>({ status: 'idle' });

  // ── WSL one-click install state (new in #373) ──────────────────────
  const [wslInstalling, setWslInstalling] = useState(false);
  const [wslInstallPhase, setWslInstallPhase] = useState<WslInstallProgress['phase'] | null>(null);
  const [wslInstallMessage, setWslInstallMessage] = useState('');
  const [wslInstallReboot, setWslInstallReboot] = useState(false);

  const handleWslInstall = useCallback(async () => {
    setWslInstalling(true);
    setWslInstallReboot(false);
    setWslInstallPhase('checking');
    setWslInstallMessage('正在检测 WSL 状态...');
    try {
      const result = await window.miqi.wsl.installAndProvision();
      if (result.success && result.rebootRequired) {
        setWslInstallReboot(true);
        setWslInstallMessage(result.nextStep ?? '请重启系统后继续');
      } else if (result.success) {
        setWslInstallPhase('complete');
        setWslInstallMessage('WSL2 安装配置完成！');
      } else {
        setWslInstallPhase('error');
        setWslInstallMessage(result.error ?? '安装失败');
      }
      await runEnvironmentChecks();
    } catch (e: any) {
      setWslInstallPhase('error');
      setWslInstallMessage(e?.message ?? '安装过程出错');
    } finally {
      setWslInstalling(false);
    }
  }, []);

  // Listen for WSL install progress events from main process
  useEffect(() => {
    const unsub = window.miqi.wsl.onInstallProgress((data: WslInstallProgress) => {
      setWslInstallPhase(data.phase);
      setWslInstallMessage(data.message);
      if (data.rebootRequired) setWslInstallReboot(true);
    });
    return unsub;
  }, []);

  useEffect(() => {
    window.miqi.config
      .get()
      .then((cfg) => {
        if (!cfg) return;
        const agents = (cfg as Record<string, unknown>)['agents'] as
          Record<string, unknown> | undefined;
        const defaults = agents?.['defaults'] as Record<string, unknown> | undefined;
        if (defaults?.['workspace']) setWorkspace(String(defaults['workspace']));
      })
      .catch(() => {
        /* no existing config yet */
      });
  }, []);

  const runEnvironmentChecks = async () => {
    setPythonCheck({ status: 'checking' });
    setWslCheck({ status: 'checking' });

    const [pythonResult, wslResult] = await Promise.allSettled([
      window.miqi.python.check(),
      window.miqi.wsl.check(),
    ]);

    if (pythonResult.status === 'fulfilled') {
      const result = pythonResult.value as PythonStatus;
      setPythonCheck({ status: result.ok ? 'ok' : 'error', result });
    } else {
      setPythonCheck({
        status: 'error',
        error: pythonResult.reason?.message ?? String(pythonResult.reason),
      });
    }

    if (wslResult.status === 'fulfilled') {
      const result = wslResult.value as WslCheckResult;
      const hasWarning = result.isWindows && result.featureState !== 'ready';
      setWslCheck({ status: hasWarning ? 'warning' : 'ok', result });
    } else {
      setWslCheck({
        status: 'warning',
        error: wslResult.reason?.message ?? String(wslResult.reason),
      });
    }
  };

  useEffect(() => {
    void runEnvironmentChecks();
  }, []);

  const saveInitialConfig = async (config: Record<string, unknown>) => {
    if (saving) return;
    setSaving(true);
    setSaveError('');
    try {
      await window.miqi.setup.writeInitialConfig(config);
      try {
        await window.miqi.runtime.start();
      } catch {
        /* non-fatal */
      }
      onComplete();
    } catch (e: any) {
      setSaveError(e?.message ?? String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleUseDefaults = () => {
    void saveInitialConfig({
      workspace: workspace || DEFAULT_WORKSPACE,
    });
  };

  const pythonBlocksStart = pythonCheck.status === 'checking' || pythonCheck.status === 'error';

  const renderStatusIcon = (status: CheckState<unknown>['status']) => {
    if (status === 'checking') return <Loader2 size={13} className="animate-spin" />;
    if (status === 'ok') return <Check size={13} />;
    if (status === 'warning' || status === 'error') return <AlertTriangle size={13} />;
    return <RefreshCw size={13} />;
  };

  const renderEnvironmentStatus = () => {
    const statusStyles: Record<CheckState<unknown>['status'], string> = {
      idle: 'border-[var(--border)] bg-[var(--surface)]',
      checking: 'border-[var(--border)] bg-[var(--surface)]',
      ok: 'border-[var(--success)]/40 bg-[var(--success)]/5',
      warning: 'border-[var(--warning)]/40 bg-[var(--warning)]/5',
      error: 'border-[var(--danger)]/40 bg-[var(--danger)]/5',
    };

    const pythonSummary = (() => {
      if (pythonCheck.status === 'checking') return '正在检查 Python 和 MiQroForge 依赖...';
      if (pythonCheck.status === 'ok') {
        const version = pythonCheck.result?.python_version;
        return version ? `已就绪 · Python ${version}` : '已就绪';
      }
      if (pythonCheck.status === 'error') {
        return (
          pythonCheck.result?.issues?.[0] ?? pythonCheck.error ?? 'Python 或 MiQroForge 依赖不可用'
        );
      }
      return '等待检查';
    })();

    const wslSummary = (() => {
      if (wslCheck.status === 'checking') return '正在检查 WSL2 状态...';
      const result = wslCheck.result;
      if (result && !result.isWindows) return '非 Windows 环境，无需 WSL2';
      if (wslCheck.status === 'ok') {
        const distro = result?.defaultDistro ?? result?.distros?.[0];
        return distro ? `已就绪 · ${distro}` : '已就绪';
      }
      if (wslCheck.status === 'warning') {
        if (wslCheck.error) return wslCheck.error;
        if (!result) return 'WSL2 状态需要确认';
        switch (result.featureState) {
          case 'not-enabled':
            return '未启用 WSL 功能，需在 Windows 功能中开启';
          case 'not-installed':
            return 'WSL 功能已启用，但 WSL2 内核未安装';
          case 'installed-but-not-initialized':
            if (result.distros.length === 0) return 'WSL 已安装，但还没有 Linux 发行版';
            return '发行版已安装，但尚未完成首次初始化';
          default:
            if (result.version && result.version !== '2')
              return `检测到 WSL ${result.version}，建议升级到 WSL2`;
            return 'WSL2 状态需要确认';
        }
      }
      return '等待检查';
    })();

    const renderCommand = (command: string) => (
      <code className="block rounded-md bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--accent)] break-all">
        {command}
      </code>
    );

    const renderPythonGuidance = () => {
      if (pythonCheck.status !== 'error') return null;

      return (
        <div className="mt-2 rounded-md border border-[var(--danger)]/25 bg-[var(--danger)]/5 p-3">
          <p className="text-xs font-medium text-[var(--danger)]">
            需要先修复 Python / MiQroForge 环境
          </p>
          <ul className="mt-1.5 list-disc pl-4 text-xs text-[var(--danger)] space-y-1">
            {(pythonCheck.result?.issues?.length
              ? pythonCheck.result.issues
              : [pythonCheck.error ?? 'Python 或 MiQroForge 依赖不可用']
            ).map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
          <div className="mt-2 space-y-1.5 text-xs text-[var(--text-muted)]">
            <p>处理方式：</p>
            <p>1. 安装 Python 3.11 或更高版本，或设置 MIQI_PYTHON_PATH 指向可用 Python。</p>
            <p>2. 在 MiQroForge 仓库根目录安装依赖后重新检查：</p>
            {renderCommand('uv sync')}
          </div>
        </div>
      );
    };

    const WSL_STEPS = [
      { phase: 'enabling_features', label: '启用功能' },
      { phase: 'installing_wsl', label: '安装内核' },
      { phase: 'installing_distro', label: '安装 Ubuntu' },
    ] as const;
    const WSL_PHASE_IDX: Record<string, number> = {
      checking: -1,
      enabling_features: 0,
      installing_wsl: 1,
      installing_distro: 2,
      complete: 3,
      error: -1,
    };

    const renderWslProgress = () => {
      if (!wslInstallPhase) return null;
      const currentIdx = WSL_PHASE_IDX[wslInstallPhase] ?? -1;
      return (
        <div className="mt-3 pt-3 border-t border-[var(--border-subtle)]">
          <div className="flex items-center justify-between mb-2">
            {WSL_STEPS.map((step, i) => {
              const isComplete = wslInstallPhase === 'complete' || currentIdx > i;
              const isCurrent = currentIdx === i;
              const isError = wslInstallPhase === 'error' && currentIdx === i;
              return (
                <div key={step.phase} className="flex flex-col items-center gap-1">
                  <div
                    className={cn(
                      'w-6 h-6 rounded-full flex items-center justify-center text-size-2xs font-bold',
                      isComplete && 'bg-[var(--accent)] text-[var(--accent-text)]',
                      isCurrent &&
                        !isError &&
                        'bg-[var(--accent)]/15 text-[var(--accent)] ring-2 ring-[var(--accent)]/30',
                      isError &&
                        'bg-[var(--danger)]/15 text-[var(--danger)] ring-2 ring-[var(--danger)]/30',
                      !isComplete &&
                        !isCurrent &&
                        !isError &&
                        'bg-[var(--surface-muted)] text-[var(--text-faint)]'
                    )}
                  >
                    {isComplete ? (
                      '✓'
                    ) : isCurrent && !isError ? (
                      <Loader2 size={10} className="animate-spin" />
                    ) : isError ? (
                      '⚠'
                    ) : (
                      '○'
                    )}
                  </div>
                  <span className="text-size-2xs text-[var(--text-faint)]">{step.label}</span>
                </div>
              );
            })}
          </div>
          <div className="relative h-1 bg-[var(--surface-muted)] rounded-full mb-2 mx-3">
            {currentIdx >= 0 && (
              <div
                className="absolute inset-y-0 left-0 bg-[var(--accent)] rounded-full transition-all duration-500"
                style={{ width: `${Math.min((currentIdx / (WSL_STEPS.length - 1)) * 100, 100)}%` }}
              />
            )}
          </div>
          <div className="flex items-center gap-1.5 text-size-2xs text-[var(--text-muted)]">
            {wslInstalling && wslInstallPhase !== 'complete' && wslInstallPhase !== 'error' && (
              <Loader2 size={10} className="animate-spin text-[var(--accent)] shrink-0" />
            )}
            {wslInstallPhase === 'complete' && (
              <Check size={10} className="text-[var(--accent)] shrink-0" />
            )}
            {wslInstallPhase === 'error' && (
              <AlertTriangle size={10} className="text-[var(--danger)] shrink-0" />
            )}
            {wslInstallMessage}
          </div>
          {wslInstallReboot && (
            <div className="mt-2 px-2.5 py-2 rounded-md bg-[var(--warning)]/10 border border-[var(--warning)]/20 text-size-2xs">
              <span className="flex items-center gap-1">
                <AlertTriangle size={10} className="text-[var(--warning)]" />
                需要重启系统以完成安装
              </span>
            </div>
          )}
        </div>
      );
    };

    const renderWslGuidance = () => {
      if (wslCheck.status !== 'warning') return null;

      const result = wslCheck.result;
      if (result && !result.isWindows) return null;

      const featureState = result?.featureState;
      const showAction =
        featureState === 'not-enabled' ||
        featureState === 'not-installed' ||
        (featureState === 'installed-but-not-initialized' && result?.distros.length === 0);

      // ── States that can run one-click install ──
      if (showAction) {
        const titles: Record<string, string> = {
          'not-enabled': '⚡ 一键安装 WSL2',
          'not-installed': '⚡ 安装 WSL2 内核',
          'installed-but-not-initialized': '⚡ 安装 Ubuntu 发行版',
        };
        const descs: Record<string, string> = {
          'not-enabled':
            '需要启用「Windows 子系统」和「虚拟机平台」两个可选功能。点击下方按钮自动完成，安装过程需重启一次。',
          'not-installed':
            'Windows 可选功能已启用，还需安装 WSL2 内核。点击按钮自动完成，需重启一次。',
          'installed-but-not-initialized':
            'WSL2 内核就绪，还需安装 Ubuntu 发行版。点击按钮自动完成（约 2-5 分钟），无需重启。',
        };
        const title = titles[featureState ?? ''] ?? '⚡ 安装 WSL2';
        const desc = descs[featureState ?? ''] ?? '点击一键安装自动完成 WSL2 配置。';

        return (
          <div
            className={cn(
              'mt-2 rounded-md border p-3',
              wslInstalling
                ? 'border-[var(--accent)]/30 bg-[var(--accent)]/3'
                : 'border-[var(--warning)]/25 bg-[var(--warning)]/5'
            )}
          >
            <p className="text-xs font-medium text-[var(--text)]">{title}</p>
            <p className="mt-1 text-xs text-[var(--text-muted)] leading-relaxed">{desc}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {wslInstalling ? (
                <Button variant="ghost" size="sm" disabled>
                  <Loader2 size={12} className="animate-spin" /> 安装中...
                </Button>
              ) : (
                <Button variant="ghost" size="sm" onClick={handleWslInstall}>
                  一键安装
                </Button>
              )}
              <Button variant="ghost" size="sm" onClick={() => void runEnvironmentChecks()}>
                重新检查
              </Button>
            </div>
            {renderWslProgress()}
            <div className="mt-3 pt-2 border-t border-[var(--border-subtle)] flex items-center justify-between">
              <p className="text-xs text-[var(--text-faint)]">
                ⚡ 沙箱等功能在 WSL2 上更稳定，也可以跳过稍后在设置中安装。
              </p>
              <Button variant="ghost" size="sm" onClick={handleUseDefaults}>
                跳过
              </Button>
            </div>
          </div>
        );
      }

      // ── installed-but-not-initialized (has distros) ──
      if (featureState === 'installed-but-not-initialized') {
        return (
          <div className="mt-2 rounded-md border border-[var(--warning)]/25 bg-[var(--warning)]/5 p-3">
            <p className="text-xs font-medium text-[var(--text)]">发行版已安装</p>
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              发行版 {result?.defaultDistro || result?.distros?.[0]} 已安装。MiQroForge
              将自动从中导出沙箱环境，无需额外操作。
            </p>
            <div className="mt-2">
              <Button variant="ghost" size="sm" onClick={() => void runEnvironmentChecks()}>
                重新检查
              </Button>
            </div>
          </div>
        );
      }

      // ── Version mismatch ──
      if (result?.version && result.version !== '2') {
        return (
          <div className="mt-2 rounded-md border border-[var(--warning)]/25 bg-[var(--warning)]/5 p-3">
            <p className="text-xs font-medium text-[var(--text)]">建议升级到 WSL2</p>
            <div className="mt-2 space-y-1.5">
              {renderCommand('wsl --set-default-version 2')}
              {result.defaultDistro
                ? renderCommand(`wsl --set-version ${result.defaultDistro} 2`)
                : null}
            </div>
          </div>
        );
      }

      return (
        <p className="mt-2 text-xs text-[var(--text-muted)]">
          WSL2 状态暂时无法确认。你可以先进入应用，稍后在设置中继续处理。
        </p>
      );
    };

    return (
      <section className="flex flex-col gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)]/40 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-[var(--text)]">启动前状态</h2>
            <p className="text-xs text-[var(--text-faint)] mt-0.5">
              保留基础环境检查；Provider 和工具能力可稍后配置。
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void runEnvironmentChecks()}
            disabled={pythonCheck.status === 'checking' || wslCheck.status === 'checking'}
            title="重新检查"
          >
            <RefreshCw size={14} />
          </Button>
        </div>

        <div className="grid gap-2">
          <div className={cn('rounded-lg border px-3 py-2.5', statusStyles[pythonCheck.status])}>
            <div className="flex items-start gap-2">
              <Terminal size={15} className="mt-0.5 text-[var(--text-muted)]" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--text)]">
                  {renderStatusIcon(pythonCheck.status)}
                  Python / MiQroForge
                </div>
                <p className="text-xs text-[var(--text-muted)] mt-1 break-words">{pythonSummary}</p>
                {renderPythonGuidance()}
              </div>
            </div>
          </div>

          <div className={cn('rounded-lg border px-3 py-2.5', statusStyles[wslCheck.status])}>
            <div className="flex items-start gap-2">
              <Monitor size={15} className="mt-0.5 text-[var(--text-muted)]" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-[var(--text)]">
                  {renderStatusIcon(wslCheck.status)}
                  WSL2
                </div>
                <p className="text-xs text-[var(--text-muted)] mt-1 break-words">{wslSummary}</p>
                {renderWslGuidance()}
              </div>
            </div>
          </div>
        </div>

        {pythonCheck.status === 'error' && (
          <p className="text-xs text-[var(--danger)]">
            Python / MiQroForge 运行环境未就绪，修复后重新检查即可继续进入应用。
          </p>
        )}
      </section>
    );
  };

  const renderWelcome = () => (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col items-center text-center gap-3">
        <div className="w-16 h-16 rounded-2xl bg-[var(--accent-soft)] flex items-center justify-center mb-1">
          <Zap size={32} className="text-[var(--accent)] icon-mono" />
          <span className="icon-color text-3xl leading-none">⚡</span>
        </div>
        <div>
          <h1 className="text-2xl font-semibold text-[var(--text)]">欢迎使用 MiQroForge Desktop</h1>
          <p className="text-sm text-[var(--text-muted)] max-w-sm leading-relaxed mt-2">
            默认配置会先初始化工作目录。Provider、API Key、模型和工具可稍后在设置中配置。
          </p>
        </div>
      </div>

      <WorkspacePicker workspace={workspace} setWorkspace={setWorkspace} />

      {renderEnvironmentStatus()}

      {saveError && (
        <div className="rounded-lg border border-[var(--danger)]/30 bg-[var(--danger)]/10 px-3 py-2 text-xs text-[var(--danger)]">
          {saveError}
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Button onClick={handleUseDefaults} disabled={saving || pythonBlocksStart}>
          {saving ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
          使用默认配置，进入应用
        </Button>
      </div>
    </div>
  );

  const allSteps: Step[] = ['welcome'];
  const stepIdx = allSteps.indexOf(step);

  return (
    <div className="flex items-center justify-center min-h-full bg-[var(--background)] py-8">
      <div className="w-full max-w-lg bg-[var(--surface-elevated)] border border-[var(--border)] rounded-xl shadow-sm p-8 relative">
        {onExit && (
          <button
            onClick={onExit}
            className="absolute top-4 right-4 p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--surface-muted)] transition-colors"
            title="退出配置向导"
          >
            <X size={16} />
          </button>
        )}

        <div className="flex items-center justify-center gap-1.5 mb-8">
          {allSteps.map((s, i) => (
            <div key={s} className="flex items-center gap-1.5">
              <div
                className={cn(
                  'w-6 h-6 rounded-full flex items-center justify-center text-size-2xs font-medium transition-colors',
                  step === s
                    ? 'bg-[var(--accent)] text-white'
                    : i < stepIdx
                      ? 'bg-[var(--success)]/30 text-[var(--success)]'
                      : 'bg-[var(--surface-muted)] text-[var(--text-faint)]'
                )}
              >
                {i < stepIdx ? <Check size={10} /> : i + 1}
              </div>
              {i < allSteps.length - 1 && <div className="w-4 h-px bg-[var(--border)]" />}
            </div>
          ))}
        </div>

        {step === 'welcome' && renderWelcome()}
      </div>
    </div>
  );
}
function WorkspacePicker({
  workspace,
  setWorkspace,
}: {
  workspace: string;
  setWorkspace: (workspace: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-xs font-medium text-[var(--text-muted)]">工作目录</label>
      <div className="flex gap-2">
        <Input
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder={DEFAULT_WORKSPACE}
          className="flex-1"
        />
        <Button
          variant="outline"
          size="sm"
          onClick={async () => {
            const dir = await window.miqi.dialog.openFile();
            if (dir) setWorkspace(dir);
          }}
          title="选择工作目录"
        >
          <Folder size={14} />
        </Button>
      </div>
    </div>
  );
}
