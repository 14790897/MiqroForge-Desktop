/**
 * Shared Electron E2E setup helpers.
 *
 * All Electron-based E2E specs share the same app-launch lifecycle:
 * clean sessions → launch Electron → wait for bridge → run tests → close.
 * This module extracts that boilerplate so each spec file stays focused.
 */

import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import { resolve } from 'node:path';
import { tmpdir, homedir } from 'node:os';
import { join } from 'node:path';
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  cpSync,
  rmSync,
  readFileSync,
  writeFileSync,
} from 'node:fs';

// ─── Constants ──────────────────────────────────────────────────────

/** Absolute path to apps/desktop (Electron entry point) */
export const APPS_DESKTOP = resolve(__dirname, '../../..');

/** Default timeout for real LLM calls */
export const LLM_TIMEOUT = 240_000; // 4 min — gives LLM more time in CI

// ─── Session path helpers ────────────────────────────────────────────

/** Derive sessions directory from a MIQI_HOME path */
export function getMiqiSessionsDir(miqiHome: string): string {
  return join(miqiHome, 'workspace', 'sessions');
}

// ─── Page helpers ───────────────────────────────────────────────────

/** Wait for the chat input textarea to be present and enabled */
export async function waitForInputReady(page: Page, timeout = 60_000) {
  const textarea = page.locator('[data-testid="chat-input-container"] textarea');

  // Wait for textarea to exist first
  await expect(page.locator('[data-testid="chat-input-container"]')).toBeVisible({ timeout });

  // Retry with exponential backoff - input may briefly appear/disappear during UI transitions
  const deadline = Date.now() + timeout;
  let lastError: Error | null = null;

  while (Date.now() < deadline) {
    try {
      await expect(textarea).toBeEnabled({ timeout: 5000 });
      return textarea;
    } catch (e) {
      lastError = e as Error;
      // Wait before retrying
      await page.waitForTimeout(1000);
    }
  }

  // Log diagnostic info before throwing
  const count = await textarea.count();
  const containerVisible = await page.locator('[data-testid="chat-input-container"]').isVisible();
  console.log(
    `[diagnostic] waitForInputReady failed: textarea count=${count}, container visible=${containerVisible}`
  );
  throw lastError;
}

/** Send a message and confirm it appears in the chat */
export async function sendMessage(page: Page, text: string) {
  const textarea = await waitForInputReady(page);
  const userBubbles = page.getByTestId('chat-message-user');
  const before = await userBubbles.count();
  await textarea.fill(text);
  await textarea.press('Enter');
  // The optimistic-UI send (#364) mounts the user bubble immediately and
  // clears the input BEFORE the backend (providers:list) resolves — so matching
  // the exact text is unreliable and the reliable signal is a count increase.
  await expect(userBubbles).toHaveCount(before + 1, { timeout: 10_000 });
  await expect(userBubbles.last()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[data-testid="chat-input-container"] textarea')).toHaveValue('');
}

/**
 * 空会话不再落盘 / 不再进 sessions.list(#774)后,list[0] 不再恒等于刚打开的
 * 当前空会话。本 helper 保证当前会话已是一条"真实"会话并返回其 key:先查
 * list,空则发一条 seed 消息(用户消息写入即持久化,不必等 AI 回复)再轮询。
 * 供那些"launch 后直接取 list[0].key 当当前会话"的 spec 使用。
 */
export async function ensurePersistedSession(
  page: Page,
  seedText = '请创建会话',
  timeout = 90_000
): Promise<string> {
  const firstKey = async (): Promise<string | undefined> => {
    const all = (await page.evaluate(() => (window as any).miqi.sessions.list())) as any;
    return (all?.sessions ?? [])[0]?.key as string | undefined;
  };
  let key = await firstKey();
  if (key) return key;
  await sendMessage(page, seedText);
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    key = await firstKey();
    if (key) return key;
    await page.waitForTimeout(500);
  }
  throw new Error(`ensurePersistedSession: no session after seeding "${seedText}"`);
}

