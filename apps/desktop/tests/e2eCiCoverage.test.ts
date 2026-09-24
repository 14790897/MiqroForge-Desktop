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
 * 判定分三层：
 *   - `parseWorkflowJobs` 把 workflow 解析成 job / step 粒度：每个 job 的 `runs-on` 与
 *     各级 `env:`，以及每个 step 里 playwright 调用了哪些 spec（或是否全量跑 electron 项目）。
 *   - `analyzeSpec` 从 skip 实参出发沿模块级 const / function 展开，得到「这个 spec 需要什么」
 *     （可达的 `process.env.X` 与 `process.platform`）。
 *   - `fatalReasons` **逐个执行者**核对：必须存在某一个 job/step 同时满足全部门与平台条件，
 *     才算在 CI 上有覆盖。按并集判定会漏掉「门在 A step、win32 在 B step」这类跨 step
 *     泄漏（#1209 评审 P2 / CodeRabbit 复审）。
 *
 * 它仍然是**漂移报警**，不是覆盖率证明：判据是启发式（字符串解析，不是 TS AST），
 * 残余盲区是个别 step 内部的运行时分支；人工确认过的例外在清单里标「人工」。
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
/** 展开迭代到不动点（每轮把所有新出现的 const / function 都并进来），上限只是防病态输入。 */
const MAX_EXPAND_ROUNDS = 8;

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

