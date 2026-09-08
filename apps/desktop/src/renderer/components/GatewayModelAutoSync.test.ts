import { describe, expect, it } from 'vitest';
import { gatewayModelToAutoSet } from './GatewayModelAutoSync';

describe('gatewayModelToAutoSet', () => {
  it('returns the gateway model id when the default model is empty', () => {
    expect(gatewayModelToAutoSet({ agents: { defaults: { model: '' } } })).toBe(
      'deepseek/deepseek-v4-flash'
    );
  });

  it('returns the gateway model id when the model field is missing', () => {
    expect(gatewayModelToAutoSet({ agents: { defaults: {} } })).toBe(
      'deepseek/deepseek-v4-flash'
    );
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
