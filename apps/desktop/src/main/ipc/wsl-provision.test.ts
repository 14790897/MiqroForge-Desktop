/**
 * Handler-level integration tests for the WSL one-click install (#1180).
 *
 * The flow is driven through its dependency seam: every probe, elevated run and
 * state write is a recorded fake.  What is asserted here is the part no unit
 * test of a single decision can reach — the *ordering* (nothing runs after the
 * first failure, no distro is installed onto a platform WSL says cannot start,
 * no reboot is requested when the machine recovered on its own) and the reboot
 * hand-off (every branch that asks for one persists its phase; Done clears it).
 */
import { describe, expect, it, vi } from 'vitest';
import { installPhaseIsObsolete, provisionWsl, type ProvisionWslDeps } from './wsl-provision';
import type { ElevatedRunResult, FeatureStates, StaleOobeState } from './wsl-state';
import type { WslCheckResult, WslInstallProgress } from '../../shared/ipc';

// ── Fixtures ────────────────────────────────────────────────────────

/** What WSL itself prints on a machine whose virtualization platform is down. */
const PLATFORM_ISSUE = 'WSL2 无法启动，因为此计算机上未启用虚拟化。';

const ok = (output = ''): ElevatedRunResult => ({ kind: 'ok', exitCode: 0, output });
const failed = (output: string, exitCode = 1): ElevatedRunResult => ({
  kind: 'failed',
  exitCode,
  output,
});
const cancelled = (): ElevatedRunResult => ({ kind: 'cancelled', exitCode: null, output: '' });
/** A run that never reported back — the timeout / lost-trampoline case. */
const lost = (error = '提权进程超时未返回（1200 秒）'): ElevatedRunResult => ({
  kind: 'unknown',
  exitCode: null,
  output: '',
  error,
});

function machine(over: Partial<WslCheckResult> = {}): WslCheckResult {
  return {
    isWindows: true,
    installed: true,
    version: '2',
    distros: [],
    defaultDistro: null,
    running: false,
    featureState: 'installed-but-not-initialized',
    rebootRequired: false,
    platformIssue: null,
    ...over,
  };
}

/** WSL is installed and usable, but no distro is registered yet. */
const distroMissing = () => machine();

/** WSL itself reports the platform cannot start — the #1171 machine. */
const brokenPlatform = () => machine({ platformIssue: PLATFORM_ISSUE });

/** A distro finally answers. */
const distroReady = () =>
  machine({ distros: ['Ubuntu'], defaultDistro: 'Ubuntu', featureState: 'ready', running: true });

const staleMarked = (): StaleOobeState => ({
  ok: true,
  stale: true,
  flags: { IsOOBEInProgress: 1 },
});
const staleCleared = (): StaleOobeState => ({ ok: true, stale: false, flags: {} });
const staleUnreadable = (): StaleOobeState => ({ ok: false, stale: false, flags: {} });

interface HarnessOpts {
  /** Probe results, consumed in order; the last one repeats. */
  probes?: WslCheckResult[];
  /** Stale-marker reads, consumed in order; the last one repeats. */
  stale?: StaleOobeState[];
  features?: FeatureStates;
  enabled?: ElevatedRunResult;
  kernel?: ElevatedRunResult;
  kernelPresent?: boolean;
  repair?: ElevatedRunResult;
  distro?: ElevatedRunResult;
  /** Replaces one dependency outright, to make it throw. */
  override?: Partial<ProvisionWslDeps>;
}

/**
 * One flow run over recorded fakes.  `probes`/`stale` model the machine's state
 * *at each point in time* — which is what the flow re-derives after every step
 * instead of trusting a cached read.
 */
