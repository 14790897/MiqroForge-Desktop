import { existsSync, readFileSync, realpathSync } from 'fs';
import { homedir } from 'os';
import { isAbsolute, join, resolve } from 'path';

/** Directory holding the local config file (`~/.miqi` by default, overridable via MIQI_HOME). */
export function getConfigDir(): string {
  const miqiHome = process.env['MIQI_HOME']?.trim();
  return miqiHome ? miqiHome : join(homedir(), '.miqi');
}

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

/** Resolve the configured workspace root (default `~/.miqi/workspace`, rebased to MIQI_HOME). */
export function getWorkspacePath(): string {
  const config = readLocalConfig();
  const agents = (config['agents'] as Record<string, unknown> | undefined) ?? {};
  const defaults = (agents['defaults'] as Record<string, unknown> | undefined) ?? {};
  const raw = (defaults['workspace'] as string) || '~/.miqi/workspace';

  // When using the default path but MIQI_HOME is set, rebase like the Python side does
  if (raw === '~/.miqi/workspace') {
    const miqiHome = process.env['MIQI_HOME']?.trim();
    if (miqiHome) return join(miqiHome, 'workspace');
  }

  // Expand ~ to home directory
  if (raw.startsWith('~')) {
    const stripSep = raw.startsWith('~/') || raw.startsWith('~\\');
    return join(homedir(), raw.slice(stripSep ? 2 : 1));
  }

  return raw;
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

  // Enforce root containment — prevent escape via .. or absolute paths that
  // land outside every allowed root.  Case-folding happens in isUnder so a
  // workspace configured with a lowercase drive letter still matches a
  // /mnt/<DRIVE>/ path.
  if (!allowedRoots(wsRoot, extraRoots).some((root) => isUnder(resolved, root))) {
    throw new Error(`Path outside workspace: ${raw}`);
  }

  return resolved;
}

/**
 * Whether an existing host path resolves (symlinks/junctions followed) to a
 * location inside one of the allowed roots.  Returns true when the path cannot
 * be resolved (e.g. it does not exist) — those are already covered by the
 * lexical containment check in resolveWorkspacePath.
 */
export function isWithinCanonicalWorkspace(
  candidate: string,
  wsRoot: string,
  extraRoots?: Array<string | null | undefined>
): boolean {
  return allowedRoots(wsRoot, extraRoots).some((root) => {
    try {
      return isUnder(realpathSync.native(candidate), realpathSync.native(root));
    } catch {
      return true;
    }
  });
}
