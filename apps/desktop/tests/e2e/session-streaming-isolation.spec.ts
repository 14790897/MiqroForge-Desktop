/**
 * Session Streaming Isolation E2E Tests
 *
 * Fix #212 / #378: prevent streaming messages leaking across sessions, and
 * ensure in-progress content is restored when switching back.
 *
 * All tests share ONE app instance and use SHORT prompts.  Three separate app
 * instances (one per test) made the full spec take >600s — longer than the
 * electron project timeout — so every test was force-terminated.  Sharing the
 * instance keeps the suite well under the timeout.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --project=electron -g 'Session Streaming Isolation'
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  createNewConversation,
  approveLoop,
  launchElectronApp,
  closeElectronApp,
} from './helpers/electron-setup';

// ─── Helpers ──────────────────────────────────────────────────────────

/** Send and wait for the reply to complete.  Short prompts → fast replies. */
async function sendAndWait(page: Page, text: string, loopTimeout = 120_000) {
  const inputX = page.locator('textarea, [contenteditable="true"], input[type="text"]').last();
  await expect(inputX).toBeVisible({ timeout: 10000 });
  await inputX.click();
  await inputX.fill('');
  await inputX.type(text);
  await inputX.press('Enter');
  await page.waitForTimeout(1000);
  await approveLoop(page, loopTimeout);
}

/**
 * Send to session A and WAIT for the user bubble to render (stream active,
 * reply forming), without waiting for the full reply.
 */
async function sendStart(page: Page, text: string) {
  const inputX = page.locator('textarea, [contenteditable="true"], input[type="text"]').last();
  await expect(inputX).toBeVisible({ timeout: 10000 });
  await inputX.click();
  await inputX.fill('');
  await inputX.type(text);
  await inputX.press('Enter');
  await expect(
    page.locator('main [class*="max-w-[760px]"]').getByText(text, { exact: false }).first()
  ).toBeVisible({ timeout: 15_000 });
}

/**
 * Resolve the current session's key from sessions.list().  The sidebar title
 * is derived asynchronously and can lag, but the key is authoritative.
 */
async function resolveSessionKey(page: Page, marker: string): Promise<string> {
  const key = await page.evaluate(async (m) => {
    const r: any = await (window as any).miqi.sessions.list();
    const list: any[] = r?.sessions || [];
    const hit = list.find((s: any) => (s.title || '').includes(m)) || list[0];
    return hit?.key || '';
  }, marker);
  expect(key, `session key for ${marker} should be resolvable`).toBeTruthy();
  return key;
}

/** Switch to a different session, then back to A.  Returns the message list. */
async function switchAwayAndBack(page: Page, aKey: string, markerA: string) {
  const msgList = page.locator('main [class*="max-w-[760px]"]');
  const sidebar = page.locator('div.flex.flex-col.shrink-0.border-r').first();
  const sessionButtons = sidebar.locator('button.rounded-xl');

  // A's sidebar button shows its TITLE (derived from the first message, e.g.
  // "只回答RESTORE_A_...") once the backend has generated it; before that it
  // shows the raw key (desktop:<ts>).  Try the marker first, then the key.
  const markerButton = sidebar.getByText(markerA, { exact: false }).first();
  const keyButton = sidebar.getByText(aKey, { exact: false }).first();
  let aButton: ReturnType<Page['locator']> | null = null;
  try {
    await markerButton.waitFor({ state: 'visible', timeout: 30_000 });
    aButton = markerButton;
  } catch {
    await keyButton.waitFor({ state: 'visible', timeout: 30_000 });
    aButton = keyButton;
  }
  expect(aButton, 'A session button should be locatable').not.toBeNull();

  // Switch AWAY to a different session (button without A's key or marker).
  let awayButton: ReturnType<Page['locator']> | null = null;
  for (let i = 0; i < (await sessionButtons.count()); i += 1) {
    const btn = sessionButtons.nth(i);
    const text = ((await btn.textContent()) || '').trim();
    if (!text.includes(aKey) && !text.includes(markerA)) {
      awayButton = btn;
      break;
    }
  }
  expect(awayButton, 'a non-A session must exist to switch away to').not.toBeNull();
  await awayButton!.click();

  // Switch back to A.
  await expect(aButton!).toBeVisible({ timeout: 30_000 });
  await aButton!.click();

  // Marker text visible again — the core restoration check (no refresh).
  await expect(msgList.getByText(markerA, { exact: false }).first()).toBeVisible({
    timeout: 10_000,
  });

  return msgList;
}

// ─── Tests ────────────────────────────────────────────────────────────

