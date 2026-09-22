/**
 * 复杂技能的产物跨账号切换（#1185）— **真实账号** live E2E（opt-in，默认跳过）。
 *
 * 维护者要求：「再测试下跑复杂技能，比如生成PPT，看看文件能否在切换账号回来后正常显示」。
 *
 * 验的是两件事：
 *   1. A 账号跑出的 PPT 产物，在换到 B 账号后**看不到**；
 *   2. 切回 A 账号后，那个文件仍在，且**应用自己能读到它**（走 `files.read`，
 *      也就是渲染层「显示文件」用的同一条路径）——只断言磁盘上有文件是不够的，
 *      路径解析、账号根、包含性检查任何一环变了都会让文件「看不见」。
 *
 * 关于「跑复杂技能」：这里走的是 `pptx_write` **工具**（`full-electron.spec.ts`
 * 里那条 AI PPT 用例同一条路），而不是 `pptx-generator` **技能**。原因是技能要
 * node + pptxgenjs，而本机沙箱是 WSL 且禁用了 Windows 二进制互操作 —— 助手会卡在
 * 「找不到 node / cannot execute: required file not found」上空转（实测 7 分钟
 * 没结束）。工具路径在宿主侧执行，不需要 node，产出的同样是真实的 .pptx。
 * 要验的「产物跨账号切换仍在」与用哪条路径生成无关。
 *
 * 用法（至少一对账号，需真实网关能跑技能）：
 *   QRAFT_LIVE=1 \
 *   QRAFT_PHONE_A=<账号A> QRAFT_PASSWORD_A=<密码A> \
 *   QRAFT_PHONE_B=<账号B> QRAFT_PASSWORD_B=<密码B> \
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 \
 *   npx playwright test --config=playwright.config.ts --project=electron \
 *     issue-1185-task-assets-live.spec.ts
 *
 * 凭据只经环境变量注入，登录态与工作区都落在临时 MIQI_HOME，仓库里不留凭据。
 */

import { test, expect } from '@playwright/test';
import { join, relative } from 'node:path';
import { existsSync, readFileSync, readdirSync, mkdirSync, statSync } from 'node:fs';
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

const ACCOUNT_A: Account = {
  label: 'A',
  phone: process.env.QRAFT_PHONE_A ?? '',
  password: process.env.QRAFT_PASSWORD_A ?? '',
};
const ACCOUNT_B: Account = {
  label: 'B',
  phone: process.env.QRAFT_PHONE_B ?? '',
  password: process.env.QRAFT_PASSWORD_B ?? '',
};
const READY = LIVE && ACCOUNT_A.phone !== '' && ACCOUNT_A.password !== '' && ACCOUNT_B.phone !== '';

/** 技能回合比普通问答长得多（多轮工具调用）。 */
const SKILL_TIMEOUT = 600_000;
const PPTX_NAME = 'account_switch_e2e.pptx';

const describeFn = READY ? test.describe : test.describe.skip;

