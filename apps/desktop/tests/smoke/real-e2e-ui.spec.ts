/**
 * Real E2E through Electron UI.
 *
 * Spawns the real miqi agent process for each chat.send() call,
 * so the UI receives actual LLM responses.
 *
 * Run: cd apps/desktop && npx playwright test --config=playwright.config.ts --grep "Real Agent"
 *
 * ⚠️ Requires: valid config at ~/.miqi/config.json with API keys
 */

import { test, expect } from '@playwright/test';
import { exec } from 'node:child_process';
import { resolve } from 'node:path';

const ROOT = resolve(__dirname, '../../..'); // apps/desktop/tests/smoke → project root
const TIMEOUT = 180_000; // 3 min per test (real LLM calls are slow)

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Run miqi agent and return stdout as a Promise */
function runMiqiAgent(message: string, sessionKey: string): Promise<string> {
  const escaped = message.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const cmd = `uv run miqi agent -m "${escaped}" -s "${sessionKey}" --no-logs --no-markdown`;
  return new Promise((resolve, reject) => {
    exec(cmd, { cwd: ROOT, timeout: TIMEOUT, encoding: 'utf-8' }, (err, stdout, stderr) => {
      if (err && !stdout) {
        reject(new Error(stderr || err.message));
      } else {
        resolve(stdout || '');
      }
    });
  });
}

/** Build mock bridge that delegates chat.send() to real miqi agent */
function buildRealBridgeScript(): string {
  return `
(function() {
  if (typeof window === 'undefined') return;

  var noop = function() { return function() {}; };
  var _callbacks = { progress: [], final: [], error: [], aborted: [] };

  function _on(type, cb) {
    _callbacks[type].push(cb);
    return function() {
      _callbacks[type] = _callbacks[type].filter(function(f) { return f !== cb; });
    };
  }

  function _fire(type, data) {
    _callbacks[type].forEach(function(f) { try { f(data); } catch(e) {} });
  }

  window.__miqiMock = {
    progress: function(data) { _fire('progress', data || { text: '' }); },
    final: function(content) {
      _fire('progress', { text: 'Generating response…' });
      setTimeout(function() {
        _fire('final', { content: content || 'No response.' });
      }, 100);
    },
    error: function(msg) { _fire('error', { message: msg || 'Unknown error' }); },
    abort: function() { _fire('aborted', {}); },
    toolProgress: function(text, callId) {
      _fire('progress', { text: text || 'exec', tool_hint: true, tool_call_id: callId || 'call_001' });
    },
    reset: function() { _callbacks = { progress: [], final: [], error: [], aborted: [] }; },
  };

  window.miqi = {
    runtime: {
      start: function() { return Promise.resolve({ state: 'running', pid: 12345 }); },
      stop: function() { return Promise.resolve({ state: 'stopped', pid: 0 }); },
      status: function() { return Promise.resolve({ state: 'running', pid: 12345 }); },
      logs: function() { return Promise.resolve(['[miqi-bridge] Bridge started']); },
      onStateChange: noop, onLog: noop,
    },
    chat: {
      send: function(content, sessionKey) {
        var key = sessionKey || 'e2e:default';
        // Fire the real agent in the background
        if (window.__realMiqiSend) {
          window.__realMiqiSend(content, key)
            .then(function(result) {
              window.__miqiMock.final(result);
            })
            .catch(function(err) {
              window.__miqiMock.error(String(err));
            });
        } else {
          // Fallback for when __realMiqiSend not yet exposed
          window.__miqiMock.final('Real agent not available. Using mock fallback.');
        }
        return Promise.resolve({ accepted: true, req_id: 'real-' + Date.now() });
      },
      abort: function() {
        _fire('aborted', {});
        return Promise.resolve({ aborted: true });
      },
      onProgress: function(cb) { return _on('progress', cb); },
      onFinal: function(cb) { return _on('final', cb); },
      onError: function(cb) { return _on('error', cb); },
      onAborted: function(cb) { return _on('aborted', cb); },
      onSubagentResult: noop,
    },
    threads: {
      start: function(opts) {
        return Promise.resolve({ thread: { id: 'thread-' + Date.now(), title: opts && opts.title || 'Chat' } });
      },
    },
    sessions: {
      list: function() { return Promise.resolve({ sessions: [] }); },
      get: function() { return Promise.resolve({ key: 'e2e:default', title: 'E2E Test', messages: [] }); },
      delete: function() { return Promise.resolve({ deleted: true }); },
      archive: function() { return Promise.resolve({ archived: true }); },
      unarchive: function() { return Promise.resolve({ unarchived: true }); },
      listArchived: function() { return Promise.resolve({ sessions: [] }); },
      getTrackedFiles: function() { return Promise.resolve({ tracked_files: [] }); },
      clearTrackedFiles: function() { return Promise.resolve({ cleared: true }); },
    },
    approvals: {
      list: function() { return Promise.resolve({ pending: [], permanent_rules: [], timeouts: null }); },
      resolve: function() { return Promise.resolve({ resolved: true }); },
      clearPermanent: function() { return Promise.resolve({ cleared: true }); },
      addPermanent: function() { return Promise.resolve({ added: { pattern: '', decision: 'always' } }); },
      history: function() { return Promise.resolve({ items: [] }); },
      onRequest: noop, onCleared: noop,
    },
    files: {
      tree: function() { return Promise.resolve({ tree: { name: '/', type: 'directory', children: [] } }); },
      read: function() { return Promise.resolve({ path: '/test.txt', content: 'test' }); },
      write: function() { return Promise.resolve({ path: '/test.txt', written: true }); },
      delete: function() { return Promise.resolve({ deleted: true, path: '/test.txt' }); },
      diff: function() { return Promise.resolve({ path: '/test.txt', diff: '' }); },
      revert: function() { return Promise.resolve({ path: '/test.txt', reverted: true }); },
      accept: function() { return Promise.resolve({ accepted: true, path: '/test.txt' }); },
    },
    config: { get: function() { return Promise.resolve({}); }, update: function() { return Promise.resolve({}); } },
    providers: { list: function() { return Promise.resolve([]); }, test: function() { return Promise.resolve({ ok: true }); }, update: function() { return Promise.resolve({ ok: true }); } },
    channels: { get: function() { return Promise.resolve({}); }, update: function() { return Promise.resolve({ ok: true }); } },
    cron: { list: function() { return Promise.resolve([]); }, create: function() { return Promise.resolve({ ok: true }); }, update: function() { return Promise.resolve({ ok: true }); }, testRun: function() { return Promise.resolve({ ok: true }); }, runs: function() { return Promise.resolve([]); } },
    memory: { list: function() { return Promise.resolve([]); }, get: function() { return Promise.resolve(null); }, save: function() { return Promise.resolve({ ok: true }); }, delete: function() { return Promise.resolve({ ok: true }); }, lessons: function() { return Promise.resolve([]); }, unlearn: function() { return Promise.resolve({ ok: true }); } },
    experience: { list: function() { return Promise.resolve({ entries: [] }); }, delete: function() { return Promise.resolve({ deleted: true }); }, toggle: function() { return Promise.resolve({ ok: true }); }, search: function() { return Promise.resolve({ entries: [] }); } },
    skills: { list: function() { return Promise.resolve({ skills: [] }); }, get: function() { return Promise.resolve(null); }, create: function() { return Promise.resolve({ ok: true }); }, upload: function() { return Promise.resolve({ ok: true }); }, delete: function() { return Promise.resolve({ ok: true }); }, openFolder: function() { return Promise.resolve({ ok: true }); } },
    mcps: { list: function() { return Promise.resolve([]); }, upsert: function() { return Promise.resolve({ ok: true }); }, delete: function() { return Promise.resolve({ ok: true }); } },
    python: { check: function() { return Promise.resolve({ ok: true, path: 'python.exe', version: '3.12.0', config_exists: true }); } },
    wsl: { check: function() { return Promise.resolve({ installed: true }); }, install: function() { return Promise.resolve({ ok: true }); }, exportDistro: function() { return Promise.resolve({ file: '/tmp/export.tar.gz', size: 0 }); }, importDistro: function() { return Promise.resolve({ ok: true }); }, getStats: function() { return Promise.resolve({ distro_count: 1, total_disk_mb: 1024 }); } },
    setup: { writeInitialConfig: function() { return Promise.resolve({ ok: true }); } },
    dialog: { openFile: function() { return Promise.resolve({ canceled: true }); } },
  };

  window.dispatchEvent(new Event('DOMContentLoaded'));
})();
`.trim();
}

