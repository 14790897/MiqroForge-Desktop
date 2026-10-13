/**
 * E2E — #1240 审批卡「输入框内联形态」。
 *
 * 真实链路：mock provider（scripts/mock_openai.py 的「写授权」分支）→ 真 Electron →
 * 真 bridge → 真 PermissionEngine 的 file_write 审批 → 真 ApprovalModal。
 *
 * 为什么用 file_write 而不是 exec 触发：CI 的 config 里 `agents.commandApproval.enabled=false`，
 * `effective_approval_bypass()` 会把 `bypass_command_approval` 强制为 True —— exec 类审批
 * 在 CI 根本不弹（见 CI-COVERAGE.md 对 approval-persistence 的登记）。file_write 只看
 * `bypass_file_write_approval`，不受该开关影响；`launchElectronApp(..., { bypassAll: false })`
 * 又会删掉 bypass_file_write_approval，所以两种环境下行为一致。
 *
 * 也刻意**不开** `tools.restrictToWorkspace`：boundary_enforced 只在 restrict_to_workspace
 * 或 WSL 沙箱下触发，关掉它 #864 的写授权卡就不会出现，主角是本 PR 改的审批卡。同理刻意
 * 不调 `approvals.addPermanent()` —— 那会直接把这张卡压掉。
 *
 * 覆盖（均为环境无关的相对断言，CI 上窗口尺寸/字体不同也成立）：
 *   1. 内联形态：卡贴在输入框内、占满内宽、四档一行、无橙色描边、可访问名正确、
 *      附件插槽随审批让位（仍在 DOM 里）、挂起期间输入框隐藏、⏎ 放行后产物写盘且
 *      焦点归还输入框；
 *   2. 跨页生命周期：聊天页上的内联卡，用户切到设置页后必须仍**可见**（回落居中模态）
 *      —— App.tsx 切页只给聊天区加 `hidden`、不卸载 ChatConsole，插槽元素仍在 DOM 里，
 *      只判断「插槽存在」会把卡片投进 display:none 的容器里、审批彻底看不见；同时
 *      Tab/Shift+Tab 要圈在对话框内，resolve 后焦点不得落进任何未渲染元素。
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *      --project=electron approval-card-inline.spec.ts --workers=1
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import type { ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { existsSync, readFileSync, mkdtempSync, rmSync } from 'node:fs';
import {
  LLM_TIMEOUT,
  sendMessage,
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';
import { startMockOpenAI, patchConfigForMock } from './helpers/mock-openai';

test.describe('#1240 审批卡输入框内联形态', () => {
  test.skip(
    process.platform === 'darwin' && !!process.env.CI,
    'macOS CI cannot reach the local mock server'
  );

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  let mockServer: ChildProcess;
  let outDir: string;

  test.beforeAll(async () => {
    outDir = mkdtempSync(join(tmpdir(), 'miqi-e2e-card-'));
    // mock 的「写授权」分支从环境变量读产物目录（startMockOpenAI 继承 process.env）
    process.env.MIQI_AUTH_OUT_DIR = outDir;
    const mock = await startMockOpenAI();
    mockServer = mock.proc;

    const fixture = await launchElectronApp(
      (config: any) => {
        patchConfigForMock(config, mock.mockUrl);
        // 本机 config 的 sandbox 常为 true（bridge 起好后回写）：写会走 WSL 沙箱，
        // 沙箱里 /mnt/c 只读 → 写宿主临时目录必挂。CI 没有 tools.sandbox。
        config.tools = {
          ...(config.tools ?? {}),
          sandbox: { ...config.tools?.sandbox, enabled: false },
        };
      },
      { bypassAll: false }
    );
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
    mockServer?.kill();
    delete process.env.MIQI_AUTH_OUT_DIR;
    try {
      rmSync(outDir, { recursive: true, force: true });
    } catch {}
  });

  test(
    '卡贴在输入框内：占满内宽 / 四档一行 / 无橙色描边；⏎ 放行并归还焦点',
    { timeout: LLM_TIMEOUT },
    async () => {
      await sendMessage(page, '写授权测试');
      await expect(page.getByTestId('approval-allow-once')).toBeVisible({ timeout: 90_000 });

      const m = await page.evaluate(() => {
        const q = (s: string) => document.querySelector(s) as HTMLElement | null;
        const rect = (el: Element) => {
          const r = el.getBoundingClientRect();
          return {
            x: Math.round(r.x),
            y: Math.round(r.y),
            w: Math.round(r.width),
            h: Math.round(r.height),
          };
        };

        const container = q('[data-testid="chat-input-container"]')!;
        const slot = q('[data-testid="approval-slot"]')!;
        const once = q('[data-testid="approval-allow-once"]')!;
        const cardRoot = once.closest('[tabindex="-1"]') as HTMLElement;
        const textarea = q('textarea');
        const cs = getComputedStyle(container);
        const containerInner =
          container.getBoundingClientRect().width -
          parseFloat(cs.paddingLeft) -
          parseFloat(cs.paddingRight) -
          parseFloat(cs.borderLeftWidth) -
          parseFloat(cs.borderRightWidth);

        const opts = [
          ['once', 'approval-allow-once'],
          ['session', 'approval-allow-session'],
          ['always', 'approval-allow-permanent'],
          ['deny', 'approval-deny'],
        ].map(([id, testid]) => {
          const el = q(`[data-testid="${testid}"]`);
          return el ? { id, ...rect(el) } : { id, missing: true as const };
        });

        // 橙色判定：转 HSL，色相落在橙区且既不是近白/近黑也不是全透明。上一轮只查了
        // border/background、漏掉 outline，才没发现卡片被画上 2px --accent 聚焦环。
        const isOrange = (color: string) => {
          const mm = color.match(/rgba?\(([^)]+)\)/);
          if (!mm) return false;
          const [r, g, b, a = 1] = mm[1].split(',').map((v) => parseFloat(v));
          if (a < 0.1) return false;
          const rn = r / 255,
            gn = g / 255,
            bn = b / 255;
          const mx = Math.max(rn, gn, bn),
            mn = Math.min(rn, gn, bn);
          const l = (mx + mn) / 2;
          const d = mx - mn;
          if (d === 0) return false;
          const s = l > 0.5 ? d / (2 - mx - mn) : d / (mx + mn);
          if (s < 0.35) return false;
          let h = 0;
          if (mx === rn) h = ((gn - bn) / d) % 6;
          else if (mx === gn) h = (bn - rn) / d + 2;
          else h = (rn - gn) / d + 4;
          h = (h * 60 + 360) % 360;
          return h >= 5 && h <= 45;
        };

        const orangeStrokes: Array<Record<string, unknown>> = [];
        for (const el of [container, ...Array.from(container.querySelectorAll('*'))]) {
          const s = getComputedStyle(el);
          const tag = el.tagName.toLowerCase();
          const id = el.getAttribute('data-testid') ?? el.className?.toString().slice(0, 60);
          for (const [side, w, c] of [
            ['top', s.borderTopWidth, s.borderTopColor],
            ['right', s.borderRightWidth, s.borderRightColor],
            ['bottom', s.borderBottomWidth, s.borderBottomColor],
            ['left', s.borderLeftWidth, s.borderLeftColor],
          ] as const) {
            if (parseFloat(w) > 0 && isOrange(c)) {
              orangeStrokes.push({ tag, id, kind: `border-${side}`, w, color: c });
            }
          }
          if (
            s.outlineStyle !== 'none' &&
            parseFloat(s.outlineWidth) > 0 &&
            isOrange(s.outlineColor)
          ) {
            orangeStrokes.push({
              tag,
              id,
              kind: 'outline',
              w: s.outlineWidth,
              color: s.outlineColor,
            });
          }
        }

        // 附件插槽必须落在让位包裹层之内且随之不被渲染（包裹层 display:none 时子元素
        // 自身 computed display 仍是 block，所以判"没被渲染"要看 offsetParent）
        const wrapperEl = slot.nextElementSibling as HTMLElement | null;
        const attachEl = wrapperEl?.firstElementChild as HTMLElement | null;

        return {
          container: rect(container),
          containerInner: Math.round(containerInner),
          slot: rect(slot),
          card: rect(cardRoot),
          opts,
          focusedIsCardRoot: document.activeElement === cardRoot,
          textareaVisible: !!textarea && textarea.offsetParent !== null,
          ariaRole: cardRoot.getAttribute('role'),
          ariaLabelledby: cardRoot.getAttribute('aria-labelledby'),
          orangeStrokes,
          attachIsInsideWrapper: !!attachEl && attachEl.parentElement === wrapperEl,
          wrapperDisplay: wrapperEl ? getComputedStyle(wrapperEl).display : null,
          attachNotRendered: !!attachEl && attachEl.offsetParent === null,
        };
      });

      console.log('[card] ' + JSON.stringify(m));

      // 位置与宽度：卡在框内、占满内宽（用相对比较，不写死像素，CI 上同样成立）
      expect(m.focusedIsCardRoot).toBe(true);
      expect(Math.abs(m.card.w - m.slot.w)).toBeLessThanOrEqual(1);
      expect(Math.abs(m.card.w - m.containerInner)).toBeLessThanOrEqual(1);
      expect(m.card.x).toBeGreaterThanOrEqual(m.container.x);
      expect(m.card.y).toBeGreaterThanOrEqual(m.container.y);
      expect(m.card.x + m.card.w).toBeLessThanOrEqual(m.container.x + m.container.w + 1);
      expect(m.card.y + m.card.h).toBeLessThanOrEqual(m.container.y + m.container.h + 1);

      // 一行四档、从左到右
      expect(m.opts.map((o) => 'missing' in o)).toEqual([false, false, false, false]);
      const ys = m.opts.map((o) => (o as any).y);
      expect(new Set(ys).size, `四档不在同一行: ${JSON.stringify(ys)}`).toBe(1);
      const xs = m.opts.map((o) => (o as any).x);
      expect([...xs].sort((a, b) => a - b)).toEqual(xs);

      // 可访问名 + 无橙色描边
      expect(m.ariaRole).toBe('group');
      expect(m.ariaLabelledby).toBe('approval-title');
      expect(m.orangeStrokes, `不该有橙色描边: ${JSON.stringify(m.orangeStrokes)}`).toEqual([]);

      // 挂起期间输入框让位，附件插槽随之不渲染但仍在 DOM
      expect(m.textareaVisible).toBe(false);
      expect(m.attachIsInsideWrapper).toBe(true);
      expect(m.wrapperDisplay).toBe('none');
      expect(m.attachNotRendered).toBe(true);

      // ⏎ = 允许一次 → 真写盘 + 焦点归还输入框
      await page.keyboard.press('Enter');
      await expect(page.getByTestId('approval-allow-once')).toBeHidden({ timeout: 30_000 });

      const target = join(outDir, 'auth_probe.txt');
      await expect.poll(async () => existsSync(target), { timeout: 30_000 }).toBe(true);
      expect(readFileSync(target, 'utf-8')).toContain('authorization-card-e2e-probe');

      const after = await page.evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        return {
          tag: a?.tagName ?? null,
          textareaVisible:
            !!document.querySelector('textarea') &&
            (document.querySelector('textarea') as HTMLElement).offsetParent !== null,
        };
      });
      expect(after.textareaVisible).toBe(true);
      expect(after.tag, '⏎ 放行后焦点应归还输入框').toBe('TEXTAREA');
    }
  );

  test(
    '切走聊天页后卡片仍可见（兜底模态）；Tab 圈在卡内；resolve 后焦点不落进未渲染元素',
    { timeout: LLM_TIMEOUT },
    async () => {
      // mock 的「写授权」分支是有状态的（历史里出现 write_file 后只回文本），而两个
      // 用例共用一个 Electron 实例 —— 上一个用例已经把 write_file 写进历史，必须开
      // 新会话把历史清空，否则这里拿不到审批卡。
      await createNewConversation(page);
      await sendMessage(page, '写授权测试');
      await expect(page.getByTestId('approval-allow-once')).toBeVisible({ timeout: 90_000 });

      // 阶段 1：聊天页上必须是**内联**形态
      const v1 = await page.evaluate(() => ({
        inSlot: !!document
          .querySelector('[data-testid="approval-allow-once"]')
          ?.closest('[data-testid="approval-slot"]'),
        hasDialog: !!document.querySelector('[role="alertdialog"]'),
      }));
      expect(v1.inSlot).toBe(true);
      expect(v1.hasDialog).toBe(false);

      // 阶段 2：切到设置页。App.tsx 只给聊天区加 `hidden`、**不卸载** ChatConsole，
      // 所以插槽元素仍在 DOM 里但不可渲染 —— 这正是回归点。
      await page.getByTestId('nav-system-settings').click();
      await page.waitForTimeout(1500);
      const v2 = await page.evaluate(() => {
        const slot = document.querySelector<HTMLElement>('[data-testid="approval-slot"]');
        const once = document.querySelector<HTMLElement>('[data-testid="approval-allow-once"]');
        return {
          slotExists: !!slot,
          slotRendered: !!slot && slot.offsetParent !== null,
          inSlot: !!once?.closest('[data-testid="approval-slot"]'),
          cardVisible: !!once && once.offsetParent !== null,
        };
      });
      console.log('[xpage] 切到设置页后: ' + JSON.stringify(v2));
      expect(v2.slotExists, '切换页面不应卸载聊天区（插槽元素仍在）').toBe(true);
      expect(v2.slotRendered).toBe(false);
      expect(v2.inSlot, '切页后不应再把卡片投进不可渲染的插槽').toBe(false);
      expect(v2.cardVisible, '切页后审批卡必须仍然可见').toBe(true);

      const dialog = page.locator('[role="alertdialog"]');
      await expect(dialog).toBeVisible({ timeout: 15_000 });
      // 兜底模态的可访问名在外层 alertdialog 上
      expect(await dialog.getAttribute('aria-labelledby')).toBe('approval-title');

      // 审批挂起时输入区被 hidden，但 ExecutionPolicySelector / ReasoningModeSwitch
      // 的 document 级 keydown 守卫只挡 INPUT/TEXTAREA —— 焦点在卡片的 DIV/按钮上时
      // 1–4 会静默改掉看不见的执行策略，Shift+Tab 还会 pick(next) 循环策略。这里锁死。
      const policyBtn = page
        .locator('button')
        .filter({ hasText: /规划|手动|允许编辑|自动/ })
        .first();
      const policyBefore = ((await policyBtn.textContent()) ?? '').trim();

      // Tab 环：次数多于卡内可聚焦元素数，焦点必须始终留在对话框内
      const inside = () => dialog.evaluate((el) => el.contains(document.activeElement));
      for (let i = 0; i < 8; i++) {
        await page.keyboard.press('Tab');
        expect(await inside(), `第 ${i + 1} 次 Tab 后焦点跑出对话框`).toBe(true);
      }
      // 每次按键后**立刻**校验，不能只在结尾查一次。初始策略恰为 plan 时，「Shift+Tab
      // 改掉」会被后面的 `1`（=plan）抵消掉，结尾那次断言照样通过——奇数次的技巧只挡得住
      // 单条快捷键转整圈，挡不住两条路径互相抵消。
      const policyLabel = async () => ((await policyBtn.textContent()) ?? '').trim();
      const expectPolicyUnchanged = async (step: string) => {
        // 停一拍再查：否则可能因为改动还没生效而误判成「没变」，把 bug 放过去
        await page.waitForTimeout(150);
        expect(await policyLabel(), `审批挂起期间执行策略被改掉了（${step}）`).toBe(policyBefore);
      };

      for (let i = 0; i < 3; i++) {
        await page.keyboard.press('Shift+Tab');
        expect(await inside(), `第 ${i + 1} 次 Shift+Tab 后焦点跑出对话框`).toBe(true);
        await expectPolicyUnchanged(`第 ${i + 1} 次 Shift+Tab`);
      }
      await page.keyboard.press('1');
      await expectPolicyUnchanged('按 1');
      await page.keyboard.press('4');
      await expectPolicyUnchanged('按 4');
      // 4 = auto，而 pick('auto') 只 setConfirmAuto(true)、**不调 onChange**——标签不变
      // 也能通过上面那条断言，所以确认框必须单独查。它没有 testid，按文案定位。
      await expect(
        page.getByText('开启自动模式', { exact: true }),
        '审批挂起期间按 4 弹出了自动模式确认框'
      ).toHaveCount(0);
      console.log(`[xpage] 执行策略 前=${policyBefore} 后=${await policyLabel()}`);

      // 阶段 3：Esc 拒绝 → 焦点不得落进任何未渲染元素（尤其聊天区那个 hidden textarea）
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden({ timeout: 15_000 });

      const after = await page.evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        return {
          tag: a?.tagName ?? null,
          testid: a?.getAttribute('data-testid') ?? null,
          isBody: a === document.body,
          rendered: !!a && a !== document.body && a.offsetParent !== null,
          hiddenTextareaFocused: a?.tagName === 'TEXTAREA' && a.offsetParent === null,
        };
      });
      console.log('[xpage] Esc 之后焦点: ' + JSON.stringify(after));
      expect(after.hiddenTextareaFocused, '焦点落进了不可见的输入框').toBe(false);
      expect(after.isBody || after.rendered, `焦点停在未渲染元素上: ${JSON.stringify(after)}`).toBe(
        true
      );
    }
  );
});
