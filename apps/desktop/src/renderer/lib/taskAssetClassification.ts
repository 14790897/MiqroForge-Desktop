/**
 * Task Assets 结果/过程资产分类 (issue #607, #1104)
 *
 * 结果资产 = 任务最终交付物；过程资产 = 中间产物/脚本/临时文件/引用上下文。
 *
 * RULES (in priority order):
 * 0. Agent explicit declaration (#1104): `result === true` (written by the
 *    `declare_result_files` tool into tracked_files.json) → results. This is
 *    the strongest signal — it beats the filename markers and the extension
 *    whitelist, because the agent knows which file the user must take away
 *    (e.g. a skill's `*_report.md`).
 * 1. delete → process (removed files are never deliverables).
 * 2. filename markers (temp/tmp/debug/draft/scratch/backup/cache/working/old/bak,
 *    stepN, .log) → process, even for deliverable formats.
 * 3. deliverable formats (excel/word/pdf/svg/html — 2026-08-18 扩充 svg/html:
 *    graph_render 渲染产物即交付物, issue #715) → results, regardless of op
 *    (a READ of a deliverable usually means a subprocess wrote it and the
 *    agent inspected it — the write was invisible to tracking).
 * 4. everything else (md/json/csv/txt/py/…) → process.
 *
 * Results sorted lastSeen DESC (newest first); process ASC.
 */

/** Deliverable document whitelist — excel/word/pdf/svg/html (user-specified). */
export const DELIVERABLE_EXT_RE = /\.(?:docx?|xlsx?|pdf|svg|html?)$/i;

/** Conservative filename markers — `step_by_step_guide.md` must NOT match. */
export const PROCESS_FILE_NAME_RE =
  /(?:^|[._-])(?:temp|tmp|debug|draft|scratch|backup|cache|working|old|bak)\d*(?:[._-]|$)|(?:^|[._-])step\d+(?:[._-]|$)|\.log$/i;

export function isProcessFileName(name: string): boolean {
  return PROCESS_FILE_NAME_RE.test(name);
}

export interface ClassifiableTrackedFile {
  name: string;
  op: string;
  lastSeen: number;
  /** #1104: agent 显式声明（declare_result_files）的结果文件标记 */
  result?: boolean;
}

export function classifyTrackedFiles<T extends ClassifiableTrackedFile>(
  files: T[]
): { results: T[]; process: T[] } {
  const results: T[] = [];
  const process: T[] = [];

  for (const file of files) {
    if (file.op === 'delete') {
      process.push(file);
    } else if (file.result === true) {
      // 显式声明优先于一切启发式（#1104）
      results.push(file);
    } else if (isProcessFileName(file.name)) {
      process.push(file);
    } else if (DELIVERABLE_EXT_RE.test(file.name)) {
      results.push(file);
    } else {
      process.push(file);
    }
  }

  results.sort((a, b) => b.lastSeen - a.lastSeen);
  process.sort((a, b) => a.lastSeen - b.lastSeen);
  return { results, process };
}

/** #1104（用户反馈 2026-09-16）：`bvse_sites/` 这类目录一次产出几十个文件，
 *  逐个铺开把面板淹了。目录里文件数达到该阈值就折成一行（默认收起）。 */
export const BULK_DIR_MIN_FILES = 3;

/** Normalise a tracked path for grouping (backslashes → slashes). */
function normalizeForGrouping(p: string): string {
  return p.replace(/\\/g, '/');
}

/** Parent directory of a tracked path ('' when there is none). */
export function dirnameOf(path: string): string {
  const s = normalizeForGrouping(path);
  const idx = s.lastIndexOf('/');
  return idx > 0 ? s.slice(0, idx) : '';
}

/** Display label for a directory: last two path segments, e.g. `out/bvse_sites/`. */
export function dirLabel(dir: string): string {
  return `${dir.split('/').slice(-2).join('/')}/`;
}

/** Split tracked files into loose files and bulk directory groups (#1104).
 *
 *  只折叠「批量子目录」：目录下文件数 ≥ {@link BULK_DIR_MIN_FILES} **且**它的某个
 *  祖先目录也直接持有产物（说明它是交付根下的子目录，如 `run/bvse_sites/`）。
 *  交付根自身的顶层产物（报告、cube、summary.json…）永远保持逐张显示——用户
 *  2026-09-16 反馈里明确要看这些。 */
export function groupTrackedByDir<T extends { path: string }>(
  files: T[]
): { loose: T[]; groups: Array<{ dir: string; files: T[] }> } {
  const byDir = new Map<string, T[]>();
  const loose: T[] = [];
  for (const f of files) {
    const dir = dirnameOf(f.path);
    if (!dir) {
      loose.push(f);
      continue;
    }
    const arr = byDir.get(dir);
    if (arr) arr.push(f);
    else byDir.set(dir, [f]);
  }
  const dirs = [...byDir.keys()];
  const hasTrackedAncestor = (dir: string): boolean =>
    dirs.some((other) => other !== dir && dir.startsWith(`${other}/`));
  const groups: Array<{ dir: string; files: T[] }> = [];
  for (const [dir, arr] of byDir) {
    if (arr.length >= BULK_DIR_MIN_FILES && hasTrackedAncestor(dir)) {
      groups.push({ dir, files: arr });
    } else {
      loose.push(...arr);
    }
  }
  return { loose, groups };
}
