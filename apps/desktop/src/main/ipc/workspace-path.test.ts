import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { mkdirSync, symlinkSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import {
  buildWslSearchScript,
  getWorkspacePath,
  isWithinCanonicalWorkspace,
  resolveWorkspacePath,
  sanitizeSessionKeyForPath,
  sessionFilesDirKey,
  shellEscape,
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

  it('does not treat an unresolvable root as containment', () => {
    // 只有 candidate 解析失败才 fail-open；root 解析不了不是包含的证据。
    // 旧实现 catch → true 会让一个不存在的 root 把整个 `.some()` 短路成
    // 「允许」，从而接受一个并不在该 root 下的 candidate。
    const ghostRoot = join(tmpdir(), `miqi-no-such-root-${Date.now()}`);
    expect(isWithinCanonicalWorkspace(tmpdir(), ghostRoot)).toBe(false);
  });
});

// #1062: 文件夹绑定会话的产物在会话自己的工作区里，主进程把它作为额外允许根。
// 根由服务端从 session_key 推导，所以这里只验证「额外根是否生效」这一契约。
describe('extra roots (#1062 folder-bound sessions)', () => {
  let wsRoot: string;
  let bound: string;

  beforeEach(() => {
    const home = join(
      tmpdir(),
      `miqi-ws-extra-${Date.now()}-${Math.random().toString(36).slice(2)}`
    );
    process.env['MIQI_HOME'] = home;
    wsRoot = getWorkspacePath();
    bound = join(tmpdir(), `miqi-bound-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  });

  afterEach(() => {
    delete process.env['MIQI_HOME'];
  });

  it('accepts a path under an extra root', () => {
    const target = join(bound, 'song.pdf');
    expect(norm(resolveWorkspacePath(target, [bound]))).toBe(norm(target));
  });

  it('anchors a relative path on the extra root, not the global workspace', () => {
    // 账本里存的是相对路径，绑定会话的相对路径就是相对它自己的工作区——
    // 锚在全局根上会去错地方找，把存在的文件报成「找不到」。
    expect(norm(resolveWorkspacePath('report.md', [bound]))).toBe(norm(join(bound, 'report.md')));
  });

  it('keeps anchoring on the global workspace when no extra root applies', () => {
    expect(resolveWorkspacePath('report.md')).toBe(join(wsRoot, 'report.md'));
    expect(resolveWorkspacePath('report.md', [])).toBe(join(wsRoot, 'report.md'));
    expect(resolveWorkspacePath('report.md', [null, undefined])).toBe(join(wsRoot, 'report.md'));
  });

  it('rejects a path under neither root', () => {
    expect(() => resolveWorkspacePath(join(tmpdir(), 'elsewhere.txt'), [bound])).toThrow(
      /outside workspace/
    );
  });

  it('rejects .. escaping the extra root', () => {
    expect(() => resolveWorkspacePath(join(bound, '..', 'elsewhere.txt'), [bound])).toThrow(
      /outside workspace/
    );
  });

  it('ignores a non-absolute extra root instead of trusting it', () => {
    expect(() => resolveWorkspacePath(join(tmpdir(), 'elsewhere.txt'), ['../../..'])).toThrow(
      /outside workspace/
    );
  });

  it('accepts a path inside either root canonically', () => {
    expect(isWithinCanonicalWorkspace(wsRoot, wsRoot, [bound])).toBe(true);
  });

  const winOnly = isWin ? describe : describe.skip;
  winOnly('Windows /mnt with an extra root', () => {
    it('accepts a /mnt path inside the extra root', () => {
      const target = join(bound, 'song.pdf');
      expect(norm(resolveWorkspacePath(toMnt(target), [bound]))).toBe(norm(target));
    });

    it('rejects a /mnt path outside both roots', () => {
      expect(() => resolveWorkspacePath('/mnt/c/Windows/System32/calc.exe', [bound])).toThrow(
        /outside workspace/
      );
    });
  });
});

// #1062 macOS：运行时返回的会话根是**规范化过**的（`/var` → `/private/var`），
// 而调用方手里往往是未规范化的同一位置。只做词法比较会把合法文件判成越界。
describe('symlinked allowed root (#1062 macOS /var)', () => {
  let home: string;
  let wsRoot: string;

  beforeEach(() => {
    home = join(tmpdir(), `miqi-ws-link-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    process.env['MIQI_HOME'] = home;
    wsRoot = join(home, 'workspace');
    mkdirSync(wsRoot, { recursive: true });
  });

  afterEach(() => {
    delete process.env['MIQI_HOME'];
  });

  it('accepts the unresolved spelling of a path under a symlinked root', () => {
    const real = join(home, 'outside-real');
    mkdirSync(real, { recursive: true });
    const link = join(wsRoot, 'link');
    try {
      symlinkSync(real, link, 'junction');
    } catch {
      return; // 该环境不支持创建符号链接 / junction
    }

    // `real/missing.txt` 词法上不在 `link` 下、也不在工作区下；但它的真实位置
    // 就是 `link/missing.txt` 的真实位置 —— 必须接受。
    expect(() => resolveWorkspacePath(join(real, 'missing.txt'), [link])).not.toThrow();

    // 规范化收紧而不是放宽：真正在允许根之外的路径仍然被拒。
    expect(() => resolveWorkspacePath(join(tmpdir(), 'elsewhere.txt'), [link])).toThrow(
      /outside workspace/
    );
  });

  it('rejects a symlink inside the workspace that points outside it', () => {
    // 词法上 `escape/secret.txt` 就在工作区里，真实位置却在外面 —— 只做词法比较
    // 会放它过去，`openExternal` 那条路径没有第二次 canonical 校验（#1103 review）。
    const outside = join(home, 'outside-dir');
    mkdirSync(outside, { recursive: true });
    const link = join(wsRoot, 'escape');
    try {
      symlinkSync(outside, link, 'junction');
    } catch {
      return; // 该环境不支持创建符号链接 / junction
    }

    expect(() => resolveWorkspacePath(join(link, 'secret.txt'), [])).toThrow(/outside workspace/);
  });
});

// #1103: session_key is renderer-controlled and ends up inside the WSL search
// script.  The sanitizer must neutralize shell metacharacters / command
// substitution while keeping everyday keys usable.
describe('sanitizeSessionKeyForPath (#1103)', () => {
  it('preserves safe session keys', () => {
    expect(sanitizeSessionKeyForPath('c1:s1')).toBe('c1_s1');
    expect(sanitizeSessionKeyForPath('session_123')).toBe('session_123');
    expect(sanitizeSessionKeyForPath('my-session.key')).toBe('my-session.key');
  });

  it('neutralizes shell command-substitution payloads', () => {
    expect(sanitizeSessionKeyForPath('$(touch /tmp/pwn)')).toBe('__touch__tmp_pwn_');
    expect(sanitizeSessionKeyForPath('`touch /tmp/pwn`')).toBe('_touch__tmp_pwn_');
  });

  it('neutralizes quotes, semicolons and path separators', () => {
    expect(sanitizeSessionKeyForPath('foo/bar')).toBe('foo_bar');
    expect(sanitizeSessionKeyForPath('foo\\bar')).toBe('foo_bar');
    expect(sanitizeSessionKeyForPath('foo"; touch /tmp/pwn; echo "')).toBe(
      'foo___touch__tmp_pwn__echo__'
    );
  });

  it('replaces all unsafe characters with underscores', () => {
    // Whitespace, unicode, brackets, dollar, ampersand, pipe, etc.
    expect(sanitizeSessionKeyForPath('a b$c|d&e')).toBe('a_b_c_d_e');
  });
});

// #1103: The session-private files directory uses the canonical directory key
// (`session_files_dir_key` on the Python side), which strips the client_id
// prefix for fully-namespaced keys.  The sandbox path uses the full sanitized
// key.  They must not be conflated.
describe('sessionFilesDirKey (#1103)', () => {
  it('strips the client_id prefix for fully-namespaced keys', () => {
    expect(sessionFilesDirKey('miqi-desktop:desktop:1786807046853')).toBe('desktop_1786807046853');
  });

  it('keeps two-segment keys unchanged', () => {
    expect(sessionFilesDirKey('desktop:1786807046853')).toBe('desktop_1786807046853');
    expect(sessionFilesDirKey('cli:direct')).toBe('cli_direct');
  });
});

// #1103: WSL search script must sanitize the session key and canonicalize the
// candidate against its authorization root so a workspace symlink cannot escape.
describe('buildWslSearchScript (#1103)', () => {
  it('sanitizes the session key before embedding it in the script', () => {
    const script = buildWslSearchScript('report.md', '$(touch /tmp/pwn)');
    expect(script).not.toContain('$(touch /tmp/pwn)');
    expect(script).toContain('/tmp/miqi-sandboxes/__touch__tmp_pwn_');
  });

  it('escapes single quotes in the relative path', () => {
    const script = buildWslSearchScript("it's'here.md", 'desktop:123');
    expect(script).toContain("it'\\''s'\\''here.md");
    expect(script).not.toContain("$'it's'here.md'");
  });

  it('includes canonical containment checks', () => {
    const script = buildWslSearchScript('report.md', 'desktop:123');
    expect(script).toContain('readlink -f "$found"');
    expect(script).toContain('readlink -f "$root"');
    expect(script).toContain('case "$canon" in');
    expect(script).toContain('"$root_canon"|"$root_canon"/*');
  });

  it('searches session sandbox, session files, global workspace and global files', () => {
    const script = buildWslSearchScript('report.md', 'desktop:123');
    expect(script).toContain('/tmp/miqi-sandboxes/desktop_123/home/miqi/workspace');
    expect(script).toContain('/sessions/desktop_123/files');
    expect(script).toContain('$HOME/.miqi/workspace');
  });

  it('uses the canonical session-files directory for namespaced keys', () => {
    const script = buildWslSearchScript('report.md', 'miqi-desktop:desktop:123');
    expect(script).toContain('/tmp/miqi-sandboxes/miqi-desktop_desktop_123/home/miqi/workspace');
    expect(script).toContain('/sessions/desktop_123/files');
    expect(script).not.toContain('/sessions/miqi-desktop_desktop_123/files');
  });
});
