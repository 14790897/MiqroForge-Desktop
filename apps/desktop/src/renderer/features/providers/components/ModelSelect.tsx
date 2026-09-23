import { useEffect, useMemo, useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { ModelInfo, ProviderInfo } from '../../../../shared/ipc';
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

/**
 * providers.list 未返回（未解析）或调用失败时的可用 provider 兜底：仅内置可激活的
 * DeepSeek。两者都按「只认内置」处理，绝不放行后端全量目录（见 filterAvailableModels）。
 */
const FALLBACK_AVAILABLE_PROVIDERS = new Set(['deepseek']);

/**
 * 从 providers.list 结果推导下拉里可选的 provider 集合（#1179）。
 *
 * 唯一依据是 builtin_available —— 平台下发的内置 provider。收口（#835）后
 * 第三方 provider 已无自配凭据入口，config.json 里的历史残留凭据只能让
 * configured 再次为真，网关型 provider 的旧 key 也会让 is_gateway 项为真；
 * 两者都不能作为放行依据，否则平台不参与的模型会重新列进下拉。
 *
 * 旧版 bridge 不带 builtin_available 字段时（整个清单都缺）退回 configured
 * 判定，与 ipc.ts 对 active_model_resolvable 的兜底口径一致 —— 否则下拉会
 * 被清空，比放行残留模型更难用。
 */
export function selectableProviders(providers: ProviderInfo[]): Set<string> {
  const knowsBuiltin = providers.some((p) => p.builtin_available !== undefined);
  return new Set(
    providers.filter((p) => (knowsBuiltin ? p.builtin_available : p.configured)).map((p) => p.name)
  );
}

/**
 * 只保留「平台可选 provider」的模型（可用集合见 selectableProviders）。
 *
 * available 为 null（providers.list 尚未返回）时按兜底集合过滤，**不放行目录**：
 * models.list 通常先于 providers.list 返回，此期间若原样放行，残留凭据机器上的
 * anthropic/dashscope/… 会在下拉里可被选中（#1179：收口后 model/list 仍返回全量
 * 目录，过滤是唯一防线）。
 */
export function filterAvailableModels(
  models: ModelInfo[],
  available: Set<string> | null
): ModelInfo[] {
  const providers = available ?? FALLBACK_AVAILABLE_PROVIDERS;
  // custom provider 已从运行时移除：不放行 custom/*，否则选择后新会话会在
  // make_provider 报错（#933 review）。
  return models.filter((m) => m.provider !== 'custom' && providers.has(m.provider));
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
        if (alive) setAvailableProviders(selectableProviders(r.providers));
      })
      .catch(() => {
        if (alive) setAvailableProviders(FALLBACK_AVAILABLE_PROVIDERS);
      });
    return () => {
      alive = false;
    };
  }, []);

  const all = useMemo(() => {
    const source = loaded === null ? null : loaded.length > 0 ? loaded : null;
    return filterAvailableModels(source ?? presets ?? FALLBACK_MODEL_PRESETS, availableProviders);
  }, [loaded, presets, availableProviders]);

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
