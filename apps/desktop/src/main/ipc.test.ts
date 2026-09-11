import { describe, expect, it, vi, beforeEach } from 'vitest';
import { spawnSync } from 'child_process';
import { readFileSync, writeFileSync } from 'fs';
import {
  classifyKernelInstall,
  classifyWslFeatureState,
  decodeWslOutput,
  ELEVATION_CANCELLED,
  hasNonRootUser,
  isBashCapableDistro,
  readFeatureStates,
  runElevated,
  summarizeElevated,
  wslKernelPresent,
  wslPackageInstalled,
  wslStatusWorks,
} from './ipc/wsl-state';

// The helpers under test spawn system commands; mock child_process.
vi.mock('child_process', () => ({
  spawnSync: vi.fn(),
}));

// ...and touch the filesystem only through the trampoline temp dir.
vi.mock('fs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('fs')>();
  return {
    ...actual,
    mkdtempSync: vi.fn(() => 'C:\\Temp\\miqi-elev-test'),
    writeFileSync: vi.fn(),
    readFileSync: vi.fn(),
    rmSync: vi.fn(),
  };
});

const mockedSpawnSync = vi.mocked(spawnSync);
const mockedReadFileSync = vi.mocked(readFileSync);
const mockedWriteFileSync = vi.mocked(writeFileSync);

function spawnResult(result: Partial<ReturnType<typeof spawnSync>>) {
  return {
    status: 0,
    stdout: '',
    stderr: '',
    signal: null,
    error: undefined,
    pid: 1,
    output: [],
    ...result,
  } as unknown as ReturnType<typeof spawnSync>;
}

function mockSpawn(result: Partial<ReturnType<typeof spawnSync>>) {
  mockedSpawnSync.mockReturnValue(spawnResult(result));
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ── Feature state classification (runWslCheckInternal core) ─────────

describe('classifyWslFeatureState', () => {
  it('returns not-supported on non-Windows', () => {
    expect(
      classifyWslFeatureState({
        isWindows: false,
        featureWsl: false,
        featureVmp: false,
        featureReadOk: true,
        wslInstalled: false,
        usableDistros: [],
        initialized: false,
      })
    ).toBe('not-supported');
  });

  it('returns not-enabled when both features are off and WSL is absent', () => {
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: false,
        featureVmp: false,
        featureReadOk: true,
        wslInstalled: false,
        usableDistros: [],
        initialized: false,
      })
    ).toBe('not-enabled');
  });

  it('returns not-installed when features are on but WSL is absent', () => {
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: true,
        featureVmp: true,
        featureReadOk: true,
        wslInstalled: false,
        usableDistros: [],
        initialized: false,
      })
    ).toBe('not-installed');
  });

  it('returns not-installed when only one feature is on and WSL is absent', () => {
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: true,
        featureVmp: false,
        featureReadOk: true,
        wslInstalled: false,
        usableDistros: [],
        initialized: false,
      })
    ).toBe('not-installed');
  });

  it('returns not-installed (not not-enabled) when the feature read failed', () => {
    // Unreadable feature state must not be classified as not-enabled:
    // enabling features would loop forever on a machine where the
    // features are actually on but the kernel is missing.
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: false,
        featureVmp: false,
        featureReadOk: false,
        wslInstalled: false,
        usableDistros: [],
        initialized: false,
      })
    ).toBe('not-installed');
  });

  it('returns installed-but-not-initialized when no usable distro exists', () => {
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: true,
        featureVmp: true,
        featureReadOk: true,
        wslInstalled: true,
        usableDistros: [],
        initialized: false,
      })
    ).toBe('installed-but-not-initialized');
  });

  it('returns installed-but-not-initialized when distro has no non-root user', () => {
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: true,
        featureVmp: true,
        featureReadOk: true,
        wslInstalled: true,
        usableDistros: ['Ubuntu'],
        initialized: false,
      })
    ).toBe('installed-but-not-initialized');
  });

  it('returns ready when a usable distro is initialized', () => {
    expect(
      classifyWslFeatureState({
        isWindows: true,
        featureWsl: true,
        featureVmp: true,
        featureReadOk: true,
        wslInstalled: true,
        usableDistros: ['Ubuntu'],
        initialized: true,
      })
    ).toBe('ready');
  });
});

