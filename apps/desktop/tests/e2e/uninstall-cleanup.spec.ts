/**
 * 卸载残留清理 E2E（issue #1177）——Windows only，真实 NSIS 安装/卸载链路。
 *
 * 前置条件（任一不满足即 skip，绝不碰真实数据）：
 *   - 运行于 Windows；
 *   - 环境变量 MIIQI_E2E_UNINSTALL=1（显式确认本机可跑安装/卸载）；
 *   - 数据根 ~/.miqi 与 %APPDATA%\miqi-desktop 当前不存在（不覆盖真实用户数据）；
 *   - dist-new 下存在构建好的安装器（MiQroForge Desktop Setup *.exe）。
 *
 * WSL 部分可选：设 MIIQI_E2E_WSL_ROOTFS=<rootfs.tar> 时先用 `wsl --import
 * AIShadowSandbox` 造一个沙箱 distro 再断言卸载后消失；不设则跳过 WSL 断言
 * （WSL 注销逻辑由 src/main/ipc/cleanup.test.ts 单测覆盖）。
 *
 * 用例 1：安装 → 造残留 → MIQI_UNINSTALL_CLEANUP=1 静默卸载 → 断言四类残留
 *         全无 + %TEMP%\miqi-uninstall.log 记录清理。
 * 用例 2：再安装 → 再造残留 → 默认静默卸载 → 断言数据完整保留。
 */
import { test, expect } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join } from 'node:path';
import { decodeWslOutput } from '../../src/main/ipc/wsl-state';

const isWindows = process.platform === 'win32';
const optIn = process.env.MIQI_E2E_UNINSTALL === '1';
const dataRoot = join(homedir(), '.miqi');
const userData = join(process.env.APPDATA ?? '', 'miqi-desktop');
const updaterCache = join(process.env.LOCALAPPDATA ?? '', 'miqi-desktop-updater');
const installDir = join(process.env.LOCALAPPDATA ?? '', 'Programs', 'miqi-desktop');

function findInstaller(): string | null {
  const dir = join(__dirname, '..', '..', '..', '..', 'dist-new');
  try {
    const match = readdirSync(dir).find(
      (f) => f.startsWith('MiQroForge Desktop Setup') && f.endsWith('.exe')
    );
    return match ? join(dir, match) : null;
  } catch {
    return null;
  }
}

/** 静默执行并返回 { status, stdout }。 */
function run(cmd: string, args: string[]): { status: number | null; stdout: string } {
  const r = spawnSync(cmd, args, { encoding: 'utf8', windowsHide: true, timeout: 300_000 });
  return { status: r.status, stdout: r.stdout ?? '' };
}

function uninstallerPath(): string {
  const match = readdirSync(installDir).find(
    (f) => f.startsWith('Uninstall') && f.endsWith('.exe')
  );
  if (!match) throw new Error(`未找到卸载器: ${installDir}`);
  return join(installDir, match);
}

/** 造四类代表残留（含 workspace 用户文档、sandbox_state.json）。 */
function seedResidue(): void {
  mkdirSync(join(dataRoot, 'workspace', 'docs'), { recursive: true });
  mkdirSync(join(dataRoot, 'sessions'), { recursive: true });
  writeFileSync(join(dataRoot, 'sandbox_state.json'), '{}', 'utf8');
  writeFileSync(join(dataRoot, 'workspace', 'docs', 'report.docx'), 'residue', 'utf8');
  mkdirSync(userData, { recursive: true });
  writeFileSync(join(userData, 'qraft-auth.json'), '{}', 'utf8');
  mkdirSync(updaterCache, { recursive: true });
  writeFileSync(join(updaterCache, 'pending.exe'), 'x', 'utf8');
}

function installSilently(installer: string): void {
  const r = run(installer, ['/S']);
  expect(r.status, `安装失败: ${r.stdout}`).toBe(0);
  expect(existsSync(installDir), '安装目录应存在').toBe(true);
}

function uninstallSilently(): void {
  const r = run(uninstallerPath(), ['/S']);
  expect(r.status, `卸载失败`).toBe(0);
}

test.describe('卸载残留清理（#1177，Windows + 显式 opt-in）', () => {
  test.skip(!isWindows || !optIn, '仅 Windows 且需 MIIQI_E2E_UNINSTALL=1');
  test.skip(
    existsSync(dataRoot) || existsSync(userData),
    '本机存在真实数据根/userData，保护性跳过'
  );
  test.skip(!findInstaller(), 'dist-new 下无构建好的安装器');

  test('勾选清理卸载后四类残留清零，日志记录清理', () => {
    const installer = findInstaller()!;
    process.env.MIQI_UNINSTALL_CLEANUP = '1';

    installSilently(installer);
    seedResidue();

    if (process.env.MIQI_E2E_WSL_ROOTFS) {
      // 可选：导入一个真 distro，卸载后必须被注销
      const wslDir = join(tmpdir(), 'miqi-e2e-wsl');
      rmSync(wslDir, { recursive: true, force: true });
      const r = run('wsl.exe', [
        '--import',
        'AIShadowSandbox',
        wslDir,
        process.env.MIQI_E2E_WSL_ROOTFS,
      ]);
      expect(r.status).toBe(0);
    }

    uninstallSilently();

    // 程序文件与四类残留全部消失
    expect(existsSync(installDir)).toBe(false);
    expect(existsSync(dataRoot)).toBe(false);
    expect(existsSync(userData)).toBe(false);
    expect(existsSync(updaterCache)).toBe(false);
    if (process.env.MIQI_E2E_WSL_ROOTFS) {
      // wsl -l -q 输出为 UTF-16LE，须先解码再比对（与主进程探测同源）。
      const list = decodeWslOutput(Buffer.from(run('wsl.exe', ['-l', '-q']).stdout, 'binary'));
      expect(list).not.toContain('AIShadowSandbox');
      rmSync(join(tmpdir(), 'miqi-e2e-wsl'), { recursive: true, force: true });
    }
    // 卸载日志记录清理
    const logPath = join(tmpdir(), 'miqi-uninstall.log');
    expect(existsSync(logPath)).toBe(true);
    expect(readFileSync(logPath, 'utf8')).toContain('[已清理]');
    rmSync(logPath, { force: true });
    delete process.env.MIQI_UNINSTALL_CLEANUP;
  });

  test('默认卸载保留全部应用数据', () => {
    const installer = findInstaller()!;
    installSilently(installer);
    seedResidue();
    uninstallSilently();

    expect(existsSync(installDir)).toBe(false);
    // 数据原样保留
    expect(existsSync(dataRoot)).toBe(true);
    expect(existsSync(userData)).toBe(true);
    expect(existsSync(updaterCache)).toBe(true);
    // 清理测试残留
    rmSync(dataRoot, { recursive: true, force: true });
    rmSync(userData, { recursive: true, force: true });
    rmSync(updaterCache, { recursive: true, force: true });
  });
});
