import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import type { ProviderInfo } from '../../../../shared/ipc';
import {
  ModelSelect,
  filterAvailableModels,
  selectableProviders,
  FALLBACK_MODEL_PRESETS,
} from './ModelSelect';

describe('ModelSelect（issue #788 常用模型预设）', () => {
  it('后端不可用时回退预设列表，且只包含内置 DeepSeek v4-flash（SSR：useEffect 不执行）', () => {
    const html = renderToStaticMarkup(
      createElement(ModelSelect, { value: 'deepseek/deepseek-v4-flash', onChange: () => {} })
    );
    expect(html).toContain('deepseek/deepseek-v4-flash');
    // 收口：chat/reasoner 已下线，兜底列表不再出现
    expect(html).not.toContain('deepseek/deepseek-chat');
    expect(html).not.toContain('deepseek/deepseek-reasoner');
    // 收口后兜底列表不再出现无凭据入口的其他 provider
    expect(html).not.toContain('openai/gpt-4o');
    expect(html).not.toContain('anthropic/claude-opus-4-5');
    // 收口后移除「自定义模型」入口
    expect(html).not.toContain('自定义模型');
  });

  it('历史遗留的自定义模型不在预设中时显示占位提示，不再提供自定义输入框', () => {
    const html = renderToStaticMarkup(
      createElement(ModelSelect, { value: 'custom/my-model', onChange: () => {} })
    );
    expect(html).toContain('请选择模型');
    expect(html).not.toContain('custom/my-model');
    expect(html).not.toContain('provider/model-name');
  });

  it('外部传入预设时使用外部预设', () => {
    const html = renderToStaticMarkup(
      createElement(ModelSelect, {
        value: 'x/y',
        onChange: () => {},
        presets: [
          {
            id: 'x/y',
            name: 'X Y',
            provider: 'x',
            providerDisplayName: 'X',
            hidden: false,
            default: false,
          },
        ],
      })
    );
    expect(html).toContain('x/y');
  });
});

describe('filterAvailableModels（#929 可用 provider 过滤回归）', () => {
  const catalog = [
    { ...FALLBACK_MODEL_PRESETS[0] },
    {
      id: 'openai/gpt-4o',
      name: 'GPT-4o',
      provider: 'openai',
      providerDisplayName: 'OpenAI',
      hidden: false,
      default: false,
    },
    {
      id: 'custom/my-model',
      name: 'My Model',
      provider: 'custom',
      providerDisplayName: 'Custom',
      hidden: false,
      default: false,
    },
  ];

  it('只保留可用 provider 的模型', () => {
    const result = filterAvailableModels(catalog, new Set(['deepseek']));
    expect(result.map((m) => m.id)).toEqual(['deepseek/deepseek-v4-flash']);
  });

  it('可用集合未知（null）时不过滤，原样返回', () => {
    expect(filterAvailableModels(catalog, null)).toBe(catalog);
  });

  it('custom/* 已从运行时移除，即使列为可用也不放行（#933 review）', () => {
    const result = filterAvailableModels(catalog, new Set(['deepseek', 'custom']));
    expect(result.map((m) => m.id)).toEqual(['deepseek/deepseek-v4-flash']);
  });
});

describe('selectableProviders（#1179 残留凭据不得复活已收口 provider）', () => {
  const provider = (over: Record<string, unknown>) =>
    ({
      name: 'x',
      display_name: 'X',
      env_key: 'X_API_KEY',
      provider_type: 'openai',
      is_gateway: false,
      is_local: false,
      default_api_base: '',
      configured: false,
      api_base: null,
      ...over,
    }) as unknown as ProviderInfo;

  it('只认平台下发的内置 provider（builtin_available）', () => {
    const result = selectableProviders([
      provider({ name: 'deepseek', builtin_available: true, configured: false }),
      provider({ name: 'openai', builtin_available: false, configured: false }),
    ]);
    expect([...result]).toEqual(['deepseek']);
  });

  it('历史残留凭据（configured）不再让已收口的第三方 provider 可选', () => {
    const result = selectableProviders([
      provider({ name: 'deepseek', builtin_available: true }),
      provider({ name: 'anthropic', configured: true, api_key_hint: 'sk-a…1234' }),
      provider({ name: 'dashscope', configured: true }),
      provider({ name: 'gemini', configured: true }),
    ]);
    expect([...result]).toEqual(['deepseek']);
  });

  it('网关型 provider 的残留凭据不再放行全量目录（旧 gatewayRouted 旁路）', () => {
    const result = selectableProviders([
      provider({ name: 'deepseek', builtin_available: true }),
      provider({ name: 'openrouter', is_gateway: true, configured: true }),
    ]);
    expect([...result]).toEqual(['deepseek']);
  });

  it('干净装（无任何凭据）仍放行平台 provider，与对照机器一致', () => {
    const result = selectableProviders([
      provider({ name: 'deepseek', builtin_available: true, configured: false }),
      provider({ name: 'openai', configured: false }),
    ]);
    expect([...result]).toEqual(['deepseek']);
  });

  it('旧版 bridge 整份清单都不带 builtin_available 时退回 configured 判定，不清空下拉', () => {
    const result = selectableProviders([
      provider({ name: 'deepseek', configured: true }),
      provider({ name: 'openai', configured: false }),
    ]);
    expect([...result]).toEqual(['deepseek']);
  });
});