// ── Feature state read (WMI, unelevated) ────────────────────────────

describe('readFeatureStates', () => {
  it('parses WMI InstallState output', () => {
    mockSpawn({
      status: 0,
      stdout: 'Microsoft-Windows-Subsystem-Linux=1\r\nVirtualMachinePlatform=2\r\n',
    });
    expect(readFeatureStates()).toEqual({
      ok: true,
      featureWsl: true,
      featureVmp: false,
    });
  });

  it('treats both features as disabled when WMI lists neither', () => {
    mockSpawn({ status: 0, stdout: 'SomeUnrelatedFeature=1\r\n' });
    expect(readFeatureStates()).toEqual({
      ok: true,
      featureWsl: false,
      featureVmp: false,
    });
  });

  it('returns ok:false on empty output (cannot verify)', () => {
    mockSpawn({ status: 0, stdout: '' });
    expect(readFeatureStates().ok).toBe(false);
  });

  it('returns ok:false when the WMI query fails', () => {
    mockSpawn({ status: 1, stdout: '' });
    expect(readFeatureStates()).toEqual({
      ok: false,
      featureWsl: false,
      featureVmp: false,
    });
  });
});

// ── Usable distro filtering ─────────────────────────────────────────

describe('isBashCapableDistro', () => {
  it('accepts a distro that can run bash', () => {
    mockSpawn({ status: 0 });
    expect(isBashCapableDistro('Ubuntu')).toBe(true);
  });

  it('rejects docker-desktop (no bash)', () => {
    mockSpawn({ status: 1 });
    expect(isBashCapableDistro('docker-desktop')).toBe(false);
  });

  it('rejects when the probe errors out', () => {
    mockedSpawnSync.mockImplementation(() => {
      throw new Error('spawn failed');
    });
    expect(isBashCapableDistro('Ubuntu')).toBe(false);
  });
});

describe('hasNonRootUser', () => {
  it('accepts a non-root uid', () => {
    mockSpawn({ status: 0, stdout: '1000' });
    expect(hasNonRootUser('Ubuntu')).toBe(true);
  });

  it('rejects root (uid 0)', () => {
    mockSpawn({ status: 0, stdout: '0' });
    expect(hasNonRootUser('Ubuntu')).toBe(false);
  });

  it('rejects empty output', () => {
    mockSpawn({ status: 0, stdout: '' });
    expect(hasNonRootUser('Ubuntu')).toBe(false);
  });

  it('rejects a failing probe', () => {
    mockSpawn({ status: 1, stdout: '' });
    expect(hasNonRootUser('Ubuntu')).toBe(false);
  });
});

// ── Kernel install post-checks ──────────────────────────────────────

describe('wslStatusWorks', () => {
  it('reflects the wsl --status exit code', () => {
    mockSpawn({ status: 0 });
    expect(wslStatusWorks()).toBe(true);
    mockSpawn({ status: 1 });
    expect(wslStatusWorks()).toBe(false);
  });
});

describe('wslPackageInstalled', () => {
  it('detects the WSL app package via Get-AppxPackage', () => {
    mockSpawn({ status: 0, stdout: 'MicrosoftCorporationII.WindowsSubsystemForLinux' });
    expect(wslPackageInstalled()).toBe(true);
  });

  it('reports absent when the query returns nothing', () => {
    mockSpawn({ status: 0, stdout: '' });
    expect(wslPackageInstalled()).toBe(false);
  });
});

