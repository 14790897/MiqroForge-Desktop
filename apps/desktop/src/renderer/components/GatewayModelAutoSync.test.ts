import { describe, expect, it, vi } from 'vitest';
import {
  currentDefaultModel,
  gatewayModelToAutoSet,
  saveGatewayModelIfUnusable,
} from './GatewayModelAutoSync';

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
  });
});

describe('gatewayModelToAutoSet', () => {
  it('falls back to the gateway model when nothing is set (even on an old bridge)', () => {
    expect(gatewayModelToAutoSet('', undefined)).toBe('deepseek/deepseek-v4-flash');
    expect(gatewayModelToAutoSet('', false)).toBe('deepseek/deepseek-v4-flash');
  });

  it('replaces an unusable model — the fresh-install default is never empty', () => {
    expect(gatewayModelToAutoSet('anthropic/claude-opus-4-5', false)).toBe(
      'deepseek/deepseek-v4-flash'
    );
    expect(gatewayModelToAutoSet('custom/legacy-model', false)).toBe('deepseek/deepseek-v4-flash');
  });

  it('keeps a usable model (builtin activation / configured gateway fallback)', () => {
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
    const listProviders = vi.fn().mockResolvedValue({ active_model_resolvable: false });
    const updateConfig = vi.fn().mockResolvedValue({ saved: true });
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(getConfig, listProviders, updateConfig, invalidate);

    expect(updateConfig).toHaveBeenCalledWith(
      { agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } },
      'anthropic/claude-opus-4-5'
    );
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it('writes the gateway model with expectModel "" when nothing is set', async () => {
    const getConfig = vi.fn().mockResolvedValue({ agents: { defaults: { model: '' } } });
    const listProviders = vi.fn().mockResolvedValue({ active_model_resolvable: false });
    const updateConfig = vi.fn().mockResolvedValue({ saved: true });
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(getConfig, listProviders, updateConfig, invalidate);

    expect(updateConfig).toHaveBeenCalledWith(
      { agents: { defaults: { model: 'deepseek/deepseek-v4-flash' } } },
      ''
    );
    expect(invalidate).toHaveBeenCalledOnce();
  });

  it('does not call update when the current model is usable', async () => {
    const getConfig = vi
      .fn()
      .mockResolvedValue({ agents: { defaults: { model: 'deepseek/deepseek-v4-pro' } } });
    const listProviders = vi.fn().mockResolvedValue({ active_model_resolvable: true });
    const updateConfig = vi.fn();
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(getConfig, listProviders, updateConfig, invalidate);

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

    await saveGatewayModelIfUnusable(getConfig, listProviders, updateConfig, invalidate);

    expect(listProviders).not.toHaveBeenCalled();
    expect(updateConfig).not.toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('keeps the newer user selection when the backend skips the compare-and-set', async () => {
    const getConfig = vi.fn().mockResolvedValue({ agents: { defaults: { model: '' } } });
    const listProviders = vi.fn().mockResolvedValue({ active_model_resolvable: false });
    const updateConfig = vi
      .fn()
      .mockResolvedValue({ saved: false, skipped: 'expect_model_mismatch' });
    const invalidate = vi.fn();

    await saveGatewayModelIfUnusable(getConfig, listProviders, updateConfig, invalidate);

    expect(updateConfig).toHaveBeenCalledOnce();
    expect(invalidate).not.toHaveBeenCalled();
  });
});
