import { describe, expect, it } from 'vitest';
import { formatDevVersion } from './dev-version';

describe('formatDevVersion', () => {
  it('appends the short hash to a clean dev build', () => {
    expect(formatDevVersion('0.25.0', { shortHash: 'abc1234', dirty: false })).toBe(
      '0.25.0-dev+abc1234'
    );
  });

  it('marks an uncommitted working tree as dirty', () => {
    expect(formatDevVersion('0.25.0', { shortHash: 'abc1234', dirty: true })).toBe(
      '0.25.0-dev+abc1234.dirty'
    );
  });

  it('degrades to -dev when git info is unavailable', () => {
    expect(formatDevVersion('0.25.0', null)).toBe('0.25.0-dev');
    expect(formatDevVersion('0.25.0', {})).toBe('0.25.0-dev');
  });

  it('does not stack -dev when the base already carries it (package.json 就是 0.32.0-dev)', () => {
    expect(formatDevVersion('0.32.0-dev', { shortHash: 'abc1234', dirty: false })).toBe(
      '0.32.0-dev+abc1234'
    );
    expect(formatDevVersion('0.32.0-dev', { shortHash: 'abc1234', dirty: true })).toBe(
      '0.32.0-dev+abc1234.dirty'
    );
  });
});
