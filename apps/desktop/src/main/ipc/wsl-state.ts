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
import { spawnSync } from 'child_process';
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
export function runElevated(payload: ElevatedPayload, timeoutMs = 300000): ElevatedRunResult {
  let dir: string | null = null;
  try {
    dir = mkdtempSync(join(tmpdir(), 'miqi-elev-'));
    const outPath = join(dir, 'out.txt');
    const errPath = join(dir, 'err.txt');
    const codePath = join(dir, 'exit.txt');
    const trampolinePath = join(dir, 'trampoline.txt');

    const elevated = payload.powershell
      ? powershellCapture(payload.powershell, outPath, errPath, codePath)
      : commandCapture(payload.command ?? { file: '' }, outPath, errPath, codePath);

    const trampoline =
      "$ErrorActionPreference='Stop'; " +
      `try { Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','${encodeCommand(elevated)}') ` +
      '-Verb RunAs -Wait -ErrorAction Stop } ' +
      'catch { ' +
      `if ($_.Exception.NativeErrorCode -eq ${ELEVATION_CANCELLED}) { exit ${ELEVATION_CANCELLED} } ` +
      `Set-Content -LiteralPath '${psEscape(trampolinePath)}' -Value $_.Exception.Message -Encoding UTF8; ` +
      'exit 99 }';

    const r = spawnSync(
      'powershell.exe',
      ['-NoProfile', '-EncodedCommand', encodeCommand(trampoline)],
      { timeout: timeoutMs, encoding: 'buffer', windowsHide: true }
    );

    if (r.error) return { kind: 'unknown', exitCode: null, output: '', error: r.error.message };
    if (r.status === ELEVATION_CANCELLED) return { kind: 'cancelled', exitCode: null, output: '' };

    const stdout = decodeWslOutput(readFileOrNull(outPath));
    const stderr = decodeWslOutput(readFileOrNull(errPath));
    const output = [stdout, stderr].filter((s) => s.length > 0).join('\n');
    const exitCode = readExitCode(codePath);
    if (exitCode === null) {
      // The elevated process never wrote its exit code: the trampoline failed
      // (no UAC prompt was shown, or the elevated process was killed early).
      const detail =
        readTextOrNull(trampolinePath) ||
        decodeWslOutput(r.stderr as Buffer | null) ||
        `提权进程未返回结果（powershell 退出码 ${r.status}）`;
      return { kind: 'unknown', exitCode: null, output, error: detail };
    }
    return exitCode === 0 ? { kind: 'ok', exitCode, output } : { kind: 'failed', exitCode, output };
  } catch (e: any) {
    return { kind: 'unknown', exitCode: null, output: '', error: e?.message ?? String(e) };
  } finally {
    if (dir) {
      try {
        rmSync(dir, { recursive: true, force: true });
      } catch {
        /* best-effort */
      }
    }
  }
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
