import { useState, useEffect, useCallback } from 'react';
import {
  Search,
  Download,
  Check,
  ExternalLink,
  RefreshCw,
  Package,
  AlertCircle,
} from 'lucide-react';
import type { SkillSummary } from '../../../shared/ipc';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface RegistrySkill {
  name: string;
  description: string;
  [key: string]: unknown;
}

interface RegistryIndexEntry {
  name: string;
  description?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const REGISTRY_BASE = 'https://skills.sixiangjia.de';
const REGISTRY_INDEX = `${REGISTRY_BASE}/index.json`;
const REGISTRY_SEARCH = `${REGISTRY_BASE}/api/search`;
const SKILL_URL = (name: string) => `${REGISTRY_BASE}/${name}/SKILL.md`;

// ---------------------------------------------------------------------------
// SkillHubPage
// ---------------------------------------------------------------------------

interface SkillHubPageProps {
  installedSkills: SkillSummary[];
  onSkillInstalled: () => void;
}

export function SkillHubPage({ installedSkills, onSkillInstalled }: SkillHubPageProps) {
  const [query, setQuery] = useState('');
  const [skills, setSkills] = useState<RegistryIndexEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [installing, setInstalling] = useState<Set<string>>(new Set());
  const [installErrors, setInstallErrors] = useState<Map<string, string>>(new Map());

  const installedNames = new Set(installedSkills.map((s) => s.name));

  // Load all skills from registry
  const loadSkills = useCallback(async () => {
    setLoading(true);
    setError(null);
    const startedAt = Date.now();
    try {
      if (query.trim()) {
        const url = `${REGISTRY_SEARCH}?q=${encodeURIComponent(query.trim())}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`搜索失败 (${res.status})`);
        const data = await res.json();
        // Search API might return { results: [...] } or just an array
        const results: RegistryIndexEntry[] = Array.isArray(data)
          ? data
          : (data.results ?? data.skills ?? []);
        setSkills(results);
      } else {
        const res = await fetch(REGISTRY_INDEX);
        if (!res.ok) throw new Error(`加载失败 (${res.status})`);
        const data = await res.json();
        const items: RegistryIndexEntry[] = Array.isArray(data)
          ? data
          : (data.skills ?? data.results ?? []);
        setSkills(items);
      }
    } catch (e: any) {
      setError(e?.message ?? '加载失败');
    }
    // Keep the spinner visible for at least 400ms so fast loads don't
    // flash the empty state / jump between states.
    const elapsed = Date.now() - startedAt;
    const remain = 400 - elapsed;
    if (remain > 0) await new Promise((r) => setTimeout(r, remain));
    setLoading(false);
  }, [query]);

  // Load on mount and when query changes (debounced)
  useEffect(() => {
    const timer = setTimeout(loadSkills, 300);
    return () => clearTimeout(timer);
  }, [loadSkills]);

  // Install a skill
  const handleInstall = async (skill: RegistryIndexEntry) => {
    if (installing.has(skill.name)) return;

    setInstalling((prev) => new Set(prev).add(skill.name));
    setInstallErrors((prev) => {
      const next = new Map(prev);
      next.delete(skill.name);
      return next;
    });

    try {
      const url = SKILL_URL(skill.name);
      const res = await fetch(url);
      if (!res.ok) throw new Error(`下载失败 (${res.status})`);
      const content = await res.text();

      const uploadRes = await window.miqi.skills.upload(skill.name, content);
      if (!uploadRes.ok) {
        throw new Error(uploadRes.error ?? '安装失败');
      }

      onSkillInstalled();
    } catch (e: any) {
      setInstallErrors((prev) => {
        const next = new Map(prev);
        next.set(skill.name, e?.message ?? '安装失败');
        return next;
      });
    }

    setInstalling((prev) => {
      const next = new Set(prev);
      next.delete(skill.name);
      return next;
    });
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-[var(--background)]">
      {/* Header */}
      <div className="shrink-0 px-6 py-4 border-b border-[var(--border-subtle)]">
        <div className="flex items-center gap-3 mb-3">
          <Package size={20} className="text-[var(--accent)]" />
          <div>
            <h2 className="text-lg font-semibold text-[var(--text)]">SkillHub</h2>
            <p className="text-xs text-[var(--text-muted)]">浏览并安装来自社区注册表的技能</p>
          </div>
        </div>

        {/* Search bar */}
        <div className="relative max-w-md">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--text-faint)]"
          />
          <input
            type="text"
            placeholder="搜索技能…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-full pl-9 pr-10 py-2 text-sm rounded-lg border border-[var(--border)] bg-[var(--surface)] text-[var(--text)] placeholder:text-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)]"
          />
          {loading && (
            <RefreshCw
              size={14}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-faint)] animate-spin"
            />
          )}
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-auto p-6">
        {/* Error state */}
        {error && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <AlertCircle size={32} className="text-[var(--danger)]" strokeWidth={1.5} />
            <div className="text-sm text-[var(--danger)]">{error}</div>
            <button
              onClick={loadSkills}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              style={{
                background: 'var(--surface-muted)',
                color: 'var(--text-muted)',
              }}
            >
              <RefreshCw size={12} />
              重试
            </button>
          </div>
        )}

        {/* Loading */}
        {loading && !error && (
          <div className="flex items-center justify-center h-40">
            <RefreshCw size={20} className="text-[var(--text-faint)] animate-spin" />
          </div>
        )}

        {/* Empty */}
        {!loading && !error && skills.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-[var(--text-muted)]">
            <Package size={32} strokeWidth={1.5} />
            <div className="text-sm">
              {query.trim()
                ? '未找到匹配的技能，换个关键词试试'
                : '技能市场暂无可安装技能，本地技能请在「我的技能」中查看'}
            </div>
          </div>
        )}

        {/* Skill cards */}
        {!loading && !error && skills.length > 0 && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {skills.map((skill) => {
              const isInstalled = installedNames.has(skill.name);
              const isInstalling = installing.has(skill.name);
              const installError = installErrors.get(skill.name);

              return (
                <div
                  key={skill.name}
                  className="rounded-xl border p-4 transition-colors hover:border-[var(--accent)]"
                  style={{
                    background: 'var(--surface)',
                    borderColor: isInstalled ? 'var(--accent)' : 'var(--border-subtle)',
                  }}
                >
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="min-w-0 flex-1">
                      <h3 className="text-sm font-semibold text-[var(--text)] truncate">
                        {skill.name}
                      </h3>
                      {skill.description && (
                        <p className="text-xs text-[var(--text-muted)] mt-1 line-clamp-2">
                          {skill.description}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 flex flex-col items-end gap-1.5">
                      {/* Installed badge */}
                      {isInstalled && (
                        <span
                          className="inline-flex items-center gap-1 text-size-2xs px-1.5 py-0.5 rounded font-medium"
                          style={{
                            background: 'var(--accent-soft)',
                            color: 'var(--accent)',
                          }}
                        >
                          <Check size={10} />
                          已安装
                        </span>
                      )}

                      {/* Install button */}
                      {!isInstalled && (
                        <button
                          onClick={() => handleInstall(skill)}
                          disabled={isInstalling}
                          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-size-2xs font-medium transition-colors text-white disabled:opacity-50"
                          style={{ background: 'var(--accent)' }}
                        >
                          {isInstalling ? (
                            <>
                              <RefreshCw size={10} className="animate-spin" />
                              安装中
                            </>
                          ) : (
                            <>
                              <Download size={10} />
                              安装
                            </>
                          )}
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Install error */}
                  {installError && (
                    <div
                      className="text-size-2xs px-2 py-1 rounded mt-1"
                      style={{
                        background: 'var(--danger-bg)',
                        color: 'var(--danger)',
                      }}
                    >
                      {installError}
                    </div>
                  )}

                  {/* External link */}
                  <a
                    href={SKILL_URL(skill.name)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-size-2xs mt-2 transition-colors hover:underline text-text-faint"
                  >
                    <ExternalLink size={10} />
                    查看源文件
                  </a>
                </div>
              );
            })}
          </div>
        )}

        {/* Footer */}
        {!loading && !error && skills.length > 0 && (
          <div className="mt-6 pt-4 border-t border-[var(--border-subtle)] text-center">
            <p className="text-size-2xs text-[var(--text-faint)]">
              数据来源:{' '}
              <a
                href={REGISTRY_BASE}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:underline"
              >
                skills.sixiangjia.de
              </a>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
