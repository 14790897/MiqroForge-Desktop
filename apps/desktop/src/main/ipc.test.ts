import { describe, expect, it, vi, beforeEach } from 'vitest';
import { spawnSync } from 'child_process';
import {
  classifyWslFeatureState,
  hasNonRootUser,
  isBashCapableDistro,
  readFeatureStates,
  wslPackageInstalled,
  wslStatusWorks,
} from './ipc/wsl-state';

// The helpers under test spawn system commands; mock child_process.
vi.mock('child_process', () => ({
  spawnSync: vi.fn(),
}));

const mockedSpawnSync = vi.mocked(spawnSync);

function mockSpawn(result: Partial<ReturnType<typeof spawnSync>>) {
  mockedSpawnSync.mockReturnValue({
    status: 0,
    stdout: '',
    stderr: '',
    signal: null,
    error: undefined,
    pid: 1,
    output: [],
    ...result,
  } as unknown as ReturnType<typeof spawnSync>);
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
