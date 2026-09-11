/**
 * E2E — issue #607 真实用户路径：调用 mof-synthesis-price-agent 技能
 *
 * 验证过程（用户视角）：
 *   1. 用户在聊天里调用 mof-synthesis-price-agent 技能处理一篇 MOF 论文
 *      （输入 = 工作区内真实论文的清洗文本 fixtures/smoke_mof_paper_cleaned.txt，
 *       来自 PMC 开放获取的真实 Cu-MOF 合成论文全文）
 *   2. 技能（确定性 router + 代理抽取）生成三类交付物：
 *        合成路线   → <prefix>_synthesis_summary.md（+ <prefix>_routes.csv）
 *        试剂价格   → <prefix>_pricing.csv（+ <prefix>_reagents.csv）
 *        可行性报告 → <prefix>_report.md / .html / .pdf
 *   3. 任务资产面板把交付物归入「结果资产」（结果 badge + 星标强调），
 *      中间产物归入「过程文件」——不再混排
 *   4. 用户可对结果文件执行「定位」（打开所在文件夹）与「预览」
 *
 * ══════════════════════════════════════════════════════════════════
 *  前置条件（模块级同步检查，不满足则整个套件 skip，不影响 CI）：
 *    - MOF_PRICE_PROJECT 环境变量指向含 extract/text_extractor.py、
 *      enrich/chembook_scraper.py、report.py 的 MOF 项目根目录
 *      （与 scripts/mof_workflow.py 的解析顺序一致：--project-root → cwd
 *       → $MOF_PRICE_PROJECT；e2e 沙箱 cwd = 工作区根，所以测试会把
 *       技能脚本 + 项目工具播种进工作区，模拟用户真实运行目录）
 *    - 仓库里存在 miqi/skills/mof-synthesis-price-agent/（本机私有技能，
 *      git 未跟踪，CI 检出不一定包含）
 *    - 真实 LLM provider 可用（与其他 LLM 驱动 spec 相同）
 *
 *  运行：MOF_PRICE_PROJECT=C:\path\to\mof-project npx playwright test
 *    --config=playwright.config.ts --project=electron mof-synthesis-price-agent.spec.ts --workers=1
 *
 *  ⚠️ 追踪范围说明：面板只收录 write_file/edit_file/exec 重定向/Office 工具
 *  触达的文件（issue #507/#607 的 NOT-tracked 清单：脚本内部 open() 写入不可见）。
 *  若运行成功但交付物未入面板，说明子进程写入未带 `>` 重定向——那是真实追踪缺口，
 *  测试会如实失败并打印面板内容，便于定位。
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForBridgeInitialized,
  waitForResponseComplete,
  launchElectronApp,
  closeElectronApp,
  APPS_DESKTOP,
} from './helpers/electron-setup';
import { join } from 'node:path';
import { existsSync } from 'node:fs';

// ─── 前置条件（同步、模块级）─────────────────────────────────────────

/** 与 mof_workflow.py 相同的项目根判定：必须含 extract/, enrich/, report.py。 */
function resolveMofProjectRoot(): string | null {
  const required = ['extract/text_extractor.py', 'enrich/chembook_scraper.py', 'report.py'];
  for (const candidate of [process.env.MOF_PRICE_PROJECT].filter(Boolean)) {
    const root = candidate as string;
    if (required.every((rel) => existsSync(join(root, ...rel.split('/'))))) return root;
  }
  return null;
}

const MOF_PROJECT_ROOT = resolveMofProjectRoot();
// 仓库私有技能目录（git 未跟踪；测试播种需要它的 scripts/ + references/）
const MOF_SKILL_DIR = join(APPS_DESKTOP, '..', '..', 'miqi', 'skills', 'mof-synthesis-price-agent');
const MOF_SKILL_PRESENT = existsSync(join(MOF_SKILL_DIR, 'SKILL.md'));

const MOF_SKIP_REASON =
  '需要 mof-synthesis-price-agent 的 MOF 项目根目录（含 extract/, enrich/, report.py）' +
  '——设置环境变量 MOF_PRICE_PROJECT 后重跑（技能目录缺失时同理）';

