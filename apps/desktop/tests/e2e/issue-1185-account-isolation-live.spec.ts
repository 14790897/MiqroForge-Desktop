/**
 * 本地存储按登录账号划分（#1185）— **真实账号** live E2E（opt-in，默认跳过，不入 CI）。
 *
 * 与 billing-live / ai-gateway-live 同策略：凭据只经环境变量注入，登录态与工作区
 * 都落在 launchElectronApp 的临时 MIQI_HOME，测试结束随临时目录清理 —— 不碰开发机
 * 的 `~/.miqi`，也不在仓库里留下任何凭据。
 *
 * 用法：
 *   QRAFT_LIVE=1 \
 *   QRAFT_PHONE_A=<账号A> QRAFT_PASSWORD_A=<密码A> \
 *   QRAFT_PHONE_B=<账号B> QRAFT_PASSWORD_B=<密码B> \
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     issue-1185-account-isolation-live.spec.ts
 *
 * 覆盖 issue #1185 的验收路径：**A 发消息 → 登出 → 登入 B（看不到 A 的会话）→
 * 登出 → 登回 A（自己的会话还在）**。
 */

import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  sendMessage,
  waitForResponseComplete,
  type ElectronFixture,
} from './helpers/electron-setup';

const LIVE = process.env.QRAFT_LIVE === '1';
const PHONE_A = process.env.QRAFT_PHONE_A ?? '';
const PASSWORD_A = process.env.QRAFT_PASSWORD_A ?? '';
const PHONE_B = process.env.QRAFT_PHONE_B ?? '';
const PASSWORD_B = process.env.QRAFT_PASSWORD_B ?? '';
const READY = LIVE && PHONE_A !== '' && PASSWORD_A !== '' && PHONE_B !== '' && PASSWORD_B !== '';

/** 真实网关回一次话比 mock 慢得多，给足预算。 */
const TURN_TIMEOUT = 300_000;

const describeFn = READY ? test.describe : test.describe.skip;

