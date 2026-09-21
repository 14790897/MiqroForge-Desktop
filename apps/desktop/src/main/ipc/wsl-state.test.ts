/**
 * Unit tests for the WSL state helpers.  The text fixtures are verbatim
 * captures from real machines (a working one, and one whose optional-feature
 * install was stuck because Windows kept deferring its servicing passes).
 */
import { describe, expect, it } from 'vitest';
import {
  buildPlatformRepairScript,
  decodeWslOutput,
  findPlatformProblem,
  parseStaleOobeFlags,
  STALE_OOBE_VALUES,
} from './wsl-state';

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
