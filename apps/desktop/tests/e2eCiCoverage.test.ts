/**
 * e2e CI 覆盖守卫（#1196）
 *
 * `tests/e2e/*.spec.ts` 里有三类守卫会让用例**在任何 CI runner 上都不执行**：
 *   - Windows 语义：文件级 `process.platform !== 'win32'`，而全量套件只有 ubuntu / macOS runner；
 *   - 只在本地设置的开关：`MIQI_RUN_*` / `QRAFT_LIVE` / `SLURM_MCP_KEY` / `MOF_PRICE_PROJECT`；
 *   - 反向门：`const X_ON_CI = !!process.env.CI`（CI 上永远跳过）。
 * 它们从不运行、也从不报错，于是「e2e 覆盖率」看起来比实际高（#1196 的现象）。
 *
 * 本测试把这份事实钉在 `tests/e2e/CI-COVERAGE.md`，并在两个方向设防：
 *   1. 检测到致命守卫的 spec 必须登记 —— 否则红（新加一个 MIQI_RUN_* 门不再静默）；
 *   2. 登记为「守卫」的行必须仍被检测到 —— 守卫被删/改名后清单不许留尸。
 *
 * 检测口径（`analyzeSpec`）是**启发式**的，刻意不试图证明「某个 runner 上真的会跑」——
 * 那要模拟每个 job 的平台 × env × 步骤级条件，静态判不了。它只做一件事：从
 * `test.skip` / `test.fixme` / `test.describe.skip` / `describeFn` 的实参出发，
 * 沿模块级 const / function 展开两层，收集可达的 `process.env.X` 与 `process.platform`，
 * 再对照「workflow 里有没有设置过这个变量」。
 *
 * 因此它是**漂移报警**，不是覆盖率证明；人工确认过的例外在清单里标「人工」。
 * 已知盲区（#1196 评审确认）：变量名只要在任一 workflow 里出现过就算「CI 已设置」，
 * 不区分它出现在哪个 job——`billing-hosted-live.spec.ts` 的 `DEEPSEEK_API_KEY`
 * 就落在 python-tests.yml 的步骤里，而真正跑 e2e 的 job 从不注入它。
 */
import { describe, expect, it } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, '..', '..', '..');
const E2E_DIR = join(REPO_ROOT, 'apps', 'desktop', 'tests', 'e2e');
const WORKFLOW_DIR = join(REPO_ROOT, '.github', 'workflows');
const MANIFEST = join(E2E_DIR, 'CI-COVERAGE.md');

const MAX_CONST_INIT = 400;
const MAX_FN_BODY = 1200;
const MAX_SKIP_CONTEXT = 400;
const EXPAND_ROUNDS = 3;

/**
 * 从 start 起截到「行尾的 `;`」——即一条语句的边界，带兜底上限。
 * 不按字符数硬切：越界会把下一条语句（比如读 `MIQI_PYTHON_PATH` 的可选配置）
 * 当成门的一部分，制造误报。
 */
function statementAt(source: string, start: number, cap: number): string {
  const semi = source.indexOf(';\n', start);
  const end = semi >= 0 && semi - start < cap ? semi + 1 : Math.min(source.length, start + cap);
  return source.slice(start, end);
}

/** 同上，但截到模块级函数的收尾 `}`（行首）——函数体不会把下一个函数吞进来。 */
function functionAt(source: string, start: number, cap: number): string {
  const close = source.indexOf('\n}', start);
  const end = close >= 0 && close - start < cap ? close + 2 : Math.min(source.length, start + cap);
  return source.slice(start, end);
}

/** 同上，但截到一个调用的 `);` 处——skip 实参不会把后续语句吞进来。 */
function callAt(source: string, start: number, cap: number): string {
  const close = source.indexOf(');', start);
  const end = close >= 0 && close - start < cap ? close + 2 : Math.min(source.length, start + cap);
  return source.slice(start, end);
}

interface Workflow {
  file: string;
  text: string;
}

function readWorkflows(): Workflow[] {
  return readdirSync(WORKFLOW_DIR)
    .filter((f) => f.endsWith('.yml') || f.endsWith('.yaml'))
    .map((f) => ({ file: f, text: readFileSync(join(WORKFLOW_DIR, f), 'utf-8') }));
}

