/**
 * Identity of a tracked-file path (#1096).
 *
 * A session's ledger stores **workspace-relative** keys, while tool messages
 * report **absolute** paths.  The same file therefore reaches the panel in two
 * shapes and used to be listed twice.  `normalizeTrackedPath` (in ChatConsole)
 * only strips a `/workspace/` prefix, which does nothing for a folder-bound
 * session whose root is somewhere else — so the two shapes never compared equal.
 */

/** Whether a slash-normalised path is absolute: POSIX, drive letter, or UNC. */
function isAbsolutePath(p: string): boolean {
  return p.startsWith('/') || /^[a-zA-Z]:\//.test(p) || p.startsWith('//');
}

/**
 * Whether two tracked paths denote the same file written two ways: a
 * workspace-relative key vs the absolute path a tool reported.
 *
 * The suffix collapse is deliberately limited to **that** shape — exactly one
 * side absolute.  Applied to two paths of the same kind it merges files that
 * merely share a tail: `sub/a/b.pdf` and `a/b.pdf` are different files, as are
 * two absolute paths under different roots.  That would be the opposite failure
 * of the bare-filename rule this replaced.
 */
export function sameTrackedFile(a: string, b: string): boolean {
  const na = a.replace(/\\/g, '/');
  const nb = b.replace(/\\/g, '/');
  if (na === nb) return true;
  if (isAbsolutePath(na) === isAbsolutePath(nb)) return false;
  return na.endsWith('/' + nb) || nb.endsWith('/' + na);
}
