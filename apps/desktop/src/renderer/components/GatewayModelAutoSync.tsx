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

/** 保存结果：saved=false 表示后端因期望值不匹配跳过（用户已在间隙选了模型）。 */
interface ConfigUpdateResult {
  saved: boolean;
  skipped?: string;
}

/**
 * 自动同步的保存动作（独立导出以便无 DOM 单测，#991 review）。
 *
 * 用 expectModel: '' 做比较并设置：后端只在磁盘上的默认模型仍为空时写入。
 * 配置快照读取与写入之间用户若已手动选了模型，后端返回 saved=false，
 * 这里直接放弃，保留用户更新的选择。
 */
export async function saveGatewayModelIfBlank(
  getConfig: () => Promise<unknown>,
  updateConfig: (
    config: Record<string, unknown>,
    expectModel?: string
  ) => Promise<ConfigUpdateResult | unknown>,
  invalidate: () => void
): Promise<void> {
  const config = await getConfig();
  const modelId = gatewayModelToAutoSet(config);
  if (!modelId) return;
  const result = await updateConfig({ agents: { defaults: { model: modelId } } }, '');
  if (result && typeof result === 'object' && (result as ConfigUpdateResult).saved === false) {
    return; // 被比较并设置拦截：用户的选择优先
  }
  invalidate();
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
    void saveGatewayModelIfBlank(
      () => window.miqi.config.get(),
      (config, expectModel) => window.miqi.config.update(config, expectModel),
      invalidateConfigCache
    ).catch(() => {
      attemptedRef.current = false; // 保存失败 → 等下一次状态变化重试
    });
  }, [loggedIn, gatewayActive, status.state]);

  return null;
}
