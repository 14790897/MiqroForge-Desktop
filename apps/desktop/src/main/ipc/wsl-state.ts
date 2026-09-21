/**
 * Pure WSL state helpers shared by the IPC handlers and unit tests.
 *
 * Split out of index.ts so the state-machine logic can be tested without
 * Electron IPC wiring.  All helpers are free of Electron imports.
 *
 * Notes from live testing (2026-09):
 * - `Get-WindowsOptionalFeature` (DISM) requires elevation and always fails
 *   inside the non-elevated app; Win32_OptionalFeature over WMI is readable
 *   unelevated and reflects pending DISM changes immediately.
 * - `Start-Process -Verb RunAs -Wait -PassThru | Select-Object ExitCode`
 *   throws "Process must exit before requested information can be
 *   determined" after UAC elevation, so exit codes of elevated commands
 *   must never be *returned* by Start-Process.  They can still be recovered
 *   by having the elevated process write them to a file (see runElevated) —
 *   without that, every failure mode collapses into one fallback message.
 */
import { spawn, spawnSync } from 'child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import type { WslFeatureState } from '../../shared/ipc';

export interface FeatureStates {
  /** False when the feature read itself failed (state cannot be verified). */
  ok: boolean;
  featureWsl: boolean;
  featureVmp: boolean;
}

const WMI_FEATURE_CMD =
  'Get-CimInstance Win32_OptionalFeature | ' +
  'Where-Object { $_.Name -eq "Microsoft-Windows-Subsystem-Linux" -or $_.Name -eq "VirtualMachinePlatform" } | ' +
  'ForEach-Object { "$($_.Name)=$($_.InstallState)" }';

export function readFeatureStates(timeoutMs = 15000): FeatureStates {
  try {
    const r = spawnSync('powershell.exe', ['-NoProfile', '-Command', WMI_FEATURE_CMD], {
      timeout: timeoutMs,
      encoding: 'utf8',
      windowsHide: true,
    });
    if (r.status !== 0 || !r.stdout) {
      return { ok: false, featureWsl: false, featureVmp: false };
    }
    const read = (name: string): boolean => {
      const m = r.stdout.match(new RegExp(`${name}=(\\d)`));
      return !!m && m[1] === '1';
    };
    return {
      ok: true,
      featureWsl: read('Microsoft-Windows-Subsystem-Linux'),
      featureVmp: read('VirtualMachinePlatform'),
    };
  } catch {
    return { ok: false, featureWsl: false, featureVmp: false };
  }
}

/** True when the distro can run bash (filters docker-desktop & friends). */
export function isBashCapableDistro(distro: string, timeoutMs = 8000): boolean {
  try {
    const r = spawnSync('wsl.exe', ['-d', distro, '--', 'bash', '-c', 'echo ok'], {
      timeout: timeoutMs,
      encoding: 'buffer',
      windowsHide: true,
    });
    return r.status === 0;
  } catch {
    return false;
  }
}

/** True when the distro finished first-run setup (a non-root user exists). */
export function hasNonRootUser(distro: string, timeoutMs = 10000): boolean {
  try {
    const r = spawnSync(
      'wsl.exe',
      ['-d', distro, '--', 'bash', '-c', 'id -u 2>/dev/null || echo ""'],
      { timeout: timeoutMs, encoding: 'utf8', windowsHide: true }
    );
    if (r.status !== 0 || !r.stdout?.trim()) return false;
    const uid = parseInt(r.stdout.trim(), 10);
    return !Number.isNaN(uid) && uid > 0;
  } catch {
    return false;
  }
}

/** True when `wsl --status` succeeds (WSL service reachable). */
export function wslStatusWorks(timeoutMs = 8000): boolean {
  try {
    const r = spawnSync('wsl', ['--status'], {
      timeout: timeoutMs,
      encoding: 'buffer',
      windowsHide: true,
    });
    return r.status === 0;
  } catch {
    return false;
  }
}

/** True when the WSL app package (kernel) is installed. */
export function wslPackageInstalled(timeoutMs = 10000): boolean {
  try {
    const r = spawnSync(
      'powershell.exe',
      [
        '-NoProfile',
        '-Command',
        'Get-AppxPackage -Name "*WindowsSubsystemForLinux*" | Select-Object -ExpandProperty Name',
      ],
      { timeout: timeoutMs, encoding: 'utf8', windowsHide: true }
    );
    return r.status === 0 && !!r.stdout?.trim();
  } catch {
    return false;
  }
}

