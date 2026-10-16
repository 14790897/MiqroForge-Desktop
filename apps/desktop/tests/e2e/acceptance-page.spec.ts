/**
 * 阶段 3-4 验收(SURE)面板 E2E —— 真实 SURE、真实 AI,零 mock。
 *
 * 前置:安装 SURE(查找链 SURE_BIN → PATH → 平台默认位置;CI 的
 * electron-e2e 会装官方 v0.1.2 的 Linux 产物)。找不到时如实 skip,
 * 绝不 mock 顶替。
 *
 * 测试项目是真实的:一个含 `# TODO` 的 py 文件会让确定性检查产出 1 条
 * 真实 finding(not_green),由此走通「检查 → 修复契约 → 交给 AI 真修 →
 * 自动复核」全链路。运行环境不带 SURE_OPENAI_API_KEY 时(2026-10-13 起
 * 白名单放行该变量、面板随用户环境启用模型评估)check 如实呈现「provider
 * 已配置但 key 未配置」或「未配置分析」;带 key 时(即下方模型评估 describe
 * 的前提——用户级 analysis 配置 + key)同一 TODO 会经第 8 阶段多出一条
 * 「Model assessment」条目,发现项计数如实分流(2026-10-14 实测:同一项目
 * 1 条 → 2 条)——expectedFindings 管计数,不抠死数字也不放宽。
 * 修复契约计数不随之变:模型条目不点名检查,不产契约(实测 repairs 恒 1)。
 * 带 key 的模型评估路径由下方第二个 describe 覆盖(环境无 key 时如实 skip,
 * 绝不 mock)。
 *
 * 已移除的两个场景(理由如实登记在 PR 描述):
 *   - 「低版本 SURE → 不兼容横幅」:构造低版本必须 mock 二进制(禁令);
 *     版本比较逻辑由 tests/runtime/test_sure_report.py 的 version_at_least
 *     四态单测覆盖。
 *   - 「运行中可取消」:无 provider 的真实检查在秒级内完成,不存在稳定的
 *     取消窗口;取消语义由 sure_task_runtime 的 pytest(进程树回收、取消
 *     后状态)覆盖。
 *
 * Run: cd apps/desktop && npm run build && npx playwright test \
 *      --config=playwright.config.ts --project=electron acceptance-page
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  browserLogin,
  LLM_TIMEOUT,
  closeElectronApp,
  createNewConversation,
  launchElectronApp,
  sendMessage,
  waitForBridgeInitialized,
  waitForResponseComplete,
} from './helpers/electron-setup';
import { findRealSureBin, runSureJson, sureSupportsMiqi } from './helpers/sure-binary';
import { seedGatewayCredentials } from './helpers/real-ai';

/** 自建临时目录清理:Windows 下刚退出的子进程句柄可能瞬时占用,重试+容忍。 */
function cleanupDir(dir: string): void {
  try {
    rmSync(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  } catch {
    // TEMP 里的残留由系统兜底;清理失败绝不连累用例结果
  }
}

/**
 * 临时 MIQI_HOME 的收尾策略,一处定义。
 *
 * CI 用 MIQI_E2E_KEEP_HOME=1 留住它 ——「Bridge ingest anomalies」「Dump bridge logs」
 * 两步读的正是 `<home>/workspace/logs/bridge-*.log`,无条件删掉等于把它们变成空转。
 * 此前这段策略在本文件里写了三遍、写法还各不相同(含一处把条件写反),改一处漏一处。
 */
function disposeMiqiHome(miqiHome: string): void {
  if (!miqiHome) return;
  if (process.env.MIQI_E2E_KEEP_HOME) {
    console.log(`[test] MIQI_E2E_KEEP_HOME=1,保留 ${miqiHome}`);
    return;
  }
  cleanupDir(miqiHome);
}

/**
 * QRAFT_LIVE=1 时先真实登录(生产账号)。本机跑必须:临时 MIQI_HOME 没有登录态,
 * 发送会被登录门拦(chat.send 到不了桥接),修复派发也就没有会话可挂靠。
 * CI 的 sure-e2e 注入同一凭据(与 electron-e2e 的云端登录 live 用例同源);
 * 变量未设时原样继续 —— 走 CI 注入的 config.json provider 的场景不需要登录。
 */
async function loginWhenLive(p: Page, app: ElectronApplication): Promise<void> {
  if (process.env.QRAFT_LIVE !== '1') return;
  const loggedIn = p.getByText('已登录');
  if (!(await loggedIn.isVisible({ timeout: 5_000 }).catch(() => false))) {
    await browserLogin(p, app, process.env.QRAFT_PHONE ?? '', process.env.QRAFT_PASSWORD ?? '');
  }
  await expect(loggedIn).toBeVisible({ timeout: 90_000 });
}

async function openAcceptance(p: Page): Promise<void> {
  const settingsBtn = p.locator('[data-testid="nav-system-settings"]');
  await expect(settingsBtn).toBeVisible({ timeout: 15_000 });
  await settingsBtn.click();
  const tab = p.getByRole('tab', { name: /验收/ }).first();
  await expect(tab).toBeVisible({ timeout: 10_000 });
  await tab.click();
  await expect(p.getByRole('heading', { name: '验收(SURE)' })).toBeVisible({ timeout: 10_000 });
}

const NO_SURE_SKIP = '未安装 SURE(查找链 SURE_BIN → PATH → 默认位置)——E2E 不 mock 外部依赖';

/**
 * 发现项数量随真实环境如实分流:确定性 TODO 检查恒产 1 条;环境带
 * SURE_OPENAI_API_KEY(即下方模型评估 describe 的前提——用户级 analysis
 * 配置 + key)时,第 8 阶段对同一候选再产出一条「Model assessment」条目,
 * 计数如实为 2(2026-10-14 实测,与模型 describe 的断言同一前提)。key 在位
 * 而模型评估没跑通,这里与模型 describe 一同红——不美化。
 */
const expectedFindings = (): number => (process.env.SURE_OPENAI_API_KEY ? 2 : 1);

test.describe.serial('验收(SURE)面板 E2E · 真实 SURE', () => {
  let electronApp: ElectronApplication | null = null;
  let page: Page;
  let sureBin: string | null = null;
  let project = '';
  let miqiHome = '';

  test.beforeAll(async () => {
    sureBin = findRealSureBin();
    if (!sureBin) return; // 各用例如实 skip;不启动 app

    // 真实测试项目:含 TODO 的 py 文件 → 确定性检查产出 1 条真实 finding,
    // 「交给 AI 修复」有真实素材可修(清理 TODO 后 recheck 将关闭该发现项)
    project = mkdtempSync(join(tmpdir(), 'sure-accept-'));
    writeFileSync(
      join(project, 'README.md'),
      '# acceptance probe\n\nA tiny project checked by the real SURE binary.\n'
    );
    writeFileSync(
      join(project, 'pay.py'),
      '# TODO: implement the payment flow, not finished yet\nraise NotImplementedError("payment")\n'
    );

    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
    // 「交给 AI 修复」是真实子代理。凭据两条路:QRAFT_LIVE=1 时先真实登录
    // (本机与 sure-e2e CI 同一凭据,走生产账号的平台网关路由);未设时沿用
    // 注入的 config.json provider(网关凭据文件复制不到时静默跳过)。
    seedGatewayCredentials(miqiHome);
    await loginWhenLive(page, fixture.electronApp);
    await waitForBridgeInitialized(page, 30);
    // 修复子代理挂靠一个真实聊天会话(空会话不落盘,spawn 需要真实会话)
    await createNewConversation(page);
    // 这一轮是真实模型经平台网关,而「网关慢」是本仓记录过的正常情形:**一次**非流式
    // 回退实测可达 600s(同文件「完整修复闭环」那条的 30 分钟窗口就是照这个值定的)。
    // 默认 120s 会把「网关慢」判成失败 —— 2026-10-15 本地与 CI 都在这里红(报
    // `回合在 120000ms 内没有结束`),而同一轮里桥接 spec 的回合是正常的。
    // 给足预算;并把 beforeAll 自身的超时一并抬高,否则 hook 会先被项目 600s 上限掐掉,
    // 报错又退化成看不出原因的超时。
    test.setTimeout(LLM_TIMEOUT * 4);
    await sendMessage(page, '你好');
    await waitForResponseComplete(page, LLM_TIMEOUT * 3);
  });

  test.afterAll(async () => {
    if (electronApp) await closeElectronApp(electronApp);
    disposeMiqiHome(miqiHome);
    // 清理本轮真实 check/recheck 写进 SURE 记录库的会话(真实命令,尽力而为):
    // ① check/recheck 以测试项目为 project_root;② 桥接事件以会话工作区为 project_root
    if (sureBin) {
      for (const scope of [project, miqiHome ? join(miqiHome, 'workspace') : '']) {
        if (!scope) continue;
        const del = runSureJson(sureBin, ['history', 'delete', '--project', scope]);
        console.log(`[test] 清理记录(${scope}): outcome=${del?.outcome ?? '<无输出>'}`);
      }
    }
    if (project) cleanupDir(project);
  });

  test('健康行(真实版本)→ 真实检查报告(结构如实)', async () => {
    if (!sureBin) test.skip(true, NO_SURE_SKIP);
    await openAcceptance(page);
    // 真实二进制的版本(不绑死具体数字,CI 装的官方版与 fork 都应通过)
    await expect(page.getByText(/SURE \d+\.\d+\.\d+/)).toBeVisible({ timeout: 15_000 });
    // 能力提示与真实 doctor 输出一致(如实呈现):fork 构建含 miqi 集成 → 无提示;
    // 官方构建 → "不支持 MiQi 事件记录"提示可见(CI 装的官方版走这一支)
    if (sureSupportsMiqi(sureBin!)) {
      await expect(page.getByTestId('acceptance-no-miqi')).toHaveCount(0);
    } else {
      await expect(page.getByTestId('acceptance-no-miqi')).toBeVisible({ timeout: 15_000 });
    }

    await page.getByTestId('acceptance-project-input').fill(project);
    await page.getByTestId('acceptance-start').click();

    // 真实检查(无 provider 时秒级完成;给足余量)
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 120_000 });
    // 真实结论因 SURE 构建而异:官方 0.1.2 release 判「检查不足」(TODO 检查
    // 未能运行);fork 构建(main tip)能真跑该检查、判「需要关注」(not_ready)。
    // 断言不锁具体某一版,只要求如实呈现其一——绝不美化
    await expect(page.getByText(/检查不足,无法判断|需要关注/).first()).toBeVisible({
      timeout: 15_000,
    });
    // 真实的发现项:1 条(TODO/FIXME 确定性检查);带 key 的环境另有模型评估条目,
    // 计数随环境分流(expectedFindings)
    await expect(page.getByText(`发现的问题(${expectedFindings()})`)).toBeVisible({
      timeout: 15_000,
    });
    // 逐阶段明细(真实 12 阶段记录)
    await expect(page.getByText('核查阶段')).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: 'test-results/acceptance-report.png', fullPage: true });

    // 2026-10-14 回归:切走再切回,完成态的报告必须还在——此前一离开面板
    // 再回来,报告与项目输入全部丢失(报告只活在组件 state 里)。
    await page.getByRole('button', { name: /返回任务/ }).click();
    await openAcceptance(page);
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(`发现的问题(${expectedFindings()})`)).toBeVisible({
      timeout: 15_000,
    });
  });

  test('真实修复契约 → 真实复核对比(未修复 → still_open)', async () => {
    if (!sureBin) test.skip(true, NO_SURE_SKIP);
    // serial:承接上一条的真实检查报告态
    await page.getByTestId('acceptance-repair').click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText('修复契约(1)')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('必须修复:').first()).toBeVisible();
    // 注:"禁止走捷径"段只在契约含 forbidden_shortcuts 时渲染(随 SURE 构建
    // 与内容而异,官方 0.1.2 的契约有、fork 的这份没有)——不锁它
    await page.screenshot({ path: 'test-results/acceptance-repair.png', fullPage: true });

    // 复核:尚未修复 → 真实对比应如实呈现 still_open(不关闭任何发现)
    await page.getByTestId('acceptance-recheck').click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(/发现项只在修复契约点名的检查/)).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: 'test-results/acceptance-recheck.png', fullPage: true });
  });

  test('交给 AI 修复(真实子代理)→ 自动复核对比', async () => {
    if (!sureBin) test.skip(true, NO_SURE_SKIP);
    // serial:上一条结束时 envelope 是 recheck(对比态;该配置下检查"未能运行",
    // 结果如实呈现为「无法确认」,因此没有 still_open 入口)——点「重新核查」
    // 回到 check 报告态,生成契约,再交给 AI 修复
    await page.getByRole('button', { name: /重新核查/ }).click();
    await expect(page.getByTestId('acceptance-report')).toBeVisible({ timeout: 120_000 });
    await expect(page.getByTestId('acceptance-repair')).toBeVisible({ timeout: 15_000 });
    await page.getByTestId('acceptance-repair').click();
    await expect(page.getByText('修复契约(1)')).toBeVisible({ timeout: 60_000 });
    await page.getByTestId('acceptance-fix').click();
    // 真实子代理"已启动"即验证派发链路(通知出现);它实际跑多久、成败如何是
    // 真实系统的运行时行为(实测:同一契约下可能数分钟仍在跑)——不赌它的
    // 时长;契约态的手动复核按钮一直在,两条路径在对比视图汇合。
    // 窗口用 LLM_TIMEOUT:派发那一下 app 正忙(建会话、起子代理),15s 在 sure-e2e
    // 那种 4 并发真实 Electron 的负载下偏紧(2026-10-15 本地就是卡在这 15s)。
    await expect(page.getByTestId('acceptance-fix-notice')).toBeVisible({ timeout: LLM_TIMEOUT });
    await expect(page.getByTestId('acceptance-fix-notice')).toContainText(
      /修复子代理已启动|修复子代理已完成|修复子代理未成功完成/,
      { timeout: LLM_TIMEOUT }
    );
    const recheckBtn = page.getByTestId('acceptance-recheck');
    if (await recheckBtn.isVisible().catch(() => false)) {
      await recheckBtn.click();
    }
    await expect(page.getByText(/发现项只在修复契约点名的检查/)).toBeVisible({ timeout: 300_000 });
    await page.screenshot({ path: 'test-results/acceptance-fix-flow.png', fullPage: true });
  });
});

