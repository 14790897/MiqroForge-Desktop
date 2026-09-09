import { existsSync, readFileSync } from 'fs';
import { homedir } from 'os';
import { isAbsolute, join } from 'path';

/** Directory holding the local config file (`~/.miqi` by default, overridable via MIQI_HOME). */
export function getConfigDir(): string {
  const miqiHome = process.env['MIQI_HOME']?.trim();
  return miqiHome ? miqiHome : join(homedir(), '.miqi');
}

export function getConfigPath(): string {
  return join(getConfigDir(), 'config.json');
}

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

/** Strip sandbox prefix and resolve against the host workspace.
 *
 *  The bwrap sandbox mounts at /home/miqi/workspace/.  Paths reported
 *  by the agent (e.g. /home/miqi/workspace/report.md) are normalised
 *  to workspace-relative form and then joined with the host workspace
 *  root.  Absolute paths outside the workspace are rejected.
 */
export function resolveWorkspacePath(raw: string): string {
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

  const wsRoot = getWorkspacePath();
  let resolved: string;
  if (isAbsolute(normalised)) {
    resolved = normalised;
  } else {
    resolved = join(wsRoot, normalised);
  }

  // Enforce workspace containment — prevent escape via .. or absolute
  // paths that land outside the workspace root.  Case-fold on Windows
  // (its filesystem is case-insensitive) so a workspace configured with a
  // lowercase drive letter still matches a /mnt/<DRIVE>/ path.
  const rel = resolved.replace(/\\/g, '/');
  const wsNorm = wsRoot.replace(/\\/g, '/');
  const relCmp = process.platform === 'win32' ? rel.toLowerCase() : rel;
  const wsCmp = process.platform === 'win32' ? wsNorm.toLowerCase() : wsNorm;
  if (!(relCmp + '/').startsWith(wsCmp + '/') && relCmp !== wsCmp) {
    throw new Error(`Path outside workspace: ${raw}`);
  }

  return resolved;
}