/** 同上，但截到模块级函数的收尾 `}`——函数体不会把下一个函数吞进来。 */
function functionAt(source: string, start: number, cap: number): string {
  const brace = source.indexOf('{', start);
  if (brace >= 0) {
    // 单行函数体（`{ ... }` 在同一个换行内闭合）就地截断，避免滑到上限把后续语句吞进来。
    // 必须配平花括号：体里可能先出现嵌套的 `{...}`（对象字面量等），停在第一个 `}` 会截断函数体。
    const newline = source.indexOf('\n', brace);
    const limit = newline < 0 ? Math.min(source.length, brace + cap) : newline;
    let depth = 0;
    for (let i = brace; i < limit; i++) {
      if (source[i] === '{') depth++;
      else if (source[i] === '}') {
        depth--;
        if (depth === 0) return source.slice(start, i + 1);
      }
    }
  }
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

// ─── workflow 解析（job / step 粒度）───────────────────────────────────
//
// 不引入 yaml 依赖（apps/desktop 的 devDependencies 里没有，js-yaml 只是传递依赖，
// 不能当成契约），而是按 GitHub Actions 的固定结构做缩进解析：job id 是 2 空格缩进的
// `name:`，step 是 job 内以 `- <step 键>:` 开头的列表项，`env:` 块取其下更深缩进的 `KEY:`。

interface Workflow {
  file: string;
  text: string;
}

interface PlaywrightStep {
  /** 该 step 按名字点名的 spec 文件。 */
  names: string[];
  /** 该 step 不点名、全量跑 electron 项目（--project=electron，且没有 --grep 过滤）。 */
  fullSuite: boolean;
  /** step 级 env 变量名。 */
  env: Set<string>;
}

interface JobScope {
  id: string;
  runsOn: string;
  /** `strategy.matrix.os` 列出的取值（形如 `os: [windows-latest, ubuntu-latest]`）。 */
  matrixOs: string[];
  /** job 级 env 变量名。 */
  env: Set<string>;
  playwrightSteps: PlaywrightStep[];
}

/**
 * 这个 job 会不会在 Windows 上跑？
 *   - `runs-on: ${{ matrix.os }}` → 由 matrix 取值决定；
 *   - 固定 runs-on（如 `ubuntu-latest`）→ 只看字面值：matrix 里恰好列了 windows 取值
 *     也不能算（真正的 runner 由固定 runs-on 决定）。
 * 拿不到 matrix 取值时按「不是 windows」处理——方向是多报警，比静默漏报安全。
 */
function isWindowsJob(job: JobScope): boolean {
  if (/\$\{\{\s*matrix\.os\s*\}\}/.test(job.runsOn)) {
    return job.matrixOs.some((v) => /windows/i.test(v));
  }
  return /windows/i.test(job.runsOn);
}

const STEP_KEY =
  /^\s*-\s+(?:name|uses|run|id|if|with|env|shell|working-directory|continue-on-error|timeout-minutes|strategy|secrets)\s*:/;

function readWorkflows(): Workflow[] {
  return readdirSync(WORKFLOW_DIR)
    .filter((f) => f.endsWith('.yml') || f.endsWith('.yaml'))
    .map((f) => ({ file: f, text: readFileSync(join(WORKFLOW_DIR, f), 'utf-8') }));
}

function parseWorkflowJobs(wf: Workflow): JobScope[] {
  const lines = wf.text.split('\n');
  const jobs: JobScope[] = [];
  let job: JobScope | null = null;
  let step: PlaywrightStep | null = null;
  let envTarget: Set<string> | null = null;
  let envIndent = -1;

  const closeEnv = () => {
    envTarget = null;
    envIndent = -1;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.trim() === '' || line.trimStart().startsWith('#')) continue;
    const indent = line.match(/^\s*/)?.[0].length ?? 0;

    // 2 空格缩进的键：job id（也含 `on:` 段里的 push/pull_request 等，但它们没有
    // runs-on / playwright，解析成空 job 无副作用）
    const mJob = line.match(/^  ([A-Za-z0-9_-]+):\s*$/);
    if (mJob && indent === 2) {
      if (job) jobs.push(job);
      job = { id: mJob[1], runsOn: '', matrixOs: [], env: new Set(), playwrightSteps: [] };
      step = null;
      closeEnv();
      continue;
    }
    if (!job) continue;
    if (indent === 0) {
      jobs.push(job);
      job = null;
      step = null;
      closeEnv();
      continue;
    }

    if (STEP_KEY.test(line)) {
      step = { names: [], fullSuite: false, env: new Set() };
      job.playwrightSteps.push(step);
      closeEnv();
    }

    const mRunsOn = line.match(/^\s+runs-on:\s*(.+?)\s*$/);
    if (mRunsOn) {
      job.runsOn = mRunsOn[1];
      continue;
    }

    // matrix 取值列表（`strategy.matrix.os: [windows-latest, ubuntu-latest]`）——
    // `runs-on: ${{ matrix.os }}` 的 job 靠它判断是否会落在 Windows 上
    const mMatrixOs = line.match(/^\s+os:\s*\[(.+)\]\s*$/);
    if (mMatrixOs) {
      job.matrixOs = mMatrixOs[1]
        .split(',')
        .map((v) => v.trim().replace(/^['"]|['"]$/g, ''))
        .filter(Boolean);
    }

    if (/^\s*env:\s*$/.test(line)) {
      envTarget = step ? step.env : job.env;
      envIndent = indent;
      continue;
    }
    if (envTarget) {
      if (indent > envIndent) {
        const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:/);
        if (m) {
          envTarget.add(m[1]);
          continue;
        }
      } else {
        closeEnv();
      }
    }

    if (/npx\s+playwright\s+test/.test(line)) {
      // 拼接续行（行尾 `\`），命令行可能跨多行
      let joined = line;
      let j = i;
      while (/\\\s*$/.test(joined) && j + 1 < lines.length) {
        j++;
        joined += ' ' + lines[j].trim();
      }
      const names = [...joined.matchAll(/([a-z0-9][a-z0-9-]*\.spec\.ts)/g)].map((m) => m[1]);
      const target =
        step ??
        (() => {
          const s: PlaywrightStep = { names: [], fullSuite: false, env: new Set() };
          job!.playwrightSteps.push(s);
          return s;
        })();
      target.names.push(...names);
      if (names.length === 0 && /--project=electron/.test(joined) && !/--grep\b/.test(joined)) {
        target.fullSuite = true;
      }
    }
  }
  if (job) jobs.push(job);
  return jobs;
}

// ─── spec 侧的守卫分析 ────────────────────────────────────────────────

interface GateAnalysis {
  /** skip 上下文里可达的 process.env 变量（`CI` 除外）——需要某个执行者全部提供。 */
  requiredEnvs: string[];
  /** skip 上下文要求 win32。 */
  needsWindows: boolean;
  /** `const XXX_ON_CI = !!process.env.CI` 这种「只在 CI 上跳过」的独占门。 */
  bareOnCiGate: boolean;
  /** 被 `test.skip('标题', fn)` / `test.fixme('标题', fn)` 永久禁用的用例标题。 */
  disabledTests: string[];
}

/** 一个「执行者」作用域：某个会收集到这个 spec 的 job/step，连同它能提供的 env 与平台。 */
interface StepScope {
  label: string;
  envs: Set<string>;
  windows: boolean;
}

/** 沿 const / function 展开 skip 实参，收集这个 spec 对执行环境的要求。 */
function analyzeSpec(source: string): GateAnalysis {
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
  for (let round = 0; round < MAX_EXPAND_ROUNDS; round++) {
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
    requiredEnvs: [...allEnvs].filter((v) => v !== 'CI').sort(),
    needsWindows: /process\.platform\s*!==\s*['"]win32['"]/.test(expanded),
    bareOnCiGate,
    disabledTests,
  };
}

/**
 * 一条致命守卫的说明；返回空数组表示这个 spec 在 CI 上有执行者。
 * 逐 scope 判定：只要**某一个** job/step 同时满足全部门与平台条件，就算覆盖；
 * 否则分别指出「哪都没设」「没有 windows 执行者」「条件分散在不同执行者」。
 */
function fatalReasons(a: GateAnalysis, scopes: StepScope[]): string[] {
  const reasons: string[] = [];
  const satisfied = scopes.some(
    (s) => a.requiredEnvs.every((v) => s.envs.has(v)) && (!a.needsWindows || s.windows)
  );
  if (!satisfied) {
    if (scopes.length === 0) {
      reasons.push('没有任何 CI job/step 会收集它');
    } else {
      const missingEverywhere = a.requiredEnvs.filter((v) => !scopes.some((s) => s.envs.has(v)));
      if (missingEverywhere.length) reasons.push(`CI 未设置的门：${missingEverywhere.join(', ')}`);
      const windowsOk = scopes.some((s) => s.windows);
      if (a.needsWindows && !windowsOk) {
        reasons.push("`process.platform !== 'win32'`，且没有 windows job 会收集它");
      }
      if (!missingEverywhere.length && (!a.needsWindows || windowsOk)) {
        // 变量都能找到、平台也满足，但凑不到同一个执行者身上——跨 step 泄漏
        const need = [...a.requiredEnvs, ...(a.needsWindows ? ['win32'] : [])];
        const detail = scopes
          .map((s) => {
            const missing = [
              ...a.requiredEnvs.filter((v) => !s.envs.has(v)),
              ...(a.needsWindows && !s.windows ? ['win32'] : []),
            ];
            return `${s.label} 缺 ${missing.join('/')}`;
          })
          .join('；');
        reasons.push(`没有任何单个 job/step 同时满足 ${need.join(' + ')}（${detail}）`);
      }
    }
  }
  if (a.bareOnCiGate) reasons.push('`= !!process.env.CI` 独占门（CI 上永远跳过）');
  if (a.disabledTests.length) reasons.push(`被永久禁用的用例：${a.disabledTests.join(' / ')}`);
  return reasons;
}

describe('e2e CI 覆盖守卫（#1196）', () => {
  const jobs = readWorkflows().flatMap(parseWorkflowJobs);
  const specFiles = readdirSync(E2E_DIR)
    .filter((f) => f.endsWith('.spec.ts'))
    .sort();

  /**
   * 谁会收集到这个 spec？点名它的 step，或全量跑 electron 项目的 step。
   * 每个这样的 job/step 都是一个**独立的执行者作用域**——判定时不能把它们的 env
   * 并起来看，否则会漏掉「门在 A step、win32 在 B step」这类跨 step 泄漏。
   */
  function scopesFor(specFile: string): StepScope[] {
    const scopes: StepScope[] = [];
    for (const job of jobs) {
      for (const step of job.playwrightSteps) {
        const collects = step.fullSuite || step.names.includes(specFile);
        if (!collects) continue;
        scopes.push({
          label: `${job.id}${step.fullSuite ? '（全量）' : '（点名）'}`,
          envs: new Set<string>([...job.env, ...step.env]),
          windows: isWindowsJob(job),
        });
      }
    }
    return scopes;
  }

  const manifestText = readFileSync(MANIFEST, 'utf-8');
  // 清单行：`| `foo.spec.ts`（部分） | 守卫 | ... |`，第二列是判定（守卫 / 人工）
  const manifestRows = [
    ...manifestText.matchAll(/^\|\s*`([^`]+\.spec\.ts)`[^|]*\|\s*([^|]*?)\s*\|/gm),
  ].map((m) => ({ file: m[1], verdict: m[2] }));

  it('检测到致命守卫的 spec 都已登记进 CI-COVERAGE.md', () => {
    const missing: string[] = [];
    for (const file of specFiles) {
      const analysis = analyzeSpec(readFileSync(join(E2E_DIR, file), 'utf-8'));
      const reasons = fatalReasons(analysis, scopesFor(file));
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
        const analysis = analyzeSpec(source);
        if (!fatalReasons(analysis, scopesFor(row.file)).length) {
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
    expect(dupes, `CI-COVERAGE.md 里重复登记：${dupes.join(', ')}`).toEqual([]);
  });

  it('展开迭代到不动点：四层 const/function 间接引用也能追到门（#1209 评审 P3）', () => {
    const source = [
      "const L3 = process.env.MIQI_DEEP_GATE === '1';",
      'function level2() { return L3; }',
      'const L2 = level2();',
      'function level1() { return L2; }',
      'const L1 = level1();',
      "test.describe('depth probe', () => {",
      "  test.skip(!L1, 'deep gate');",
      "  test('t', async () => {});",
      '});',
    ].join('\n');
    const analysis = analyzeSpec(source);
    expect(analysis.requiredEnvs).toContain('MIQI_DEEP_GATE');
    const reasons = fatalReasons(analysis, [
      { label: 'synthetic', envs: new Set(), windows: false },
    ]);
    expect(reasons).toEqual([expect.stringContaining('MIQI_DEEP_GATE')]);
  });

  it('matrix.os 含 windows 的 job 被当作 windows 执行者；固定 runs-on 不受 matrix 影响（#1209 评审 P3）', () => {
    const workflow = [
      'jobs:',
      '  matrix-job:',
      '    strategy:',
      '      matrix:',
      '        os: [windows-latest, ubuntu-latest]',
      '    runs-on: ${{ matrix.os }}',
      '    steps:',
      '      - name: run',
      '        run: npx playwright test --config=playwright.config.ts --project=electron some-spec.spec.ts',
      '  fixed-linux:',
      '    strategy:',
      '      matrix:',
      '        os: [windows-latest, ubuntu-latest]',
      '    runs-on: ubuntu-latest',
      '    steps:',
      '      - name: run',
      '        run: npx playwright test --config=playwright.config.ts --project=electron fixed-spec.spec.ts',
      '  linux-only:',
      '    runs-on: ubuntu-latest',
      '    steps:',
      '      - name: run',
      '        run: npx playwright test --config=playwright.config.ts --project=electron other-spec.spec.ts',
    ].join('\n');
    const parsed = parseWorkflowJobs({ file: 'synthetic.yml', text: workflow });
    const matrixJob = parsed.find((j) => j.id === 'matrix-job');
    const fixedLinuxJob = parsed.find((j) => j.id === 'fixed-linux');
    const linuxJob = parsed.find((j) => j.id === 'linux-only');
    expect(matrixJob && isWindowsJob(matrixJob)).toBe(true);
    expect(matrixJob?.playwrightSteps.flatMap((s) => s.names)).toContain('some-spec.spec.ts');
    // 固定 runs-on: ubuntu-latest —— matrix 里列了 windows 也不能算 Windows 执行者
    expect(fixedLinuxJob && isWindowsJob(fixedLinuxJob)).toBe(false);
    expect(linuxJob && isWindowsJob(linuxJob)).toBe(false);
  });

  it('单行函数体含嵌套花括号时仍能取到完整函数体（#1209 评审）', () => {
    const source = [
      'const L1 = gateConfig();',
      "function gateConfig() { const o = { a: 1 }; return o.a ? true : process.env.MIQI_NESTED_GATE === '1'; }",
      "test.describe('nested', () => {",
      "  test.skip(!L1, 'nested gate');",
      "  test('t', async () => {});",
      '});',
    ].join('\n');
    const analysis = analyzeSpec(source);
    expect(analysis.requiredEnvs).toContain('MIQI_NESTED_GATE');
  });
});
