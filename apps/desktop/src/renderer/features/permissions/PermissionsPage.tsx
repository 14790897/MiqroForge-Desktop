import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Shield, Plus, Trash2, Save } from 'lucide-react';
import { cn } from '../../lib/utils';

interface PathRule {
  path: string;
  mode: 'read' | 'write' | 'none';
  recursive: boolean;
}

interface PermissionsConfig {
  filesystem: {
    rules: PathRule[];
    default_mode: 'read' | 'write' | 'none';
  };
  network: 'allow_all' | 'block_all' | 'allow_list';
  exec_approval: 'never' | 'dangerous' | 'always';
}

const DEFAULT_CONFIG: PermissionsConfig = {
  filesystem: { rules: [], default_mode: 'read' },
  network: 'allow_all',
  exec_approval: 'dangerous',
};

const selectCls =
  'px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-subtle)] bg-[var(--surface)] text-[var(--text)] focus:outline-none focus:border-[var(--border-strong)]/50 transition-colors';

const inputCls =
  'flex-1 px-2.5 py-1.5 text-xs rounded-lg border border-[var(--border-subtle)] bg-[var(--surface)] text-[var(--text)] placeholder:text-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)]/50 transition-colors';

export function PermissionsPage() {
  const { t } = useTranslation();
  const [config, setConfig] = useState<PermissionsConfig>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const result = await window.miqi.permissions.get();
        if (result) setConfig({ ...DEFAULT_CONFIG, ...(result as unknown as PermissionsConfig) });
      } catch {
        /* use defaults */
      }
      setLoading(false);
    })();
  }, []);

  const addRule = () => {
    setConfig((prev) => ({
      ...prev,
      filesystem: {
        ...prev.filesystem,
        rules: [...prev.filesystem.rules, { path: '', mode: 'read' as const, recursive: true }],
      },
    }));
  };

  const updateRule = (index: number, field: keyof PathRule, value: string | boolean) => {
    setConfig((prev) => {
      const rules = [...prev.filesystem.rules];
      rules[index] = { ...rules[index], [field]: value };
      return { ...prev, filesystem: { ...prev.filesystem, rules } };
    });
  };

  const removeRule = (index: number) => {
    setConfig((prev) => ({
      ...prev,
      filesystem: {
        ...prev.filesystem,
        rules: prev.filesystem.rules.filter((_, i) => i !== index),
      },
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await window.miqi.permissions.update(config as unknown as Record<string, unknown>);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      console.error('Failed to save permissions:', e);
    }
    setSaving(false);
  };

  if (loading)
    return (
      <div className="p-4 flex items-center gap-2">
        <div className="w-4 h-4 border-2 border-[var(--border)] border-t-[var(--accent)] rounded-full animate-spin" />
        <span className="text-xs text-[var(--text-faint)]">{t('permissions.loading')}</span>
      </div>
    );

  return (
    <div className="p-4 max-w-2xl">
      <h2 className="text-sm font-semibold text-[var(--text)] mb-4 flex items-center gap-2">
        <Shield size={16} />
        {t('permissions.title')}
      </h2>

      {/* Filesystem Rules */}
      <section className="mb-5">
        <h3 className="text-xs font-semibold text-[var(--text-muted)] mb-2">
          {t('permissions.fsRules')}
        </h3>
        <div className="space-y-2 mb-2">
          {config.filesystem.rules.map((rule, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                type="text"
                value={rule.path}
                onChange={(e) => updateRule(i, 'path', e.target.value)}
                placeholder="/path/to/directory"
                className={inputCls}
              />
              <select
                value={rule.mode}
                onChange={(e) => updateRule(i, 'mode', e.target.value)}
                className={selectCls}
              >
                <option value="read">{t('permissions.modeRead')}</option>
                <option value="write">{t('permissions.modeWrite')}</option>
                <option value="none">{t('permissions.modeNone')}</option>
              </select>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer text-text-muted">
                <input
                  type="checkbox"
                  checked={rule.recursive}
                  onChange={(e) => updateRule(i, 'recursive', e.target.checked)}
                  className="accent-[var(--accent)]"
                />
                {t('permissions.recursive')}
              </label>
              <button
                onClick={() => removeRule(i)}
                className="text-[var(--danger)] hover:opacity-70 transition-opacity shrink-0"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={addRule}
          className="flex items-center gap-1 text-xs font-medium transition-colors hover:opacity-80"
          style={{ color: 'var(--accent)' }}
        >
          <Plus size={12} /> {t('permissions.addRule')}
        </button>
      </section>

      {/* Network Policy */}
      <section className="mb-5">
        <h3 className="text-xs font-semibold text-[var(--text-muted)] mb-2">
          {t('permissions.networkPolicy')}
        </h3>
        <select
          value={config.network}
          onChange={(e) =>
            setConfig((prev) => ({
              ...prev,
              network: e.target.value as PermissionsConfig['network'],
            }))
          }
          className={selectCls}
        >
          <option value="allow_all">{t('permissions.netAllowAll')}</option>
          <option value="block_all">{t('permissions.netBlockAll')}</option>
          <option value="allow_list">{t('permissions.netAllowList')}</option>
        </select>
      </section>

      {/* Exec Approval */}
      <section className="mb-6">
        <h3 className="text-xs font-semibold text-[var(--text-muted)] mb-2">
          {t('permissions.execApproval')}
        </h3>
        <select
          value={config.exec_approval}
          onChange={(e) =>
            setConfig((prev) => ({
              ...prev,
              exec_approval: e.target.value as PermissionsConfig['exec_approval'],
            }))
          }
          className={selectCls}
        >
          <option value="never">{t('permissions.execNever')}</option>
          <option value="dangerous">{t('permissions.execDangerous')}</option>
          <option value="always">{t('permissions.execAlways')}</option>
        </select>
      </section>

      {/* Save */}
      <button
        onClick={handleSave}
        disabled={saving}
        className={cn(
          'flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-medium transition duration-200',
          saved
            ? 'bg-[var(--success-bg)] text-[var(--success)]'
            : 'bg-[var(--accent)] text-[var(--accent-text)] hover:opacity-90'
        )}
      >
        {saving ? (
          <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
        ) : saved ? (
          '✓'
        ) : (
          <Save size={13} />
        )}
        {saved ? t('permissions.saved') : t('common.save')}
      </button>
    </div>
  );
}