describe('wslKernelPresent', () => {
  it('accepts the first probe when wsl --status works', () => {
    mockSpawn({ status: 0 });
    expect(wslKernelPresent(3, 0)).toBe(true);
    expect(mockedSpawnSync).toHaveBeenCalledTimes(1);
  });

  it('accepts the Appx package probe when wsl --status fails', () => {
    mockedSpawnSync
      .mockReturnValueOnce(spawnResult({ status: 1 }))
      .mockReturnValueOnce(spawnResult({ status: 0, stdout: 'MicrosoftCorporationII.WSL' }));
    expect(wslKernelPresent(3, 0)).toBe(true);
  });

  it('retries before giving up (registration lag)', () => {
    mockSpawn({ status: 1, stdout: '' });
    expect(wslKernelPresent(2, 0)).toBe(false);
    // Two probes per attempt.
    expect(mockedSpawnSync).toHaveBeenCalledTimes(4);
  });
});

// ── Elevated run trampoline ─────────────────────────────────────────

describe('decodeWslOutput', () => {
  it('decodes BOM-prefixed UTF-16LE output', () => {
    expect(decodeWslOutput(Buffer.from('﻿安装失败', 'utf16le'))).toBe('安装失败');
  });

  it('decodes UTF-16LE without a BOM (wsl.exe writes NUL bytes)', () => {
    expect(decodeWslOutput(Buffer.from('Invalid command line option', 'utf16le'))).toBe(
      'Invalid command line option'
    );
  });

  it('decodes UTF-16LE coded CJK without a BOM (no NUL bytes to detect)', () => {
    expect(decodeWslOutput(Buffer.from('系统找不到指定的文件。', 'utf16le'))).toBe(
      '系统找不到指定的文件。'
    );
  });

  it('decodes plain UTF-8 output', () => {
    expect(decodeWslOutput(Buffer.from('plain text'))).toBe('plain text');
  });

  it('treats empty input as empty', () => {
    expect(decodeWslOutput(null)).toBe('');
    expect(decodeWslOutput(Buffer.alloc(0))).toBe('');
  });
});

describe('summarizeElevated', () => {
  it('surfaces the trampoline error for unknown results', () => {
    expect(
      summarizeElevated({ kind: 'unknown', exitCode: null, output: '', error: 'spawn ENOENT' })
    ).toBe('spawn ENOENT');
  });

  it('combines exit code and output for failed results', () => {
    const summary = summarizeElevated({
      kind: 'failed',
      exitCode: 1,
      output: 'WSL 内核更新失败\n更多信息请访问 https://aka.ms/wsl2kernel',
    });
    expect(summary).toContain('退出码 1');
    expect(summary).toContain('WSL 内核更新失败');
  });

  it('returns 无输出 when nothing at all was captured', () => {
    expect(summarizeElevated({ kind: 'failed', exitCode: null, output: '' })).toBe('无输出');
  });
});

describe('classifyKernelInstall', () => {
  const failed = { kind: 'failed' as const, exitCode: 1, output: 'boom' };
  const ok = { kind: 'ok' as const, exitCode: 0, output: '' };
  const cancelled = { kind: 'cancelled' as const, exitCode: null, output: '' };

  it('trusts system state over a non-zero exit code', () => {
    expect(classifyKernelInstall(failed, true)).toEqual({ status: 'installed' });
  });

  it('treats exit code 0 as installed even when the probe lags', () => {
    expect(classifyKernelInstall(ok, false)).toEqual({ status: 'installed' });
  });

  it('reports a declined UAC prompt as cancelled', () => {
    expect(classifyKernelInstall(cancelled, false)).toEqual({ status: 'cancelled' });
  });

  it('reports the exit code and output when the install really failed', () => {
    expect(classifyKernelInstall(failed, false)).toEqual({
      status: 'failed',
      detail: expect.stringContaining('退出码 1'),
    });
  });

  it('reports the transport error when the trampoline itself failed', () => {
    expect(
      classifyKernelInstall({ kind: 'unknown', exitCode: null, output: '', error: 'EPERM' }, false)
    ).toEqual({ status: 'failed', detail: 'EPERM' });
  });
});