/**
 * Kernel presence with retries.  A single probe misreports a successful
 * `wsl --install` as "package not found": the Appx registration can lag a few
 * seconds behind the elevated process exiting, and `wsl --status` keeps
 * failing until the next reboot.  Retrying is preferred over widening the
 * Appx query with `-AllUsers`, which itself requires elevation.
 */
export function wslKernelPresent(attempts = 3, intervalMs = 3000): boolean {
  for (let i = 0; i < attempts; i++) {
    if (wslStatusWorks() || wslPackageInstalled()) return true;
    if (i < attempts - 1) sleepSync(intervalMs);
  }
  return false;
}

function sleepSync(ms: number): void {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

// ---------------------------------------------------------------------------
// Elevation trampoline — recovers the exit code / output of a UAC-elevated
// process.  Start-Process cannot return them (`ExitCode` throws after RunAs),
// so the elevated child writes its exit code to a file instead.
//
// The elevated payload travels as an *encoded command line*, never as a script
// file: %TEMP% is user-writable, so a same-user process could replace a script
// between writing it and the UAC-elevated launch, turning the elevation prompt
// into an admin code-execution primitive.  Only result files live in %TEMP%.
// ---------------------------------------------------------------------------

/** Exit code the trampoline reports when the user declines the UAC prompt. */
export const ELEVATION_CANCELLED = 1223; // Win32 ERROR_CANCELLED

export interface ElevatedRunResult {
  /**
   * `ok` = elevated process ran and exited 0; `failed` = it ran and exited
   * non-zero; `cancelled` = the UAC prompt was declined; `unknown` = the
   * trampoline itself failed (nothing can be said about the command).
   */
  kind: 'ok' | 'failed' | 'cancelled' | 'unknown';
  exitCode: number | null;
  /** Combined stdout+stderr of the elevated process. */
  output: string;
  /** Transport-level error detail, only set when kind is `unknown`. */
  error?: string;
}

export interface ElevatedPayload {
  /** Executable run elevated; its stdout/stderr and exit code are captured. */
  command?: { file: string; args?: string[] };
  /** PowerShell script run elevated; its output and exit code are captured. */
  powershell?: string;
}

/** Decode command output, which may be UTF-16LE even when redirected to a file. */
export function decodeWslOutput(buf: Buffer | string | null | undefined): string {
  if (!buf || buf.length === 0) return '';
  if (typeof buf === 'string') return buf.replace(/\0/g, '').trim();
  if (buf.length >= 2 && buf[0] === 0xff && buf[1] === 0xfe) {
    return buf.toString('utf16le').replace(/^﻿/, '').replace(/\0/g, '').trim();
  }
  // ASCII text in UTF-16LE is NUL-interleaved, but CJK text is not: its code
  // units have no zero high byte, so the NUL heuristic alone silently turns
  // localized (e.g. Chinese) output into mojibake.  Fall back to "UTF-8
  // decoding produced replacement characters" as a second signal.
  const nullRatio =
    buf.reduce((acc, b, i) => (i % 2 === 1 && b === 0 ? acc + 1 : acc), 0) /
    Math.max(1, Math.floor(buf.length / 2));
  const asUtf8 = buf.toString('utf8');
  const looksUtf16 = nullRatio > 0.3 || (buf.length % 2 === 0 && asUtf8.includes('�'));
  if (looksUtf16) return buf.toString('utf16le').replace(/\0/g, '').trim();
  return asUtf8.replace(/\0/g, '').trim();
}

/**
 * One-line description of a failed elevated run, for the error card.  Exit
 * code 0 is omitted: in a failure path it says "the process did not fail",
 * and the captured output is the part that explains what went wrong.
 */
export function summarizeElevated(r: ElevatedRunResult, maxLen = 300): string {
  if (r.kind === 'unknown' && r.error) return r.error;
  const parts: string[] = [];
  if (r.exitCode !== null && r.exitCode !== 0) parts.push(`退出码 ${r.exitCode}`);
  const out = r.output.replace(/\s+/g, ' ').trim();
  if (out) parts.push(out.length > maxLen ? out.slice(-maxLen) : out);
  return parts.join('——') || '无输出';
}

/**
 * Run a command with administrator rights (UAC prompt) and recover its exit
 * code and output.  Blocks until the elevated process exits.
 */
interface ElevatorPaths {
  dir: string;
  outPath: string;
  errPath: string;
  codePath: string;
  trampolinePath: string;
}

/** Temp-dir layout + trampoline command line for one elevated run. */
function prepareElevator(payload: ElevatedPayload): { paths: ElevatorPaths; trampoline: string } {
  const dir = mkdtempSync(join(tmpdir(), 'miqi-elev-'));
  const paths: ElevatorPaths = {
    dir,
    outPath: join(dir, 'out.txt'),
    errPath: join(dir, 'err.txt'),
    codePath: join(dir, 'exit.txt'),
    trampolinePath: join(dir, 'trampoline.txt'),
  };

  const elevated = payload.powershell
    ? powershellCapture(payload.powershell, paths.outPath, paths.errPath, paths.codePath)
    : commandCapture(payload.command ?? { file: '' }, paths.outPath, paths.errPath, paths.codePath);

  const trampoline =
    "$ErrorActionPreference='Stop'; " +
    `try { Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','${encodeCommand(elevated)}') ` +
    '-Verb RunAs -Wait -ErrorAction Stop } ' +
    'catch { ' +
    `if ($_.Exception.NativeErrorCode -eq ${ELEVATION_CANCELLED}) { exit ${ELEVATION_CANCELLED} } ` +
    `Set-Content -LiteralPath '${psEscape(paths.trampolinePath)}' -Value $_.Exception.Message -Encoding UTF8; ` +
    'exit 99 }';

  return { paths, trampoline };
}

function removeElevatorDir(dir: string | null): void {
  if (!dir) return;
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best-effort */
  }
}