/** Wait for streaming to finish (no "Thinking…" indicator) */
export async function waitForResponseComplete(page: Page, timeout = 120_000) {
  // Phase 1: if the AI used tools, "IN PROGRESS" stays visible while
  // the tool runs.  Wait for it to hide (tool result rendered).
  try {
    await expect(page.locator('.tag-inprogress')).toBeHidden({ timeout: 15_000 });
  } catch {
    // Fast responses may never show IN PROGRESS.
  }

  // Phase 2: wait for main textContent to stop changing (streaming done).
  // Tolerate small growth (a "已深度思考 · N 秒" live timer adds a few chars
  // per second); a large jump means the reply is still streaming.
  await page.evaluate(() => {
    const main = document.querySelector('main');
    (window as any).__miqi_stream_state = { base: (main?.textContent || '').length, stable: 0 };
  });

  await page.waitForFunction(
    () => {
      const main = document.querySelector('main');
      if (!main) return false;
      const text = main.textContent || '';
      const s = (window as any).__miqi_stream_state;
      if (!s) {
        (window as any).__miqi_stream_state = { base: text.length, stable: 0 };
        return false;
      }
      if (text.length - s.base >= 10) {
        s.base = text.length;
        s.stable = 0;
        return false;
      }
      s.stable++;
      return s.stable >= 2;
      // Respect the caller's timeout: CI LLM providers have been slow enough
      // that PR-Agent's ai_timeout was raised to 600s (#707).  The old
      // Math.min(timeout, 90_000) cap made 240s callers time out at 90s and
      // deterministically fail LLM-dependent tests like regression-480.
    },
    { timeout, polling: 200 }
  );
}

/** Poll for approval dialogs and click "永久允许" until the AI stops
 *  thinking.  Used by sandbox and session-isolation tests. */
export async function approveLoop(page: Page, timeout = 180_000) {
  // The thinking indicator was removed, so completion can't be detected via
  // [data-testid="thinking-indicator"].  Keep auto-approving any dialogs, and
  // consider the turn done when main's textContent stops growing (tolerating
  // a small live-timer delta so the "已深度思考 · N 秒" counter doesn't block
  // completion).
  const deadline = Date.now() + timeout;
  let lastLen = -1;
  let stable = 0;
  let started = false;
  while (Date.now() < deadline) {
    const btn = page.getByTestId('approval-allow-permanent');
    if (await btn.isVisible({ timeout: 1000 }).catch(() => false)) {
      await btn.click();
      console.log('[test] Auto-approved tool');
    }
    const text = await page
      .locator('main')
      .textContent()
      .catch(() => '');
    const len = text ? text.length : 0;
    if (len > 0) started = true;
    // Allow small growth (a live timer adds a few chars per second); a large
    // jump means the reply is still streaming.
    if (lastLen === -1 || Math.abs(len - lastLen) < 10) {
      stable += 1;
      if (stable >= 3) return; // content stable → reply done
    } else {
      stable = 0;
    }
    lastLen = len;
    await page.waitForTimeout(1000);
  }
  throw new Error(
    started
      ? 'approveLoop timed out before the response completed'
      : 'approveLoop timed out before the response started'
  );
}

// ─── Session / Sidebar helpers ──────────────────────────────────────

/** Get the current session title from the header.
 *  Uses stable class-based selector: both old (text-sm) and new (text-[18px])
 *  UI share font-semibold.truncate on the title h2. */
export function getSessionTitle(page: Page) {
  return page.locator('h2.font-semibold.truncate').first();
}

/** Locator for the user message bubble containing `text` (substring match).
 *  Scoped to `[data-testid="chat-message-user"]` (not `main`) and visible-only:
 *  the session title is auto-derived from the first user message, so the same
 *  marker text also lives in the header's `chat-title`, which a `main`-scoped
 *  `.first()` would hit before the message list.  The `visible: true` filter is
 *  a defensive guard against stale/hidden nodes (#872). */