describeFn('本地存储按登录账号划分（#1185）— 真实账号 live E2E (opt-in)', () => {
  let fixture: ElectronFixture;

  test.beforeAll(async () => {
    // 不绕过登录门：这条用例验的就是真实登录/登出/换账号这条路径。
    fixture = await launchElectronApp(undefined, { noLoginBypass: true });
  }, 180_000);

  test.afterAll(async () => {
    if (fixture?.electronApp) await closeElectronApp(fixture.electronApp, fixture.miqiHome);
  });

  /** 等 bridge 真的进 running：真实登录后 AppShell 才挂载并拉起运行时。 */
  async function waitForRuntime(): Promise<void> {
    await expect
      .poll(
        async () => {
          const status = await fixture.page.evaluate(() => (window as any).miqi.runtime.status());
          return status?.state ?? 'unknown';
        },
        { timeout: 120_000, message: '登录后 bridge 应进入 running' }
      )
      .toBe('running');
  }

  /** 走应用自己的登录入口（登录门按钮调的就是它），返回该账号的 sub。 */
  async function login(phone: string, password: string): Promise<string> {
    const page = fixture.page;
    const result = await page.evaluate(([p, pw]) => (window as any).miqi.qraft.login(p, pw), [
      phone,
      password,
    ] as const);
    expect(result?.ok, `登录失败：${JSON.stringify(result)}`).toBe(true);
    await expect(page.getByTestId('nav-new-session')).toBeVisible({ timeout: 60_000 });
    await waitForRuntime();
    return String(result.account?.sub ?? '');
  }

  async function logout(): Promise<void> {
    const page = fixture.page;
    await page.evaluate(() => (window as any).miqi.qraft.logout());
    await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 60_000 });
  }

  /**
   * 当前账号侧栏里的会话 key（与侧栏同一个数据源）。
   *
   * 形状不对就抛：否则 bridge 没起来时返回的 `undefined` 会让「B 看不到 A 的
   * 会话」变成空数组直接通过 —— 那是最典型的假绿。
   */
  async function sessionKeys(): Promise<string[]> {
    const list = await fixture.page.evaluate(() => (window as any).miqi.sessions.list());
    expect(list, 'sessions.list 应返回对象').toBeTruthy();
    expect(
      Array.isArray(list.sessions),
      `sessions.list 应带 sessions 数组：${JSON.stringify(list)}`
    ).toBe(true);
    return (list.sessions as Array<{ key: string }>).map((s) => s.key);
  }

  function readMarker(name: string): string {
    const file = join(fixture.miqiHome, 'accounts', name);
    return existsSync(file) ? readFileSync(file, 'utf8').trim() : '<无标记>';
  }

  /** 指定会话目录下有没有哪个 conversation.jsonl 含这段文本。 */
  function sessionsDirContains(sessionsDir: string, text: string): boolean {
    if (!existsSync(sessionsDir)) return false;
    return readdirSync(sessionsDir).some((name) => {
      const file = join(sessionsDir, name, 'conversation.jsonl');
      return existsSync(file) && readFileSync(file, 'utf8').includes(text);
    });
  }

  function accountSessions(sub: string): string {
    return join(fixture.miqiHome, 'accounts', sub, 'workspace', 'sessions');
  }

  /**
   * 账号**实际**的工作区根。
   *
   * 真机上这条路径通常不走 accounts/<sub>/：应用停在登录门时 bridge 已经跑过一次
   * （无账号态），把 `<数据根>/workspace` 建了出来；首个账号登录就按 #1185 的存量
   * 归属规则把它认领走、就地使用。第二个账号才会拿到 accounts/<sub>/workspace。
   * 断言必须按 `.legacy-owner` 分情况，否则验的就不是真实行为。
   */
  function effectiveSessions(sub: string): string {
    const legacyOwner = readMarker('.legacy-owner');
    return legacyOwner === sub
      ? join(fixture.miqiHome, 'workspace', 'sessions')
      : accountSessions(sub);
  }

  test(
    'A 发消息 → 登出 → 登入 B 看不到 A 的会话 → 登回 A 数据仍在',
    { timeout: TURN_TIMEOUT },
    async () => {
      const page = fixture.page;
      // 判据用「这句话本身」而不是会话 key：`desktop:default` 是默认哨兵 key，
      // 每个账号登录都会被建一个同名的会话 —— 拿 key 比对会把「两边各有自己的
      // 哨兵会话」误报成泄漏，而真正要验的是**内容**互不可见。
      const token = `ACCOUNTISOLATION${Date.now()}`;

      // ── A：真实登录 → 新建会话 → 真实网关回一条 ──────────────────────
      const subA = await login(PHONE_A, PASSWORD_A);
      await createNewConversation(page);
      await sendMessage(page, `只回复两个字：收到（${token}）`);
      await waitForResponseComplete(page, 240_000);

      // 先确认这条消息真的走通了网关 —— 否则拿到的可能只是一个「运行时未就绪」
      // 的报错回合，那就不是在验真实链路。
      const assistantText = await page.getByTestId('chat-message-assistant').last().innerText();
      expect(assistantText, '真实网关应给出回复').not.toContain('运行时未启动');

      await expect
        .poll(sessionKeys, { timeout: 120_000, message: 'A 发完消息后侧栏应出现会话' })
        .not.toEqual([]);
      // 磁盘布局：标记指向当前账号，且该账号的工作区根下确实落了会话。
      expect(readMarker('.active')).toBe(subA);
      const aSessions = effectiveSessions(subA);
      console.log(
        `[1185] A(sub=${subA}) 工作区=${aSessions} .legacy-owner=${readMarker('.legacy-owner')}`
      );
      expect(existsSync(aSessions), `A 的会话目录应存在：${aSessions}`).toBe(true);
      expect(sessionsDirContains(aSessions, token), 'A 的那句话应该落在 A 自己的会话目录里').toBe(
        true
      );
      // 用户可见的那一层：A 的侧栏里能看到这句话。
      await expect(page.getByTestId('session-item').first()).toContainText(token, {
        timeout: 30_000,
      });

      // ── 登出，登入 B：看不到 A 的任何对话 ────────────────────────────
      await logout();
      const subB = await login(PHONE_B, PASSWORD_B);
      expect(subB, '两个账号的 sub 应不同').not.toBe(subA);
      expect(readMarker('.active')).toBe(subB);
      // B 的账号工作区由它自己的第一次会话操作建出来（SessionManager 初始化时
      // ensure_dir），所以这里要等一拍 —— 直接断言会撞上时序。
      await expect
        .poll(() => existsSync(accountSessions(subB)), {
          timeout: 60_000,
          message: `B 应拿到自己的账号工作区：${accountSessions(subB)}`,
        })
        .toBe(true);
      expect(accountSessions(subB)).not.toBe(aSessions);
      console.log(
        `[1185] A(sub=${subA}) 会话目录=${aSessions}
` +
          `[1185] B(sub=${subB}) 会话目录=${accountSessions(subB)}
` +
          `[1185] .active=${readMarker('.active')} .legacy-owner=${readMarker('.legacy-owner')}`
      );

      // 1) 磁盘：B 的会话目录里找不到 A 的那句话。
      expect(sessionsDirContains(accountSessions(subB), token), 'B 的工作区里不该有 A 的对话').toBe(
        false
      );
      // 2) 侧栏：用户看不到 A 的会话。
      const titlesB = await page.getByTestId('session-item').allInnerTexts();
      expect(titlesB.join('\n'), 'B 的侧栏里不该出现 A 的对话').not.toContain(token);
      // 3) 切走不删数据：A 的会话目录原封不动。
      expect(existsSync(aSessions), 'A 的会话目录不该因为换账号被删掉').toBe(true);

      // ── 登回 A：自己的对话还在 ──────────────────────────────────────
      await logout();
      await login(PHONE_A, PASSWORD_A);
      expect(readMarker('.active')).toBe(subA);

      await expect
        .poll(async () => (await page.getByTestId('session-item').allInnerTexts()).join('\n'), {
          timeout: 60_000,
          message: '切回 A 后自己的会话应重新出现',
        })
        .toContain(token);
    }
  );
});