/** Build the result of an elevated run from its trampoline files. */
function collectElevatedResult(
  paths: ElevatorPaths,
  info: {
    cancelled?: boolean;
    status: number | null;
    stderr?: Buffer | string | null;
    error?: string;
  }
): ElevatedRunResult {
  if (info.error) return { kind: 'unknown', exitCode: null, output: '', error: info.error };
  if (info.cancelled) return { kind: 'cancelled', exitCode: null, output: '' };

  const stdout = decodeWslOutput(readFileOrNull(paths.outPath));
  const stderr = decodeWslOutput(readFileOrNull(paths.errPath));
  const output = [stdout, stderr].filter((s) => s.length > 0).join('\n');
  const exitCode = readExitCode(paths.codePath);
  if (exitCode === null) {
    // The elevated process never wrote its exit code: the trampoline failed
    // (no UAC prompt was shown, or the elevated process was killed early).
    const detail =
      readTextOrNull(paths.trampolinePath) ||
      decodeWslOutput(info.stderr) ||
      `提权进程未返回结果（powershell 退出码 ${info.status}）`;
    return { kind: 'unknown', exitCode: null, output, error: detail };
  }
  return exitCode === 0 ? { kind: 'ok', exitCode, output } : { kind: 'failed', exitCode, output };
}

/**
 * Run a command with administrator rights (UAC prompt) and recover its exit
 * code and output.  Blocks until the elevated process exits.
 */
export function runElevated(payload: ElevatedPayload, timeoutMs = 300000): ElevatedRunResult {
  let paths: ElevatorPaths | null = null;
  try {
    const prepared = prepareElevator(payload);
    paths = prepared.paths;

    const r = spawnSync(
      'powershell.exe',
      ['-NoProfile', '-EncodedCommand', encodeCommand(prepared.trampoline)],
      { timeout: timeoutMs, encoding: 'buffer', windowsHide: true }
    );

    return collectElevatedResult(paths, {
      status: r.status,
      stderr: r.stderr as Buffer | null,
      error: r.error?.message,
      cancelled: r.status === ELEVATION_CANCELLED,
    });
  } catch (e: any) {
    return { kind: 'unknown', exitCode: null, output: '', error: e?.message ?? String(e) };
  } finally {
    removeElevatorDir(paths?.dir ?? null);
  }
}

