import { describe, it, expect } from 'vitest';
import { sanitizeUiMessage } from '../src/renderer/lib/sanitizeUiMessage';

describe('sanitizeUiMessage', () => {
  // ── Truncation ─────────────────────────────────────────────────────────

  it('truncates messages longer than 300 chars', () => {
    // Use '#' which is NOT in the base64 token charset, so it won't be
    // consumed by the RE_TOKEN pass (matching Python's test_sanitize_truncates_long_strings).
    const long = '#'.repeat(500);
    const result = sanitizeUiMessage(long);
    expect(result.length).toBe(301); // 300 + '…'
    expect(result.endsWith('…')).toBe(true);
  });

  it('does not truncate short messages', () => {
    const short = 'Connection refused';
    expect(sanitizeUiMessage(short)).toBe('Connection refused');
  });

  it('returns empty string for empty input', () => {
    expect(sanitizeUiMessage('')).toBe('');
  });

  // ── Unix absolute paths ────────────────────────────────────────────────

  it('strips Unix absolute paths', () => {
    const result = sanitizeUiMessage('File not found: /home/user/projects/my-app/src/main.py');
    expect(result).not.toContain('/home/user/projects/my-app/src/main.py');
    expect(result).toContain('[path]');
  });

  it('strips multiple Unix paths', () => {
    const result = sanitizeUiMessage('Cannot read /tmp/a.txt or /var/log/b.log');
    expect(result).not.toContain('/tmp/a.txt');
    expect(result).not.toContain('/var/log/b.log');
    expect(result).toContain('[path]');
  });

  // ── Windows absolute paths ─────────────────────────────────────────────

  it('strips Windows absolute paths', () => {
    const result = sanitizeUiMessage('Error: C:\\Users\\test\\AppData\\Local\\file.json not found');
    expect(result).not.toContain('C:\\Users\\test\\AppData\\Local\\file.json');
    expect(result).toContain('[path]');
  });

  it('strips Windows UNC paths', () => {
    // Use forward slashes in the test string because backslashes in JS strings
    // need escaping — but `\\\\?\\` in source = `\\?\` in memory.
    const result = sanitizeUiMessage('Access denied: \\\\?\\C:\\Users\\admin\\secret.txt');
    expect(result).toContain('[path]');
  });

  // ── URLs ───────────────────────────────────────────────────────────────

  it('strips HTTP URLs', () => {
    const result = sanitizeUiMessage('Failed to fetch http://192.168.1.100:8080/api/keys');
    expect(result).not.toContain('http://192.168.1.100:8080/api/keys');
    expect(result).toContain('[url]');
  });

  it('strips HTTPS URLs', () => {
    const result = sanitizeUiMessage('Auth failed: https://api.example.com/v1/auth?token=abc123');
    expect(result).not.toContain('https://api.example.com');
    expect(result).toContain('[url]');
  });

  // ── Long Base64-like tokens ────────────────────────────────────────────

  it('strips long base64 tokens (40+ chars)', () => {
    const token =
      'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0';
    const result = sanitizeUiMessage(`Invalid token: ${token}`);
    expect(result).not.toContain(token);
    expect(result).toContain('[token]');
  });

  it('preserves short tokens under 40 chars', () => {
    const short = 'abc123';
    const result = sanitizeUiMessage(`Key: ${short}`);
    expect(result).toContain(short);
  });

  // ── Combined sanitization ──────────────────────────────────────────────

  it('sanitizes paths, URLs, and tokens in one pass', () => {
    const token = 'a'.repeat(40);
    // Place token outside the URL so it isn't consumed by the URL regex
    const input = `Error at /home/user/file.py: GET https://evil.com/leak failed with token=${token}`;
    const result = sanitizeUiMessage(input);
    expect(result).toContain('[path]');
    expect(result).toContain('[url]');
    expect(result).toContain('[token]');
    // Should NOT contain any raw sensitive data
    expect(result).not.toContain('/home/user');
    expect(result).not.toContain('evil.com');
    expect(result).not.toContain(token);
  });

  // ── Safe messages pass through ─────────────────────────────────────────

  it('passes through safe messages unchanged', () => {
    const safe = 'Tool execution timed out after 30 seconds';
    expect(sanitizeUiMessage(safe)).toBe(safe);
  });

  it('passes through short error names unchanged', () => {
    expect(sanitizeUiMessage('BridgeNotReadyError: backend not running')).toBe(
      'BridgeNotReadyError: backend not running'
    );
  });

  // ── Edge cases ─────────────────────────────────────────────────────────

  it('handles messages that are exactly 300 chars', () => {
    // Use '#' which is NOT in the token charset
    const exact = '#'.repeat(300);
    const result = sanitizeUiMessage(exact);
    expect(result.length).toBe(300); // no truncation needed
    expect(result).toBe(exact);
  });

  it('handles newlines and special chars in safe messages', () => {
    const msg = 'Error: connection refused\nPlease try again.\nDetails: timeout=30s';
    const result = sanitizeUiMessage(msg);
    // Should not be affected by sanitization (no paths/URLs/tokens)
    expect(result).toContain('connection refused');
  });
});
