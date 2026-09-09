import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
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
  { value: 'bug', labelKey: 'feedback.catCard.bug', icon: Bug },
  { value: 'question', labelKey: 'feedback.catCard.question', icon: HelpCircle },
  { value: 'suggestion', labelKey: 'feedback.catCard.suggestion', icon: Lightbulb },
  { value: 'other', labelKey: 'feedback.catCard.other', icon: FileText },
] as const;

// Badge labels shown in the entry list (no emoji prefix).
const CATEGORY_LABEL_KEYS: Record<string, string> = {
  bug: 'feedback.catName.bug',
  question: 'feedback.catName.question',
  suggestion: 'feedback.catName.suggestion',
  other: 'feedback.catName.other',
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
  const { t } = useTranslation();
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
    return !window.confirm(t('feedback.confirmDiscard'));
  }, [hasUnsavedContent, submitting, success, t]);

  const readFileAsDataUrl = (file: File): Promise<ScreenshotFile> =>
    new Promise((resolve, reject) => {
      if (!file.type.startsWith(ALLOWED_MIME_PREFIX)) {
        reject(
          new Error(
            t('feedback.errUnsupportedType', { type: file.type || t('feedback.errUnknownType') })
          )
        );
        return;
      }
      if (file.size > MAX_SCREENSHOT_BYTES) {
        reject(new Error(t('feedback.errTooLarge', { name: file.name })));
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
      reader.onerror = () => reject(new Error(t('feedback.errReadFile')));
      reader.readAsDataURL(file);
    });

  const addFiles = useCallback(
    async (files: FileList | File[]) => {
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
          setError(t('feedback.errRejected', { count: rejected }));
        }
        setScreenshots((prev) => {
          const cap = Math.max(0, MAX_SCREENSHOTS - prev.length);
          if (cap === 0) {
            setError(t('feedback.errMax', { count: MAX_SCREENSHOTS }));
            return prev;
          }
          if (accepted.length > cap) {
            setError(t('feedback.errPartial', { count: cap, max: MAX_SCREENSHOTS }));
          }
          return [...prev, ...accepted.slice(0, cap)];
        });
      } catch (e: any) {
        setError(e?.message || t('feedback.errProcess'));
      }
    },
    [t]
  );

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
        throw new Error(t('feedback.errSubmitUnconfirmed'));
      }
      setSuccess(true);
      setTimeout(() => {
        onSubmitted();
        onClose();
      }, 1500);
    } catch (e: any) {
      setError(e?.message || t('feedback.errSubmit'));
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
          <h3 className="text-lg font-semibold">{t('feedback.submit')}</h3>
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
            <p className="text-sm font-medium">{t('feedback.successTitle')}</p>
            <p className="text-xs text-[var(--muted-foreground)]">{t('feedback.successHint')}</p>
          </div>
        ) : (
          <>
            {/* Hints */}
            <div className="flex flex-col gap-1.5 mb-4 p-2.5 rounded-md bg-[var(--accent)]/5 border border-[var(--accent)]/15">
              <p className="text-size-2xs text-[var(--muted-foreground)]">
                {t('feedback.hintAutoAttach')}
              </p>
              <p className="text-size-2xs text-[var(--warning)]">{t('feedback.hintCopyFirst')}</p>
            </div>

            {/* Category */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                {t('feedback.catLabel')}
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
                    {t(opt.labelKey)}
                  </button>
                ))}
              </div>
            </div>

            {/* Title */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                {t('feedback.titleLabel')}
              </label>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={t('feedback.titlePlaceholder')}
                maxLength={200}
                className="w-full px-3 py-2 text-sm bg-[var(--muted)]/10 rounded-md border border-[var(--border)]
                           outline-none focus:border-[var(--border-strong)]"
              />
            </div>

            {/* Content */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                {t('feedback.contentLabel')}
              </label>
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={t('feedback.contentPlaceholder')}
                rows={5}
                maxLength={10000}
                className="w-full px-3 py-2 text-sm bg-[var(--muted)]/10 rounded-md border border-[var(--border)]
                           outline-none focus:border-[var(--border-strong)] resize-none"
              />
            </div>

            {/* Contact (optional) */}
            <div className="mb-4">
              <label className="block text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                {t('feedback.contactLabel')}
              </label>
              <input
                value={contact}
                onChange={(e) => setContact(e.target.value)}
                placeholder={t('feedback.contactPlaceholder')}
                maxLength={200}
                className="w-full px-3 py-2 text-sm bg-[var(--muted)]/10 rounded-md border border-[var(--border)]
                           outline-none focus:border-[var(--border-strong)]"
              />
            </div>

            {/* Screenshots */}
            <div className="mb-4">
              <label className="flex items-center justify-between text-xs font-medium text-[var(--muted-foreground)] mb-1.5">
                <span>{t('feedback.screenshotLabel')}</span>
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
                <p className="text-xs text-[var(--muted-foreground)]">{t('feedback.dropHint')}</p>
                <p className="text-size-2xs text-[var(--muted-foreground)] opacity-70">
                  {t('feedback.supportedFormats')}
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
                        title={t('feedback.remove')}
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
                {t('common.cancel')}
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
                    {t('feedback.submitting')}
                  </>
                ) : (
                  t('feedback.submitBtn')
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
  const { t } = useTranslation();
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
      setError(t('feedback.loadError'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-4 px-5 py-3 border-b border-[var(--border)] shrink-0">
        <MessageSquare size={18} className="text-[var(--muted-foreground)]" />
        <h2 className="text-lg font-semibold flex-1">{t('feedback.title')}</h2>
        <button
          onClick={() => setShowSubmitModal(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md
                     bg-[var(--accent)]/10 hover:bg-[var(--accent)]/20 text-[var(--accent)] transition-colors"
        >
          <Plus size={15} />
          {t('feedback.submit')}
        </button>
        <button
          onClick={load}
          className="p-1.5 rounded hover:bg-[var(--muted)]/20 text-[var(--muted-foreground)]"
          title={t('common.refresh')}
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
              {t('common.retry')}
            </button>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-16 text-center">
            <MessageSquare size={28} className="text-[var(--muted-foreground)] opacity-30" />
            <p className="text-sm text-[var(--muted-foreground)]">{t('feedback.empty')}</p>
            <p className="text-xs text-[var(--muted-foreground)] opacity-60">
              {t('feedback.emptyHint')}
            </p>
            <button
              onClick={() => setShowSubmitModal(true)}
              className="flex items-center gap-1.5 mt-2 px-4 py-2 text-sm rounded-md
                         bg-[var(--accent)]/10 hover:bg-[var(--accent)]/20 text-[var(--accent)]"
            >
              <Plus size={15} />
              {t('feedback.submitFirst')}
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
                          {CATEGORY_LABEL_KEYS[entry.category]
                            ? t(CATEGORY_LABEL_KEYS[entry.category])
                            : entry.category}
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
