import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Search,
  Wrench,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Copy,
  Check,
  FolderOpen,
  Plus,
  Upload,
  Trash2,
  Lock,
  X,
  Package,
} from 'lucide-react';
import type { SkillSummary, SkillDetail } from '../../../shared/ipc';
import { SkillHubPage } from './SkillHubPage';

import { Modal } from '../../components/shared';

function CreateSkillModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  if (!open) return null;

  const handleCreate = async () => {
    setError('');
    if (!/^[a-z][a-z0-9-]*$/.test(name)) {
      setError(t('skills.errNameRule'));
      return;
    }
    setSaving(true);
    try {
      const res = await window.miqi.skills.create(name, description);
      if (res.ok) {
        onCreated(name);
        onClose();
      } else {
        setError(res.error ?? t('skills.createFail'));
      }
    } catch (e: any) {
      setError(e?.message ?? t('skills.createFail'));
    }
    setSaving(false);
  };

  return (
    <Modal
      open={open}
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
      hideClose
    >
      <div className="rounded-xl shadow-2xl w-full max-w-md mx-4 bg-surface">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border">
          <h2 className="text-base font-semibold text-text">{t('skills.modalTitle')}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-[var(--surface-muted)] text-text-muted"
          >
            <X size={16} />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-medium mb-1 text-text-muted">
              {t('skills.nameLabel')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-skill"
              className="w-full px-3 py-2 rounded-lg text-sm border"
              style={{
                background: 'var(--surface-muted)',
                color: 'var(--text)',
                borderColor: 'var(--border)',
              }}
            />
          </div>
          <div>
            <label className="block text-xs font-medium mb-1 text-text-muted">
              {t('skills.descLabel')}
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('skills.descPlaceholder')}
              className="w-full px-3 py-2 rounded-lg text-sm border"
              style={{
                background: 'var(--surface-muted)',
                color: 'var(--text)',
                borderColor: 'var(--border)',
              }}
            />
          </div>
          {error && (
            <div
              className="text-xs px-3 py-2 rounded"
              style={{ background: 'var(--danger-bg)', color: 'var(--danger)' }}
            >
              {error}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 px-5 py-4 border-t border-border">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-xs font-medium transition-colors hover:bg-[var(--surface-muted)] text-text-muted"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleCreate}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-xs font-medium text-white transition-colors"
            style={{ background: 'var(--accent)' }}
          >
            {saving ? t('skills.createSaving') : t('skills.createAction')}
          </button>
        </div>
      </div>
    </Modal>
  );
}

