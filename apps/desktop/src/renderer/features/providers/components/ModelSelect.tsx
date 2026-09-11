import { useEffect, useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { ModelInfo } from '../../../../shared/ipc';
import { PROVIDER_DISPLAY_NAMES } from '../../../lib/providers';

/**
 * 常用模型下拉（issue #788）。
 * 预设清单来自后端 model/list（model_catalog.py，覆盖
 * context_runtime._MODEL_MAX_INPUT_TOKENS 常用模型）；选择后自动填充
 * "provider/model-name" 格式。已移除「自定义模型」自由输入（#835 合规收口）。
 */

// 后端不可用（运行时未启动）时的兜底预设，保证下拉始终可用。
// 收口（#835）后仅保留内置 DeepSeek：其他 provider 已无自配凭据入口，
// 出现在下拉里只会诱导保存一个运行时无法使用的模型。
export const FALLBACK_MODEL_PRESETS: ModelInfo[] = [
  {
    id: 'deepseek/deepseek-v4-flash',
    name: 'DeepSeek V4 Flash',
    provider: 'deepseek',
    providerDisplayName: 'DeepSeek',
    hidden: false,
    default: false,
  },
];

/** providers.list 不可用时的可用 provider 兜底：仅内置可激活的 DeepSeek。 */
const FALLBACK_AVAILABLE_PROVIDERS = ['deepseek'];

/**
 * 只保留「可用 provider」的模型：内置可激活（builtin_available）或已配置
 * 凭据（configured，兼容历史配置）。available 为 null 时不过滤（目录还没
 * 加载完）。gatewayRouted 为 true 时保留任意模型 —— 已配置的网关（如
 * OpenRouter）在运行时兜底路由任意模型（Config._match_provider），过滤
 * 掉反而会清掉用户能正常使用的模型（#929 review）。收口后 model/list
 * 仍返回全量目录，这里负责兜住 custom 等已从运行时工厂移除的 provider。
 */
export function filterAvailableModels(
  models: ModelInfo[],
  available: Set<string> | null,
  gatewayRouted = false
): ModelInfo[] {
  if (available === null) return models;
  const filtered = gatewayRouted ? models : models.filter((m) => available.has(m.provider));
  // custom provider 已从运行时移除：即使网关兜底路由也不放行 custom/*，
  // 否则选择后新会话会在 make_provider 报错（#933 review）。
  return filtered.filter((m) => m.provider !== 'custom');
}

function displayName(provider: string): string {
  return PROVIDER_DISPLAY_NAMES[provider] ?? provider;
}

function groupPresets(
  presets: ModelInfo[]
): { provider: string; label: string; models: ModelInfo[] }[] {
  const map = new Map<string, ModelInfo[]>();
  for (const m of presets) {
    if (m.hidden) continue;
    const arr = map.get(m.provider) ?? [];
    arr.push(m);
    map.set(m.provider, arr);
  }
  return [...map.entries()].map(([provider, models]) => ({
    provider,
    label: models[0]?.providerDisplayName || displayName(provider),
    models,
  }));
}

interface ModelSelectProps {
  value: string;
  onChange: (v: string) => void;
  /** 外部传入的预设（如已加载的 providers 列表）；默认走后端 model/list */
  presets?: ModelInfo[];
}

export function ModelSelect({ value, onChange, presets }: ModelSelectProps) {
  const [loaded, setLoaded] = useState<ModelInfo[] | null>(null);
  const [availableProviders, setAvailableProviders] = useState<Set<string> | null>(null);
  const [gatewayRouted, setGatewayRouted] = useState(false);

  useEffect(() => {
    let alive = true;
    window.miqi.models
      .list()
      .then((r) => {
        if (alive) setLoaded(r.models ?? []);
      })
      .catch(() => {
        if (alive) setLoaded([]);
      });
    window.miqi.providers
      .list()
      .then((r) => {
        if (!alive) return;
        setAvailableProviders(
          new Set(r.providers.filter((p) => p.builtin_available || p.configured).map((p) => p.name))
        );
        setGatewayRouted(r.providers.some((p) => p.is_gateway && p.configured));
      })
      .catch(() => {
        if (alive) setAvailableProviders(new Set(FALLBACK_AVAILABLE_PROVIDERS));
      });
    return () => {
      alive = false;
    };
  }, []);

  const all = useMemo(() => {
    const source = loaded === null ? null : loaded.length > 0 ? loaded : null;
    return filterAvailableModels(
      source ?? presets ?? FALLBACK_MODEL_PRESETS,
      availableProviders,
      gatewayRouted
    );
  }, [loaded, presets, availableProviders, gatewayRouted]);

  const groups = useMemo(() => groupPresets(all), [all]);
  const isPreset = all.some((m) => m.id === value);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="relative">
        <select
          value={isPreset ? value : ''}
          onChange={(e) => onChange(e.target.value)}
          className="w-full appearance-none px-3 py-2 pr-9 rounded-lg text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] text-[var(--text)] focus:outline-none focus:border-[var(--border-strong)] font-mono cursor-pointer"
        >
          {!isPreset && (
            <option value="" disabled>
              请选择模型…
            </option>
          )}
          {groups.map((g) => (
            <optgroup key={g.provider} label={g.label}>
              {g.models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <ChevronDown
          size={14}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-faint)] pointer-events-none"
        />
      </div>
    </div>
  );
}
