import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '../../components/ui/Button';
import { ScrollArea } from '../../components/ui/ScrollArea';
import { ContextMenu } from '../../components/ContextMenu';
import { cn } from '../../lib/utils';
import { formatAbsoluteTime } from '../../lib/formatTime';
import {
  MessageSquare,
  Trash2,
  RefreshCw,
  Loader2,
  Clock,
  Bot,
  ShieldAlert,
  KeyRound,
} from 'lucide-react';
import type { SessionInfo, SessionDetail, LiveAgentInfo } from '../../../shared/ipc';

/** Structured error info extracted from a rejected bridge call. */
export interface SessionLoadError {
  code: string;
  message: string;
  /** 'requires_claim' | 'unauthorized' | 'generic' */
  kind: 'requires_claim' | 'unauthorized' | 'generic';
}

export function classifySessionError(e: unknown): SessionLoadError {
  const msg = (e as any)?.message ?? String(e ?? '');
  const code = (e as any)?.code ?? '';
  const combined = `${code} ${msg}`.toLowerCase();

  if (combined.includes('requires_claim') || combined.includes('unowned')) {
    return { kind: 'requires_claim', code, message: msg };
  }
  if (combined.includes('unauthorized') || combined.includes('not authorized')) {
    return { kind: 'unauthorized', code, message: msg };
  }
  return { kind: 'generic', code, message: msg };
}

function agentStatusColor(status: string): string {
  switch (status) {
    case 'running':
    case 'thinking':
      return 'bg-blue-500 animate-pulse';
    case 'tool_running':
      return 'bg-amber-500 animate-pulse';
    case 'idle':
      return 'bg-emerald-500';
    case 'error':
    case 'failed':
      return 'bg-red-500';
    default:
      return 'bg-gray-400';
  }
}

