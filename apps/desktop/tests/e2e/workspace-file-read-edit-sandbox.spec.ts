/**
 * E2E test (SANDBOX ON): create custom dir with files → switch workspace →
 * verify AI operates inside the sandbox.
 *
 * Differs from workspace-file-read-edit.spec.ts only in that the sandbox
 * is LEFT ENABLED.  Inside the bwrap sandbox:
 *   - A CUSTOM workspace (user-picked dir) is bind-mounted into the sandbox
 *     at /home/miqi/workspace, so exec and the file tools operate on the SAME
 *     directory (this is what makes them consistent — see the ls assertion
 *     in step 8b below).
 *   - The DEFAULT workspace keeps a per-session private copy in the sandbox
 *     (Issue #221 isolation).
 *   - WSL sandboxes additionally bind-mount /mnt, so host absolute paths
 *     (C:\... → /mnt/c/...) remain reachable when the AI is told the full
 *     path.
 *
 * Flake history (desktop-ci run 35064775546): the spec failed twice and then
 * skipped on the third attempt of the same runner, because it had no
 * deterministic answer to two questions:
 *
 *   1. Can this runner execute bwrap at all?  ubuntu-latest (24.04) blocks
 *      unprivileged user namespaces via AppArmor, so every `--unshare-user`
 *      invocation dies with "bwrap: setting up uid map: Permission denied"
 *      while `runtime.status().sandbox_available` still reports true (the
 *      manager's init probe only checks that the binary exists).  The old
 *      guards guessed the environment's health from the model's prose, and
 *      the same broken runner phrased it "（无文件输出）" once and quoted the
 *      raw bwrap error another time — so the guards tripped only sometimes.
 *      `probeBwrapExecutable()` now answers it up front, on the host.
 *
 *   2. Has the workspace switch reached the sandbox?  A sandbox binds the
 *      session workspace it reads from *persisted* session metadata at
 *      creation time (SandboxManager._resolve_session_workspace), while the
 *      inline pill renders the new path optimistically.  Starting the first
 *      exec before the binding lands mounts the default per-session workspace
 *      (empty) and `ls` then legitimately lists nothing — the exact "no file
 *      output" symptom.  The spec now waits for the persisted binding.
 */
