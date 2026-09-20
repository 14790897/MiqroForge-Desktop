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
  Search,
  ArrowLeft,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { MiQroForgeLogo } from './MiQroForgeLogo';
import { ContextMenu } from './ContextMenu';
import { InputDialog } from './shared/InputDialog';
import { useSessionStatus, type SessionStatus } from '../hooks/useSessionStatus';
import type { SessionInfo } from '../../shared/ipc';

type FilterTab = 'ALL' | 'PENDING' | 'IN-PROGRESS' | 'REVIEW' | 'COMPLETED';

const MIN_WIDTH = 180;
const MAX_WIDTH = 480;

/** 底部展示的版本串。dev 模式(#1055)会带 commit hash，可能长到 30+ 字符。 */
const APP_VERSION_LABEL = `PRO v${
  typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'
}`;

import { usePanelResize } from '../hooks/usePanelResize';

import { formatRelativeTime, formatShortDateTime } from '../lib/formatTime';

interface SidebarProps {
  currentSession?: string;
  onSessionSelect?: (key: string) => void;
  onNavChange?: (id: string) => void;
  /** 当前的顶层导航（App 的 activeNav）。离开 chat 时底部要让出「返回任务」，
   *  否则用户只能靠点某个会话回到聊天——会话列表为空时完全回不去。 */
  currentNav?: string;
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

/** 卡片上的工作目录：home 缩成 ~，过长时保留尾部。 */
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

const STATUS_ICONS: Record<SessionStatus, LucideIcon> = {
  'IN-PROGRESS': Play,
  PENDING: Clock,
  REVIEW: Eye,
  COMPLETED: CheckCircle2,
  CC: Eye,
};

export function Sidebar({
  currentSession,
  onSessionSelect,
  onNavChange,
  currentNav = 'chat',
  refreshKey,
  onNewSession,
  onRenamed,
  onSessionDeleted,
}: SidebarProps) {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [initialLoading, setInitialLoading] = useState(true);
  const [filter, setFilter] = useState<FilterTab>('ALL');
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
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

  // Reset display count when sessions list, filter or search changes
  useEffect(() => {
    setDisplayCount(PER_PAGE);
  }, [sessions, filter, query]);

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
    { value: 'PENDING', label: '待处理' },
    { value: 'IN-PROGRESS', label: '进行中' },
    { value: 'REVIEW', label: '待审阅' },
    { value: 'COMPLETED', label: '已完成' },
  ];

  // Single-pass: count per filter + compute filtered list (Copilot optimization)
  const { filterCounts, filteredSessions } = useMemo(() => {
    const counts: Record<FilterTab, number> = {
      ALL: 0,
      PENDING: 0,
      'IN-PROGRESS': 0,
      REVIEW: 0,
      COMPLETED: 0,
    };
    const filtered: SessionInfo[] = [];
    // 搜索只影响列表，不影响各状态计数——计数表示的是「该状态总共有多少」。
    const q = query.trim().toLowerCase();
    for (const s of sessions) {
      counts.ALL++;
      const status = getStatus(s.key);
      // PENDING 是默认态，旧实现不统计它，导致这些会话只能从「全部」里翻。
      if (status === 'PENDING') counts.PENDING++;
      else if (status === 'IN-PROGRESS') counts['IN-PROGRESS']++;
      else if (status === 'REVIEW') counts.REVIEW++;
      else if (status === 'COMPLETED') counts.COMPLETED++;
      if (filter !== 'ALL' && status !== filter) continue;
      if (q) {
        const haystack = `${s.title ?? ''}\n${s.workspace ?? ''}`.toLowerCase();
        if (!haystack.includes(q)) continue;
      }
      filtered.push(s);
    }
    return { filterCounts: counts, filteredSessions: filtered };
  }, [sessions, filter, getStatus, query]);

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
      {/* Header — 紧凑版：logo 28→22、标题 14→13.125、内边距 py-3→py-2，
          实测头部 52→36px。搜索平时只占一个图标位，点击才展开成输入框，
          不占常驻高度。 */}
      <div className="flex items-center gap-2 px-3 py-2 shrink-0">
        <MiQroForgeLogo size={22} />
        {/* 用 text-xs 而非 text-size-2xs：后者(12.25px)和分组头同号，会把
            「侧栏区块名」和「目录名」的层级压平。 */}
        <span className="text-xs font-semibold text-text" data-testid="nav-tasks-title">
          任务
        </span>
        <div className="ml-auto flex items-center gap-0.5">
          <button
            onClick={() => {
              // 关闭搜索时必须同时清 query：否则输入框藏起来了，列表却仍停在被
              // 过滤的状态，用户看到残缺/空白的结果却找不到原因（Esc 本来就清两者）。
              if (searchOpen) setQuery('');
              setSearchOpen((v) => !v);
            }}
            className="w-6 h-6 rounded flex items-center justify-center transition-colors hover:bg-[var(--surface-muted)]"
            title={searchOpen ? '关闭搜索' : '搜索会话'}
            aria-expanded={searchOpen}
            data-testid="nav-session-search"
          >
            <Search
              size={13}
              style={{ color: searchOpen ? 'var(--accent)' : 'var(--text-faint)' }}
            />
          </button>
          <button
            onClick={onNewSession}
            className="w-6 h-6 rounded flex items-center justify-center transition-colors hover:bg-[var(--surface-muted)]"
            title="新建会话"
            data-testid="nav-new-session"
          >
            <Plus size={14} style={{ color: 'var(--text-faint)' }} />
          </button>
        </div>
      </div>

