/**
 * 本地 mock provider 的公共启动器（#1118 macOS CI 修复）。
 *
 * ── 为什么要单独一个模块 ─────────────────────────────────────────────
 * macos-e2e（commit 0191ca40）上 #1118 的两个 spec 都在 beforeAll 里挂了：
 *
 *     Error: mock mock_hang.py startup line not seen in 30s:
 *
 * stderr 全空、进程退出码始终是 null、30s 空转。旧写法是每个 spec 各抄一份的
 *
 *     const python = process.env['MIQI_PYTHON_PATH'] || 'python';
 *     const proc = spawn(python, [script, port]);
 *
 * 有两处硬伤，正好都会伪装成上面这条「mock 起得慢」：
 *
 *   1. 只有 `python` 一个候选。macOS runner 上 PATH 里若是 `python3` 而没有
 *      `python`（或 `MIQI_PYTHON_PATH` 指向失效解释器），spawn 直接 ENOENT；
 *   2. 没有 `on('error')`。ENOENT/EACCES 这类 spawn 失败**不会**让
 *      `proc.exitCode` 变非空、也**不会**往 stderr 写任何字节，只发一个 'error'
 *      事件——没人接就彻底查无此错，于是循环一直转到 30s 才报「启动行没出现」。
 *
 * 本模块做两件事（对应 #1118 修复任务的两条要求）：
 *
 *   - `resolveMockPython()`：候选链逐个探测，选第一个真的能起解释器的
 *     （MIQI_PYTHON_PATH → uv run python → 仓库 venv → python3 → python）。
 *     思路与 `helpers/electron-setup.ts` 里「MIQI_PYTHON_PATH 探测不通过就清掉
 *     让 bridge 走 uv」一致：不信任环境变量本身，只信任探测结果。
 *   - `startMockServer()`：spawn 失败立刻抛（不再空转 30s），失败/超时消息带上
 *     解析结果、各候选探测摘要、PATH（截断）、cwd 与 stdout/stderr 尾巴。
 *
 * 成功时只打一行（最终选择）；失败时全量打（每个候选的探测结果）。
 *
 * 各 spec 的注入逻辑（chat:progress / chat:final 怎么造）留在各自文件里，
 * 本模块只管「把 mock 起起来」。
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { APPS_DESKTOP } from './electron-setup';

/** 仓库根：mock 脚本在 <repo>/scripts 下，子进程 cwd 与 `uv run` 的工程根都是它。 */
const REPO_ROOT = join(APPS_DESKTOP, '..', '..');

/** 单个候选的探测超时。冷启动的 `uv run` 可能慢，但不该慢到拖垮 beforeAll。 */
const PROBE_TIMEOUT_MS = 5_000;
/**
 * 探测载荷：只要求「能起解释器」。刻意不 import 任何第三方包——mock 脚本本身
 * 只用标准库，探测也不该被 venv 里的依赖装没装全绑架。
 */
const PROBE_ARGS = ['-c', 'import sys; sys.exit(0)'];
/** mock 启动行等待上限（沿用旧实现的 30s）。 */
const STARTUP_TIMEOUT_MS = 30_000;
/** 失败消息里 PATH 的截断长度（PATH 可能上千字符，但前段才是有用的那部分）。 */
const PATH_SNIPPET_LIMIT = 800;
/** 失败消息里 stdout/stderr 的保留尾巴长度。 */
const OUTPUT_TAIL_LIMIT = 2_000;

/** 一个候选的探测结果。 */
export interface PythonProbe {
  /** 人类可读标识（日志用）。 */
  label: string;
  /** 实际传给 spawn 的可执行文件（`uv run python` 这档是 `uv`）。 */
  command: string;
  /** 固定前缀参数；mock 脚本路径与端口追加在其后。 */
  args: string[];
  /** 探测是否通过。 */
  ok: boolean;
  /** 探测细节：`ok` / `ENOENT: …` / `exit 1 — …`。 */
  detail: string;
}

/** 解析结果 + 全量候选探测记录（失败消息里要全量打印）。 */
export interface ResolvedMockPython {
  label: string;
  command: string;
  args: string[];
  probes: PythonProbe[];
}

/** 每个 worker 只探测一次：解释器在该进程生命周期内不会变。 */
let cached: ResolvedMockPython | undefined;

