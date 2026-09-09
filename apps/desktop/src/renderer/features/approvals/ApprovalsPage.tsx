import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Shield,
  Trash2,
  Loader2,
  Plus,
  Check,
  X,
  Pencil,
  ChevronDown,
  ChevronRight,
  History,
  List,
  AlertTriangle,
} from 'lucide-react';
import type { ApprovalsListResult, ApprovalHistoryEntry } from '../../../shared/ipc';
import { Modal } from '../../components/shared';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type ApprovalBypassKey =
  | 'bypassAll'
  | 'bypassCommandApproval'
  | 'bypassFileWriteApproval'
  | 'bypassToolConfirmation'
  | 'bypassNetworkApproval';

interface ApprovalBypassConfig {
  bypassAll: boolean;
  bypassCommandApproval: boolean;
  bypassFileWriteApproval: boolean;
  bypassToolConfirmation: boolean;
  bypassNetworkApproval: boolean;
}

const DEFAULT_APPROVAL_BYPASS: ApprovalBypassConfig = {
  bypassAll: false,
  bypassCommandApproval: false,
  bypassFileWriteApproval: false,
  bypassToolConfirmation: false,
  bypassNetworkApproval: false,
};

function normalizeApprovalBypass(config: Record<string, unknown>): ApprovalBypassConfig {
  const approvals = (config.approvals ?? {}) as Partial<ApprovalBypassConfig>;

  return {
    bypassAll: Boolean(approvals.bypassAll),
    bypassCommandApproval: Boolean(approvals.bypassCommandApproval),
    bypassFileWriteApproval: Boolean(approvals.bypassFileWriteApproval),
    bypassToolConfirmation: Boolean(approvals.bypassToolConfirmation),
    bypassNetworkApproval: Boolean(approvals.bypassNetworkApproval),
  };
}

