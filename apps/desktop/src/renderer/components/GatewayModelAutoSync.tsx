import { useEffect, useRef } from 'react';
import { invalidateConfigCache } from '../lib/configCache';
import { GATEWAY_MODEL_ID } from '../features/providers/components/ModelQuickPanel';
import { useQraftStatus } from '../hooks/useQraftStatus';
import { useRuntime } from '../contexts/RuntimeContext';

/**
 * 登录后 AI 网关自动生效（#922 收尾）。
 *
 * 网关不是登录即启用的开关：运行时只在默认模型恰好是网关实测模型时才会
 * 把调用改道到平台网关（miqi/providers/factory.py）。登录本身不会写默认
 * 模型，用户会卡在「已开通但模型未设置」的状态。本组件在登录 + 网关
 * active 且默认模型为空时自动写入网关模型；已配置非空模型时不动它
 * （可能是有意直连），仅在「未设置」时兜底。
 */

/** 默认模型为空（未设置）时返回要自动写入的网关模型 id，否则返回 null。 */
export function gatewayModelToAutoSet(config: unknown): string | null {
  if (!config || typeof config !== 'object') return null;
  const agents = (config as Record<string, unknown>).agents;
  if (!agents || typeof agents !== 'object') return null;
  const defaults = (agents as Record<string, unknown>).defaults;
  if (!defaults || typeof defaults !== 'object') return null;
  const model = (defaults as Record<string, unknown>).model;
  if (typeof model === 'string' && model.trim() !== '') return null;
  return GATEWAY_MODEL_ID;
}

export function GatewayModelAutoSync() {
  const { loggedIn, gatewayActive } = useQraftStatus();
  const { status } = useRuntime();
  const attemptedRef = useRef(false);

  useEffect(() => {
    if (!loggedIn || !gatewayActive) {
      attemptedRef.current = false; // 退出登录/网关不可用后，下次登录允许重试
      return;
    }
    if (status.state !== 'running' || attemptedRef.current) return;
    attemptedRef.current = true;
    void (async () => {
      try {
        const config = await window.miqi.config.get();
        const modelId = gatewayModelToAutoSet(config);
        if (!modelId) return;
        await window.miqi.config.update({ agents: { defaults: { model: modelId } } });
        invalidateConfigCache();
      } catch {
        attemptedRef.current = false; // 保存失败 → 等下一次状态变化重试
      }
    })();
  }, [loggedIn, gatewayActive, status.state]);

  return null;
}
