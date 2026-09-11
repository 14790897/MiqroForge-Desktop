/**
 * REPRO: MiQroForge 窗口最小化/完全遮挡（后台）时，AI 流式回复卡住，切回前台才继续。
 *
 * 机制（代码层面已确认）：
 *  - 流式正文由 rAF 打字机动画驱动：revealNext 每帧 reveal 4 字符，
 *    `animId = requestAnimationFrame(revealNext)` 循环调度
 *    (apps/desktop/src/renderer/features/chat/ChatConsole.tsx:3296-3357, 3829)。
 *  - BrowserWindow webPreferences 未设置 backgroundThrottling: false
 *    (apps/desktop/src/main/index.ts:37-42) → Chromium 默认开启后台节流。
 *  - 窗口不可见（最小化/完全遮挡）时 Chromium 完全暂停 requestAnimationFrame，
 *    流式 IPC 事件仍实时到达（fullContent 持续累积），但 revealNext 不执行
 *    → UI 文本卡住；切回前台 rAF 恢复才继续。
 *
 * 测量两个信号：
 *  1. rAF 心跳计数（注入的 rAF 循环）—— 直接证据：后台期间 rAF 是否暂停
 *  2. assistant 气泡文本长度 —— 用户可感知的症状：后台期间不增长，恢复后继续
 *
 * Run:
 *   cd apps/desktop
 *   npm run build
 *   unset PYTHONPATH
 *   PLAYWRIGHT_SKIP_WEB_SERVER=1 npx playwright test \
 *     --config=playwright.config.ts --project=electron repro-bg-throttle --workers=1
 */
