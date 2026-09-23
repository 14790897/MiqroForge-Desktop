/**
 * 应用数据清理执行器（issue #1177）——扫描/执行/退出清理三合一。
 *
 * 无 Electron 依赖（app/ipcMain/bridge 由调用方注入），像 wsl-state.ts
 * 一样可纯 Node 单测。删除目标全部来自 cleanup-paths.ts 的规划结果，
 * 每个目标在删除前再过一次候选分类与安全校验（#1103：解析不出安全目标
 * 就跳过并记日志，绝不扩大删除范围）。
 */
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import {
  appendFileSync,
  existsSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  ACTIVE_DATA_ROOT_DEFAULT_NAME,
  classifyDataRootCandidate,
  isSafeDeletionRoot,
  PACKAGED_USER_DATA_DIR_NAME,
  planCleanupItems,
  resolveActiveDataRoot,
  resolveExplicitDataRoot,
  UPDATER_CACHE_DIR_NAME,
  WSL_SANDBOX_DISTRO,
  type CleanupContext,
  type CleanupItem,
  type CleanupItemId,
} from '../../shared/cleanup-paths';
import type { CleanupRunReport, CleanupScanItem } from '../../shared/ipc';
import { computeRegistryDataRoot } from '../data-root-registry';
import { decodeWslOutput } from './wsl-state';

const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// 上下文构建（注册表 DataRoot 快照 + 环境变量）
// ---------------------------------------------------------------------------

function platformOf(): CleanupContext['platform'] {
  if (process.platform === 'win32') return 'win32';
  if (process.platform === 'darwin') return 'darwin';
  if (process.platform === 'linux') return 'linux';
  return 'other';
}

/**
 * WSL 命令的异步执行（不阻塞 Electron 主进程事件循环）：
 * execFile 带超时；非零退出/超时都归一成 { status, stdout, stderr, timedOut }，
 * 超时时仍返回已捕获的部分输出。
 */
async function runWsl(
  args: string[],
  timeoutMs: number
): Promise<{ status: number | null; stdout: Buffer; stderr: Buffer; timedOut: boolean }> {
  try {
    const { stdout, stderr } = await execFileAsync('wsl.exe', args, {
      timeout: timeoutMs,
      windowsHide: true,
      encoding: 'buffer',
      maxBuffer: 16 * 1024 * 1024,
    });
    return { status: 0, stdout, stderr, timedOut: false };
  } catch (err) {
    const e = err as { code?: string | number; killed?: boolean; stdout?: Buffer; stderr?: Buffer };
    return {
      status: typeof e.code === 'number' ? e.code : null,
      stdout: e.stdout ?? Buffer.alloc(0),
      stderr: e.stderr ?? Buffer.alloc(0),
      timedOut: e.killed === true,
    };
  }
}

export function buildCleanupContext(opts?: {
  env?: Record<string, string | undefined>;
  registryDataRoot?: string | null;
  /** 应用当前 profile 的 userData（dev 下与打包版 %APPDATA%\miqi-desktop 不同）。 */
  userDataDir?: string;
}): CleanupContext {
  const home = homedir();
  return {
    platform: platformOf(),
    homeDir: home,
    appDataDir: process.env['APPDATA'] || join(home, 'AppData', 'Roaming'),
    localAppDataDir: process.env['LOCALAPPDATA'] || join(home, 'AppData', 'Local'),
    // 与打包版启动时写入注册表的计算同源（computeRegistryDataRoot）：
    // 环境变量 MIQI_HOME 优先、legacy 探测、默认名——避免另起一套 reg.exe
    // 解析逻辑。非 Windows 上无注册表概念，走默认名候选。
    registryDataRoot:
      opts?.registryDataRoot !== undefined
        ? opts.registryDataRoot
        : process.platform === 'win32'
          ? computeRegistryDataRoot()
          : null,
    env: opts?.env ?? process.env,
    systemRoot: process.env['SystemRoot'] || undefined,
    dirExists: existsSync,
    userDataDir: opts?.userDataDir,
  };
}