/** 候选链（顺序即优先级，见文件头）。 */
function pythonCandidates(): Array<Omit<PythonProbe, 'ok' | 'detail'>> {
  const candidates: Array<Omit<PythonProbe, 'ok' | 'detail'>> = [];

  // a. 显式指定的解释器：只是候选之一，探测不通过就往后走（本地/CI 都可能留着一个
  //    已失效的 uv 托管路径，electron-setup.ts 里已见过这种残留）。
  const fromEnv = process.env['MIQI_PYTHON_PATH'];
  if (fromEnv) {
    candidates.push({ label: `MIQI_PYTHON_PATH=${fromEnv}`, command: fromEnv, args: [] });
  }

  // b. uv：CI（macos job `pip install uv; uv sync`）与本地都有，会自己找仓库 venv。
  candidates.push({ label: 'uv run python', command: 'uv', args: ['run', 'python'] });

  // c. 仓库 venv。macos CI 实测 `uv sync` 建在**仓库根**（日志：Creating virtual
  //    environment at: <repo>/.venv），apps/desktop 下也允许存在——两处都试。
  for (const root of [APPS_DESKTOP, REPO_ROOT]) {
    const exe =
      process.platform === 'win32'
        ? join(root, '.venv', 'Scripts', 'python.exe')
        : join(root, '.venv', 'bin', 'python');
    candidates.push({ label: `venv ${exe}`, command: exe, args: [] });
  }

  // d./e. PATH 上的通用名。posix 上 `python3` 才是稳的那个，所以排在 `python` 前。
  if (process.platform !== 'win32') {
    candidates.push({ label: 'python3', command: 'python3', args: [] });
  }
  candidates.push({ label: 'python', command: 'python', args: [] });

  return candidates;
}

/** 探测单个候选：能 `import sys` 且退出码 0 才算可用。 */
function probeCandidate(candidate: Omit<PythonProbe, 'ok' | 'detail'>): PythonProbe {
  // 绝对路径候选（venv）先看存在性：比 spawn 一遍更快，日志里也能一眼区分
  // 「环境里没有这个 venv」和「这个 venv 坏了」。
  if (isAbsolute(candidate.command) && !existsSync(candidate.command)) {
    return { ...candidate, ok: false, detail: 'not found (no such file)' };
  }
  const res = spawnSync(candidate.command, [...candidate.args, ...PROBE_ARGS], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    timeout: PROBE_TIMEOUT_MS,
    windowsHide: true,
  });
  if (res.error) {
    const err = res.error as NodeJS.ErrnoException;
    return { ...candidate, ok: false, detail: `${err.code ?? 'ERROR'}: ${err.message}` };
  }
  if (res.status !== 0) {
    const stderr = (res.stderr ?? '').trim().slice(-200);
    return { ...candidate, ok: false, detail: `exit ${res.status}${stderr ? ` — ${stderr}` : ''}` };
  }
  return { ...candidate, ok: true, detail: 'ok' };
}

function truncate(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, limit)}…(+${text.length - limit} chars)`;
}

/** 探测记录的多行渲染（缩进 2 空格，直接拼进日志/错误消息）。 */
function formatProbeList(probes: PythonProbe[]): string {
  return probes.map((p) => `  - [${p.ok ? 'ok' : 'FAIL'}] ${p.label} → ${p.detail}`).join('\n');
}

/**
 * 解析可用的 python 解释器（进程内缓存）。
 *
 * 全部候选都不可用时抛错，消息里带全量探测结果——这正是旧实现最缺的那块信息。
 */
export function resolveMockPython(): ResolvedMockPython {
  if (cached) return cached;
  const probes = pythonCandidates().map(probeCandidate);
  const hit = probes.find((p) => p.ok);
  if (!hit) {
    const dump = formatProbeList(probes);
    console.log(`[mock-python] ❌ 没有可用的 python 候选：\n${dump}`);
    throw new Error(
      `no usable python interpreter for local mock servers\n` +
        `probes:\n${dump}\n` +
        `cwd=${REPO_ROOT}\n` +
        `PATH (truncated)=${truncate(process.env.PATH ?? '', PATH_SNIPPET_LIMIT)}`
    );
  }
  cached = { label: hit.label, command: hit.command, args: hit.args, probes };
  // 成功时一行，失败时全量（见文件头）。
  console.log(
    `[mock-python] python 解释器：${hit.label} → ${[hit.command, ...hit.args].join(' ')}`
  );
  return cached;
}

/** mock 启动行里的地址（各 mock 脚本统一打 `... http://127.0.0.1:<port>/v1`）。 */
const READY_URL_RE = /http:\/\/127\.0\.0\.1:(\d+)\/v1/;

