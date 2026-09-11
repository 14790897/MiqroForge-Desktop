/**
 * Unit tests for the main-process log redaction in electron-log.ts.
 *
 * The redaction masks credential-looking key=value / key: value pairs in
 * console output. Metric keys that merely CONTAIN a keyword
 * (first_token_latency_ms, prompt_tokens, ...) must stay untouched.
 */

import { describe, it, expect } from 'vitest';
import { redactMessage } from './electron-log';

describe('redactMessage', () => {
  it('masks credential keys ending in a keyword', () => {
    expect(redactMessage('api_key=sk-abc123')).toBe('api_key=[REDACTED]');
    expect(redactMessage('access_token=sk-xyz')).toBe('access_token=[REDACTED]');
    expect(redactMessage('client_secret=supersecret')).toBe('client_secret=[REDACTED]');
    expect(redactMessage('password=hunter2')).toBe('password=[REDACTED]');
    expect(redactMessage('apiKey=sk-camel')).toBe('apiKey=[REDACTED]');
  });

  it('masks colon-separated authorization headers', () => {
    // The separator is normalised to `=` by the replacement (pre-existing shape).
    expect(redactMessage('Authorization: Bearer sk-abc123')).toBe('Authorization=[REDACTED]');
  });

  it('fully masks multi-word credential values without partial leaks', () => {
    // 4-word passphrase: every word must be consumed, nothing may persist.
    expect(redactMessage('password: correct horse battery staple')).toBe('password=[REDACTED]');
    expect(redactMessage('password=correct horse battery staple')).toBe('password=[REDACTED]');
    // Trailing context on the same line is consumed too (over-redaction
    // is safer than leaking the credential).
    expect(redactMessage('Authorization: Bearer sk-1 for turn=t-1')).toBe(
      'Authorization=[REDACTED]'
    );
    // Values delimited by comma/semicolon stop there.
    expect(redactMessage('api_key=sk-1, model=kimi')).toBe('api_key=[REDACTED], model=kimi');
  });

  it('leaves metric keys that only contain a keyword untouched', () => {
    const line = 'turn_runner: first_token_latency_ms=1234 for turn=t-1 (reasoning)';
    expect(redactMessage(line)).toBe(line);

    expect(redactMessage('prompt_tokens=500')).toBe('prompt_tokens=500');
    expect(redactMessage('completion_tokens=120')).toBe('completion_tokens=120');
    expect(redactMessage('token_usage={"total":620}')).toBe('token_usage={"total":620}');
    expect(redactMessage('max_tokens=8000')).toBe('max_tokens=8000');
  });

  it('leaves plain messages untouched', () => {
    const line = 'AppServer: registered method chat.send';
    expect(redactMessage(line)).toBe(line);
  });
});
