import React, { useState, useEffect } from 'react';
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
        <span className="text-xs text-[var(--text-faint)]">加载中...</span>
      </div>
    );

  return (
    <div className="p-4 max-w-2xl">
      <h2 className="text-sm font-semibold text-[var(--text)] mb-4 flex items-center gap-2">
        <Shield size={16} />
        权限
      </h2>

      {/* Filesystem Rules */}
      <section className="mb-5">
        <h3 className="text-xs font-semibold text-[var(--text-muted)] mb-2">文件系统规则</h3>
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
                <option value="read">读取</option>
                <option value="write">写入</option>
                <option value="none">禁止</option>
              </select>
              <label className="flex items-center gap-1.5 text-xs cursor-pointer text-text-muted">
                <input
                  type="checkbox"
                  checked={rule.recursive}
                  onChange={(e) => updateRule(i, 'recursive', e.target.checked)}
                  className="accent-[var(--accent)]"
                />
                递归
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
          <Plus size={12} /> 添加规则
        </button>
      </section>

      {/* Network Policy */}
      <section className="mb-5">
        <h3 className="text-xs font-semibold text-[var(--text-muted)] mb-2">网络策略</h3>
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
          <option value="allow_all">允许所有</option>
          <option value="block_all">全部阻止</option>
          <option value="allow_list">白名单模式</option>
        </select>
      </section>

      {/* Exec Approval */}
      <section className="mb-6">
        <h3 className="text-xs font-semibold text-[var(--text-muted)] mb-2">Shell 命令审批</h3>
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
          <option value="never">无需审批</option>
          <option value="dangerous">仅危险命令（默认）</option>
          <option value="always">始终需要审批</option>
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
        {saved ? '已保存' : '保存'}
      </button>
    </div>
  );
}
