/**
 * 法律文件同意状态的权威存储（issue #1068 / #1071）。
 *
 * 为什么不能只依赖渲染层 localStorage：打包版两个实例共享同一 Chromium
 * userData 时，第二个实例的存储会退化成内存 —— 它读不到第一个实例写入的
 * 同意记录（每次都弹确认门），自己写入的同意也不会落盘。主进程把版本写到
 * userData/privacy-consent.json，任何实例都能读到，且写入即持久（不受
 * Chromium 存储懒刷盘影响）。渲染层 localStorage 保留为快速缓存。
 */
import { join } from 'node:path';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { electron } from '../shared/electron';

const { app } = electron;

function storePath(): string {
  return join(app.getPath('userData'), 'privacy-consent.json');
}

/** 读取已同意的协议版本；无记录或文件损坏时返回 null。 */
export function readConsentVersion(): string | null {
  try {
    const raw = readFileSync(storePath(), 'utf8');
    const parsed: unknown = JSON.parse(raw);
    const version = (parsed as { version?: unknown } | null)?.version;
    return typeof version === 'string' && version ? version : null;
  } catch {
    return null;
  }
}

/** 写入同意版本；传 null 清除记录（撤回同意 / E2E 重置）。 */
export function writeConsentVersion(version: string | null): void {
  try {
    if (!version) {
      rmSync(storePath(), { force: true });
      return;
    }
    mkdirSync(app.getPath('userData'), { recursive: true });
    writeFileSync(
      storePath(),
      JSON.stringify({ version, updatedAt: new Date().toISOString() }, null, 2),
      'utf8'
    );
  } catch {
    /* 磁盘不可写：本实例仍以内存态继续，渲染层缓存不受影响 */
  }
}
