import { useState, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookOpen,
  FileText,
  RefreshCw,
  Save,
  Loader2,
  AlertTriangle,
  Lightbulb,
  Shield,
  X,
  Plus,
  Trash2,
  Copy,
  Check,
  ChevronRight,
  type LucideIcon,
} from 'lucide-react';
import { ContextMenu } from '../../components/ContextMenu';
import { cn } from '../../lib/utils';
import type { MemoryFileInfo, MemoryLessonEntry } from '../../../shared/ipc';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const SCOPE_ICONS: Record<string, LucideIcon> = {
  workspace: FileText,
  agent: BookOpen,
};

const SCOPE_LABELS: Record<string, string> = {
  workspace: '日常笔记',
  agent: 'Agent 记忆',
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

import { formatAbsoluteTime } from '../../lib/formatTime';

import { ConfirmDialog, SaveConfirmDialog, InputDialog } from '../../components/shared';

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function MemoryPage() {
  const [files, setFiles] = useState<MemoryFileInfo[]>([]);
  const [lessons, setLessons] = useState<MemoryLessonEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeFile, setActiveFile] = useState<MemoryFileInfo | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [editorContent, setEditorContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [showSaveConfirm, setShowSaveConfirm] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [showNewFileDialog, setShowNewFileDialog] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(null);
  const [copiedAll, setCopiedAll] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [fl, ll] = await Promise.all([window.miqi.memory.list(), window.miqi.memory.lessons()]);
      setFiles(fl.files);
      setLessons(ll.lessons);
    } catch {
      // runtime not running
    } finally {
      setLoading(false);
    }
  }, []);

  const unlearnLesson = useCallback(
    async (lessonId: string) => {
      try {
        await window.miqi.memory.lessonUnlearn(lessonId);
        await load();
      } catch {
        // runtime not running
      }
    },
    [load]
  );

  useEffect(() => {
    load();
  }, [load]);

  // Load file content when switching active file
  const selectFile = useCallback(
    async (info: MemoryFileInfo) => {
      if (dirty && activeFile && info.path !== activeFile.path) {
        const ok = confirm(`文件 ${activeFile.path} 有未保存的更改，确认丢弃？`);
        if (!ok) return;
      }

      setActiveFile(info);
      setDirty(false);
      setEditorContent('');
      setError(null);
      setSuccess(null);

      try {
        const result = await window.miqi.memory.get(info.path);
        setFileContent(result.content);
        setEditorContent(result.content);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : '加载文件失败');
      }
    },
    [dirty, activeFile]
  );

  const handleSave = async () => {
    if (!activeFile) return;
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      await window.miqi.memory.update(activeFile.path, editorContent);
      setFileContent(editorContent);
      setDirty(false);
      setSuccess(`已保存 ${activeFile.path}`);
      setTimeout(() => setSuccess(null), 2000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const requestSave = () => {
    setShowSaveConfirm(true);
  };

  // Create new file
  const handleCreateFile = async (rawName: string) => {
    const name = rawName.trim();
    if (!name) return;
    const path = name.endsWith('.md') ? name : `${name}.md`;
    try {
      await window.miqi.memory.update(path, '');
      await load();
      const newFile = files.find((f) => f.path === path) ?? {
        path,
        scope: 'workspace',
        size: 0,
        updatedAtMs: Date.now(),
      };
      selectFile(newFile);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '创建文件失败');
    }
    setShowNewFileDialog(false);
  };

  // Delete file
  const handleDeleteFile = async (path: string) => {
    try {
      await window.miqi.memory.delete(path);
      if (activeFile?.path === path) {
        setActiveFile(null);
        setFileContent(null);
        setEditorContent('');
        setDirty(false);
      }
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : '删除文件失败');
    }
    setShowDeleteConfirm(null);
  };

  // Copy all content
  const handleCopyAll = () => {
    navigator.clipboard.writeText(editorContent);
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  };

  // Group files by scope
  const workspaceFiles = files.filter((f) => f.scope === 'workspace');
  const agentFiles = files.filter((f) => f.scope === 'agent');

  const hasUnsaved = dirty && activeFile;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0">
        <div>
          <h1 className="text-base font-semibold text-[var(--text)]">记忆</h1>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">
            {loading ? '加载中…' : `${files.length} 个文件，${lessons.length} 条 Lesson`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {success && <span className="text-xs text-[var(--success)]">{success}</span>}
          {error && (
            <span className="text-xs text-[var(--danger)] flex items-center gap-1">
              <AlertTriangle size={12} /> {error}
              <button onClick={() => setError(null)} className="hover:text-[var(--text)]">
                <X size={12} />
              </button>
            </span>
          )}
          <button
            onClick={load}
            className="text-xs text-[var(--text-faint)] hover:text-[var(--text-muted)] transition-colors px-2 py-1 rounded"
          >
            <RefreshCw size={14} />
          </button>
          <button
            onClick={requestSave}
            disabled={!hasUnsaved || saving}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors',
              hasUnsaved
                ? 'bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]'
                : 'bg-[var(--surface-muted)] text-[var(--text-faint)]',
              'disabled:opacity-50'
            )}
          >
            {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            {hasUnsaved ? '保存 *' : '保存'}
          </button>
        </div>
      </div>

      {/* Body: left files + right editor */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar: file list */}
        <div className="w-[240px] shrink-0 border-r border-[var(--border-subtle)] bg-[var(--surface)] overflow-y-auto flex flex-col">
          {/* New file button */}
          <div className="px-2 py-2 border-b border-[var(--border-subtle)]">
            <button
              onClick={() => setShowNewFileDialog(true)}
              className="flex items-center gap-1.5 w-full px-2 py-1.5 rounded-lg text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
            >
              <Plus size={13} />
              <span>新建</span>
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="flex items-center justify-center h-20 text-xs text-[var(--text-faint)]">
                <Loader2 size={14} className="animate-spin mr-1.5" /> Loading...
              </div>
            ) : files.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-20 gap-1.5 text-xs text-[var(--text-faint)]">
                <FileText size={16} />
                <span>暂无记忆文件</span>
              </div>
            ) : (
              <div className="py-1">
                <FileGroup
                  label="Agent 记忆"
                  icon={BookOpen}
                  files={agentFiles}
                  activePath={activeFile?.path ?? null}
                  onSelect={selectFile}
                  onDelete={(path) => setShowDeleteConfirm(path)}
                />
                <FileGroup
                  label="日常笔记"
                  icon={FileText}
                  files={workspaceFiles}
                  activePath={activeFile?.path ?? null}
                  onSelect={selectFile}
                  onDelete={(path) => setShowDeleteConfirm(path)}
                />
              </div>
            )}
          </div>
        </div>

        {/* Right: editor + lessons */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {!activeFile ? (
            <div className="flex flex-col items-center justify-center flex-1 gap-3 text-sm text-[var(--text-faint)]">
              <BookOpen size={28} />
              <span>从左侧选择文件查看和编辑</span>
            </div>
          ) : (
            <>
              {/* Editor header */}
              <div className="flex items-center gap-2 px-5 py-2 border-b border-[var(--border-subtle)] bg-[var(--surface-muted)] shrink-0">
                <span className="text-xs font-medium text-[var(--text)]">{activeFile.path}</span>
                <span className="text-xs text-[var(--text-faint)]">
                  {formatSize(activeFile.size)}
                </span>
                <span className="text-xs text-[var(--text-faint)]">
                  {formatAbsoluteTime(activeFile.updatedAtMs)}
                </span>
                {dirty && (
                  <span className="text-xs text-[var(--warning)] font-medium ml-auto">未保存</span>
                )}
                {/* Copy all button */}
                <button
                  onClick={handleCopyAll}
                  className="ml-auto flex items-center gap-1 px-2 py-1 rounded text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                >
                  {copiedAll ? <Check size={12} /> : <Copy size={12} />}
                  <span>{copiedAll ? '已复制' : '复制全部'}</span>
                </button>
                {!dirty && (
                  <div className="flex items-center gap-1 rounded-md border border-[var(--border-subtle)] overflow-hidden">
                    <button
                      onClick={() => setPreviewMode(false)}
                      className={cn(
                        'px-2 py-0.5 text-xs',
                        !previewMode
                          ? 'bg-[var(--accent)] text-white'
                          : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'
                      )}
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => setPreviewMode(true)}
                      className={cn(
                        'px-2 py-0.5 text-xs',
                        previewMode
                          ? 'bg-[var(--accent)] text-white'
                          : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'
                      )}
                    >
                      预览
                    </button>
                  </div>
                )}
              </div>

              {/* Editor */}
              <div className="flex-1 overflow-hidden">
                {previewMode ? (
                  <div className="w-full h-full px-5 py-4 overflow-y-auto text-sm text-[var(--text)] prose prose-sm max-w-none leading-relaxed">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{editorContent}</ReactMarkdown>
                  </div>
                ) : (
                  <textarea
                    value={editorContent}
                    onChange={(e) => {
                      setEditorContent(e.target.value);
                      setDirty(e.target.value !== fileContent);
                    }}
                    className="w-full h-full px-5 py-4 resize-none text-sm font-mono text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none leading-relaxed"
                    spellCheck={false}
                    placeholder="文件内容…"
                  />
                )}
              </div>

              {/* Lessons section */}
              <div className="border-t border-[var(--border-subtle)] shrink-0">
                <div className="flex items-center gap-2 px-5 py-2 bg-[var(--surface-muted)] border-b border-[var(--border-subtle)]">
                  <Lightbulb size={13} className="text-[var(--warning)]" />
                  <span className="text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
                    自我优化 Lessons
                  </span>
                  <span className="text-xs text-[var(--text-faint)] ml-auto">
                    {lessons.length} total
                  </span>
                </div>
                <div className="max-h-[200px] overflow-y-auto">
                  {lessons.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-8 gap-2 text-xs text-[var(--text-faint)]">
                      <Lightbulb size={18} />
                      <span>暂无 Lesson 记录。</span>
                      <span>Agent 收到用户纠正后会自动记录 Lesson。</span>
                    </div>
                  ) : (
                    <div className="divide-y divide-[var(--border-subtle)]">
                      {lessons.map((lesson) => (
                        <LessonRow key={lesson.id} lesson={lesson} onUnlearn={unlearnLesson} />
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {/* New file dialog */}
      {showNewFileDialog && (
        <InputDialog
          open={showNewFileDialog}
          onOpenChange={(o) => {
            if (!o) setShowNewFileDialog(false);
          }}
          title="新建记忆文件"
          label="文件名（自动添加 .md）"
          onConfirm={handleCreateFile}
        />
      )}

      {/* Delete confirm dialog */}
      {showDeleteConfirm && (
        <ConfirmDialog
          title="删除文件"
          message={`确定删除 ${showDeleteConfirm}？此操作不可撤销。`}
          danger
          onConfirm={() => handleDeleteFile(showDeleteConfirm)}
          onCancel={() => setShowDeleteConfirm(null)}
        />
      )}

      {/* Save confirm dialog */}
      {showSaveConfirm && activeFile && (
        <SaveConfirmDialog
          filePath={activeFile.path}
          onConfirm={() => {
            setShowSaveConfirm(false);
            handleSave();
          }}
          onCancel={() => setShowSaveConfirm(false)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// File group
// ---------------------------------------------------------------------------

function FileGroup({
  label,
  icon: Icon,
  files,
  activePath,
  onSelect,
  onDelete,
}: {
  label: string;
  icon: LucideIcon;
  files: MemoryFileInfo[];
  activePath: string | null;
  onSelect: (f: MemoryFileInfo) => void;
  onDelete: (path: string) => void;
}) {
  if (files.length === 0) return null;
  return (
    <div>
      <div className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold uppercase tracking-widest text-[var(--text-faint)]">
        <Icon size={12} />
        {label}
      </div>
      <div>
        {files.map((f) => {
          const isActive = activePath === f.path;
          return (
            <ContextMenu
              key={f.path}
              items={[
                { label: '打开编辑', onSelect: () => onSelect(f) },
                {
                  label: '复制文件内容',
                  onSelect: async () => {
                    try {
                      const r = await window.miqi.memory.get(f.path);
                      navigator.clipboard.writeText(r.content);
                    } catch {
                      /* ignore */
                    }
                  },
                },
                {
                  label: '删除文件',
                  danger: true,
                  divider: true,
                  onSelect: () => onDelete(f.path),
                },
              ]}
            >
              {({ onContextMenu }) => (
                <div
                  className={cn(
                    'flex items-center gap-1 group w-full',
                    isActive ? 'bg-[var(--accent-soft)]' : 'hover:bg-[var(--surface-muted)]'
                  )}
                  onContextMenu={onContextMenu}
                >
                  <button
                    onClick={() => onSelect(f)}
                    className={cn(
                      'flex items-center gap-2 flex-1 px-3 py-1.5 text-left text-sm transition-colors min-w-0',
                      isActive
                        ? 'text-[var(--accent)] font-medium'
                        : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                    )}
                  >
                    <ChevronRight
                      size={10}
                      className={cn(
                        'shrink-0',
                        isActive ? 'text-[var(--accent)]' : 'text-[var(--text-faint)]'
                      )}
                    />
                    <span className="truncate flex-1">{f.path}</span>
                    <span className="text-xs text-[var(--text-faint)] shrink-0">
                      {formatSize(f.size)}
                    </span>
                  </button>
                  <button
                    onClick={() => onDelete(f.path)}
                    className="shrink-0 pr-2 opacity-0 group-hover:opacity-100 text-[var(--text-faint)] hover:text-[var(--danger)] transition-all"
                    title="删除文件"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              )}
            </ContextMenu>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lesson row
// ---------------------------------------------------------------------------

const STATE_COLORS: Record<string, string> = {
  active: 'bg-[var(--success)]',
  stale: 'bg-[var(--warning)]',
  archived: 'bg-[var(--text-faint)]',
};

const STATE_LABELS: Record<string, string> = {
  active: 'active',
  stale: 'stale',
  archived: 'archived',
};

function LessonRow({
  lesson,
  onUnlearn,
}: {
  lesson: MemoryLessonEntry;
  onUnlearn: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const stateColor = STATE_COLORS[lesson.state] ?? STATE_COLORS.active;

  return (
    <div className="hover:bg-[var(--surface-muted)] transition-colors">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-5 py-2 text-left text-xs"
      >
        <div
          className={cn(
            'w-1.5 h-1.5 rounded-full shrink-0',
            lesson.scope === 'global' ? 'bg-[var(--accent)]' : 'bg-[var(--text-faint)]'
          )}
        />
        <span className="flex-1 text-[var(--text)] truncate">{lesson.trigger}</span>
        <span className={cn('text-xs shrink-0 px-1 rounded', stateColor)}>
          {STATE_LABELS[lesson.state] ?? lesson.state}
        </span>
        <span
          className={cn(
            'text-xs shrink-0',
            lesson.effectiveConfidence >= 3
              ? 'text-[var(--success)]'
              : lesson.effectiveConfidence >= 1
                ? 'text-[var(--warning)]'
                : 'text-[var(--text-faint)]'
          )}
        >
          c:{lesson.effectiveConfidence}
        </span>
        <span className="text-[var(--text-faint)] shrink-0">{lesson.hits}h</span>
        {!lesson.enabled && <Shield size={11} className="text-[var(--text-faint)]" />}
      </button>
      {expanded && (
        <div className="px-8 pb-2 flex flex-col gap-1 text-xs text-[var(--text-muted)]">
          {lesson.badAction && (
            <div>
              <span className="text-[var(--text-faint)]">Bad:</span> {lesson.badAction}
            </div>
          )}
          <div>
            <span className="text-[var(--text-faint)]">Better:</span> {lesson.betterAction}
          </div>
          <div className="flex items-center gap-3 mt-1">
            <span className="text-[var(--text-faint)]">Scope: {lesson.scope}</span>
            <span className="text-[var(--text-faint)]">Source: {lesson.source}</span>
            <span className="text-[var(--text-faint)]">Confidence: {lesson.confidence}</span>
          </div>
          {lesson.state !== 'archived' && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onUnlearn(lesson.id);
              }}
              className="mt-1 px-2 py-0.5 text-xs rounded border border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] self-start"
            >
              Unlearn
            </button>
          )}
        </div>
      )}
    </div>
  );
}
