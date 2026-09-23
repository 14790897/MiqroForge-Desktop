/**
 * 存量工作区归属（#1185 item 5）— Electron E2E。
 *
 * 升级场景：设备上已经有一份未分账号的 `<数据根>/workspace`（老用户的历史会话
 * 就在里面）。它必须归**首个在设备上登录的账号**，并且**就地保留** —— 既不搬
 * 目录（搬失败与「数据消失」在用户眼里没有区别），也不能被第二个账号看到。
 *
 * 用真机链路验证：应用在无账号态启动一次（bridge 自己建出 `<数据根>/workspace`，
 * 这就是「升级前的存量目录」）→ 往里预置一段会话 → 换两个账号分别登录，看侧栏。
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import {
  launchElectronApp,
  relaunchElectronApp,
  closeElectronApp,
  type ElectronFixture,
} from './helpers/electron-setup';

const STORE_ENV = 'MIQI_QRAFT_STORE';
const ACCOUNT_FIRST = '19';
const ACCOUNT_SECOND = '20';
const LEGACY_TITLE = '老用户的存量会话';
const SECOND_TITLE = '后到账号自己的会话';

let tmpDir: string;
let storePath: string;
let miqiHome: string;

function seedLogin(sub: string): void {
  const state = {
    version: 1,
    env: 'test',
    baseUrl: 'http://127.0.0.1:9/api',
    clientId: 'miqi',
    clientSecret: 'test-client-secret',
    redirectUri: 'http://localhost:38000/callback',
    cookie: 'Authorization=e2e-test-cookie',
    account: { phone: '18500000000', sub, username: `E2E-${sub}`, nickname: `E2E账号${sub}` },
    tokens: {
      accessToken: `e2e-fake-access-token-${sub}`,
      refreshToken: 'e2e-fake-refresh-token',
      openid: 'e2e-fake-openid',
      expiresAt: Date.now() + 7_199_000,
    },
  };
  writeFileSync(
    storePath,
    JSON.stringify({
      v: 1,
      enc: 'plain',
      payload: Buffer.from(JSON.stringify(state), 'utf8').toString('base64'),
    }),
    'utf8'
  );
}

function sessionDirName(key: string): string {
  const parts = key.split(':');
  if (parts.length >= 3) parts.shift();
  return parts.join('_').replace(/[^A-Za-z0-9._-]/g, '_');
}

/** 存量目录：升级前那份未分账号的工作区。 */
function legacyWorkspace(): string {
  return join(miqiHome, 'workspace');
}

function accountWorkspace(sub: string): string {
  return join(miqiHome, 'accounts', sub, 'workspace');
}

function readMarker(name: string): string {
  const file = join(miqiHome, 'accounts', name);
  return existsSync(file) ? readFileSync(file, 'utf8').trim() : '<无标记>';
}

/** 在指定工作区根下预置一段会话（格式与 `SessionManager.save` 一致）。 */
function seedConversation(root: string, key: string, title: string, content: string): void {
  const dir = join(root, 'sessions', sessionDirName(key));
  mkdirSync(dir, { recursive: true });
  const now = new Date().toISOString();
  const metadata = {
    _type: 'metadata',
    key,
    owner_client_id: 'miqi-desktop',
    created_at: now,
    updated_at: now,
    metadata: { owner_client_id: 'miqi-desktop', title },
    last_consolidated: 0,
  };
  // 用 fromCharCode 而不是字面量转义：这个文件里已经因为转义吃过一次亏。
  const NL = String.fromCharCode(10);
  const message = { role: 'user', content, timestamp: now };
  writeFileSync(
    join(dir, 'conversation.jsonl'),
    [JSON.stringify(metadata), JSON.stringify(message)].join(NL) + NL,
    'utf8'
  );
}

async function sidebarTitles(page: Page): Promise<string[]> {
  return page.getByTestId('session-item').allInnerTexts();
}

