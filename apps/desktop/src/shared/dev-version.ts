/** Dev-mode app version formatting (#1055).
 *
 * In `npm run dev` the version string embeds the current commit short hash (and
 * a `.dirty` marker when the working tree has uncommitted changes) so a locally
 * running build can be traced back to the exact code it came from.
 */

export interface GitVersionInfo {
  /** `git rev-parse --short HEAD`, or undefined when git is unavailable. */
  shortHash?: string;
  /** True when `git status --porcelain` is non-empty (uncommitted changes). */
  dirty?: boolean;
}

export function formatDevVersion(base: string, git: GitVersionInfo | null): string {
  let version = `${base}-dev`;
  if (git?.shortHash) {
    version += `+${git.shortHash}`;
    if (git.dirty) {
      version += '.dirty';
    }
  }
  return version;
}
