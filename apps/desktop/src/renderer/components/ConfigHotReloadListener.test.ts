import { describe, expect, it } from 'vitest';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { IPC_EVENTS } from '../../shared/ipc';
import type { ConfigUpdatedPayload } from '../../shared/ipc';
import { ConfigHotReloadListener, resolveConfigUpdateFeedback } from './ConfigHotReloadListener';

function payload(overrides: Partial<ConfigUpdatedPayload> = {}): ConfigUpdatedPayload {
  return {
    applied: [],
    newSessionsOnly: [],
    restartRequired: [],
    restartReasons: [],
    ...overrides,
  };
}

describe('IPC channels (issue #789)', () => {
  it('defines the config:updated renderer channel', () => {
    expect(IPC_EVENTS.CONFIG_UPDATED).toBe('config:updated');
    // Main-process orphan mapping uses CHAT_<TYPE>.
    expect(IPC_EVENTS.CHAT_CONFIG_UPDATED).toBe('config:updated');
  });
});

describe('resolveConfigUpdateFeedback', () => {
  it('tier A only → ok toast "配置已生效，无需重启"', () => {
    const f = resolveConfigUpdateFeedback(payload({ applied: ['providers.deepseek.api_key'] }));
    expect(f).toEqual({ kind: 'ok', text: '配置已生效，无需重启' });
  });

  it('tier B only → info toast "已保存，对新建会话生效"', () => {
    const f = resolveConfigUpdateFeedback(
      payload({ newSessionsOnly: ['tools.web.search.provider'] })
    );
    expect(f).toEqual({ kind: 'info', text: '已保存，对新建会话生效' });
  });

  it('tier C → warn toast with the first reason, suffix stripped', () => {
    const f = resolveConfigUpdateFeedback(
      payload({
        restartRequired: ['tools.sandbox.wsl_distro'],
        restartReasons: ['WSL 发行版在进程启动时检测，修改后需重启应用'],
      })
    );
    expect(f?.kind).toBe('warn');
    expect(f?.text).toContain('已保存，部分配置需要重启后生效');
    expect(f?.text).toContain('WSL 发行版在进程启动时检测');
    expect(f?.text).not.toContain('，修改后需重启应用');
  });

  it('mixed tiers → restart wins (warn)', () => {
    const f = resolveConfigUpdateFeedback(
      payload({
        applied: ['providers.deepseek.api_key'],
        newSessionsOnly: ['tools.web.search.provider'],
        restartRequired: ['gateway.port'],
        restartReasons: ['网关监听端口在进程启动时绑定，修改后需重启应用'],
      })
    );
    expect(f?.kind).toBe('warn');
  });

  it('no change → null (no toast)', () => {
    expect(resolveConfigUpdateFeedback(payload())).toBeNull();
  });

  it('provider rebuild failed → info toast, NOT "已生效"', () => {
    const f = resolveConfigUpdateFeedback(
      payload({
        applied: ['providers.deepseek.api_key'],
        providerRebuilt: false,
      })
    );
    expect(f).toEqual({
      kind: 'info',
      text: '已保存，Provider 重建失败，新配置将在新会话生效',
    });
  });
});

describe('ConfigHotReloadListener SSR', () => {
  it('renders nothing before any event (initial state)', () => {
    const html = renderToStaticMarkup(createElement(ConfigHotReloadListener));
    expect(html).toBe('');
  });
});
