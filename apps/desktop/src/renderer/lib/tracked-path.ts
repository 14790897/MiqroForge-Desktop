/**
 * Identity of a tracked-file path (#1096).
 *
 * A session's ledger stores **workspace-relative** keys, while tool messages
 * report **absolute** paths, so the same file reaches the panel in two shapes
 * and was listed twice.  `normalizeTrackedPath` (in ChatConsole) only strips a
 * `/workspace/` prefix, which does nothing for a folder-bound session whose
 * root is somewhere else.
 *
 * A purely lexical rule cannot settle this: `C:/other-root/sub/a.pdf` and
 * `sub/a.pdf` share a tail just as much as `C:/bound/sub/a.pdf` and
 * `sub/a.pdf` do.  Only the known workspace root tells them apart, so it is a
 * required input.
 *
 * This is display-layer deduplication only.  It grants nothing: the
 * containment checks that guard opening/revealing a path still derive every
 * allowed root server-side from the session key (#955) and never take one from
 * the renderer.
 */

/** Whether a slash-normalised path is absolute: POSIX, drive letter, or UNC. */
function isAbsolutePath(p: string): boolean {
  return p.startsWith('/') || /^[a-zA-Z]:\//.test(p) || p.startsWith('//');
}

function slash(p: string): string {
  return p.replace(/\\/g, '/');
}

/** Canonical workspace-relative form of `p`, or null when `p` is absolute but
 *  does not sit under `root` (in which case it is not this workspace's file). */
function asWorkspaceRelative(p: string, root: string): string | null {
  if (p === root) return '';
  if (p.startsWith(root + '/')) return p.slice(root.length + 1);
  return isAbsolutePath(p) ? null : p;
}

/**
 * Whether two tracked paths denote the same file: the ledger's
 * workspace-relative key vs the absolute path a tool reported.
 *
 * Identity is decided by mapping **both** sides into the workspace-relative
 * form and comparing those — not by matching tails.  Two files that merely
 * share a suffix (`sub/a.pdf` vs `other/sub/a.pdf`, or an absolute path under
 * a *different* root) must stay distinct.
 *
 * `workspaceRoot` is required for any non-identical pair: without it there is
 * no way to tell "the absolute form of this key" from "a file elsewhere that
 * happens to end the same way".
 */
export function sameTrackedFile(a: string, b: string, workspaceRoot?: string | null): boolean {
  const na = slash(a);
  const nb = slash(b);
  if (na === nb) return true;
  const root = workspaceRoot ? slash(workspaceRoot).replace(/\/+$/, '') : '';
  if (!root) return false;
  const ra = asWorkspaceRelative(na, root);
  const rb = asWorkspaceRelative(nb, root);
  return ra !== null && rb !== null && ra === rb;
}