async function run(opts: HarnessOpts = {}) {
  const calls: string[] = [];
  const emitted: WslInstallProgress[] = [];
  const written: string[] = [];
  const probes = [...(opts.probes ?? [])];
  const stales = [...(opts.stale ?? [])];
  let lastProbe = distroMissing();
  let lastStale = staleCleared();

  const deps: ProvisionWslDeps = {
    probe: () => {
      calls.push('probe');
      if (probes.length > 0) lastProbe = probes.shift()!;
      return lastProbe;
    },
    enableFeatures: async () => {
      calls.push('enableFeatures');
      return opts.enabled ?? ok();
    },
    readFeatures: () => {
      calls.push('readFeatures');
      return opts.features ?? { ok: true, featureWsl: true, featureVmp: false };
    },
    installKernel: async () => {
      calls.push('installKernel');
      return opts.kernel ?? ok();
    },
    installDistro: async () => {
      calls.push('installDistro');
      return opts.distro ?? ok();
    },
    kernelPresent: () => {
      calls.push('kernelPresent');
      return opts.kernelPresent ?? false;
    },
    repairPlatform: async () => {
      calls.push('repairPlatform');
      return opts.repair ?? ok();
    },
    readStale: () => {
      calls.push('readStale');
      if (stales.length > 0) lastStale = stales.shift()!;
      return lastStale;
    },
    emit: (p) => {
      emitted.push(p);
    },
    // Writes and clears join the call log too, so an exact-sequence assertion
    // pins down *when* the phase was persisted, not just that it was.
    state: {
      write: (phase) => {
        calls.push(`state.write:${phase}`);
        written.push(phase);
      },
      clear: vi.fn(() => {
        calls.push('state.clear');
      }),
    },
    ...opts.override,
  };

  const result = await provisionWsl(deps);
  const phases = emitted.map((e) => e.phase);
  return { result, calls, emitted, phases, written };
}

// ── Step 3.5 — the platform repair ──────────────────────────────────

describe('provisionWsl — 平台故障修复 (Step 3.5)', () => {
  it('repairs the platform and installs the distro in the same round', async () => {
    // The #1171 behaviour: DISM applied the queued payload outright, so the
    // machine became usable without a reboot and the flow continued on the spot.
    const { result, calls, emitted, phases } = await run({
      probes: [
        brokenPlatform(),
        machine({ platformIssue: null }), // platform came back
        distroReady(), // distro registered
      ],
      stale: [staleMarked(), staleCleared()],
      repair: ok('marker-cleared: IsOOBEInProgress'),
      distro: ok(),
    });

    expect(result).toEqual({ success: true, phase: 'complete' });
    expect(calls).toEqual([
      'probe',
      'readStale',
      'repairPlatform',
      'probe',
      'readStale',
      'installDistro',
      'probe',
      'state.clear',
    ]);
    expect(phases).toEqual([
      'checking',
      'enabling_features', // 正在修复
      'enabling_features', // 继续安装发行版
      'installing_distro',
      'complete',
    ]);
    // No reboot is requested when the machine recovered on its own.
    expect(emitted.some((e) => e.rebootRequired)).toBe(false);
  });

  it('stops with PLATFORM_REPAIR_FAILED when the platform is still down afterwards', async () => {
    const { result, calls } = await run({
      probes: [brokenPlatform()],
      stale: [staleMarked(), staleMarked()], // the markers survived the repair
      repair: ok('marker-still-set: IsOOBEInProgress=1'),
    });

    expect(result.errorCode).toBe('PLATFORM_REPAIR_FAILED');
    expect(result.success).toBe(false);
    // The point of the branch: a distro installed here could not register.
    expect(calls).not.toContain('installDistro');
    expect(calls).not.toContain('state.write:platform_repair_pending');
  });

  it('reports a failed elevated repair as PLATFORM_REPAIR_FAILED, with its output', async () => {
    const { result } = await run({
      probes: [brokenPlatform()],
      stale: [staleMarked()],
      repair: failed('拒绝访问: DISM 需要管理员权限', 5),
    });

    expect(result.errorCode).toBe('PLATFORM_REPAIR_FAILED');
    expect(result.error).toContain('退出码 5');
    expect(result.error).toContain('拒绝访问');
  });

  it('stops with ELEVATION_CANCELLED on a declined UAC prompt, before any re-probe', async () => {
    const { result, calls, emitted } = await run({
      probes: [brokenPlatform()],
      stale: [staleMarked()],
      repair: cancelled(),
    });

    expect(result.errorCode).toBe('ELEVATION_CANCELLED');
    expect(calls).toEqual(['probe', 'readStale', 'repairPlatform']);
    expect(emitted.at(-1)).toMatchObject({ phase: 'error', error: 'ELEVATION_CANCELLED' });
  });

  it.each([
    ['no stale marker is set', staleCleared()],
    ['the marker read fails', staleUnreadable()],
  ])('reports PLATFORM_NOT_READY without elevating when %s', async (_label, staleState) => {
    // Without the stale marker a reboot fixes nothing, and asking for one would
    // loop forever — so this must not reach the elevated repair at all.
    const { result, calls } = await run({
      probes: [brokenPlatform()],
      stale: [staleState],
    });

    expect(result).toMatchObject({
      success: false,
      phase: 'error',
      errorCode: 'PLATFORM_NOT_READY',
      error: PLATFORM_ISSUE,
    });
    expect(calls).toEqual(['probe', 'readStale']);
  });

  it('asks for a reboot when the repair re-submitted the payload but the platform is still down', async () => {
    const { result, calls, written, emitted } = await run({
      probes: [brokenPlatform()], // still down right after the repair
      stale: [staleMarked(), staleCleared()], // but the markers are gone
      repair: ok(),
    });

    expect(result).toMatchObject({
      success: true,
      phase: 'enabling_features',
      rebootRequired: true,
    });
    expect(written).toEqual(['platform_repair_pending']);
    expect(calls).not.toContain('installDistro');
    expect(emitted.at(-1)?.rebootRequired).toBe(true);
  });
});