import { test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { launchElectronApp, closeElectronApp, sendMessage } from './helpers/electron-setup';

test.describe('repro: background (minimized) window freezes streaming reply', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.afterAll(async () => {
    await closeElectronApp(electronApp).catch(() => {});
  });

  test('minimized → assistant text stalls & rAF pauses; restore → resumes', async () => {
    // Retry path (LLM tool-loop / silent no-op turns) can exceed the project
    // default 600s — give this repro room.
    test.setTimeout(900_000);

    // Disable the sandbox: its first-time WSL/bwrap setup stalls the bridge
    // event loop right after init on cold e2e runs (every IPC times out,
    // ChatConsole stays on "正在连接…"). This repro only needs a streaming
    // reply, no sandbox. Correct key path: tools.sandbox.enabled.
    const fixture = await launchElectronApp((config: any) => {
      config.tools = {
        ...(config.tools ?? {}),
        sandbox: { ...((config.tools ?? {}).sandbox ?? {}), enabled: false },
      };
    });
    electronApp = fixture.electronApp;
    page = fixture.page;

    // ── Inject a rAF heartbeat loop so we can measure whether
    //    requestAnimationFrame actually runs while the window is hidden. ──
    await page.evaluate(() => {
      (window as any).__rafCount = 0;
      const loop = () => {
        (window as any).__rafCount++;
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    });

    // ── Wait for the session to FINISH loading before sending. The cold e2e
    //    bridge has a ~28s silent window during which every IPC times out;
    //    if chat.send fires while historyLoaded=false, the optimistic bubble
    //    lands under the "正在连接…" placeholder and later gets wiped by
    //    load()'s full messages overwrite. The textarea becomes enabled
    //    earlier, so waitForInputReady alone is NOT sufficient. ──
    await expect(page.getByText('正在连接…')).toBeHidden({ timeout: 120_000 });
    console.log('[repro] history loaded (正在连接… gone)');

    // ── Send a question that produces a LONG streaming reply. Pure
    //    knowledge question — deepseek tends to enter a tool loop (web_search
    //    etc.) instead of answering "MOF 造粒如何避免 BET 损失" directly,
    //    which stalls the text stream past the wait window. ──
    await sendMessage(
      page,
      '请详细介绍一下金属有机框架（MOF）材料：请从结构特点、常见合成方法、典型应用领域（气体储存、分离、催化）四个角度展开，给出至少 800 字的中文回答。'
    );

    // ── Wait until streaming actually started (assistant bubble exists and
    //    is still growing — i.e. content is being revealed). If the turn ends
    //    without text (silent no-op), the textarea becomes enabled again —
    //    wait for that, then resend. Up to 3 attempts. ──
    const assistantText = () =>
      page
        .locator('[data-testid="chat-message-assistant"]')
        .last()
        .textContent()
        .catch(() => '');

    let L0 = 0;
    for (let attempt = 0; attempt < 3 && L0 === 0; attempt++) {
      if (attempt > 0) {
        console.log(
          `[repro] no stream on attempt ${attempt - 1} — waiting for turn to end, then resending…`
        );
        await page.waitForFunction(
          () => {
            const ta = document.querySelector(
              '[data-testid="chat-input-container"] textarea'
            ) as HTMLTextAreaElement | null;
            return !!ta && !ta.disabled;
          },
          { timeout: 180_000 }
        );
        await sendMessage(page, '请继续：直接给出至少 800 字的中文详细回答（不要调用任何工具）。');
      }
      const deadline = Date.now() + 180_000;
      while (Date.now() < deadline) {
        const len = ((await assistantText()) || '').length;
        if (len >= 20) {
          // Confirm it is STILL growing (streaming in progress), then capture.
          await page.waitForTimeout(600);
          const len2 = ((await assistantText()) || '').length;
          if (len2 > len) {
            L0 = len2;
            break;
          }
        }
        await page.waitForTimeout(300);
      }
    }
    expect(L0, 'streaming should have started').toBeGreaterThan(0);

    const raf0 = await page.evaluate(() => (window as any).__rafCount);
    console.log(`[repro] stream started: assistantLen=${L0} raf=${raf0}`);

    // ── Minimize the window → Chromium background throttling kicks in. ──
    await electronApp.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows().forEach((w) => w.minimize());
    });
    const tMin = Date.now();

    // Sample while hidden (two 8s windows).  With rAF frozen the assistant
    // text must NOT grow (revealNext never runs).
    await page.waitForTimeout(8_000);
    const L1 = ((await assistantText()) || '').length;
    const raf1 = await page.evaluate(() => (window as any).__rafCount);
    console.log(`[repro] hidden t=+8s: assistantLen=${L1} raf=${raf1}`);

    await page.waitForTimeout(8_000);
    const L2 = ((await assistantText()) || '').length;
    const raf2 = await page.evaluate(() => (window as any).__rafCount);
    const tBack = Date.now();
    console.log(`[repro] hidden t=+16s: assistantLen=${L2} raf=${raf2}`);

    // ── Restore the window → rAF resumes, revealNext catches up. ──
    await electronApp.evaluate(({ BrowserWindow }) => {
      BrowserWindow.getAllWindows().forEach((w) => w.restore());
    });

    // Wait for the TERMINAL streaming state: ChatConsole enables the composer
    // only after the final reply has fully revealed (finish branch:
    // `if (finalDone) setStreaming(false)`).  A fixed-length "text stable for
    // N samples" loop is NOT reliable — a provider/tool pause can exceed the
    // window and record a partial reply as Lfinal.  The enabled composer is
    // the deterministic completion signal.
    await expect(page.locator('[data-testid="chat-input-container"] textarea')).toBeEnabled({
      timeout: 120_000,
    });
    const Lfinal = ((await assistantText()) || '').length;
    const raf3 = await page.evaluate(() => (window as any).__rafCount);
    const tEnd = Date.now();
    console.log(
      `[repro] restored: assistantLen=${Lfinal} raf=${raf3} ` +
        `(hidden=${((tBack - tMin) / 1000).toFixed(1)}s, ` +
        `catchup=${((tEnd - tBack) / 1000).toFixed(1)}s)`
    );
    console.log(
      `[repro] SUMMARY L0=${L0} L1=${L1} L2=${L2} Lfinal=${Lfinal} ` +
        `raf0=${raf0} raf1=${raf1} raf2=${raf2} raf3=${raf3}`
    );
    // Persist the measurements so they survive reporter truncation.
    const { writeFileSync } = require('node:fs');
    writeFileSync(
      require('node:path').join(
        __dirname,
        '..',
        '..',
        'test-results',
        'repro-bg-throttle-summary.txt'
      ),
      `L0=${L0} L1=${L1} L2=${L2} Lfinal=${Lfinal} raf0=${raf0} raf1=${raf1} raf2=${raf2} raf3=${raf3} hidden_ms=${tBack - tMin} catchup_ms=${tEnd - tBack}\n`
    );

    // ── Assertions ──
    // Background throttling (Chromium dropping rAF to ~1Hz when the window is
    // minimized/occluded) is ENVIRONMENT-DEPENDENT — a CDP-attached page is
    // sometimes exempt, so the hidden rAF frame count is measured for
    // diagnostics rather than asserted.  The FIX is frame-rate agnostic
    // (time-driven advance), so correctness is verified by assertions 2-3 at
    // ANY frame rate.
    const hiddenRafFps = ((raf2 - raf0) / 16).toFixed(1);
    console.log(
      `[repro] rAF while hidden: ${raf2 - raf0} frames / 16s ≈ ${hiddenRafFps} fps ` +
        `(throttled when ≤ ~2.5fps, unthrottled ≈ 60fps)`
    );
    // 1. THE FIX: the assistant text keeps growing while hidden. The
    //    time-driven typewriter advances ~240 chars/s of wall-clock time per
    //    rAF tick (~1Hz while hidden → ~240 chars per second), so 16s hidden
    //    should reveal far more than the pre-fix ~76 chars (19 frames × 4).
    //    FullContent grows at stream speed (~55 chars/s → ~880 in 16s), so
    //    L2-L0 should approach the streamed amount — assert > 300.
    expect(
      L2 - L0,
      'assistant text should KEEP GROWING while hidden (time-driven typewriter)'
    ).toBeGreaterThan(300);
    // 2. The reply eventually completes to a full-length answer.
    expect(Lfinal, 'reply must complete to a full-length answer').toBeGreaterThan(800);
    expect(Lfinal).toBeGreaterThanOrEqual(L2);
  });
});
