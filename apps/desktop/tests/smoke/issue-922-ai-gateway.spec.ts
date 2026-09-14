/**
 * Issue #922 — AI 网关门禁（smoke，mock bridge）。
 *
 * 覆盖渲染侧两个门禁面：
 *   1. 模型 tab（ModelQuickPanel）：登录且 aiGatewayStatus 非 active →
 *      禁用模型下拉并给出"查看平台账号"引导；active → 正常可选模型。
 *   2. 聊天发送（ChatConsole）：登录且网关非 active → 发送被拦截，
 *      乐观气泡替换为网关提示、输入框恢复、chat.send 未被调用。
 */

import { test, expect } from '@playwright/test';
import { buildMockBridgeScript } from './mocks';

const CONFIGURED_DEEPSEEK = {
  name: 'deepseek',
  display_name: 'DeepSeek',
  env_key: 'DEEPSEEK_API_KEY',
  provider_type: 'openai',
  is_gateway: false,
  is_local: false,
  default_api_base: '',
  configured: true,
  api_key_hint: 'sk-t...seek',
  api_base: null,
  configured_model: 'deepseek-chat',
  verification_status: 'success',
  builtin_available: true,
  builtin_activated: false,
};

async function gotoModelTab(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
  await page.getByText(/^(System Settings|系统设置)$/).click();
  await page.getByRole('tab', { name: '模型' }).click();
}

