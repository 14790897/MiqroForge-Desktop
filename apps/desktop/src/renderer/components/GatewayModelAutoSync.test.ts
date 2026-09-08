import { describe, expect, it, vi } from 'vitest';
import { gatewayModelToAutoSet, saveGatewayModelIfBlank } from './GatewayModelAutoSync';

describe('gatewayModelToAutoSet', () => {
  it('returns the gateway model id when the default model is empty', () => {
    expect(gatewayModelToAutoSet({ agents: { defaults: { model: '' } } })).toBe(
      'deepseek/deepseek-v4-flash'
    );
  });

  it('returns the gateway model id when the model field is missing', () => {
    expect(gatewayModelToAutoSet({ agents: { defaults: {} } })).toBe('deepseek/deepseek-v4-flash');
  });

  it('returns null when a non-empty model is already configured', () => {
    expect(
      gatewayModelToAutoSet({ agents: { defaults: { model: 'deepseek/deepseek-v4-pro' } } })
    ).toBeNull();
  });

  it('returns null for malformed config shapes', () => {
    expect(gatewayModelToAutoSet(null)).toBeNull();
    expect(gatewayModelToAutoSet({})).toBeNull();
    expect(gatewayModelToAutoSet({ agents: null })).toBeNull();
  });
});

describe('saveGatewayModelIfBlank', () => {
  it('writes the gateway model with expectModel "" and invalidates cache on success', async () => {
    const getConfig = vi.fn().mockResolvedValue({ agents: { defaults: { model: '' } } });
    const updateConfig = vi.fn().mockResolvedValue({ saved: true });
    const invalidate = vi.fn();

    await saveGatewayModelIfBlank(getConfig, updateConfig, invalidate);

    expect(updateConfig).toHaveBeenCalledWith(
      { agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } },
      ''
    );
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it('does not call update when a model is already configured', async () => {
    const getConfig = vi
      .fn()
      .mockResolvedValue({ agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } });
    const updateConfig = vi.fn();
    const invalidate = vi.fn();

    await saveGatewayModelIfBlank(getConfig, updateConfig, invalidate);

    expect(updateConfig).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('keeps the newer user selection when the backend skips the compare-and-set', async () => {
    const getConfig = vi.fn().mockResolvedValue({ agents: { defaults: { model: '' } } });
    const updateConfig = vi
      .fn()
      .mockResolvedValue({ saved: false, skipped: 'expect_model_mismatch' });
    const invalidate = vi.fn();

    await saveGatewayModelIfBlank(getConfig, updateConfig, invalidate);

    expect(updateConfig).toHaveBeenCalledOnce();
    expect(invalidate).not.toHaveBeenCalled();
  });
});
