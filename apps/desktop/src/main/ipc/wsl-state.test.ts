/**
 * Unit tests for the WSL state helpers.  The text fixtures are verbatim
 * captures from real machines (a working one, and one whose optional-feature
 * install was stuck because Windows kept deferring its servicing passes).
 */
import { describe, expect, it } from 'vitest';
import {
  buildEnableFeaturesScript,
  buildPlatformRepairScript,
  classifyPlatformRepair,
  decodeWslOutput,
  findPlatformProblem,
  isStaleElevatorDir,
  parseStaleOobeFlags,
  STALE_OOBE_VALUES,
} from './wsl-state';
import type { ElevatedRunResult } from './wsl-state';

// `wsl --status` on a Chinese Windows whose virtualization platform never
// landed.  Only the "WSL2 无法启动" line may be reported as the problem.
const ZH_BROKEN_STATUS = [
  '默认版本: 2',
  '当前计算机配置不支持 WSL1。',
  '若要使用 WSL1，请启用“Windows Subsystem for Linux”可选组件。',
  'WSL2 无法启动，因为此计算机上未启用虚拟化。',
  '请确保计算机固件设置中“虚拟机平台”可选组件已启用，且虚拟化已开启。',
  '',
  '启用“虚拟机平台”通过运行: wsl.exe --install --no-distribution',
  '',
  '有关信息，请访问 https://aka.ms/enablevirtualization',
].join('\r\n');

const EN_BROKEN_STATUS = [
  'Default Version: 2',
  'WSL2 cannot start because virtualization is not enabled.',
  'For information, please visit https://aka.ms/enablevirtualization',
].join('\n');

const HEALTHY_STATUS = ['默认版本: 2', '内核版本: 6.6.87.2-1', 'WSLg 版本: 1.0.73.2'].join('\r\n');

describe('findPlatformProblem', () => {
  it('reports the WSL2 line when the virtualization platform is missing', () => {
    expect(findPlatformProblem(ZH_BROKEN_STATUS)).toBe(
      'WSL2 无法启动，因为此计算机上未启用虚拟化。'
    );
    expect(findPlatformProblem(EN_BROKEN_STATUS)).toBe(
      'WSL2 cannot start because virtualization is not enabled.'
    );
  });

  it('matches other phrasings of the same failure', () => {
    // The symptom is matched as a family, so a wording change still lands as
    // long as the line carries both the subject and one of the phrasings.
    expect(findPlatformProblem('Virtualization is not enabled, so WSL2 fails to start.')).toBe(
      'Virtualization is not enabled, so WSL2 fails to start.'
    );
    expect(findPlatformProblem('WSL2 无法启动：当前系统未启用虚拟化支持')).toBe(
      'WSL2 无法启动：当前系统未启用虚拟化支持'
    );
    expect(findPlatformProblem('虚拟化已被禁用，WSL2 无法使用。')).toBe(
      '虚拟化已被禁用，WSL2 无法使用。'
    );
    expect(findPlatformProblem('Virtualization is disabled on this machine.')).toBe(
      'Virtualization is disabled on this machine.'
    );
    expect(findPlatformProblem('此计算机不支持虚拟化。')).toBe('此计算机不支持虚拟化。');
  });

  it('stays quiet on the advise lines and the help URL', () => {
    // "已启用" must not read as "未启用", and the help URL contains
    // "enablevirtualization" — neither is a problem statement.
    expect(
      findPlatformProblem('请确保计算机固件设置中“虚拟机平台”可选组件已启用，且虚拟化已开启。')
    ).toBeNull();
    expect(findPlatformProblem('有关信息，请访问 https://aka.ms/enablevirtualization')).toBeNull();
  });

  it('stays quiet on a healthy status output', () => {
    expect(findPlatformProblem(HEALTHY_STATUS)).toBeNull();
    expect(findPlatformProblem('')).toBeNull();
  });
});

describe('parseStaleOobeFlags', () => {
  it('reads the values the registry carries', () => {
    const stdout = 'IsOOBEInProgress=1\r\nAcceleratedInstallRequired=1\r\n';
    expect(parseStaleOobeFlags(stdout)).toEqual({
      IsOOBEInProgress: 1,
      AcceleratedInstallRequired: 1,
    });
  });

  it('ignores absent values and unrelated output', () => {
    expect(parseStaleOobeFlags('')).toEqual({});
    expect(parseStaleOobeFlags('IsOOBEInProgress=0\r\n')).toEqual({ IsOOBEInProgress: 0 });
    expect(parseStaleOobeFlags('Get-ItemProperty : 找不到路径\r\n')).toEqual({});
  });
});

