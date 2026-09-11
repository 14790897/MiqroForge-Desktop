import { describe, it, expect } from 'vitest';
import {
  classifySessionError,
  type SessionLoadError,
} from '../src/renderer/features/sessions/SessionExplorer';

describe('classifySessionError', () => {
  // ── REQUIRES_CLAIM ─────────────────────────────────────────────────────

  it('detects REQUIRES_CLAIM in error code', () => {
    const e = { code: 'REQUIRES_CLAIM', message: 'Session needs to be claimed first' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('requires_claim');
    expect(result.code).toBe('REQUIRES_CLAIM');
    expect(result.message).toBe('Session needs to be claimed first');
  });

  it('detects REQUIRES_CLAIM in error message', () => {
    const e = { message: 'Session REQUIRES_CLAIM for ownership transfer' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('requires_claim');
  });

  it('detects unowned in error message', () => {
    const e = { message: 'Cannot load unowned session' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('requires_claim');
  });

  it('detects REQUIRES_CLAIM case-insensitively', () => {
    const e = { message: 'session requires_claim: owner mismatch' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('requires_claim');
  });

  // ── UNAUTHORIZED ───────────────────────────────────────────────────────

  it('detects UNAUTHORIZED in error code', () => {
    const e = { code: 'UNAUTHORIZED', message: 'Access denied' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('unauthorized');
    expect(result.code).toBe('UNAUTHORIZED');
  });

  it('detects unauthorized in error message', () => {
    const e = { message: 'Client unauthorized for this session' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('unauthorized');
  });

  it('detects "not authorized" in error message', () => {
    const e = { message: 'This client is not authorized to access session' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('unauthorized');
  });

  it('detects unauthorized case-insensitively', () => {
    const e = { message: 'UNAUTHORIZED ACCESS DETECTED' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('unauthorized');
  });

  // ── Generic errors ─────────────────────────────────────────────────────

  it('classifies unknown errors as generic', () => {
    const e = { message: 'Something went wrong' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('generic');
    expect(result.message).toBe('Something went wrong');
  });

  it('classifies timeout errors as generic', () => {
    const e = { message: 'Request timed out after 30s' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('generic');
  });

  // ── Lower-bound: raw exception simulations ─────────────────────────────

  it('classifies connection refused as generic (not raw exception)', () => {
    // Simulates a bridge-level error like "ConnectionError: connect ECONNREFUSED"
    const e = { code: 'BRIDGE_ERROR', message: 'Connection refused' };
    const result = classifySessionError(e);
    expect(result.kind).toBe('generic');
    // The UI for 'generic' shows the .message directly — verify it's not a raw
    // exception with paths/URLs leaking through.
    expect(result.message).not.toContain('Traceback');
    expect(result.message).not.toContain('File "');
    expect(result.message).not.toContain('.py", line');
  });

  it('maps Error objects correctly (structural, not raw exception)', () => {
    // An actual Error object from a bridge call rejection
    const err = new Error('Session not found: desktop:legacy-2024');
    const result = classifySessionError(err);
    expect(result.kind).toBe('generic');
    expect(result.message).toBe('Session not found: desktop:legacy-2024');
  });

  // ── Edge cases ─────────────────────────────────────────────────────────

  it('handles null/undefined gracefully', () => {
    const result = classifySessionError(null);
    expect(result.kind).toBe('generic');
    expect(result.message).toBe('');
  });

  it('handles empty object gracefully', () => {
    const result = classifySessionError({});
    expect(result.kind).toBe('generic');
  });

  it('handles string error', () => {
    const result = classifySessionError('plain string error');
    expect(result.kind).toBe('generic');
    expect(result.message).toContain('plain string error');
  });

  it('ensures REQUIRES_CLAIM takes priority over UNAUTHORIZED in combined text', () => {
    // If both are present, requires_claim should win (checked first)
    const e = {
      code: 'REQUIRES_CLAIM',
      message: 'not authorized to view this unowned session',
    };
    const result = classifySessionError(e);
    expect(result.kind).toBe('requires_claim');
  });

  // ── Safety: verify all three kinds produce valid SessionLoadError ──────

  it('always returns a valid SessionLoadError shape', () => {
    const kinds: Array<'requires_claim' | 'unauthorized' | 'generic'> = [
      'requires_claim',
      'unauthorized',
      'generic',
    ];
    const errors = [
      { code: 'REQUIRES_CLAIM', message: 'test' },
      { code: 'UNAUTHORIZED', message: 'test' },
      { message: 'unknown problem' },
    ];
    for (let i = 0; i < errors.length; i++) {
      const r: SessionLoadError = classifySessionError(errors[i]);
      expect(r).toHaveProperty('kind');
      expect(r).toHaveProperty('code');
      expect(r).toHaveProperty('message');
      expect(r.kind).toBe(kinds[i]);
      // code is always a string (may be empty)
      expect(typeof r.code).toBe('string');
      // message is always a string
      expect(typeof r.message).toBe('string');
    }
  });
});
