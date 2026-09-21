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
 * active 且当前默认模型不可用时自动写入网关模型；已可用的模型（内置激活、
 * 已配置的其他网关兜底）不动 —— 尊重用户已有的选择。
 */

/** 读 config 快照里的当前默认模型；形态非法或未设置时返回空串。 */
export function currentDefaultModel(config: unknown): string {
  if (!config || typeof config !== 'object') return '';
  const agents = (config as Record<string, unknown>).agents;
  if (!agents || typeof agents !== 'object') return '';
  const defaults = (agents as Record<string, unknown>).defaults;
  if (!defaults || typeof defaults !== 'object') return '';
  const model = (defaults as Record<string, unknown>).model;
  return typeof model === 'string' ? model.trim() : '';
}

/**
 * 默认模型需要自动兜底为网关模型时返回该 id，否则返回 null。
 *
 * 判「可用」用 providers.list 的 active_model_resolvable（与运行时同一套
 * 判定，含网关路由）。全新安装的默认值 anthropic/claude-opus-4-5 是 schema
 * 默认值、config.get 会把它带出来永不空，因此只判空是永远不触发的
 * （#1172 实测）；这里改为按可用性判定：空值始终兜底，非空值仅在明确
 * 不可解析时替换。undefined（旧版 bridge 无该字段）时非空值不动。
 */
export function gatewayModelToAutoSet(
  current: string,
  resolvable: boolean | undefined
): string | null {
  if (!current) return GATEWAY_MODEL_ID;
  if (current === GATEWAY_MODEL_ID) return null;
  if (resolvable === undefined) return null;
  return resolvable ? null : GATEWAY_MODEL_ID;
}

/** 保存结果：saved=false 表示后端因期望值不匹配跳过（用户已在间隙选了模型）。 */
interface ConfigUpdateResult {
  saved: boolean;
  skipped?: string;
}

/**
 * 自动同步的保存动作（独立导出以便无 DOM 单测，#991 review）。
 *
 * 用当前模型值做 expectModel 比较并设置：后端只在磁盘上的默认模型仍与
 * 快照一致时写入。读取快照与写入之间用户若已手动改了模型，后端返回
 * saved=false，这里直接放弃，保留用户更新的选择。
 */
export async function saveGatewayModelIfUnusable(
  getConfig: () => Promise<unknown>,
  listProviders: () => Promise<{ active_model_resolvable?: boolean }>,
  updateConfig: (
    config: Record<string, unknown>,
    expectModel?: string
  ) => Promise<ConfigUpdateResult | unknown>,
  invalidate: () => void
): Promise<void> {
  const config = await getConfig();
  const current = currentDefaultModel(config);
  if (current === GATEWAY_MODEL_ID) return;
  const providers = await listProviders();
  const modelId = gatewayModelToAutoSet(current, providers?.active_model_resolvable);
  if (!modelId) return;
  const result = await updateConfig({ agents: { defaults: { model: modelId } } }, current);
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
    void saveGatewayModelIfUnusable(
      () => window.miqi.config.get(),
      () => window.miqi.providers.list(),
      (config, expectModel) => window.miqi.config.update(config, expectModel),
      invalidateConfigCache
    ).catch(() => {
      attemptedRef.current = false; // 保存失败 → 等下一次状态变化重试
    });
  }, [loggedIn, gatewayActive, status.state]);

  return null;
}