/**
 * 从**累计的** stdout 文本里解析 mock 的 ready URL，没有则返回 null。
 *
 * 单独抽出来是为了能直接单测分块场景（见 `mock-server.test.ts`）：调用方负责
 * 拼接，本函数只看「迄今为止的全部输出」，所以一行被切成几段都不影响结果。
 */
export function matchReadyUrl(accumulatedStdout: string): string | null {
  const m = accumulatedStdout.match(READY_URL_RE);
  return m ? `http://127.0.0.1:${m[1]}/v1` : null;
}

/**
 * 起一个 mock provider（`scripts/` 下的脚本），等它打出启动行。
 *
 * 失败路径全部带上诊断块：解析结果、候选探测摘要、PATH（截断）、cwd、
 * stdout/stderr 尾巴。spawn 失败（ENOENT 等）立刻抛，不再空转 30s。
 */
export async function startMockServer(
  script: string
): Promise<{ proc: ChildProcess; mockUrl: string }> {
  const resolved = resolveMockPython();
  const port = 20000 + Math.floor(Math.random() * 20000);
  const scriptPath = join(REPO_ROOT, 'scripts', script);
  const proc = spawn(resolved.command, [...resolved.args, scriptPath, String(port)], {
    cwd: REPO_ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
    windowsHide: true,
  });

  let readyUrl = '';
  let stdoutTail = '';
  let stderrTail = '';
  // spawn 失败只发 'error' 事件：不接住的话，exitCode 保持 null、stderr 保持空，
  // 调用方只能看到 30s 后的「启动行没出现」——#1118 在 macOS 上就是这么丢的信息。
  const spawnErrors: Error[] = [];
  proc.on('error', (err) => spawnErrors.push(err));
  proc.stdout?.on('data', (d) => {
    stdoutTail = (stdoutTail + String(d)).slice(-OUTPUT_TAIL_LIMIT);
    // 匹配**累计的** stdout 尾巴，不是这一个 chunk（#1118 第九轮）：
    // Node 的 pipe 会把一次 write 切成任意多个 'data' 事件，`...write(banner)`
    // 完全可能在 URL 中间断开——只在单 chunk 上 match 就会永远等不到 ready 行，
    // 一直空转到 30s 超时（症状与「mock 起得慢」一模一样，白烧整个 beforeAll）。
    // 每来一段就重试一次，行一旦拼齐即命中；`readyUrl` 已定就不再重复匹配。
    if (!readyUrl) {
      const matched = matchReadyUrl(stdoutTail);
      if (matched) readyUrl = matched;
    }
  });
  proc.stderr?.on('data', (d) => {
    stderrTail = (stderrTail + String(d)).slice(-OUTPUT_TAIL_LIMIT);
  });

  /** 失败消息的诊断块（要求：解析结果 + 候选探测摘要 + PATH + cwd）。 */
  const diagnostics = (): string =>
    [
      `resolve=${resolved.label} (${[resolved.command, ...resolved.args].join(' ')})`,
      `script=${scriptPath} port=${port}`,
      `cwd=${REPO_ROOT}`,
      `PATH (truncated)=${truncate(process.env.PATH ?? '', PATH_SNIPPET_LIMIT)}`,
      `probes:`,
      formatProbeList(resolved.probes),
      `stdout tail=${stdoutTail || '<empty>'}`,
      `stderr tail=${stderrTail || '<empty>'}`,
    ].join('\n');

  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  while (!readyUrl && Date.now() < deadline) {
    if (spawnErrors.length > 0) {
      throw new Error(
        `mock ${script} failed to spawn: ${spawnErrors[0]?.message}\n${diagnostics()}`
      );
    }
    if (proc.exitCode !== null) {
      throw new Error(`mock ${script} exited early (code ${proc.exitCode})\n${diagnostics()}`);
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  if (!readyUrl) {
    proc.kill();
    throw new Error(
      `mock ${script} startup line not seen in ${STARTUP_TIMEOUT_MS / 1000}s\n${diagnostics()}`
    );
  }
  console.log(`[mock-python] mock ${script} ready at ${readyUrl} (${resolved.label})`);
  return { proc, mockUrl: readyUrl };
}
