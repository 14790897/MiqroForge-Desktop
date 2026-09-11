import { describe, it, expect } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { ModelSelect, filterAvailableModels, FALLBACK_MODEL_PRESETS } from './ModelSelect';

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

  it('只保留可用 provider 的模型（内置可激活或已配置凭据）', () => {
    const result = filterAvailableModels(catalog, new Set(['deepseek']));
    expect(result.map((m) => m.id)).toEqual(['deepseek/deepseek-v4-flash']);
  });

  it('可用集合含历史已配置的 openai 时保留其模型', () => {
    const result = filterAvailableModels(catalog, new Set(['deepseek', 'openai']));
    expect(result.map((m) => m.id)).toEqual(['deepseek/deepseek-v4-flash', 'openai/gpt-4o']);
  });

  it('可用集合未知（null）时不过滤，原样返回', () => {
    expect(filterAvailableModels(catalog, null)).toBe(catalog);
  });

  it('已配置网关（gatewayRouted）时保留任意模型 —— 运行时网关兜底路由', () => {
    const result = filterAvailableModels(catalog, new Set(['deepseek']), true);
    // custom/* 已从运行时移除，网关兜底也不放行（#933 review）
    expect(result.map((m) => m.id)).toEqual(['deepseek/deepseek-v4-flash', 'openai/gpt-4o']);
  });
});