describe('runElevated', () => {
  function mockFiles(files: { out?: string | Buffer; exit?: string; trampoline?: string }) {
    mockedReadFileSync.mockImplementation(((p: string) => {
      const name = String(p);
      const out = files.out;
      if (name.endsWith('out.txt') && out !== undefined) {
        return Buffer.isBuffer(out) ? out : Buffer.from(out);
      }
      if (name.endsWith('exit.txt') && files.exit !== undefined) return Buffer.from(files.exit);
      if (name.endsWith('trampoline.txt') && files.trampoline !== undefined) {
        return Buffer.from(files.trampoline);
      }
      throw new Error(`ENOENT: ${name}`);
    }) as any);
  }

  it('reports ok when the elevated process exited 0', () => {
    mockFiles({ out: 'installed', exit: '0' });
    mockSpawn({ status: 0 });
    expect(runElevated({ command: 'wsl.exe --install' })).toEqual({
      kind: 'ok',
      exitCode: 0,
      output: 'installed',
    });
  });

  it('reports failed with the recovered exit code and output', () => {
    mockFiles({ out: Buffer.from('内核更新失败', 'utf16le'), exit: '1' });
    mockSpawn({ status: 0 });
    const r = runElevated({ command: 'wsl.exe --install' });
    expect(r.kind).toBe('failed');
    expect(r.exitCode).toBe(1);
    expect(r.output).toBe('内核更新失败');
    expect(summarizeElevated(r)).toContain('退出码 1');
  });

  it('reports cancelled when the trampoline exits with ERROR_CANCELLED', () => {
    mockFiles({});
    mockSpawn({ status: ELEVATION_CANCELLED });
    expect(runElevated({ command: 'wsl.exe --install' })).toEqual({
      kind: 'cancelled',
      exitCode: null,
      output: '',
    });
  });

  it('reports unknown with the trampoline message when no exit code was written', () => {
    mockFiles({ trampoline: 'This operation has been cancelled' });
    mockSpawn({ status: 99 });
    expect(runElevated({ command: 'wsl.exe --install' })).toEqual({
      kind: 'unknown',
      exitCode: null,
      output: '',
      error: 'This operation has been cancelled',
    });
  });

  it('reports unknown when powershell itself cannot be spawned', () => {
    mockFiles({});
    mockSpawn({ error: new Error('spawn powershell.exe ENOENT') as any, status: null });
    expect(runElevated({ command: 'wsl.exe --install' })).toMatchObject({
      kind: 'unknown',
      error: 'spawn powershell.exe ENOENT',
    });
  });

  it('writes the elevated command and its exit-code echo into run.cmd', () => {
    mockFiles({ exit: '0' });
    mockSpawn({ status: 0 });
    runElevated({ command: 'wsl.exe --install --no-distribution' });
    const [, content] = mockedWriteFileSync.mock.calls.find(([p]) =>
      String(p).endsWith('run.cmd')
    )!;
    expect(String(content)).toContain('wsl.exe --install --no-distribution');
    expect(String(content)).toContain('%~dp0out.txt');
    expect(String(content)).toContain('> "%~dp0exit.txt" echo %EC%');
  });

  it('writes a BOM-prefixed payload.ps1 in powershell mode', () => {
    mockFiles({ exit: '0' });
    mockSpawn({ status: 0 });
    runElevated({ powershell: 'Enable-WindowsOptionalFeature -Online' });
    const [, content] = mockedWriteFileSync.mock.calls.find(([p]) =>
      String(p).endsWith('payload.ps1')
    )!;
    expect(String(content).startsWith('﻿')).toBe(true);
    expect(String(content)).toContain('Enable-WindowsOptionalFeature');
  });
});
