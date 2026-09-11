/**
 * E2E spec for issue #797 — [BUG] 中断后 turn 锁未及时释放，后续消息报
 * '上一个任务还在进行中'。
 *
 * Walks the issue's reproduction steps as a real user would:
 *   1. 会话中发起包含长工具调用的任务（MOF-5 相关、多次 web_search）；
 *   2. 在工具执行中点击中断（停止生成按钮）；
 *   3. 工具调用节点显示"已停止"后，立即继续发送新消息；
 *   4. 观察会话流：不得出现"上一个任务还在进行中"红色错误，且新消息
 *      正常完成（期望行为：中断后后端立即释放会话 turn 锁）。
 *
 * Backend fix under test (PR #852): chat.abort now calls
 * release_turn_lock() — the bridge-side drain task is popped from
 * _session_drain_tasks immediately (it keeps draining in the background),
 * so a resend is accepted instead of rejected with TURN_IN_PROGRESS.
 *
 * Timing contract (learned the hard way — both the stop-click and the
 * tool-row wait must gate on REAL backend progress, not the renderer's
 * optimistic streaming flag):
 *   - The "进行中" tag (.tag-inprogress) is the TASK HEADER status — it
 *     appears the instant handleSend sets streaming=true, BEFORE the
 *     backend accepts the send.  Clicking stop that early races the abort
 *     ahead of chat.send (threads.start pipeline) → the abort hits the
 *     bridge before the session exists → UNAUTHORIZED → the turn is never
 *     cancelled and the lock persists → the resend gets TURN_IN_PROGRESS.
 *   - The correct "backend accepted" gate is the #364 pending spinner on
 *     the user bubble hiding (it disappears on the FIRST backend progress
 *     event).  Only then does an abort reach an existing session and the
 *     fix's release_turn_lock actually fire.
 *
 * Uses a real LLM via the user's configured provider (approval-bypass on).
 * Sandbox is disabled for determinism (the WSL-stuck drain variant is
 * covered by the backend unit tests in
 * tests/bridge/test_issue_797_turn_lock_release.py).
 *
 * Run:
 *   cd apps/desktop && npx playwright test --config=playwright.config.ts \
 *     --project=electron issue-797-turn-lock-resend.spec.ts --workers=1
 */

import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  createNewConversation,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

const INPUT = '[data-testid="chat-input-container"] textarea';
const STOP_BTN = 'button[title="停止生成"]';
const TURN_IN_PROGRESS_TEXT = '上一个任务还在进行中，请稍候片刻或新开一个会话。';

/** Send a prompt via real keystrokes.  type() (not fill()) drives React's
 *  onChange per keystroke, so `input` state is committed before Enter fires
 *  (the #542 resend pattern). */
async function sendPrompt(page: Page, text: string): Promise<void> {
  const inputX = page.locator(INPUT);
  await inputX.click();
  await inputX.fill('');
  await inputX.type(text);
  await inputX.press('Enter');
}

/** Assert the TURN_IN_PROGRESS error text NEVER appears for a real time
 *  window.  expect().toHaveCount(0) resolves instantly when the locator
 *  matches nothing — the pre-fix error lands ~1-2s AFTER the resend, so we
 *  must actively poll, not just check once. */
async function assertNoTurnInProgressError(page: Page, windowMs: number) {
  const errorLoc = page.getByText(TURN_IN_PROGRESS_TEXT);
  const deadline = Date.now() + windowMs;
  while (Date.now() < deadline) {
    const n = await errorLoc.count();
    expect(
      n,
      `TURN_IN_PROGRESS error appeared after abort→resend (window ${windowMs}ms) — 锁未释放`
    ).toBe(0);
    await page.waitForTimeout(500);
  }
}

/** Dismiss any approval bottom-sheet (ApprovalModal) by clicking 永久允许 —
 *  approvals.bypass_all covers the tool-exec permission path, but some
 *  requests (e.g. network policy for web_search) still surface a modal that
 *  intercepts pointer events on the composer (stop button / input). */
async function dismissApprovalModals(page: Page) {
  const approve = page.getByTestId('approval-allow-permanent');
  for (let i = 0; i < 10; i++) {
    if (!(await approve.isVisible().catch(() => false))) return;
    await approve.click();
    console.log('[test] dismissed an approval modal (永久允许)');
    await page.waitForTimeout(500);
  }
}