/**
 * workflow 里出现过的环境变量名（job / step 级 `env:` 的键）。
 *
 * 有意扫描整份文件（含 `run: |` 块）而不做 YAML 解析：块内出现 `FOO:` 形态的文本
 * 会被误当「已设置」，方向上是**少报警**——漏报一个新门的概率极低（门变量名都是一
 * 次性、专为某个 spec 起的），换来的是不用为此引入 yaml 依赖。
 */
function envNamesSetInWorkflows(workflows: Workflow[]): Set<string> {
  const names = new Set<string>();
  for (const wf of workflows) {
    for (const m of wf.text.matchAll(/^\s{2,}([A-Z][A-Z0-9_]{2,})\s*:/gm)) names.add(m[1]);
  }
  return names;
}

/** 在某个 windows runner 的 job 里被按名字点名的 spec（这些 spec 真有 Windows 执行者）。 */
function specsNamedInWindowsJobs(workflows: Workflow[]): Set<string> {
  const named = new Set<string>();
  for (const wf of workflows) {
    // 以 2 空格缩进的 job id 切块，只保留 runs-on: windows* 的块
    for (const block of wf.text.split(/^(?=  [A-Za-z0-9_-]+:\s*$)/m)) {
      if (!/^\s+runs-on:\s*windows/m.test(block)) continue;
      for (const m of block.matchAll(/([a-z0-9][a-z0-9-]*\.spec\.ts)/g)) named.add(m[1]);
    }
  }
  return named;
}

interface GateAnalysis {
  /** skip 上下文里可达、且 CI 从未设置的 process.env 变量（`CI` 除外）。 */
  fatalEnvs: string[];
  /** skip 上下文里可达的全部 env 变量（含 CI 上会设置的，便于人工核对）。 */
  allEnvs: string[];
  /** skip 上下文要求 win32，且该文件没有被任何 windows job 点名。 */
  win32Only: boolean;
  /** `const XXX_ON_CI = !!process.env.CI` 这种「只在 CI 上跳过」的独占门。 */
  bareOnCiGate: boolean;
  /** 被 `test.skip('标题', fn)` / `test.fixme('标题', fn)` 永久禁用的用例标题。 */
  disabledTests: string[];
}