function ToggleSwitch({
  checked,
  disabled,
  testId,
  tone = 'accent',
}: {
  checked: boolean;
  disabled?: boolean;
  testId: string;
  tone?: 'accent' | 'warning';
}) {
  return (
    <span
      role="switch"
      aria-checked={checked}
      aria-disabled={disabled ? 'true' : 'false'}
      data-testid={testId}
      className="relative shrink-0 rounded-full transition-colors"
      style={{
        width: 42,
        height: 24,
        background:
          checked && tone === 'warning'
            ? 'var(--approval-warning-strong)'
            : checked
              ? `var(--${tone})`
              : 'var(--border)',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <span
        className="absolute top-1 h-4 w-4 rounded-full bg-white transition-transform"
        style={{
          left: checked ? 22 : 4,
        }}
      />
    </span>
  );
}

import { formatAbsoluteTime } from '../../lib/formatTime';
import type { TFunction } from 'i18next';

function decisionLabel(d: string, t: TFunction): { text: string; color: string } {
  switch (d) {
    case 'deny':
      return { text: t('approvals.decideDeny'), color: 'text-[var(--danger)]' };
    case 'once':
      return { text: t('approvals.decideOnce'), color: 'text-[var(--info)]' };
    case 'session':
      return { text: t('approvals.decideSession'), color: 'text-[var(--success)]' };
    case 'always':
      return { text: t('approvals.decideAlways'), color: 'text-[var(--accent)]' };
    default:
      return { text: d, color: 'text-[var(--text-muted)]' };
  }
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ApprovalsPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<ApprovalsListResult | null>(null);
  const [history, setHistory] = useState<ApprovalHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [clearing, setClearing] = useState<string | null>(null);
  const [tab, setTab] = useState<'whitelist' | 'history' | 'pending'>('whitelist');

  // Add dialog
  const [showAdd, setShowAdd] = useState(false);
  const [newPattern, setNewPattern] = useState('');
  const [adding, setAdding] = useState(false);

  // Edit
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [saving, setSaving] = useState(false);

  // Category filter for pending tab
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const CATEGORIES = [
    { key: 'all', label: t('approvals.catAll') },
    { key: 'exec', label: t('approvals.catExec') },
    { key: 'file_write', label: t('approvals.catFile') },
    { key: 'network', label: t('approvals.catNet') },
  ];

  // Global bypass settings
  const [bypassConfig, setBypassConfig] = useState<ApprovalBypassConfig>(DEFAULT_APPROVAL_BYPASS);
  const [bypassLoading, setBypassLoading] = useState(true);
  const [bypassSaving, setBypassSaving] = useState<ApprovalBypassKey | null>(null);
  const [bypassSaved, setBypassSaved] = useState<ApprovalBypassKey | null>(null);
  const [bypassError, setBypassError] = useState<string | null>(null);

  // Auto mode override: when active, all switches appear checked
  const [autoMode, setAutoMode] = useState(() => sessionStorage.getItem('miqi:mode:auto') === '1');
  useEffect(() => {
    const h = () => setAutoMode(sessionStorage.getItem('miqi:mode:auto') === '1');
    window.addEventListener('miqi:mode-changed', h);
    return () => window.removeEventListener('miqi:mode-changed', h);
  }, []);

  // Expand
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [expandedHistory, setExpandedHistory] = useState<Set<string>>(new Set());
  const [expandedPending, setExpandedPending] = useState<Set<string>>(new Set());

  // Countdown timer
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [tick, setTick] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await window.miqi.approvals.list();
      setData(result);
    } catch {
      // runtime not running
    } finally {
      setLoading(false);
    }
  }, []);

  const loadBypassConfig = useCallback(async () => {
    setBypassLoading(true);
    try {
      const config = await window.miqi.config.get();
      const normalized = normalizeApprovalBypass(config);
      setBypassConfig(normalized);
    } catch {
      setBypassConfig(DEFAULT_APPROVAL_BYPASS);
    } finally {
      setBypassLoading(false);
    }
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const r = await window.miqi.approvals.history(200);
      setHistory(r.history ?? []);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    loadBypassConfig();
  }, [loadBypassConfig]);
  useEffect(() => {
    if (tab === 'history') loadHistory();
  }, [tab, loadHistory]);

  // Tick every second for countdown display
  useEffect(() => {
    if (tab !== 'pending') return;
    timerRef.current = setInterval(() => setTick((t) => t + 1), 1000);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [tab]);

  // Reload on tab switch
  useEffect(() => {
    if (tab === 'pending') load();
  }, [tab, load]);

  const clearOne = async (pattern: string) => {
    setClearing(pattern);
    try {
      await window.miqi.approvals.clearPermanent(pattern);
      await load();
    } finally {
      setClearing(null);
    }
  };

  const clearAll = async () => {
    setClearing('all');
    try {
      await window.miqi.approvals.clearPermanent();
      await load();
    } finally {
      setClearing(null);
    }
  };

  const handleAdd = async () => {
    if (!newPattern.trim()) return;
    setAdding(true);
    try {
      await window.miqi.approvals.addPermanent(newPattern.trim());
      setNewPattern('');
      setShowAdd(false);
      await load();
    } catch {
      /* ignore */
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (pattern: string) => {
    setEditing(pattern);
    setEditValue(pattern);
  };

  const handleEditSave = async () => {
    if (!editing || !editValue.trim() || editValue.trim() === editing) {
      setEditing(null);
      return;
    }
    setSaving(true);
    try {
      await window.miqi.approvals.addPermanent(editValue.trim());
      await window.miqi.approvals.clearPermanent(editing);
      await load();
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
      setEditing(null);
    }
  };

  const updateBypassConfig = async (key: ApprovalBypassKey, enabled: boolean) => {
    if (bypassSaving !== null) return;
    const previous = bypassConfig;
    const next =
      key === 'bypassAll'
        ? { ...DEFAULT_APPROVAL_BYPASS, bypassAll: enabled }
        : (() => {
            const updated = { ...bypassConfig, [key]: enabled };
            // If all 4 individual bypasses are now ON, auto-check bypassAll
            if (
              updated.bypassCommandApproval &&
              updated.bypassFileWriteApproval &&
              updated.bypassToolConfirmation &&
              updated.bypassNetworkApproval
            ) {
              updated.bypassAll = true;
            } else {
              updated.bypassAll = false;
            }
            return updated;
          })();
    setBypassSaving(key);
    setBypassError(null);
    setBypassConfig(next);
    try {
      const update: Record<string, unknown> = { approvals: next };
      if ((key === 'bypassAll' && !enabled) || (key === 'bypassCommandApproval' && !enabled)) {
        update.agents = { commandApproval: { enabled: true } };
      }
      await window.miqi.config.update(update);
      setBypassSaved(key);
      window.dispatchEvent(new Event('miqi:approval-bypass-updated'));
      window.setTimeout(
        () => setBypassSaved((current) => (current === key ? null : current)),
        1800
      );
    } catch (e) {
      console.error('Failed to save approval bypass config:', e);
      setBypassConfig(previous);
      setBypassError(e instanceof Error ? e.message : t('approvals.saveFail'));
    } finally {
      setBypassSaving(null);
    }
  };

  const bypassRows: Array<{
    key: ApprovalBypassKey;
    label: string;
    description: string;
  }> = [
    {
      key: 'bypassCommandApproval',
      label: t('approvals.bpCmd'),
      description: t('approvals.bpCmdDesc'),
    },
    {
      key: 'bypassFileWriteApproval',
      label: t('approvals.bpFile'),
      description: t('approvals.bpFileDesc'),
    },
    {
      key: 'bypassToolConfirmation',
      label: t('approvals.bpTool'),
      description: t('approvals.bpToolDesc'),
    },
    {
      key: 'bypassNetworkApproval',
      label: t('approvals.bpNet'),
      description: t('approvals.bpNetDesc'),
    },
  ];

  const isBypassOn = (key: ApprovalBypassKey): boolean => {
    if (key === 'bypassAll') return bypassConfig.bypassAll;
    if (bypassConfig.bypassAll) return true;
    return bypassConfig[key];
  };

  const toggleExpand = (set: Set<string>, key: string, setFn: (s: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setFn(next);
  };

  const tabs = [
    { key: 'whitelist' as const, label: t('approvals.tabWhitelist'), icon: Shield },
    { key: 'history' as const, label: t('approvals.tabHistory'), icon: History },
    { key: 'pending' as const, label: t('approvals.tabPending'), icon: List },
  ];

  return (
    <div className="flex flex-col h-full bg-[var(--background)]">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0">
        <div>
          <h1 className="text-base font-semibold text-[var(--text)]">{t('approvals.title')}</h1>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">{t('approvals.subtitle')}</p>
        </div>
        <button
          onClick={load}
          className="text-xs text-[var(--text-faint)] hover:text-[var(--text-muted)] transition-colors px-2 py-1 rounded"
        >
          {t('common.refresh')}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-0 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0 px-4">
        {tabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`settings-hover-tab flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 -mb-px ${
                tab === t.key
                  ? 'border-[var(--accent)] text-[var(--accent)]'
                  : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]'
              }`}
            >
              <Icon size={13} />
              {t.label}
              {t.key === 'pending' && (data?.pending?.length ?? 0) > 0 && (
                <span className="ml-0.5 bg-[var(--danger)] text-white text-size-2xs rounded-full px-1.5 py-0.5 leading-none">
                  {data?.pending?.length}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 py-2">
        <div
          className={`border rounded-lg mb-2 overflow-hidden ${
            bypassConfig.bypassAll
              ? 'border-[var(--approval-warning-border)] bg-[var(--approval-warning-bg)]'
              : 'border-[var(--border-subtle)] bg-[var(--surface)]'
          }`}
        >
          <div className="flex items-center justify-between gap-4 px-4 py-2 border-b border-[var(--border-subtle)]">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <AlertTriangle
                  size={14}
                  className={
                    bypassConfig.bypassAll
                      ? 'text-[var(--approval-warning)]'
                      : 'text-[var(--text-faint)]'
                  }
                />
                <h2 className="text-sm font-semibold text-[var(--text)]">
                  {t('approvals.bypassTitle')}
                </h2>
                {bypassSaved === 'bypassAll' && (
                  <span className="text-size-2xs text-[var(--success)]">
                    {t('approvals.saved')}
                  </span>
                )}
              </div>
              <p className="text-size-2xs text-[var(--text-muted)] mt-0.5 leading-tight">
                {t('approvals.bypassHint')}
              </p>
            </div>
            <button
              type="button"
              onClick={() => updateBypassConfig('bypassAll', !bypassConfig.bypassAll)}
              disabled={bypassLoading || autoMode}
              className="flex items-center gap-3 shrink-0 cursor-pointer disabled:cursor-not-allowed"
            >
              <span className="text-xs font-medium text-[var(--text-muted)]">
                {t('approvals.bypassAll')}
              </span>
              <ToggleSwitch
                checked={autoMode || bypassConfig.bypassAll}
                disabled={bypassLoading || autoMode}
                testId="approval-bypass-all-toggle"
                tone="warning"
              />
            </button>
          </div>
          {bypassError && (
            <div className="px-5 py-2 text-xs text-[var(--danger)] border-b border-[var(--border-subtle)]">
              {bypassError}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 divide-y md:divide-y-0 md:divide-x divide-[var(--border-subtle)]">
            {bypassRows.map((row) => {
              const disabled = bypassLoading || bypassConfig.bypassAll || autoMode;
              const checked = autoMode || isBypassOn(row.key);
              const storedChecked = bypassConfig[row.key];
              const nextStored = !storedChecked;
              return (
                <button
                  type="button"
                  key={row.key}
                  onClick={() => updateBypassConfig(row.key, nextStored)}
                  disabled={disabled}
                  className={`flex items-center justify-between gap-3 px-4 py-1.5 ${
                    disabled && !bypassLoading
                      ? 'opacity-75 cursor-not-allowed'
                      : 'cursor-pointer hover:bg-[rgba(194,65,12,0.08)]'
                  }`}
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2 text-xs font-medium text-[var(--text)]">
                      {row.label}
                      {bypassSaved === row.key && (
                        <span className="text-size-2xs text-[var(--success)]">
                          {t('approvals.saved')}
                        </span>
                      )}
                    </span>
                    <span className="block text-size-2xs text-[var(--text-muted)] mt-0.5">
                      {row.description}
                      {bypassConfig.bypassAll ? t('approvals.bypassControlled') : ''}
                    </span>
                  </span>
                  <ToggleSwitch
                    checked={checked}
                    disabled={disabled}
                    testId={`approval-${row.key}-toggle`}
                    tone="warning"
                  />
                </button>
              );
            })}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-40 text-sm text-[var(--text-faint)]">
            <Loader2 size={16} className="animate-spin mr-2" /> {t('statusBar.loading')}
          </div>
        ) : !data ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2 text-sm text-[var(--text-faint)]">
            <Shield size={24} />
            <span>{t('channels.runtimeNotStarted')}</span>
          </div>
        ) : (
          <>
            {/* Status bar (always shown) */}
            <div className="settings-hover-card bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl px-5 py-2 flex items-center gap-4 text-sm mb-3">
              <div className="flex items-center gap-2">
                <Shield
                  size={14}
                  className={data.enabled ? 'text-[var(--success)]' : 'text-[var(--text-faint)]'}
                />
                <span className="text-[var(--text-muted)]">{t('approvals.system')}</span>
                <span
                  className={data.enabled ? 'text-[var(--success)]' : 'text-[var(--text-faint)]'}
                >
                  {data.enabled ? t('approvals.enabled') : t('approvals.disabled')}
                </span>
              </div>
              <div className="text-[var(--text-faint)]">·</div>
              <span className="text-[var(--text-muted)]">
                {t('approvals.timeout', { count: data.timeout ?? 60 })}
              </span>
              <div className="text-[var(--text-faint)]">·</div>
              <span className="text-[var(--text-muted)]">
                {t('approvals.pendingCount', { count: data.pending?.length ?? 0 })}
              </span>
            </div>

            {/* ── TAB: Whitelist ──────────────────────────────────────── */}
            {tab === 'whitelist' && (
              <div className="settings-hover-card bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
                <div className="flex items-center justify-between px-5 py-2 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)]">
                  <span className="text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                    {t('approvals.whitelistTitle', {
                      count: data.permanent_entries?.length ?? 0,
                    })}
                  </span>
                  <div className="flex items-center gap-2">
                    {data.permanent_entries && data.permanent_entries.length > 0 && (
                      <button
                        onClick={clearAll}
                        disabled={clearing === 'all'}
                        className="text-xs text-[var(--danger)] hover:underline disabled:opacity-50"
                      >
                        {clearing === 'all' ? t('approvals.clearingAll') : t('approvals.clearAll')}
                      </button>
                    )}
                    <button
                      onClick={() => setShowAdd(true)}
                      className="flex items-center gap-1 text-xs text-[var(--text-muted)] hover:text-[var(--text)] hover:underline"
                    >
                      <Plus size={12} /> {t('approvals.add')}
                    </button>
                  </div>
                </div>
                {!data.permanent_entries || data.permanent_entries.length === 0 ? (
                  <div className="px-5 py-8 text-sm text-[var(--text-faint)] text-center">
                    {t('approvals.noWhitelist')}
                  </div>
                ) : (
                  <div className="divide-y divide-[var(--border-subtle)]">
                    {data.permanent_entries.map((entry, i) => {
                      const isExpanded = expanded.has(entry.pattern);
                      const isEditing = editing === entry.pattern;
                      return (
                        <div key={entry.pattern}>
                          <div className="flex items-center gap-3 px-5 py-2.5 hover:bg-[var(--surface-muted)] transition-colors">
                            <button
                              onClick={() => toggleExpand(expanded, entry.pattern, setExpanded)}
                              className="text-[var(--text-faint)] hover:text-[var(--text-muted)] shrink-0"
                            >
                              {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                            </button>
                            {isEditing ? (
                              <div className="flex-1 flex items-center gap-2">
                                <input
                                  value={editValue}
                                  onChange={(e) => setEditValue(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter') handleEditSave();
                                    if (e.key === 'Escape') setEditing(null);
                                  }}
                                  className="flex-1 text-xs font-mono bg-[var(--surface-elevated)] border border-[var(--border)] rounded px-2 py-1 focus:outline-none focus:border-[var(--border-strong)]"
                                  autoFocus
                                />
                                <button
                                  onClick={handleEditSave}
                                  disabled={saving}
                                  className="p-1 rounded text-[var(--success)] hover:bg-green-50"
                                >
                                  {saving ? (
                                    <Loader2 size={12} className="animate-spin" />
                                  ) : (
                                    <Check size={12} />
                                  )}
                                </button>
                                <button
                                  onClick={() => setEditing(null)}
                                  className="p-1 rounded text-[var(--text-faint)] hover:text-[var(--text)]"
                                >
                                  <X size={12} />
                                </button>
                              </div>
                            ) : (
                              <code className="flex-1 text-xs font-mono text-[var(--text)] truncate">
                                {entry.pattern}
                              </code>
                            )}
                            {!isEditing && (
                              <div className="flex items-center gap-1 shrink-0">
                                <button
                                  onClick={() => startEdit(entry.pattern)}
                                  className="p-1 rounded text-[var(--text-faint)] hover:text-[var(--info)] transition-colors"
                                  title={t('approvals.edit')}
                                >
                                  <Pencil size={12} />
                                </button>
                                <button
                                  onClick={() => clearOne(entry.pattern)}
                                  disabled={clearing === entry.pattern}
                                  title={t('approvals.removeFromWhitelist')}
                                  className="p-1 rounded text-[var(--text-faint)] hover:text-[var(--danger)] transition-colors disabled:opacity-50"
                                >
                                  {clearing === entry.pattern ? (
                                    <Loader2 size={12} className="animate-spin" />
                                  ) : (
                                    <Trash2 size={12} />
                                  )}
                                </button>
                              </div>
                            )}
                          </div>
                          {isExpanded && !isEditing && (
                            <div className="px-10 py-2.5 bg-[var(--surface-muted)] text-xs text-[var(--text-muted)] space-y-1">
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.commandMode')}
                                </span>
                                <code className="font-mono text-[var(--text)] break-all">
                                  {entry.pattern}
                                </code>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.addedAt')}
                                </span>
                                <span>
                                  {entry.added_at
                                    ? formatAbsoluteTime(entry.added_at * 1000)
                                    : t('approvals.unknown')}
                                </span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* ── TAB: History ────────────────────────────────────────── */}
            {tab === 'history' && (
              <div className="settings-hover-card bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
                <div className="px-5 py-2 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)]">
                  <span className="text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                    {t('approvals.historyTitle', { count: history.length })}
                  </span>
                </div>
                {history.length === 0 ? (
                  <div className="px-5 py-8 text-sm text-[var(--text-faint)] text-center">
                    {t('approvals.noHistory')}
                  </div>
                ) : (
                  <div className="divide-y divide-[var(--border-subtle)]">
                    {history.map((h) => {
                      const d = decisionLabel(h.decision, t);
                      const isExpanded = expandedHistory.has(h.id);
                      return (
                        <div key={h.id}>
                          <div
                            className="flex items-center gap-3 px-5 py-2.5 hover:bg-[var(--surface-muted)] transition-colors cursor-pointer"
                            onClick={() => toggleExpand(expandedHistory, h.id, setExpandedHistory)}
                          >
                            {isExpanded ? (
                              <ChevronDown size={12} className="text-[var(--text-faint)]" />
                            ) : (
                              <ChevronRight size={12} className="text-[var(--text-faint)]" />
                            )}
                            <span className={`text-xs font-medium shrink-0 ${d.color}`}>
                              {d.text}
                            </span>
                            <code className="flex-1 text-xs font-mono text-[var(--text-muted)] truncate">
                              {h.description}
                            </code>
                            <span className="text-size-2xs text-[var(--text-faint)] shrink-0">
                              {formatAbsoluteTime(h.timestamp * 1000)}
                            </span>
                          </div>
                          {isExpanded && (
                            <div className="px-10 py-2.5 bg-[var(--surface-muted)] text-xs text-[var(--text-muted)] space-y-1">
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.decision')}
                                </span>
                                <span className={d.color}>{d.text}</span>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.rulePattern')}
                                </span>
                                <code className="font-mono text-[var(--text)] break-all">
                                  {h.pattern_key}
                                </code>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.command')}
                                </span>
                                <code className="font-mono text-[var(--text)] break-all">
                                  {h.command}
                                </code>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.session')}
                                </span>
                                <span className="font-mono">{h.session_key || '-'}</span>
                              </div>
                              <div className="flex gap-2">
                                <span className="text-[var(--text-faint)] shrink-0">
                                  {t('approvals.time')}
                                </span>
                                <span>{formatAbsoluteTime(h.timestamp * 1000)}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* ── TAB: Pending ────────────────────────────────────────── */}
            {tab === 'pending' &&
              (() => {
                const filtered = (data.pending || []).filter(
                  (p) => categoryFilter === 'all' || p.category === categoryFilter
                );
                return (
                  <div
                    data-tick={tick}
                    className="settings-hover-card bg-[var(--surface)] border border-[var(--border-subtle)] rounded-xl overflow-hidden"
                  >
                    <div className="px-5 py-2 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)] flex items-center justify-between">
                      <span className="text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                        {t('approvals.pendingTitle', { count: filtered.length })}
                      </span>
                      <div className="flex gap-1">
                        {CATEGORIES.map((c) => (
                          <button
                            key={c.key}
                            onClick={() => setCategoryFilter(c.key)}
                            className={`px-2 py-0.5 text-size-2xs rounded-full transition-colors ${
                              categoryFilter === c.key
                                ? 'bg-[var(--accent)] text-white'
                                : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'
                            }`}
                          >
                            {c.label}
                          </button>
                        ))}
                      </div>
                    </div>
                    {filtered.length === 0 ? (
                      <div className="px-5 py-8 text-sm text-[var(--text-faint)] text-center">
                        {t('approvals.noPending')}
                      </div>
                    ) : (
                      <div className="divide-y divide-[var(--border-subtle)]">
                        {filtered.map((p) => {
                          const isExpanded = expandedPending.has(p.approval_id);
                          const remaining = Math.max(
                            0,
                            Math.ceil((data.timeout ?? 60) - (Date.now() / 1000 - p.created_at))
                          );
                          const pct = (remaining / (data.timeout || 60)) * 100;
                          const isLow = remaining <= 5;
                          return (
                            <div key={p.approval_id}>
                              <div
                                className="flex items-center gap-3 px-5 py-2.5 hover:bg-[var(--surface-muted)] transition-colors cursor-pointer"
                                onClick={() =>
                                  toggleExpand(expandedPending, p.approval_id, setExpandedPending)
                                }
                              >
                                {isExpanded ? (
                                  <ChevronDown size={12} className="text-[var(--text-faint)]" />
                                ) : (
                                  <ChevronRight size={12} className="text-[var(--text-faint)]" />
                                )}
                                <AlertTriangle
                                  size={12}
                                  className="text-[var(--warning)] shrink-0"
                                />
                                <code className="flex-1 text-xs font-mono text-[var(--text-muted)] truncate">
                                  {p.description}
                                </code>
                                {/* Countdown bar */}
                                <div className="flex items-center gap-2 shrink-0">
                                  <div className="w-16 h-1.5 bg-[var(--surface-muted)] rounded-full overflow-hidden">
                                    <div
                                      className={`h-full rounded-full transition-all ${
                                        isLow ? 'bg-[var(--danger)]' : 'bg-[var(--warning)]'
                                      }`}
                                      style={{
                                        width: `${Math.max(0, Math.min(100, pct))}%`,
                                      }}
                                    />
                                  </div>
                                  <span
                                    className={`text-size-2xs font-mono tabular-nums w-8 text-right ${isLow ? 'text-[var(--danger)] font-semibold' : 'text-[var(--text-faint)]'}`}
                                  >
                                    {remaining}s
                                  </span>
                                </div>
                              </div>
                              {isExpanded && (
                                <div className="px-10 py-2.5 bg-[var(--surface-muted)] text-xs text-[var(--text-muted)] space-y-1">
                                  <div className="flex gap-2">
                                    <span className="text-[var(--text-faint)] shrink-0">
                                      {t('approvals.approvalId')}
                                    </span>
                                    <span className="font-mono">{p.approval_id}</span>
                                  </div>
                                  <div className="flex gap-2">
                                    <span className="text-[var(--text-faint)] shrink-0">
                                      {t('approvals.description')}
                                    </span>
                                    <span>{p.description}</span>
                                  </div>
                                  <div className="flex gap-2">
                                    <span className="text-[var(--text-faint)] shrink-0">
                                      {t('approvals.command')}
                                    </span>
                                    <code className="font-mono text-[var(--text)] break-all">
                                      {p.command}
                                    </code>
                                  </div>
                                  <div className="flex gap-2">
                                    <span className="text-[var(--text-faint)] shrink-0">
                                      {t('approvals.timeoutCountdown')}
                                    </span>
                                    <span
                                      className={`font-mono tabular-nums ${isLow ? 'text-[var(--danger)] font-semibold' : ''}`}
                                    >
                                      {remaining > 0
                                        ? t('approvals.seconds', { count: remaining })
                                        : t('approvals.expired')}
                                    </span>
                                    <span className="text-[var(--text-faint)]">
                                      {t('approvals.secondsTotal', { count: data.timeout ?? 60 })}
                                    </span>
                                  </div>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })()}
          </>
        )}
      </div>

      {/* ── Add Dialog ────────────────────────────────────────────────── */}
      {showAdd && (
        <Modal
          open={showAdd}
          onOpenChange={(o) => {
            if (!o) setShowAdd(false);
          }}
          hideClose
        >
          <div
            className="bg-[var(--surface-elevated)] border border-[var(--border)] rounded-xl shadow-xl w-full max-w-[420px] mx-4 overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="px-5 py-3 border-b border-[var(--border-subtle)]">
              <h3 className="text-sm font-semibold text-[var(--text)]">
                {t('approvals.addTitle')}
              </h3>
              <p className="text-xs text-[var(--text-muted)] mt-0.5">{t('approvals.addHint')}</p>
            </div>
            <div className="px-5 py-3">
              <input
                type="text"
                value={newPattern}
                onChange={(e) => setNewPattern(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAdd();
                  if (e.key === 'Escape') setShowAdd(false);
                }}
                placeholder={t('approvals.addPlaceholder')}
                className="w-full text-xs font-mono bg-[var(--surface)] border border-[var(--border)] rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--border-strong)]"
                autoFocus
              />
            </div>
            <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[var(--border-subtle)]">
              <button
                onClick={() => setShowAdd(false)}
                className="px-3 py-1.5 rounded-lg text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleAdd}
                disabled={adding || !newPattern.trim()}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50"
              >
                {adding ? t('approvals.adding') : t('approvals.addAction')}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
