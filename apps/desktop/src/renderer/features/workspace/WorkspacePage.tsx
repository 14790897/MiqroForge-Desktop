import { useState, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  FolderOpen,
  FileText,
  Folder,
  ChevronRight,
  ChevronDown,
  Save,
  AlertCircle,
  FilePlus,
  FolderPlus,
  Trash2,
  Pencil,
  Copy,
  Check,
  RefreshCw,
  ExternalLink,
  FolderSearch,
} from 'lucide-react';
import { ContextMenu } from '../../components/ContextMenu';
import type { FileNode } from '../../../shared/ipc';
import { SandboxHtmlFrame } from '../chat/components/SandboxHtmlFrame';

import { ConfirmDialog } from '../../components/shared';

// ---------------------------------------------------------------------------
// Input dialog (for new file/folder/rename)
// ---------------------------------------------------------------------------

import { InputDialog } from '../../components/shared';

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function WorkspacePage() {
  const [tree, setTree] = useState<FileNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentPath, setCurrentPath] = useState<string | null>(null);
  const [content, setContent] = useState('');
  const [savedContent, setSavedContent] = useState('');
  const [fileLoading, setFileLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingPath, setPendingPath] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [previewMode, setPreviewMode] = useState(false);
  const [copiedAll, setCopiedAll] = useState(false);
  const [binaryUrl, setBinaryUrl] = useState<string | null>(null);

  // File operations state
  const [actionTarget, setActionTarget] = useState<{
    type: 'newFile' | 'newFolder' | 'rename';
    parentPath?: string;
    nodePath?: string;
    currentName?: string;
  } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ path: string; isDir: boolean } | null>(null);

  const isUnsaved = content !== savedContent;

  const loadTree = useCallback(async () => {
    try {
      const res = await window.miqi.files.tree();
      setTree(res.root);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    loadTree().then(() => setLoading(false));
  }, [loadTree]);

  // Revoke blob URL on unmount to prevent memory leaks
  useEffect(() => {
    return () => {
      if (binaryUrl) URL.revokeObjectURL(binaryUrl);
    };
  }, [binaryUrl]);

  const openFile = useCallback(
    (path: string) => {
      if (isUnsaved && path !== currentPath) {
        setPendingPath(path);
        setShowConfirm(true);
        return;
      }
      loadFile(path);
    },
    [isUnsaved, currentPath]
  );

  const loadFile = useCallback(
    (path: string) => {
      // Revoke previous blob URL to prevent memory leaks
      if (binaryUrl) {
        URL.revokeObjectURL(binaryUrl);
        setBinaryUrl(null);
      }

      setFileLoading(true);
      setError(null);
      setCurrentPath(path);
      if (/\.html?$/i.test(path)) setPreviewMode(true);
      window.miqi.files
        .read(path)
        .then((res) => {
          if (res.is_binary && res.data_base64) {
            // Binary file: decode base64 → blob URL for iframe rendering
            const binary = atob(res.data_base64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) {
              bytes[i] = binary.charCodeAt(i);
            }
            const blob = new Blob([bytes], { type: res.mime_type || 'application/pdf' });
            setBinaryUrl(URL.createObjectURL(blob));
            setContent('');
            setSavedContent('');
          } else {
            setContent(res.content || '');
            setSavedContent(res.content || '');
          }
          setFileLoading(false);
        })
        .catch((err) => {
          setError(String(err?.message ?? err));
          setContent('');
          setSavedContent('');
          setFileLoading(false);
        });
    },
    [binaryUrl]
  );

  const confirmSwitch = useCallback(
    (ok: boolean) => {
      setShowConfirm(false);
      if (ok && pendingPath) {
        loadFile(pendingPath);
      }
      setPendingPath(null);
    },
    [pendingPath, loadFile]
  );

  const handleSave = useCallback(() => {
    if (!currentPath) return;
    setSaving(true);
    setError(null);
    window.miqi.files
      .write(currentPath, content)
      .then(() => {
        setSavedContent(content);
        setSaving(false);
      })
      .catch((err) => {
        setError(String(err?.message ?? err));
        setSaving(false);
      });
  }, [currentPath, content]);

  // Copy all
  const handleCopyAll = () => {
    navigator.clipboard.writeText(content);
    setCopiedAll(true);
    setTimeout(() => setCopiedAll(false), 2000);
  };

  // Create file/folder
  const handleCreate = async (name: string) => {
    if (!actionTarget) return;
    const parentPath = actionTarget.parentPath || '.';
    const fullPath = parentPath === '.' ? name : `${parentPath}/${name}`;

    try {
      if (actionTarget.type === 'newFolder') {
        // Create folder by creating a .gitkeep file inside it
        await window.miqi.files.write(`${fullPath}/.gitkeep`, '');
      } else {
        await window.miqi.files.write(fullPath, '');
      }
      await loadTree();
    } catch (err: unknown) {
      setError(String(err instanceof Error ? err.message : err));
    }
    setActionTarget(null);
  };

  // Rename file/folder
  const handleRename = async (newName: string) => {
    if (!actionTarget?.nodePath || !actionTarget.currentName) return;
    const nodePath = actionTarget.nodePath;
    const parentPath = nodePath.substring(0, nodePath.lastIndexOf('/'));
    const oldName = nodePath.substring(nodePath.lastIndexOf('/') + 1);
    const newPath = parentPath ? `${parentPath}/${newName}` : newName;

    try {
      // Read old content, write to new path, delete old
      const r = await window.miqi.files.read(nodePath).catch(() => null);
      if (!r) {
        setActionTarget(null);
        return;
      }
      if (r.is_binary && r.data_base64) {
        await window.miqi.files.write(newPath, '', undefined, r.data_base64);
      } else {
        await window.miqi.files.write(newPath, r.content || '');
      }
      await window.miqi.files.delete(nodePath);
      if (currentPath === nodePath) {
        setCurrentPath(newPath);
      }
      await loadTree();
    } catch (err: unknown) {
      setError(String(err instanceof Error ? err.message : err));
    }
    setActionTarget(null);
  };

  // Delete
  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await window.miqi.files.delete(deleteTarget.path);
      if (currentPath === deleteTarget.path || currentPath?.startsWith(deleteTarget.path + '/')) {
        setCurrentPath(null);
        setContent('');
        setSavedContent('');
      }
      await loadTree();
    } catch (err: unknown) {
      setError(String(err instanceof Error ? err.message : err));
    }
    setDeleteTarget(null);
  };

  // Keyboard shortcut: Ctrl+S to save
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        if (isUnsaved && currentPath) handleSave();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isUnsaved, currentPath, handleSave]);

  const isMdFile = currentPath?.endsWith('.md');
  const isPdfFile = currentPath?.toLowerCase().endsWith('.pdf');
  const isHtmlFile = currentPath ? /\.html?$/i.test(currentPath) : false;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-sm text-[var(--text-muted)]">正在加载工作区…</div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      {/* Left sidebar — file tree */}
      <div className="w-[260px] shrink-0 border-r border-[var(--border-subtle)] bg-[var(--surface)] flex flex-col">
        <div className="px-3 py-3 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <div className="text-xs font-semibold text-[var(--text-muted)] uppercase tracking-wider">
            工作区文件
          </div>
          <button
            onClick={loadTree}
            className="text-xs text-[var(--text-faint)] hover:text-[var(--text-muted)] transition-colors"
            title="刷新"
          >
            <RefreshCw size={12} />
          </button>
        </div>
        <div className="flex-1 overflow-auto px-1.5 py-1.5">
          {tree ? (
            <FileTree
              node={tree}
              onSelect={openFile}
              selectedPath={currentPath}
              onNewFile={(parentPath) => setActionTarget({ type: 'newFile', parentPath })}
              onNewFolder={(parentPath) => setActionTarget({ type: 'newFolder', parentPath })}
              onRename={(nodePath, currentName) =>
                setActionTarget({ type: 'rename', nodePath, currentName })
              }
              onDelete={(path, isDir) => setDeleteTarget({ path, isDir })}
            />
          ) : (
            <div className="text-xs text-[var(--text-muted)] text-center mt-8">未找到文件</div>
          )}
        </div>
      </div>

      {/* Right panel — editor */}
      <div className="flex-1 flex flex-col overflow-hidden bg-[var(--background)]">
        {currentPath ? (
          <>
            {/* Toolbar */}
            <div className="shrink-0 flex items-center justify-between px-4 py-2 border-b border-[var(--border-subtle)] bg-[var(--surface)]">
              <div className="flex items-center gap-2 min-w-0">
                <FileText size={14} className="text-[var(--text-muted)] shrink-0" />
                <span className="text-xs font-mono text-[var(--text)] truncate">{currentPath}</span>
                {isUnsaved && (
                  <span className="text-size-2xs px-1.5 py-0.5 rounded font-medium bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 shrink-0">
                    未保存
                  </span>
                )}
                {isPdfFile && (
                  <span className="text-size-2xs px-1.5 py-0.5 rounded font-medium bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400 shrink-0">
                    PDF
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                {/* Open with system app */}
                <button
                  onClick={() => window.miqi.files.openExternal(currentPath)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs text-[var(--accent)] hover:bg-[var(--accent-soft)] transition-colors"
                  title="用系统默认应用打开"
                >
                  <ExternalLink size={12} />
                  <span>系统应用打开</span>
                </button>
                {/* Open containing folder */}
                <button
                  onClick={() => window.miqi.files.openContainingFolder(currentPath)}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                  title="打开所在文件夹"
                >
                  <FolderSearch size={12} />
                  <span>打开文件夹</span>
                </button>
                {!isPdfFile && (
                  <>
                    {/* Copy all button */}
                    <button
                      onClick={handleCopyAll}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                    >
                      {copiedAll ? <Check size={12} /> : <Copy size={12} />}
                      <span>{copiedAll ? '已复制' : '复制全部'}</span>
                    </button>
                    <button
                      onClick={handleSave}
                      disabled={!isUnsaved || saving}
                      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                        isUnsaved
                          ? 'bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]'
                          : 'bg-[var(--surface-muted)] text-[var(--text-muted)] cursor-not-allowed'
                      }`}
                    >
                      <Save size={12} />
                      {saving ? '保存中…' : '保存'}
                    </button>
                    {(isMdFile || isHtmlFile) && (
                      <div className="flex items-center gap-1 rounded-md border border-[var(--border-subtle)] overflow-hidden">
                        <button
                          onClick={() => setPreviewMode(false)}
                          className={`px-2 py-0.5 text-xs ${!previewMode ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'}`}
                        >
                          编辑
                        </button>
                        <button
                          onClick={() => setPreviewMode(true)}
                          className={`px-2 py-0.5 text-xs ${previewMode ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'}`}
                        >
                          预览
                        </button>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
            {error && (
              <div className="shrink-0 flex items-center gap-2 px-4 py-2 bg-red-50 dark:bg-red-900/20 border-b border-red-200 dark:border-red-900/30 text-xs text-[var(--danger)]">
                <AlertCircle size={12} />
                {error}
              </div>
            )}

            {/* Editor area */}
            <div className="flex-1 overflow-hidden">
              {fileLoading ? (
                <div className="flex items-center justify-center h-full">
                  <div className="text-sm text-[var(--text-muted)]">正在加载文件…</div>
                </div>
              ) : isPdfFile && binaryUrl ? (
                <iframe
                  src={binaryUrl}
                  className="w-full h-full border-0"
                  title={currentPath ?? 'PDF Viewer'}
                />
              ) : isMdFile && previewMode ? (
                <div className="w-full h-full overflow-y-auto px-5 py-4 text-[15px] leading-[1.7] text-[var(--text)] prose prose-sm max-w-none bg-transparent">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                </div>
              ) : isHtmlFile && previewMode ? (
                <SandboxHtmlFrame
                  html={content}
                  className="w-full h-full border-0"
                  maxHeight="100%"
                />
              ) : (
                <textarea
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  className={`w-full h-full resize-none bg-transparent text-[var(--text)] outline-none font-mono p-5 leading-relaxed ${
                    isMdFile ? 'text-[15px] leading-[1.7]' : 'text-[13px]'
                  }`}
                  placeholder="文件为空"
                  spellCheck={false}
                />
              )}
            </div>
          </>
        ) : (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-[var(--text-muted)]">
            <FolderOpen size={32} strokeWidth={1.5} />
            <div className="text-sm">从左侧选择文件进行编辑</div>
          </div>
        )}
      </div>

      {/* Unsaved-switch confirmation dialog */}
      {showConfirm && (
        <ConfirmDialog
          title="有未保存的更改"
          message={
            <span>
              文件 <code className="text-[var(--text)] font-mono">{currentPath}</code>{' '}
              有未保存的更改，确认丢弃并打开其他文件？
            </span>
          }
          confirmLabel="丢弃并切换"
          onConfirm={() => confirmSwitch(true)}
          onCancel={() => confirmSwitch(false)}
        />
      )}

      {/* File operation dialogs */}
      {actionTarget && actionTarget.type === 'rename' ? (
        <InputDialog
          open={!!actionTarget}
          onOpenChange={(o) => {
            if (!o) setActionTarget(null);
          }}
          title="重命名"
          label="新名称"
          defaultValue={actionTarget.currentName}
          onConfirm={handleRename}
        />
      ) : actionTarget && (actionTarget.type === 'newFile' || actionTarget.type === 'newFolder') ? (
        <InputDialog
          open={!!actionTarget}
          onOpenChange={(o) => {
            if (!o) setActionTarget(null);
          }}
          title={actionTarget.type === 'newFile' ? '新建文件' : '新建文件夹'}
          label={actionTarget.type === 'newFile' ? '文件名' : '文件夹名'}
          onConfirm={handleCreate}
        />
      ) : null}

      {/* Delete confirm */}
      {deleteTarget && (
        <ConfirmDialog
          title="删除"
          message={`确定删除${deleteTarget.isDir ? '目录' : '文件'} "${deleteTarget.path}"？此操作不可撤销。`}
          danger
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FileTree recursive component
// ---------------------------------------------------------------------------

function FileTree({
  node,
  onSelect,
  selectedPath,
  onNewFile,
  onNewFolder,
  onRename,
  onDelete,
  depth = 0,
}: {
  node: FileNode;
  onSelect: (path: string) => void;
  selectedPath: string | null;
  onNewFile: (parentPath: string) => void;
  onNewFolder: (parentPath: string) => void;
  onRename: (nodePath: string, currentName: string) => void;
  onDelete: (path: string, isDir: boolean) => void;
  depth?: number;
}) {
  const [open, setOpen] = useState(depth < 1);

  if (node.is_dir) {
    const children = node.children ?? [];
    return (
      <div>
        <ContextMenu
          items={[
            { label: '新建文件', onSelect: () => onNewFile(node.path) },
            { label: '新建文件夹', onSelect: () => onNewFolder(node.path) },
            { label: '重命名', divider: true, onSelect: () => onRename(node.path, node.name) },
            { label: '复制路径', onSelect: () => navigator.clipboard.writeText(node.path) },
            {
              label: '打开所在文件夹',
              divider: true,
              onSelect: () => window.miqi.files.openContainingFolder(node.path),
            },
            {
              label: '删除',
              danger: true,
              divider: true,
              onSelect: () => onDelete(node.path, true),
            },
          ]}
        >
          {({ onContextMenu }) => (
            <div className="group flex items-center gap-0.5" onContextMenu={onContextMenu}>
              <button
                onClick={() => setOpen(!open)}
                className="flex items-center gap-1.5 flex-1 text-left px-1.5 py-1 rounded-md text-xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] transition-colors"
              >
                {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                <Folder size={12} />
                <span className="truncate font-medium">{node.name}</span>
              </button>
              {/* Action buttons on hover */}
              <div className="hidden group-hover:flex items-center gap-0.5 pr-1 shrink-0">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onNewFile(node.path);
                  }}
                  className="p-0.5 rounded text-[var(--text-faint)] hover:text-[var(--accent)] hover:bg-[var(--surface-muted)] transition-colors"
                  title="新建文件"
                >
                  <FilePlus size={11} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onNewFolder(node.path);
                  }}
                  className="p-0.5 rounded text-[var(--text-faint)] hover:text-[var(--accent)] hover:bg-[var(--surface-muted)] transition-colors"
                  title="新建文件夹"
                >
                  <FolderPlus size={11} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onRename(node.path, node.name);
                  }}
                  className="p-0.5 rounded text-[var(--text-faint)] hover:text-[var(--info)] hover:bg-[var(--surface-muted)] transition-colors"
                  title="重命名"
                >
                  <Pencil size={11} />
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(node.path, true);
                  }}
                  className="p-0.5 rounded text-[var(--text-faint)] hover:text-[var(--danger)] hover:bg-[var(--surface-muted)] transition-colors"
                  title="删除"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            </div>
          )}
        </ContextMenu>
        {open && children.length > 0 && (
          <div className="ml-3 border-l border-[var(--border-subtle)] pl-1.5">
            {children.map((child) => (
              <FileTree
                key={child.path}
                node={child}
                onSelect={onSelect}
                selectedPath={selectedPath}
                onNewFile={onNewFile}
                onNewFolder={onNewFolder}
                onRename={onRename}
                onDelete={onDelete}
                depth={depth + 1}
              />
            ))}
          </div>
        )}
        {open && children.length === 0 && (
          <div className="ml-7 text-size-2xs text-[var(--text-faint)] py-0.5">（空）</div>
        )}
      </div>
    );
  }

  const isSelected = selectedPath === node.path;
  return (
    <ContextMenu
      items={[
        { label: '打开文件', onSelect: () => onSelect(node.path) },
        { label: '在系统应用中打开', onSelect: () => window.miqi.files.openExternal(node.path) },
        { label: '重命名', divider: true, onSelect: () => onRename(node.path, node.name) },
        { label: '复制路径', onSelect: () => navigator.clipboard.writeText(node.path) },
        {
          label: '打开所在文件夹',
          onSelect: () => window.miqi.files.openContainingFolder(node.path),
        },
        { label: '删除', danger: true, divider: true, onSelect: () => onDelete(node.path, false) },
      ]}
    >
      {({ onContextMenu }) => (
        <div className="group flex items-center gap-0.5" onContextMenu={onContextMenu}>
          <button
            onClick={() => onSelect(node.path)}
            className={`flex items-center gap-1.5 flex-1 text-left px-1.5 py-1 rounded-md text-xs transition-colors ${
              isSelected
                ? 'bg-[var(--accent-soft)] text-[var(--accent)] font-medium'
                : 'text-[var(--text)] hover:bg-[var(--surface-muted)]'
            }`}
          >
            <FileText size={12} className="shrink-0" />
            <span className="truncate">{node.name}</span>
          </button>
          {/* Action buttons on hover */}
          <div className="hidden group-hover:flex items-center gap-0.5 pr-1 shrink-0">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onRename(node.path, node.name);
              }}
              className="p-0.5 rounded text-[var(--text-faint)] hover:text-[var(--info)] hover:bg-[var(--surface-muted)] transition-colors"
              title="重命名"
            >
              <Pencil size={11} />
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDelete(node.path, false);
              }}
              className="p-0.5 rounded text-[var(--text-faint)] hover:text-[var(--danger)] hover:bg-[var(--surface-muted)] transition-colors"
              title="删除"
            >
              <Trash2 size={11} />
            </button>
          </div>
        </div>
      )}
    </ContextMenu>
  );
}