export function userMessage(page: Page, text: string) {
  return page
    .locator('[data-testid="chat-message-user"]')
    .filter({ hasText: text, visible: true })
    .first();
}

/** Get sidebar session items (clickable buttons that switch sessions).
 *  Scoped to the sidebar panel to avoid picking up buttons in main content.
 *  New UI: session cards use rounded-xl; filter tabs (rounded-md) and the
 *  "New Session" title button are excluded by the class selector. */
export function getSidebarSessionItems(page: Page) {
  const sidebar = page.locator('div.flex.flex-col.shrink-0.border-r').first();
  return sidebar.locator('button.rounded-xl');
}

/** Get the count of sidebar session items */
export async function getSidebarSessionCount(page: Page): Promise<number> {
  return getSidebarSessionItems(page).count();
}

/** Create a new conversation via sidebar "+" button and wait for it to be ready.
 *  The sidebar "+" button now creates a session directly (no workspace picker). */
export async function createNewConversation(page: Page): Promise<string> {
  // Remove stale Radix overlays that can block clicks from previous tests
  await page.evaluate(() => {
    document.querySelectorAll('[data-radix-focus-guard]').forEach((e) => e.remove());
    document.querySelectorAll('[data-aria-hidden="true"]').forEach((e) => {
      if (e.classList.contains('fixed') && e.classList.contains('inset-0')) {
        (e as HTMLElement).style.display = 'none';
      }
    });
  });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);

  const sidebarPlusBtn = page.locator('[data-testid="nav-new-session"]');
  await expect(sidebarPlusBtn).toBeVisible();
  await sidebarPlusBtn.click();

  // sidebar "+" creates session directly — no picker modal
  // The old workspace picker is now only opened by the inline "更换" button

  // Wait for the new session to load — input becomes enabled when ChatConsole mounts
  await waitForInputReady(page, 15_000);
  await waitForSidebarRefresh(page);
  const titleEl = getSessionTitle(page);
  return (await titleEl.textContent()) || '';
}

/** Wait for sidebar to refresh after session creation/deletion */
export async function waitForSidebarRefresh(page: Page, _timeout = 10_000) {
  await page.waitForTimeout(1500);
}

/** Switch to a sidebar session by clicking through sessions until the
 *  given marker text becomes visible in the main chat area.
 *  No longer depends on a "对话" nav button — the sidebar is always visible. */
