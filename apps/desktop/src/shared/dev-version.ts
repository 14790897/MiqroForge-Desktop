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
  // package.json 的开发版本号本身可能已带 -dev(如 `0.32.0-dev`),再拼一次就成了
  // `0.32.0-dev-dev+hash`——白长 4 个字符,而侧栏底部本来就放不下。先归一化,
  // 保证只出现一个 -dev;纯 semver 入参(如 `0.25.0`)行为不变。
  const cleanBase = base.replace(/-dev$/, '');
  let version = `${cleanBase}-dev`;
  if (git?.shortHash) {
    version += `+${git.shortHash}`;
    if (git.dirty) {
      version += '.dirty';
    }
  }
  return version;
}