// ── Step 2 — enabling the optional features ─────────────────────────

describe('provisionWsl — 启用可选功能 (Step 2)', () => {
  const featuresOff = () => machine({ installed: false, featureState: 'not-enabled' });

  it('enables the features, persists the phase and asks for a reboot', async () => {
    const { result, calls, written, phases } = await run({
      probes: [featuresOff()],
      enabled: ok('Enable-WindowsOptionalFeature 完成'),
      // VirtualMachinePlatform is still reported Disabled — on VBS machines it
      // stays that way while being functional, so it must not fail the step.
      features: { ok: true, featureWsl: true, featureVmp: false },
    });

    expect(result).toMatchObject({
      success: true,
      phase: 'enabling_features',
      rebootRequired: true,
    });
    expect(written).toEqual(['features_enabled']);
    expect(phases).toEqual(['checking', 'enabling_features', 'enabling_features']);
    expect(calls).toEqual([
      'probe',
      'enableFeatures',
      'readFeatures',
      'state.write:features_enabled',
    ]);
  });

  it('stops with ELEVATION_CANCELLED on a declined UAC prompt', async () => {
    const { result, calls } = await run({ probes: [featuresOff()], enabled: cancelled() });

    expect(result.errorCode).toBe('ELEVATION_CANCELLED');
    expect(calls).toEqual(['probe', 'enableFeatures']);
  });

  it.each([
    ['the WSL feature is still off', { ok: true, featureWsl: false, featureVmp: false }],
    ['the feature state cannot be read', { ok: false, featureWsl: false, featureVmp: false }],
  ])('reports FEATURE_ENABLE_FAILED when %s', async (_label, features) => {
    const { result, calls, emitted } = await run({
      probes: [featuresOff()],
      // A failed cmdlet leaves the exit code at 0 — the output is the evidence.
      enabled: { kind: 'ok', exitCode: 0, output: '拒绝访问: 需要管理员权限' },
      features: features as FeatureStates,
    });

    expect(result.errorCode).toBe('FEATURE_ENABLE_FAILED');
    expect(emitted.at(-1)?.message).toContain('拒绝访问');
    expect(calls).toEqual(['probe', 'enableFeatures', 'readFeatures']);
  });
});

// ── Step 3 — the kernel install ─────────────────────────────────────