      {searchOpen && (
        <div className="shrink-0 px-3 pb-2">
          <div className="relative flex items-center">
            <Search
              size={12}
              className="absolute left-2 pointer-events-none"
              style={{ color: 'var(--text-faint)' }}
            />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setQuery('');
                  setSearchOpen(false);
                }
              }}
              placeholder="搜索会话或目录"
              aria-label="搜索会话"
              className="w-full pl-7 pr-2 py-1 rounded-md text-size-2xs text-text border border-[var(--border-subtle)] focus:border-[var(--accent)] outline-none transition-colors"
              style={{ background: 'var(--surface)' }}
            />
          </div>
        </div>
      )}

      {/* Filter tabs — underline style（沿用原样式；5 个标签下每格约 47px，
          3 个中文字已占 42px，故不再带数字徽标，否则必然溢出） */}
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

      {/* Session list — 平铺（分组已回退，见底部栏注释处的说明） */}
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
        ) : filteredSessions.length === 0 ? (
          // 计数为 0 的筛选 Tab 不渲染，但 filter 可能仍停在该值上（列表重载后
          // 计数掉了）；搜索也可能一无所获。两种情况都要兜底，否则一片空白。
          <div className="flex flex-col items-center gap-2 py-8 text-center">
            <ListChecks size={20} style={{ color: 'var(--text-faint)', opacity: 0.4 }} />
            <p className="text-xs text-text-faint">
              {query.trim() ? '没有匹配的会话' : '该状态下暂无会话'}
            </p>
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
                            // #1062：带上会话 key。绑定文件夹会话的工作区不是全局
                            // 工作区，不带 key 时主进程只按全局校验 → 被拒且无提示。
                            onSelect: () =>
                              window.miqi.files.openContainingFolder(s.workspace!, s.key),
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
                      data-testid="session-item"
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
                      {/* Top row: status icon + label left · time right（用户：恢复原版） */}
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

      {/* Bottom bar。两个元素都必须禁止折行：窄侧栏(最小 180px)下它们是可压缩
          的 flex 子项,「系统设置」和版本号会各自折成两行,把底部栏从 34px 撑到
          50px。版本号改用 truncate,空间不够时省略号收尾而不是换行。 */}
      <div
        className="shrink-0 px-4 py-2.5 border-t flex items-center justify-between"
        style={{ borderColor: 'var(--sidebar-border)' }}
      >
        <button
          className="shrink-0 whitespace-nowrap flex items-center gap-1.5 text-size-2xs cursor-pointer transition duration-150 hover:scale-110 hover:text-[var(--text)] origin-left text-text-faint"
          onClick={() => onNavChange?.('settings')}
          data-testid="nav-system-settings"
        >
          <Settings size={13} />
          <span>系统设置</span>
        </button>
        {currentNav === 'chat' ? (
          // dev 模式(#1055)下版本号会带 commit hash(形如 0.32.0-dev-dev+2a270f53.dirty,
          // 30+ 字符),260px 默认宽度都放不下。truncate 让它省略号收尾而不是折行,
          // title 保证完整字符串仍可读到——否则 #1055 的可追溯性就没了。
          <span
            className="min-w-0 truncate text-size-2xs font-mono text-text-faint"
            title={APP_VERSION_LABEL}
          >
            {APP_VERSION_LABEL}
          </span>
        ) : (
          // 离开聊天后唯一的回程入口：上面的「系统设置」只会往设置页走，
          // 不点某个会话就回不来（会话列表为空时更是死路）。版本号让位给它。
          // 不用 hover:scale（旁边那个用了），180px 最小宽度下两个按钮会互相压到。
          <button
            className="shrink-0 whitespace-nowrap flex items-center gap-1.5 text-size-2xs cursor-pointer transition duration-150 hover:opacity-80"
            style={{ color: 'var(--accent)' }}
            onClick={() => onNavChange?.('chat')}
            data-testid="nav-back-to-tasks"
          >
            <ArrowLeft size={13} />
            <span>返回任务</span>
          </button>
        )}
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