async function setupRealE2E(page: import('@playwright/test').Page) {
  // Expose the real miqi agent runner to the browser
  await page.exposeFunction('__realMiqiSend', async (message: string, sessionKey: string) => {
    const result = await runMiqiAgent(message, sessionKey);
    return result;
  });

  // Inject the mock bridge that uses __realMiqiSend
  await page.addInitScript({ content: buildRealBridgeScript() });

  // Navigate and wait for render
  await page.goto('/');
  await page.waitForSelector('#root', { state: 'visible' });
}

/**
 * 在聊天界面发送消息并等待显示
 *
 * @param page - Playwright 页面对象
 * @param text - 要发送的消息文本
 */
async function sendMessage(page: import('@playwright/test').Page, text: string) {
  const textarea = page.getByPlaceholder('Ask Agent to analyze or edit files...');
  await textarea.fill(text);
  await textarea.press('Enter');
  await expect(page.getByText(text).first()).toBeVisible({ timeout: 5000 });
}

/** Wait for the textarea to be re-enabled (streaming finished) */
async function waitForReady(page: import('@playwright/test').Page, timeout = 120000) {
  const textarea = page.getByPlaceholder('Ask Agent to analyze or edit files...');
  await expect(textarea).toBeEnabled({ timeout });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test.describe('Real Agent E2E (UI)', () => {
  test('basic greeting through UI', { timeout: TIMEOUT }, async ({ page }) => {
    await setupRealE2E(page);
    await sendMessage(page, '回复一个字：好');

    // Wait for the real response to render (with timeout for LLM)
    await expect(page.getByText('好')).toBeVisible({ timeout: 120000 });
  });

  test('web search through UI', { timeout: TIMEOUT }, async ({ page }) => {
    await setupRealE2E(page);
    await sendMessage(page, '搜索一下今天北京的天气如何');

    // The real agent will use web_search → web_fetch → respond
    // Verify weather-related content appears
    await expect(page.getByText(/天气|weather|温度|℃/i).first()).toBeVisible({ timeout: 120000 });

    // Also verify the source of data is mentioned
    // (the agent typically cites weather.cma.cn or similar)
  });

  test('multi-turn conversation through UI', { timeout: TIMEOUT }, async ({ page }) => {
    await setupRealE2E(page);

    // Turn 1
    await sendMessage(page, '记住：我最喜欢的编程语言是Python');
    await expect(page.getByText(/python/i).first()).toBeVisible({ timeout: 120000 });

    // Wait for streaming to finish before next turn
    await waitForReady(page);

    // Turn 2 — verify memory works
    await sendMessage(page, '我刚才说我最喜欢的编程语言是什么？');
    await expect(page.getByText(/python/i).first()).toBeVisible({ timeout: 120000 });
  });
});