// ---------------------------------------------------------------------------
// 扫描：存在性 + 大小
// ---------------------------------------------------------------------------

/** 目录大小（上限/时限内）；超限或失败返回 null（UI 显示「未知」）。 */
export async function dirSizeBytes(
  dir: string,
  capBytes = 8 * 1024 ** 3,
  deadlineMs = 10000
): Promise<number | null> {
  const deadline = Date.now() + deadlineMs;
  let total = 0;
  try {
    const entries = await readdir(dir, { recursive: true, withFileTypes: true });
    for (const entry of entries) {
      if (Date.now() > deadline) return null;
      if (total > capBytes) return null;
      if (!entry.isFile()) continue;
      const parent = (entry as { parentPath?: string }).parentPath ?? dir;
      try {
        total += (await stat(join(parent, entry.name))).size;
      } catch {
        /* 扫描中消失的文件忽略 */
      }
    }
    return total;
  } catch {
    return null;
  }
}

export interface WslProbe {
  /** WSL 服务可用（wsl --status 成功）。 */
  wslAvailable: boolean;
  /** 沙箱 distro 存在；null = 探测失败。 */
  distroExists: boolean | null;
  distroSizeBytes: number | null;
  detail?: string;
}

/**
 * `wsl -l -q` 输出（UTF-16LE 解码后）的逐行解析：返回干净 distro 名列表。
 * 头部的本地化提示行（如「适用于 Linux 的 Windows 子系统分发版:」）不含
 * 合法 distro 名特征，直接按行过滤。
 */
export function parseWslDistroList(text: string): string[] {
  const names: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    // distro 名只含 [a-zA-Z0-9._-]；提示行含空格/全角标点，天然被过滤。
    if (/^[A-Za-z0-9._-]+$/.test(line)) names.push(line);
  }
  return names;
}

/** WSL 沙箱 distro 探测：存在性 + distro 内磁盘占用（异步，不阻塞主进程）。 */
export async function probeWslSandbox(opts?: {
  statusTimeoutMs?: number;
  sizeTimeoutMs?: number;
}): Promise<WslProbe> {
  const statusTimeoutMs = opts?.statusTimeoutMs ?? 10000;
  const sizeTimeoutMs = opts?.sizeTimeoutMs ?? 60000;
  if (process.platform !== 'win32') {
    return {
      wslAvailable: false,
      distroExists: false,
      distroSizeBytes: null,
      detail: '非 Windows',
    };
  }

  const status = await runWsl(['--status'], statusTimeoutMs);
  if (status.status !== 0) {
    return {
      wslAvailable: false,
      // WSL 服务不可用（非超时的失败）→ distro 不可能可用，判定不存在；
      // 超时 → 状态未知（null），不替用户下结论。
      distroExists: status.timedOut ? null : false,
      distroSizeBytes: null,
      detail: 'WSL 不可用（未安装或服务未启动），沙箱发行版无法清理',
    };
  }

  let exists: boolean | null = null;
  const list = await runWsl(['-l', '-q'], statusTimeoutMs);
  if (list.status === 0) {
    const names = parseWslDistroList(decodeWslOutput(list.stdout));
    exists = names.includes(WSL_SANDBOX_DISTRO);
  }

  let size: number | null = null;
  if (exists) {
    // -x 只统计 distro 自身文件系统（不跨 /mnt/c 遍历 Windows 盘），
    // -u root 避开普通用户对 /proc /root 等的权限报错。du 个别条目报错时
    // 会以非零退出但 stdout 仍带总量——只要解析得出来就采用。
    const du = await runWsl(
      ['-d', WSL_SANDBOX_DISTRO, '-u', 'root', '--', 'du', '-s', '-x', '-k', '/'],
      sizeTimeoutMs
    );
    const kb = parseInt(decodeWslOutput(du.stdout).trim().split(/\s/)[0] ?? '', 10);
    if (!Number.isNaN(kb)) size = kb * 1024;
  }
  return { wslAvailable: true, distroExists: exists, distroSizeBytes: size };
}

