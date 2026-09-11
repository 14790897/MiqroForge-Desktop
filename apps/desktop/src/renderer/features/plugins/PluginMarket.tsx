import React, { useState, useEffect } from 'react';
import { Package, ToggleLeft, ToggleRight, Trash2, Puzzle } from 'lucide-react';
import { cn } from '../../lib/utils';

interface PluginInfo {
  name: string;
  version: string;
  description: string;
  status: 'active' | 'disabled' | 'error';
  scope: string;
}

export function PluginMarket() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const result = await window.miqi.plugins.list();
      setPlugins((result as unknown as { plugins: PluginInfo[] })?.plugins || []);
    } catch {
      /* ignore */
    }
    setLoading(false);
  };

  useEffect(() => {
    load();
  }, []);

  const handleToggle = async (name: string, currentStatus: string) => {
    try {
      await window.miqi.plugins.toggle(name, currentStatus !== 'active');
      await load();
    } catch (e) {
      console.error(e);
    }
  };

  const handleUninstall = async (name: string) => {
    if (!confirm(`确定卸载 ${name}？`)) return;
    try {
      await window.miqi.plugins.uninstall(name);
      await load();
    } catch (e) {
      console.error(e);
    }
  };

  const statusLabel = (s: string) => {
    switch (s) {
      case 'active':
        return '已启用';
      case 'error':
        return '错误';
      default:
        return '已禁用';
    }
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
        <Package size={16} />
        插件市场
      </h2>
      {plugins.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 px-4 rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-muted)]/30">
          <div className="w-10 h-10 rounded-full bg-[var(--surface-muted)] flex items-center justify-center mb-3 text-text-faint">
            <Puzzle size={18} />
          </div>
          <p className="text-sm font-medium text-[var(--text-muted)] mb-1">暂无已安装插件</p>
          <p className="text-xs text-[var(--text-faint)] text-center leading-relaxed">
            将插件添加到 ~/.miqi/plugins/ 或 &lt;workspace&gt;/.miqi/plugins/
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {plugins.map((p) => (
            <div
              key={p.name}
              className="rounded-lg px-3 py-2.5 transition-colors"
              style={{
                background: 'var(--surface-muted)',
                border: '1px solid var(--border-subtle)',
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-text">{p.name}</span>
                  <span className="text-size-2xs text-text-faint">v{p.version}</span>
                  <span
                    className={cn(
                      'text-size-2xs px-1.5 py-0.5 rounded',
                      p.status === 'active' && 'bg-[var(--success-bg)] text-[var(--success)]',
                      p.status === 'error' && 'bg-[var(--danger-bg)] text-[var(--danger)]',
                      p.status === 'disabled' &&
                        'bg-[var(--surface-muted)] text-[var(--text-faint)]'
                    )}
                  >
                    {statusLabel(p.status)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => handleToggle(p.name, p.status)}
                    style={{
                      color: p.status === 'active' ? 'var(--success)' : 'var(--text-faint)',
                    }}
                    className="hover:opacity-80 transition-opacity"
                  >
                    {p.status === 'active' ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                  </button>
                  <button
                    onClick={() => handleUninstall(p.name)}
                    className="text-[var(--danger)] hover:opacity-70 transition-opacity"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
              {p.description && <p className="text-xs mt-1 text-text-muted">{p.description}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