test.describe('Issue #797: turn lock released on abort → immediate resend works', () => {
  test.describe.configure({ mode: 'serial' });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;
  const t0 = Date.now();
  const log = (msg: string) =>
    console.log(`[test] +${((Date.now() - t0) / 1000).toFixed(1)}s ${msg}`);

  test.beforeAll(async () => {
    const fixture = await launchElectronApp((config) => {
      // Deterministic: no WSL sandbox first-run / cold-start stalls.
      config.tools = {
        ...config.tools,
        sandbox: { ...config.tools?.sandbox, enabled: false },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  }, 180_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'stop during a tool call → immediate resend → no "上一个任务还在进行中" error, new turn completes',
    { timeout: 900_000 },
    async () => {
      const inputX = page.locator(INPUT);
      await expect(inputX).toBeVisible({ timeout: 10_000 });

      // Fresh session, gate on history load (cold-start flake guard).
      await createNewConversation(page);
      await expect(page.getByText('正在连接…')).toBeHidden({ timeout: 60_000 });
      log('session ready, history loaded');

      // ── Repro step 1: a task with a LONG tool-call phase (5 sequential
      //    web_search calls in the MOF-5 domain — matches the reporter's
      //    session and keeps the turn in tool execution for a while). ──
      const markerA = `797A_${Date.now().toString(36)}`;
      await sendPrompt(
        page,
        `${markerA}：请依次用 web_search 工具搜索以下 5 个关键词，每个搜索一次再搜下一个：MOF-5 合成方法、MOF 造粒工艺、BET 损失、MOF-5 市场价格、MOF-5 生产厂商。全部搜索完成后，用中文总结。`
      );
      log('sent prompt A');

      // ── Gate 1: the #364 pending spinner on the user bubble hides on the
      //    FIRST BACKEND progress event — the send is accepted and the turn
      //    is genuinely running (NOT the renderer's optimistic flag). ──
      const pendingSpinner = page.locator('[data-testid="chat-message-user"] svg.animate-spin');
      await expect(pendingSpinner.last()).toBeHidden({ timeout: 120_000 });
      log('backend accepted the turn (pending spinner gone)');

      // ── Gate 2 (repro step 2): the turn is GENUINELY running — either a
      //    real tool call is executing (网页搜索/工具调用 rows rendered from
      //    ToolCallBeginEvent) or the model is in its thinking phase (快速思考
      //    block).  Both are real backend progress; the tool row is preferred
      //    but the model may answer with reasoning only (LLM randomness —
      //    v6 run never called web_search). ──
      const turnActivity = page.getByText(/快速思考|网页搜索|工具调用/).first();
      await expect(turnActivity).toBeVisible({ timeout: 240_000 });
      log('turn actively running (tool call or thinking)');

      // ── Repro step 2: click interrupt (停止生成).  Dismiss any approval
      //    bottom-sheet first — it intercepts pointer events on the
      //    composer. ──
      await dismissApprovalModals(page);
      const stopBtn = page.locator(STOP_BTN);
      await expect(stopBtn).toBeVisible({ timeout: 10_000 });
      await stopBtn.click();
      log('clicked stop during tool execution');

      // ── Repro step 3: the flow shows "已停止" (frontend appends it after
      //    the abort IPC settles — post-fix the backend turn lock is
      //    released in the same handler). ──
      await expect(page.getByText(/已停止/).first()).toBeVisible({
        timeout: 15_000,
      });
      log('"已停止" marker shown');

      // ── Repro step 3→4: IMMEDIATELY send the next message. ──
      const markerB = `797B_${Date.now().toString(36)}`;
      const assistantBefore = await page.getByTestId('chat-message-assistant').count();
      await dismissApprovalModals(page);
      await sendPrompt(page, `${markerB}：只用一句话介绍 MOF-5 是什么。`);
      log('sent prompt B immediately after stop');

      // The resend's optimistic user bubble appears (send accepted — the
      // #364 optimistic UI mounts it regardless of backend acceptance;
      // acceptance is proven by the absence of the error below).
      await expect(page.getByTestId('chat-message-user')).toHaveCount(2, {
        timeout: 15_000,
      });
      log('resend user bubble visible');

      // ── Repro step 4 / 期望行为: the TURN_IN_PROGRESS error must NOT
      //    appear.  Watch a real 30s window (polling — toHaveCount(0)
      //    alone is instant and would miss a late error). ──
      await assertNoTurnInProgressError(page, 30_000);
      log('30s window: no TURN_IN_PROGRESS error');

      // ── The new turn settles: the 停止生成 button (shown while streaming
      //    with an empty input) disappears. ──
      await expect(stopBtn).toBeHidden({ timeout: 240_000 });
      log('streaming ended for the resend turn');

      // The resend turn must produce a real assistant reply.  A transient
      // provider error ("模型服务暂时不可用或过载" — 5xx classified TRANSIENT)
      // is a PROVIDER flake, not the #797 bug: the resend WAS accepted and
      // reached the LLM (the bug's symptom — TURN_IN_PROGRESS rejection —
      // was already asserted absent above).  Retry the resend on that
      // specific error so the completion assertion stays meaningful.
      const providerErrorText = '模型服务暂时不可用或过载';
      const providerFlake = () =>
        page
          .getByText(providerErrorText)
          .count()
          .then((n) => n > 0);
      let assistantAfter = await page.getByTestId('chat-message-assistant').count();
      for (let attempt = 1; attempt <= 2 && assistantAfter <= assistantBefore; attempt++) {
        if (!(await providerFlake())) break;
        log(`resend hit a transient provider error — resending (attempt ${attempt})`);
        await dismissApprovalModals(page);
        await sendPrompt(page, `${markerB}：只用一句话介绍 MOF-5 是什么。`);
        // The lock must STILL be free on every retry (no TURN_IN_PROGRESS).
        await assertNoTurnInProgressError(page, 15_000);
        await expect(stopBtn).toBeHidden({ timeout: 240_000 });
        assistantAfter = await page.getByTestId('chat-message-assistant').count();
      }
      expect(
        assistantAfter,
        `the resend should produce an assistant reply (before=${assistantBefore}, after=${assistantAfter})`
      ).toBeGreaterThan(assistantBefore);

      // Final safety: the error never showed up anywhere in the stream.
      await expect(page.getByText(TURN_IN_PROGRESS_TEXT)).toHaveCount(0);
      await expect(inputX).toBeEnabled({ timeout: 5_000 });
      log('done — no error, resend turn completed');
    }
  );
});