export async function scanCleanup(ctx: CleanupContext): Promise<CleanupScanItem[]> {
  const items = planCleanupItems(ctx);
  const wsl = process.platform === 'win32' ? await probeWslSandbox() : null;
  const out: CleanupScanItem[] = [];
  for (const item of items) {
    let exists: boolean | null = null;
    let sizeBytes: number | null = null;
    let detail: string | undefined;
    if (item.kind === 'wsl-distro') {
      if (!wsl) {
        exists = false;
        detail = '仅 Windows';
      } else {
        exists = wsl.distroExists;
        sizeBytes = wsl.distroSizeBytes;
        detail = wsl.detail;
      }
    } else if (item.path) {
      exists = existsSync(item.path);
      if (exists) {
        sizeBytes = await dirSizeBytes(item.path);
        // data-root:rest 的显示大小要扣除 excludes（保留的 workspace），
        // 否则确认页会把「不会删除」的空间也算进删除量。
        for (const ex of item.excludes) {
          if (sizeBytes == null) break;
          if (!existsSync(ex)) continue;
          const exSize = await dirSizeBytes(ex);
          sizeBytes = exSize == null ? null : Math.max(0, sizeBytes - exSize);
        }
      }
    }
    out.push({
      id: item.id,
      label: item.label,
      description: item.description,
      path: item.path,
      exists,
      sizeBytes,
      defaultChecked: item.defaultChecked,
      deletableNow: item.deletableNow,
      detail,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// 执行
// ---------------------------------------------------------------------------

export interface CleanupRunOptions {
  /** data-root 相关项删除前回调（调用方停 bridge 释放 runtime.db 句柄）。 */
  beforeDataRootDelete?: () => Promise<void>;
  /** 日志路径；缺省 %TEMP%\miqi-cleanup-<ts>.log。 */
  logPath?: string;
  /** user-data 允许删除（仅 --cleanup 退出清理模式为 true，运行态删除必失败）。 */
  allowUserData?: boolean;
  /** 删除失败重试次数与间隔（退出清理模式给旧实例留出释放时间）。 */
  retries?: number;
  retryDelayMs?: number;
}

export function logCleanupLine(logPath: string, line: string): void {
  try {
    appendFileSync(logPath, `${line}\r\n`, 'utf8');
  } catch {
    /* 日志写入失败不阻断清理 */
  }
}

/**
 * 删除前守卫：目录必须解析为「已知候选」（默认名/注册表/环境变量记录值）
 * 且通过安全校验，否则跳过并记原因——宁可漏删（#1103）。
 * 只用于数据根本身；根之内的子项在根校验通过后按构造安全。
 */
function guardDataRoot(dir: string, ctx: CleanupContext): string | null {
  const safe = isSafeDeletionRoot(dir, ctx);
  if (!safe.safe) return safe.reason ?? '路径未通过安全校验';
  const kind = classifyDataRootCandidate(dir, ctx).kind;
  if (kind === 'not-a-candidate') return '不是已知的数据目录（拒绝删除）';
  return null;
}

/**
 * 固定路径守卫（user-data / updater-cache）：路径必须与期望组合完全一致，
 * 防止任何解析偏差把删除范围引到别处。
 */
function guardFixedDir(dir: string, expected: string): string | null {
  if (dir !== expected) return `路径与预期不一致（期望 ${expected}，拒绝删除）`;
  return null;
}

async function rmDirGuarded(
  dir: string,
  label: string,
  itemId: CleanupItemId,
  ctx: CleanupContext,
  report: CleanupRunReport,
  logPath: string,
  opts: CleanupRunOptions,
  guardReason: string | null
): Promise<void> {
  if (guardReason) {
    report.failed.push({ id: itemId, label, reason: guardReason });
    logCleanupLine(logPath, `[跳过] ${label}: ${dir}（${guardReason}）`);
    return;
  }
  const retries = opts.retries ?? 0;
  for (let attempt = 0; ; attempt++) {
    try {
      rmSync(dir, { recursive: true, force: true });
      if (!existsSync(dir)) {
        report.cleaned.push({ id: itemId, label });
        logCleanupLine(logPath, `[已清理] ${label}: ${dir}`);
        return;
      }
      // 删除后仍存在：占用或权限问题
      if (attempt >= retries) {
        report.failed.push({ id: itemId, label, reason: '删除后仍存在，可能被其他进程占用' });
        logCleanupLine(logPath, `[失败] ${label}: ${dir}（删除后仍存在，可能被占用）`);
        return;
      }
    } catch (err) {
      if (attempt >= retries) {
        report.failed.push({ id: itemId, label, reason: `删除失败: ${(err as Error).message}` });
        logCleanupLine(logPath, `[失败] ${label}: ${dir}（${(err as Error).message}）`);
        return;
      }
    }
    await new Promise((r) => setTimeout(r, opts.retryDelayMs ?? 2000));
  }
}

/** WSL 沙箱 distro 注销：terminate → unregister，只碰精确名字（异步）。 */
export async function unregisterWslSandbox(
  label: string,
  report: CleanupRunReport,
  logPath: string,
  opts: { terminate?: boolean } = { terminate: true }
): Promise<void> {
  if (opts.terminate) {
    // 未运行时报错可忽略
    await runWsl(['--terminate', WSL_SANDBOX_DISTRO], 30000);
  }
  const r = await runWsl(['--unregister', WSL_SANDBOX_DISTRO], 180000);
  if (r.status === 0) {
    report.cleaned.push({ id: 'wsl-distro', label });
    logCleanupLine(logPath, `[已清理] ${label}: ${WSL_SANDBOX_DISTRO}`);
    return;
  }
  const stderr = decodeWslOutput(r.stderr).replace(/\s+/g, ' ').trim();
  const reason = stderr
    ? `注销失败（退出码 ${r.status ?? '?'}）：${stderr.slice(0, 200)}`
    : `注销失败（退出码 ${r.status ?? '?'}）。可手动执行: wsl --unregister ${WSL_SANDBOX_DISTRO}`;
  report.failed.push({ id: 'wsl-distro', label, reason });
  logCleanupLine(logPath, `[失败] ${label}：${reason}`);
}

export async function runCleanup(
  ctx: CleanupContext,
  selected: CleanupItem[],
  opts: CleanupRunOptions = {}
): Promise<CleanupRunReport> {
  const logPath = opts.logPath ?? join(tmpdir(), `miqi-cleanup-${Date.now()}.log`);
  const report: CleanupRunReport = { cleaned: [], failed: [], logPath };
  logCleanupLine(
    logPath,
    `===== MiQroForge Desktop 应用数据清理开始（${new Date().toISOString()}）=====`
  );

  const wantsDataRoot = selected.some((i) => i.kind === 'workspace' || i.kind === 'data-root');
  if (wantsDataRoot && opts.beforeDataRootDelete) {
    await opts.beforeDataRootDelete();
  }

  // 数据根必须先于其余项确定（workspace 守卫需要知道根是否合法）。
  const explicit = resolveExplicitDataRoot(ctx);
  // 显式数据根（注册表/MIQI_HOME）存在但不安全 → 跳过全部数据根相关项，
  // 与 NSIS 卸载器同语义（#1103：宁可漏删，不回退默认名候选扩大范围）。
  const dataRootBlocked = explicit !== null && !explicit.safe;
  if (dataRootBlocked) {
    const reason = `显式数据根未通过安全校验（${explicit.reason ?? '未知原因'}），跳过数据根清理`;
    logCleanupLine(logPath, `[跳过] ${reason}`);
    for (const item of selected.filter((i) => i.kind === 'workspace' || i.kind === 'data-root')) {
      report.failed.push({ id: item.id, label: item.label, reason });
    }
  }
  // 应用内清理只删扫描/确认页展示的那一个根（与 planCleanupItems 一致）：
  // 显式安全根，或 resolveActiveDataRoot 的生效根（MIQI_HOME/legacy/默认名，
  // ctx.dirExists 由 buildCleanupContext 注入）。全部候选根的清除由 NSIS
  // 卸载器负责（那边逐候选探测），应用内不越界删除未展示的路径。
  const primaryRoot = explicit?.safe ? explicit.path : resolveActiveDataRoot(ctx);
  const rootTargets = [primaryRoot];

  for (const item of selected) {
    // 已在上面的 dataRootBlocked 分支记入 failed，这里不重复执行。
    if (dataRootBlocked && (item.kind === 'workspace' || item.kind === 'data-root')) continue;
    switch (item.kind) {
      case 'workspace': {
        if (!item.path || !primaryRoot) {
          report.failed.push({ id: item.id, label: item.label, reason: '数据根不可确认，跳过' });
          logCleanupLine(logPath, `[跳过] ${item.label}（数据根不可确认）`);
          continue;
        }
        // workspace 只能是 <数据根>/workspace 这个相对位置。
        const expected = join(primaryRoot, 'workspace');
        if (item.path !== expected) {
          report.failed.push({
            id: item.id,
            label: item.label,
            reason: `路径异常（期望 ${expected}）`,
          });
          logCleanupLine(logPath, `[跳过] ${item.label}: ${item.path}（期望 ${expected}）`);
          continue;
        }
        const rootGuardReason = guardDataRoot(primaryRoot, ctx);
        await rmDirGuarded(
          item.path,
          item.label,
          item.id,
          ctx,
          report,
          logPath,
          opts,
          rootGuardReason
        );
        continue;
      }
      case 'data-root': {
        // 遍历删除目标全集（显式根或全部默认名候选），每个根单独过守卫；
        // 根之内的子项按构造安全，无需逐个候选分类；各根的 workspace 一律保留。
        for (const target of rootTargets) {
          const targetGuardReason = guardDataRoot(target, ctx);
          if (targetGuardReason) {
            report.failed.push({ id: item.id, label: item.label, reason: targetGuardReason });
            logCleanupLine(logPath, `[跳过] ${item.label}: ${target}（${targetGuardReason}）`);
            continue;
          }
          let children: string[] = [];
          try {
            children = readdirSync(target);
          } catch {
            /* 根不存在则无事可做 */
          }
          for (const child of children) {
            const childPath = join(target, child);
            if (join(target, 'workspace') === childPath) {
              logCleanupLine(logPath, `[保留] ${childPath}`);
              continue;
            }
            await rmDirGuarded(
              childPath,
              `${item.label} / ${child}`,
              item.id,
              ctx,
              report,
              logPath,
              opts,
              null
            );
          }
        }
        continue;
      }
      case 'user-data': {
        if (!opts.allowUserData) {
          report.failed.push({
            id: item.id,
            label: item.label,
            reason: '应用运行中无法删除用户数据，请使用「退出并清理」',
          });
          continue;
        }
        if (!item.path) {
          report.failed.push({ id: item.id, label: item.label, reason: '路径不可确认，跳过' });
          continue;
        }
        // dev/E2E 下 userData 是 miqi-desktop-dev\ws-<hash>（或 MIQI_USER_DATA_DIR），
        // 以调用方注入的 userDataDir 为准，避免误删打包版真实 profile。
        const expected = ctx.userDataDir ?? join(ctx.appDataDir, PACKAGED_USER_DATA_DIR_NAME);
        await rmDirGuarded(
          item.path,
          item.label,
          item.id,
          ctx,
          report,
          logPath,
          opts,
          guardFixedDir(item.path, expected)
        );
        continue;
      }
      case 'wsl-distro': {
        await unregisterWslSandbox(item.label, report, logPath);
        continue;
      }
      case 'updater-cache': {
        if (!item.path) {
          report.failed.push({ id: item.id, label: item.label, reason: '路径不可确认，跳过' });
          continue;
        }
        const expected = join(ctx.localAppDataDir, UPDATER_CACHE_DIR_NAME);
        await rmDirGuarded(
          item.path,
          item.label,
          item.id,
          ctx,
          report,
          logPath,
          opts,
          guardFixedDir(item.path, expected)
        );
        continue;
      }
    }
  }

  logCleanupLine(
    logPath,
    `===== 清理结束：成功 ${report.cleaned.length}，失败 ${report.failed.length} =====`
  );
  return report;
}

// ---------------------------------------------------------------------------
// 退出清理（--cleanup 模式）
// ---------------------------------------------------------------------------

export interface CleanupScope {
  /** 退出时快照的上下文（新实例环境可能已变化）。 */
  ctx: {
    homeDir: string;
    appDataDir: string;
    localAppDataDir: string;
    registryDataRoot: string | null;
    systemRoot?: string;
    platform: CleanupContext['platform'];
    userDataDir?: string;
  };
  ids: CleanupItemId[];
  logPath: string;
}

export function defaultCleanupScopePath(): string {
  return join(tmpdir(), 'miqi-cleanup-scope.json');
}

export function writeCleanupScope(
  scope: CleanupScope,
  scopePath = defaultCleanupScopePath()
): void {
  writeFileSync(scopePath, JSON.stringify(scope, null, 2), 'utf8');
}

export function readCleanupScope(scopePath: string): CleanupScope | null {
  try {
    const raw = JSON.parse(readFileSync(scopePath, 'utf8'));
    if (!raw || typeof raw !== 'object' || !Array.isArray(raw.ids)) return null;
    return raw as CleanupScope;
  } catch {
    return null;
  }
}

/**
 * 退出并清理：把 scope 落盘后拉起 detached 的 --cleanup 实例，
 * 由调用方随后 app.quit()。清理完成后不自动重启（产品决策：#1177）。
 */
export function launchQuitAndClean(
  execPath: string,
  scope: CleanupScope,
  scopePath = defaultCleanupScopePath(),
  extraArgs: string[] = []
): { ok: boolean; reason?: string } {
  try {
    writeCleanupScope(scope, scopePath);
    // dev 下 execPath 是 electron.exe，必须带上应用路径才能跑进本应用的 main()。
    const child = spawn(execPath, [...extraArgs, '--cleanup', scopePath], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.unref();
    return { ok: true };
  } catch (err) {
    return { ok: false, reason: (err as Error).message };
  }
}

/** --cleanup 模式入口：读 scope → 执行（允许 user-data）→ 写日志。 */
export async function runCleanupFromScope(scopePath: string): Promise<CleanupRunReport> {
  const scope = readCleanupScope(scopePath);
  if (!scope) {
    const logPath = join(tmpdir(), `miqi-cleanup-${Date.now()}.log`);
    logCleanupLine(logPath, `[失败] 清理 scope 文件缺失或损坏: ${scopePath}`);
    return {
      cleaned: [],
      failed: [{ id: 'data-root:rest', label: '清理', reason: 'scope 文件缺失或损坏' }],
      logPath,
    };
  }
  try {
    rmSync(scopePath, { force: true });
  } catch {
    /* scope 残留可被下次覆盖 */
  }
  const ctx: CleanupContext = {
    platform: scope.ctx.platform,
    homeDir: scope.ctx.homeDir,
    appDataDir: scope.ctx.appDataDir,
    localAppDataDir: scope.ctx.localAppDataDir,
    registryDataRoot: scope.ctx.registryDataRoot,
    env: process.env,
    systemRoot: scope.ctx.systemRoot,
    dirExists: existsSync,
    userDataDir: scope.ctx.userDataDir,
  };
  const selected = planCleanupItems(ctx).filter((i) => scope.ids.includes(i.id));
  return runCleanup(ctx, selected, {
    logPath: scope.logPath,
    allowUserData: true,
    retries: 3,
    retryDelayMs: 2000,
  });
}
