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
 *   must never be relied on — verify the resulting system state instead.
 */
import { spawnSync } from 'child_process';
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
    const r = spawnSync(
      'powershell.exe',
      ['-NoProfile', '-Command', WMI_FEATURE_CMD],
      { timeout: timeoutMs, encoding: 'utf8', windowsHide: true }
    );
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
    const r = spawnSync(
      'wsl.exe',
      ['-d', distro, '--', 'bash', '-c', 'echo ok'],
      { timeout: timeoutMs, encoding: 'buffer', windowsHide: true }
    );
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

export function classifyWslFeatureState(opts: {
  isWindows: boolean;
  featureWsl: boolean;
  featureVmp: boolean;
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
  return opts.featureWsl || opts.featureVmp ? 'not-installed' : 'not-enabled';
}
