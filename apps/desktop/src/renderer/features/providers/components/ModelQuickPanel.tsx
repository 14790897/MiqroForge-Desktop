import { useEffect, useState } from 'react';
import { Check, Loader2, LogIn, Save } from 'lucide-react';
import { invalidateConfigCache } from '../../../lib/configCache';
import { sanitizeUiMessage } from '../../../lib/sanitizeUiMessage';
import { useQraftStatus } from '../../../hooks/useQraftStatus';
import { ModelSelect } from './ModelSelect';

/**
 * 模型选择面板（#835 合规收口后）。
 * 仅保留「默认模型」下拉；移除 Provider 凭据（API Key / Base URL）配置。
 * 未登录时禁用模型选择并引导去 Qraft 登录。
 */

interface ModelQuickPanelProps {
  activeModel: string;
  onSaved: () => void;
  onGoToQraft: () => void;
}

export function ModelQuickPanel({ activeModel, onSaved, onGoToQraft }: ModelQuickPanelProps) {
  const [modelValue, setModelValue] = useState(activeModel || '');
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { loggedIn, gatewayActive, aiGatewayKnown } = useQraftStatus();
  // 可改模型 = 未登录时引导登录（现状）；登录时需网关 active；平台未下发网关
  // 状态（aiGatewayKnown=false）视为可用，避免误锁存量账号。
  const canUseModel = loggedIn && (gatewayActive || !aiGatewayKnown);
  const gatewayBlocked = loggedIn && aiGatewayKnown && !gatewayActive;

  useEffect(() => {
    if (activeModel) setModelValue(activeModel);
  }, [activeModel]);

  const handleSave = async () => {
    if (!modelValue) {
      setError('请先选择模型');
      return;
    }
    // 模型 id 必须带 provider 前缀（如 "deepseek/deepseek-v4-flash"）。
    // 用 config.update 深合并只改 agents.defaults.model，不触碰 provider 的
    // api_base / extra_headers（避免 model-only 保存误重置它们，CodeRabbit #907）。
    if (!modelValue.includes('/')) {
      setError('请从下拉列表选择有效模型');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await window.miqi.config.update({ agents: { defaults: { model: modelValue.trim() } } });
      invalidateConfigCache();
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 2500);
      onSaved();
    } catch (err: unknown) {
      setError(sanitizeUiMessage(err instanceof Error ? err.message : String(err)));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--surface)]">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-[var(--text)]">模型设置</h2>
        <span className="text-xs text-[var(--text-faint)]">
          当前默认模型：
          <span className="font-mono text-[var(--text-muted)] ml-1">{activeModel || '未设置'}</span>
        </span>
      </div>

      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
            默认模型
          </label>
          {canUseModel ? (
            <ModelSelect value={modelValue} onChange={setModelValue} />
          ) : gatewayBlocked ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5">
              <span className="text-sm text-[var(--text-muted)]">
                AI 网关未就绪（平台开通中或不可用），暂不可选模型
              </span>
              <button
                onClick={onGoToQraft}
                data-testid="model-quickpanel-go-gateway"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white transition-colors shrink-0"
              >
                <LogIn size={13} />
                查看平台账号
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5">
              <span className="text-sm text-[var(--text-muted)]">登录后使用平台内置模型</span>
              <button
                onClick={onGoToQraft}
                data-testid="model-quickpanel-go-login"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white transition-colors shrink-0"
              >
                <LogIn size={13} />
                去登录
              </button>
            </div>
          )}
        </div>

        {canUseModel && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white transition-colors disabled:opacity-50"
            >
              {saving ? (
                <Loader2 size={14} className="animate-spin" />
              ) : savedFlash ? (
                <Check size={14} />
              ) : (
                <Save size={14} />
              )}
              {savedFlash ? '已保存' : '保存'}
            </button>
            {savedFlash && (
              <span className="text-xs text-[var(--success)]">已保存，新会话立即生效</span>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-lg px-3 py-2 bg-[var(--accent-soft)] text-xs text-[var(--danger)]">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
