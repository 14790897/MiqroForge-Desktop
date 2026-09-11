import { describe, it, expect } from 'vitest';
import {
  extractProgressMessage,
  type ProgressPayload,
} from '../src/renderer/features/chat/progressUtils';

describe('extractProgressMessage', () => {
  // ── Direct text (happy path) ────────────────────────────────────────

  it('returns progress role for direct text', () => {
    const result = extractProgressMessage({ text: 'Hello world' });
    expect(result).toEqual({ message: 'Hello world', role: 'progress' });
  });

  it('returns null for whitespace-only text', () => {
    const result = extractProgressMessage({ text: '   ' });
    expect(result).toBeNull();
  });

  it('returns null for empty payload', () => {
    const result = extractProgressMessage({});
    expect(result).toBeNull();
  });

  // ── Structured ErrorEvent ───────────────────────────────────────────

  it('detects ErrorEvent with data.message', () => {
    const result = extractProgressMessage({
      event: 'ErrorEvent',
      data: { message: 'Provider auth failed', error_kind: 'auth' },
    });
    expect(result).toEqual({
      message: 'Provider auth failed',
      role: 'error',
    });
  });

  it('detects ErrorEvent with nested error.message', () => {
    const result = extractProgressMessage({
      event: 'RuntimeError',
      data: { error: { message: 'Connection refused' }, error_kind: 'network' },
    });
    expect(result).toEqual({
      message: 'Connection refused',
      role: 'error',
    });
  });

  it('detects ErrorEvent with only reason field', () => {
    const result = extractProgressMessage({
      event: 'ToolError',
      data: { reason: 'Tool not found' },
    });
    expect(result).toEqual({
      message: 'Tool not found',
      role: 'error',
    });
  });

  it('falls back to event name for ErrorEvent with no data', () => {
    const result = extractProgressMessage({
      event: 'ErrorEvent',
    });
    expect(result).toEqual({
      message: 'ErrorEvent',
      role: 'error',
    });
  });

  it('detects error via error_kind even without Error in event name', () => {
    const result = extractProgressMessage({
      event: 'ProviderResponse',
      data: { message: 'Invalid API key', error_kind: 'auth' },
    });
    expect(result).toEqual({
      message: 'Invalid API key',
      role: 'error',
    });
  });

  it('prefers data.message over error.message when both present', () => {
    const result = extractProgressMessage({
      event: 'ErrorEvent',
      data: {
        message: 'Direct message',
        error: { message: 'Nested message' },
      },
    });
    expect(result).toEqual({
      message: 'Direct message',
      role: 'error',
    });
  });

  // ── ToolErrorEvent demotion（可恢复的工具级失败不当作系统报错）──

  it('renders ToolErrorEvent as warning, not error', () => {
    const result = extractProgressMessage({
      event: 'ToolErrorEvent',
      data: {
        message: 'PermissionError: 路径位于其他会话的 files 目录内——会话隔离禁止跨会话访问。',
        recoverable: true,
      },
    });
    expect(result).toEqual({
      message: 'PermissionError: 路径位于其他会话的 files 目录内——会话隔离禁止跨会话访问。',
      role: 'warning',
    });
  });

  it('falls back to a friendly label for ToolErrorEvent without a message', () => {
    const result = extractProgressMessage({
      event: 'ToolErrorEvent',
      data: {},
    });
    expect(result).toEqual({
      message: '工具执行失败',
      role: 'warning',
    });
  });

  // ── Structured WarningEvent ─────────────────────────────────────────

  it('detects WarningEvent with data.message', () => {
    const result = extractProgressMessage({
      event: 'WarningEvent',
      data: { message: 'Token usage approaching limit' },
    });
    expect(result).toEqual({
      message: 'Token usage approaching limit',
      role: 'warning',
    });
  });

  it('detects warn-like event names', () => {
    const result = extractProgressMessage({
      event: 'Warn',
      data: { message: 'Low memory' },
    });
    expect(result).toEqual({
      message: 'Low memory',
      role: 'warning',
    });
  });

  // ── Unknown structured events ───────────────────────────────────────

  it('renders unknown structured events as progress with message', () => {
    const result = extractProgressMessage({
      event: 'AgentReasoningEvent',
      data: { message: 'Analyzing requirements...' },
    });
    expect(result).toEqual({
      message: 'Analyzing requirements...',
      role: 'progress',
    });
  });

  it('renders unknown event without message as bracketed event name', () => {
    const result = extractProgressMessage({
      event: 'SomethingHappened',
    });
    expect(result).toEqual({
      message: '[SomethingHappened]',
      role: 'progress',
    });
  });

  // ── Tool-hint / stream events (non-text) ────────────────────────────

  // An event with a name but no displayable message still gets a bracketed label
  it('renders event name for event with non-displayable data only', () => {
    const result = extractProgressMessage({
      event: 'AgentReasoningEvent',
      data: { reasoning: '...' },
    });
    expect(result).toEqual({
      message: '[AgentReasoningEvent]',
      role: 'progress',
    });
  });

  it('returns null for truly empty payload with no event name', () => {
    // No event name, no text, no data → should be null
    const result = extractProgressMessage({ stream: 'stdout' });
    expect(result).toBeNull();
  });

  it('handles tool_hint flag combined with text', () => {
    const result = extractProgressMessage({
      text: 'Read: /path/to/file.ts',
      tool_hint: true,
    });
    expect(result).toEqual({
      message: 'Read: /path/to/file.ts',
      role: 'progress',
    });
  });

  // ── Exec event filtering (issue #236) ──────────────────────────────────

  it('skips outputDelta events to prevent flooding workbench', () => {
    const result = extractProgressMessage({
      event: 'item/commandExecution/outputDelta',
      stream: 'stdout',
      delta: 'some output',
    });
    expect(result).toBeNull();
  });

  it('skips generic commandExecution events', () => {
    const result = extractProgressMessage({
      event: 'commandExecution/completed',
      data: { command: 'echo hello', exitCode: 0 },
    });
    expect(result).toBeNull();
  });
});