export function SessionExplorer({
  onOpenSession,
  refreshKey,
}: {
  onOpenSession: (key: string) => void;
  refreshKey?: number;
}) {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<SessionLoadError | null>(null);
  const [claiming, setClaiming] = useState(false);
  // Agent status polling (Phase 7.8)
  const [agents, setAgents] = useState<LiveAgentInfo[]>([]);

  useEffect(() => {
    const load = async () => {
      try {
        const r = await window.miqi.agents.list();
        setAgents(r?.agents || []);
      } catch {
        /* bridge not ready */
      }
    };
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const r = await window.miqi.sessions.list();
      setSessions(r?.sessions ?? []);
    } catch {
      // Bridge not available
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadSessions();
  }, [loadSessions, refreshKey]);

  // Re-load when bridge becomes running (app startup race condition)
  useEffect(() => {
    const unsub = window.miqi.runtime.onStateChange((status) => {
      if (status.state === 'running') loadSessions();
    });
    return () => {
      unsub();
    };
  }, [loadSessions]);

  const loadDetail = async (key: string) => {
    setSelected(key);
    setDetailLoading(true);
    setDetailError(null);
    try {
      const d = await window.miqi.sessions.get(key);
      setDetail(d);
      setDetailError(null);
    } catch (e: unknown) {
      setDetail(null);
      setDetailError(classifySessionError(e));
    }
    setDetailLoading(false);
  };

  const handleClaim = async (key: string) => {
    setClaiming(true);
    try {
      await window.miqi.sessions.claimLegacy(key);
      // Reload after successful claim
      await loadDetail(key);
    } catch {
      setDetailError({ kind: 'generic', code: '', message: 'Claim failed. Check runtime logs.' });
    } finally {
      setClaiming(false);
    }
  };

  const handleDelete = async (key: string) => {
    await window.miqi.sessions.delete(key);
    if (selected === key) {
      setSelected(null);
      setDetail(null);
    }
    loadSessions();
  };

  return (
    <div className="flex h-full">
      {/* Session list */}
      <div className="w-[320px] shrink-0 border-r border-[var(--border-subtle)] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-subtle)]">
          <h2 className="text-sm font-semibold text-[var(--text)]">Sessions</h2>
          <Button variant="ghost" size="icon" onClick={loadSessions} disabled={loading}>
            <RefreshCw size={14} className={cn(loading && 'animate-spin')} />
          </Button>
        </div>

        <ScrollArea className="flex-1">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 size={16} className="animate-spin text-[var(--text-muted)]" />
            </div>
          ) : sessions.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center px-4">
              <MessageSquare size={24} className="text-[var(--text-faint)]" />
              <p className="text-xs text-[var(--text-muted)]">No sessions yet</p>
              <p className="text-xs text-[var(--text-faint)]">Start a chat to create one</p>
            </div>
          ) : (
            <div className="flex flex-col">
              {sessions.map((s) => (
                <ContextMenu
                  key={s.key}
                  items={[
                    {
                      label: t('sessionExplorer.open'),
                      onSelect: () => {
                        loadDetail(s.key);
                        onOpenSession?.(s.key);
                      },
                    },
                    {
                      label: t('sessionExplorer.copyKey'),
                      onSelect: () => navigator.clipboard.writeText(s.key),
                    },
                    {
                      label: t('sessionExplorer.delete'),
                      danger: true,
                      divider: true,
                      onSelect: () => handleDelete(s.key),
                    },
                  ]}
                >
                  {({ onContextMenu }) => (
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => loadDetail(s.key)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          loadDetail(s.key);
                        }
                      }}
                      onContextMenu={onContextMenu}
                      className={cn(
                        'flex items-start gap-3 px-4 py-3 text-left transition-colors border-b border-[var(--border-subtle)] w-full cursor-pointer',
                        selected === s.key
                          ? 'bg-[var(--accent-soft)]/50'
                          : 'hover:bg-[var(--surface-muted)]'
                      )}
                    >
                      <MessageSquare
                        size={16}
                        className="text-[var(--text-muted)] shrink-0 mt-0.5"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-[var(--text)] truncate flex items-center gap-1.5">
                          {s.key}
                          {(() => {
                            const agent = agents.find(
                              (a) =>
                                a.thread_id === s.key ||
                                a.thread_id.endsWith(':' + s.key) ||
                                s.key.includes(a.agent_id)
                            );
                            if (!agent) return null;
                            return (
                              <span
                                className={cn('flex items-center gap-1 shrink-0')}
                                title={`${agent.type}: ${agent.status}`}
                              >
                                <Bot size={11} className="text-[var(--text-muted)] icon-mono" />
                                <span className="icon-color text-xs leading-none">🤖</span>
                                <span
                                  className={`w-2 h-2 rounded-full ${agentStatusColor(agent.status)}`}
                                />
                              </span>
                            );
                          })()}
                        </div>
                        {s.updated_at && (
                          <div className="flex items-center gap-1 text-xs text-[var(--text-faint)] mt-0.5">
                            <Clock size={10} />
                            {formatAbsoluteTime(s.updated_at)}
                          </div>
                        )}
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(s.key);
                        }}
                        className="text-[var(--text-faint)] hover:text-[var(--danger)] transition-colors shrink-0"
                        tabIndex={-1}
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                  )}
                </ContextMenu>
              ))}
            </div>
          )}
        </ScrollArea>
      </div>

      {/* Session detail */}
      <div className="flex-1 flex flex-col">
        {!selected ? (
          <div className="flex items-center justify-center h-full text-xs text-[var(--text-muted)]">
            Select a session to view messages
          </div>
        ) : detailLoading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 size={16} className="animate-spin text-[var(--text-muted)]" />
          </div>
        ) : detail ? (
          <ScrollArea className="flex-1">
            <div className="px-6 py-4 flex flex-col gap-3">
              <div className="text-xs text-[var(--text-faint)] mb-2">
                Session: {detail.key} • Messages: {detail.messages?.length ?? 0}
              </div>
              {(detail.messages ?? []).map((msg, i) => {
                const role = String(msg.role ?? '');
                const content = String(msg.content ?? '');
                const isUser = role === 'user';
                const isTool = role === 'tool';
                return (
                  <div
                    key={i}
                    className={cn(
                      'text-sm rounded-lg px-3 py-2 max-w-[80%]',
                      isUser
                        ? 'bg-[var(--accent-soft)] text-[var(--text)] self-end'
                        : isTool
                          ? 'bg-[var(--surface-muted)] text-[var(--text-muted)] text-xs self-start italic'
                          : 'bg-[var(--surface)] border border-[var(--border-subtle)] text-[var(--text)] self-start'
                    )}
                  >
                    {isTool ? (
                      <span className="text-[var(--text-faint)]">
                        tool: {String(msg.name ?? 'result')}
                      </span>
                    ) : null}
                    <div className={cn(isTool && 'mt-1')}>
                      {isUser || isTool ? (
                        <>
                          {content.slice(0, 500)}
                          {content.length > 500 && '...'}
                        </>
                      ) : (
                        <div className="prose prose-sm max-w-none text-[var(--text)]">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {content.slice(0, 1000) + (content.length > 1000 ? '\n\n...' : '')}
                          </ReactMarkdown>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </ScrollArea>
        ) : detailError ? (
          <div className="flex flex-col items-center justify-center h-full px-6 text-center gap-3">
            {detailError.kind === 'requires_claim' ? (
              <>
                <KeyRound size={28} className="text-[var(--warning)]" />
                <div>
                  <p className="text-sm font-medium text-[var(--text)] mb-1">
                    {t('sessionExplorer.claimTitle')}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {t('sessionExplorer.claimBody')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={claiming}
                  onClick={() => handleClaim(selected)}
                  className="mt-1"
                >
                  {claiming ? t('sessionExplorer.claiming') : t('sessionExplorer.claim')}
                </Button>
              </>
            ) : detailError.kind === 'unauthorized' ? (
              <>
                <ShieldAlert size={28} className="text-[var(--danger)]" />
                <div>
                  <p className="text-sm font-medium text-[var(--text)] mb-1">
                    {t('sessionExplorer.unauthTitle')}
                  </p>
                  <p className="text-xs text-[var(--text-muted)]">
                    {t('sessionExplorer.unauthBody')}
                  </p>
                </div>
              </>
            ) : (
              <>
                <ShieldAlert size={28} className="text-[var(--danger)]" />
                <div>
                  <p className="text-sm font-medium text-[var(--text)] mb-1">
                    {t('sessionExplorer.loadFail')}
                  </p>
                  <p className="text-xs text-[var(--text-muted)] max-w-xs break-all">
                    {detailError.message || t('sessionExplorer.unknownError')}
                  </p>
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-xs text-[var(--text-muted)]">
            Select a session to view messages
          </div>
        )}
      </div>
    </div>
  );
}
