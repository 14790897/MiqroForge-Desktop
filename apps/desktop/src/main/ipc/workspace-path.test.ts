import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { mkdirSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import {
  getWorkspacePath,
  isWithinCanonicalWorkspace,
  resolveWorkspacePath,
} from './workspace-path';

const isWin = process.platform === 'win32';

/** Normalise a path for comparison: forward slashes + lowercase. */
function norm(p: string): string {
  return p.replace(/\\/g, '/').toLowerCase();
}

/** Turn a Windows path like C:\a\b into a WSL /mnt/c/a/b path. */
function toMnt(winPath: string): string {
  return '/mnt/' + winPath[0].toLowerCase() + winPath.slice(2).replace(/\\/g, '/');
}

describe('resolveWorkspacePath', () => {
  let wsRoot: string;

  beforeEach(() => {
    // Point MIQI_HOME at a fresh temp dir (no config.json) so the default
    // workspace rebases to <tmp>/workspace and never reads the real ~/.miqi.
    const home = join(
      tmpdir(),
      `miqi-ws-test-${Date.now()}-${Math.random().toString(36).slice(2)}`
    );
    process.env['MIQI_HOME'] = home;
    wsRoot = getWorkspacePath();
  });

  afterEach(() => {
    delete process.env['MIQI_HOME'];
  });

  it('resolves a workspace-relative path', () => {
    expect(resolveWorkspacePath('report.md')).toBe(join(wsRoot, 'report.md'));
  });

  it('resolves a /home/miqi/workspace sandbox path', () => {
    expect(resolveWorkspacePath('/home/miqi/workspace/report.md')).toBe(join(wsRoot, 'report.md'));
  });

  it('rejects .. traversal escaping the workspace', () => {
    expect(() => resolveWorkspacePath('../secret.txt')).toThrow(/outside workspace/);
  });

  it('rejects an absolute path outside the workspace', () => {
    const outside = join(wsRoot, '..');
    expect(() => resolveWorkspacePath(outside)).toThrow(/outside workspace/);
  });

  // /mnt conversion is Windows-specific (WSL mount paths).
  const winOnly = isWin ? describe : describe.skip;
  winOnly('Windows /mnt handling (#955)', () => {
    it('resolves a /mnt path that stays inside the workspace', () => {
      const target = join(wsRoot, 'report.md');
      expect(norm(resolveWorkspacePath(toMnt(target)))).toBe(norm(target));
    });

    it('rejects a /mnt path outside the workspace', () => {
      expect(() => resolveWorkspacePath('/mnt/c/Windows/System32/calc.exe')).toThrow(
        /outside workspace/
      );
    });

    it('matches /mnt paths case-insensitively (lowercase drive workspace)', () => {
      // Configure a workspace with a lowercase drive letter, then resolve a
      // /mnt path (which uppercases the drive) — must not be rejected.
      const lowerHome = 'c' + tmpdir().slice(1);
      process.env['MIQI_HOME'] = join(lowerHome, 'miqi-ws-lower');
      const lowerWs = getWorkspacePath();
      const target = join(lowerWs, 'report.md');
      expect(norm(resolveWorkspacePath(toMnt(target)))).toBe(norm(target));
    });

    it('rejects an absolute path with literal .. traversal', () => {
      const escaped = `${wsRoot}\\..\\..\\Windows\\System32\\calc.exe`;
      expect(() => resolveWorkspacePath(escaped)).toThrow(/outside workspace/);
    });

    it('rejects a /mnt path with .. traversal escaping the workspace', () => {
      const escaped = toMnt(`${wsRoot}\\..\\..\\Windows\\System32\\calc.exe`);
      expect(() => resolveWorkspacePath(escaped)).toThrow(/outside workspace/);
    });
  });
});

describe('isWithinCanonicalWorkspace', () => {
  let wsRoot: string;

  beforeEach(() => {
    const home = join(
      tmpdir(),
      `miqi-ws-test-${Date.now()}-${Math.random().toString(36).slice(2)}`
    );
    process.env['MIQI_HOME'] = home;
    wsRoot = getWorkspacePath();
    mkdirSync(wsRoot, { recursive: true });
  });

  afterEach(() => {
    delete process.env['MIQI_HOME'];
  });

  it('accepts a path inside the workspace', () => {
    expect(isWithinCanonicalWorkspace(wsRoot, wsRoot)).toBe(true);
  });

  it('rejects a path outside the workspace', () => {
    expect(isWithinCanonicalWorkspace(tmpdir(), wsRoot)).toBe(false);
  });

  it('accepts a non-existent path (lexical check covers it)', () => {
    expect(isWithinCanonicalWorkspace(join(wsRoot, 'no-such-file.txt'), wsRoot)).toBe(true);
  });
});
