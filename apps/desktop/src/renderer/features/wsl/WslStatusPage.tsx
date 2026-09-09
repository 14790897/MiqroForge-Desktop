import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Cpu,
  HardDrive,
  MemoryStick,
  Clock,
  RefreshCw,
  TriangleAlert,
  CheckCircle2,
  Activity,
  Download,
  Circle,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import type {
  WslStatsResult,
  WslInstallProgress,
  WslInstallAndProvisionResult,
} from '../../../shared/ipc';

const REFRESH_MS = 3_000;

function pctColor(p: number) {
  if (p > 85) return 'bg-[var(--danger)]';
  if (p > 60) return 'bg-[var(--warning)]';
  return 'bg-[var(--accent)]';
}

function pctColorText(p: number) {
  if (p > 85) return 'text-[var(--danger)]';
  if (p > 60) return 'text-[var(--warning)]';
  return 'text-[var(--accent)]';
}

function fmtUptime(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (s < 86400) return `${h}h ${m}m`;
  const d = Math.floor(s / 86400);
  return `${d}d ${Math.floor((s % 86400) / 3600)}h`;
}

function fmtMem(mb: number): string {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${mb} MB`;
}

function CircleRing({ pct, size = 112 }: { pct: number; size?: number }) {
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.min(pct, 100) / 100);
  const color = pct > 85 ? 'var(--danger)' : pct > 60 ? 'var(--warning)' : 'var(--accent)';

  return (
    <svg width={size} height={size} className="-rotate-90">
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--surface-muted)"
        strokeWidth="9"
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth="9"
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={offset}
        className="transition-all duration-700"
      />
    </svg>
  );
}

const AISHADOW_PREFIX = 'AIShadow';

// ── Install phase definitions for the state machine ──────────────────
const INSTALL_STEPS: { phase: string; labelKey: string; icon: typeof CheckCircle2 }[] = [
  { phase: 'enabling_features', labelKey: 'wsl.stepEnableFeatures', icon: Cpu },
  { phase: 'installing_wsl', labelKey: 'wsl.stepInstallWsl', icon: Download },
  { phase: 'installing_distro', labelKey: 'wsl.stepInstallDistro', icon: Download },
];

const PHASE_INDEX: Record<string, number> = {
  checking: -1,
  enabling_features: 0,
  installing_wsl: 1,
  installing_distro: 2,
  complete: 3,
  error: -1,
};

export default function WslStatusPage() {
  const { t } = useTranslation();
  const [stats, setStats] = useState<WslStatsResult | null>(null);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [distros, setDistros] = useState<string[]>([]);
  const [selected, setSelected] = useState('');
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const initialFetch = useRef(false);

  // ── Install state (new in #361) ────────────────────────────────────
  const [installing, setInstalling] = useState(false);
  const [installPhase, setInstallPhase] = useState<WslInstallProgress['phase'] | null>(null);
  const [installMessage, setInstallMessage] = useState('');
  const [installRebootRequired, setInstallRebootRequired] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [installNextStep, setInstallNextStep] = useState<string | null>(null);

  const fetchDistros = useCallback(async () => {
    try {
      const r = await window.miqi.wsl.check();
      if (r?.installed && r.distros?.length > 0) {
        const sorted = [...r.distros];
        const idx = sorted.findIndex(
          (d: string) => d === AISHADOW_PREFIX || d.startsWith(AISHADOW_PREFIX)
        );
        if (idx > 0) {
          const [aisDistro] = sorted.splice(idx, 1);
          sorted.unshift(aisDistro);
        }
        setDistros(sorted);

        if (!initialFetch.current) {
          const auto =
            sorted.find((d: string) => d === AISHADOW_PREFIX || d.startsWith(AISHADOW_PREFIX)) ??
            r.defaultDistro;
          if (auto) setSelected(auto);
          initialFetch.current = true;
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  const fetchStats = useCallback(
    async (silent = false) => {
      if (!selected) return;
      if (!silent) setFetching(true);
      try {
        const r = await window.miqi.wsl.getStats(selected);
        if (r?.ok) {
          setStats(r);
          setError(null);
        } else {
          setError(r?.error ?? t('wsl.fetchFail'));
        }
      } catch (e: any) {
        setError(e?.message ?? t('wsl.ipcFail'));
      } finally {
        setFetching(false);
      }
    },
    [selected, t]
  );

  // ── One-click install flow ─────────────────────────────────────────
  const handleInstall = useCallback(async () => {
    setInstalling(true);
    setInstallError(null);
    setInstallRebootRequired(false);
    setInstallNextStep(null);
    setInstallPhase('checking');
    setInstallMessage(t('wsl.checking'));

    try {
      const result = await window.miqi.wsl.installAndProvision();
      if (result.success) {
        if (result.rebootRequired) {
          setInstallRebootRequired(true);
          setInstallNextStep(result.nextStep ?? null);
        } else {
          setInstallPhase('complete');
          setInstallMessage(t('wsl.done'));
        }
      } else {
        setInstallError(result.error ?? t('wsl.installFail'));
        setInstallNextStep(result.nextStep ?? null);
        setInstallPhase('error');
      }
      // Refresh distro list
      await fetchDistros();
    } catch (e: any) {
      setInstallError(e?.message ?? t('wsl.installError'));
      setInstallPhase('error');
    } finally {
      setInstalling(false);
    }
  }, [fetchDistros, t]);

  // ── Listen for install progress events ─────────────────────────────
  useEffect(() => {
    const unsub = window.miqi.wsl.onInstallProgress((data: WslInstallProgress) => {
      setInstallPhase(data.phase);
      setInstallMessage(data.message);
      if (data.rebootRequired) setInstallRebootRequired(true);
      if (data.error) setInstallError(data.error);
    });

    const unsub2 = window.miqi.wsl.onCheckUpdated(() => {
      fetchDistros();
    });

    return () => {
      unsub();
      unsub2();
    };
  }, [fetchDistros]);

  useEffect(() => {
    fetchDistros();
  }, [fetchDistros]);
  useEffect(() => {
    if (selected) {
      setStats(null);
      fetchStats();
    }
  }, [selected]); // eslint-disable-line

  useEffect(() => {
    if (!selected) return;
    timerRef.current = setInterval(() => fetchStats(true), REFRESH_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [selected, fetchStats]);

  const mem = stats?.memory;
  const cpu = stats?.cpu;
  const dsk = stats?.disk;

  // ── Render install progress ────────────────────────────────────────
  const renderInstallProgress = () => {
    if (!installPhase && !installing) return null;

    const currentIdx = installPhase ? (PHASE_INDEX[installPhase] ?? -1) : -1;

    return (
      <div className="mx-6 mt-4 p-4 rounded-xl border bg-[var(--surface)] border-[var(--border-subtle)]">
        {/* Progress steps */}
        <div className="flex items-center justify-between mb-3">
          {INSTALL_STEPS.map((step, i) => {
            const isComplete = installPhase === 'complete' || currentIdx > i;
            const isCurrent = currentIdx === i;
            const isError = installPhase === 'error' && currentIdx === i;

            return (
              <div key={step.phase} className="flex flex-col items-center gap-1">
                <div
                  className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center text-xs',
                    isComplete && 'bg-[var(--accent)] text-white',
                    isCurrent &&
                      !isError &&
                      'bg-[var(--accent)]/20 text-[var(--accent)] ring-2 ring-[var(--accent)]/40',
                    isError &&
                      'bg-[var(--danger)]/20 text-[var(--danger)] ring-2 ring-[var(--danger)]/40',
                    !isComplete &&
                      !isCurrent &&
                      !isError &&
                      'bg-[var(--surface-muted)] text-[var(--text-faint)]'
                  )}
                >
                  {isComplete ? (
                    <CheckCircle2 size={14} />
                  ) : isCurrent && !isError ? (
                    <RefreshCw size={14} className="animate-spin" />
                  ) : isError ? (
                    <TriangleAlert size={14} />
                  ) : (
                    <Circle size={10} />
                  )}
                </div>
                <span className="text-size-2xs text-[var(--text-muted)]">{t(step.labelKey)}</span>
              </div>
            );
          })}
        </div>

        {/* Connecting lines between steps */}
        <div className="relative h-1 bg-[var(--surface-muted)] rounded-full mb-3 mx-4 -mt-1">
          {currentIdx >= 0 && (
            <div
              className="absolute inset-y-0 left-0 bg-[var(--accent)] rounded-full transition-all duration-500"
              style={{
                width: `${Math.min((currentIdx / (INSTALL_STEPS.length - 1)) * 100, 100)}%`,
              }}
            />
          )}
        </div>

        {/* Message */}
        <div className="flex items-center gap-2">
          {installing && installPhase !== 'complete' && installPhase !== 'error' && (
            <RefreshCw size={14} className="animate-spin text-[var(--accent)] shrink-0" />
          )}
          {installPhase === 'complete' && (
            <CheckCircle2 size={14} className="text-[var(--accent)] shrink-0" />
          )}
          {installPhase === 'error' && (
            <TriangleAlert size={14} className="text-[var(--danger)] shrink-0" />
          )}
          <span
            className={cn(
              'text-xs',
              installPhase === 'error' ? 'text-[var(--danger)]' : 'text-[var(--text-muted)]'
            )}
          >
            {installMessage}
          </span>
        </div>

        {/* Reboot notice */}
        {installRebootRequired && (
          <div className="mt-3 p-2.5 rounded-lg bg-[var(--warning)]/10 border border-[var(--warning)]/25">
            <p className="text-xs text-[var(--text)] flex items-center gap-1.5">
              <TriangleAlert size={12} className="text-[var(--warning)]" />
              {t('wsl.rebootNeeded')}
            </p>
            {installNextStep && (
              <p className="text-xs text-[var(--text-muted)] mt-1">{installNextStep}</p>
            )}
          </div>
        )}

        {/* Error with next step */}
        {installPhase === 'error' && installNextStep && (
          <div className="mt-3 p-2.5 rounded-lg bg-[var(--surface-muted)]">
            <p className="text-xs text-[var(--text-muted)]">{installNextStep}</p>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full bg-[var(--background)] overflow-y-auto">
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0">
        <div>
          <h1 className="text-base font-semibold text-[var(--text)] flex items-center gap-2">
            <Cpu size={16} /> {t('wsl.title')}
          </h1>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">
            {stats
              ? t('wsl.headerUptime', { distro: stats.distro, uptime: fmtUptime(stats.uptime_sec) })
              : t('wsl.subtitleEmpty')}
          </p>
        </div>

        <div className="flex items-center gap-3">
          {distros.length === 0 ? (
            <>
              {/* Install button when no distros available */}
              <button
                onClick={handleInstall}
                disabled={installing}
                className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-[var(--accent)] text-white hover:bg-[var(--accent)]/85 disabled:opacity-50 transition-colors"
              >
                {installing ? (
                  <RefreshCw size={12} className="animate-spin" />
                ) : (
                  <Download size={12} />
                )}
                {t('wsl.installBtn')}
              </button>
              <button
                onClick={() => fetchDistros()}
                disabled={installing}
                className="p-2 rounded-lg bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] transition-colors disabled:opacity-40"
                title={t('wsl.recheck')}
              >
                <RefreshCw size={14} />
              </button>
            </>
          ) : distros.length > 1 ? (
            <select
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              className="text-xs px-3 py-1.5 rounded-lg bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)]"
            >
              {distros.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          ) : distros.length === 1 ? (
            <span className="text-xs text-[var(--text-muted)]">{distros[0]}</span>
          ) : (
            <span className="text-xs text-[var(--text-faint)]">{t('wsl.noDistro')}</span>
          )}
          {distros.length > 0 && (
            <button
              onClick={() => fetchStats()}
              disabled={fetching || !selected}
              className="p-2 rounded-lg bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] transition-colors disabled:opacity-40"
              title={t('common.refresh')}
            >
              <RefreshCw size={14} className={fetching ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
      </div>

      {/* Install progress */}
      {renderInstallProgress()}

      <div className="flex-1 p-6">
        {error && (
          <div className="flex items-center gap-2 text-sm text-[var(--danger)] bg-[var(--danger)]/10 px-4 py-3 rounded-xl mb-6">
            <TriangleAlert size={14} /> {error}
          </div>
        )}

        {/* Show skeleton/empty state while fetching, never block */}
        {!stats ? (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            {fetching ? (
              <>
                <RefreshCw size={20} className="animate-spin text-text-faint" />
                <p className="text-sm text-[var(--text-muted)]">
                  {t('wsl.fetchingState', { name: selected || 'WSL' })}
                </p>
                <p className="text-xs text-[var(--text-faint)]">{t('wsl.switchHint')}</p>
              </>
            ) : selected ? (
              <>
                <Activity size={20} style={{ color: 'var(--text-faint)' }} />
                <p className="text-sm text-[var(--text-muted)]">{t('wsl.noData')}</p>
                <p className="text-xs text-[var(--text-faint)]">{t('wsl.clickRefresh')}</p>
              </>
            ) : distros.length > 0 ? (
              <>
                <Cpu size={20} style={{ color: 'var(--text-faint)' }} />
                <p className="text-sm text-[var(--text-muted)]">{t('wsl.chooseDistro')}</p>
                <p className="text-xs text-[var(--text-faint)]">{t('wsl.chooseDistroHint')}</p>
              </>
            ) : (
              <>
                <Cpu size={20} style={{ color: 'var(--text-faint)' }} />
                <p className="text-sm text-[var(--text-muted)]">{t('wsl.noDistro')}</p>
                <p className="text-xs text-[var(--text-faint)]">{t('wsl.installHint')}</p>
              </>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-6">
            <div className="flex flex-wrap gap-8 justify-center">
              {mem && (
                <div className="flex flex-col items-center gap-3">
                  <div className="relative">
                    <CircleRing pct={mem.used_pct} size={112} />
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span
                        className={`text-xl font-bold tabular-nums ${pctColorText(mem.used_pct)}`}
                      >
                        {mem.used_pct}%
                      </span>
                      <span className="text-size-2xs text-[var(--text-faint)]">
                        {t('wsl.memShort')}
                      </span>
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-xs text-[var(--text-muted)]">{t('wsl.memUsage')}</div>
                    <div className="text-xs font-mono text-[var(--text)]">
                      {fmtMem(mem.used_mb)} / {fmtMem(mem.total_mb)}
                    </div>
                  </div>
                </div>
              )}
              {cpu && (
                <div className="flex flex-col items-center gap-3">
                  <div className="relative">
                    <CircleRing pct={cpu.usage_pct} size={112} />
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span
                        className={`text-xl font-bold tabular-nums ${pctColorText(cpu.usage_pct)}`}
                      >
                        {cpu.usage_pct}%
                      </span>
                      <span className="text-size-2xs text-[var(--text-faint)]">
                        {t('wsl.cpuShort')}
                      </span>
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-xs text-[var(--text-muted)]">{t('wsl.cpuUsage')}</div>
                    <div className="text-xs font-mono text-[var(--text)]">
                      {t('wsl.cores', { count: cpu.cores })}
                    </div>
                  </div>
                </div>
              )}
              {dsk && (
                <div className="flex flex-col items-center gap-3">
                  <div className="relative">
                    <CircleRing pct={dsk.used_pct} size={112} />
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                      <span
                        className={`text-xl font-bold tabular-nums ${pctColorText(dsk.used_pct)}`}
                      >
                        {dsk.used_pct}%
                      </span>
                      <span className="text-size-2xs text-[var(--text-faint)]">
                        {t('wsl.diskShort')}
                      </span>
                    </div>
                  </div>
                  <div className="text-center">
                    <div className="text-xs text-[var(--text-muted)]">{t('wsl.diskUsage')}</div>
                    <div className="text-xs font-mono text-[var(--text)]">
                      {dsk.used_gb} / {dsk.total_gb} GB
                    </div>
                  </div>
                </div>
              )}
            </div>

            <div className="bg-[var(--surface)] border border-[var(--border-subtle)] rounded-2xl overflow-hidden">
              <div className="px-5 py-3 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)]">
                <span className="text-xs font-semibold text-[var(--text)]">{t('wsl.details')}</span>
              </div>
              <table className="w-full text-sm">
                <tbody>
                  {[
                    [t('wsl.distro'), stats.distro],
                    [
                      t('wsl.status'),
                      <span className="flex items-center gap-1 text-[var(--accent)]">
                        <CheckCircle2 size={11} /> {t('wsl.running')}
                      </span>,
                    ],
                    [t('wsl.uptime'), fmtUptime(stats.uptime_sec)],
                    [t('wsl.cpuCores'), t('wsl.cores', { count: cpu?.cores ?? 0 })],
                    [t('wsl.memTotal'), fmtMem(mem?.total_mb ?? 0)],
                    [t('wsl.memUsed'), `${fmtMem(mem?.used_mb ?? 0)} (${mem?.used_pct ?? 0}%)`],
                    [t('wsl.memFree'), fmtMem(mem?.free_mb ?? 0)],
                    [t('wsl.diskTotal'), `${dsk?.total_gb ?? 0} GB`],
                    [t('wsl.diskUsed'), `${dsk?.used_gb ?? 0} GB (${dsk?.used_pct ?? 0}%)`],
                    [t('wsl.diskFree'), `${dsk?.free_gb ?? 0} GB`],
                  ].map(([k, v], i) => (
                    <tr
                      key={String(k)}
                      className={i % 2 === 0 ? 'bg-[var(--surface)]' : 'bg-[var(--surface-muted)]'}
                    >
                      <td className="px-5 py-2.5 text-xs text-[var(--text-muted)] w-40">{k}</td>
                      <td className="px-5 py-2.5 text-xs text-[var(--text)] font-mono">{v}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