test.describe('Session Streaming Isolation E2E', () => {
  // Serial: the tests share ONE app instance (beforeAll launches it once) and
  // the switch-away test depends on the earlier tests having created other
  // sessions to switch to. fullyParallel:true would give each test its own app
  // (and its own empty sidebar), breaking that dependency. Serial forces
  // declaration order on one worker.
  test.describe.configure({ mode: 'serial' });

  let electronApp: ElectronApplication;
  let page: Page;
  let miqiHome: string;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
    miqiHome = fixture.miqiHome;
  });

  test.afterAll(async () => {
    await closeElectronApp(electronApp, miqiHome);
  });

  test(
    'no cross-session message leak when switching sessions',
    { timeout: LLM_TIMEOUT },
    async () => {
      // ── Session A: send and wait for completion ──
      await createNewConversation(page);
      const markerA = `ISOLATE_A_${Date.now().toString(36)}`;
      await sendAndWait(page, `只回答${markerA}`);
      expect((await page.locator('main').textContent()) || '').toContain(markerA);

      // ── Session B: send and wait for completion ──
      await createNewConversation(page);
      const markerB = `ISOLATE_B_${Date.now().toString(36)}`;
      await sendAndWait(page, `只回答${markerB}`);

      // B's message list must NOT contain A's marker, and must contain B's own.
      const contentB = (await page.locator('main').textContent()) || '';
      expect(contentB, 'Session B should not contain Session A marker').not.toContain(markerA);
      expect(contentB, 'Session B should contain its own marker').toContain(markerB);

      console.log(`[test] ✅ Session B isolated — no cross-session message leak`);
    }
  );

  test(
    'session history isolation — no cross-contamination via sessions.get',
    { timeout: LLM_TIMEOUT },
    async () => {
      // ── Session A: send and wait ──
      await createNewConversation(page);
      const markerA = `HIST_A_${Date.now().toString(36)}`;
      await sendAndWait(page, `只回答${markerA}`);
      expect((await page.locator('main').textContent()) || '').toContain(markerA);

      // ── Session B: send and wait ──
      await createNewConversation(page);
      const markerB = `HIST_B_${Date.now().toString(36)}`;
      await sendAndWait(page, `只回答${markerB}`);
      expect((await page.locator('main').textContent()) || '').toContain(markerB);

      // Verify via IPC: A's history must not contain B's marker and vice versa.
      const isolation = await page.evaluate(
        async (markers) => {
          const all = await (window as any).miqi.sessions.list();
          const sessions: any[] = all.sessions || all || [];
          const results: any[] = [];
          for (const s of sessions) {
            try {
              const detail = await (window as any).miqi.sessions.get(s.key);
              const msgs = Array.isArray(detail?.messages) ? detail.messages : [];
              const text = msgs.map((m: any) => m.content || '').join('\n');
              results.push({ key: s.key, title: s.title, text });
            } catch (e) {
              results.push({ key: s.key, title: s.title, text: '', error: String(e) });
            }
          }
          return results;
        },
        [markerA, markerB]
      );

      const sessionA = isolation.find((s: any) => s.text.includes(markerA));
      const sessionB = isolation.find((s: any) => s.text.includes(markerB));

      expect(sessionA, 'Session A should exist with its marker').toBeTruthy();
      expect(sessionB, 'Session B should exist with its marker').toBeTruthy();
      // macOS 慢 runner 上 sessions.get 可能读到半写入状态（流式回复刚落盘）——
      // 首次断言失败时重拉一次再判，避免误报（session-streaming-isolation 在
      // macos-e2e 偶发；electron/wsl 均已稳定通过）。
      let aText = sessionA?.text ?? '';
      let bText = sessionB?.text ?? '';
      if (aText.includes(markerB) || bText.includes(markerA)) {
        const retry = await page.evaluate(
          async (markers) => {
            const all = await (window as any).miqi.sessions.list();
            const sessions: any[] = all.sessions || all || [];
            const results: any[] = [];
            for (const s of sessions) {
              try {
                const detail = await (window as any).miqi.sessions.get(s.key);
                const msgs = Array.isArray(detail?.messages) ? detail.messages : [];
                results.push({
                  key: s.key,
                  text: msgs.map((m: any) => m.content || '').join('\n'),
                });
              } catch {
                /* ignore */
              }
            }
            return results;
          },
          [markerA, markerB]
        );
        const a2 = retry.find((s: any) => s.key === sessionA?.key);
        const b2 = retry.find((s: any) => s.key === sessionB?.key);
        aText = a2?.text ?? aText;
        bText = b2?.text ?? bText;
      }
      expect(aText, 'Session A should not contain Session B marker').not.toContain(markerB);
      expect(bText, 'Session B should not contain Session A marker').not.toContain(markerA);

      console.log(`[test] ✅ Session history isolation verified`);
    }
  );

  test(
    'switch away WHILE the reply is streaming, then back — content continues, no refresh',
    { timeout: 300_000 },
    async () => {
      // ── Session A: start a response with a medium-length prompt so the
      //    model keeps generating long enough to switch away mid-stream ──
      await createNewConversation(page);
      const markerA = `RESTORE_A_${Date.now().toString(36)}`;
      // Medium prompt → a few seconds of generation (not instant), so the
      // switch happens while the reply is actively streaming.
      const promptA = `${markerA}：请用 3 段话介绍杭州这座城市。`;
      await sendStart(page, promptA);

      // Wait for the REPLY to actually start rendering (content beyond the
      // user prompt) BEFORE switching away — otherwise the switch happens
      // before generation begins and the test doesn't exercise mid-stream.
      const msgList = page.locator('main [class*="max-w-[760px]"]');
      await page.waitForFunction(
        (marker) => {
          const list = document.querySelector('main [class*="max-w-[760px]"]');
          if (!list) return false;
          const text = (list.textContent || '').replace(marker, '');
          // Beyond the user prompt, the reply has started (thinking or text).
          return text.trim().length > 20;
        },
        markerA,
        { timeout: 60_000 }
      );

      // Resolve A's session key, then switch away and back while the reply is
      // still streaming.
      const aKey = await resolveSessionKey(page, markerA);
      await switchAwayAndBack(page, aKey, markerA);

      // The reply must CONTINUE and eventually complete after switching back —
      // without a manual refresh.  A multi-sentence answer is >40 chars; wait
      // for it to render in full.
      await page.waitForFunction(
        (marker) => {
          const list = document.querySelector('main [class*="max-w-[760px]"]');
          if (!list) return false;
          const text = (list.textContent || '').replace(marker, '');
          return text.trim().length > 40;
        },
        markerA,
        { timeout: 120_000 }
      );

      console.log(`[test] ✅ Reply continued after switch-back — no refresh`);
    }
  );
});