export async function switchToSessionWithMarker(page: Page, marker: string): Promise<boolean> {
  // Ensure the Tasks section is scrolled into view
  const tasksHeader = page.locator('[data-testid="nav-tasks-title"]');
  await tasksHeader.scrollIntoViewIfNeeded().catch(() => {});

  // Get sidebar session items - try multiple selector patterns for robustness
  const sidebarSelectors = [
    'button.rounded-xl',
    '[data-testid^="session-"]',
    'div[role="button"][class*="session"]',
  ];

  let items: ReturnType<Page['locator']>;
  for (const selector of sidebarSelectors) {
    const count = await page.locator(selector).count();
    if (count > 0) {
      items = page.locator(selector);
      console.log(`[test] Found ${count} session items with selector: ${selector}`);
      break;
    }
  }

  if (!items) {
    console.log('[test] No session items found with any selector');
    return false;
  }

  const count = await items.count();
  console.log(`[test] Searching ${count} sidebar sessions for marker: ${marker}`);

  for (let i = 0; i < count; i++) {
    const btn = items.nth(i);
    const isVisible = await btn.isVisible().catch(() => false);
    if (!isVisible) continue;

    await btn.scrollIntoViewIfNeeded().catch(() => {});
    // Snapshot the current title before clicking so we can detect when
    // the header actually updates to reflect the newly selected session.
    const prevTitle = await getSessionTitle(page).textContent();

    await btn.click({ force: true, timeout: 5000 });

    // Wait for the session title to change (or a short timeout).  On
    // macOS the header can lag behind the click; reading textContent
    // immediately may return the previous session's title and mislead
    // the titleHasMarker calculation below.
    try {
      await page.waitForFunction(
        (prev: string) => {
          const el = document.querySelector('h2.font-semibold.truncate');
          const text = el?.textContent || '';
          return text !== prev && text.length > 0;
        },
        prevTitle ?? '',
        { timeout: 5_000, polling: 200 }
      );
    } catch {
      // Title didn't change — session may not have loaded, or this is
      // the same session.  Fall through and use whatever textContent
      // is present now.
    }

    const currentTitle = await getSessionTitle(page).textContent();
    console.log(`[test] Clicked session #${i} → title: ${currentTitle}`);
    await page.waitForTimeout(4000);

    // Session load is async (sessions.get → thread resume → message render).
    // Poll the marker in <main> — the timeout depends on whether we're
    // confident this is the right session.
    //
    // When the title itself contains the marker (the app sets the session
    // title from the first user message), we KNOW we're on the correct
    // session.  On macOS ARM64 runners the history load after a cold
    // restart can take 30-60+ seconds (APFS + SQLite WAL recovery +
    // Python bridge cold start), so we give it 120s here.
    //
    // When the title does NOT contain the marker, this might not be the
    // right session — use a shorter timeout (15s) and move on.
    const titleHasMarker = currentTitle?.includes(marker) ?? false;
    const pollTimeout = titleHasMarker ? 120_000 : 15_000;
    if (titleHasMarker) {
      console.log(
        `[test] Title confirms this is the right session — waiting up to ${pollTimeout / 1000}s for history to render`
      );
    }

    const markerInMain = page.locator('main').getByText(marker, { exact: false });
    try {
      await markerInMain.first().waitFor({ state: 'visible', timeout: pollTimeout });
      console.log(`[test] Found marker "${marker}" in session #${i}`);
      return true;
    } catch {
      // Marker not visible here — try the next sidebar session.
      if (titleHasMarker) {
        console.log(
          `[test] Session #${i} title matched but marker did not appear in ${pollTimeout / 1000}s — continuing search`
        );
      }
    }
  }

  console.log(`[test] Marker "${marker}" not found in any of ${count} sessions`);
  return false;
}

/** Ensure bridge is initialized (s?.initialized === true).
 *  Some tests need to call bridge APIs (e.g. approvals.clearPermanent)
 *  which require the AppServer to be fully registered. */