test.describe('Issue #922 — AI 网关状态门禁', () => {
  test('登录且网关非 active（provisioning）：模型 tab 禁用并引导查看平台账号', async ({ page }) => {
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [CONFIGURED_DEEPSEEK],
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'provisioning' },
        },
      }),
    });
    await gotoModelTab(page);

    // 模型下拉不可见，出现网关未就绪门禁与平台账号引导
    await expect(page.getByText('AI 网关未就绪')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('查看平台账号')).toBeVisible();
    await expect(page.getByText('登录后使用平台内置模型')).not.toBeVisible();
    await expect(page.getByRole('button', { name: '保存' })).not.toBeVisible();

    // 模型设置页展示网关使用状态：透出平台下发的状态文案（开通中）
    const gatewayBadge = page.getByTestId('model-gateway-status');
    await expect(gatewayBadge).toBeVisible();
    await expect(gatewayBadge).toContainText('开通中');
  });

  test('登录且网关 active：模型 tab 可正常选择与保存', async ({ page }) => {
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [CONFIGURED_DEEPSEEK],
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'active', configVersion: 1 },
        },
        // 默认模型为网关模型 → 状态行应显示「使用中」
        activeModel: 'deepseek/deepseek-v4-flash',
      }),
    });
    await gotoModelTab(page);

    await expect(page.getByText('AI 网关未就绪')).not.toBeVisible();
    await expect(page.getByRole('button', { name: '保存' })).toBeVisible({ timeout: 10_000 });
    // ModelSelect 已渲染（select 元素存在，含内置 DeepSeek 兜底预设）
    await expect(page.locator('select').first()).toBeVisible();

    // 模型设置页展示网关使用状态：active + 网关模型 → 「使用中」
    const gatewayBadge = page.getByTestId('model-gateway-status');
    await expect(gatewayBadge).toBeVisible();
    await expect(gatewayBadge).toContainText('使用中');
  });

  test('登录且网关非 active（failed）：聊天发送被拦截，chat.send 不被调用', async ({ page }) => {
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [CONFIGURED_DEEPSEEK],
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'failed' },
        },
      }),
    });
    await page.goto('/');
    await page.waitForSelector('#root', { state: 'visible' });

    // 统计 chat.send 调用次数（网关拦截意味着后端根本不该收到发送请求）
    await page.evaluate(() => {
      (window as any).__chatSends = 0;
      const orig = (window as any).miqi.chat.send.bind((window as any).miqi.chat);
      (window as any).miqi.chat.send = (...args: unknown[]) => {
        (window as any).__chatSends += 1;
        return orig(...args);
      };
    });

    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await expect(textarea).toBeVisible({ timeout: 10_000 });
    await textarea.fill('ping gateway');
    await textarea.press('Enter');

    // 乐观气泡被替换为网关阻断提示，输入框恢复草稿
    await expect(page.getByText(/AI 网关未就绪/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/暂时无法发起会话/)).toBeVisible();
    await expect(textarea).toHaveValue('ping gateway');

    // 后端 chat.send 从未被调用
    await page.waitForTimeout(500);
    const sends = await page.evaluate(() => (window as any).__chatSends);
    expect(sends).toBe(0);
  });

  test('网关 active 时聊天发送走正常链路（chat.send 被调用）', async ({ page }) => {
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [CONFIGURED_DEEPSEEK],
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'active', configVersion: 1 },
        },
      }),
    });
    await page.goto('/');
    await page.waitForSelector('#root', { state: 'visible' });

    await page.evaluate(() => {
      (window as any).__chatSends = 0;
      const orig = (window as any).miqi.chat.send.bind((window as any).miqi.chat);
      (window as any).miqi.chat.send = (...args: unknown[]) => {
        (window as any).__chatSends += 1;
        return orig(...args);
      };
    });

    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await expect(textarea).toBeVisible({ timeout: 10_000 });
    await textarea.fill('hello gateway');
    await textarea.press('Enter');

    // 用户气泡保留（未被替换为错误气泡；首条消息同时成为会话标题，用 testid 限定）
    await expect(page.getByTestId('chat-message-user').getByText('hello gateway')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(/AI 网关未就绪/)).not.toBeVisible();
    await page.waitForTimeout(500);
    const sends = await page.evaluate(() => (window as any).__chatSends);
    expect(sends).toBe(1);
  });

  test('登录 + 网关 active + 无本地凭据：默认网关模型可直接发送（chat.send 被调用）', async ({
    page,
  }) => {
    // 登录用户没有任何本地 provider 凭据 —— 此前只看 providers[].configured
    // 会把这条路径拦成「尚未配置模型服务」（用户登录后仍被要求配置模型）。
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [],
        // 真实后端按运行时判定：网关路由可用 → 模型可发起会话
        activeModelResolvable: true,
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'active', configVersion: 1 },
        },
      }),
    });
    await page.goto('/');
    await page.waitForSelector('#root', { state: 'visible' });

    await page.evaluate(() => {
      (window as any).__chatSends = 0;
      const orig = (window as any).miqi.chat.send.bind((window as any).miqi.chat);
      (window as any).miqi.chat.send = (...args: unknown[]) => {
        (window as any).__chatSends += 1;
        return orig(...args);
      };
    });

    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await expect(textarea).toBeVisible({ timeout: 10_000 });
    await textarea.fill('hello gateway no local key');
    await textarea.press('Enter');

    // 用户气泡保留，未被替换为「未配置模型服务」或登录引导
    await expect(
      page.getByTestId('chat-message-user').getByText('hello gateway no local key')
    ).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/尚未配置模型服务/)).not.toBeVisible();
    await expect(page.getByText(/尚未登录平台账号/)).not.toBeVisible();
    await page.waitForTimeout(500);
    const sends = await page.evaluate(() => (window as any).__chatSends);
    expect(sends).toBe(1);
  });

  test('登录 + 网关 active + 无本地凭据 + 模型不可解析：拦截并引导去选择模型', async ({ page }) => {
    // 已登录但默认模型运行时不可用（非网关模型且无本地凭据）——引导落点是
    // 重选平台内置模型，而不是「去配置模型」（#835 收口后凭据配置已不存在）。
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [],
        activeModelResolvable: false,
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'active', configVersion: 1 },
        },
      }),
    });
    await page.goto('/');
    await page.waitForSelector('#root', { state: 'visible' });

    await page.evaluate(() => {
      (window as any).__chatSends = 0;
      const orig = (window as any).miqi.chat.send.bind((window as any).miqi.chat);
      (window as any).miqi.chat.send = (...args: unknown[]) => {
        (window as any).__chatSends += 1;
        return orig(...args);
      };
    });

    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await expect(textarea).toBeVisible({ timeout: 10_000 });
    await textarea.fill('ping unresolvable');
    await textarea.press('Enter');

    await expect(page.getByText(/当前默认模型没有可用的模型服务/)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole('button', { name: '去选择模型' })).toBeVisible();
    await expect(textarea).toHaveValue('ping unresolvable');

    await page.waitForTimeout(500);
    const sends = await page.evaluate(() => (window as any).__chatSends);
    expect(sends).toBe(0);
  });

  test('网关非 active（provisioning）：论文无直链的 AI 下载 fallback 同样被拦截（chat.send 零调用）', async ({
    page,
  }) => {
    // 论文无 open_access_pdf_url / pdf_url / arxiv_id → 点击「下载 PDF」走
    // aiDownload fallback。该路径此前直连 chat.send 绕过网关门禁（CodeRabbit
    // #943），修复后必须经 handleSend 主流程同样被拦截。
    const paperJson = JSON.stringify({
      query: 'transformer',
      source: 'semantic_scholar',
      total: 1,
      count: 1,
      items: [
        {
          id: 'p-922',
          title: 'Attention Is All You Need',
          abstract: 'A novel neural network architecture based solely on attention mechanisms.',
          authors: ['A. Vaswani', 'N. Shazeer'],
          year: 2017,
          is_open_access: true,
        },
      ],
    });
    await page.addInitScript({
      content: buildMockBridgeScript({
        providers: [CONFIGURED_DEEPSEEK],
        qraftStatus: {
          loggedIn: true,
          account: {
            phone: '18500000000',
            sub: '19',
            username: 'U-GW',
            nickname: '网关用户',
          },
          env: 'test',
          baseUrl: 'https://test.forge.miqroera.com/api',
          aiGateway: { status: 'provisioning' },
        },
        sessions: [
          {
            key: 'gw-paper-session',
            title: '网关论文会话',
            updated_at: Date.now(),
            message_count: 2,
          },
        ],
        sessionMessages: {
          'gw-paper-session': [
            { role: 'user', content: '找 transformer 论文', timestamp: '2026-09-04T01:00:00.000Z' },
            {
              role: 'tool',
              name: 'paper_search',
              content: paperJson,
              timestamp: '2026-09-04T01:00:01.000Z',
            },
          ],
        },
      }),
    });
    await page.goto('/');
    await page.waitForSelector('#root', { state: 'visible' });
    await page.getByText('网关论文会话').click();
    await expect(page.getByText('Attention Is All You Need')).toBeVisible({ timeout: 10_000 });

    // 统计 chat.send 调用次数（网关拦截意味着后端根本不该收到发送请求）
    await page.evaluate(() => {
      (window as any).__chatSends = 0;
      const orig = (window as any).miqi.chat.send.bind((window as any).miqi.chat);
      (window as any).miqi.chat.send = (...args: unknown[]) => {
        (window as any).__chatSends += 1;
        return orig(...args);
      };
    });

    await page.getByText('下载 PDF').click();

    // 乐观气泡被替换为网关阻断提示，输入框恢复下载指令草稿
    await expect(page.getByText(/AI 网关未就绪/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/暂时无法发起会话/)).toBeVisible();
    const textarea = page.locator('[data-testid="chat-input-container"] textarea');
    await expect(textarea).toHaveValue(/请下载论文《Attention Is All You Need》/);

    // 后端 chat.send 从未被调用
    await page.waitForTimeout(500);
    const sends = await page.evaluate(() => (window as any).__chatSends);
    expect(sends).toBe(0);
  });
});
