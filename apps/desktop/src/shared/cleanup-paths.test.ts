/**
 * 卸载/清理路径规划的单测（issue #1177）。
 * 核心回归面：#1103 教训——路径解析失败或指向家目录/盘根/系统目录时，
 * 删除目标必须被拒，绝不回退到扩大删除范围。
 */
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  classifyDataRootCandidate,
  dataRootDeletionTargets,
  isSafeDeletionRoot,
  planCleanupItems,
  resolveActiveDataRoot,
  resolveExplicitDataRoot,
  type CleanupContext,
} from './cleanup-paths';

function makeCtx(overrides: Partial<CleanupContext> = {}): CleanupContext {
  const homeDir = 'C:\\Users\\tester';
  return {
    platform: 'win32',
    homeDir,
    appDataDir: path.join(homeDir, 'AppData', 'Roaming'),
    localAppDataDir: path.join(homeDir, 'AppData', 'Local'),
    registryDataRoot: null,
    env: {},
    systemRoot: 'C:\\Windows',
    ...overrides,
  };
}

describe('isSafeDeletionRoot', () => {
  const ctx = makeCtx();

  it('accepts a default candidate data root', () => {
    expect(isSafeDeletionRoot(path.join(ctx.homeDir, '.miqi'), ctx).safe).toBe(true);
    expect(isSafeDeletionRoot('D:\\miqi-data', ctx).safe).toBe(true);
  });

  it('rejects the filesystem root', () => {
    const r = isSafeDeletionRoot('C:\\', ctx);
    expect(r.safe).toBe(false);
    expect(r.reason).toContain('根目录');
  });

  it('rejects the user home directory (#1103: never delete the whole user dir)', () => {
    const r = isSafeDeletionRoot(ctx.homeDir, ctx);
    expect(r.safe).toBe(false);
  });

  it('rejects system directories and anything inside them', () => {
    expect(isSafeDeletionRoot('C:\\Windows', ctx).safe).toBe(false);
    expect(isSafeDeletionRoot('C:\\windows\\system32\\x', ctx).safe).toBe(false);
  });

  it('rejects %APPDATA% / %LOCALAPPDATA% themselves', () => {
    expect(isSafeDeletionRoot(ctx.appDataDir, ctx).safe).toBe(false);
    expect(isSafeDeletionRoot(ctx.localAppDataDir, ctx).safe).toBe(false);
  });

  it('rejects empty and unparsable paths', () => {
    expect(isSafeDeletionRoot('', ctx).safe).toBe(false);
    expect(isSafeDeletionRoot('   ', ctx).safe).toBe(false);
    expect(isSafeDeletionRoot('C:\\bad\\\0\\dir', ctx).safe).toBe(false);
  });

  it('compares case-insensitively on win32', () => {
    expect(isSafeDeletionRoot('c:\\users\\TESTER', ctx).safe).toBe(false);
  });
});

describe('resolveExplicitDataRoot', () => {
  it('registry wins over MIQI_HOME', () => {
    const ctx = makeCtx({
      registryDataRoot: 'D:\\miqi-reg',
      env: { MIQI_HOME: 'E:\\miqi-env' },
    });
    const r = resolveExplicitDataRoot(ctx);
    expect(r?.path).toBe('D:\\miqi-reg');
    expect(r?.source).toBe('registry');
    expect(r?.safe).toBe(true);
  });

  it('falls back to MIQI_HOME when no registry value', () => {
    const ctx = makeCtx({ env: { MIQI_HOME: 'E:\\miqi-env' } });
    const r = resolveExplicitDataRoot(ctx);
    expect(r?.path).toBe('E:\\miqi-env');
    expect(r?.source).toBe('env');
  });

  it('reports unsafe registry values instead of pretending they are fine', () => {
    const ctx = makeCtx({ registryDataRoot: 'C:\\Users\\tester' });
    const r = resolveExplicitDataRoot(ctx);
    expect(r).not.toBeNull();
    expect(r!.safe).toBe(false);
    expect(r!.reason).toContain('用户主目录');
  });

  it('returns null when neither registry nor env is set', () => {
    expect(resolveExplicitDataRoot(makeCtx())).toBeNull();
  });
});

