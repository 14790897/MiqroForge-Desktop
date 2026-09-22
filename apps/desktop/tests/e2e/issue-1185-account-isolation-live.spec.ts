/**
 * 本地存储按登录账号划分（#1185）— **真实账号** live E2E（opt-in，默认跳过，不入 CI）。
 *
 * 与 billing-live / ai-gateway-live 同策略：凭据只经环境变量注入，登录态与工作区
 * 都落在 launchElectronApp 的临时 MIQI_HOME，测试结束随临时目录清理 —— 不碰开发机
 * 的 `~/.miqi`，也不在仓库里留下任何凭据。
 *
 * 用法（至少两对账号，可给到三对）：
 *   QRAFT_LIVE=1 \
 *   QRAFT_PHONE_A=<账号A> QRAFT_PASSWORD_A=<密码A> \
 *   QRAFT_PHONE_B=<账号B> QRAFT_PASSWORD_B=<密码B> \
 *   [QRAFT_PHONE_C=<账号C> QRAFT_PASSWORD_C=<密码C>] \
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     issue-1185-account-isolation-live.spec.ts
 *
 * 验收路径：**逐个账号登录并发一条带唯一 token 的消息；每个账号登录后都要证明
 * 前面所有账号的 token 在工作区与侧栏里都找不到；最后登回第一个账号，它自己的
 * 还在**。
 *
 * 判据刻意用「消息内容」而不是会话 key：`desktop:default` 是默认哨兵 key，每个
 * 账号登录都会被建一个同名会话 —— 拿 key 比对会把「两边各有自己的哨兵会话」误报
 * 成泄漏。
 */

