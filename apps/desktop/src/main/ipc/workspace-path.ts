import {
  existsSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'fs';
import { homedir } from 'os';
import { basename, dirname, isAbsolute, join, resolve } from 'path';

/** Directory holding the local config file (`~/.forge` by default, overridable via MIQI_HOME). */
export function getConfigDir(): string {
  const miqiHome = process.env['MIQI_HOME']?.trim();
  return miqiHome ? miqiHome : join(homedir(), '.forge');
}

/**
 * Default name of the data root, used where the *literal* `~/.forge` spelling
 * matters rather than the resolved root — inside WSL, where `$HOME` is the
 * distro's own home and `MIQI_HOME` (a Windows path) does not apply.
 */
const DEFAULT_HOME_DIR_NAME = '.forge';

/** Path to the config JSON file (inside the MIQI_HOME config dir). */
export function getConfigPath(): string {
  return join(getConfigDir(), 'config.json');
}

/** Read and parse the local config JSON, returning `{}` when absent or malformed. */
export function readLocalConfig(): Record<string, unknown> {
  const configPath = getConfigPath();
  try {
    if (!existsSync(configPath)) return {};
    const raw = readFileSync(configPath, 'utf8');
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** Resolve the configured workspace root (default `~/.forge/workspace`, rebased to MIQI_HOME). */
export function getWorkspacePath(): string {
  const config = readLocalConfig();
  const agents = (config['agents'] as Record<string, unknown> | undefined) ?? {};
  const defaults = (agents['defaults'] as Record<string, unknown> | undefined) ?? {};
  const raw = (defaults['workspace'] as string) || DEFAULT_WORKSPACE_VALUE;

  // Default value = "follow the data root": rebased onto MIQI_HOME and scoped
  // to the logged-in account, exactly like `Config.workspace_path` in Python.
  if (raw === DEFAULT_WORKSPACE_VALUE) return getDefaultWorkspacePath();

  // A custom workspace is the user's own choice of directory — never rebased
  // and never account-scoped, so it stays visible to every account on the
  // device (#1185 自定义工作区).
  // Expand ~ to home directory
  if (raw.startsWith('~')) {
    const stripSep = raw.startsWith('~/') || raw.startsWith('~\\');
    return join(homedir(), raw.slice(stripSep ? 2 : 1));
  }

  return raw;
}

// ── account-scoped storage layout (#1185) ──────────────────────────────
// Sessions, task assets, memory, skills and experience all hang off the
// workspace root, so scoping that root per account is what keeps one account's
// conversation history out of the next account's sidebar.  The account
// dimension deliberately does NOT go into MIQI_HOME itself: `config.json`
// (providers, model choice, approvals) stays device-level, along with the
// install directory, Chromium profile and sandbox distro.
//
// `miqi/paths.py` implements the same rule for the runtime.  The two must
// stay in step: the renderer's workspace-containment check below and the
// runtime's writes have to agree on where the workspace is.

const ACCOUNTS_DIR_NAME = 'accounts';
const ACTIVE_ACCOUNT_FILE = '.active';
const LEGACY_WORKSPACE_OWNER_FILE = '.legacy-owner';
const DEFAULT_WORKSPACE_VALUE = `~/${DEFAULT_HOME_DIR_NAME}/workspace`;

/**
 * Whether `sub` is safe to use as a path segment.
 *
 * Account ids come from the platform and end up inside a path.  Anything
 * outside this set is treated as "no account" rather than sanitised —
 * sanitising `../x` into `__x` would silently point a request at a different
 * account's directory.  Leading dots are excluded on top of the separator ban:
 * `..` resolves to `<data root>/workspace` (the shared root), `.` to
 * `<accounts>/workspace`, and `.active` would collide with the marker file.
 */
export function isValidAccountSub(sub: string | null | undefined): sub is string {
  return typeof sub === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(sub);
}

function getAccountsDir(): string {
  return join(getConfigDir(), ACCOUNTS_DIR_NAME);
}

function getActiveAccountFile(): string {
  return join(getAccountsDir(), ACTIVE_ACCOUNT_FILE);
}

function getLegacyWorkspaceOwnerFile(): string {
  return join(getAccountsDir(), LEGACY_WORKSPACE_OWNER_FILE);
}

/** Workspace root of account `sub` (caller must have validated `sub`). */
export function getAccountWorkspace(sub: string): string {
  return join(getAccountsDir(), sub, 'workspace');
}

/**
 * Read an account sub from `path`; null when absent or unusable.
 *
 * Invalid content is reported as "no account" instead of throwing: a
 * truncated or hand-edited marker must never be interpreted as a different
 * account.
 */
function readAccountMarker(path: string): string | null {
  try {
    // 没有账号是常态（登录前、E2E、CLI），先 stat 一下：让常态路径少一次
    // 异常构造，也少一条噪声日志来源。
    if (!existsSync(path)) return null;
    const sub = readFileSync(path, 'utf8').trim();
    return isValidAccountSub(sub) ? sub : null;
  } catch {
    return null;
  }
}

/** The account the Desktop is currently logged in as, if any. */
export function readActiveAccount(): string | null {
  return readAccountMarker(getActiveAccountFile());
}

/** Account that claimed the pre-#1185 `<data root>/workspace`, if any. */
export function readLegacyWorkspaceOwner(): string | null {
  return readAccountMarker(getLegacyWorkspaceOwnerFile());
}

/** Default workspace root, account-scoped when an account is active. */
export function getDefaultWorkspacePath(): string {
  const sub = readActiveAccount();
  // No account (before login, E2E bypass, CLI) — and the account that claimed
  // the legacy directory keeps working in place: moving `<data root>/workspace`
  // would have to race the running bridge for it, and a failed move is exactly
  // the "upgrade ate my history" case #1185 warns about.
  if (sub === null || readLegacyWorkspaceOwner() === sub) {
    return join(getConfigDir(), 'workspace');
  }
  return getAccountWorkspace(sub);
}

/**
 * Record the logged-in account so both processes resolve the same workspace.
 *
 * Written here and read by `miqi/paths.py`; a file rather than the bridge's
 * env or a config field because the bridge is a single long-lived process
 * that is NOT restarted on login/logout, while `agents.defaults.workspace`
 * would classify as a tier-B change and toast the user on every switch.
 *
 * **Throws when the marker cannot be replaced** (#1185 review). Swallowing the
 * error here is what let a failed switch keep the *previous* account's marker
 * on disk while the login still reported success — the long-lived Python
 * resolver reads that marker on every workspace resolution, so the new account
 * would go on working inside the old account's workspace. Callers that are
 * about to persist a new login must treat a throw as "the login did not
 * happen"; a caller that merely re-asserts an already-correct marker (the
 * startup restore path) may log and carry on.
 */
export function setActiveAccount(sub: string): void {
  const file = getActiveAccountFile();
  mkdirSync(dirname(file), { recursive: true });
  // 原子替换：桥可能会在任意时刻读取该文件，就地截断会让它读到空串，
  // 也就是「无账号」——恰好退回共享工作区。rename 在 POSIX 与 Windows
  // 上都会替换已存在的目标文件。
  const tmp = `${file}.${process.pid}.tmp`;
  try {
    writeFileSync(tmp, sub, { encoding: 'utf8' });
    renameSync(tmp, file);
  } catch (err) {
    try {
      rmSync(tmp, { force: true });
    } catch {
      /* 临时文件清理失败无碍：它不含凭据，且下次会被覆写 */
    }
    throw new Error(
      `账号标记写入失败（${file}）：${
        err instanceof Error ? err.message : String(err)
      }；本次登录已中止，以免运行时继续使用上一个账号的工作区`
    );
  }
}

/**
 * Forget the logged-in account (logout).
 *
 * **Throws when the marker cannot be cleared** (#1185 review).  Swallowing it
 * left `.active` pointing at the account that just logged out while
 * `logout()` reported success, so the long-lived bridge kept resolving that
 * account's workspace for anything asked before the next login.
 *
 * A failed delete first retries by *overwriting* the marker with content the
 * readers reject (`'.'` fails {@link isValidAccountSub}), which has the same
 * effect as "no account" — a lock that blocks `rmSync` does not necessarily
 * block a write.
 */
export function clearActiveAccount(): void {
  const file = getActiveAccountFile();
  try {
    rmSync(file, { force: true });
    return;
  } catch {
    /* 删除失败 → 退而求其次：写坏它 */
  }
  try {
    writeFileSync(file, '.', { encoding: 'utf8' });
  } catch (err) {
    throw new Error(
      `退出登录时无法清除账号标记（${file}）：${
        err instanceof Error ? err.message : String(err)
      }；运行时可能继续按上一个账号解析工作区`
    );
  }
}

/**
 * 存量数据归属（#1185 item 5）：升级前 `<数据根>/workspace` 里的会话/记忆
 * 归**首个在设备上登录的账号**，并且就地保留 —— 不搬目录。
 *
 * 搬家的收益只是布局整齐，代价是 `rename` 可能撞上仍在运行、句柄开在该
 * 目录里的 bridge（Windows 上直接失败），而失败与「数据消失」在用户眼里
 * 没有区别。写一份归属标记就能达到同样的隔离效果：被认领的账号继续用旧
 * 目录，其余账号各自拿到空的 `accounts/<sub>/workspace`。
 *
 * `.legacy-owner` 只写一次：后来的账号不会把前一个账号的旧数据认成自己的。
 *
 * 认领还有一个前提：**该账号名下还没有新布局的工作区**。否则会翻车——
 * 账号在 `accounts/<sub>/workspace` 用过一阵之后登出，bridge 会同它当时的
 * 无账号解析把 `<数据根>/workspace` 建出来（骨架目录），下次再登录时只看
 * 「根目录存在」就会认领它，于是这个账号自己的工作区凭空换到根目录、原数据
 * 反而看不见了。已经在新布局下用过，就说明根目录里的东西不是它的存量。
 */
export function claimLegacyWorkspace(sub: string): void {
  if (!isValidAccountSub(sub)) return;
  const ownerFile = getLegacyWorkspaceOwnerFile();
  try {
    if (existsSync(ownerFile)) return; // 已被（任一）账号认领
    if (!existsSync(join(getConfigDir(), 'workspace'))) return; // 无存量数据
    if (existsSync(getAccountWorkspace(sub))) return; // 该账号已在新布局下用过
    mkdirSync(getAccountsDir(), { recursive: true });
    writeFileSync(ownerFile, sub, { encoding: 'utf8' });
  } catch {
    /* 认领失败：该账号退回自己的空 workspace，旧数据保持不可见 */
  }
}

/** Slash-normalised, case-folded form used for prefix containment comparison. */
function normForCompare(p: string): string {
  const rel = p.replace(/\\/g, '/');
  return process.platform === 'win32' ? rel.toLowerCase() : rel;
}

/** Whether `candidate` is `root` itself or lives underneath it. */
function isUnder(candidate: string, root: string): boolean {
  const relCmp = normForCompare(candidate);
  const rootCmp = normForCompare(root);
  return relCmp === rootCmp || relCmp.startsWith(rootCmp + '/');
}

/**
 * Canonical form of `p` even when `p` does not exist yet: the longest existing
 * prefix is realpath'd and the missing tail is appended unchanged.
 *
 * `realpathSync` alone is not enough here.  A folder-bound session's root comes
 * back canonical from the runtime — on macOS `mkdtemp` hands out `/var/…` while
 * the real directory is `/private/var/…` — so a candidate spelled the
 * unresolved way would never compare equal to its own root, and a perfectly
 * legitimate file was refused as "outside workspace" (#1062).  Resolving the
 * existing prefix fixes that without requiring the file to exist: containment
 * is still decided on the real location, so a symlink that leads outside the
 * workspace is still rejected.
 */
function canonicalWithMissingTail(p: string): string {
  let head = p;
  const tail: string[] = [];
  for (;;) {
    try {
      const real = realpathSync.native(head);
      return tail.length ? join(real, ...tail.reverse()) : real;
    } catch {
      const parent = dirname(head);
      if (parent === head) return p; // hit the root without an existing prefix
      tail.push(basename(head));
      head = parent;
    }
  }
}

/**
 * Absolute roots a path is allowed to land in (#1062).
 *
 * `extraRoots` carries a folder-bound session's own workspace.  It is derived
 * server-side from the session key — never sent by the renderer — because a
 * root the renderer could name would make this containment check meaningless
 * (#955).  Entries that are empty or not absolute are dropped rather than
 * trusted.
 */
function allowedRoots(wsRoot: string, extraRoots?: Array<string | null | undefined>): string[] {
  const roots = [wsRoot];
  for (const extra of extraRoots ?? []) {
    if (!extra || !isAbsolute(extra)) continue;
    roots.push(resolve(extra));
  }
  return roots;
}

/**
 * Root a relative path is joined with (#1062).
 *
 * A folder-bound session's ledger stores its paths relative to the session's own
 * workspace, and the Python side resolves them the same way.  Anchoring such a
 * path on the global workspace would look in the wrong place and report a file
 * that exists as "not found" — which is what made 定位 fail before.
 */
function anchorRoot(wsRoot: string, extraRoots?: Array<string | null | undefined>): string {
  for (const extra of extraRoots ?? []) {
    if (extra && isAbsolute(extra)) return resolve(extra);
  }
  return resolve(wsRoot);
}

/** Strip sandbox prefix and resolve against the host workspace.
 *
 *  The bwrap sandbox mounts at /home/miqi/workspace/.  Paths reported
 *  by the agent (e.g. /home/miqi/workspace/report.md) are normalised
 *  to workspace-relative form and then joined with the host workspace
 *  root.  Absolute paths outside the workspace are rejected.
 */
export function resolveWorkspacePath(
  raw: string,
  extraRoots?: Array<string | null | undefined>
): string {
  // Convert WSL /mnt/<drive>/ paths to Windows <drive>:\ paths
  // (e.g. /mnt/c/Users/... -> C:\Users\...).  Fold the result into
  // `normalised` instead of returning early, so the workspace-containment
  // check below still applies — an early return here let the renderer
  // escape the workspace via /mnt (security regression #955).
  const mntMatch = raw.match(/^\/mnt\/([a-zA-Z])\/?(.*)$/);

  const SANDBOX_WS = '/home/miqi/workspace';
  let normalised = raw;
  if (mntMatch) {
    normalised = mntMatch[1].toUpperCase() + ':\\' + mntMatch[2];
  } else if (normalised === SANDBOX_WS) {
    normalised = '.';
  } else if (normalised.startsWith(SANDBOX_WS + '/')) {
    normalised = normalised.slice(SANDBOX_WS.length + 1);
  } else if (normalised.startsWith(SANDBOX_WS + '\\')) {
    normalised = normalised.slice(SANDBOX_WS.length + 1);
  }

  // Resolve both the workspace root and the candidate to normalized absolute
  // paths so ".." segments are collapsed before the prefix comparison.  An
  // absolute path like C:\ws\..\..\Windows\calc.exe would otherwise keep its
  // literal ".." (which looks like it stays inside ws) while the filesystem
  // resolves it outside the workspace (#955).
  const wsRoot = resolve(getWorkspacePath());
  let resolved: string;
  if (isAbsolute(normalised)) {
    resolved = resolve(normalised);
  } else {
    resolved = resolve(anchorRoot(wsRoot, extraRoots), normalised);
  }

  // Enforce root containment — prevent escape via .., absolute paths that land
  // outside every allowed root, and symlinks that lead out of one.  Case-folding
  // happens in isUnder so a workspace configured with a lowercase drive letter
  // still matches a /mnt/<DRIVE>/ path.
  //
  // The comparison is canonical on **both** sides, and canonical only.  A
  // lexical check accepts `link/secret` when `link` points outside the root, and
  // OR-ing it with the canonical verdict let that lexical answer short-circuit
  // past the canonical one (#1103 review).  `canonicalWithMissingTail` is what
  // keeps paths that do not exist yet working, including the folder-bound case
  // where the runtime's root is canonical and the caller's path is not
  // (macOS /var → /private/var).
  const canonicalResolved = canonicalWithMissingTail(resolved);
  const contained = allowedRoots(wsRoot, extraRoots).some((root) =>
    isUnder(canonicalResolved, canonicalWithMissingTail(root))
  );
  if (!contained) {
    throw new Error(`Path outside workspace: ${raw}`);
  }

  return resolved;
}

/**
 * Whether an existing host path resolves (symlinks/junctions followed) to a
 * location inside one of the allowed roots.
 *
 * Only the *candidate* failing to resolve is fail-open (returns true): a path
 * that does not exist yet is already covered by the lexical containment check
 * in resolveWorkspacePath.  A *root* that cannot be canonicalised is not
 * evidence of containment — treating it as "allowed" would let one unreadable
 * root short-circuit the whole `.some()` into accepting a candidate that lives
 * under none of them, and adding roots (#1062) widens that.  Such a root is
 * skipped instead.
 */
export function isWithinCanonicalWorkspace(
  candidate: string,
  wsRoot: string,
  extraRoots?: Array<string | null | undefined>
): boolean {
  let canonicalCandidate: string;
  try {
    canonicalCandidate = realpathSync.native(candidate);
  } catch {
    return true;
  }
  return allowedRoots(wsRoot, extraRoots).some((root) => {
    try {
      return isUnder(canonicalCandidate, realpathSync.native(root));
    } catch {
      return false;
    }
  });
}

/**
 * Derive the on-disk per-session directory key from a session key.
 *
 * This mirrors `miqi.session.session_keys.session_files_dir_key`: a
 * fully-namespaced key such as `miqi-desktop:desktop:1786...` drops the
 * client_id prefix and becomes `desktop_1786...`; two-segment keys such as
 * `desktop:1786...` keep the whole key (`desktop_1786...`).
 *
 * The WSL search script must use this key for `sessions/<key>/files`, not the
 * sandbox key, otherwise it searches a directory no writer ever creates
 * (#1103 review).
 */
export function sessionFilesDirKey(sessionKey: string): string {
  const parts = sessionKey.split(':');
  if (parts.length >= 3) parts.shift();
  return parts.join('_').replace(/[^A-Za-z0-9._-]/g, '_');
}

/**
 * Sanitize a session key so it can safely be embedded in a shell path segment.
 *
 * Only alphanumerics, dots, underscores and hyphens survive; every other
 * character is replaced with an underscore.  This prevents command substitution
 * and shell metacharacters from leaking into WSL search scripts that build
 * paths from the key (#1103 review).
 */
export function sanitizeSessionKeyForPath(sessionKey: string): string {
  return sessionKey.replace(/[^A-Za-z0-9._-]/g, '_');
}

/** Escape a string for safe embedding in a single-quoted bash argument. */
export function shellEscape(s: string): string {
  return s.replace(/'/g, "'\\''");
}

/**
 * `$HOME`-relative path of the WSL-native global workspace.
 *
 * Mirrors `getDefaultWorkspacePath`, but built from the *default* directory
 * name rather than `getConfigDir()`: this path is resolved inside the distro,
 * where `$HOME` is the WSL user's home and `MIQI_HOME` (a Windows path) never
 * applies.  The account sub is interpolated into a bash line, so it goes
 * through `readActiveAccount`'s validation — it can only be `[A-Za-z0-9._-]`.
 */
function wslGlobalWorkspaceSubpath(): string {
  const sub = readActiveAccount();
  // 无账号 / 认领了旧目录的账号 → 沿用 WSL 里那个未分账号的旧位置。
  if (sub === null || readLegacyWorkspaceOwner() === sub) {
    return `${DEFAULT_HOME_DIR_NAME}/workspace`;
  }
  return `${DEFAULT_HOME_DIR_NAME}/${ACCOUNTS_DIR_NAME}/${sub}/workspace`;
}

/**
 * Build the bash script used to locate a workspace-relative file inside WSL.
 *
 * The script searches the session sandbox and the session-private files
 * directory — plus, unless `allowGlobalWorkspace` is false, the global WSL
 * workspace and the global session files directory.  A folder-bound session
 * passes false: its relative paths resolve against the bound folder, so a miss
 * there must not be answered from the global root — the hit would be copied
 * into the bound folder and opened, i.e. another root's file (#1103 review).
 *
 * The global workspace follows the logged-in account (#1185), same rule as the
 * host side: without it, account B's "定位" would hand back a file it found in
 * the WSL workspace of account A.
 *
 * When a file is found the script canonicalizes both the candidate and its
 * authorization root and rejects the result if the canonical candidate lives
 * outside the root, closing a symlink-escape path (#1103 review).
 *
 * The sandbox path uses the full sanitized session key, while the
 * session-private files directory uses `sessionFilesDirKey` so it matches the
 * canonical on-disk layout used by the Python side.
 */
export function buildWslSearchScript(
  relPath: string,
  sessionKey: string,
  opts: { allowGlobalWorkspace?: boolean } = {}
): string {
  const escapedRelPath = shellEscape(relPath);
  const sandboxKey = sanitizeSessionKeyForPath(sessionKey);
  const sessionFilesKey = sessionFilesDirKey(sessionKey);
  const allowGlobal = opts.allowGlobalWorkspace ?? true;
  return (
    `RP=$'${escapedRelPath}'\n` +
    `W="/tmp/miqi-sandboxes/${sandboxKey}/home/miqi/workspace"\n` +
    `S="$W/sessions/${sessionFilesKey}/files"\n` +
    (allowGlobal
      ? `ws="$HOME/${wslGlobalWorkspaceSubpath()}"\n` +
        `s="$ws/sessions/${sessionFilesKey}/files"\n`
      : '') +
    `found=""\n` +
    `root=""\n` +
    `if [ -f "$W/$RP" ]; then found="$W/$RP"; root="$W"; fi\n` +
    `if [ -z "$found" ] && [ -f "$S/$RP" ]; then found="$S/$RP"; root="$W"; fi\n` +
    (allowGlobal
      ? `if [ -z "$found" ] && [ -f "$ws/$RP" ]; then found="$ws/$RP"; root="$ws"; fi\n` +
        `if [ -z "$found" ] && [ -f "$s/$RP" ]; then found="$s/$RP"; root="$ws"; fi\n`
      : '') +
    `if [ -z "$found" ]; then exit 1; fi\n` +
    `canon=$(readlink -f "$found") || exit 1\n` +
    `root_canon=$(readlink -f "$root") || exit 1\n` +
    `case "$canon" in\n` +
    `  "$root_canon"|"$root_canon"/*) echo "$found"; exit 0 ;;\n` +
    `  *) exit 1 ;;\n` +
    `esac\n`
  );
}
