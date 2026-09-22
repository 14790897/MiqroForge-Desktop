/**
 * 本地存储按登录账号划分（#1185）— Electron E2E。
 *
 * 验证目标：**同一台设备上换账号登录，看不到对方的会话**，切回来数据仍在。
 *
 * 走完整真实链路：预置登录态 → 主进程 QraftService 写 `<数据根>/accounts/.active`
 * → bridge 解析工作区根 → SessionManager 读 `sessions/` → 侧栏渲染。不 mock 任何
 * 一层，所以它同时钉住磁盘布局（`accounts/<sub>/workspace`）与用户可见结果（侧栏）。
 *
 * 「切换账号」用换预置登录态重启应用实现 —— 这是用户在同一台设备上换账号最常见的
 * 形态，也是唯一不依赖平台网络与登录表单的确定性形态（进程内换账号还要先登出再走
 * 一遍平台登录，那条路由 qraft-login / login-gate 覆盖）。
 *
 * 会话文件按运行时自己的落盘格式预置（首行 metadata + 消息行）：本用例验的是
 * 「哪些会话会被列出来」，不需要 LLM。
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
const ACCOUNT_A = '19';
const ACCOUNT_B = '20';
const TITLE_A = 'A账号的私密会话';
const TITLE_B = 'B账号的会话';

let tmpDir: string;
let storePath: string;
let miqiHome: string;

/**
 * 预置登录态（plain 信封；QraftStore 在无 safeStorage 时按明文降级读取）。
 *
 * 固定路径、按账号替换内容 —— 就是「同一台设备上前一个账号登出、后一个账号
 * 登录」在磁盘上留下的样子。
 */
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

/** 会话目录名 = `miqi.session.session_keys.session_files_dir_key(key)`。 */
function sessionDirName(key: string): string {
  const parts = key.split(':');
  if (parts.length >= 3) parts.shift();
  return parts.join('_').replace(/[^A-Za-z0-9._-]/g, '_');
}

/** 账号的工作区根 —— 与 QraftService / get_default_workspace_path 同一条式子。 */
function accountWorkspace(sub: string): string {
  return join(miqiHome, 'accounts', sub, 'workspace');
}

/** 在指定工作区根下预置一段会话（格式与 `SessionManager.save` 一致）。 */
function seedConversation(
  workspaceRoot: string,
  key: string,
  title: string,
  content: string
): void {
  const dir = join(workspaceRoot, 'sessions', sessionDirName(key));
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
  writeFileSync(
    join(dir, 'conversation.jsonl'),
    `${JSON.stringify(metadata)}\n${JSON.stringify({ role: 'user', content, timestamp: now })}\n`,
    'utf8'
  );
}

function readMarker(name: string): string {
  const file = join(miqiHome, 'accounts', name);
  return existsSync(file) ? readFileSync(file, 'utf8').trim() : '<无标记>';
}

/** 侧栏实际渲染出来的会话标题（走真实 UI，不是直接读 IPC 返回值）。 */
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

test.describe.serial('本地存储按登录账号划分（#1185）', () => {
  let fixture: ElectronFixture;
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    tmpDir = mkdtempSync(join(tmpdir(), 'miqi-account-e2e-'));
    storePath = join(tmpDir, 'qraft-auth.json');
    process.env[STORE_ENV] = storePath;

    // 第一次启动就带着 A 的登录态：这台设备上从来没过「未登录运行」，因此不会先
    // 建出 `<数据根>/workspace`，也就不会把它当成存量数据认领走（那条路径由
    // issue-1185-legacy-workspace-claim.spec.ts 单独覆盖）。首次启动只是为了让
    // fixture 建出临时 home，好往里预置两个账号的会话。
    seedLogin(ACCOUNT_A);
    fixture = await launchElectronApp();
    miqiHome = fixture.miqiHome;
    electronApp = fixture.electronApp;
    await closeElectronApp(electronApp, miqiHome, true);

    seedConversation(accountWorkspace(ACCOUNT_A), 'desktop:seed-a', TITLE_A, 'A 的私密问题');
    seedConversation(accountWorkspace(ACCOUNT_B), 'desktop:seed-b', TITLE_B, 'B 的问题');
  });

  test.afterAll(async () => {
    delete process.env[STORE_ENV];
    // 不传 keepHome：这一下才把 fixture 建的临时 home 收掉（与 cron-page 同）。
    if (electronApp) await closeElectronApp(electronApp, miqiHome);
    if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
  });

  /** 换登录态重启，返回新的一页。 */
  async function switchTo(sub: string): Promise<void> {
    if (electronApp) await closeElectronApp(electronApp, miqiHome, true);
    seedLogin(sub);
    fixture = await relaunchElectronApp(miqiHome);
    electronApp = fixture.electronApp;
    page = fixture.page;
  }

  test('A 账号：看到自己的会话，看不到 B 的；工作区落在 accounts/19 下', async () => {
    await switchTo(ACCOUNT_A);

    await expectSidebarToShow(page, TITLE_A, TITLE_B);

    // 磁盘布局：账号维度确实进了路径，而不是继续共用 <数据根>/workspace。
    expect(readMarker('.active')).toBe(ACCOUNT_A);
    expect(existsSync(join(accountWorkspace(ACCOUNT_A), 'sessions'))).toBe(true);
    expect(existsSync(join(miqiHome, 'workspace', 'sessions'))).toBe(false);
  });

  test('切到 B 账号：看不到 A 的会话（同设备换账号不再共享历史）', async () => {
    await switchTo(ACCOUNT_B);

    await expectSidebarToShow(page, TITLE_B, TITLE_A);
    expect(readMarker('.active')).toBe(ACCOUNT_B);
  });

  test('切回 A 账号：A 的会话还在', async () => {
    await switchTo(ACCOUNT_A);

    await expectSidebarToShow(page, TITLE_A, TITLE_B);
    expect(readMarker('.active')).toBe(ACCOUNT_A);
  });
});