const NO_KEY_SKIP =
  '本机未设置 SURE_OPENAI_API_KEY——真实模型评估无从跑起,E2E 不 mock 外部依赖,如实跳过';

/**
 * 模型评估(面板路径,2026-10-13 接入):`SURE_OPENAI_API_KEY` 已进面板
 * spawn 白名单,检查随用户环境启用模型评估。真实前置:①运行环境带该 key
 * (用户自己的 DeepSeek 凭据);②用户级 analysis 配置已设
 * (provider=openai_compatible + endpoint/model);③SURE 构建含模型评估
 * 阶段(fork ≥ 0.1.3)。任一不满足时如实 skip 或如实呈现,绝不 mock。
 */
test.describe('验收(SURE)面板 E2E · 模型评估(需真实 key)', () => {
  test('面板检查 → 第 8 阶段真实评估 + Model assessment 发现项', async () => {
    // 本用例串了三次真实等待:报告 180s + 两次 LLM_TIMEOUT(各 240s)= 660s,超过
    // electron 项目 600s 的默认用例上限(playwright.config.ts)。不抬高上限的话,
    // 第三条断言的窗口永远走不满,失败会显示成 "Test timeout exceeded" 而不是断言
    // 本身的错,还会白搭一次 Electron 重启 + 真实 SURE check 的重试。仓库既有写法
    // 同此(delivery-path-truth / session-workspace-isolation / task-assets 均
    // test.setTimeout(LLM_TIMEOUT * 2))。
    test.setTimeout(LLM_TIMEOUT * 2);
    if (!process.env.SURE_OPENAI_API_KEY) test.skip(true, NO_KEY_SKIP);
    const sureBin = findRealSureBin();
    if (!sureBin) test.skip(true, NO_SURE_SKIP);

    const project = mkdtempSync(join(tmpdir(), 'sure-accept-model-'));
    writeFileSync(
      join(project, 'pay.py'),
      '# TODO: implement the refund flow, not finished yet\nraise NotImplementedError("refund")\n'
    );

    const fixture = await launchElectronApp();
    try {
      await openAcceptance(fixture.page);
      await fixture.page.getByTestId('acceptance-project-input').fill(project);
      await fixture.page.getByTestId('acceptance-start').click();

      await expect(fixture.page.getByTestId('acceptance-report')).toBeVisible({
        timeout: 180_000,
      });
      // 第 8 阶段真实跑了模型(flagged / noise 两种判定都算"模型被调用")。
      // 这两条都是 SURE 第 8 阶段的真实模型评估落地 —— LLM 绑定的等待，用 LLM_TIMEOUT。
      // 2026-10-14 实测:120s 窗口下首跑等不到「Model assessment:」(失败瞬间页面快照为
      // 「已核查 1 · 未能运行 0 · 未解决问题 1 / Can fix later 未解决」，即评估条目还没渲染
      // 出来)，重试才过。断言不变，只是把窗口给够。
      await expect(fixture.page.getByText(/assessed 1 completeness candidate/)).toBeVisible({
        timeout: LLM_TIMEOUT,
      });
      // 用户要看的效果:报告里出现模型评估条目。用户环境实测配置的是
      // deepseek-flash,判该 TODO 为 problem 是稳定行为;若模型偶判 noise,
      // 该断言如实失败并暴露,不美化。
      await expect(fixture.page.getByText(/Model assessment:/).first()).toBeVisible({
        timeout: LLM_TIMEOUT,
      });
      await fixture.page.screenshot({
        path: 'test-results/acceptance-model-assessment.png',
        fullPage: true,
      });
    } finally {
      await closeElectronApp(fixture.electronApp);
      const del = runSureJson(sureBin, ['history', 'delete', '--project', project]);
      console.log(`[test] 清理记录(${project}): outcome=${del?.outcome ?? '<无输出>'}`);
      cleanupDir(project);
      disposeMiqiHome(fixture.miqiHome);
    }
  });
});