/**
 * Same contract as `runElevated`, but yields the main thread while the elevated
 * process runs.  The app calls this from the Electron main process, where a
 * blocking wait freezes every window until the user answers the UAC prompt and
 * the elevated work (a DISM feature enable can take minutes) finishes.
 */
export function runElevatedAsync(
  payload: ElevatedPayload,
  timeoutMs = 300000
): Promise<ElevatedRunResult> {
  return new Promise((resolve) => {
    let paths: ElevatorPaths | null = null;
    let settled = false;
    const finish = (result: ElevatedRunResult) => {
      if (settled) return;
      settled = true;
      removeElevatorDir(paths?.dir ?? null);
      resolve(result);
    };

    try {
      const prepared = prepareElevator(payload);
      paths = prepared.paths;

      const child = spawn(
        'powershell.exe',
        ['-NoProfile', '-EncodedCommand', encodeCommand(prepared.trampoline)],
        { windowsHide: true }
      );

      // On timeout the trampoline dies with the app's patience, but a running
      // elevated child (DISM) is left to finish on its own.
      const timer = setTimeout(() => {
        child.kill();
        finish({
          kind: 'unknown',
          exitCode: null,
          output: '',
          error: `提权进程超时未返回（${Math.round(timeoutMs / 1000)} 秒）`,
        });
      }, timeoutMs);

      child.once('error', (e) => {
        clearTimeout(timer);
        finish({ kind: 'unknown', exitCode: null, output: '', error: e.message });
      });
      child.once('close', (status) => {
        clearTimeout(timer);
        finish(
          collectElevatedResult(paths as ElevatorPaths, {
            status,
            cancelled: status === ELEVATION_CANCELLED,
          })
        );
      });
    } catch (e: any) {
      finish({ kind: 'unknown', exitCode: null, output: '', error: e?.message ?? String(e) });
    }
  });
}

/** Base64 UTF-16LE, the encoding PowerShell's -EncodedCommand expects. */
function encodeCommand(script: string): string {
  return Buffer.from(script, 'utf16le').toString('base64');
}

/** Elevated script: run an executable, capture both streams and the exit code. */
function commandCapture(
  cmd: { file: string; args?: string[] },
  outPath: string,
  errPath: string,
  codePath: string
): string {
  const args = (cmd.args ?? []).map((a) => `'${psEscape(a)}'`).join(',');
  // An empty @() is rejected by Start-Process ("argument collection contains a
  // null value"), so the parameter is omitted entirely when there are no args.
  const argList = args ? ` -ArgumentList @(${args})` : '';
  return [
    "$ErrorActionPreference = 'Stop'",
    'try {',
    `  $p = Start-Process -FilePath '${psEscape(cmd.file)}'${argList} ` +
      `-RedirectStandardOutput '${psEscape(outPath)}' ` +
      `-RedirectStandardError '${psEscape(errPath)}' -NoNewWindow -Wait -PassThru -ErrorAction Stop`,
    `  Set-Content -LiteralPath '${psEscape(codePath)}' -Value $p.ExitCode -Encoding ASCII`,
    '  exit $p.ExitCode',
    '} catch {',
    `  $_ | Out-String | Set-Content -LiteralPath '${psEscape(errPath)}' -Encoding UTF8`,
    `  Set-Content -LiteralPath '${psEscape(codePath)}' -Value 99 -Encoding ASCII`,
    '  exit 99',
    '}',
  ].join('\r\n');
}

/**
 * Elevated script: run a PowerShell body and capture everything it writes.
 * `*>&1` merges the error stream into the captured text, so a cmdlet failure
 * that PowerShell does not turn into a non-zero exit code still reaches the
 * user instead of collapsing into a generic message.
 */
function powershellCapture(
  body: string,
  outPath: string,
  errPath: string,
  codePath: string
): string {
  return [
    "$ErrorActionPreference = 'Continue'",
    `$log = '${psEscape(outPath)}'`,
    `$err = '${psEscape(errPath)}'`,
    '$ec = 0',
    'try {',
    `  & {\n${body}\n  } *>&1 | Out-String | Set-Content -LiteralPath $log -Encoding UTF8`,
    '} catch {',
    '  $_ | Out-String | Set-Content -LiteralPath $err -Encoding UTF8',
    '  $ec = 1',
    '}',
    'if ($LASTEXITCODE -is [int]) { $ec = $LASTEXITCODE }',
    `Set-Content -LiteralPath '${psEscape(codePath)}' -Value $ec -Encoding ASCII`,
    'exit $ec',
  ].join('\r\n');
}