describeFn('复杂技能产物跨账号切换（#1185）— 真实账号 live E2E (opt-in)', () => {
  let fixture: ElectronFixture;

  test.beforeAll(async () => {
    fixture = await launchElectronApp(undefined, { noLoginBypass: true });
  }, 180_000);

  test.afterAll(async () => {
    if (fixture?.electronApp) await closeElectronApp(fixture.electronApp, fixture.miqiHome);
  });

  const shotDir = join('test-results', 'issue-1185-assets-live');
  async function shoot(name: string): Promise<void> {
    mkdirSync(shotDir, { recursive: true });
    await fixture.page.screenshot({ path: join(shotDir, `${name}.png`) });
  }

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
    expect(sub, `账号 ${account.label} 的登录响应里没有 sub`).not.toBe('');
    return sub;
  }

  async function logout(): Promise<void> {
    await fixture.page.evaluate(() => (window as any).miqi.qraft.logout());
    await expect(fixture.page.getByTestId('login-step')).toBeVisible({ timeout: 60_000 });
  }

  function readMarker(name: string): string {
    const file = join(fixture.miqiHome, 'accounts', name);
    return existsSync(file) ? readFileSync(file, 'utf8').trim() : '<无标记>';
  }

  /** 账号**实际**的工作区根（首账号可能认领共享根，见 account-isolation 用例的说明）。 */
  function effectiveWorkspace(sub: string): string {
    return readMarker('.legacy-owner') === sub
      ? join(fixture.miqiHome, 'workspace')
      : join(fixture.miqiHome, 'accounts', sub, 'workspace');
  }

  /** 在给定工作区根下递归找这个文件名，返回绝对路径（找不到返回 null）。 */
  function findFile(root: string, name: string): string | null {
    if (!existsSync(root)) return null;
    const stack = [root];
    while (stack.length > 0) {
      const dir = stack.pop()!;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) stack.push(full);
        else if (entry.name === name) return full;
      }
    }
    return null;
  }

  /**
   * 走**应用自己的**文件接口读一次，返回读到了多少内容。
   *
   * 两个都别漏：
   * - `sessionKey`：路径落在 `sessions/` 下时桥侧明确拒绝「会话目录需带
   *   session_key 访问」，不带就返回 undefined（`sendSafe` 把错误吞了，表现为
   *   「读到的内容是 0」这种最像业务问题的假象）。
   * - `asBinary`：默认按文本读，`.pptx` 这类二进制会读成空。
   *
   * 读失败直接抛出（不吞）：读不到就是失败。
   */
  async function appReadSize(rel: string, sessionKey: string): Promise<number> {
    return await fixture.page.evaluate(
      async ([p, key]: [string, string]) => {
        const r = await (window as any).miqi.files.read(p, key, { asBinary: true });
        const b64 = typeof r?.data_base64 === 'string' ? r.data_base64.length : 0;
        const size = typeof r?.size === 'number' ? r.size : 0;
        return Math.max(size, b64);
      },
      [rel, sessionKey] as [string, string]
    );
  }

  /** 当前账号侧栏里的会话 key（产物挂在会话目录下，读它必须带上 key）。 */
  async function firstSessionKey(): Promise<string> {
    const list = await fixture.page.evaluate(() => (window as any).miqi.sessions.list());
    const keys = ((list?.sessions ?? []) as Array<{ key: string }>).map((s) => s.key);
    expect(keys.length, '该账号应至少有一个会话').toBeGreaterThan(0);
    return keys[0];
  }

  /** 绝对路径 → 应用文件接口要的 workspace 相对路径（POSIX 分隔符）。 */
  const BACKSLASH = String.fromCharCode(92);
  function relToWorkspace(root: string, file: string): string {
    return relative(root, file).split(BACKSLASH).join('/');
  }

  test(
    'A 跑 pptx 技能生成文件 → B 看不到 → 切回 A 文件仍在且应用能读到',
    { timeout: SKILL_TIMEOUT },
    async () => {
      const page = fixture.page;

      // ── A：登录 → 预授权工具 → 让技能真的生成一个 pptx ────────────────
      const subA = await login(ACCOUNT_A);
      await createNewConversation(page);
      // 技能会连续调多个工具，逐个点确认会把回合拖死；预授权与 pptx-generator
      // 那条既有用例同策略。
      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      // 但**计划确认卡**是另一个入口（#646-v2 的四入口之一），`addPermanent` 管不到：
      // 第一次跑就停在「已取消任务：用户未确认执行计划」。所以这里一边跑一边放行
      // 计划卡与工具审批弹窗，回合结束即停（与 plan-card.spec 同策略）。
      let turnDone = false;
      const autoDrive = (async () => {
        while (!turnDone) {
          try {
            const planConfirm = page.getByTestId('plan-confirm').first();
            if (await planConfirm.isVisible().catch(() => false)) {
              await planConfirm.click().catch(() => undefined);
            }
            const dialog = page.getByRole('alertdialog').first();
            if (await dialog.isVisible().catch(() => false)) {
              const allow = dialog.getByRole('button', { name: /允许一次|允许/ }).first();
              if (await allow.isVisible().catch(() => false)) {
                await allow.click().catch(() => undefined);
              }
            }
          } catch {
            /* 页面切走/关闭：结束循环 */
            return;
          }
          await page.waitForTimeout(500);
        }
      })();

      await sendMessage(
        page,
        `使用 pptx_write 工具创建一个两页的 PPT：file_path=${PPTX_NAME}，` +
          `slides=[{title:"账号隔离验证",content:"本地存储按账号划分"},` +
          `{title:"切换账号",content:"各账号的工作区与会话互不可见"}]。` +
          `创建成功后只回复一个字：成`
      );
      await waitForResponseComplete(page, 420_000);
      turnDone = true;
      await autoDrive;
      await shoot('A-1-skill-done');

      const assistantText = await page.getByTestId('chat-message-assistant').last().innerText();
      expect(assistantText, 'A 应拿到真实网关回复').not.toContain('运行时未启动');
      expect(assistantText, '技能回合不该停在「未确认执行计划」').not.toContain('未确认执行计划');

      // 产物落盘：`<workspace>/sessions/<key>/files/<name>`（先等它出现）。
      const wsA = effectiveWorkspace(subA);
      let pptxOnDisk: string | null = null;
      await expect
        .poll(
          () => {
            pptxOnDisk = findFile(wsA, PPTX_NAME);
            return pptxOnDisk !== null;
          },
          { timeout: 60_000, message: `A 的工作区里应出现 ${PPTX_NAME}` }
        )
        .toBe(true);
      expect(statSync(pptxOnDisk!).size, 'ppt 不该是空文件').toBeGreaterThan(0);
      console.log(`[1185-assets] A(sub=${subA}) 产物=${pptxOnDisk}`);

      // 应用自己能读到它（渲染层显示文件走的就是这条链路）。
      const relA = relToWorkspace(wsA, pptxOnDisk!);
      expect(
        await appReadSize(relA, await firstSessionKey()),
        'A 应能通过应用接口读到自己的产物'
      ).toBeGreaterThan(0);

      // ── 换到 B：既看不到 A 的会话，也找不到那个文件 ──────────────────
      await logout();
      const subB = await login(ACCOUNT_B);
      expect(subB).not.toBe(subA);
      await shoot('B-1-no-assets');

      const wsB = effectiveWorkspace(subB);
      expect(wsB, '两个账号的工作区根不该是同一个').not.toBe(wsA);
      expect(findFile(wsB, PPTX_NAME), 'B 的工作区里不该有 A 生成的 ppt').toBeNull();
      // 用户可见的那一层：B 的屏幕上看不到那个文件名。
      // （不比对会话 key：`desktop:default` 是默认哨兵 key，两个账号登录时都会各建
      // 一个同名会话，拿 key 比对会把「各有自己的哨兵会话」误报成泄漏。）
      expect(await page.locator('body').innerText(), 'B 的界面上不该出现 A 的产物名').not.toContain(
        PPTX_NAME
      );

      // ── 切回 A：文件还在，且应用仍然读得到（这就是「正常显示」）────────
      await logout();
      await login(ACCOUNT_A);
      await shoot('A-2-back-with-assets');

      const pptxAgain = findFile(effectiveWorkspace(subA), PPTX_NAME);
      expect(pptxAgain, '切回 A 后产物应仍在').not.toBeNull();
      expect(
        await appReadSize(
          relToWorkspace(effectiveWorkspace(subA), pptxAgain!),
          await firstSessionKey()
        ),
        '切回 A 后应用应仍能读到产物'
      ).toBeGreaterThan(0);

      // A 的会话也在（文件挂在会话目录下，会话不在就等于看不见）。
      const listA = await page.evaluate(() => (window as any).miqi.sessions.list());
      expect((listA?.sessions ?? []).length, '切回 A 后应看到自己的会话').toBeGreaterThan(0);
    }
  );
});