const FULL_REPAIR_SKIP =
  '完整修复闭环需要真实子代理(分钟级)跑完——默认如实跳过,设 MIQI_E2E_FULL_REPAIR=1 显式启用';

/**
 * 完整修复闭环:核查 → 生成契约 → 交给 AI 修复(真实子代理)→ 等它修完 →
 * 自动复核对比 → 契约点名的检查真通过后,发现项被关闭(「已关闭」徽标)。
 *
 * 与上方只验证"派发成功"的用例互补:这条赌真实子代理的完整运行(时长与
 * 成败都是真实系统的行为),默认 skip、由 MIQI_E2E_FULL_REPAIR=1 显式开启;
 * 等待期间顺手批准任何弹出来的审批卡,真实结局(完成/未成功完成)如实分流。
 */
test.describe('验收(SURE)面板 E2E · 完整修复闭环(需真实子代理跑完)', () => {
  test('子代理修完 → 自动复核 → 发现项关闭', async () => {
    test.setTimeout(2_400_000);
    if (!process.env.MIQI_E2E_FULL_REPAIR) test.skip(true, FULL_REPAIR_SKIP);
    const sureBin = findRealSureBin();
    if (!sureBin) test.skip(true, NO_SURE_SKIP);

    const project = mkdtempSync(join(tmpdir(), 'sure-accept-fullfix-'));
    writeFileSync(
      join(project, 'pay.py'),
      '# TODO: implement the payment flow, not finished yet\nraise NotImplementedError("payment")\n'
    );

    const fixture = await launchElectronApp();
    try {
      // 凭据通道二选一:默认复制平台网关凭据(有凭据文件 → 网关路由);
      // 设 MIQI_E2E_NO_GATEWAY_SEED=1 则不复制——与 CI 同构,模型走
      // config.json 的自带 provider(直连供应商,绕开平台网关)。
      if (!process.env.MIQI_E2E_NO_GATEWAY_SEED) seedGatewayCredentials(fixture.miqiHome);
      // 同第一个 describe:QRAFT_LIVE=1(本机 / sure-e2e CI)先真实登录——
      // 否则发送被登录门拦、没有会话,派发必然 SURE_NO_SESSION(见下方 notice 注释)
      await loginWhenLive(fixture.page, fixture.electronApp);
      await waitForBridgeInitialized(fixture.page, 30);
      // 修复子代理挂靠真实会话(阶段 4 的既有前提)
      await createNewConversation(fixture.page);
      await sendMessage(fixture.page, '你好');
      await waitForResponseComplete(fixture.page);

      await openAcceptance(fixture.page);
      await fixture.page.getByTestId('acceptance-project-input').fill(project);
      await fixture.page.getByTestId('acceptance-start').click();
      await expect(fixture.page.getByTestId('acceptance-report')).toBeVisible({ timeout: 120_000 });
      await fixture.page.getByTestId('acceptance-repair').click();
      await expect(fixture.page.getByText('修复契约(1)')).toBeVisible({ timeout: 60_000 });
      await fixture.page.getByTestId('acceptance-fix').click();

      const notice = fixture.page.getByTestId('acceptance-fix-notice');
      // 窗口提到 LLM_TIMEOUT 只为与前一处口径一致。**但这条不是窗口问题**:
      // 2026-10-15 本地实测给到 240s 也等不到 notice,当天查清并修掉了根因——
      // 那一轮 fixture 没有登录态:发送在 UI 侧被拦(chat.send 一次都没到桥接),
      // 核查与修复契约照常跑完,而 sure.fix.start 因「没有可用的聊天会话,且自动
      // 建会话失败(No API key configured)」被拒,派发根本没被受理。现在
      // QRAFT_LIVE=1 时上面的 loginWhenLive 会先真实登录(本机与 sure-e2e 同一
      // 凭据);CI 上该用例能跑到「已关闭」并(重试后)通过。
      await expect(notice).toBeVisible({ timeout: LLM_TIMEOUT });

      // 等真实子代理:完成/未成功完成如实分流,期间顺手批准审批卡。
      // 窗口 30 分钟——2026-10-13 实测:网关慢时一次非流式回退可达 600s,
      // 720s 的窗口会在子代理仍在工作时拔线,把它误判成"未完成"。
      const deadline = Date.now() + 1_800_000;
      let verdict = '';
      while (Date.now() < deadline) {
        for (const name of ['持久允许', '永久允许', '允许']) {
          const btn = fixture.page.getByRole('button', { name });
          if (
            await btn
              .first()
              .isVisible()
              .catch(() => false)
          ) {
            await btn
              .first()
              .click()
              .catch(() => {});
            break;
          }
        }
        const text = (await notice.textContent().catch(() => '')) ?? '';
        if (text.includes('已完成')) {
          verdict = 'completed';
          break;
        }
        if (text.includes('未成功完成')) {
          verdict = 'failed';
          break;
        }
        await fixture.page.waitForTimeout(3000);
      }
      const finalNotice = (await notice.textContent().catch(() => '')) ?? '';
      expect(verdict, `子代理结局(notice=${finalNotice})`).toBe('completed');

      // 完成 → 自动复核(对比)落地
      await expect(fixture.page.getByText('复审对比')).toBeVisible({ timeout: 180_000 });
      // 契约点名的检查真通过 → 发现项关闭(「已关闭」),而不是"未解决"。
      // 用 LLM_TIMEOUT 而不是 120s:这条自动复核是 SURE 的完整一轮 recheck,而
      // sure-e2e 给它配了真实模型评估(第 8 阶段),是 LLM 绑定的等待 —— 2026-10-14
      // 实测首跑在 120s 窗口里没等到、重试才过(flaky),同一条用例里前一步「复审对比」
      // 都给了 180s,这一步反而更短属于窗口没给够,不是语义变松(仍然必须真出现「已关闭」)。
      await expect(fixture.page.getByText('已关闭').first()).toBeVisible({ timeout: LLM_TIMEOUT });
      await fixture.page.screenshot({
        path: 'test-results/acceptance-fix-complete.png',
        fullPage: true,
      });
    } finally {
      await closeElectronApp(fixture.electronApp);
      const del = runSureJson(sureBin, ['history', 'delete', '--project', project]);
      console.log(`[test] 清理记录(${project}): outcome=${del?.outcome ?? '<无输出>'}`);
      cleanupDir(project);
      disposeMiqiHome(fixture.miqiHome);
    }
  });
});