function psEscape(value: string): string {
  return value.replace(/'/g, "''");
}

function readFileOrNull(path: string): Buffer | null {
  try {
    return readFileSync(path);
  } catch {
    return null;
  }
}

function readTextOrNull(path: string): string | null {
  const buf = readFileOrNull(path);
  return buf ? decodeWslOutput(buf) : null;
}

function readExitCode(path: string): number | null {
  const text = readTextOrNull(path);
  if (text === null) return null;
  const code = parseInt(text.trim(), 10);
  return Number.isNaN(code) ? null : code;
}

export type KernelInstallOutcome =
  { status: 'installed' } | { status: 'cancelled' } | { status: 'failed'; detail: string };

/**
 * Decide the kernel-install result from the elevated run plus the post-install
 * system probe.  System state wins over the exit code: `wsl --install` can
 * exit 0 while the Appx registration still lags behind the probe, and vice
 * versa.  Only when both disagree does the exit code/output get surfaced.
 */
export function classifyKernelInstall(
  r: ElevatedRunResult,
  kernelPresent: boolean
): KernelInstallOutcome {
  if (kernelPresent) return { status: 'installed' };
  if (r.kind === 'cancelled') return { status: 'cancelled' };
  if (r.kind === 'ok') return { status: 'installed' };
  return { status: 'failed', detail: summarizeElevated(r) };
}

export function classifyWslFeatureState(opts: {
  isWindows: boolean;
  featureWsl: boolean;
  featureVmp: boolean;
  /** Whether the feature read succeeded; false values are meaningless otherwise. */
  featureReadOk: boolean;
  /** `wsl --status` succeeded. */
  wslInstalled: boolean;
  /** Distros that can actually run bash (docker-desktop filtered out). */
  usableDistros: string[];
  /** Some usable distro has a non-root user (first-run setup done). */
  initialized: boolean;
}): WslFeatureState {
  if (!opts.isWindows) return 'not-supported';
  if (opts.wslInstalled) {
    return opts.usableDistros.length === 0 || !opts.initialized
      ? 'installed-but-not-initialized'
      : 'ready';
  }
  // Unreadable feature state must not be classified as not-enabled: on a
  // machine where the features are actually on but the kernel is missing,
  // that would loop the enable-features step forever.  The kernel install
  // step repairs both cases.
  if (!opts.featureReadOk) return 'not-installed';
  return opts.featureWsl || opts.featureVmp ? 'not-installed' : 'not-enabled';
}

// ---------------------------------------------------------------------------
// Stuck-servicing repair (live failure, 2026-09)
//
// Some OEM images leave `IsOOBEInProgress=1` behind in the Windows Update
// state.  Windows then still believes setup is running and aborts every
// startup servicing pass — CBS.log shows "Startup: Deferring startup
// processing at users request" plus "Reboot mark set" on each boot — so a
// queued `Enable-WindowsOptionalFeature VirtualMachinePlatform` is never
// applied.  Without that payload there is no Hyper-V host compute service
// (`vmcompute`), WSL2 reports that virtualization is not enabled, no distro
// can be registered, and rebooting changes nothing.  Clearing the stale
// markers is the only way out of that loop; the app being installed and
// running proves OOBE finished long ago.
// ---------------------------------------------------------------------------

export const WU_AUTO_UPDATE_KEY =
  'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update';

/** Markers a finished setup should not carry: they gate all servicing. */
export const STALE_OOBE_VALUES = ['IsOOBEInProgress', 'AcceleratedInstallRequired'] as const;

const READ_STALE_OOBE_CMD = [
  `$key = '${WU_AUTO_UPDATE_KEY}'`,
  `foreach ($name in ${STALE_OOBE_VALUES.map((n) => `'${n}'`).join(', ')}) {`,
  '  $value = (Get-ItemProperty -LiteralPath $key -Name $name -ErrorAction SilentlyContinue).$name',
  '  if ($null -ne $value) { "$name=$value" }',
  '}',
].join('\r\n');

/** Parse `name=value` lines; names the registry does not carry stay absent. */
export function parseStaleOobeFlags(stdout: string): Record<string, number> {
  const flags: Record<string, number> = {};
  for (const line of stdout.split(/\r?\n/)) {
    const m = line.trim().match(/^(\w+)=(\d+)$/);
    if (m) flags[m[1]] = parseInt(m[2], 10);
  }
  return flags;
}

export interface StaleOobeState {
  /** False when the registry read itself failed; the rest is meaningless then. */
  ok: boolean;
  /** A marker is set, i.e. Windows is deferring every pending servicing pass. */
  stale: boolean;
  flags: Record<string, number>;
}

export function readStaleOobeState(timeoutMs = 8000): StaleOobeState {
  try {
    const r = spawnSync('powershell.exe', ['-NoProfile', '-Command', READ_STALE_OOBE_CMD], {
      timeout: timeoutMs,
      encoding: 'utf8',
      windowsHide: true,
    });
    if (r.status !== 0) return { ok: false, stale: false, flags: {} };
    const flags = parseStaleOobeFlags(r.stdout ?? '');
    return { ok: true, stale: Object.values(flags).some((v) => v === 1), flags };
  } catch {
    return { ok: false, stale: false, flags: {} };
  }
}

/**
 * Elevated repair for the state above: drop the stale markers, then re-submit
 * both optional features so the next boot applies them.
 */
export function buildPlatformRepairScript(): string {
  const clear = STALE_OOBE_VALUES.map(
    (name) => `Remove-ItemProperty -LiteralPath $key -Name ${name} -ErrorAction SilentlyContinue`
  );
  // Report what the registry actually says once the script is done: the caller
  // treats a still-set marker as a failed repair, so this line is its evidence.
  const report = [
    `foreach ($name in ${STALE_OOBE_VALUES.map((n) => `'${n}'`).join(', ')}) {`,
    '  $value = (Get-ItemProperty -LiteralPath $key -Name $name -ErrorAction SilentlyContinue).$name',
    '  if ($null -eq $value) { "marker-cleared: $name" } else { "marker-still-set: $name=$value" }',
    '}',
  ].join('\r\n');
  return [
    // 'Stop' on purpose: a non-terminating failure of
    // Enable-WindowsOptionalFeature would leave the exit code at 0, and the
    // caller only verifies the markers afterwards — it would report a repair
    // that never happened and ask for a reboot that changes nothing.  The
    // best-effort steps below each carry an explicit -ErrorAction.
    "$ErrorActionPreference = 'Stop'",
    `$key = '${WU_AUTO_UPDATE_KEY}'`,
    // Unattended Windows Update can write the markers back the moment it runs,
    // so pause it around the edit.  Both services are demand-started anyway and
    // come back on their own.
    "foreach ($svc in 'wuauserv', 'UsoSvc') { Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue }",
    ...clear,
    'Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart',
    'Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart',
    // Clearing again after the feature work: that is the state the next boot
    // reads, and the markers have just been observed to come back while it runs.
    ...clear,
    report,
    "foreach ($svc in 'wuauserv', 'UsoSvc') { Start-Service -Name $svc -ErrorAction SilentlyContinue }",
  ].join('\r\n');
}

// WSL reports why WSL2 cannot start in free text (localized).  Require both a
// subject and a symptom on the same line: the healthy output also mentions
// `enablevirtualization`, but only inside its help URL.
const VIRTUALIZATION_SUBJECT = /虚拟化|virtualization/i;
const PLATFORM_PROBLEM_SYMPTOM = /无法启动|cannot start|未启用|not enabled/i;

/**
 * The `wsl --status` line saying WSL2 cannot start, or null when the platform
 * looks usable.  Non-null means installing a distro cannot work yet, whatever
 * its exit code says.
 */
export function findPlatformProblem(statusText: string): string | null {
  for (const raw of statusText.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    if (VIRTUALIZATION_SUBJECT.test(line) && PLATFORM_PROBLEM_SYMPTOM.test(line)) return line;
  }
  return null;
}