export function SkillsPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<'local' | 'skillhub'>('local');
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [copied, setCopied] = useState(false);
  const [openingFolder, setOpeningFolder] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadSkills = () => {
    window.miqi.skills
      .list()
      .then((res) => setSkills(res.skills))
      .catch(() => {});
  };

  useEffect(() => {
    loadSkills();
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!selectedName) {
      setDetail(null);
      return;
    }
    setDetailLoading(true);
    window.miqi.skills
      .get(selectedName)
      .then((d) => {
        setDetail(d);
        setDetailLoading(false);
      })
      .catch(() => setDetailLoading(false));
  }, [selectedName]);

  const filtered = skills.filter((s) => {
    if (!query.trim()) return true;
    const q = query.toLowerCase();
    return s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
  });

  // Match either forward or backslash separators so KWP skills are
  // recognized on Windows where the path arrives with backslashes.
  // (The server normalizes to forward slashes in skills.list, but we
  // still tolerate either for safety.)
  const KWP_PATH_RE = /[/\\]kwp[/\\]/;
  const builtin = filtered.filter((s) => s.source === 'builtin' && !KWP_PATH_RE.test(s.path));
  const workspace = filtered.filter((s) => s.source === 'workspace');
  const kwp = filtered.filter((s) => s.source === 'builtin' && KWP_PATH_RE.test(s.path));

  const handleCopyContent = () => {
    if (!detail) return;
    navigator.clipboard.writeText(detail.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleOpenFolder = async () => {
    if (!selectedName) return;
    setOpeningFolder(true);
    try {
      await window.miqi.skills.openFolder(selectedName);
    } catch {
      // ignore
    }
    setOpeningFolder(false);
  };

  const handleCreated = (name: string) => {
    loadSkills();
    setSelectedName(name);
  };

  const handleDelete = async (name: string) => {
    if (!window.confirm(t('skills.confirmDelete', { name }))) return;
    try {
      await window.miqi.skills.delete(name);
      if (selectedName === name) {
        setSelectedName(null);
        setDetail(null);
      }
      loadSkills();
    } catch {
      // ignore
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const content = await file.text();
      const name = file.name.replace(/\.(yml|yaml|md)$/i, '');
      const res = await window.miqi.skills.upload(name, content);
      if (res.ok) {
        loadSkills();
        setSelectedName(name);
      }
    } catch {
      // ignore
    }
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  if (loading && tab === 'local') {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-sm text-[var(--text-muted)]">{t('skills.loading')}</div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="shrink-0 flex items-center gap-0 border-b border-[var(--border-subtle)] bg-[var(--surface)]">
        <button
          onClick={() => setTab('local')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            tab === 'local'
              ? 'border-[var(--accent)] text-[var(--accent)]'
              : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]'
          }`}
        >
          <Wrench size={14} />
          {t('skills.localTab')}
        </button>
        <button
          onClick={() => setTab('skillhub')}
          className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
            tab === 'skillhub'
              ? 'border-[var(--accent)] text-[var(--accent)]'
              : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text)]'
          }`}
        >
          <Package size={14} />
          SkillHub
        </button>
      </div>

      {/* SkillHub view */}
      {tab === 'skillhub' && (
        <SkillHubPage installedSkills={skills} onSkillInstalled={loadSkills} />
      )}

      {/* Local skills view */}
      {tab === 'local' && (
        <div className="flex flex-1 overflow-hidden">
          {/* Left sidebar — skill list */}
          <div className="w-[280px] shrink-0 border-r border-[var(--border-subtle)] bg-[var(--surface)] flex flex-col">
            <div className="px-4 pt-4 pb-2 space-y-2">
              <div className="relative">
                <Search
                  size={14}
                  className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-faint)]"
                />
                <input
                  type="text"
                  placeholder={t('skills.searchPlaceholder')}
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 text-xs rounded-md border border-[var(--border)] bg-[var(--background)] text-[var(--text)] placeholder:text-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)]"
                />
              </div>
              <div className="flex gap-1.5">
                <button
                  onClick={() => setModalOpen(true)}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-md text-size-2xs font-medium transition-colors text-white"
                  style={{ background: 'var(--accent)' }}
                >
                  <Plus size={11} />
                  {t('skills.newSkill')}
                </button>
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                  className="flex items-center gap-1 px-2.5 py-1 rounded-md text-size-2xs font-medium transition-colors"
                  style={{
                    background: 'var(--surface-muted)',
                    color: 'var(--text-muted)',
                  }}
                >
                  <Upload size={11} />
                  {uploading ? t('skills.uploading') : t('skills.uploadYml')}
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".yml,.yaml,.md"
                  className="hidden"
                  onChange={handleFileUpload}
                />
              </div>
            </div>

            <div className="flex-1 overflow-auto px-2 pb-2">
              {filtered.length === 0 && (
                <div className="text-xs text-[var(--text-muted)] text-center mt-8">
                  {query.trim() ? t('skills.noMatch') : t('skills.noSkills')}
                </div>
              )}

              {builtin.length > 0 && (
                <SkillGroup
                  label={t('skills.groupBuiltin')}
                  skills={builtin}
                  selectedName={selectedName}
                  onSelect={setSelectedName}
                />
              )}
              {kwp.length > 0 && (
                <SkillGroup
                  label="Knowledge Work"
                  skills={kwp}
                  selectedName={selectedName}
                  onSelect={setSelectedName}
                />
              )}
              {workspace.length > 0 && (
                <SkillGroup
                  label={t('skills.groupWorkspace')}
                  skills={workspace}
                  selectedName={selectedName}
                  onSelect={setSelectedName}
                />
              )}
            </div>
          </div>

          {/* Right panel — skill detail */}
          <div className="flex-1 flex flex-col overflow-hidden bg-[var(--background)]">
            {detailLoading ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-sm text-[var(--text-muted)]">{t('skills.loadingDetail')}</div>
              </div>
            ) : detail ? (
              <div className="flex flex-col h-full overflow-auto">
                {/* Header */}
                <div className="shrink-0 px-6 py-5 border-b border-[var(--border-subtle)]">
                  <div className="flex items-center gap-2.5 mb-2">
                    <Wrench size={20} className="text-[var(--accent)]" />
                    <h2 className="text-lg font-semibold text-[var(--text)]">{detail.name}</h2>
                    <span
                      className="inline-flex items-center gap-1 text-size-2xs px-2 py-0.5 rounded-full font-medium"
                      style={
                        detail.source === 'builtin'
                          ? {
                              background:
                                'color-mix(in srgb, var(--surface-muted) 60%, transparent)',
                              color: 'var(--text-muted)',
                            }
                          : {
                              background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
                              color: 'var(--accent)',
                            }
                      }
                    >
                      {detail.source === 'builtin' ? <Lock size={10} /> : <FolderOpen size={10} />}
                      {detail.source === 'builtin'
                        ? t('skills.badgeBuiltin')
                        : t('skills.badgeWorkspace')}
                    </span>
                    {detail.available ? (
                      <span className="inline-flex items-center gap-1 text-size-2xs px-2 py-0.5 rounded-full font-medium bg-[var(--accent-soft)] text-[var(--accent)]">
                        <CheckCircle2 size={10} />
                        {t('skills.available')}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-size-2xs px-2 py-0.5 rounded-full font-medium bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400">
                        <AlertTriangle size={10} />
                        {t('skills.unavailable')}
                      </span>
                    )}
                    {/* Action buttons */}
                    <div className="ml-auto flex items-center gap-1">
                      {detail.source !== 'builtin' && (
                        <button
                          onClick={() => handleDelete(detail.name)}
                          className="flex items-center gap-1 px-2 py-1 rounded text-size-2xs text-[var(--danger)] hover:bg-[var(--danger-bg)] transition-colors"
                          title={t('skills.deleteSkill')}
                        >
                          <Trash2 size={12} />
                          <span>{t('skills.delete')}</span>
                        </button>
                      )}
                      <button
                        onClick={handleCopyContent}
                        className="flex items-center gap-1 px-2 py-1 rounded text-size-2xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                        title={t('skills.copySkillMd')}
                      >
                        {copied ? <Check size={12} /> : <Copy size={12} />}
                        <span>{copied ? t('skills.copied') : t('skills.copyContent')}</span>
                      </button>
                      <button
                        onClick={handleOpenFolder}
                        disabled={openingFolder}
                        className="flex items-center gap-1 px-2 py-1 rounded text-size-2xs text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                        title={t('skills.openFolderTitle')}
                      >
                        <FolderOpen size={12} />
                        <span>{openingFolder ? t('skills.opening') : t('skills.openFolder')}</span>
                      </button>
                    </div>
                  </div>
                  {detail.description && (
                    <p className="text-sm leading-relaxed text-[var(--text-muted)] mb-2 pl-0.5">
                      {detail.description}
                    </p>
                  )}
                  {!detail.available && detail.missingRequirements && (
                    <div className="text-size-2xs text-[var(--danger)] mt-1">
                      {t('skills.missing', { requirements: detail.missingRequirements })}
                    </div>
                  )}
                  <div className="text-size-2xs text-[var(--text-faint)] mt-1.5 font-mono">
                    {detail.path}
                  </div>
                </div>

                {/* Content — frontmatter stripped, body rendered as markdown */}
                <div className="flex-1 overflow-auto p-6">
                  <div className="settings-hover-card text-sm text-[var(--text)] leading-relaxed bg-[var(--surface)] border border-[var(--border-subtle)] rounded-lg p-4 prose prose-sm max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {detail.content.replace(/^---[\s\S]*?---\s*/, '')}
                    </ReactMarkdown>
                  </div>
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-3 text-[var(--text-muted)]">
                <Wrench size={32} strokeWidth={1.5} />
                <div className="text-sm">{t('skills.selectHint')}</div>
              </div>
            )}
          </div>

          <CreateSkillModal
            open={modalOpen}
            onClose={() => setModalOpen(false)}
            onCreated={handleCreated}
          />
        </div>
      )}
    </div>
  );
}

function SkillGroup({
  label,
  skills,
  selectedName,
  onSelect,
}: {
  label: string;
  skills: SkillSummary[];
  selectedName: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <div className="mb-3">
      <div className="text-size-2xs font-semibold text-[var(--text-faint)] uppercase tracking-wider px-2 mb-1">
        {label}
      </div>
      {skills.map((s) => (
        <button
          key={s.name}
          onClick={() => onSelect(s.name)}
          className={`w-full text-left px-2.5 py-2 rounded-lg text-sm transition-colors mb-0.5 ${
            selectedName === s.name
              ? 'bg-[var(--accent-soft)] text-[var(--accent)] font-medium'
              : 'text-[var(--text)] hover:bg-[var(--surface-muted)]'
          }`}
        >
          <div className="flex items-center gap-2">
            <span className="truncate flex-1">{s.name}</span>
            {!s.available && <XCircle size={12} className="text-[var(--danger)] shrink-0" />}
          </div>
          {s.description && (
            <div className="text-size-2xs text-[var(--text-muted)] truncate mt-0.5">
              {s.description}
            </div>
          )}
        </button>
      ))}
    </div>
  );
}