describe('buildPlatformRepairScript', () => {
  const script = buildPlatformRepairScript();

  it('clears every stale marker', () => {
    for (const name of STALE_OOBE_VALUES) {
      expect(script).toContain(`Remove-ItemProperty -LiteralPath $key -Name ${name}`);
    }
  });

  it('pauses Windows Update around the edit and re-submits both features', () => {
    expect(script).toMatch(/Stop-Service -Name \$svc/);
    expect(script).toContain('Microsoft-Windows-Subsystem-Linux');
    expect(script).toContain('VirtualMachinePlatform');
    expect(script).toMatch(/Enable-WindowsOptionalFeature[^\n]*VirtualMachinePlatform/);
  });

  it('fails loudly, so a silent feature-enable error cannot pass as success', () => {
    // The caller only verifies the markers afterwards: with 'Continue' a
    // non-terminating Enable-WindowsOptionalFeature failure would still exit 0.
    expect(script).toContain("$ErrorActionPreference = 'Stop'");
    expect(script).not.toContain("$ErrorActionPreference = 'Continue'");
  });

  it('clears the markers again after the feature work and reports the result', () => {
    // Observed on the #1171 machine: the markers come back while DISM runs, and
    // the next boot reads whatever state the script leaves behind.
    expect(script.lastIndexOf('Remove-ItemProperty')).toBeGreaterThan(
      script.lastIndexOf('Enable-WindowsOptionalFeature')
    );
    expect(script).toContain('marker-cleared:');
    expect(script).toContain('marker-still-set:');
  });
});

describe('buildEnableFeaturesScript', () => {
  const script = buildEnableFeaturesScript();

  it('enables both features and fails loudly on a cmdlet error', () => {
    expect(script).toContain("$ErrorActionPreference = 'Stop'");
    expect(script).toContain('Microsoft-Windows-Subsystem-Linux');
    expect(script).toContain('VirtualMachinePlatform');
  });

  it('is the same enable step the platform repair runs', () => {
    // One definition, so the plain path and the repair path cannot drift apart.
    const repair = buildPlatformRepairScript();
    for (const line of script
      .split('\r\n')
      .filter((l) => l.startsWith('Enable-WindowsOptionalFeature'))) {
      expect(repair).toContain(line);
    }
  });
});

describe('isStaleElevatorDir', () => {
  const now = 1_700_000_000_000;

  it('sweeps the leftovers of past runs', () => {
    expect(isStaleElevatorDir('miqi-elev-ab12', now - 25 * 3600 * 1000, now)).toBe(true);
  });

  it('leaves a directory a still-running run may write into', () => {
    // A timed-out run keeps its directory on purpose: the elevated child writes
    // its exit code there whenever it finishes.
    expect(isStaleElevatorDir('miqi-elev-ab12', now - 60 * 1000, now)).toBe(false);
    expect(isStaleElevatorDir('miqi-elev-ab12', now, now)).toBe(false);
  });

  it('ignores directories that are not ours', () => {
    expect(isStaleElevatorDir('some-other-tool', now - 25 * 3600 * 1000, now)).toBe(false);
  });
});

describe('decodeWslOutput', () => {
  it('decodes UTF-16LE output that carries no BOM', () => {
    const text = 'WSL2 无法启动，因为此计算机上未启用虚拟化。';
    expect(decodeWslOutput(Buffer.from(text, 'utf16le'))).toBe(text);
  });

  it('passes UTF-8 output through', () => {
    const text = 'Default Version: 2';
    expect(decodeWslOutput(Buffer.from(text, 'utf8'))).toBe(text);
  });

  it('handles empty input', () => {
    expect(decodeWslOutput(null)).toBe('');
    expect(decodeWslOutput(Buffer.alloc(0))).toBe('');
  });
});

describe('classifyPlatformRepair', () => {
  const STILL_BROKEN = 'WSL2 无法启动，因为此计算机上未启用虚拟化。';
  const repairOk: ElevatedRunResult = { kind: 'ok', exitCode: 0, output: '' };
  const repairFailed: ElevatedRunResult = {
    kind: 'failed',
    exitCode: 1,
    output: 'DISM 失败：拒绝访问',
  };

  it('continues the install once the platform recovered, whatever the run reported', () => {
    // Exactly what the #1171 machine did: the payload landed while the markers
    // were being rewritten, so both the exit code and the markers mislead.
    expect(
      classifyPlatformRepair({
        repair: repairFailed,
        platformIssueAfter: null,
        staleAfter: { ok: false, stale: true },
      })
    ).toEqual({ status: 'continue' });
  });

  it('does not let a failed repair reach the distro install', () => {
    const outcome = classifyPlatformRepair({
      repair: repairFailed,
      platformIssueAfter: STILL_BROKEN,
      staleAfter: { ok: true, stale: false },
    });

    expect(outcome.status).toBe('failed');
    expect(outcome).toMatchObject({ detail: expect.stringContaining('DISM 失败') });
  });

  it('fails when the post-repair marker state cannot be read', () => {
    expect(
      classifyPlatformRepair({
        repair: repairOk,
        platformIssueAfter: STILL_BROKEN,
        staleAfter: { ok: false, stale: false },
      })
    ).toEqual({ status: 'failed', detail: '修复后无法确认标记已清除' });
  });

  it('fails while the markers are still set', () => {
    expect(
      classifyPlatformRepair({
        repair: repairOk,
        platformIssueAfter: STILL_BROKEN,
        staleAfter: { ok: true, stale: true },
      })
    ).toEqual({ status: 'failed', detail: '修复后仍检测到被推迟的更新' });
  });

  it('asks for the reboot that applies the queued features', () => {
    expect(
      classifyPlatformRepair({
        repair: repairOk,
        platformIssueAfter: STILL_BROKEN,
        staleAfter: { ok: true, stale: false },
      })
    ).toEqual({ status: 'reboot-required' });
  });
});
