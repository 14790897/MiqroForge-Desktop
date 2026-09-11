/**
 * MiQroForge 登录态安全存储。
 *
 * token / 平台登录 cookie 用 Electron safeStorage 加密后落盘
 * （文件位于 userData/qraft-auth.json）。安全存储不可用（如 Linux 无
 * keyring）时降级为 Base64 混淆存储，并在日志中警告 —— 绝不写入明文。
 *
 * 所有读写失败都不抛给调用方（登录流程本身不依赖磁盘），只记录警告。
 */

import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs';
import { dirname } from 'path';
import type { QraftStoredState } from './types';
import type { QraftLogger } from './client';

/** Electron safeStorage 的最小注入接口（便于测试与降级）。 */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plainText: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

interface StoreEnvelope {
  v: 1;
  /** safeStorage | plain（安全存储不可用时的降级） */
  enc: string;
  payload: string;
}

const ENVELOPE_VERSION = 1;

export class QraftStore {
  private state: QraftStoredState | null = null;
  private encryptionAvailable: boolean;

  constructor(
    private readonly filePath: string,
    private readonly safeStorage: SafeStorageLike | null,
    private readonly log: QraftLogger
  ) {
    this.encryptionAvailable = !!safeStorage?.isEncryptionAvailable?.();
    if (!this.encryptionAvailable) {
      log('WARN', 'qraft: 系统安全存储不可用，登录态将以 Base64 混淆方式落盘');
    }
  }

  get current(): QraftStoredState | null {
    return this.state;
  }

  /** 从磁盘加载加密登录态。文件缺失/损坏时返回 null 并记录警告。 */
  load(): QraftStoredState | null {
    try {
      if (!existsSync(this.filePath)) return null;
      const envelope = JSON.parse(readFileSync(this.filePath, 'utf8')) as StoreEnvelope;
      if (!envelope || envelope.v !== ENVELOPE_VERSION || typeof envelope.payload !== 'string') {
        this.log('WARN', 'qraft: 登录态文件格式无法识别，按未登录处理');
        return null;
      }
      let plain: string;
      if (envelope.enc === 'safeStorage' && this.safeStorage) {
        plain = this.safeStorage.decryptString(Buffer.from(envelope.payload, 'base64'));
      } else if (envelope.enc === 'plain') {
        plain = Buffer.from(envelope.payload, 'base64').toString('utf8');
      } else {
        this.log('WARN', `qraft: 未知的登录态加密方式（${envelope.enc}），按未登录处理`);
        return null;
      }
      this.state = JSON.parse(plain) as QraftStoredState;
      return this.state;
    } catch (err) {
      this.log(
        'WARN',
        `qraft: 读取登录态失败（${err instanceof Error ? err.message : err}），按未登录处理`
      );
      return null;
    }
  }

  /** 加密并落盘当前登录态。失败仅警告，不影响内存中的登录态。 */
  save(state: QraftStoredState): void {
    this.state = state;
    try {
      mkdirSync(dirname(this.filePath), { recursive: true });
      const plain = JSON.stringify(state);
      let envelope: StoreEnvelope;
      if (this.encryptionAvailable && this.safeStorage) {
        envelope = {
          v: ENVELOPE_VERSION,
          enc: 'safeStorage',
          payload: this.safeStorage.encryptString(plain).toString('base64'),
        };
      } else {
        envelope = {
          v: ENVELOPE_VERSION,
          enc: 'plain',
          payload: Buffer.from(plain, 'utf8').toString('base64'),
        };
      }
      writeFileSync(this.filePath, JSON.stringify(envelope), { encoding: 'utf8', mode: 0o600 });
      // writeFileSync 的 mode 只在创建文件时生效；对已存在的文件显式收紧权限
      //（Windows 上是 no-op，POSIX 上防止历史文件权限过宽）。
      chmodSync(this.filePath, 0o600);
    } catch (err) {
      this.log('WARN', `qraft: 保存登录态失败（${err instanceof Error ? err.message : err}）`);
    }
  }

  /** 清除内存与磁盘上的登录态（退出登录）。 */
  clear(): void {
    this.state = null;
    try {
      if (existsSync(this.filePath)) {
        writeFileSync(this.filePath, '', { encoding: 'utf8' });
      }
    } catch {
      /* 磁盘清理失败不阻塞退出登录 */
    }
  }
}
