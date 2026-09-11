import { describe, it, expect } from 'vitest';
import {
  getApprovalDisplay,
  getApprovalTitle,
} from '../src/renderer/features/approvals/approvalDisplayUtils';

describe('getApprovalDisplay', () => {
  it('uses pending.command for exec approvals', () => {
    const result = getApprovalDisplay({
      command: 'git status',
      description: 'Run: git status',
    });
    expect(result).toBe('git status');
  });

  it('falls back to details.command when pending.command is empty', () => {
    const result = getApprovalDisplay({
      command: '',
      description: 'Run: npm install',
      details: { command: 'npm install' },
    });
    expect(result).toBe('npm install');
  });

  it('falls back to details.command when pending.command is undefined', () => {
    const result = getApprovalDisplay({
      description: 'Run: cargo build',
      details: { command: 'cargo build' },
    });
    expect(result).toBe('cargo build');
  });

  it('falls back to details.path for file_write approvals', () => {
    const result = getApprovalDisplay({
      command: '',
      description: 'write_file: /tmp/test.py',
      details: { path: '/tmp/test.py', operation: 'write_file' },
    });
    expect(result).toBe('/tmp/test.py');
  });

  it('falls back to details.tool_name for unknown tool approvals', () => {
    const result = getApprovalDisplay({
      command: '',
      description: 'Unknown tool: fancy_tool',
      details: { tool_name: 'fancy_tool' },
    });
    expect(result).toBe('fancy_tool');
  });

  it('falls back to description when nothing else is available', () => {
    const result = getApprovalDisplay({
      command: '',
      description: 'Run: some command',
      details: {},
    });
    expect(result).toBe('Run: some command');
  });

  it('returns fallback text when description is also empty', () => {
    const result = getApprovalDisplay({
      command: '',
      description: '',
    });
    expect(result).toBe('(no details)');
  });
});

describe('getApprovalTitle', () => {
  it('returns 命令审批 for exec category', () => {
    expect(getApprovalTitle('exec')).toBe('命令审批');
  });

  it('returns 文件操作审批 for file_write category', () => {
    expect(getApprovalTitle('file_write')).toBe('文件操作审批');
  });

  it('returns 操作审批 for unknown category', () => {
    expect(getApprovalTitle('unknown_tool')).toBe('操作审批');
  });

  it('returns 操作审批 for undefined category', () => {
    expect(getApprovalTitle(undefined)).toBe('操作审批');
  });

  it('returns 操作审批 for empty string category', () => {
    expect(getApprovalTitle('')).toBe('操作审批');
  });
});
