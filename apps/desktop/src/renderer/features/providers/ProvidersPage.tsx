import { useState, useEffect, useCallback } from 'react';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import { Server, Loader2 } from 'lucide-react';
import type { ProviderInfo } from '../../../shared/ipc';
import { EditSheet } from './components/EditProviderSheet';
import { ModelQuickPanel } from './components/ModelQuickPanel';

/**
 * 「模型」设置页（#835 合规收口后）。
 * 移除 provider 滚动列表（网关/国际/国内/本地），只保留「默认模型」下拉框；
 * 内置 DeepSeek 激活入口保留在「编辑当前模型」弹窗中。
 */
export function ProvidersPage({ onGoToQraft }: { onGoToQraft: () => void }) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [editProvider, setEditProvider] = useState<ProviderInfo | null>(null);
  const [activeModel, setActiveModel] = useState('');
  const [activeProvider, setActiveProvider] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const result = await window.miqi.providers.list();
      setProviders(result.providers);
      setActiveModel(result.active_model ?? '');
      setActiveProvider(result.active_provider ?? null);
    } catch {
      // silent — runtime may not be running
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const activeProviderInfo = activeProvider
    ? providers.find((provider) => provider.name === activeProvider)
    : null;
  // 遗留 custom/* 默认模型解析不到 active_provider 时，激活按钮不能消失
  //（内置激活码是唯一的凭据入口，#929 review）：退到第一个可激活的内置
  // provider。
  const editTarget = activeProviderInfo ?? providers.find((p) => p.builtin_available) ?? null;

  return (
    <div className="flex flex-col h-full bg-[var(--background)]">
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface)] shrink-0">
        <div>
          <h1 className="text-base font-semibold text-[var(--text)]">模型</h1>
          <p
            className="text-xs text-[var(--text-muted)] mt-0.5"
            data-testid="providers-active-model"
          >
            {loading ? '加载中…' : `当前默认模型：${activeModel || '未设置'}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {editTarget && (
            <button
              onClick={() => setEditProvider(editTarget)}
              className="text-xs text-[var(--accent)] hover:text-[var(--accent-hover)] transition-colors px-2 py-1 rounded bg-[var(--accent-soft)]"
            >
              {editTarget === activeProviderInfo ? '编辑当前模型' : '激活内置模型'}
            </button>
          )}
          <button
            onClick={load}
            className="text-xs text-[var(--text-faint)] hover:text-[var(--text-muted)] transition-colors px-2 py-1 rounded"
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-sm text-[var(--text-faint)]">
            <Loader2 size={16} className="animate-spin mr-2" /> 正在加载…
          </div>
        ) : providers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-40 gap-2 text-sm text-[var(--text-faint)]">
            <Server size={24} />
            <span>MiQroForge 运行时未启动</span>
          </div>
        ) : (
          <ModelQuickPanel activeModel={activeModel} onSaved={load} onGoToQraft={onGoToQraft} />
        )}
      </div>

      {editProvider && (
        <ErrorBoundary
          fallback={(error, reset) => (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
              <div className="bg-[var(--surface-elevated)] border border-[var(--border)] rounded-xl shadow-xl p-6 max-w-sm">
                <p className="text-sm text-[var(--danger)] mb-3">
                  编辑面板加载失败: {error.message}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={reset}
                    className="px-3 py-1.5 rounded-md bg-[var(--accent)] text-white text-xs"
                  >
                    重试
                  </button>
                  <button
                    onClick={() => setEditProvider(null)}
                    className="px-3 py-1.5 rounded-md border border-[var(--border)] text-xs"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    关闭
                  </button>
                </div>
              </div>
            </div>
          )}
        >
          <EditSheet provider={editProvider} onClose={() => setEditProvider(null)} onSaved={load} />
        </ErrorBoundary>
      )}
    </div>
  );
}
