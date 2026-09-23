import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { existsSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { dirname, join } from 'path';
import {
  buildWslSearchScript,
  claimLegacyWorkspace,
  clearActiveAccount,
  getDefaultWorkspacePath,
  getWorkspacePath,
  isWithinCanonicalWorkspace,
  readActiveAccount,
  readLegacyWorkspaceOwner,
  resolveWorkspacePath,
  sanitizeSessionKeyForPath,
  sessionFilesDirKey,
  setActiveAccount,
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
    // workspace rebases to <tmp>/workspace and never reads the real ~/.forge.
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
  // #1185: 全局工作区那一行现在由账号决定，所以这组用例必须自己钉住数据根——
  // 没有账号标记时才是它断言的那个 `$HOME/.forge/workspace`。
  //
  // 在此之前它不设 MIQI_HOME，靠的是「本文件前面的用例都还原了环境」这个巧合；
  // 一旦环境里带着一个活跃账号（同 worker 的另一个测试文件留下的 MIQI_HOME、
  // 或开发机上真实存在的 ~/.forge/accounts/.active），断言就会读到一个按账号
  // 分过的路径而失败——CI 上正是这么挂的。
  let home: string;

  beforeEach(() => {
    home = join(tmpdir(), `miqi-wsl-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    process.env['MIQI_HOME'] = home;
    expect(readActiveAccount()).toBeNull();
  });

  afterEach(() => {
    delete process.env['MIQI_HOME'];
    rmSync(home, { recursive: true, force: true });
  });

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
    expect(script).toContain('$HOME/.forge/workspace');
  });

  it('omits the global workspace for folder-bound sessions (#1103 review)', () => {
    const script = buildWslSearchScript('report.md', 'desktop:123', {
      allowGlobalWorkspace: false,
    });
    // 绑定会话的相对路径锚在绑定目录上：那里没有就该报 not found，不能退到全局
    // 工作区——否则全局的同名文件会被 copyFromWsl 复制进绑定目录再打开。
    expect(script).not.toContain('$HOME/.forge/workspace');
    expect(script).not.toContain('"$ws/$RP"');
    expect(script).not.toContain('"$s/$RP"');
    // 会话自己的 WSL 位置仍然可搜
    expect(script).toContain('"$W/$RP"');
    expect(script).toContain('"$S/$RP"');
  });

  it('uses the canonical session-files directory for namespaced keys', () => {
    const script = buildWslSearchScript('report.md', 'miqi-desktop:desktop:123');
    expect(script).toContain('/tmp/miqi-sandboxes/miqi-desktop_desktop_123/home/miqi/workspace');
    expect(script).toContain('/sessions/desktop_123/files');
    expect(script).not.toContain('/sessions/miqi-desktop_desktop_123/files');
  });
});

// #1185: 本地存储按登录账号划分。会话、记忆、技能、经验都挂在工作区根下，
// 所以「账号维度」就是工作区根的维度。这里覆盖主进程这一侧的规则；Python
// 侧的同名规则由 tests/test_account_workspace.py 覆盖，两边必须一致。
describe('account-scoped workspace (#1185)', () => {
  let home: string;

  beforeEach(() => {
    home = join(tmpdir(), `miqi-acct-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(home, { recursive: true });
    process.env['MIQI_HOME'] = home;
  });

  afterEach(() => {
    delete process.env['MIQI_HOME'];
    rmSync(home, { recursive: true, force: true });
  });

  const accountsDir = () => join(home, 'accounts');
  const writeMarker = (name: string, value: string) => {
    mkdirSync(accountsDir(), { recursive: true });
    writeFileSync(join(accountsDir(), name), value, 'utf8');
  };
  const writeConfig = (workspace: string) => {
    writeFileSync(
      join(home, 'config.json'),
      JSON.stringify({ agents: { defaults: { workspace } } }),
      'utf8'
    );
  };

  it('keeps the shared workspace while no account is logged in', () => {
    expect(readActiveAccount()).toBeNull();
    expect(getDefaultWorkspacePath()).toBe(join(home, 'workspace'));
    expect(getWorkspacePath()).toBe(join(home, 'workspace'));
  });

  it('scopes the default workspace to the logged-in account', () => {
    writeMarker('.active', '19');

    expect(readActiveAccount()).toBe('19');
    expect(getDefaultWorkspacePath()).toBe(join(accountsDir(), '19', 'workspace'));
    // 配置里是默认值 → 跟随账号；配置里是自定义目录 → 不跟随（下一个用例）。
    writeConfig('~/.forge/workspace');
    expect(getWorkspacePath()).toBe(join(accountsDir(), '19', 'workspace'));
  });

  it('switches roots when the account switches', () => {
    writeMarker('.active', '19');
    const a = getDefaultWorkspacePath();
    writeMarker('.active', '20');
    const b = getDefaultWorkspacePath();

    expect(a).not.toBe(b);
    expect(b).toBe(join(accountsDir(), '20', 'workspace'));
  });

  it('does not account-scope a user-picked workspace', () => {
    const custom = join(tmpdir(), 'miqi-custom-workspace');
    writeMarker('.active', '19');
    writeConfig(custom);

    // 用户明确指到这个目录，尊重他的选择：宁可提示设备内共享，也不把他
    // 的项目目录搬到一个按账号分的子目录下（#1185 item 6）。
    expect(getWorkspacePath()).toBe(custom);
  });

  it('treats a traversal in the marker as "no account"', () => {
    // 'a\\b' 是反斜杠（Windows 分隔符）；写成 'a\b' 会是 U+0008，那这条用例
    // 就没在测分隔符了。
    for (const bad of ['..', '.', '../evil', 'a/b', 'a\\b', '']) {
      writeMarker('.active', bad);
      expect(readActiveAccount()).toBeNull();
      expect(getDefaultWorkspacePath()).toBe(join(home, 'workspace'));
    }
  });

  it('round-trips the active account marker', () => {
    setActiveAccount('19');
    expect(readActiveAccount()).toBe('19');
    clearActiveAccount();
    expect(readActiveAccount()).toBeNull();
  });

  it('throws when the marker cannot be replaced', () => {
    // 把 .active 占成目录：rename 没法用它替换，于是写入失败。这里必须是**抛**，
    // 不能像以前那样静默吞掉 —— 静默失败会留下上一个账号的标记，而长期驻留的
    // 运行时每次解析工作区都读它（#1185 评审）。
    mkdirSync(join(accountsDir(), '.active'), { recursive: true });

    expect(() => setActiveAccount('19')).toThrow(/账号标记写入失败/);
  });

  it('throws when the marker cannot be cleared', () => {
    // 把 .active 占成**目录**：`rmSync` 不带 recursive 会 EISDIR（force 只吞
    // ENOENT），退一步的「写坏它」也会 EISDIR —— 两条路都堵死时必须抛。
    // 静默成功会让 logout() 报 ok，而运行时继续按上一个账号解析工作区
    // （#1185 评审）。
    mkdirSync(join(accountsDir(), '.active'), { recursive: true });

    expect(() => clearActiveAccount()).toThrow(/无法清除账号标记/);
  });

  it('leaves the legacy workspace to the first account that claims it', () => {
    mkdirSync(join(home, 'workspace', 'sessions', 'desktop_k'), { recursive: true });
    setActiveAccount('19');
    claimLegacyWorkspace('19');

    expect(readLegacyWorkspaceOwner()).toBe('19');
    // 认领方就地继续用旧目录（不搬家：rename 会撞上仍开着句柄的 bridge，
    // 而失败与「数据消失」在用户眼里没有区别）。
    expect(getDefaultWorkspacePath()).toBe(join(home, 'workspace'));

    // 另一个账号拿到自己的空目录，看不到那份存量数据。
    setActiveAccount('20');
    expect(getDefaultWorkspacePath()).toBe(join(accountsDir(), '20', 'workspace'));
  });

  it('never re-claims an already-claimed legacy workspace', () => {
    mkdirSync(join(home, 'workspace'), { recursive: true });
    claimLegacyWorkspace('19');
    claimLegacyWorkspace('20');

    expect(readLegacyWorkspaceOwner()).toBe('19');
    // 后到的账号不会把前一个账号的旧数据认成自己的。
    setActiveAccount('20');
    expect(getDefaultWorkspacePath()).toBe(join(accountsDir(), '20', 'workspace'));
  });

  it('claims nothing when there is no legacy data to claim', () => {
    setActiveAccount('19');
    claimLegacyWorkspace('19');

    expect(readLegacyWorkspaceOwner()).toBeNull();
    expect(getDefaultWorkspacePath()).toBe(join(accountsDir(), '19', 'workspace'));
  });

  it('never moves an account that already used the new layout', () => {
    // 登出后的 bridge 会把 <数据根>/workspace 建出来（骨架目录），下次登录
    // 只看「根目录存在」就认领，会让这个账号自己的工作区凭空换到根目录、
    // 原数据反而看不见。已经在新布局下用过 → 根目录里的东西不是它的存量。
    mkdirSync(join(home, 'workspace'), { recursive: true });
    mkdirSync(join(accountsDir(), '19', 'workspace', 'sessions'), { recursive: true });

    setActiveAccount('19');
    claimLegacyWorkspace('19');

    expect(readLegacyWorkspaceOwner()).toBeNull();
    expect(getDefaultWorkspacePath()).toBe(join(accountsDir(), '19', 'workspace'));
  });

  it('mirrors the account into the WSL global-workspace fallback', () => {
    // 账号维度必须镜像到 WSL 侧：否则 B 账号的「定位」会从 A 的 WSL 工作区
    // 里把同名文件找回来（findFileInWsl 的全局回退分支）。
    expect(buildWslSearchScript('report.md', 'desktop:123')).toContain(
      'ws="$HOME/.forge/workspace"'
    );

    setActiveAccount('19');
    const scoped = buildWslSearchScript('report.md', 'desktop:123');
    expect(scoped).toContain('ws="$HOME/.forge/accounts/19/workspace"');
    expect(scoped).not.toContain('ws="$HOME/.forge/workspace"');
  });

  it('lets the claiming account keep the un-scoped WSL workspace', () => {
    mkdirSync(join(home, 'workspace'), { recursive: true });
    setActiveAccount('19');
    claimLegacyWorkspace('19');

    // 认领方在 WSL 侧同样沿用旧位置——存量数据在那边也是一份旧的。
    expect(buildWslSearchScript('report.md', 'desktop:123')).toContain(
      'ws="$HOME/.forge/workspace"'
    );
  });
});

// #1185 的账号维度是一条**跨进程**约定：桌面主进程写标记文件，Python 运行时
// 读它。两边的常量各写各的，改了一边另一边不会报错——只会安静地把运行时指向
// 上一个账号的工作区。这里直接读 Python 源码里的常量来对齐。
describe('account marker contract with miqi/paths.py (#1185)', () => {
  /** 从 cwd 往上找仓库根下的相对文件（打包/独立环境下找不到时返回 null）。 */
  function repoFile(rel: string): string | null {
    let dir = process.cwd();
    for (let i = 0; i < 6; i++) {
      const candidate = join(dir, rel);
      if (existsSync(candidate)) return candidate;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
    return null;
  }

  it('uses the same accounts dir, marker names and default value', () => {
    const path = repoFile(join('miqi', 'paths.py'));
    if (!path) return; // 读不到源码（非仓库内运行）时无契约可校验
    const source = readFileSync(path, 'utf8');
    const literal = (name: string): string => {
      const match = source.match(new RegExp(`^${name} = "([^"]+)"`, 'm'));
      expect(match, `miqi/paths.py 里找不到常量 ${name}`).not.toBeNull();
      return match![1];
    };

    expect(literal('ACCOUNTS_DIR_NAME')).toBe('accounts');
    expect(literal('ACTIVE_ACCOUNT_FILE')).toBe('.active');
    expect(literal('LEGACY_WORKSPACE_OWNER_FILE')).toBe('.legacy-owner');
    // 默认值在 Python 侧由数据根名拼出（两者都跟随 #1175 的更名），约束的是
    // 「拼出来的字面量必须是 ~/.forge/workspace」而不是某个内部标识符。
    expect(source).toContain('DEFAULT_WORKSPACE_VALUE = f"~/{DEFAULT_HOME_NAME}/workspace"');
    expect(`~/${literal('DEFAULT_HOME_NAME')}/workspace`).toBe('~/.forge/workspace');

    // 标记文件必须落在 <数据根>/accounts/ 下，且内容是裸的 sub。
    const home = join(tmpdir(), `miqi-contract-${Date.now()}`);
    mkdirSync(home, { recursive: true });
    process.env['MIQI_HOME'] = home;
    try {
      setActiveAccount('19');
      expect(readFileSync(join(home, 'accounts', '.active'), 'utf8')).toBe('19');
    } finally {
      delete process.env['MIQI_HOME'];
      rmSync(home, { recursive: true, force: true });
    }
  });
});