async function expectSidebarToShow(page: Page, present: string, absent: string): Promise<void> {
  await expect
    .poll(async () => (await sidebarTitles(page)).join('\n'), {
      message: `侧栏应列出「${present}」`,
      timeout: 30_000,
    })
    .toContain(present);
  expect(
    (await sidebarTitles(page)).join('\n'),
    `「${absent}」不该出现在这个账号的侧栏里`
  ).not.toContain(absent);
}

test.describe.serial('存量工作区归属（#1185）', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    tmpDir = mkdtempSync(join(tmpdir(), 'miqi-legacy-e2e-'));
    storePath = join(tmpDir, 'qraft-auth.json');
    process.env[STORE_ENV] = storePath;

    // 无账号态启动一次（存储文件还不存在）：这就是「升级后第一次打开、还没登录」
    // 的样子，bridge 按无账号解析把 `<数据根>/workspace` 建出来。
    fixture = await launchElectronApp();
    miqiHome = fixture.miqiHome;
    electronApp = fixture.electronApp;
    await closeElectronApp(electronApp, miqiHome, true);

    expect(existsSync(legacyWorkspace()), '无账号启动应建出存量根目录').toBe(true);
    // 给第二个账号也预置一段**它自己的**会话。断言「看不到存量」需要一个正向栅栏：
    // 侧栏初始是空数组，`length === 0` 的轮询完全可能在 list 返回之前就通过
    // （#1185 评审）。
    seedConversation(
      accountWorkspace(ACCOUNT_SECOND),
      'desktop:second',
      SECOND_TITLE,
      '后到的账号'
    );
    // 往里放一段老用户的会话 —— 升级前它就在这个位置。
    const dir = join(legacyWorkspace(), 'sessions', sessionDirName('desktop:legacy'));
    mkdirSync(dir, { recursive: true });
    const now = new Date().toISOString();
    const metadata = {
      _type: 'metadata',
      key: 'desktop:legacy',
      owner_client_id: 'miqi-desktop',
      created_at: now,
      updated_at: now,
      metadata: { owner_client_id: 'miqi-desktop', title: LEGACY_TITLE },
      last_consolidated: 0,
    };
    writeFileSync(
      join(dir, 'conversation.jsonl'),
      `${JSON.stringify(metadata)}\n${JSON.stringify({ role: 'user', content: '升级前的历史', timestamp: now })}\n`,
      'utf8'
    );
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    // 不传 keepHome：这一下才把 fixture 建的临时 home 收掉（与 cron-page 同）。
    if (electronApp) await closeElectronApp(electronApp, miqiHome);
    if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
  });

  async function switchTo(sub: string): Promise<void> {
    if (electronApp) await closeElectronApp(electronApp, miqiHome, true);
    seedLogin(sub);
    fixture = await relaunchElectronApp(miqiHome);
    electronApp = fixture.electronApp;
    page = fixture.page;
  }

  test('首个登录的账号认领存量工作区，历史会话看得见', async () => {
    await switchTo(ACCOUNT_FIRST);

    await expectSidebarToShow(page, LEGACY_TITLE, '不存在的会话');

    // 归属标记只写一次，且**没有**搬家：账号继续用旧目录。
    expect(readMarker('.legacy-owner')).toBe(ACCOUNT_FIRST);
    expect(existsSync(join(legacyWorkspace(), 'sessions'))).toBe(true);
    expect(existsSync(accountWorkspace(ACCOUNT_FIRST))).toBe(false);
  });

  test('后到的账号看不到那份存量历史，拿到自己的账号工作区', async () => {
    await switchTo(ACCOUNT_SECOND);

    // 正向栅栏：先等到**它自己的**会话出现（证明 list 已经返回），再断言存量那
    // 条不在。只等空列表的话，可能在 list 还没回来时就通过 —— 那是假绿。
    await expectSidebarToShow(page, SECOND_TITLE, LEGACY_TITLE);

    // 不会被改写成第二个账号的存量：归属标记仍是首个账号。
    expect(readMarker('.legacy-owner')).toBe(ACCOUNT_FIRST);
    expect(existsSync(join(accountWorkspace(ACCOUNT_SECOND), 'sessions'))).toBe(true);
  });
});