describe('dataRootDeletionTargets', () => {
  it('returns only the explicit root when it is safe', () => {
    const ctx = makeCtx({ registryDataRoot: 'D:\\miqi-reg' });
    expect(dataRootDeletionTargets(ctx)).toEqual(['D:\\miqi-reg']);
  });

  it('returns nothing when the explicit root is unsafe (#1103: skip, never widen)', () => {
    const ctx = makeCtx({ registryDataRoot: 'C:\\Users\\tester' });
    expect(dataRootDeletionTargets(ctx)).toEqual([]);
  });

  it('returns nothing when MIQI_HOME resolves to the home dir via traversal', () => {
    const ctx = makeCtx({ env: { MIQI_HOME: 'C:\\Users\\tester\\.miqi\\..' } });
    // resolve 后 = 家目录 → 不安全 → 不删任何东西
    expect(dataRootDeletionTargets(ctx)).toEqual([]);
  });

  it('falls back to all default-name candidates when nothing is explicit', () => {
    const ctx = makeCtx();
    expect(dataRootDeletionTargets(ctx)).toEqual([
      path.join(ctx.homeDir, '.forge'),
      path.join(ctx.homeDir, '.miqi'),
      path.join(ctx.homeDir, '.assistant'),
    ]);
  });
});

describe('classifyDataRootCandidate', () => {
  const ctx = makeCtx();

  it('classifies default and legacy names', () => {
    expect(classifyDataRootCandidate(path.join(ctx.homeDir, '.miqi'), ctx).kind).toBe('default');
    expect(classifyDataRootCandidate(path.join(ctx.homeDir, '.forge'), ctx).kind).toBe('default');
    expect(classifyDataRootCandidate(path.join(ctx.homeDir, '.assistant'), ctx).kind).toBe(
      'legacy'
    );
  });

  it('classifies the registry root as custom', () => {
    const withReg = makeCtx({ registryDataRoot: 'D:\\miqi-reg' });
    expect(classifyDataRootCandidate('D:\\miqi-reg', withReg).kind).toBe('custom');
  });

  it('marks anything else as not-a-candidate (never deleted)', () => {
    expect(classifyDataRootCandidate('C:\\Users\\tester', ctx).kind).toBe('not-a-candidate');
    expect(classifyDataRootCandidate('C:\\Users\\tester\\Downloads', ctx).kind).toBe(
      'not-a-candidate'
    );
    // 名字相似但不是默认候选的目录也不删（如 .miqi-backup）
    expect(classifyDataRootCandidate(path.join(ctx.homeDir, '.miqi-backup'), ctx).kind).toBe(
      'not-a-candidate'
    );
    expect(classifyDataRootCandidate('C:\\Users\\tester\\..\\..', ctx).kind).toBe(
      'not-a-candidate'
    );
  });
});

describe('resolveActiveDataRoot (mirrors miqi/utils/helpers.py get_data_path)', () => {
  it('MIQI_HOME wins', () => {
    expect(resolveActiveDataRoot(makeCtx({ env: { MIQI_HOME: 'E:\\x' } }))).toBe('E:\\x');
  });

  it('legacy dir is used only when the default does not exist', () => {
    const ctx = makeCtx();
    const legacy = path.join(ctx.homeDir, '.assistant');
    const withLegacy = { ...ctx, dirExists: (d: string) => d === legacy };
    expect(resolveActiveDataRoot(withLegacy)).toBe(legacy);
  });

  it('default wins when both exist', () => {
    const ctx = makeCtx({ dirExists: () => true });
    expect(resolveActiveDataRoot(ctx)).toBe(path.join(ctx.homeDir, '.miqi'));
  });

  it('defaults to the active default name', () => {
    const ctx = makeCtx({ dirExists: () => false });
    expect(resolveActiveDataRoot(ctx)).toBe(path.join(ctx.homeDir, '.miqi'));
  });
});

describe('planCleanupItems', () => {
  const ctx = makeCtx();
  const items = planCleanupItems(ctx);
  const byId = Object.fromEntries(items.map((i) => [i.id, i]));

  it('defaults to preserving user documents (workspace unchecked)', () => {
    expect(byId['data-root:workspace'].defaultChecked).toBe(false);
    expect(byId['data-root:rest'].defaultChecked).toBe(true);
  });

  it('marks user-data as requiring app exit and unchecked by default', () => {
    expect(byId['user-data'].defaultChecked).toBe(false);
    expect(byId['user-data'].deletableNow).toBe(false);
    expect(byId['user-data'].path).toBe(path.join(ctx.appDataDir, 'miqi-desktop'));
  });

  it('data-root:rest excludes the workspace from deletion', () => {
    expect(byId['data-root:rest'].excludes).toEqual([path.join(ctx.homeDir, '.miqi', 'workspace')]);
  });

  it('WSL distro item carries the exact distro name and no filesystem path', () => {
    expect(byId['wsl-distro'].path).toBeNull();
    expect(byId['wsl-distro'].label).toContain('AIShadowSandbox');
  });

  it('updater cache item points at %LOCALAPPDATA%\\miqi-desktop-updater', () => {
    expect(byId['updater-cache'].path).toBe(path.join(ctx.localAppDataDir, 'miqi-desktop-updater'));
  });

  it('every run-while-alive item is deletable now except user-data', () => {
    for (const item of items) {
      expect(item.deletableNow).toBe(item.id !== 'user-data');
    }
  });
});
