import { describe, expect, it, vi } from 'vitest';
import {
  autoSyncGatewayModel,
  currentDefaultModel,
  gatewayModelToAutoSet,
  saveGatewayModelIfUnusable,
} from './GatewayModelAutoSync';

/** 永不 settle：镜像被热重启孤儿化的桥调用（旧进程 close 提前返回，pending 不落）。 */
const never = () => new Promise<void>(() => {});

describe('currentDefaultModel', () => {
  it('reads the model from a config snapshot', () => {
    expect(
      currentDefaultModel({ agents: { defaults: { model: 'deepseek/deepseek-v4-pro' } } })
    ).toBe('deepseek/deepseek-v4-pro');
  });

  it('returns an empty string for missing or malformed shapes', () => {
    expect(currentDefaultModel(null)).toBe('');
    expect(currentDefaultModel({})).toBe('');
    expect(currentDefaultModel({ agents: null })).toBe('');
    expect(currentDefaultModel({ agents: { defaults: {} } })).toBe('');
    expect(currentDefaultModel({ agents: { defaults: { model: '' } } })).toBe('');
    expect(currentDefaultModel({ agents: { defaults: { model: '  ' } } })).toBe('');
  });
});

describe('gatewayModelToAutoSet', () => {
  it('falls back to the gateway model when nothing is set (even on an old bridge)', () => {
    expect(gatewayModelToAutoSet('', undefined)).toBe('deepseek/deepseek-v4-flash');
    expect(gatewayModelToAutoSet('', false)).toBe('deepseek/deepseek-v4-flash');
  });

  it('replaces an unusable model — the fresh-install default is never empty', () => {
    // #1172：config.get 把 schema 默认值带出来，永远非空，所以只判空的条件
    // 从不成立。全新安装的 anthropic/claude-opus-4-5 自身 provider 无凭据、
    // 也没有平台网关路由 → 严格判据为 false → 自动写入网关模型。
    expect(gatewayModelToAutoSet('anthropic/claude-opus-4-5', false)).toBe(
      'deepseek/deepseek-v4-flash'
    );
    expect(gatewayModelToAutoSet('custom/legacy-model', false)).toBe('deepseek/deepseek-v4-flash');
  });

  it('keeps a model served by its own provider or the platform gateway', () => {
    expect(gatewayModelToAutoSet('deepseek/deepseek-v4-pro', true)).toBeNull();
    expect(gatewayModelToAutoSet('anthropic/claude-opus-4-5', true)).toBeNull();
  });

  it('keeps a configured model when usability is unknown (old bridge)', () => {
    expect(gatewayModelToAutoSet('deepseek/deepseek-v4-pro', undefined)).toBeNull();
  });

  it('does nothing when the gateway model is already set', () => {
    expect(gatewayModelToAutoSet('deepseek/deepseek-v4-flash', false)).toBeNull();
    expect(gatewayModelToAutoSet('deepseek/deepseek-v4-flash', true)).toBeNull();
  });
});

