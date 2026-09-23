import { test, expect } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

/**
 * #1179 残留 config.json 旧凭据让模型下拉列出非平台模型。
 *
 * 复现机器画像：曾多次卸载重装（~/.miqi/config.json 未清理），providers 下留着
 * 收口前配置的第三方凭据（anthropic / openai / dashscope / gemini），外加一个带
 * 旧 key 的网关型 provider（openrouter）。干净机器只有平台下发的 deepseek。
 *
 * 复现路径是「设置 → 通用 → 默认模型」（issue 步骤 2），这里先断言该路径，再断言
 * 同一个 ModelSelect 的另一处入口（模型选项卡）。修复前两处都会列出残留 provider
 * 的模型（anthropic/*、dashscope/*、gemini/*、openai/gpt-4o），修复后都只剩
 * deepseek/deepseek-v4-flash。
 */
test('残留凭据机器的模型下拉只列平台下发的模型（#1179）', async ({ page }) => {
  test.setTimeout(90_000);

  /** 平台下发的内置 provider：干净装与残留机器都一样（未激活、无本地凭据）。 */
  const platformProvider = {
    name: 'deepseek',
    display_name: 'DeepSeek',
    env_key: 'DEEPSEEK_API_KEY',
    provider_type: 'openai',
    is_gateway: false,
    is_local: false,
    default_api_base: '',
    configured: false,
    api_key_hint: null,
    api_base: null,
    configured_model: null,
    verification_status: 'missing',
    builtin_available: true,
    builtin_activated: false,
  };

  /** 收口（#835）前留下的第三方凭据 —— 已无自配入口，只剩残留。 */
  const residualProvider = (name: string, displayName: string, extra = {}) => ({
    ...platformProvider,
    name,
    display_name: displayName,
    env_key: `${name.toUpperCase()}_API_KEY`,
    configured: true,
    api_key_hint: 'sk-l…gacy',
    verification_status: 'unverified',
    builtin_available: false,
    ...extra,
  });

  const providers = [
    platformProvider,
    residualProvider('anthropic', 'Anthropic'),
    residualProvider('openai', 'OpenAI'),
    residualProvider('dashscope', 'DashScope'),
    residualProvider('gemini', 'Gemini'),
    // 网关旁路：旧 key 让 is_gateway && configured 为真（修复前 gatewayRouted=true
    // 时直接放行全量目录）
    residualProvider('openrouter', 'OpenRouter', { is_gateway: true }),
  ];

  const models = [
    {
      id: 'deepseek/deepseek-v4-flash',
      name: 'DeepSeek V4 Flash',
      provider: 'deepseek',
      providerDisplayName: 'DeepSeek',
      hidden: false,
      default: false,
    },
    {
      id: 'anthropic/claude-3.5-haiku',
      name: 'Claude 3.5 Haiku',
      provider: 'anthropic',
      providerDisplayName: 'Anthropic',
      hidden: false,
      default: false,
    },
    {
      id: 'anthropic/claude-opus-4',
      name: 'Claude Opus 4',
      provider: 'anthropic',
      providerDisplayName: 'Anthropic',
      hidden: false,
      default: false,
    },
    {
      id: 'dashscope/qwen-max',
      name: 'Qwen Max',
      provider: 'dashscope',
      providerDisplayName: 'DashScope',
      hidden: false,
      default: false,
    },
    {
      id: 'gemini/gemini-2.5-pro',
      name: 'Gemini 2.5 Pro',
      provider: 'gemini',
      providerDisplayName: 'Gemini',
      hidden: false,
      default: false,
    },
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

  await page.addInitScript({
    content: buildMockBridgeScript({
      activeModel: 'deepseek/deepseek-v4-flash',
      activeProvider: 'deepseek',
      providers,
      models,
    }),
  });

  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });

  // 前置断言：残留凭据确实还在 providers.list 里被标成 configured —— 下拉过滤
  // 掉的是「已在清单里的第三方凭据」，不是 mock 里根本没有这些 provider。
  const configured = await page.evaluate(async () =>
    (await (window as any).miqi.providers.list()).providers
      .filter((p: any) => p.configured)
      .map((p: any) => p.name)
  );
  expect(configured).toEqual(['anthropic', 'openai', 'dashscope', 'gemini', 'openrouter']);

  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page.evaluate(() => (window as any).miqi.qraft.login('18500000000', 'test-password'));

  /** 断言当前可见的默认模型下拉只列平台下发的模型。 */
  const expectOnlyPlatformModel = async () => {
    const select = page.locator('select').first();
    await expect(select).toBeVisible({ timeout: 10_000 });
    // 平台下发的模型仍可选
    await expect(select).toContainText('deepseek/deepseek-v4-flash');
    // 残留凭据的 provider 不得进入下拉
    await expect(select).not.toContainText('anthropic');
    await expect(select).not.toContainText('claude');
    await expect(select).not.toContainText('dashscope');
    await expect(select).not.toContainText('qwen');
    await expect(select).not.toContainText('gemini');
    await expect(select).not.toContainText('openai');
    await expect(select).not.toContainText('gpt-4o');
    // 网关旁路也不得放行全量目录
    await expect(select).not.toContainText('custom');
    // 下拉里除占位项外只剩平台 provider 的模型（未设置默认模型时会有占位项）
    const modelOptions = (await select.locator('option').allTextContents()).filter(
      (text) => text !== '请选择模型…'
    );
    expect(modelOptions).toEqual(['deepseek/deepseek-v4-flash']);
  };

  // 复现路径（issue #1179 步骤 2）：设置 → 通用 → 默认模型
  await page.getByRole('tab', { name: '通用' }).click();
  await expectOnlyPlatformModel();
  await page.screenshot({ path: 'test-results/1179-shots/01-general-tab-default-model.png' });

  // 同一个 ModelSelect 组件的另一处入口（模型选项卡），一并守住
  await page.getByRole('tab', { name: '模型' }).click();
  await expectOnlyPlatformModel();
  await page.screenshot({ path: 'test-results/1179-shots/02-model-tab-model-select.png' });
});