export async function waitForBridgeInitialized(page: Page, timeoutS = 30) {
  await page.evaluate(async (maxSec) => {
    for (let i = 0; i < maxSec; i++) {
      try {
        const s = await (window as any).miqi.runtime.status();
        if (s?.state === 'running' && s?.initialized) return;
      } catch {
        /* preload not injected yet */
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }, timeoutS);
}

/** Poll for sandbox manager to finish initialization.
 *
 *  On first-run (cold CI), the sandbox manager may spend 3-5 minutes
 *  doing wsl export → import → apt-get install.  Tests that use exec
 *  tools should wait here so they don't fire LLM queries into a
 *  half-initialized sandbox (which silently falls back to local exec).
 *
 *  Returns true when sandbox is ready, false on timeout. */
export async function waitForSandboxReady(page: Page, timeoutMs = 300_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  let lastLog = 0;
  while (Date.now() < deadline) {
    try {
      const status = await page.evaluate(() => (window as any).miqi.runtime.status());
      if (status?.sandbox_available === true) {
        const elapsed = Math.round((timeoutMs - (deadline - Date.now())) / 1000);
        console.log(`[test] Sandbox ready after ${elapsed}s`);
        return true;
      }
      // Log progress every 30s so CI logs show we're not hung
      const elapsed = Math.round((timeoutMs - (deadline - Date.now())) / 1000);
      if (elapsed - lastLog >= 30) {
        console.log(
          `[test] Waiting for sandbox... ${elapsed}s elapsed (state: ${status?.state}, sandbox_available: ${status?.sandbox_available})`
        );
        lastLog = elapsed;
      }
    } catch {
      /* bridge not ready yet */
    }
    await page.waitForTimeout(2000);
  }
  console.log('[test] Warning: sandbox not ready within timeout');
  return false;
}

// ─── App lifecycle ──────────────────────────────────────────────────

export interface ElectronFixture {
  electronApp: ElectronApplication;
  page: Page;
  /** Unique temporary MIQI_HOME directory for this test run */
  miqiHome: string;
  /** Derived sessions directory inside miqiHome */
  miqiSessionsDir: string;
}

/** Launch Electron app, wait for bridge ready, return { electronApp, page, miqiHome, miqiSessionsDir }.
 *
 *  - Creates a unique temporary MIQI_HOME so parallel test workers are fully isolated.
 *  - Strips ELECTRON_RUN_AS_NODE (inherited from Electron-based IDEs).
 *  - Waits for the MiQroForge main UI + bridge runtime.status() === 'running'.
 *  - `patchConfig` (optional) mutates the temp-home config JSON before it is
 *    written — used by specs that need a custom provider endpoint (e.g. the
 *    confirm-card spec points deepseek at a local mock OpenAI server).
 */
export async function launchElectronApp(
  patchConfig?: (config: any) => any,
  opts?: { bypassAll?: boolean; noConsentBypass?: boolean }
): Promise<ElectronFixture> {
  // Create unique temporary home per test worker for full isolation.
  // Parallel workers each get their own MIQI_HOME → no race on sessions/.
  const miqiHome = mkdtempSync(join(tmpdir(), 'miqi-e2e-'));
  const miqiSessionsDir = getMiqiSessionsDir(miqiHome);
  console.log(`[test] MIQI_HOME=${miqiHome}`);

  // Copy user's provider config into the temp home so the LLM backend is reachable.
  const userConfigPath = join(homedir(), '.miqi', 'config.json');
  const destConfigPath = join(miqiHome, 'config.json');
  if (existsSync(userConfigPath)) {
    cpSync(userConfigPath, destConfigPath);
  }

  // ── E2E: always enable approval bypass so tests don't hang on dialogs ──
  // This is safer than *:* wildcard pre-approve because it takes effect
  // before the bridge starts — no race with NOT_INITIALIZED or approval popups.
  const config = existsSync(destConfigPath)
    ? JSON.parse(readFileSync(destConfigPath, 'utf-8'))
    : {};
  // ── E2E: start from a clean provider state ──
  // The user's real config carries desktop.providerActivation (builtin key
  // markers). The runtime pins builtin-activated providers to their official
  // endpoint (#933), which would silently redirect specs that patch apiBase
  // to local mock servers (confirm-card, bridge-chinese-error, …) at the
  // real API — mock never receives a request. Strip the markers; specs that
  // need activation re-add it explicitly via patchConfig.
  if (config.desktop && typeof config.desktop === 'object') {
    delete (config.desktop as Record<string, unknown>).providerActivation;
  }
  if (patchConfig) patchConfig(config);
  const bypassAll = opts?.bypassAll ?? true;
  if (bypassAll) {
    config.approvals = { ...config.approvals, bypass_all: true };
  } else {
    // A spec that verifies approval cards must opt out of the global bypass.
    // The user's config.json stores camelCase keys (bypassAll) and the app
    // schema accepts both — delete BOTH forms so the bridge never sees an
    // approval bypass.
    delete config.approvals?.bypass_all;
    delete config.approvals?.bypassAll;
    delete config.approvals?.bypass_file_write_approval;
    delete config.approvals?.bypassFileWriteApproval;
  }
  // ── E2E: always disable feedback channel so tests don't hit real Feishu ──
  // Each test that needs feedback enabled can opt in by patching the config
  // after launchElectronApp.  Default OFF keeps the disabled-error path
  // verifiable for the E2E suite.
  config.channels = {
    ...config.channels,
    feishu: { ...(config.channels?.feishu ?? {}), enabled: false },
    feedback: { enabled: false, bitableAppToken: '', bitableTableId: '' },
  };
  writeFileSync(destConfigPath, JSON.stringify(config, null, 2));

  // Delete ELECTRON_RUN_AS_NODE inherited from Electron-based IDEs
  // (WorkBuddy / VSCode).  Otherwise Electron runs as plain Node.js.
  const env: Record<string, string | undefined> = { ...process.env };
  env.MIQI_HOME = miqiHome;
  delete env.ELECTRON_RUN_AS_NODE;
  // E2E default: set MIQI_E2E so the main process skips the #837 privacy-consent
  // gate (fresh userData has no stored consent). The privacy-consent spec opts
  // out via noConsentBypass to exercise the gate itself.
  if (opts?.noConsentBypass) {
    delete env.MIQI_E2E;
  } else {
    env.MIQI_E2E = '1';
  }

  // The bridge is spawned per E2E run (cold start).  If MIQI_PYTHON_PATH
  // points at a python that cannot even run (e.g. a stale uv-managed
  // interpreter whose executable is gone), findBridgeExecutable() picks it
  // first and the bridge dies at startup → the app shows "离线 MiQroForge 智能体"
  // and never streams.  Clear it so the bridge falls back to `uv run python`
  // (which resolves the current repo's venv) and actually boots.
  if (env.MIQI_PYTHON_PATH) {
    const probe = require('node:child_process').spawnSync(
      env.MIQI_PYTHON_PATH,
      ['-c', 'import sys; sys.exit(0)'],
      { encoding: 'utf8', timeout: 5000, windowsHide: true }
    );
    if (probe.status !== 0) {
      console.log(
        `[test] MIQI_PYTHON_PATH unusable (status ${probe.status}) — clearing so bridge uses the repo venv`
      );
      delete env.MIQI_PYTHON_PATH;
    }
  }

  // Isolated Electron userData per launch: without it every test instance
  // (and the dev app) shares the default profile, so sessions/UI state leak
  // between runs and tests "continue" a previous conversation (#721 实测).
  const userDataDir = join(miqiHome, 'userdata');

  const electronApp = await electron.launch({
    args: [`--user-data-dir=${userDataDir}`, APPS_DESKTOP],
    executablePath: require('electron') as string,
    env: env as Record<string, string>,
    // chromiumSandbox: false covers --no-sandbox + --disable-gpu
    // needed on CI (root user).  No-op on Windows.
    chromiumSandbox: false,
  });

  // Wait for the main window (skip splash window — 480x100, title "MiQroForge")
  let page;
  for (let i = 0; i < 100; i++) {
    const windows = electronApp.windows();
    for (const w of windows) {
      try {
        const info = await w.evaluate(() => ({ t: document.title, w: window.outerWidth }));
        if (info.w > 500 && info.t === 'MiQroForge Desktop') {
          page = w;
          break;
        }
      } catch {}
    }
    if (page) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!page) page = await electronApp.firstWindow();
  await page.waitForLoadState('domcontentloaded');

  // Capture bridge stderr and app console errors for CI debugging
  page.on('console', (msg) => {
    const t = msg.text();
    if (
      msg.type() === 'error' ||
      t.includes('[MIQI BRIDGE STDERR]') ||
      t.includes('[miqi-bridge]') ||
      t.includes('[Bridge]') ||
      t.includes('[MiQroForge]') ||
      t.includes('[e2e]')
    ) {
      console.log(`[e2e-console] ${t}`);
    }
  });

  // With noConsentBypass the app is parked on the privacy-consent gate —
  // app-title / chat input never mount, so skip the UI-readiness tail and
  // let the spec drive the gate interaction itself.
  if (opts?.noConsentBypass) {
    console.log('[test] Launched with consent gate (no MIQI_E2E)');
    return { electronApp, page, miqiHome, miqiSessionsDir };
  }

  try {
    await page.getByTestId('app-title').waitFor({ timeout: 30_000 });
    console.log('[test] App UI loaded');
  } catch {
    console.log('[test] App UI may still be loading — continuing');
  }

  // Wait for bridge AppServer to finish registering methods before checking input
  const bridgeReady = await page.evaluate(async () => {
    for (let i = 0; i < 60; i++) {
      try {
        const s = await (window as any).miqi.runtime.status();
        if (s?.state === 'running') return true;
      } catch {
        /* preload not injected yet */
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    return false;
  });
  if (!bridgeReady) console.log('[test] Warning: bridge did not reach running state');

  // Now wait for the input to be ready
  await waitForInputReady(page, 60_000);

  console.log('[test] Ready');
  return { electronApp, page, miqiHome, miqiSessionsDir };
}

/** Relaunch Electron on an EXISTING miqiHome so the prior session's SQLite
 *  history is present — for restart-recovery E2E (e.g. #490 recall-across-restart).
 *
 *  Differs from launchElectronApp only in that it reuses the given home dir
 *  (with its persisted config + sessions + runtime.db) instead of mkdtemp-ing
 *  a fresh one. Re-applies approval-bypass + disabled channels so the relaunched
 *  run doesn't hang on dialogs or hit real feedback channels. */
export async function relaunchElectronApp(
  miqiHome: string,
  opts?: { noConsentBypass?: boolean }
): Promise<ElectronFixture> {
  const miqiSessionsDir = getMiqiSessionsDir(miqiHome);

  // Re-apply the same test-safe config overrides as launchElectronApp.
  const destConfigPath = join(miqiHome, 'config.json');
  if (existsSync(destConfigPath)) {
    const config = JSON.parse(readFileSync(destConfigPath, 'utf-8'));
    config.approvals = { ...config.approvals, bypass_all: true };
    config.channels = {
      ...config.channels,
      feishu: { ...(config.channels?.feishu ?? {}), enabled: false },
      feedback: { enabled: false, bitableAppToken: '', bitableTableId: '' },
    };
    writeFileSync(destConfigPath, JSON.stringify(config, null, 2));
  }

  const env: Record<string, string | undefined> = { ...process.env };
  env.MIQI_HOME = miqiHome;
  delete env.ELECTRON_RUN_AS_NODE;
  // Same #837 consent-gate bypass logic as launchElectronApp (see above).
  if (opts?.noConsentBypass) {
    delete env.MIQI_E2E;
  } else {
    env.MIQI_E2E = '1';
  }
  // Same broken-MIQI_PYTHON_PATH fallback as launchElectronApp (see above).
  if (env.MIQI_PYTHON_PATH) {
    const relaunchProbe = require('node:child_process').spawnSync(
      env.MIQI_PYTHON_PATH,
      ['-c', 'import sys; sys.exit(0)'],
      { encoding: 'utf8', timeout: 5000, windowsHide: true }
    );
    if (relaunchProbe.status !== 0) {
      console.log(
        `[test] (relaunch) MIQI_PYTHON_PATH unusable — clearing so bridge uses the repo venv`
      );
      delete env.MIQI_PYTHON_PATH;
    }
  }

  // Isolated Electron userData per launch: without it every test instance
  // (and the dev app) shares the default profile, so sessions/UI state leak
  // between runs and tests "continue" a previous conversation (#721 实测).
  const userDataDir = join(miqiHome, 'userdata');

  const electronApp = await electron.launch({
    args: [`--user-data-dir=${userDataDir}`, APPS_DESKTOP],
    executablePath: require('electron') as string,
    env: env as Record<string, string>,
    chromiumSandbox: false,
  });

  let page;
  for (let i = 0; i < 100; i++) {
    const windows = electronApp.windows();
    for (const w of windows) {
      try {
        const info = await w.evaluate(() => ({ t: document.title, w: window.outerWidth }));
        if (info.w > 500 && info.t === 'MiQroForge Desktop') {
          page = w;
          break;
        }
      } catch {}
    }
    if (page) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  if (!page) page = await electronApp.firstWindow();
  await page.waitForLoadState('domcontentloaded');

  page.on('console', (msg) => {
    const t = msg.text();
    if (
      msg.type() === 'error' ||
      t.includes('[MIQI BRIDGE STDERR]') ||
      t.includes('[miqi-bridge]') ||
      t.includes('[Bridge]') ||
      t.includes('[MiQroForge]') ||
      t.includes('[e2e]')
    ) {
      console.log(`[e2e-console] ${t}`);
    }
  });

  // Same as launchElectronApp: parked on the consent gate, no UI tail.
  if (opts?.noConsentBypass) {
    console.log('[test] Relaunched with consent gate (no MIQI_E2E)');
    return { electronApp, page, miqiHome, miqiSessionsDir };
  }

  try {
    await page.getByTestId('app-title').waitFor({ timeout: 30_000 });
    console.log('[test] App UI loaded (relaunch)');
  } catch {
    console.log('[test] App UI may still be loading — continuing');
  }

  await waitForInputReady(page);

  const bridgeReady = await page.evaluate(async () => {
    for (let i = 0; i < 60; i++) {
      try {
        const s = await (window as any).miqi.runtime.status();
        if (s?.state === 'running') return true;
      } catch {
        /* preload not injected yet */
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
    return false;
  });
  if (!bridgeReady) console.log('[test] Warning: bridge did not reach running state (relaunch)');

  console.log('[test] Ready (relaunch)');
  return { electronApp, page, miqiHome, miqiSessionsDir };
}

/** Close the Electron app. By default also removes the temporary MIQI_HOME.
 *
 *  Pass `keepHome: true` to leave the home dir on disk — used by restart-recovery
 *  E2E (#490), which closes the app mid-test and relaunches on the SAME home so
 *  the persisted session history is present for the relaunch to recover.
 *  Deleting it would destroy the very data the test is verifying survives. */
export async function closeElectronApp(
  app: ElectronApplication,
  miqiHome?: string,
  keepHome = false
) {
  if (app) {
    // Bound the close: some tests leave an in-flight LLM/bridge request
    // behind, and Electron then waits on the bridge child process forever —
    // a stuck `app.close()` would burn the whole CI afterAll timeout (600s)
    // and then the worker force-kill (300s).  Race the close against a
    // 15s deadline and force-kill the Electron process if it overruns.
    await Promise.race([
      app.close().catch(() => {}),
      (async () => {
        await new Promise((r) => setTimeout(r, 15_000));
        try {
          app.process().kill();
        } catch {
          /* already gone */
        }
      })(),
    ]);
  }
  if (miqiHome && !keepHome && existsSync(miqiHome)) {
    // The bridge may still be tearing down children (exec bash/curl) whose
    // cwd lives under miqiHome — Windows refuses to delete a directory that
    // a dying process still holds.  Retry briefly instead of failing the
    // spec on a cleanup race.
    let cleaned = false;
    for (let i = 0; i < 8 && !cleaned; i++) {
      try {
        rmSync(miqiHome, { recursive: true, force: true });
        cleaned = true;
      } catch (e: any) {
        if (e?.code !== 'EPERM' && e?.code !== 'EBUSY') throw e;
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
    console.log(
      cleaned
        ? `[test] Cleaned up MIQI_HOME: ${miqiHome}`
        : `[test] MIQI_HOME cleanup gave up: ${miqiHome}`
    );
  }
}
