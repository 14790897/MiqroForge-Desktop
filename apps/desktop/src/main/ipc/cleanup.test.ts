/**
 * 清理执行器的单测（issue #1177）。
 * 覆盖：WSL 列表解析（含名字部分匹配不误删）、scope 文件往返、
 * 临时目录上的真实删除（数据根/workspace 排除/updater 缓存）、
 * #1103 回归——根目录未通过安全校验时绝不扩大删除范围。
 */
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { planCleanupItems, type CleanupContext } from '../../shared/cleanup-paths';
import {
  dirSizeBytes,
  parseWslDistroList,
  readCleanupScope,
  runCleanup,
  writeCleanupScope,
  type CleanupScope,
} from './cleanup';

function makeCtx(homeDir: string): CleanupContext {
  return {
    platform: 'win32',
    homeDir,
    appDataDir: join(homeDir, 'AppData', 'Roaming'),
    localAppDataDir: join(homeDir, 'AppData', 'Local'),
    registryDataRoot: null,
    env: {},
    systemRoot: 'C:\\Windows',
  };
}

/** 临时家目录 + 假数据根（workspace/sessions/sandbox_state.json）。 */
function makeFixtureRoot(): { homeDir: string; root: string } {
  const homeDir = mkdtempSync(join(tmpdir(), 'miqi-cleanup-test-'));
  const root = join(homeDir, '.miqi');
  mkdirSync(join(root, 'workspace', 'session1', 'files'), { recursive: true });
  mkdirSync(join(root, 'sessions'), { recursive: true });
  mkdirSync(join(root, 'plugins'), { recursive: true });
  writeFileSync(join(root, 'sandbox_state.json'), '{}', 'utf8');
  writeFileSync(join(root, 'workspace', 'session1', 'files', 'report.docx'), 'hello', 'utf8');
  writeFileSync(join(root, 'sessions', 'x.json'), '{}', 'utf8');
  return { homeDir, root };
}

describe('parseWslDistroList', () => {
  it('extracts distro names and drops localized header lines', () => {
    const text = [
      '适用于 Linux 的 Windows 子系统分发版:',
      'Ubuntu',
      'AIShadowSandbox',
      'docker-desktop',
    ].join('\r\n');
    expect(parseWslDistroList(text)).toEqual(['Ubuntu', 'AIShadowSandbox', 'docker-desktop']);
  });

  it('lookalike names never trigger the exact-match sandbox check', () => {
    // 解析器按行提取名字；「是否沙箱 distro」的判定是 includes 精确全等——
    // 相似名字（-dev / Old 后缀）不得命中。
    const names = parseWslDistroList(
      'AIShadowSandbox-dev\r\nAIShadowSandboxOld\r\nAIShadowSandbox'
    );
    expect(names.includes('AIShadowSandbox')).toBe(true);
    const lookalikesOnly = parseWslDistroList('AIShadowSandbox-dev\r\nAIShadowSandboxOld');
    expect(lookalikesOnly.includes('AIShadowSandbox')).toBe(false);
  });

  it('handles empty output', () => {
    expect(parseWslDistroList('')).toEqual([]);
    expect(parseWslDistroList('\r\n \r\n')).toEqual([]);
  });
});

describe('dirSizeBytes', () => {
  it('sums file sizes in a small tree', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-cleanup-size-'));
    writeFileSync(join(dir, 'a.txt'), '1234', 'utf8');
    mkdirSync(join(dir, 'sub'));
    writeFileSync(join(dir, 'sub', 'b.txt'), '12345678', 'utf8');
    expect(await dirSizeBytes(dir)).toBe(12);
    rmSync(dir, { recursive: true, force: true });
  });

  it('returns null for a missing directory', async () => {
    expect(await dirSizeBytes(join(tmpdir(), 'miqi-cleanup-missing-xyz'))).toBeNull();
  });
});