describe('provisionWsl — 内核安装 (Step 3)', () => {
  const kernelMissing = () => machine({ installed: false, featureState: 'not-installed' });

  it('persists kernel_installed and asks for a reboot without touching a distro', async () => {
    const { result, calls, written } = await run({
      probes: [kernelMissing()],
      kernel: ok(),
    });

    expect(result).toMatchObject({ success: true, phase: 'installing_wsl', rebootRequired: true });
    expect(written).toEqual(['kernel_installed']);
    // Exit code 0 already proves the install — no system probe is needed.
    expect(calls).toEqual(['probe', 'installKernel', 'state.write:kernel_installed']);
    expect(calls).not.toContain('installDistro');
  });

  it('stops with ELEVATION_CANCELLED on a declined UAC prompt', async () => {
    const { result, calls } = await run({ probes: [kernelMissing()], kernel: cancelled() });

    expect(result.errorCode).toBe('ELEVATION_CANCELLED');
    expect(calls).toEqual(['probe', 'installKernel']);
    expect(calls).not.toContain('installDistro');
  });

  it('reports KERNEL_INSTALL_FAILED when the run failed and no package registered', async () => {
    const { result, calls } = await run({
      probes: [kernelMissing()],
      kernel: failed('WSL 内核更新失败', 1),
      kernelPresent: false,
    });

    expect(result.errorCode).toBe('KERNEL_INSTALL_FAILED');
    expect(result.error).toContain('退出码 1');
    expect(calls).toEqual(['probe', 'installKernel', 'kernelPresent']);
    expect(calls).not.toContain('installDistro');
  });

  it('trusts a registered package over a non-zero exit code', async () => {
    // The Appx registration lags the elevated process; the machine wins.
    const { result, calls } = await run({
      probes: [kernelMissing()],
      kernel: failed('内核更新失败', 1),
      kernelPresent: true,
    });

    expect(result).toMatchObject({ success: true, phase: 'installing_wsl', rebootRequired: true });
    expect(calls).not.toContain('installDistro');
  });
});

// ── Step 4 — the distro install ─────────────────────────────────────

describe('provisionWsl — 发行版安装 (Step 4)', () => {
  it('persists distro_installed and asks for a reboot when the distro has not registered yet', async () => {
    const { result, calls, written } = await run({ probes: [distroMissing()], distro: ok() });

    expect(result).toMatchObject({
      success: true,
      phase: 'installing_distro',
      rebootRequired: true,
    });
    expect(written).toEqual(['distro_installed']);
    expect(calls).toEqual(['probe', 'installDistro', 'probe', 'state.write:distro_installed']);
  });

  it('stops with ELEVATION_CANCELLED on a declined UAC prompt', async () => {
    const { result, calls, written } = await run({
      probes: [distroMissing()],
      distro: cancelled(),
    });

    expect(result.errorCode).toBe('ELEVATION_CANCELLED');
    expect(calls).toEqual(['probe', 'installDistro']);
    expect(written).toEqual([]);
  });

  it('reports a timed-out install as unconfirmed, with a refresh hint', async () => {
    // The install was observed to finish in the background long after the app
    // stopped waiting, so this outcome must not be presented as a failure.
    const { result, calls, emitted } = await run({
      probes: [distroMissing()],
      distro: lost(),
    });

    expect(result.success).toBe(false);
    expect(result.errorCode).toBe('DISTRO_INSTALL_UNCONFIRMED');
    expect(result.error).toContain('无法确认');
    expect(result.error).toContain('超时');
    expect(result.nextStep).toContain('刷新');
    expect(emitted.at(-1)?.message).toContain('无法确认');
    expect(calls).toEqual(['probe', 'installDistro', 'probe']);
  });

  it('still reports a real install failure as failed', async () => {
    const { result, emitted } = await run({
      probes: [distroMissing()],
      distro: failed('WslRegisterDistribution failed with error: 0x80370102', 1),
    });

    expect(result.errorCode).toBe('DISTRO_INSTALL_FAILED');
    expect(result.error).toContain('0x80370102');
    expect(emitted.at(-1)?.message).toContain('Ubuntu 安装失败');
  });

  it('reports PLATFORM_NOT_READY instead of promising a reboot when WSL says the platform is down', async () => {
    const { result } = await run({
      probes: [distroMissing(), brokenPlatform()],
      distro: ok(),
    });

    expect(result).toMatchObject({
      success: false,
      phase: 'error',
      errorCode: 'PLATFORM_NOT_READY',
      error: PLATFORM_ISSUE,
    });
    expect(result.rebootRequired).toBeUndefined();
  });

  it('finishes once the distro registers', async () => {
    const { result, calls, phases } = await run({
      probes: [distroMissing(), distroReady()],
      distro: ok(),
    });

    expect(result).toEqual({ success: true, phase: 'complete' });
    expect(phases).toEqual(['checking', 'installing_distro', 'complete']);
    expect(calls).toEqual(['probe', 'installDistro', 'probe', 'state.clear']);
  });
});

