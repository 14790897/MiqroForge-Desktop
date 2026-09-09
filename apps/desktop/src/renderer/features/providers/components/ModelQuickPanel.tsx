import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Loader2, LogIn, Save, ShieldCheck } from 'lucide-react';
import { invalidateConfigCache } from '../../../lib/configCache';
import { sanitizeUiMessage } from '../../../lib/sanitizeUiMessage';
import { gatewayStatusText } from '../../../lib/qraftGateway';
import { useQraftStatus } from '../../../hooks/useQraftStatus';
import { i18n } from '../../../i18n';
import { ModelSelect } from './ModelSelect';

/**
 * 模型选择面板（#835 合规收口后）。
 * 仅保留「默认模型」下拉；移除 Provider 凭据（API Key / Base URL）配置。
 * 未登录时禁用模型选择并引导去 Qraft 登录。
 */

/** 经平台 AI 网关路由的模型（须与 miqi/providers/gateway.py 的 GATEWAY_MODEL 一致）。 */
export const GATEWAY_MODEL_ID = 'deepseek/deepseek-v4-flash';

const BADGE_BASE =
  'inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium';
const BADGE_MUTED = `${BADGE_BASE} bg-[var(--surface-muted)] text-[var(--text-muted)]`;
const BADGE_SUCCESS = `${BADGE_BASE} bg-[var(--success-bg)] text-[var(--success-text)]`;
const BADGE_INFO = `${BADGE_BASE} bg-[var(--info-bg)] text-[var(--info)]`;
const BADGE_WARNING = `${BADGE_BASE} bg-[var(--warning-bg)] text-[var(--warning)]`;

interface GatewayUsageDisplay {
  label: string;
  hint: string;
  badgeClass: string;
}

/**
 * 「是否使用 Qraft AI 网关」的展示状态（#922）。
 * 网关路由的前提（miqi/providers/factory.py）：deepseek + 网关实测模型 +
 * 登录凭据 active。渲染侧无法看到凭据文件与网关 origin，用登录态 +
 * aiGateway.status + 当前默认模型近似判定；凭据/origin 异常时运行时
 * 会落回直连，此处展示的是平台下发状态。
 */
function gatewayUsageDisplay(
  loggedIn: boolean,
  gatewayActive: boolean,
  aiGatewayKnown: boolean,
  aiGatewayStatus: string | undefined,
  activeModel: string
): GatewayUsageDisplay {
  if (!loggedIn) {
    return {
      label: i18n.t('providers.gwNotLoggedIn'),
      hint: i18n.t('providers.gwLoginHint'),
      badgeClass: BADGE_MUTED,
    };
  }
  if (gatewayActive) {
    if (activeModel === GATEWAY_MODEL_ID) {
      return {
        label: i18n.t('providers.gwActive'),
        hint: i18n.t('providers.gwActiveHint'),
        badgeClass: BADGE_SUCCESS,
      };
    }
    return {
      label: i18n.t('providers.gwOpen'),
      hint: i18n.t('providers.gwOpenHint', {
        model: activeModel || i18n.t('providers.notSet'),
        gateway: GATEWAY_MODEL_ID,
      }),
      badgeClass: BADGE_INFO,
    };
  }
  if (aiGatewayKnown && aiGatewayStatus) {
    const gw = gatewayStatusText(aiGatewayStatus);
    return { label: gw.label, hint: gw.hint, badgeClass: BADGE_WARNING };
  }
  return {
    label: i18n.t('providers.gwNotIssued'),
    hint: i18n.t('providers.gwNotIssuedHint'),
    badgeClass: BADGE_MUTED,
  };
}

interface ModelQuickPanelProps {
  activeModel: string;
  onSaved: () => void;
  onGoToQraft: () => void;
}

export function ModelQuickPanel({ activeModel, onSaved, onGoToQraft }: ModelQuickPanelProps) {
  const { t } = useTranslation();
  const [modelValue, setModelValue] = useState(activeModel || '');
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { loggedIn, gatewayActive, aiGatewayKnown, aiGatewayStatus } = useQraftStatus();
  // 可改模型 = 未登录时引导登录（现状）；登录时需网关 active；平台未下发网关
  // 状态（aiGatewayKnown=false）视为可用，避免误锁存量账号。
  const canUseModel = loggedIn && (gatewayActive || !aiGatewayKnown);
  const gatewayBlocked = loggedIn && aiGatewayKnown && !gatewayActive;
  const gatewayDisplay = gatewayUsageDisplay(
    loggedIn,
    gatewayActive,
    aiGatewayKnown,
    aiGatewayStatus,
    activeModel
  );

  useEffect(() => {
    if (activeModel) setModelValue(activeModel);
  }, [activeModel]);

  const handleSave = async () => {
    if (!modelValue) {
      setError(t('providers.errNoModel'));
      return;
    }
    // 模型 id 必须带 provider 前缀（如 "deepseek/deepseek-v4-flash"）。
    // 用 config.update 深合并只改 agents.defaults.model，不触碰 provider 的
    // api_base / extra_headers（避免 model-only 保存误重置它们，CodeRabbit #907）。
    if (!modelValue.includes('/')) {
      setError(t('providers.errPickValid'));
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
        <h2 className="text-sm font-semibold text-[var(--text)]">{t('providers.title')}</h2>
        <span className="text-xs text-[var(--text-faint)]">
          {t('providers.currentDefault')}
          <span className="font-mono text-[var(--text-muted)] ml-1">
            {activeModel || t('providers.notSet')}
          </span>
        </span>
      </div>

      <div className="flex flex-col gap-3">
        {/* 是否使用 Qraft AI 网关（#922）：始终可见，便于确认模型调用走网关还是直连 */}
        <div
          className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2"
          data-testid="model-gateway-status"
        >
          <ShieldCheck size={13} className="shrink-0 text-[var(--text-faint)]" />
          <span className="text-xs text-[var(--text-muted)]">{t('providers.aiGateway')}</span>
          <span className={gatewayDisplay.badgeClass}>{gatewayDisplay.label}</span>
          <span className="min-w-0 text-xs leading-relaxed text-[var(--text-faint)]">
            {gatewayDisplay.hint}
          </span>
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wide">
            {t('providers.defaultModel')}
          </label>
          {canUseModel ? (
            <ModelSelect value={modelValue} onChange={setModelValue} />
          ) : gatewayBlocked ? (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5">
              <span className="text-sm text-[var(--text-muted)]">{t('providers.gwBlocked')}</span>
              <button
                onClick={onGoToQraft}
                data-testid="model-quickpanel-go-gateway"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white transition-colors shrink-0"
              >
                <LogIn size={13} />
                {t('providers.viewAccount')}
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5">
              <span className="text-sm text-[var(--text-muted)]">{t('providers.loginHint')}</span>
              <button
                onClick={onGoToQraft}
                data-testid="model-quickpanel-go-login"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white transition-colors shrink-0"
              >
                <LogIn size={13} />
                {t('providers.goLogin')}
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
              {savedFlash ? t('providers.saved') : t('common.save')}
            </button>
            {savedFlash && (
              <span className="text-xs text-[var(--success)]">
                {t('providers.savedNewSession')}
              </span>
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
