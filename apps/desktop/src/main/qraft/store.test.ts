import { describe, expect, it, beforeEach, afterEach } from 'vitest';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { QraftStore, type SafeStorageLike } from './store';
import type { QraftStoredState } from './types';
import type { QraftLogger } from './client';

function fakeSafeStorage(): SafeStorageLike {
  return {
    isEncryptionAvailable: () => true,
    encryptString: (plain: string) => Buffer.from(`ENC:${plain}`, 'utf8'),
    decryptString: (buf: Buffer) => buf.toString('utf8').replace(/^ENC:/, ''),
  };
}

function makeState(overrides: Partial<QraftStoredState> = {}): QraftStoredState {
  return {
    version: 1,
    env: 'test',
    baseUrl: 'https://test.forge.miqroera.com/api',
    clientId: 'miqi',
    clientSecret: 'miqi123456',
    redirectUri: 'http://localhost:38000/callback',
    cookie: 'Authorization=uuid-1',
    account: { phone: '18500000000', sub: '19', username: 'U-HKY4-GB4E', nickname: 'MiQi测试' },
    tokens: {
      accessToken: 'ACCESS',
      refreshToken: 'REFRESH',
      openid: 'OPENID',
      expiresAt: Date.now() + 7_199_000,
    },
    ...overrides,
  };
}

const noopLog = (() => undefined) as unknown as QraftLogger;

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), 'qraft-store-'));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe('QraftStore', () => {
  it('加密保存 → 加载还原（safeStorage 往返）', () => {
    const file = join(dir, 'qraft-auth.json');
    const safe = fakeSafeStorage();
    const store = new QraftStore(file, safe, noopLog);
    // 用当前生产登录态（非历史默认值），迁移不应触碰
    const state = makeState({ env: 'prod', baseUrl: 'https://www.miqroforge.com/api' });
    store.save(state);

    // 磁盘上是信封结构，不包含明文凭据
    const onDisk = readFileSync(file, 'utf8');
    expect(onDisk).not.toContain('ACCESS');
    expect(onDisk).not.toContain('REFRESH');
    expect(onDisk).not.toContain('uuid-1');
    const envelope = JSON.parse(onDisk);
    expect(envelope.enc).toBe('safeStorage');

    const loaded = new QraftStore(file, safe, noopLog).load();
    expect(loaded).toEqual(state);
  });

  it('存量生产登录态的历史默认地址随域名迁移更新为新默认', () => {
    const file = join(dir, 'qraft-auth.json');
    const safe = fakeSafeStorage();
    const store = new QraftStore(file, safe, noopLog);
    store.save(makeState({ env: 'prod', baseUrl: 'https://forge.miqroera.com/api' }));

    const loaded = new QraftStore(file, safe, noopLog).load();
    expect(loaded?.baseUrl).toBe('https://www.miqroforge.com/api');
    // 迁移只改地址，其余登录态原样保留
    expect(loaded?.tokens.accessToken).toBe('ACCESS');
    expect(loaded?.account.sub).toBe('19');
  });

  it('存量测试环境登录态并入生产：环境、地址与回调一并迁移', () => {
    const file = join(dir, 'qraft-auth.json');
    const safe = fakeSafeStorage();
    const store = new QraftStore(file, safe, noopLog);
    store.save(
      makeState({
        env: 'test',
        baseUrl: 'https://test.forge.miqroera.com/api',
        redirectUri: 'http://localhost:52311/callback', // 测试环境自动生成的随机 loopback
      })
    );

    const loaded = new QraftStore(file, safe, noopLog).load();
    expect(loaded?.env).toBe('prod');
    expect(loaded?.baseUrl).toBe('https://www.miqroforge.com/api');
    // 随机 loopback 未在平台注册，迁到生产时换成注册值
    expect(loaded?.redirectUri).toBe('http://localhost:38000/callback');
    expect(loaded?.tokens.accessToken).toBe('ACCESS');
  });

  it('自定义 baseUrl 不被域名迁移改写', () => {
    const file = join(dir, 'qraft-auth.json');
    const safe = fakeSafeStorage();
    const store = new QraftStore(file, safe, noopLog);
    store.save(makeState({ env: 'prod', baseUrl: 'https://internal.example.com/api' }));

    const loaded = new QraftStore(file, safe, noopLog).load();
    expect(loaded?.baseUrl).toBe('https://internal.example.com/api');
  });

  it('安全存储不可用时降级 Base64（仍不落明文），并告警', () => {
    const file = join(dir, 'qraft-auth.json');
    const warnings: string[] = [];
    const log = ((_l: string, m: string) => warnings.push(m)) as unknown as QraftLogger;
    const unavailable: SafeStorageLike = {
      isEncryptionAvailable: () => false,
      encryptString: () => {
        throw new Error('should not be called');
      },
      decryptString: () => {
        throw new Error('should not be called');
      },
    };
    const store = new QraftStore(file, unavailable, log);
    store.save(makeState());

    const onDisk = readFileSync(file, 'utf8');
    expect(onDisk).not.toContain('ACCESS');
    expect(JSON.parse(onDisk).enc).toBe('plain');

    const loaded = new QraftStore(file, unavailable, noopLog).load();
    expect(loaded?.tokens.accessToken).toBe('ACCESS');
    expect(warnings.some((w) => w.includes('安全存储不可用'))).toBe(true);
  });

  it('文件缺失 / 损坏时返回 null 且不抛异常', () => {
    const file = join(dir, 'qraft-auth.json');
    const safe = fakeSafeStorage();
    expect(new QraftStore(file, safe, noopLog).load()).toBeNull();

    writeFileSync(file, '{"v":99,"payload":"x"}', 'utf8');
    expect(new QraftStore(file, safe, noopLog).load()).toBeNull();

    writeFileSync(file, 'not-json-at-all', 'utf8');
    expect(new QraftStore(file, safe, noopLog).load()).toBeNull();
  });

  it('clear 清空内存与磁盘', () => {
    const file = join(dir, 'qraft-auth.json');
    const safe = fakeSafeStorage();
    const store = new QraftStore(file, safe, noopLog);
    store.save(makeState());
    expect(store.current).not.toBeNull();

    store.clear();
    expect(store.current).toBeNull();
    expect(existsSync(file)).toBe(true);
    expect(readFileSync(file, 'utf8')).toBe('');
  });
});
