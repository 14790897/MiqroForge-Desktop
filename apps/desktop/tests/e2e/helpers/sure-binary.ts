/**
 * 真实 SURE 二进制的共用探针(E2E)。
 *
 * 「测试不 mock」规则下,凡需要 SURE 的 spec 共用同一条真实查找链和最小
 * JSON 调用器;找不到时由各 spec 如实 skip、绝不拿替身顶替。
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

/** 查找链:env SURE_BIN → PATH → 平台默认安装位置。 */
export function findRealSureBin(): string | null {
  const fromEnv = process.env.SURE_BIN;
  if (fromEnv && existsSync(fromEnv)) return fromEnv;
  const which = spawnSync(process.platform === 'win32' ? 'where' : 'which', ['sure'], {
    encoding: 'utf-8',
    windowsHide: true,
    timeout: 15_000,
  });
  const hit = (which.stdout ?? '').split('\n')[0]?.trim();
  if (which.status === 0 && hit && existsSync(hit)) return hit;
  const fallback =
    process.platform === 'win32' && process.env.LOCALAPPDATA
      ? join(process.env.LOCALAPPDATA, 'SURE', 'bin', 'sure.exe')
      : '';
  return fallback && existsSync(fallback) ? fallback : null;
}

/** 跑真实 sure 并把 stdout 解析为 JSON(退出码非 0 / 解析失败返回 null)。 */
export function runSureJson(bin: string, args: string[]): any | null {
  const res = spawnSync(bin, [...args, '--format', 'json'], {
    encoding: 'utf-8',
    timeout: 30_000,
    windowsHide: true,
  });
  if (res.status !== 0 || !res.stdout) return null;
  try {
    return JSON.parse(res.stdout.trim()) as any;
  } catch {
    return null;
  }
}

/**
 * 该构建是否含 miqi 集成——`sure doctor` 的集成清单行(官方 0.1.2 没有,
 * fork 构建才有)。按行首 `miqi` 匹配,避免路径等处的子串误报。
 */
export function sureSupportsMiqi(bin: string): boolean {
  const res = spawnSync(bin, ['doctor'], { encoding: 'utf-8', timeout: 30_000, windowsHide: true });
  return /^\s*miqi\s/m.test(res.stdout ?? '');
}