describe('cleanup scope roundtrip', () => {
  it('writes and reads a scope file', () => {
    const scopePath = join(mkdtempSync(join(tmpdir(), 'miqi-cleanup-scope-')), 'scope.json');
    const scope: CleanupScope = {
      ctx: {
        homeDir: 'C:\\Users\\x',
        appDataDir: 'C:\\Users\\x\\AppData\\Roaming',
        localAppDataDir: 'C:\\Users\\x\\AppData\\Local',
        registryDataRoot: null,
        platform: 'win32',
      },
      ids: ['data-root:rest', 'wsl-distro'],
      logPath: 'C:\\Temp\\log.txt',
    };
    writeCleanupScope(scope, scopePath);
    expect(readCleanupScope(scopePath)).toEqual(scope);
  });

  it('rejects broken scope files', () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-cleanup-scope-'));
    const bad = join(dir, 'bad.json');
    writeFileSync(bad, '{not json', 'utf8');
    expect(readCleanupScope(bad)).toBeNull();
    const noIds = join(dir, 'noids.json');
    writeFileSync(noIds, JSON.stringify({ ctx: {} }), 'utf8');
    expect(readCleanupScope(noIds)).toBeNull();
    expect(readCleanupScope(join(dir, 'missing.json'))).toBeNull();
  });
});

describe('runCleanup on a temp data root', () => {
  it('data-root:rest deletes everything except workspace, and logs', async () => {
    const { homeDir, root } = makeFixtureRoot();
    const ctx = makeCtx(homeDir);
    const logPath = join(homeDir, 'clean.log');
    const rest = planCleanupItems(ctx).find((i) => i.id === 'data-root:rest')!;
    const report = await runCleanup(ctx, [rest], { logPath });

    expect(report.cleaned.length).toBeGreaterThan(0);
    expect(report.failed).toEqual([]);
    // 其余子项已删、workspace 保留
    expect(existsSync(join(root, 'sessions'))).toBe(false);
    expect(existsSync(join(root, 'plugins'))).toBe(false);
    expect(existsSync(join(root, 'sandbox_state.json'))).toBe(false);
    expect(existsSync(join(root, 'workspace', 'session1', 'files', 'report.docx'))).toBe(true);
    // 日志有内容
    const log = readFileSync(logPath, 'utf8');
    expect(log).toContain('[已清理]');
    expect(log).toContain('[保留]');
  });

  it('workspace item deletes the workspace only', async () => {
    const { homeDir, root } = makeFixtureRoot();
    const ctx = makeCtx(homeDir);
    const workspace = planCleanupItems(ctx).find((i) => i.id === 'data-root:workspace')!;
    const report = await runCleanup(ctx, [workspace]);
    expect(report.failed).toEqual([]);
    expect(existsSync(join(root, 'workspace'))).toBe(false);
    expect(existsSync(join(root, 'sessions'))).toBe(true);
  });

  it('user-data is refused while running (deletableNow 语义)', async () => {
    const { homeDir } = makeFixtureRoot();
    const ctx = makeCtx(homeDir);
    const userData = planCleanupItems(ctx).find((i) => i.id === 'user-data')!;
    const report = await runCleanup(ctx, [userData]);
    expect(report.cleaned).toEqual([]);
    expect(report.failed[0]?.reason).toContain('退出');
  });

  it('updater cache dir is deleted', async () => {
    const { homeDir } = makeFixtureRoot();
    const ctx = makeCtx(homeDir);
    const cache = join(ctx.localAppDataDir, 'miqi-desktop-updater');
    mkdirSync(cache, { recursive: true });
    writeFileSync(join(cache, 'pending.exe'), 'x', 'utf8');
    const updater = planCleanupItems(ctx).find((i) => i.id === 'updater-cache')!;
    const report = await runCleanup(ctx, [updater]);
    expect(report.failed).toEqual([]);
    expect(existsSync(cache)).toBe(false);
  });

  it('#1103 回归：根指向家目录本身时跳过，绝不删除家目录', async () => {
    const { homeDir, root } = makeFixtureRoot();
    // 恶意/异常的注册表值指向家目录 → 显式数据根不安全 → data-root 项被守卫拒绝
    const ctx: CleanupContext = {
      ...makeCtx(homeDir),
      registryDataRoot: homeDir,
    };
    const rest = planCleanupItems(ctx).find((i) => i.id === 'data-root:rest')!;
    const report = await runCleanup(ctx, [rest]);
    expect(report.cleaned).toEqual([]);
    expect(report.failed.length).toBeGreaterThan(0);
    // 家目录与其中所有内容原封不动
    expect(existsSync(join(root, 'sessions'))).toBe(true);
    expect(existsSync(join(root, 'workspace'))).toBe(true);
  });
});
