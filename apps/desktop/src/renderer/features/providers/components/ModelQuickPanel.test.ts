import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import { ModelQuickPanel } from './ModelQuickPanel';
import { useQraftStatus } from '../../../hooks/useQraftStatus';

vi.mock('../../../hooks/useQraftStatus', () => ({
  useQraftStatus: vi.fn(),
}));

const mockedStatus = vi.mocked(useQraftStatus);

// SSR 不跑 useEffect，故各测试显式控制 useQraftStatus 返回值以覆盖三态门禁（#922）。
function loggedOut() {
  return {
    status: { loggedIn: false },
    loggedIn: false,
    aiGatewayStatus: undefined,
    gatewayActive: false,
    aiGatewayKnown: false,
  };
}

function loggedInGatewayActive() {
  return {
    status: { loggedIn: true, aiGateway: { status: 'active', configVersion: 1 } },
    loggedIn: true,
    aiGatewayStatus: 'active',
    gatewayActive: true,
    aiGatewayKnown: true,
  };
}

function loggedInGatewayNotReady(status: string) {
  return {
    status: { loggedIn: true, aiGateway: { status } },
    loggedIn: true,
    aiGatewayStatus: status,
    gatewayActive: false,
    aiGatewayKnown: true,
  };
}

/** 登录但平台未下发 aiGateway：视为可用，避免误锁（#922 门禁边界）。 */
function loggedInGatewayUnknown() {
  return {
    status: { loggedIn: true },
    loggedIn: true,
    aiGatewayStatus: undefined,
    gatewayActive: false,
    aiGatewayKnown: false,
  };
}

const render = (activeModel = 'deepseek/deepseek-v4-flash') =>
  renderToStaticMarkup(
    createElement(ModelQuickPanel, {
      activeModel,
      onSaved: () => {},
      onGoToQraft: () => {},
    })
  );

describe('ModelQuickPanel（#835/#922 门控）', () => {
  beforeEach(() => {
    mockedStatus.mockReturnValue(loggedOut());
  });

  it('未登录时显示登录门控，隐藏自定义配置入口', () => {
    const html = render();
    expect(html).toContain('模型设置');
    expect(html).toContain('默认模型');
    expect(html).toContain('登录后使用平台内置模型');
    // #1000：一键浏览器登录按钮（原「去登录」跳设置页改为直接发起 OAuth）
    expect(html).toContain('登录 MiQroForge 账号');
    expect(html).toContain('model-quickpanel-login-btn');
    expect(html).not.toContain('保存');
    expect(html).not.toContain('API Key');
    expect(html).not.toContain('API Base URL');
  });

  it('登录且网关 active：显示模型下拉与保存', () => {
    mockedStatus.mockReturnValue(loggedInGatewayActive());
    const html = render();
    expect(html).not.toContain('登录后使用平台内置模型');
    expect(html).not.toContain('AI 网关未就绪');
    expect(html).toContain('保存');
  });

  it('登录但网关非 active（provisioning）：禁用并给出平台账号引导', () => {
    mockedStatus.mockReturnValue(loggedInGatewayNotReady('provisioning'));
    const html = render();
    expect(html).toContain('AI 网关未就绪');
    expect(html).toContain('查看平台账号');
    expect(html).not.toContain('登录后使用平台内置模型');
    expect(html).not.toContain('保存');
  });

  it('登录但网关状态未下发：按可用放行，不误锁', () => {
    mockedStatus.mockReturnValue(loggedInGatewayUnknown());
    const html = render();
    expect(html).toContain('保存');
    expect(html).not.toContain('AI 网关未就绪');
  });

  describe('AI 网关使用状态展示（#922）', () => {
    it('未登录：显示「未登录」', () => {
      const html = render();
      expect(html).toContain('AI 网关');
      expect(html).toContain('未登录');
      expect(html).not.toContain('使用中');
    });

    it('登录 + 网关 active + 当前模型为网关模型：显示「使用中」', () => {
      mockedStatus.mockReturnValue(loggedInGatewayActive());
      const html = render('deepseek/deepseek-v4-flash');
      expect(html).toContain('使用中');
      expect(html).not.toContain('已开通');
    });

    it('登录 + 网关 active + 当前模型非网关模型：显示「已开通」并提示不走网关', () => {
      mockedStatus.mockReturnValue(loggedInGatewayActive());
      const html = render('deepseek/deepseek-v4-pro');
      expect(html).toContain('已开通');
      expect(html).not.toContain('使用中');
      expect(html).toContain('deepseek/deepseek-v4-pro');
    });

    it('登录但网关非 active：显示平台下发状态（开通中）', () => {
      mockedStatus.mockReturnValue(loggedInGatewayNotReady('provisioning'));
      const html = render();
      expect(html).toContain('开通中');
    });

    it('登录但网关状态未下发：显示「未下发」', () => {
      mockedStatus.mockReturnValue(loggedInGatewayUnknown());
      const html = render();
      expect(html).toContain('未下发');
    });
  });
});