import { test, expect } from '@playwright/test';
import { join } from 'node:path';
import { existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import {
  launchElectronApp,
  closeElectronApp,
  createNewConversation,
  sendMessage,
  waitForResponseComplete,
  type ElectronFixture,
} from './helpers/electron-setup';

const LIVE = process.env.QRAFT_LIVE === '1';

interface Account {
  label: string;
  phone: string;
  password: string;
}

const ACCOUNTS: Account[] = (
  [
    ['A', 'QRAFT_PHONE_A', 'QRAFT_PASSWORD_A'],
    ['B', 'QRAFT_PHONE_B', 'QRAFT_PASSWORD_B'],
    ['C', 'QRAFT_PHONE_C', 'QRAFT_PASSWORD_C'],
  ] as const
)
  .map(([label, phoneVar, passwordVar]) => ({
    label,
    phone: process.env[phoneVar] ?? '',
    password: process.env[passwordVar] ?? '',
  }))
  .filter((a) => a.phone !== '' && a.password !== '');

/** 真实网关回一次话比 mock 慢得多，给足预算。 */
const TURN_TIMEOUT = 300_000;

const describeFn = LIVE && ACCOUNTS.length >= 2 ? test.describe : test.describe.skip;

describeFn('本地存储按登录账号划分（#1185）— 真实账号 live E2E (opt-in)', () => {
  let fixture: ElectronFixture;

  test.beforeAll(async () => {
    // 不绕过登录门：这条用例验的就是真实登录/登出/换账号这条路径。
    fixture = await launchElectronApp(undefined, { noLoginBypass: true });
  }, 180_000);

  test.afterAll(async () => {
    stopCapture(); // 用例中途失败时的兜底：别让抓帧循环挂在已关闭的窗口上
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
  async function login(account: Account): Promise<string> {
    const page = fixture.page;
    const result = await page.evaluate(([p, pw]) => (window as any).miqi.qraft.login(p, pw), [
      account.phone,
      account.password,
    ] as const);
    expect(result?.ok, `账号 ${account.label} 登录失败：${JSON.stringify(result)}`).toBe(true);
    await expect(page.getByTestId('nav-new-session')).toBeVisible({ timeout: 60_000 });
    await waitForRuntime();
    const sub = String(result.account?.sub ?? '');
    // 空 sub 必须在这里拦住：`accountSessions('')` 指的是一个并不存在的目录，
    // 而 `sessionsDirContains` 对不存在的目录一律返回 false —— 后面的「看不到
    // 别人的」就成了空转的假绿（#1185 评审）。这里是唯一能拦的地方。
    expect(sub, `账号 ${account.label} 的登录响应里没有 sub`).not.toBe('');
    return sub;
  }

  async function logout(): Promise<void> {
    const page = fixture.page;
    await page.evaluate(() => (window as any).miqi.qraft.logout());
    await expect(page.getByTestId('login-step')).toBeVisible({ timeout: 60_000 });
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
   * 账号**实际**的工作区根 —— 首个账号有两种可能，取决于一次竞态。
   *
   * 应用停在登录门时 bridge 会按无账号态跑一次并把 `<数据根>/workspace` 建出来；
   * 登录若发生在它之后，首个账号就按 #1185 的存量归属规则把这个目录认领走、就地
   * 使用（`.legacy-owner=<sub>`）；登录若赶在它之前，则三个账号都拿到
   * `accounts/<sub>/workspace`。两种结果实测都出现过，功能上等效（各账号互不可见），
   * 所以断言按 `.legacy-owner` 分情况，而不是假定某一种。
   */
  function effectiveSessions(sub: string): string {
    return readMarker('.legacy-owner') === sub
      ? join(fixture.miqiHome, 'workspace', 'sessions')
      : accountSessions(sub);
  }

  async function sidebarText(): Promise<string> {
    return (await fixture.page.getByTestId('session-item').allInnerTexts()).join('\n');
  }

  /** 关键节点截图（给 PR 留证据）。 */
  const shotDir = join('test-results', 'issue-1185-live');
  async function shoot(name: string): Promise<void> {
    mkdirSync(shotDir, { recursive: true });
    await fixture.page.screenshot({ path: join(shotDir, `${name}.png`) });
  }

  /**
   * 逐帧抓拍，事后用 ffmpeg 合成录屏。
   *
   * Playwright 的 `video` 只对浏览器生效，**对 `_electron.launch()` 不录**，
   * 所以录屏只能自己抓帧。间隔放宽到 700ms：抓帧和测试动作共用一条 CDP 连接，
   * 太密会拖慢真实回合。
   */
  const framesDir = join(shotDir, 'frames');
  let captureTimer: ReturnType<typeof setInterval> | null = null;
  let frameNo = 0;
  function startCapture(): void {
    mkdirSync(framesDir, { recursive: true });
    captureTimer = setInterval(() => {
      void fixture.page
        .screenshot({ path: join(framesDir, `frame-${String(frameNo++).padStart(4, '0')}.png`) })
        .catch(() => undefined);
    }, 700);
  }
  function stopCapture(): void {
    if (captureTimer) clearInterval(captureTimer);
    captureTimer = null;
  }

  test(
    '逐个真实账号登录：看不到此前任何一个账号的对话，登回第一个自己的还在',
    // 每个账号都要登录 + 真实回一条，按账号数放宽：3 个账号 ≈ 10 分钟。
    { timeout: Math.max(TURN_TIMEOUT, 240_000 + ACCOUNTS.length * 120_000) },
    async () => {
      startCapture();
      const page = fixture.page;
      /** 已发过消息的账号：label / sub / token / 它的会话目录。 */
      const sent: Array<{ label: string; sub: string; token: string; sessions: string }> = [];

      for (const account of ACCOUNTS) {
        if (sent.length > 0) await logout();
        const sub = await login(account);
        expect(
          sent.map((s) => s.sub),
          `账号 ${account.label} 的 sub 不该与已测账号重复`
        ).not.toContain(sub);

        // ── 先证明「看不到前面的」：这是本用例的主诉求 ──────────────────
        const ownSessions = effectiveSessions(sub);
        for (const prev of sent) {
          expect(
            prev.sessions,
            `账号 ${prev.label} 与 ${account.label} 的会话目录不该是同一个`
          ).not.toBe(ownSessions);
          expect(
            sessionsDirContains(ownSessions, prev.token),
            `账号 ${account.label} 的工作区里不该有账号 ${prev.label} 的对话`
          ).toBe(false);
          expect(
            await sidebarText(),
            `账号 ${account.label} 的侧栏里不该出现账号 ${prev.label} 的对话`
          ).not.toContain(prev.token);
        }

        // 这一屏正是「看不到前面任何账号的对话」——截图留证。
        await shoot(`${account.label}-1-logged-in-no-previous`);

        // ── 再让这个账号说一句自己的，作为下一位的对照物 ────────────────
        const token = `ACCOUNTISOLATION${account.label}${Date.now()}`;
        await createNewConversation(page);
        await sendMessage(page, `只回复两个字：收到（${token}）`);
        await waitForResponseComplete(page, 240_000);

        // 先确认这条消息真的走通了网关 —— 否则拿到的可能是一个「运行时未就绪」
        // 的报错回合，那就不是在验真实链路。
        const assistantText = await page.getByTestId('chat-message-assistant').last().innerText();
        expect(assistantText, `账号 ${account.label} 应拿到真实网关回复`).not.toContain(
          '运行时未启动'
        );

        // 自己的落盘与侧栏都要能看到这句话（否则下一轮的「看不到」就没有意义）。
        await expect
          .poll(() => sessionsDirContains(ownSessions, token), {
            timeout: 120_000,
            message: `账号 ${account.label} 的那句话应落在自己的工作区里`,
          })
          .toBe(true);
        await expect
          .poll(sidebarText, {
            timeout: 60_000,
            message: `账号 ${account.label} 的侧栏应出现该会话`,
          })
          .toContain(token);

        expect(readMarker('.active')).toBe(sub);
        await shoot(`${account.label}-2-own-session`);
        sent.push({ label: account.label, sub, token, sessions: ownSessions });
        console.log(
          `[1185] ${account.label}(sub=${sub}) 工作区=${ownSessions} ` +
            `.active=${readMarker('.active')} .legacy-owner=${readMarker('.legacy-owner')}`
        );
      }

      // ── 登回第一个账号：自己的对话还在，别人的照旧不在 ────────────────
      await logout();
      const first = ACCOUNTS[0];
      const subFirst = await login(first);
      expect(readMarker('.active')).toBe(subFirst);
      await expect
        .poll(sidebarText, { timeout: 60_000, message: '切回第一个账号后自己的会话应重新出现' })
        .toContain(sent[0].token);
      for (const other of sent.slice(1)) {
        expect(await sidebarText(), `第一个账号的侧栏不该出现 ${other.label} 的对话`).not.toContain(
          other.token
        );
      }
      await shoot('first-3-back-own-session-only');

      // 切走不删数据：前面每个账号的会话目录都还在。
      for (const s of sent) {
        expect(existsSync(s.sessions), `账号 ${s.label} 的会话目录不该因为换账号被删掉`).toBe(true);
      }
      stopCapture();
    }
  );
});