/** 沿 const / function 展开 skip 实参，收集其中可达的 env / platform 引用。 */
function analyzeSpec(
  source: string,
  envSetInCi: Set<string>,
  namedInWindowsJob: boolean
): GateAnalysis {
  const consts = new Map<string, string>();
  for (const m of source.matchAll(
    /^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]*)?=\s*/gm
  )) {
    consts.set(m[1], statementAt(source, m.index! + m[0].length, MAX_CONST_INIT));
  }
  const fns = new Map<string, string>();
  for (const m of source.matchAll(
    /^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*[<(]/gm
  )) {
    fns.set(m[1], functionAt(source, m.index!, MAX_FN_BODY));
  }

  let expanded = '';
  const seen = new Set<string>();
  for (const m of source.matchAll(
    /\b(?:test\.skip|test\.fixme|test\.describe\.skip|describeFn)\b/g
  )) {
    expanded += '\n' + callAt(source, m.index!, MAX_SKIP_CONTEXT);
  }
  for (let round = 0; round < EXPAND_ROUNDS; round++) {
    let added = false;
    for (const id of new Set(expanded.match(/[A-Za-z_$][\w$]*/g) ?? [])) {
      if (seen.has(id)) continue;
      const src = consts.get(id) ?? fns.get(id);
      if (!src) continue;
      seen.add(id);
      expanded += `\n/* ${id} */ ${src}`;
      added = true;
    }
    if (!added) break;
  }

  const allEnvs = new Set<string>();
  for (const m of expanded.matchAll(/process\.env\.([A-Z][A-Z0-9_]*)/g)) allEnvs.add(m[1]);
  for (const m of expanded.matchAll(/process\.env\[['"]([A-Z][A-Z0-9_]*)['"]\]/g))
    allEnvs.add(m[1]);

  const bareOnCiGate = [...consts].some(([name, init]) => {
    if (!/ON_CI|SKIP_/.test(name)) return false;
    const head = init.replace(/\s+/g, ' ').trim().slice(0, 80);
    return /^!!process\.env(\.CI|\[['"]CI['"]\])\s*;?$/.test(head);
  });

  const disabledTests: string[] = [];
  for (const m of source.matchAll(/\btest\.(?:skip|fixme)\(\s*(['"`])([^'"`\n]{1,120})\1\s*,/g)) {
    disabledTests.push(m[2]);
  }

  return {
    fatalEnvs: [...allEnvs].filter((v) => v !== 'CI' && !envSetInCi.has(v)).sort(),
    allEnvs: [...allEnvs].sort(),
    win32Only: /process\.platform\s*!==\s*['"]win32['"]/.test(expanded) && !namedInWindowsJob,
    bareOnCiGate,
    disabledTests,
  };
}

/** 一条致命守卫的说明；返回空数组表示这个 spec 在 CI 上有执行者。 */
function fatalReasons(a: GateAnalysis): string[] {
  const reasons: string[] = [];
  if (a.fatalEnvs.length) reasons.push(`CI 未设置的门：${a.fatalEnvs.join(', ')}`);
  if (a.win32Only) reasons.push("`process.platform !== 'win32'`，且未被任何 windows job 点名");
  if (a.bareOnCiGate) reasons.push('`= !!process.env.CI` 独占门（CI 上永远跳过）');
  if (a.disabledTests.length) reasons.push(`被永久禁用的用例：${a.disabledTests.join(' / ')}`);
  return reasons;
}

describe('e2e CI 覆盖守卫（#1196）', () => {
  const workflows = readWorkflows();
  const envSetInCi = envNamesSetInWorkflows(workflows);
  const namedInWindowsJobs = specsNamedInWindowsJobs(workflows);
  const specFiles = readdirSync(E2E_DIR)
    .filter((f) => f.endsWith('.spec.ts'))
    .sort();

  const manifestText = readFileSync(MANIFEST, 'utf-8');
  // 清单行：`| `foo.spec.ts`（部分） | 守卫 | ... |`，第二列是判定（守卫 / 人工）
  const manifestRows = [
    ...manifestText.matchAll(/^\|\s*`([^`]+\.spec\.ts)`[^|]*\|\s*([^|]*?)\s*\|/gm),
  ].map((m) => ({ file: m[1], verdict: m[2] }));

  it('检测到致命守卫的 spec 都已登记进 CI-COVERAGE.md', () => {
    const missing: string[] = [];
    for (const file of specFiles) {
      const analysis = analyzeSpec(
        readFileSync(join(E2E_DIR, file), 'utf-8'),
        envSetInCi,
        namedInWindowsJobs.has(file)
      );
      const reasons = fatalReasons(analysis);
      if (reasons.length && !manifestRows.some((r) => r.file === file)) {
        missing.push(`  ${file} — ${reasons.join('；')}`);
      }
    }
    expect(
      missing,
      `以下 spec 在任何 CI runner 上都不会执行，但未登记进 tests/e2e/CI-COVERAGE.md：\n` +
        `${missing.join('\n')}\n` +
        `—— 若确实要留在 CI 外，请在清单里加一行（门 / 代价 / 原因）；否则把它接进某个 CI 步骤。`
    ).toEqual([]);
  });

  it('CI-COVERAGE.md 的行都还成立（清单不留尸）', () => {
    const stale: string[] = [];
    for (const row of manifestRows) {
      const path = join(E2E_DIR, row.file);
      if (!existsSync(path)) {
        stale.push(`  ${row.file} — 文件已不存在`);
        continue;
      }
      const source = readFileSync(path, 'utf-8');
      if (!/\btest\.(skip|fixme)\(|describeFn\b/.test(source)) {
        stale.push(`  ${row.file} — 已不含任何跳过守卫（守卫被移除或改写了？）`);
        continue;
      }
      if (row.verdict === '守卫') {
        const analysis = analyzeSpec(source, envSetInCi, namedInWindowsJobs.has(row.file));
        if (!fatalReasons(analysis).length) {
          stale.push(
            `  ${row.file} — 标为「守卫」但已检测不到致命守卫（守卫形态变了就改标「人工」，真被接进 CI 了就删掉这行）`
          );
        }
      }
    }
    expect(stale, `CI-COVERAGE.md 有以下过期行：\n${stale.join('\n')}`).toEqual([]);
  });

  it('清单里没有重复行', () => {
    const dupes = manifestRows.map((r) => r.file).filter((f, i, all) => all.indexOf(f) !== i);
    expect(dupes).toEqual([]);
  });
});