describe('saveGatewayModelIfUnusable', () => {
  it('writes the gateway model with the current value as expectModel', async () => {
    const getConfig = vi
      .fn()
      .mockResolvedValue({ agents: { defaults: { model: 'anthropic/claude-opus-4-5' } } });
    const listProviders = vi
      .fn()
      .mockResolvedValue({ active_model_own_or_gateway_resolvable: false });
    const updateConfig = vi.fn().mockResolvedValue({ saved: true });
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => true
    );

    expect(updateConfig).toHaveBeenCalledWith(
      { agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } },
      'anthropic/claude-opus-4-5'
    );
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it('writes the gateway model with expectModel "" when nothing is set', async () => {
    const getConfig = vi.fn().mockResolvedValue({ agents: { defaults: { model: '' } } });
    const listProviders = vi.fn();
    const updateConfig = vi.fn().mockResolvedValue({ saved: true });
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => true
    );

    expect(updateConfig).toHaveBeenCalledWith(
      { agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } },
      ''
    );
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it('does not call update when the current model is served by its own provider', async () => {
    const getConfig = vi
      .fn()
      .mockResolvedValue({ agents: { defaults: { model: 'deepseek/deepseek-v4-pro' } } });
    const listProviders = vi
      .fn()
      .mockResolvedValue({ active_model_own_or_gateway_resolvable: true });
    const updateConfig = vi.fn();
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => true
    );

    expect(updateConfig).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('does not call update when the gateway model is already set', async () => {
    const getConfig = vi
      .fn()
      .mockResolvedValue({ agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } });
    const listProviders = vi.fn();
    const updateConfig = vi.fn();
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => true
    );

    expect(listProviders).not.toHaveBeenCalled();
    expect(updateConfig).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('keeps a configured model when the bridge does not report the strict field', async () => {
    // 旧版 bridge：拿不到严格判据就不改写非空模型（保持修复前的行为）。
    const getConfig = vi
      .fn()
      .mockResolvedValue({ agents: { defaults: { model: 'deepseek/deepseek-v4-pro' } } });
    const listProviders = vi.fn().mockResolvedValue({});
    const updateConfig = vi.fn();
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => true
    );

    expect(updateConfig).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('keeps the newer user selection when the backend skips the compare-and-set', async () => {
    const getConfig = vi.fn().mockResolvedValue({ agents: { defaults: { model: '' } } });
    const listProviders = vi.fn();
    const updateConfig = vi
      .fn()
      .mockResolvedValue({ saved: false, skipped: 'expect_model_mismatch' });
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => true
    );

    expect(updateConfig).toHaveBeenCalledOnce();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('does not write when eligibility is lost while reading config', async () => {
    // 读快照 / 查 provider 列表都带 await：期间用户可能已登出或网关失效。
    // 后端的比较并设置只看模型值，察觉不到这种变化 —— 写之前必须再查一次。
    let eligible = true;
    const getConfig = vi.fn().mockImplementation(async () => {
      eligible = false; // 读取返回时用户已登出
      return { agents: { defaults: { model: 'anthropic/claude-opus-4-5' } } };
    });
    const listProviders = vi
      .fn()
      .mockResolvedValue({ active_model_own_or_gateway_resolvable: false });
    const updateConfig = vi.fn();
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(
      getConfig,
      listProviders,
      updateConfig,
      invalidate,
      () => eligible
    );

    expect(updateConfig).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe('autoSyncGatewayModel', () => {
  it('runs the save once and reports success', async () => {
    const save = vi.fn().mockResolvedValue(undefined);

    await expect(
      autoSyncGatewayModel(save, () => true, { attempts: 3, timeoutMs: 50, retryDelayMs: 1 })
    ).resolves.toBe(true);
    expect(save).toHaveBeenCalledOnce();
  });

  it('retries past a call that never settles (orphaned bridge request)', async () => {
    // #1172 实测：启动即登录时首次 config.get 撞上桥热重启窗口，promise 永不
    // settle；没有超时重试时整个自动就绪就静默死在这里。
    const save = vi.fn().mockImplementationOnce(never).mockResolvedValue(undefined);

    await expect(
      autoSyncGatewayModel(save, () => true, { attempts: 3, timeoutMs: 10, retryDelayMs: 1 })
    ).resolves.toBe(true);
    expect(save).toHaveBeenCalledTimes(2);
  });

  it('gives up after the attempt budget and reports failure', async () => {
    const save = vi.fn().mockImplementation(never);

    await expect(
      autoSyncGatewayModel(save, () => true, { attempts: 3, timeoutMs: 10, retryDelayMs: 1 })
    ).resolves.toBe(false);
    expect(save).toHaveBeenCalledTimes(3);
  });

  it('stops before the first attempt when not eligible', async () => {
    const save = vi.fn().mockResolvedValue(undefined);

    await expect(
      autoSyncGatewayModel(save, () => false, { attempts: 3, timeoutMs: 10, retryDelayMs: 1 })
    ).resolves.toBe(false);
    expect(save).not.toHaveBeenCalled();
  });

  it('stops retrying once eligibility is lost (logout / gateway down)', async () => {
    const save = vi.fn().mockImplementation(never);
    let eligible = true;

    const running = autoSyncGatewayModel(save, () => eligible, {
      attempts: 4,
      timeoutMs: 10,
      retryDelayMs: 1,
    });
    eligible = false;

    await expect(running).resolves.toBe(false);
    expect(save).toHaveBeenCalledTimes(1);
  });
});