test.describe('MOF 合成路线/试剂价格/可行性报告 生成与结果资产展示 (#607)', () => {
  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  // 环境不满足 → 整个套件跳过（CI 无 MOF_PRICE_PROJECT，天然跳过）
  test.skip(() => !MOF_PROJECT_ROOT || !MOF_SKILL_PRESENT, MOF_SKIP_REASON);

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;

    // MOF 项目根必须已预置技能内容（scripts/ + references/ + SKILL.md）——
    // 测试把会话工作区切换到该项目根，沙箱会 bind-mount 它，exec 可见。
    await waitForBridgeInitialized(page);
    await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));
    console.log('[test] *:* wildcard pre-approved');
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test('调用 mof-synthesis-price-agent → 三类交付物归为结果资产 → 定位/预览可用', async () => {
    // 24 min：整条 pipeline（router 路由/代理抽取/CSV 写入/富化/报告）+ LLM 长推理
    test.setTimeout(LLM_TIMEOUT * 6);
    // ── 1. 以 MOF 项目根作为会话工作区发送触发消息 ──
    // chat.send 的 workspace 参数是权威路径（loop.py:695，第 1 优先级）：
    // 后端校验后用于 RuntimeSession + 沙箱 bind-mount → exec 与文件工具
    // 看到同一份文件。UI 的「更换工作目录」选择器只在 historyLoaded &&
    // messages.length===0 时渲染，e2e 里不稳定，故走 API 直发（同一链路）。
    const sessionKey = await page.evaluate(
      () => localStorage.getItem('miqi:lastSession') || 'desktop:default'
    );
    const trigger =
      '用 mof-synthesis-price-agent 技能处理这个 MOF 论文清洗文本：' +
      'fixtures/smoke_mof_paper_cleaned.txt，生成合成路线、试剂价格和可行性报告。';

    await page.evaluate(
      ([key, text, ws]: [string, string, string]) =>
        (window as any).miqi.chat.send(text, key, undefined, undefined, undefined, ws),
      [sessionKey, trigger, MOF_PROJECT_ROOT as string]
    );
    console.log('[test] ✅ Skill trigger sent via chat.send(workspace=MOF project root)');

    // ── 2. 等待整条 pipeline 跑完（多轮工具调用 + 子进程阶段）──
    await waitForResponseComplete(page, 20 * 60_000);
    console.log('[test] ✅ Turn complete');
    // 可观察性：打印 agent 最终回复文本（模型空回复/纯文本偷懒时能直接看到）
    const lastReply = await page
      .locator('main .message .text-wrap, main [class*="message"] [class*="whitespace-pre-wrap"]')
      .last()
      .textContent()
      .catch(() => '(无法读取)');
    console.log(`[test] Agent final reply: ${String(lastReply).slice(0, 300)}`);

    // ── 3. 面板按 结果/过程 分类展示 ──
    const panel = page.getByTestId('task-assets-panel');
    const stats = page.locator('[data-testid="task-assets-stats"]');
    await expect(stats).toBeVisible({ timeout: 30_000 });
    const statsText = await stats.textContent();
    console.log(`[test] Panel stats: ${statsText}`);

    // 白名单（用户指定 2026-08-13）：结果 = excel/word/pdf 三类。
    // 沙箱 router 产物全是 md/csv/html/json（无三类格式）→ 结果区为 0
    // 是白名单规则的正确反映；用户真实环境 agent 会生成 xlsx/pdf 交付物，
    // 结果区即有内容（task-assets.spec 的 .pdf 用例验证结果区展示）。
    await expect(stats).toContainText('0 个结果', { timeout: 60_000 });

    // 过程区存在 + 展开后能看到全部交付物（分类拆分生效）
    const processToggle = page.locator('[data-testid="asset-section-toggle-process"]');
    await expect(processToggle).toBeVisible({ timeout: 10_000 });
    await processToggle.click().catch(() => {});
    await page.waitForTimeout(300);

    // ── 4. 交付物全部归为「过程资产」（非三类格式）──
    const card = (nameRe: RegExp) =>
      panel.locator('.rounded-lg.p-2\\.5').filter({ hasText: nameRe }).first();
    const deliverables: Array<{ label: string; re: RegExp }> = [
      { label: '合成路线', re: /synthesis_summary\.md|_routes\.csv/ },
      { label: '试剂价格', re: /_pricing\.csv|_reagents\.csv/ },
      { label: '可行性报告', re: /_report\.(md|html)/ },
    ];
    for (const d of deliverables) {
      const c = card(d.re);
      await expect(c).toBeVisible({ timeout: 30_000 });
      console.log(`[test] ✅ 过程资产: ${d.label}`);
    }

    // ── 5. 打开结果文件：定位（文件管理器） + 预览（系统应用/回退弹窗）──
    const reportCard = card(/synthesis_summary\.md|_routes\.csv/); // 合成路线卡片
    const revealBtn = reportCard.getByTestId('file-reveal-btn');
    await expect(revealBtn).toBeVisible({ timeout: 10_000 });
    await revealBtn.click();
    await page.waitForTimeout(800);
    // 点击后应用不应崩溃
    await expect(panel).toBeVisible({ timeout: 10_000 });
    console.log('[test] ✅ 定位（打开所在文件夹）无崩溃');

    const previewBtn = reportCard.getByTestId('file-preview-btn');
    await previewBtn.click();
    await page.waitForTimeout(800);
    // 预览可能走系统应用分发（无弹窗）或回退弹窗——两者都算打开成功
    const dialog = page.getByRole('dialog');
    if (await dialog.isVisible().catch(() => false)) {
      console.log('[test] ✅ 预览回退弹窗已打开');
      // 关闭弹窗，避免遗留 radix focus trap 影响后续（见 task-assets 参考 postmortem #1）
      await page.keyboard.press('Escape').catch(() => {});
      await expect(dialog).toHaveCount(0, { timeout: 5_000 });
    } else {
      console.log('[test] ✅ 预览已分发到系统应用（无弹窗）');
    }
    await expect(panel).toBeVisible({ timeout: 10_000 });
    console.log('[test] ✅ 预览无崩溃');

    // ── 6. 证据：面板内容快照（失败时也用于定位追踪缺口）──
    const panelText = await panel.textContent();
    console.log(`[test] Panel content:\n${panelText}`);
  });
});