// ── The reboot hand-off and the Done branch ─────────────────────────

describe('provisionWsl — 重启交接与完成', () => {
  it.each([
    [
      'Step 2 启用可选功能',
      {
        probes: [machine({ installed: false, featureState: 'not-enabled' })],
        enabled: ok(),
        features: { ok: true, featureWsl: true, featureVmp: false } as FeatureStates,
      },
      'features_enabled',
    ],
    [
      'Step 3 安装内核',
      { probes: [machine({ installed: false, featureState: 'not-installed' })], kernel: ok() },
      'kernel_installed',
    ],
    [
      'Step 3.5 修复平台',
      {
        probes: [brokenPlatform()],
        stale: [staleMarked(), staleCleared()],
        repair: ok(),
      },
      'platform_repair_pending',
    ],
    ['Step 4 安装发行版', { probes: [distroMissing()], distro: ok() }, 'distro_installed'],
  ])('%s 请求重启时写入 phase，且不清理', async (_label, opts, phase) => {
    const { result, calls, written } = await run(opts as HarnessOpts);

    expect(result.rebootRequired).toBe(true);
    expect(written).toEqual([phase]);
    // Clearing here would lose the resume the reboot copy promises.
    expect(calls).not.toContain('state.clear');
  });

  it('clears the persisted phase once the flow reaches Done', async () => {
    const { result, calls, written } = await run({ probes: [distroReady()] });

    expect(result).toEqual({ success: true, phase: 'complete' });
    // An already-ready machine installs nothing and elevates nothing.
    expect(calls).toEqual(['probe', 'state.clear']);
    expect(written).toEqual([]);
  });

  it('reports NOT_WINDOWS without touching anything', async () => {
    const { result, calls } = await run({
      probes: [machine({ isWindows: false, installed: false, featureState: 'not-supported' })],
    });

    expect(result.errorCode).toBe('NOT_WINDOWS');
    expect(calls).toEqual(['probe']);
  });

  it('resolves an UNKNOWN result when a dependency throws, instead of rejecting', async () => {
    const { result, emitted } = await run({
      probes: [distroMissing()],
      override: {
        installDistro: async () => {
          throw new Error('spawn wsl.exe ENOENT');
        },
      },
    });

    expect(result).toMatchObject({ success: false, phase: 'error', errorCode: 'UNKNOWN' });
    expect(result.error).toBe('spawn wsl.exe ENOENT');
    expect(emitted.at(-1)?.message).toContain('spawn wsl.exe ENOENT');
  });
});

// ── The lifecycle of a persisted phase ──────────────────────────────

describe('provisionWsl — 持久化 phase 的生命周期', () => {
  it('treats a persisted phase as obsolete once a usable distro answers', () => {
    // Every reboot branch can only be reached while the distro list is empty,
    // so a phase surviving next to a live distro was left behind by a run that
    // never reached Done — and the page's auto-resume gates on exactly this
    // list being empty, which is what would make it fire again.
    expect(installPhaseIsObsolete(distroReady())).toBe(true);
    expect(installPhaseIsObsolete(distroMissing())).toBe(false);
    // A broken platform is the opposite case: the machine still needs help.
    expect(installPhaseIsObsolete(brokenPlatform())).toBe(false);
  });
});
