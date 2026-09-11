import { useState } from 'react';
import { X, CheckCircle, Loader2, TestTube2, Save } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { sanitizeUiMessage } from '../../../lib/sanitizeUiMessage';
import type { ProviderInfo } from '../../../../shared/ipc';
import { PROVIDER_DISPLAY_NAMES, PROVIDER_SUGGESTED_MODELS } from '../../../lib/providers';
import { ModelSelect } from './ModelSelect';
import { Modal } from '../../../components/shared';

/**
 * Provider 编辑弹窗（#835 合规收口后）。
 * 移除自配 API Key / Base URL / 自定义模型名文本输入；仅保留内置激活码
 * （DeepSeek 企业共享密钥）与模型下拉。
 */

interface EditSheetProps {
  provider: ProviderInfo;
  onClose: () => void;
  onSaved: () => void;
}

export function EditSheet({ provider, onClose, onSaved }: EditSheetProps) {
  const [model, setModel] = useState(provider.configured_model ?? '');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    message: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Built-in activation state
  const [activationCode, setActivationCode] = useState('');
  const [activating, setActivating] = useState(false);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [activationSuccess, setActivationSuccess] = useState(provider.builtin_activated ?? false);

  const handleActivate = async (): Promise<boolean> => {
    if (!activationCode.trim()) {
      setActivationError('请输入激活码');
      return false;
    }
    setActivating(true);
    setActivationError(null);
    try {
      const result = await window.miqi.providers.activate(provider.name, activationCode.trim());
      if (result.activated) {
        setActivationSuccess(true);
        // Auto-test and auto-set as default
        try {
          await window.miqi.providers.test(provider.name);
        } catch {
          /* test failure doesn't block activation */
        }
        const fallbackModel = `${provider.name}/${
          (PROVIDER_SUGGESTED_MODELS[provider.name] ?? [])[0] || 'deepseek-v4-flash'
        }`;
        try {
          await window.miqi.providers.update(
            provider.name,
            undefined,
            undefined,
            undefined,
            fallbackModel
          );
        } catch {
          /* activation as default can fail silently */
        }
        onSaved();
        return true;
      } else {
        setActivationError(result.error || '激活失败');
        return false;
      }
    } catch (err: unknown) {
      const msg = sanitizeUiMessage(err instanceof Error ? err.message : String(err));
      setActivationError(msg || '激活失败，请检查激活码');
      return false;
    } finally {
      setActivating(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      // 内置 provider 未激活时，先激活（输入激活码），否则只保存模型。
      if (provider.builtin_available && !activationSuccess) {
        if (activationCode.trim()) {
          const activated = await handleActivate();
          if (!activated) {
            setError('激活失败，请检查激活码后重试');
            return;
          }
          // handleActivate 已把默认模型设为内置 fallback 并刷新父级；
          // 用户未另选模型时不再补发一次空 update（后端会报
          // "No fields to update"，#929 review）。
          if (!model) {
            onSaved();
            onClose();
            return;
          }
        } else {
          setError('请输入激活码并点击"激活"');
          return;
        }
      }
      await window.miqi.providers.update(provider.name, undefined, null, null, model || undefined);
      onSaved();
      onClose();
    } catch (err: unknown) {
      const msg = sanitizeUiMessage(err instanceof Error ? err.message : String(err));
      setError(msg);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!provider.configured && !activationSuccess) {
      setTestResult({ ok: false, message: '请先激活内置密钥' });
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await window.miqi.providers.test(
        provider.name,
        undefined,
        undefined,
        model || provider.configured_model || undefined
      );
      setTestResult({
        ok: result.ok,
        message: result.ok ? '连接成功，已记录验证状态。' : '连接失败',
      });
      if (result.ok) onSaved();
    } catch (err: unknown) {
      const message = sanitizeUiMessage(err instanceof Error ? err.message : String(err));
      setTestResult({ ok: false, message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <Modal open onOpenChange={onClose} hideClose className="max-w-[480px] w-[480px] p-0">
      <div className="w-full max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-subtle)]">
          <div>
            <h2 className="text-sm font-semibold text-[var(--text)]">
              {PROVIDER_DISPLAY_NAMES[provider.name] ?? provider.display_name}
            </h2>
            <p className="text-xs text-[var(--text-muted)] mt-0.5">{provider.name}</p>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--text-faint)] hover:text-[var(--text)] transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 flex flex-col gap-4">
          {provider.builtin_available && (
            <div className="flex flex-col gap-3">
              <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
                API 来源
              </label>
              <div className="flex items-start gap-3 p-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface)]">
                <div className="flex-1 min-w-0">
                  <span className="text-sm text-[var(--text)]">推荐（无需API Key）</span>
                  {activationSuccess ? (
                    <div className="flex items-center gap-2 mt-1">
                      <p className="flex items-center gap-1.5 text-xs text-[var(--success)]">
                        <CheckCircle size={12} />
                        已激活
                      </p>
                      <button
                        type="button"
                        onClick={async () => {
                          // 走专用 deactivate 接口清空 api_key + 内置激活标记（#835 收口）
                          try {
                            await window.miqi.providers.deactivate(provider.name);
                            setActivationSuccess(false);
                            // 刷新父级列表：否则重开弹窗会从陈旧 prop 恢复
                            // 出「已激活」状态（#929 review）。
                            onSaved();
                          } catch (err: unknown) {
                            const msg = sanitizeUiMessage(
                              err instanceof Error ? err.message : String(err)
                            );
                            setError(msg || '取消激活失败');
                          }
                        }}
                        className="text-xs text-[var(--text-faint)] hover:text-[var(--danger)] underline transition-colors"
                      >
                        取消激活
                      </button>
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2 mt-2">
                      <div className="flex gap-2">
                        <input
                          type="password"
                          value={activationCode}
                          onChange={(e) => {
                            setActivationCode(e.target.value);
                            setActivationError(null);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') handleActivate();
                          }}
                          placeholder="输入激活码"
                          className="flex-1 px-3 py-1.5 rounded-md text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] placeholder-[var(--text-faint)] focus:outline-none focus:border-[var(--border-strong)] font-mono"
                          autoComplete="off"
                          spellCheck={false}
                          disabled={activating}
                        />
                        <button
                          onClick={handleActivate}
                          disabled={activating || !activationCode.trim()}
                          className="px-3 py-1.5 rounded-md bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-medium transition-colors disabled:opacity-50 shrink-0"
                        >
                          {activating ? <Loader2 size={13} className="animate-spin" /> : '激活'}
                        </button>
                      </div>
                      {activationError && (
                        <p className="text-xs text-[var(--danger)]">{activationError}</p>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
              默认模型
            </label>
            <ModelSelect value={model} onChange={setModel} />
            <p className="text-xs text-[var(--text-faint)]">修改此字段会更新全局默认模型</p>
          </div>

          {error && (
            <div className="rounded-lg px-3 py-2 bg-[var(--accent-soft)] text-xs text-[var(--danger)]">
              {error}
            </div>
          )}
          {testResult && (
            <div
              className={cn(
                'rounded-lg px-3 py-2 text-xs',
                testResult.ok
                  ? 'bg-[color-mix(in_srgb,var(--success)_15%,transparent)] text-[var(--success)]'
                  : 'bg-[var(--accent-soft)] text-[var(--danger)]'
              )}
            >
              {testResult.message}
            </div>
          )}
          <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2 text-xs text-[var(--text-muted)] leading-relaxed">
            保存 Provider
            配置后，当前运行中的会话可能仍在使用旧实例；如需确认新配置生效，请重新测试并按提示重启运行时或新建会话。
          </div>
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-t border-[var(--border-subtle)]">
          <button
            onClick={handleTest}
            disabled={testing}
            className="flex items-center gap-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors disabled:opacity-50"
          >
            {testing ? <Loader2 size={14} className="animate-spin" /> : <TestTube2 size={14} />}
            测试连接
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-sm text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-sm font-medium transition-colors disabled:opacity-50"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
              保存
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
