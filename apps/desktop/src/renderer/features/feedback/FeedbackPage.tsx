import { useState, useEffect, useCallback, useRef } from 'react';
import {
  MessageSquare,
  Plus,
  RefreshCw,
  Trash2,
  Bug,
  HelpCircle,
  Lightbulb,
  FileText,
  X,
  Loader2,
  CheckCircle,
  AlertTriangle,
  ImagePlus,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import type { FeedbackEntry, FeedbackSubmitResult } from '../../../shared/ipc';

// ─── Constants ──────────────────────────────────────────────────────────────

const MAX_SCREENSHOTS = 5;
const MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024; // 10 MB per image
const ALLOWED_MIME_PREFIX = 'image/';

interface ScreenshotFile {
  dataUrl: string;
  name: string;
  size: number;
}

const CATEGORY_OPTIONS = [
  { value: 'bug', label: '🐛 缺陷报告', icon: Bug },
  { value: 'question', label: '❓ 使用问题', icon: HelpCircle },
  { value: 'suggestion', label: '💡 功能建议', icon: Lightbulb },
  { value: 'other', label: '📝 其他', icon: FileText },
] as const;

const CATEGORY_LABELS: Record<string, string> = {
  bug: '缺陷报告',
  question: '使用问题',
  suggestion: '功能建议',
  other: '其他',
};

const CATEGORY_ICONS: Record<string, typeof Bug> = {
  bug: Bug,
  question: HelpCircle,
  suggestion: Lightbulb,
  other: FileText,
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

import { formatRelativeTime } from '../../lib/formatTime';

// ─── Submit Modal ────────────────────────────────────────────────────────────

import { Modal } from '../../components/shared';

function SubmitModal({ onClose, onSubmitted }: { onClose: () => void; onSubmitted: () => void }) {
  const [category, setCategory] = useState<'bug' | 'question' | 'suggestion' | 'other'>('bug');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [contact, setContact] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [screenshots, setScreenshots] = useState<ScreenshotFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const canSubmit = title.trim().length > 0 && content.trim().length > 0 && !submitting;

  const hasUnsavedContent =
    title.trim().length > 0 ||
    content.trim().length > 0 ||
    contact.trim().length > 0 ||
    screenshots.length > 0;

  const onBeforeClose = useCallback(() => {
    if (submitting) return true;
    if (!hasUnsavedContent || success) return false;
    return !window.confirm('放弃已填写的内容？');
  }, [hasUnsavedContent, submitting, success]);

  const readFileAsDataUrl = (file: File): Promise<ScreenshotFile> =>
    new Promise((resolve, reject) => {
      if (!file.type.startsWith(ALLOWED_MIME_PREFIX)) {
        reject(new Error(`不支持的文件类型: ${file.type || '未知'}`));
        return;
      }
      if (file.size > MAX_SCREENSHOT_BYTES) {
        reject(new Error(`${file.name} 超过 10MB 限制`));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        resolve({
          dataUrl: String(reader.result),
          name: file.name,
          size: file.size,
        });
      };
      reader.onerror = () => reject(new Error('读取文件失败'));
      reader.readAsDataURL(file);
    });

  const addFiles = useCallback(async (files: FileList | File[]) => {
    const list = Array.from(files);
    setError(null);
    try {
      // Pre-decode all files (catching per-file errors so one bad file
      // doesn't drop the whole batch); then commit against the LATEST
      // state to enforce MAX_SCREENSHOTS under concurrent pastes/drops.
      const results = await Promise.allSettled(list.map(readFileAsDataUrl));
      const accepted: ScreenshotFile[] = [];
      for (const r of results) {
        if (r.status === 'fulfilled') accepted.push(r.value);
      }
      if (accepted.length < results.length) {
        const rejected = results.length - accepted.length;
        setError(`${rejected} 个文件未添加（不支持的类型或超过 10MB）`);
      }
      setScreenshots((prev) => {
        const cap = Math.max(0, MAX_SCREENSHOTS - prev.length);
        if (cap === 0) {
          setError(`最多 ${MAX_SCREENSHOTS} 张截图`);
          return prev;
        }
        if (accepted.length > cap) {
          setError(`仅添加了前 ${cap} 张，已达 ${MAX_SCREENSHOTS} 张上限`);
        }
        return [...prev, ...accepted.slice(0, cap)];
      });
    } catch (e: any) {
      setError(e?.message || '处理图片失败');
    }
  }, []);

  // Paste from clipboard (Ctrl+V) when modal is open
  useEffect(() => {
    if (success) return;
    const onPaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const imageFiles: File[] = [];
      for (let i = 0; i < items.length; i++) {
        if (items[i].type.startsWith(ALLOWED_MIME_PREFIX)) {
          const f = items[i].getAsFile();
          if (f) imageFiles.push(f);
        }
      }
      if (imageFiles.length > 0) {
        e.preventDefault();
        addFiles(imageFiles);
      }
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [addFiles, success]);

  const removeScreenshot = (idx: number) => {
    setScreenshots((prev) => prev.filter((_, i) => i !== idx));
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await window.miqi.feedback.submit({
        category,
        title: title.trim(),
        content: content.trim(),
        contact: contact.trim() || undefined,
        app_version: typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev',
        screenshots: screenshots.map((s) => s.dataUrl),
      });
      // The bridge always returns ok=true for successful submissions.  An
      // unexpected payload (e.g. from an older backend) is treated as a
      // failure rather than silently marking success.
      if (!result || result.ok !== true) {
        throw new Error('提交未确认（后端返回 ok=false）');
      }
      setSuccess(true);
      setTimeout(() => {
        onSubmitted();
        onClose();
      }, 1500);
    } catch (e: any) {
      setError(e?.message || '提交失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open
      onOpenChange={onClose}
      onBeforeClose={onBeforeClose}
      hideClose
      className="border-0 p-0 shadow-none bg-transparent max-w-lg"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-[var(--surface)] rounded-lg p-6 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto"
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-lg font-semibold">提交反馈</h3>
          <button
            onClick={() => {
              if (!submitting && !onBeforeClose()) onClose();
            }}
            disabled={submitting}
            className="p-1 rounded hover:bg-[var(--muted)]/20 text-[var(--muted-foreground)] disabled:opacity-50"
          >
            <X size={18} />
          </button>
        </div>

        {success ? (
          <div className="flex flex-col items-center gap-3 py-8">
            <CheckCircle size={40} className="text-green-400" />
            <p className="text-sm font-medium">提交成功！</p>
            <p className="text-xs text-[var(--muted-foreground)]">日志已自动附加并发送到飞书</p>
          </div>
        ) : (
          <>
            {/* Hints */}
            <div className="flex flex-col gap-1.5 mb-4 p-2.5 rounded-md bg-[var(--accent)]/5 border border-[var(--accent)]/15">
              <p className="text-size-2xs text-[var(--muted-foreground)]">
                日志将在提交时自动附加并发送到飞书
              </p>
              <p className="text-size-2xs text-[var(--warning)]">
                提示：建议先复制已填写的提示词，避免因意外关闭而丢失
              </p>
            </div>

            {/* Category */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                类别
              </label>
              <div className="grid grid-cols-2 gap-2">
                {CATEGORY_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setCategory(opt.value)}
                    className={cn(
                      'flex items-center gap-2 px-3 py-2 text-sm rounded-md border transition-colors',
                      category === opt.value
                        ? 'border-[var(--accent)] bg-[var(--accent)]/10 text-[var(--accent)]'
                        : 'border-[var(--border)] hover:bg-[var(--muted)]/10'
                    )}
                  >
                    <opt.icon size={15} />
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Title */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                标题
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="简要描述你的问题或建议"
                maxLength={200}
                className="w-full px-3 py-2 text-sm bg-[var(--muted)]/10 rounded-md border border-[var(--border)]
                           outline-none focus:border-[var(--border-strong)]"
              />
            </div>

            {/* Content */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                详细描述
              </label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="请详细描述你的问题或建议..."
                rows={5}
                maxLength={10000}
                className="w-full px-3 py-2 text-sm bg-[var(--muted)]/10 rounded-md border border-[var(--border)]
                           outline-none focus:border-[var(--border-strong)] resize-none"
              />
            </div>

            {/* Contact (optional) */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                联系方式（选填）
              </label>
              <input
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                placeholder="邮箱或飞书账号，方便我们联系你"
                maxLength={200}
                className="w-full px-3 py-2 text-sm bg-[var(--muted)]/10 rounded-md border border-[var(--border)]
                           outline-none focus:border-[var(--border-strong)]"
              />
            </div>

            {/* Screenshots */}
            <div className="mb-4">
              <label className="flex items-center justify-between text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                <span>截图（选填，可拖入 / 粘贴 / 点击上传）</span>
                <span className="text-size-2xs opacity-70">
                  {screenshots.length}/{MAX_SCREENSHOTS}
                </span>
              </label>

              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  if (e.dataTransfer?.files?.length) {
                    addFiles(e.dataTransfer.files);
                  }
                }}
                onClick={() => fileInputRef.current?.click()}
                className={cn(
                  'flex flex-col items-center justify-center gap-1.5 py-4 px-3 rounded-md border border-dashed cursor-pointer transition-colors',
                  dragOver
                    ? 'border-[var(--accent)] bg-[var(--accent)]/5'
                    : 'border-[var(--border)] hover:border-[var(--accent)]/50 hover:bg-[var(--muted)]/5'
                )}
              >
                <ImagePlus size={20} className="text-[var(--muted-foreground)]" />
                <p className="text-xs text-[var(--muted-foreground)]">
                  拖入图片 / 粘贴 (Ctrl+V) / 点击选择
                </p>
                <p className="text-size-2xs text-[var(--muted-foreground)] opacity-70">
                  支持 PNG / JPG / GIF / WebP，单张 ≤ 10MB
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files?.length) addFiles(e.target.files);
                    e.target.value = '';
                  }}
                />
              </div>

              {screenshots.length > 0 && (
                <div className="grid grid-cols-3 gap-2 mt-2">
                  {screenshots.map((s, idx) => (
                    <div
                      key={idx}
                      className="relative group rounded-md overflow-hidden border border-[var(--border)] aspect-video bg-[var(--muted)]/10"
                    >
                      <img src={s.dataUrl} alt={s.name} className="w-full h-full object-cover" />
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeScreenshot(idx);
                        }}
                        className="absolute top-1 right-1 p-1 rounded-full bg-black/60 text-white opacity-0 group-hover:opacity-100 transition-opacity"
                        title="移除"
                      >
                        <X size={12} />
                      </button>
                      <div className="absolute bottom-0 left-0 right-0 px-1.5 py-0.5 bg-black/60 text-size-2xs text-white truncate">
                        {(s.size / 1024).toFixed(0)} KB
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {error && (
              <div className="flex items-center gap-2 mb-4 p-2.5 rounded-md bg-red-500/10 border border-red-500/20">
                <AlertTriangle size={14} className="text-red-400 shrink-0" />
                <p className="text-xs text-red-400">{error}</p>
              </div>
            )}

            {/* Actions */}
            <div className="flex gap-3 justify-end">
              <button
                onClick={onClose}
                disabled={submitting}
                className="px-4 py-2 text-sm rounded-md border border-[var(--border)] hover:bg-[var(--muted)]/30 disabled:opacity-50"
              >
                取消
              </button>
              <button
                onClick={handleSubmit}
                disabled={!canSubmit}
                className={cn(
                  'flex items-center gap-2 px-4 py-2 text-sm rounded-md transition-colors',
                  canSubmit
                    ? 'bg-[var(--accent)] text-white hover:opacity-90'
                    : 'bg-[var(--muted)]/20 text-[var(--muted-foreground)] cursor-not-allowed'
                )}
              >
                {submitting ? (
                  <>
                    <Loader2 size={14} className="animate-spin" />
                    提交中...
                  </>
                ) : (
                  '提交'
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}

// ─── FeedbackPage ────────────────────────────────────────────────────────────

export function FeedbackPage() {
  const [entries, setEntries] = useState<FeedbackEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await window.miqi.feedback.list({ limit: 50 });
      setEntries(res?.entries ?? []);
    } catch {
      setError('加载反馈记录失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-4 px-5 py-3 border-b border-[var(--border)] shrink-0">
        <MessageSquare size={18} className="text-[var(--muted-foreground)]" />
        <h2 className="text-lg font-semibold flex-1">用户反馈</h2>
        <button
          onClick={() => setShowSubmitModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md
                     bg-[var(--accent)]/10 hover:bg-[var(--accent)]/20 text-[var(--accent)] transition-colors"
        >
          <Plus size={15} />
          提交反馈
        </button>
        <button
          onClick={load}
          className="p-1.5 rounded hover:bg-[var(--muted)]/20 text-[var(--muted-foreground)]"
          title="刷新"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {loading && entries.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 size={20} className="animate-spin text-[var(--muted-foreground)]" />
          </div>
        ) : error ? (
          <div className="flex flex-col items-center gap-2 py-12 text-center">
            <AlertTriangle size={24} className="text-[var(--muted-foreground)] opacity-40" />
            <p className="text-sm text-[var(--muted-foreground)]">{error}</p>
            <button onClick={load} className="text-xs text-[var(--accent)] hover:underline mt-1">
              重试
            </button>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-16 text-center">
            <MessageSquare size={28} className="text-[var(--muted-foreground)] opacity-30" />
            <p className="text-sm text-[var(--muted-foreground)]">暂无反馈记录</p>
            <p className="text-xs text-[var(--muted-foreground)] opacity-60">
              提交反馈将自动附加日志并发送到飞书
            </p>
            <button
              onClick={() => setShowSubmitModal(true)}
              className="flex items-center gap-1.5 mt-2 px-4 py-2 text-sm rounded-md
                         bg-[var(--accent)]/10 hover:bg-[var(--accent)]/20 text-[var(--accent)]"
            >
              <Plus size={15} />
              提交第一条反馈
            </button>
          </div>
        ) : (
          <div className="divide-y divide-[var(--border)]">
            {entries.map((entry) => {
              const Icon = CATEGORY_ICONS[entry.category] || FileText;
              return (
                <div
                  key={entry.id}
                  className="px-5 py-3.5 hover:bg-[var(--muted)]/5 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <Icon size={16} className="mt-0.5 text-[var(--muted-foreground)] shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--muted)]/10 text-[var(--muted-foreground)]">
                          {CATEGORY_LABELS[entry.category] || entry.category}
                        </span>
                        <span className="text-sm font-medium truncate">{entry.title}</span>
                      </div>
                      <p className="text-xs text-[var(--muted-foreground)] line-clamp-2 mb-1.5">
                        {entry.content}
                      </p>
                      <div className="flex items-center gap-2 text-size-2xs text-[var(--muted-foreground)]">
                        <span>{formatRelativeTime(entry.created_at)}</span>
                        {entry.contact && <span>· {entry.contact}</span>}
                        {entry.app_version && <span>· v{entry.app_version}</span>}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Submit modal */}
      {showSubmitModal && (
        <SubmitModal onClose={() => setShowSubmitModal(false)} onSubmitted={load} />
      )}
    </div>
  );
}