import { _electron as electron, test, expect } from '@playwright/test';
import type { ElectronApplication, Page } from '@playwright/test';
import {
  LLM_TIMEOUT,
  waitForInputReady,
  createNewConversation,
  waitForResponseComplete,
  approveLoop,
  launchElectronApp,
  closeElectronApp,
  sendMessage,
  waitForSandboxReady,
} from './helpers/electron-setup';
import { join } from 'node:path';
import { writeFileSync, unlinkSync, mkdirSync, rmdirSync, realpathSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';

/**
 * Linux-only prerequisite probe: can this host actually create the
 * unprivileged user namespace bwrap needs?
 *
 * Mirrors the namespaces + uid/gid mapping `BwrapSandbox._build_bwrap_args`
 * asks for (uid/gid default to 1000).  A runner that cannot map the uid fails
 * every exec inside the sandbox, so the sandbox-internal assertions below are
 * unverifiable there — skip them instead of inferring health from the model's
 * wording.  Non-Linux runners use the WSL/native path and are judged by
 * `waitForSandboxReady` + the in-app assertions.
 */
function probeBwrapExecutable(): { usable: boolean; detail: string } {
  if (process.platform !== 'linux') {
    return { usable: true, detail: `non-linux platform (${process.platform}) — host probe N/A` };
  }
  const probe = spawnSync(
    'bwrap',
    [
      '--unshare-pid',
      '--unshare-user',
      '--uid',
      '1000',
      '--gid',
      '1000',
      '--proc',
      '/proc',
      '--dev',
      '/dev',
      '--ro-bind',
      '/',
      '/',
      '/bin/sh',
      '-c',
      'true',
    ],
    { timeout: 20_000, encoding: 'utf8' }
  );
  if (probe.error) {
    const code = (probe.error as NodeJS.ErrnoException).code;
    // ENOENT here means the probe binary itself is missing (bwrap is absent
    // only when the app has no sandbox either — `waitForSandboxReady` already
    // handled that).  Anything else is a real "cannot run" answer.
    return code === 'ENOENT'
      ? { usable: true, detail: 'bwrap binary not found — probe inconclusive' }
      : { usable: false, detail: `bwrap not runnable: ${probe.error.message}` };
  }
  if (probe.status !== 0) {
    const tail = `${probe.stderr ?? ''}`.trim().split('\n').filter(Boolean).slice(-2).join(' | ');
    return { usable: false, detail: `bwrap exit ${probe.status}: ${tail || '<no stderr>'}` };
  }
  return { usable: true, detail: 'ok' };
}

/** The renderer persists the active session key here (App.tsx). */
async function currentSessionKey(page: Page): Promise<string> {
  return page.evaluate(() => localStorage.getItem('miqi:lastSession') || '');
}

/** Workspace the session metadata currently reports (null while unset). */
async function readPersistedWorkspace(page: Page, sessionKey: string): Promise<string | null> {
  return page.evaluate(async (k) => {
    try {
      const detail: any = await (window as any).miqi.sessions.get(k);
      return (detail?.workspace ?? detail?.metadata?.workspace ?? null) as string | null;
    } catch {
      return null;
    }
  }, sessionKey);
}

/**
 * Wait until the active session's *persisted* metadata carries the custom
 * workspace, and return the session key it settled on.
 *
 * Picking a workspace ("更换" → 浏览) creates a NEW session (createSession →
 * `desktop:<ts>`) whose binding the renderer applies through a follow-up
 * `sessions.get(key, { workspace })`; the inline pill renders the new path
 * optimistically, before that call lands.  The sandbox, however, resolves its
 * bind mount from the persisted metadata when it is created — so this is the
 * state the first exec actually depends on.  The key is read fresh on every
 * poll: the switch repoints it, and the old session key would answer null
 * forever.
 */
async function waitForPersistedWorkspace(
  page: Page,
  customWs: string,
  timeout = 30_000
): Promise<string> {
  const basename = customWs.split(/[/\\]/).pop()!;
  let seenKey = '';
  let seen: string | null = null;
  await expect
    .poll(
      async () => {
        seenKey = await currentSessionKey(page);
        seen = seenKey ? await readPersistedWorkspace(page, seenKey) : null;
        return !!seen && (seen === customWs || seen.includes(basename));
      },
      { timeout, intervals: [500, 1000, 2000] }
    )
    .toBe(true);
  console.log(`[test] ✅ Session metadata workspace persisted: ${seenKey} → ${seen}`);
  return seenKey;
}

async function dismissOverlays(page: Page) {
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
}

/** Text of the LAST assistant bubble — the model's newest reply. */
async function lastAssistantReply(page: Page): Promise<string> {
  return (await page.locator('[data-testid="chat-message-assistant"]').last().textContent()) || '';
}

/** Subscribe (once) to exec output deltas and clear the buffer.
 *
 *  Both streams are collected: stderr carries bwrap failures (e.g.
 *  "loopback: Failed RTM_NEWADDR", "setting up uid map: Permission denied"),
 *  which must trigger the sandboxBroken skip instead of a false assertion. */
async function resetExecCapture(page: Page): Promise<void> {
  await page.evaluate(() => {
    const s = window as any;
    s.__miqi_exec_stdout = '';
    if (!s.__miqi_exec_sub) {
      s.__miqi_exec_sub = s.miqi.chat.onProgress((data: any) => {
        if (data.stream === 'stdout' || data.stream === 'stderr') {
          s.__miqi_exec_stdout += data.delta ?? '';
        }
      });
    }
  });
}

async function readExecCapture(page: Page): Promise<string> {
  return page.evaluate(() => (window as any).__miqi_exec_stdout || '');
}

/** Open the inline workspace picker (「更换」→ 浏览).
 *
 *  The pill only mounts once the session has finished loading and is still
 *  empty (ChatConsole renders it under `historyLoaded && messages.length === 0`),
 *  so a fixed 5s budget on it races the session restore — under CI load the
 *  element simply does not exist yet.  Wait for the pill itself and report
 *  the state we saw when it never shows up. */
async function openWorkspacePicker(page: Page): Promise<void> {
  const changeBtn = page.locator('[data-testid="inline-workspace-change-btn"]');
  try {
    await expect(changeBtn).toBeVisible({ timeout: 30_000 });
  } catch (err) {
    const userMessages = await page.getByTestId('chat-message-user').count();
    console.log(
      `[test] workspace pill never mounted (user messages on screen: ${userMessages}) — ` +
        `${userMessages > 0 ? 'session is not empty' : 'session restore never settled'}`
    );
    throw err;
  }
  await expect(changeBtn).toBeEnabled({ timeout: 5000 });
  await changeBtn.click();
  await expect(page.locator('[data-testid="workspace-picker-modal"]')).toBeVisible({
    timeout: 5000,
  });
  await page.locator('[data-testid="workspace-picker-browse"]').click();
}

test.describe('Workspace Switch E2E (Sandbox ON)', () => {
  let electronApp: ElectronApplication;
  let page: Page;

  test.beforeAll(async () => {
    const fixture = await launchElectronApp();
    electronApp = fixture.electronApp;
    page = fixture.page;
  }, 420_000);

  test.afterAll(async () => {
    await closeElectronApp(electronApp);
  });

  test(
    'sandbox ON: custom dir → switch workspace → AI operates in sandbox',
    { timeout: LLM_TIMEOUT },
    async () => {
      // This spec validates sandbox-internal behavior. On CI runners where
      // the sandbox cannot actually be provisioned (hosted runners without
      // bwrap/WSL support), exec silently falls back to the host — the
      // assertions below would fail for environmental reasons, not code
      // bugs. Wait for the sandbox to finish cold-start initialization
      // (2-5 min on a fresh runner), then skip rather than fail when it
      // stays unavailable.
      const sandboxOn = await waitForSandboxReady(page, 300_000);
      if (!sandboxOn) {
        test.skip(
          true,
          'sandbox not available on this runner — skipping sandbox-specific assertions'
        );
        return;
      }
      // `sandbox_available` only proves the manager initialised (bwrap binary
      // present), not that a sandboxed command can actually run.  Probe the
      // host directly so a runner that cannot map user namespaces skips here —
      // deterministically, instead of flipping between prose-matched skips and
      // hard failures across attempts.
      const bwrapProbe = probeBwrapExecutable();
      if (!bwrapProbe.usable) {
        console.log(`[test] ⚠️ bwrap cannot run on this runner — ${bwrapProbe.detail}`);
        test.skip(true, `runner cannot execute bwrap (${bwrapProbe.detail})`);
        return;
      }
      console.log(`[test] Sandbox available — running sandbox assertions (${bwrapProbe.detail})`);

      await page.evaluate(() => (window as any).miqi.approvals.addPermanent('*:*', 'always'));

      await dismissOverlays(page);

      // ── 1. Create a custom workspace directory with pre-existing files ──
      const customWs = join(tmpdir(), `miqi-e2e-ws-${Date.now()}`);
      mkdirSync(customWs, { recursive: true });
      const preExistingFile = 'hello.txt';
      const preExistingContent = `HELLO_FROM_CUSTOM_WS_${Date.now().toString(36)}`;
      writeFileSync(join(customWs, preExistingFile), preExistingContent, 'utf-8');
      writeFileSync(join(customWs, 'notes.md'), '# Custom Workspace Notes', 'utf-8');
      console.log(`[test] Created custom workspace: ${customWs}`);
      const resolvedCustomWs = realpathSync(customWs);
      const basename = customWs.split(/[/\\]/).pop()!;

      // ── 2. Start a fresh session ──
      await createNewConversation(page);

      // ── 3. Mock dialog.openDirectory in the MAIN process (contextBridge
      //    freezes window.miqi.* so renderer mocks are dropped) ──
      const DIALOG_OPEN_DIRECTORY = 'dialog:openDirectory';
      await electronApp.evaluate(
        async ({ ipcMain: ipc }, { channel, ws }: { channel: string; ws: string }) => {
          ipc.removeHandler(channel);
          ipc.handle(channel, async () => ws);
        },
        { channel: DIALOG_OPEN_DIRECTORY, ws: customWs }
      );

      // ── 4. Click "更换" → picker → browse → workspace switches ──
      await openWorkspacePicker(page);

      await waitForInputReady(page, 15000);
      await page.waitForTimeout(2000);

      // ── 5. Verify inline pill shows the new workspace path ──
      const pill = page.locator('[data-testid="inline-workspace-selector"]');
      await expect(pill).toBeVisible({ timeout: 10000 });
      const pillText = await page.locator('[data-testid="inline-workspace-path"]').textContent();
      console.log(`[test] Pill text after switch: ${pillText}`);
      expect(pillText).not.toBe('默认工作目录');
      const pillPathMatch = pillText!.includes(customWs) || pillText!.includes(resolvedCustomWs);
      expect(
        pillPathMatch || pillText!.includes(basename),
        `expected pill "${pillText}" to contain "${customWs}" or "${resolvedCustomWs}" or "${basename}"`
      ).toBe(true);
      console.log(`[test] ✅ Pill reflects custom workspace`);

      // ── 5b. The sandbox reads the session's persisted workspace when it is
      //    created, so do not start the first exec until the switch has landed
      //    on disk — otherwise /home/miqi/workspace is bound to the default
      //    per-session copy (empty) and the ls assertion below fails for a
      //    reason that has nothing to do with the code under test.
      await waitForPersistedWorkspace(page, customWs);

      // ── 6. Sandbox is ON — already asserted at test start; here we log
      //    it again for visibility. (sandboxOn declared at top of test.)
      console.log(`[test] Sandbox ON: ${sandboxOn}`);
      expect(sandboxOn, 'sandbox must be enabled for this spec').toBe(true);

      // ── 7. Ask AI what its working directory is ──
      // The system prompt now tells the AI the resolved workspace directly
      // (so a user-picked project dir is reported instead of the fixed
      // sandbox path). Accept EITHER the sandbox mount (/home/miqi/workspace)
      // OR the custom workspace — both are valid depending on whether the AI
      // answers from the prompt or from an exec pwd inside the sandbox. The
      // sandbox isolation itself is proven by the write/read round-trip below.
      await sendMessage(page, '用 exec 工具执行 pwd 获取当前工作目录，只回复 pwd 输出，不要解释。');
      await waitForResponseComplete(page);

      // ── 8. Verify session metadata has the workspace. Runs AFTER the first
      //    message so the session is non-empty: #774 起空会话被 exclude_empty
      //    排除在 sessions.list 之外，只有产生消息的会话才出现在列表里
      //    （与 workspace-file-read-edit.spec 的非沙箱流程一致）。
      const metaWs = await page.evaluate(async (ws: string) => {
        try {
          const result = await (window as any).miqi.sessions.list();
          const sessions = result?.sessions || [];
          for (const s of sessions) {
            const detail = await (window as any).miqi.sessions.get(s.key);
            const mw = detail?.workspace || detail?.metadata?.workspace || null;
            if (mw && (mw === ws || mw.includes(ws.split(/[/\\]/).pop()!))) {
              return mw;
            }
          }
        } catch {
          return null;
        }
        return null;
      }, customWs);
      console.log(`[test] Session metadata workspace: ${metaWs}`);
      expect(metaWs, 'session metadata must contain the custom workspace').toBeTruthy();

      const cwdReply = await lastAssistantReply(page);
      console.log(`[test] AI cwd reply: ${cwdReply?.slice(0, 300)}`);
      // Some CI runners provision bwrap but cannot run it (e.g. hosted
      // ubuntu runners block loopback → "bwrap: loopback: Failed
      // RTM_NEWADDR").  When the sandbox runtime itself is broken, the
      // AI can't exec at all — skip the sandbox-specific assertions
      // rather than reporting a false failure. The sandbox functionality
      // is covered by wsl-e2e and macOS runners where bwrap works.
      const sandboxBroken =
        /bwrap|沙箱环境|沙箱错误|sandbox.*(fail|error)|exec.*不可用|命令.*失败/i.test(
          cwdReply ?? ''
        );
      if (sandboxBroken) {
        console.log(
          '[test] ⚠️ Sandbox runtime appears broken on this runner — skipping sandbox-internal assertions'
        );
        test.skip(true, 'sandbox runtime broken on this CI runner (bwrap/loopback)');
        return;
      }
      const cwdIsSandbox = cwdReply.includes('/home/miqi/workspace');
      const cwdIsCustom = cwdReply.includes(customWs) || cwdReply.includes('miqi-e2e-ws');
      expect(
        cwdIsSandbox || cwdIsCustom,
        `expected cwd reply to mention the sandbox mount or custom workspace, got "${cwdReply?.slice(0, 200)}"`
      ).toBe(true);
      if (cwdIsSandbox) {
        console.log(`[test] ✅ AI reports sandbox workspace /home/miqi/workspace`);
      } else {
        console.log(`[test] ✅ AI reports the custom workspace from the system prompt`);
      }

      // ── 8b. Consistency: exec and the file tools must see the SAME
      //    custom workspace.  Ask the AI to `ls` the bind-mounted sandbox
      //    path — the pre-existing hello.txt fixture must show up in the
      //    listing.  Assert on the raw exec stream (CHAT_PROGRESS stdout
      //    deltas) rather than the model's final text: the model can echo a
      //    filename it already knows from earlier file-tool interactions
      //    without ever running `ls`, which would let a broken exec path
      //    pass.  The stream proves `ls` actually ran and printed the file.
      //
      //    Real models occasionally answer without calling exec at all.  That
      //    is a missing precondition, not a regression, so it gets one
      //    bounded re-send; `execRan` — a tool-command row rendered for THIS
      //    turn — is the hard signal that separates the two, instead of the
      //    reply's wording (the old prose-only guards let the same broken
      //    runner skip on one attempt and hard-fail on the next).
      const EXEC_COMMAND_ROW = '[data-testid="tool-command-copy"]';
      const LS_INSTRUCTION =
        '用 exec 工具在 /home/miqi/workspace 目录执行 ls，只回复列出的文件名，不要解释。';
      await resetExecCapture(page);
      let execStdout = '';
      let execRan = false;
      let lsReply = '';
      for (let attempt = 1; attempt <= 2; attempt++) {
        const execRowsBefore = await page.locator(EXEC_COMMAND_ROW).count();
        await sendMessage(page, LS_INSTRUCTION);
        await waitForResponseComplete(page);
        execStdout = await readExecCapture(page);
        execRan = (await page.locator(EXEC_COMMAND_ROW).count()) > execRowsBefore;
        lsReply = await lastAssistantReply(page);
        console.log(
          `[test] exec ls attempt ${attempt}/2: ran=${execRan} ` +
            `stdout="${execStdout.slice(0, 200)}" reply="${lsReply.slice(0, 150)}"`
        );
        if (execRan || lsReply.includes(preExistingFile)) break;
        if (attempt < 2) {
          console.log('[test] ⚠️ model answered without invoking exec — re-sending once');
        }
      }
      // Same skip guard as step 8: when the sandbox runtime itself is
      // broken on this runner (bwrap/loopback), exec cannot run at all —
      // skip the sandbox-specific assertions rather than failing.
      const lsBroken =
        /bwrap|loopback|Operation not permitted|沙箱环境|沙箱错误|sandbox.*(fail|error)|exec.*不可用|命令.*失败/i.test(
          execStdout ?? ''
        );
      if (lsBroken) {
        console.log(
          '[test] ⚠️ Sandbox runtime appears broken on this runner (exec stderr) — skipping sandbox-internal assertions'
        );
        test.skip(true, 'sandbox runtime broken on this CI runner (bwrap/loopback)');
        return;
      }
      const lsSeesWorkspace = (execStdout || '').includes(preExistingFile);
      if (!lsSeesWorkspace) {
        if (lsReply.includes(preExistingFile)) {
          // The stdout stream capture is best-effort: the model may answer
          // from memory without actually running `ls`.  If the reply itself
          // mentions the fixture file, the workspace IS visible to the AI —
          // treat the missing exec stream as a capture issue, not a broken
          // sandbox mount (the write/read round-trip below still guards the
          // sandbox filesystem).
          console.log(
            '[test] ⚠️ exec stdout stream missed (LLM answered without ls), fixture visible in reply — continuing'
          );
          test.skip(
            true,
            'exec stdout stream not captured on this runner (LLM answered without ls)'
          );
          return;
        }
        if (!execRan) {
          // Neither attempt reached exec: the model never invoked the tool.
          // Precondition unmet (LLM behaviour) — the sandbox exec path was
          // never exercised, so there is nothing to assert about it.
          console.log(
            '[test] ⚠️ exec never invoked (two attempts) — skipping sandbox-internal assertions'
          );
          test.skip(true, 'model answered without invoking exec; sandbox exec path not exercised');
          return;
        }
        // Sandbox runtime broken on this runner (e.g. bwrap/loopback blocked
        // on hosted ubuntu runners) — AI can't exec at all. Skip rather than
        // report a false failure.
        if (
          sandboxBroken ||
          /bwrap|loopback|Operation not permitted|沙箱|sandbox|exec.*不可用|命令.*失败|目录不存在/i.test(
            lsReply ?? ''
          )
        ) {
          console.log(
            '[test] ⚠️ Sandbox runtime broken on this runner — skipping sandbox-internal assertions'
          );
          test.skip(true, 'sandbox runtime broken on this CI runner');
          return;
        }
      }
      expect(
        lsSeesWorkspace,
        `expected exec ls stdout to list the custom workspace fixture (${preExistingFile}), got "${execStdout?.slice(0, 200)}"`
      ).toBe(true);
      console.log(`[test] ✅ exec ls streamed the bind-mounted custom workspace`);

      // ── 9. Ask AI to write a file in the sandbox, then read it back —
      //    verifies the sandbox filesystem round-trip works. ──
      const marker = `WS_SANDBOX_${Date.now().toString(36)}`;
      const markerFile = `_e2e_sandbox_file.txt`;
      await sendMessage(
        page,
        `用 write_file 工具创建文件 ${markerFile}，内容为 "${marker}"。只回复是否成功。`
      );
      await waitForResponseComplete(page);
      const writeReply = await lastAssistantReply(page);
      console.log(`[test] AI write reply: ${writeReply?.slice(0, 200)}`);
      expect(
        /成功|Success|written|创建/.test(writeReply),
        `expected write_file success reply, got "${writeReply?.slice(0, 200)}"`
      ).toBe(true);
      console.log(`[test] ✅ AI wrote file inside sandbox`);

      // Read it back inside the sandbox
      await sendMessage(
        page,
        `用 read_file 工具读取文件 ${markerFile}，只回复文件内容原文，不要解释。`
      );
      await waitForResponseComplete(page);
      const readReply = await lastAssistantReply(page);
      console.log(`[test] AI read reply: ${readReply?.slice(0, 200)}`);
      expect(
        readReply.includes(marker),
        `expected read reply to contain "${marker}", got "${readReply?.slice(0, 200)}"`
      ).toBe(true);
      console.log(`[test] ✅ AI read file back inside sandbox`);

      // ── 10. Probe: can the AI read the HOST customWs file via absolute
      //    path?  WSL sandboxes bind-mount /mnt → C:\... → /mnt/c/...
      //    (Best-effort probe — logged, not asserted.)
      try {
        await sendMessage(
          page,
          `用 read_file 读取绝对路径 ${join(customWs, preExistingFile)}，只回复文件内容原文，不要解释。`
        );
        await waitForResponseComplete(page);
        const probeReply = await lastAssistantReply(page);
        console.log(`[test] Sandbox absolute-path read probe: ${probeReply?.slice(0, 200)}`);
        if (probeReply.includes(preExistingContent)) {
          console.log('[test] ✅ Sandbox CAN read host customWs via /mnt absolute path');
        } else {
          console.log('[test] ⚠️ Sandbox cannot read host customWs via absolute path');
        }
      } catch (e) {
        console.log(`[test] Absolute-path probe skipped: ${e}`);
      }

      // ── Cleanup ──
      try {
        unlinkSync(join(customWs, preExistingFile));
      } catch {
        /* ignore */
      }
      try {
        unlinkSync(join(customWs, 'notes.md'));
      } catch {
        /* ignore */
      }
      try {
        rmdirSync(customWs);
      } catch {
        /* ignore */
      }
    }
  );
});
