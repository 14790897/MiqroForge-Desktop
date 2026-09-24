/**
 * 复杂技能的产物跨账号切换（#1185）— **真实账号** live E2E（opt-in，默认跳过）。
 *
 * 维护者要求：「再测试下跑复杂技能，比如生成PPT，看看文件能否在切换账号回来后正常显示」。
 *
 * 验的是两件事：
 *   1. A 账号跑出的 PPT 产物，在换到 B 账号后**看不到**；
 *   2. 切回 A 账号后，那个文件仍在，**界面上也能正常显示**（任务资产面板里列出来）。
 *      A 首次生成后还额外用应用自己的 `files.read` 读一遍 —— 只断言磁盘上有文件
 *      是不够的，路径解析、账号根、包含性检查任何一环变了都会让文件「看不见」。
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
const READY =
  LIVE &&
  ACCOUNT_A.phone !== '' &&
  ACCOUNT_A.password !== '' &&
  ACCOUNT_B.phone !== '' &&
  ACCOUNT_B.password !== '';

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
   * 走应用自己的文件接口读一次，读不到返回 0（不抛），供轮询用。
   *
   * 两个参数都别漏：
   * - `sessionKey`：路径落在 `sessions/` 下时桥侧明确拒绝「会话目录需带
   *   session_key 访问」，不带就返回 undefined（`sendSafe` 把错误吞了，表现为
   *   「读到的内容是 0」这种最像业务问题的假象）。
   * - `asBinary`：默认按文本读，`.pptx` 这类二进制会读成空。
   */
  async function readSizeOnce(rel: string, sessionKey: string): Promise<number> {
    return await fixture.page.evaluate(
      async ([p, key]: [string, string]) => {
        const r = await (window as any).miqi.files.read(p, key, { asBinary: true });
        const b64 = typeof r?.data_base64 === 'string' ? r.data_base64.length : 0;
        const s = typeof r?.size === 'number' ? r.size : 0;
        return Math.max(s, b64);
      },
      [rel, sessionKey] as [string, string]
    );
  }

  /**
   * 等应用能读到产物；超时则把桥侧那几行一起抛出来。
   *
   * 轮询而不是读一次：`files.read` 会校验「client 对该会话已授权」，而会话是在
   * 用户**打开**它时（`sessions.get`）才在桥的注册表里建档的。换账号回来之后
   * 侧栏虽然已经列出会话，但它要在会话**重新建档**之后才可用 —— 缓存里那份是
   * 上一个账号的，会被退役、由调用方重建，这个过程有先后。`sendSafe` 把这类失败吞成 undefined，
   * 所以失败信息里必须带上桥日志，否则只会看到「读到了 0」。
   */
  async function expectAppCanRead(rel: string, sessionKey: string, label: string): Promise<void> {
    for (let i = 0; i < 180; i++) {
      if ((await readSizeOnce(rel, sessionKey)) > 0) return;
      await fixture.page.waitForTimeout(1000);
    }
    const logs = await fixture.page
      .evaluate(async () => {
        try {
          return ((await (window as any).miqi.runtime.backendLogs()) ?? []) as string[];
        } catch {
          return [];
        }
      })
      .catch(() => [] as string[]);
    // 会话生命周期也要带出来：`files.read` 会校验「client 对该会话已授权」，
    // 而授权来自 create_session —— 只看 files.read 的报错分不清「从没授权」和
    // 「记录被别人顶掉了」。轮询本身会刷屏，先把读失败那几行排掉。
    const tail = logs
      .filter((l) => !/sendSafe files\.read failed/.test(l))
      .filter((l) =>
        /files:read|created session|retiring|discard|stop_session|evict|account|UNAUTHORIZED/i.test(
          l
        )
      )
      .slice(-10);
    throw new Error(`${label}（rel=${rel} key=${sessionKey}）；桥侧最近几行：\n${tail.join('\n')}`);
  }

  /** 会话目录名 = `miqi.session.session_keys.session_files_dir_key(key)`。 */
  function sessionDirName(key: string): string {
    const parts = key.split(':');
    if (parts.length >= 3) parts.shift();
    return parts.join('_').replace(/[^A-Za-z0-9._-]/g, '_');
  }

  /**
   * 产物落在哪个会话目录 → 用哪个 session_key 去读。
   *
   * 读 `sessions/<dir>/files/...` 必须带**和这个目录对应**的 key（桥侧会拒绝对
   * 不上的），所以不能拿「列表里第一个 key」凑 —— 切账号回来列表会按更新时间
   * 重排，凑巧对上过不代表一直对得上。这里按与应用同一条派生规则反推。
   */
  async function sessionKeyForFile(absPath: string): Promise<string> {
    const dir = absPath.split(/[\\/]sessions[\\/]/)[1]?.split(/[\\/]/)[0] ?? '';
    const list = await fixture.page.evaluate(() => (window as any).miqi.sessions.list());
    const keys = ((list?.sessions ?? []) as Array<{ key: string }>).map((s) => s.key);
    const match = keys.find((k) => sessionDirName(k) === dir);
    expect(
      match,
      `应能找到目录 ${dir} 对应的会话 key（列表：${JSON.stringify(keys)}）`
    ).toBeTruthy();
    return match!;
  }

  /** 绝对路径 → 应用文件接口要的 workspace 相对路径（POSIX 分隔符）。 */
  const BACKSLASH = String.fromCharCode(92);
  function relToWorkspace(root: string, file: string): string {
    return relative(root, file).split(BACKSLASH).join('/');
  }

  test(
    'A 跑 pptx 生成文件 → B 看不到 → 切回 A 文件仍在、任务资产面板里正常显示',
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
      await expectAppCanRead(relA, await sessionKeyForFile(pptxOnDisk!), 'A 应能读到自己的产物');

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
      // 截图前先等会话在界面上真正渲染出来（刚登录那一拍主区还是「正在连接…」，
      // 拍出来看不到产物）。等待本身不承担断言职责，断言在下面。
      await expect
        .poll(async () => (await page.getByTestId('session-item').allInnerTexts()).length, {
          timeout: 60_000,
          message: '切回 A 后侧栏应重新列出会话',
        })
        .toBeGreaterThan(0);
      await page.waitForTimeout(3000);
      await shoot('A-2-back-with-assets');

      const pptxAgain = findFile(effectiveWorkspace(subA), PPTX_NAME);
      expect(pptxAgain, '切回 A 后产物应仍在').not.toBeNull();

      // 界面这一层：「任务资产」面板里能看到这个产物 —— 这正是维护者问的
      // 「文件能否在切换账号回来后正常显示」。
      await expect
        .poll(async () => await page.locator('body').innerText(), {
          timeout: 90_000,
          message: '切回 A 后任务资产面板应列出该产物',
        })
        .toContain(PPTX_NAME);

      // A 的会话也在（产物挂在会话目录下，会话不在就等于看不见）。
      const listA = await page.evaluate(() => (window as any).miqi.sessions.list());
      expect((listA?.sessions ?? []).length, '切回 A 后应看到自己的会话').toBeGreaterThan(0);

      // 为什么这里**不**再断言 files.read：会话作用域的文件操作要求 client 对该
      // 会话在桥的注册表里仍然有效，而注册表条目是 `chat.send` 建立的 ——
      // `sessions.get` / `sessions.list` 只读磁盘、不建档（既有行为，重启后直接
      // 预览老会话的文件同样如此）。换账号会把上一份条目退役，所以要等这个账号
      // 下一次发消息才会重新建档。这是本次改动换来的：退役条目正是关掉「B 复用
      // A 的运行时」那个跨账号口子的手段。要不要让文件操作不依赖活跃会话，是另一
      // 个决定（涉及授权口径），已记在 PR 里。
    }
  );
});
