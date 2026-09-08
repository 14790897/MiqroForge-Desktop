import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { cn } from '../lib/utils';
import {
  Plus,
  ListChecks,
  Settings,
  Play,
  Clock,
  Eye,
  CheckCircle2,
  RotateCcw,
  Archive,
  Trash2,
  FolderOpen,
  Pencil,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { MiQroForgeLogo } from './MiQroForgeLogo';
import { ContextMenu } from './ContextMenu';
import { InputDialog } from './shared/InputDialog';
import { useSessionStatus, type SessionStatus } from '../hooks/useSessionStatus';
import type { SessionInfo } from '../../shared/ipc';

type FilterTab = 'ALL' | 'IN-PROGRESS' | 'REVIEW' | 'COMPLETED';

const MIN_WIDTH = 180;
const MAX_WIDTH = 480;

import { usePanelResize } from '../hooks/usePanelResize';

import { formatRelativeTime, formatShortDateTime } from '../lib/formatTime';

interface SidebarProps {
  currentSession?: string;
  onSessionSelect?: (key: string) => void;
  onNavChange?: (id: string) => void;
  refreshKey?: number;
  onNewSession?: () => void;
  /** Called after a successful rename so the parent can refresh the active
   *  chat header (which reads the title from the backend on reload). */
  onRenamed?: () => void;
  /** Called after the CURRENTLY OPEN session is deleted, so the parent can
   *  reset the active session (otherwise ChatConsole keeps showing the
   *  deleted conversation's messages). */
  onSessionDeleted?: (key: string) => void;
}

const STATUS_ICONS: Record<SessionStatus, LucideIcon> = {
  'IN-PROGRESS': Play,
  PENDING: Clock,
  REVIEW: Eye,
  COMPLETED: CheckCircle2,
  CC: Eye,
};

function formatWorkspace(workspace?: string): string | null {
  if (!workspace) return null;
  const home = (typeof process !== 'undefined' ? process.env?.HOME : null) ?? '';
  let display = workspace;
  if (home && workspace.startsWith(home)) {
    display = '~' + workspace.slice(home.length);
  }
  if (display.length > 28) {
    display = '...' + display.slice(display.length - 25);
  }
  return display;
}

export function Sidebar({
  currentSession,
  onSessionSelect,
  onNavChange,
  refreshKey,
  onNewSession,
  onRenamed,
  onSessionDeleted,
}: SidebarProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [filter, setFilter] = useState<FilterTab>('ALL');
  const [renameTarget, setRenameTarget] = useState<SessionInfo | null>(null);
  const {
    width: sidebarWidth,
    containerRef: sidebarRef,
    handleMouseDown,
  } = usePanelResize({
    minWidth: MIN_WIDTH,
    maxWidth: MAX_WIDTH,
    defaultWidth: 260,
    computeWidth: (e, rect) => e.clientX - rect.left,
  });

  const { getStatus, getStatusDisplay, setStatus, clearStatus } = useSessionStatus();

  // ── Lazy rendering ──────────────────────────────────────────────────
  const PER_PAGE = 20;
  const [displayCount, setDisplayCount] = useState(PER_PAGE);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const listContainerRef = useRef<HTMLDivElement>(null);

  // Reset display count when sessions list or filter changes
  useEffect(() => {
    setDisplayCount(PER_PAGE);
  }, [sessions, filter]);

  const loadSessions = useCallback(async () => {
    try {
      const r = await window.miqi.sessions.list();
      setSessions(r?.sessions ?? []);
    } catch {
      /* Bridge not available */
    }
    setInitialLoading(false);
  }, []);

  const handleRenameConfirm = useCallback(
    async (title: string) => {
      if (!renameTarget) return;
      // Cap at 100 chars and trim whitespace so the IPC validator (min 1, max 100)
      // can't reject an overlong/blank title and cause a silent no-op.
      const cleaned = title.trim().slice(0, 100);
      if (!cleaned) return;
      try {
        await window.miqi.sessions.rename(renameTarget.key, cleaned);
      } catch {
        /* ignore */
      }
      setRenameTarget(null);
      onRenamed?.();
      loadSessions();
    },
    [renameTarget, loadSessions, onRenamed]
  );

  useEffect(() => {
    loadSessions();
  }, [loadSessions, refreshKey]);

  useEffect(() => {
    const unsub = window.miqi.runtime.onStateChange((status) => {
      if (status.state === 'running') loadSessions();
    });
    return () => {
      unsub();
    };
  }, [loadSessions]);

  const FILTER_TABS: Array<{ value: FilterTab; label: string }> = [
    { value: 'ALL', label: '全部' },
    { value: 'IN-PROGRESS', label: '进行中' },
    { value: 'REVIEW', label: '待审阅' },
    { value: 'COMPLETED', label: '已完成' },
  ];

  // Single-pass: count per filter + compute filtered list (Copilot optimization)
  const { filterCounts, filteredSessions } = useMemo(() => {
    const counts: Record<FilterTab, number> = { ALL: 0, 'IN-PROGRESS': 0, REVIEW: 0, COMPLETED: 0 };
    const filtered: SessionInfo[] = [];
    for (const s of sessions) {
      counts.ALL++;
      const status = getStatus(s.key);
      if (status === 'IN-PROGRESS') counts['IN-PROGRESS']++;
      else if (status === 'REVIEW') counts.REVIEW++;
      else if (status === 'COMPLETED') counts.COMPLETED++;
      if (filter === 'ALL' || status === filter) filtered.push(s);
    }
    return { filterCounts: counts, filteredSessions: filtered };
  }, [sessions, filter, getStatus]);

  // IntersectionObserver: load next page when sentinel enters viewport
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const container = listContainerRef.current;
    if (!sentinel || !container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setDisplayCount((prev) => {
            const next = prev + PER_PAGE;
            return next > filteredSessions.length ? filteredSessions.length : next;
          });
        }
      },
      {
        root: container,
        rootMargin: '300px',
        threshold: 0,
      }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [filteredSessions.length]);

  return (
    <div
      ref={sidebarRef}
      className="sidebar-shell flex flex-col shrink-0 border-r relative"
      style={{
        width: sidebarWidth,
      }}
    >
      {/* Resize handle */}
      <div
        onMouseDown={handleMouseDown}
        className="absolute top-0 right-0 w-1.5 h-full cursor-col-resize hover:bg-[var(--accent)]/30 transition-colors z-10"
        style={{ marginRight: -2 }}
      />
      {/* Header: glitch M logo + Tasks title */}
      <div className="flex items-center gap-2.5 px-4 py-3 shrink-0">
        <MiQroForgeLogo size={28} />
        <span className="text-sm font-semibold text-text" data-testid="nav-tasks-title">
          任务
        </span>
        <button
          onClick={onNewSession}
          className="ml-auto w-6 h-6 rounded flex items-center justify-center transition-colors hover:bg-[var(--surface-muted)]"
          title="新建会话"
          data-testid="nav-new-session"
        >
          <Plus size={14} style={{ color: 'var(--text-faint)' }} />
        </button>
      </div>

      {/* Filter tabs — underline style */}
      <div className="shrink-0 overflow-x-auto px-3 pb-2">
        <div className="flex items-stretch justify-between min-w-max" role="tablist">
          {FILTER_TABS.map((tab) => {
            const isActive = filter === tab.value;
            const count = filterCounts[tab.value];
            const tabButton = (
              <button
                key={tab.value}
                role="tab"
                aria-selected={isActive}
                onClick={() => setFilter(tab.value)}
                className={cn(
                  'relative flex-1 flex items-center justify-center gap-1 py-2 text-xs font-medium transition duration-150 rounded-md',
                  'hover:bg-black/[0.04]',
                  isActive
                    ? 'text-[var(--text)] font-semibold'
                    : 'text-[var(--text-faint)] hover:text-[var(--text-muted)]'
                )}
              >
                {tab.label}
                {count > 0 && (
                  <span
                    className={cn(
                      'inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full text-size-2xs font-medium leading-none',
                      isActive ? 'text-[var(--accent)]' : 'text-[var(--text-faint)]'
                    )}
                    style={
                      isActive
                        ? { background: 'color-mix(in srgb, var(--accent) 18%, transparent)' }
                        : { background: 'var(--surface-muted)' }
                    }
                  >
                    {count}
                  </span>
                )}
                {isActive && (
                  <span className="absolute bottom-0 left-2 right-2 h-[2px] rounded-full bg-[var(--accent)]/70" />
                )}
              </button>
            );
            // Right-click on the 全部 tab: bulk delete / archive all
            if (tab.value === 'ALL') {
              return (
                <ContextMenu
                  key={tab.value}
                  items={[
                    {
                      label: '删除全部任务',
                      icon: <Trash2 size={13} />,
                      danger: true,
                      onSelect: async () => {
                        if (!window.confirm(`确认删除全部 ${count} 个任务？此操作不可撤销。`))
                          return;
                        window.dispatchEvent(new Event('miqi:chat-focus-regrant'));
                        for (const s of sessions) {
                          try {
                            await window.miqi.sessions.delete(s.key);
                            // 命中当前会话立即通知 App 切到新空会话，不必等整批删完
                            // ——否则删除期间 UI 仍指向已删的 key（CodeRabbit）。会话 key
                            // 唯一，快照内最多命中一次，不会重复通知。
                            if (s.key === currentSession) {
                              onSessionDeleted?.(currentSession);
                            }
                          } catch {
                            /* ignore */
                          }
                        }
                        loadSessions();
                      },
                    },
                    {
                      label: '归档全部任务',
                      icon: <Archive size={13} />,
                      onSelect: async () => {
                        for (const s of sessions) {
                          try {
                            await window.miqi.sessions.archive(s.key);
                          } catch {
                            /* ignore */
                          }
                        }
                        loadSessions();
                      },
                    },
                  ]}
                >
                  {({ onContextMenu }) =>
                    React.cloneElement(
                      tabButton as React.ReactElement<{
                        onContextMenu?: (e: React.MouseEvent) => void;
                      }>,
                      { onContextMenu }
                    )
                  }
                </ContextMenu>
              );
            }
            return tabButton;
          })}
        </div>
      </div>

      {/* Session list — card style with left border + description */}
      <div ref={listContainerRef} className="flex-1 overflow-y-auto px-3 pt-1 pb-2">
        {initialLoading && sessions.length === 0 ? (
          <div className="flex items-center justify-center py-6">
            <div className="w-4 h-4 border-2 border-[var(--border)] border-t-[var(--accent)] rounded-full animate-spin" />
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-8 text-center">
            <ListChecks size={20} style={{ color: 'var(--text-faint)', opacity: 0.4 }} />
            <p className="text-xs text-text-faint">暂无任务</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filteredSessions.slice(0, displayCount).map((s) => {
              const isActive = currentSession === s.key;
              const displayName = s.title || formatShortDateTime(parseInt(s.key, 10));
              const wsPath = formatWorkspace(s.workspace);
              const sessionStatus = getStatus(s.key);
              const status = getStatusDisplay(sessionStatus);
              const StatusIcon = STATUS_ICONS[sessionStatus];
              return (
                <ContextMenu
                  key={s.key}
                  items={[
                    {
                      label: '标记为进行中',
                      icon: <Play size={13} />,
                      onSelect: () => setStatus(s.key, 'IN-PROGRESS'),
                    },
                    {
                      label: '标记为待处理',
                      icon: <Clock size={13} />,
                      onSelect: () => setStatus(s.key, 'PENDING'),
                    },
                    {
                      label: '标记为待审阅',
                      icon: <Eye size={13} />,
                      onSelect: () => setStatus(s.key, 'REVIEW'),
                    },
                    {
                      label: '标记为已完成',
                      icon: <CheckCircle2 size={13} />,
                      divider: true,
                      onSelect: () => setStatus(s.key, 'COMPLETED'),
                    },
                    ...(s.workspace
                      ? [
                          {
                            label: '在文件管理器中打开',
                            icon: <FolderOpen size={13} />,
                            onSelect: () => window.miqi.files.openContainingFolder(s.workspace!),
                          },
                        ]
                      : []),
                    {
                      label: '重命名',
                      icon: <Pencil size={13} />,
                      onSelect: () => setRenameTarget(s),
                    },
                    {
                      label: '重置状态',
                      icon: <RotateCcw size={13} />,
                      danger: true,
                      onSelect: () => clearStatus(s.key),
                    },
                    {
                      label: '归档',
                      icon: <Archive size={13} />,
                      divider: true,
                      onSelect: async () => {
                        try {
                          await window.miqi.sessions.archive(s.key);
                          loadSessions();
                        } catch {
                          /* ignore */
                        }
                      },
                    },
                    {
                      label: '删除对话',
                      icon: <Trash2 size={13} />,
                      danger: true,
                      onSelect: async () => {
                        if (!window.confirm(`删除对话「${s.title || s.key}」？此操作不可撤销。`))
                          return;
                        window.dispatchEvent(new Event('miqi:chat-focus-regrant'));
                        try {
                          await window.miqi.sessions.delete(s.key);
                          // Deleting the OPEN session must reset the active chat —
                          // otherwise ChatConsole keeps rendering its messages.
                          if (s.key === currentSession) onSessionDeleted?.(s.key);
                          loadSessions();
                        } catch {
                          /* ignore */
                        }
                      },
                    },
                  ]}
                >
                  {({ onContextMenu }) => (
                    <button
                      onClick={() => onSessionSelect?.(s.key)}
                      onContextMenu={onContextMenu}
                      className={cn(
                        'w-full text-left rounded-xl px-3 py-3 transition-transform duration-150',
                        isActive && 'shadow-[0_2px_16px_rgba(0,0,0,0.14)]',
                        !isActive &&
                          'hover:shadow-[0_4px_12px_rgba(0,0,0,0.1)] hover:-translate-y-px'
                      )}
                      style={{
                        background: status.cardBg,
                        border: `1px solid ${isActive ? (sessionStatus === 'IN-PROGRESS' ? status.bg : status.color) : status.cardBorder}`,
                      }}
                    >
                      {/* Top row: status icon + label left · time right */}
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-1.5">
                          <span
                            className="shrink-0 flex items-center justify-center w-[18px] h-[18px] rounded"
                            style={{ background: status.bg, color: status.color }}
                          >
                            <StatusIcon size={11} strokeWidth={2.5} />
                          </span>
                          <span
                            className="text-size-2xs font-medium"
                            style={{
                              color: sessionStatus === 'IN-PROGRESS' ? status.bg : status.color,
                            }}
                          >
                            {status.label}
                          </span>
                        </div>
                        <span className="text-size-2xs text-text-faint">
                          {formatRelativeTime(s.updated_at)}
                        </span>
                      </div>
                      {/* Title — large bold, one line */}
                      <p className="text-sm font-bold truncate mb-1 text-text" title={displayName}>
                        {displayName}
                      </p>
                      {/* Workspace — small muted path */}
                      {wsPath && (
                        <p
                          className="text-[10px] truncate mb-1 text-text-faint"
                          title={s.workspace}
                        >
                          {wsPath}
                        </p>
                      )}
                      {/* Description — small gray, multi-line */}
                      <p className="text-xs leading-relaxed text-text-muted">
                        {s.message_count != null ? `${s.message_count} 条消息` : '暂无描述'}
                      </p>
                    </button>
                  )}
                </ContextMenu>
              );
            })}
            {/* Sentinel element for lazy-load intersection detection */}
            {displayCount < filteredSessions.length && <div ref={sentinelRef} className="h-1" />}
          </div>
        )}
      </div>

      {/* Bottom bar */}
      <div
        className="shrink-0 px-4 py-2.5 border-t flex items-center justify-between"
        style={{ borderColor: 'var(--sidebar-border)' }}
      >
        <button
          className="flex items-center gap-1.5 text-size-2xs cursor-pointer transition duration-150 hover:scale-110 hover:text-[var(--text)] origin-left text-text-faint"
          onClick={() => onNavChange?.('settings')}
          data-testid="nav-system-settings"
        >
          <Settings size={13} />
          <span>系统设置</span>
        </button>
        <span className="text-size-2xs font-mono text-text-faint">
          PRO v{typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'}
        </span>
      </div>

      {/* Rename dialog */}
      <InputDialog
        open={renameTarget != null}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null);
        }}
        title="重命名会话"
        label="输入新的会话标题"
        defaultValue={renameTarget?.title ?? ''}
        onConfirm={handleRenameConfirm}
      />
    </div>
  );
}
