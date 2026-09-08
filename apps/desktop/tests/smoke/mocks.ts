/**
 * Mock bridge for Playwright smoke QA tests.
 * Supports both static (read-only) and interactive modes.
 *
 * Interactive mode: the test calls window.__miqiMock.trigger*(...) to
 * simulate backend events (progress, final, error, abort).
 */

export interface MockBridgeOptions {
  runtimeStatus?: 'stopped' | 'running' | 'starting';
  sessions?: Array<{ key: string; title: string; updated_at: number; message_count: number }>;
  sessionMessages?: Record<string, unknown[]>;
  preloadOk?: boolean;
  providers?: Array<Record<string, unknown>>;
  /** model/list 目录（issue #788）。默认含 deepseek + openai + custom，供过滤逻辑验证。 */
  models?: Array<Record<string, unknown>>;
  activeModel?: string;
  activeProvider?: string | null;
  config?: Record<string, unknown>;
  /** MiQroForge 登录态（issue #726 设置页）。默认未登录。 */
  qraftStatus?: Record<string, unknown>;
  /** qraft.login 的返回结果。默认登录成功。 */
  qraftLoginResult?: Record<string, unknown>;
  /** 登录成功后的状态（login 成功时写入）。 */
  qraftLoggedInStatus?: Record<string, unknown>;
  /** qraft.pointsBalance 的返回结果。默认成功返回 270 可用积分。 */
  qraftPointsResult?: Record<string, unknown>;
  /**
   * 让 chat.send 挂起直到 mock 触发 terminal 事件（final/error/aborted），
   * 保持回合 in-flight。真实桥接下 send promise 由 terminal 事件才 settle
   * （src/main/bridge.ts TERMINAL_EVENT_TYPES），#918 改版后 ChatConsole 在
   * send settle 时立即退订本轮监听器——立即 resolve 会让发送后注入的
   * progress 事件被丢弃。默认关闭，保持其余用例的既有行为。
   */
  hangChatSend?: boolean;
}

/** Build a self-contained init script that installs the mock bridge on
 *  `window.miqi` and exposes `window.__miqiMock` for tests to fire events. */
export function buildMockBridgeScript(opts: MockBridgeOptions = {}): string {
  const runtimeStatus = opts.runtimeStatus || 'running';
  const preloadOk = opts.preloadOk !== false;
  const initialSessions = opts.sessions || [
    { key: 'sess-001', title: 'Test conversation 1', updated_at: Date.now(), message_count: 5 },
    {
      key: 'sess-002',
      title: 'Test conversation 2',
      updated_at: Date.now() - 3600000,
      message_count: 3,
    },
  ];
  const sessionsJson = JSON.stringify(initialSessions);
  const sessionMessagesJson = JSON.stringify(opts.sessionMessages || {});
  const providersJson = JSON.stringify(opts.providers || []);
  const modelsJson = JSON.stringify(
    opts.models || [
      {
        id: 'deepseek/deepseek-v4-flash',
        name: 'DeepSeek V4 Flash',
        provider: 'deepseek',
        providerDisplayName: 'DeepSeek',
        hidden: false,
        default: false,
      },
      {
        id: 'openai/gpt-4o',
        name: 'GPT-4o',
        provider: 'openai',
        providerDisplayName: 'OpenAI',
        hidden: false,
        default: false,
      },
      {
        id: 'custom/my-model',
        name: 'My Model',
        provider: 'custom',
        providerDisplayName: 'Custom',
        hidden: false,
        default: false,
      },
    ]
  );
  const activeModelJson = JSON.stringify(opts.activeModel || '');
  const activeProviderJson = JSON.stringify(opts.activeProvider ?? null);
  const configJson = JSON.stringify(opts.config || {});
  const qraftStatusJson = JSON.stringify(opts.qraftStatus || { loggedIn: false });
  const qraftLoginResultJson = JSON.stringify(
    opts.qraftLoginResult || {
      ok: true,
      account: {
        phone: '18500000000',
        sub: '19',
        username: 'U-HKY4-GB4E',
        nickname: 'MiQi测试',
      },
    }
  );
  const qraftLoggedInStatusJson = JSON.stringify(
    opts.qraftLoggedInStatus || {
      loggedIn: true,
      account: {
        phone: '18500000000',
        sub: '19',
        username: 'U-HKY4-GB4E',
        nickname: 'MiQi测试',
      },
      env: 'test',
      baseUrl: 'https://test.forge.miqroera.com/api',
      expiresAt: Date.now() + 7_199_000,
      refreshScheduledAt: Date.now() + 6_299_000,
      // #922：登录态默认网关已开通（active），模型面板/发送门禁放行。
      aiGateway: { status: 'active', configVersion: 1 },
    }
  );
  const qraftPointsResultJson = JSON.stringify(
    opts.qraftPointsResult || {
      ok: true,
      points: { availablePoints: 270, heldPoints: 0, totalEarned: 300, totalSpent: 30 },
    }
  );
  const hangChatSendJson = opts.hangChatSend === true ? 'true' : 'false';

  return `
(function() {
  if (typeof window === 'undefined') return;
  if (!${preloadOk}) return;

  // #837 隐私确认门：smoke 场景预置已同意状态（addInitScript 先于应用代码
  // 执行，localStorage 可用）。隐私门自身的交互由 e2e/privacy-consent.spec.ts
  // 覆盖。
  try {
    localStorage.setItem('miqi:privacyConsentVersion', '1.0');
  } catch (e) {}

  // Polyfill requestAnimationFrame with setTimeout so the ChatConsole
  // typewriter animation completes instantly in headless Playwright.
  // In idle / background pages, native rAF can be throttled to 1 fps
  // or stopped entirely, causing expect(...).toBeVisible() timeouts.
  window._requestAnimationFrame = window.requestAnimationFrame;
  window._cancelAnimationFrame = window.cancelAnimationFrame;
  window.requestAnimationFrame = function(fn) { return setTimeout(fn, 0); };
  window.cancelAnimationFrame = function(id) { clearTimeout(id); };

  var noop = function() { return function() {}; };
  var _config = ${configJson};
  var _configUpdates = [];
  var _modelCatalog = ${modelsJson};
  // 动态 provider 状态：update/activate/deactivate 会修改，镜像真实后端行为
  // （#929 修复分支的 E2E 评估依赖这一动态性）。
  var _providers = ${providersJson};
  var _activeModel = ${activeModelJson};
  var _activeProvider = ${activeProviderJson};

  // ── Interactive helpers ──────────────────────────────────────────
  var _callbacks = {
    progress: [],
    final: [],
    error: [],
    aborted: [],
    log: [],
    qraftStatus: [],
    'config:updated': [],
  };

  function _on(type, cb) {
    if (!_callbacks[type]) _callbacks[type] = [];
    _callbacks[type].push(cb);
    return function() {
      _callbacks[type] = _callbacks[type].filter(function(f) { return f !== cb; });
    };
  }

  function _fire(type, data) {
    _callbacks[type].forEach(function(f) { try { f(data); } catch(e) {} });
  }

  // ── Mock log data ────────────────────────────────────────────────
  var _mockLogs = [
    '[2026-07-07T10:00:00.000Z] [INFO] [bridge] Bridge process started',
    '[2026-07-07T10:00:01.000Z] [INFO] [bridge] Agent ready',
    '[2026-07-07T10:00:02.000Z] [INFO] [renderer] Runtime context initialized',
    '[2026-07-07T10:00:05.000Z] [WARN] [bridge] Slow IPC response: sessions.list (850ms)',
    '[2026-07-07T10:00:10.000Z] [ERROR] [sandbox] Sandbox timeout after 30s',
  ];

  // ── MiQroForge 登录态（issue #726，login/logout 会变更并推送状态事件） ──
  var _qraftStatus = ${qraftStatusJson};

  // ── window.miqi ──────────────────────────────────────────────────

  window.miqi = {
    runtime: {
      start: function() { return Promise.resolve({ state: 'running', pid: 12345 }); },
      stop: function() { return Promise.resolve({ state: 'stopped', pid: 0 }); },
      status: function() { return Promise.resolve({ state: '${runtimeStatus}', pid: ${runtimeStatus === 'running' ? 12345 : 0} }); },
      logs: function() { return Promise.resolve(_mockLogs.slice()); },
      onStateChange: noop,
      onLog: function(cb) { return _on('log', cb); },
      reportRendererLog: function(entry) {
        // Simulate renderer log by also firing the log callback
        if (entry && entry.message) {
          var msg = '[' + new Date().toISOString() + '] [' + (entry.level || 'INFO') + '] [' + (entry.source || 'renderer') + '] ' + entry.message;
          setTimeout(function() { _fire('log', msg); }, 0);
        }
      },
    },

    chat: {
      // 默认立即 resolve（accepted）。hangChatSend 开启时挂起直到 terminal
      // 事件，镜像真实桥接：主进程 bridge client 在 final/aborted 时 resolve、
      // error 时 reject（src/main/bridge.ts TERMINAL_EVENT_TYPES）。只有挂起
      // 时 ChatConsole 才会在整个回合期间保持 progress 监听器注册，测试才能
      // 在发送后注入 progress 事件（#902 工具行渲染回归）。
      send: function() {
        if (!${hangChatSendJson}) {
          return Promise.resolve({ accepted: true, req_id: 'req-test-001' });
        }
        return new Promise(function(resolve, reject) {
          var settled = false;
          var settleOk = function() {
            if (settled) return;
            settled = true;
            resolve({ accepted: true, req_id: 'req-test-001' });
          };
          _on('final', settleOk);
          _on('aborted', settleOk);
          _on('error', function(data) {
            if (settled) return;
            settled = true;
            reject(new Error((data && data.message) || 'Mock backend error'));
          });
        });
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
        var id = 'thread-' + Date.now();
        return Promise.resolve({ thread: { id: id, title: opts && opts.title || 'Chat' } });
      },
    },

    sessions: {
      list: function() { return Promise.resolve({ sessions: ${sessionsJson} }); },
      get: function(key) {
        var sessions = ${sessionsJson};
        var sessionMessages = ${sessionMessagesJson};
        var found = null;
        for (var i = 0; i < sessions.length; i++) {
          if (sessions[i].key === key) { found = sessions[i]; break; }
        }
        return Promise.resolve({ key: key, title: found ? found.title : key, messages: sessionMessages[key] || [], tracked_files: [] });
      },
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
      addPermanent: function() { return Promise.resolve({ added: { pattern: 'echo', decision: 'always' } }); },
      history: function() { return Promise.resolve({ items: [] }); },
      onRequest: noop,
      onCleared: noop,
    },

    files: {
      tree: function() { return Promise.resolve({ tree: { name: '/', type: 'directory', children: [] } }); },
      read: function() { return Promise.resolve({ path: '/test.txt', content: 'test' }); },
      write: function() { return Promise.resolve({ path: '/test.txt', written: true }); },
      delete: function() { return Promise.resolve({ deleted: true, path: '/test.txt' }); },
      diff: function() { return Promise.resolve({ path: '/test.txt', diff: 'no changes' }); },
      revert: function() { return Promise.resolve({ path: '/test.txt', reverted: true }); },
      accept: function() { return Promise.resolve({ accepted: true, path: '/test.txt' }); },
    },

    config: {
      get: function() { return Promise.resolve(JSON.parse(JSON.stringify(_config))); },
      update: function(payload) {
        _configUpdates.push(JSON.parse(JSON.stringify(payload)));
        // 镜像真实后端：默认模型经 config.update 修改后立即反映到 providers.list
        var model = payload && payload.agents && payload.agents.defaults && payload.agents.defaults.model;
        if (model) _activeModel = model;
        return Promise.resolve({});
      },
      // #897 ConfigHotReloadListener subscribes on mount; return an unsubscribe.
      onUpdated: function(cb) { return _on('config:updated', cb); },    },

    providers: {
      list: function() { return Promise.resolve({ providers: JSON.parse(JSON.stringify(_providers)), active_model: _activeModel, active_provider: _activeProvider }); },
      test: function() { return Promise.resolve({ ok: true }); },
      update: function(providerName, apiKey, apiBase, headers, model) {
        // 镜像真实后端：model 覆盖写为默认模型，并归属 provider
        if (model) {
          _activeModel = model;
          _activeProvider = providerName;
          for (var i = 0; i < _providers.length; i++) {
            if (_providers[i].name === providerName) _providers[i].configured_model = model;
          }
        }
        return Promise.resolve({ ok: true });
      },
      activate: function(providerName) {
        for (var i = 0; i < _providers.length; i++) {
          if (_providers[i].name === providerName) {
            _providers[i].builtin_activated = true;
            _providers[i].configured = true;
          }
        }
        return Promise.resolve({ activated: true, provider_name: providerName });
      },
      deactivate: function(providerName) {
        for (var i = 0; i < _providers.length; i++) {
          if (_providers[i].name === providerName) {
            _providers[i].builtin_activated = false;
            _providers[i].configured = false;
            _providers[i].configured_model = null;
          }
        }
        // 镜像真实后端：默认模型归属被取消激活的 provider 时重置为可用
        // 模型或清空为「未选择」（#929 / #933）
        if (_activeProvider === providerName) {
          _activeModel = '';
          _activeProvider = null;
        }
        return Promise.resolve({ deactivated: true, provider_name: providerName });
      },
    },

    models: {
      list: function() { return Promise.resolve({ models: JSON.parse(JSON.stringify(_modelCatalog)) }); },
    },

    models: {
      // 空目录 → ModelSelect 回退 FALLBACK_MODEL_PRESETS（内置 DeepSeek 下拉）。
      list: function() { return Promise.resolve({ models: [] }); },
    },

    channels: {
      get: function() { return Promise.resolve({}); },
      update: function() { return Promise.resolve({ ok: true }); },
    },

    cron: {
      list: function() { return Promise.resolve([]); },
      create: function() { return Promise.resolve({ ok: true }); },
      update: function() { return Promise.resolve({ ok: true }); },
      testRun: function() { return Promise.resolve({ ok: true }); },
      runs: function() { return Promise.resolve([]); },
    },

    memory: {
      list: function() { return Promise.resolve([]); },
      get: function() { return Promise.resolve(null); },
      save: function() { return Promise.resolve({ ok: true }); },
      delete: function() { return Promise.resolve({ ok: true }); },
      lessons: function() { return Promise.resolve([]); },
      unlearn: function() { return Promise.resolve({ ok: true }); },
    },

    experience: {
      list: function() { return Promise.resolve({ entries: [
        { id: '1', type: 'rule', title: 'Python type hints', content: 'Always use type hints in function signatures', confidence: 0.9, enabled: true, scope: 'all', source: 'agent', session_key: 'sess-001' },
        { id: '2', type: 'rule', title: 'Error handling pattern', content: 'Use try-catch with specific error types', confidence: 0.8, enabled: true, scope: 'all', source: 'agent', session_key: 'sess-001' },
        { id: '3', type: 'trace', title: 'React best practices', content: 'Use functional components and hooks', confidence: 0.7, enabled: true, scope: 'all', source: 'agent', session_key: 'sess-002' },
        { id: '4', type: 'trace', title: 'Database migration', content: 'Always test migrations before applying', confidence: 0.85, enabled: true, scope: 'all', source: 'agent', session_key: 'sess-002' },
      ] }); },
      delete: function() { return Promise.resolve({ deleted: true }); },
      toggle: function() { return Promise.resolve({ ok: true }); },
      search: function() { return Promise.resolve({ entries: [] }); },
    },

    skills: {
      list: function() { return Promise.resolve({ skills: [
        { key: 'code-reviewer', name: 'code-reviewer', description: 'Review code for bugs and style issues', source: 'workspace' },
        { key: 'pdf-generator', name: 'pdf-generator', description: 'Generate PDF documents from markdown', source: 'workspace' },
        { key: 'data-analyzer', name: 'data-analyzer', description: 'Analyze CSV and JSON data files', source: 'builtin' },
        { key: 'web-scraper', name: 'web-scraper', description: 'Scrape web pages for data extraction', source: 'builtin' },
      ] }); },
      get: function() { return Promise.resolve(null); },
      create: function() { return Promise.resolve({ ok: true }); },
      upload: function() { return Promise.resolve({ ok: true }); },
      delete: function() { return Promise.resolve({ ok: true }); },
      openFolder: function() { return Promise.resolve({ ok: true }); },
    },

    mcps: {
      list: function() { return Promise.resolve([]); },
      upsert: function() { return Promise.resolve({ ok: true }); },
      delete: function() { return Promise.resolve({ ok: true }); },
    },

    python: {
      check: function() { return Promise.resolve({ ok: true, path: 'python.exe', version: '3.12.0', config_exists: true }); },
    },

    wsl: {
      check: function() { return Promise.resolve({ installed: true }); },
      install: function() { return Promise.resolve({ ok: true }); },
      exportDistro: function() { return Promise.resolve({ file: '/tmp/export.tar.gz', size: 0 }); },
      importDistro: function() { return Promise.resolve({ ok: true }); },
      getStats: function() { return Promise.resolve({ distro_count: 1, total_disk_mb: 1024 }); },
    },

    setup: {
      writeInitialConfig: function() { return Promise.resolve({ ok: true }); },
    },

    dialog: {
      openFile: function() { return Promise.resolve({ canceled: true }); },
    },

    // -- MiQroForge 平台 OAuth2 登录 (issue #726) ------------------------------
    qraft: {
      login: function(phone, password, opts) {
        var result = JSON.parse(JSON.stringify(${qraftLoginResultJson}));
        if (result.ok) {
          _qraftStatus = JSON.parse(JSON.stringify(${qraftLoggedInStatusJson}));
          setTimeout(function() { _fire('qraftStatus', _qraftStatus); }, 0);
        }
        return Promise.resolve(result);
      },
      browserLogin: function(opts) {
        var result = JSON.parse(JSON.stringify(${qraftLoginResultJson}));
        if (result.ok) {
          _qraftStatus = JSON.parse(JSON.stringify(${qraftLoggedInStatusJson}));
          setTimeout(function() { _fire('qraftStatus', _qraftStatus); }, 0);
        }
        return Promise.resolve(result);
      },
      status: function() { return Promise.resolve(JSON.parse(JSON.stringify(_qraftStatus))); },
      refresh: function() { return Promise.resolve({ ok: true }); },
      logout: function() {
        _qraftStatus = { loggedIn: false };
        setTimeout(function() { _fire('qraftStatus', _qraftStatus); }, 0);
        return Promise.resolve({ ok: true });
      },
      pointsBalance: function() {
        var result = JSON.parse(JSON.stringify(${qraftPointsResultJson}));
        // 镜像主进程 QraftService.fetchPointsBalance：成功后缓存进状态
        // 并推送 statusChanged，状态栏/设置页等订阅方随之更新。
        if (result.ok && _qraftStatus && _qraftStatus.loggedIn) {
          _qraftStatus = Object.assign({}, _qraftStatus, { points: result.points });
          setTimeout(function() { _fire('qraftStatus', _qraftStatus); }, 0);
        }
        return Promise.resolve(result);
      },
      billingHistory: function() { return Promise.resolve([]); },
      onStatusChanged: function(cb) { return _on('qraftStatus', cb); },
    },
  };

  // ── Trigger API (for tests) ──────────────────────────────────────

  window.__miqiMock = {
    /** Simulate a progress event (tool-hint or status text) */
    progress: function(data) { _fire('progress', data || { text: '' }); },

    /** Simulate the final assistant response and trigger typewriter animation */
    final: function(content) {
      _fire('progress', { text: 'Generating response…' });
      // Small delay so ChatConsole has time to process the progress event first
      setTimeout(function() {
        _fire('final', { content: content || 'This is a test response from the mock bridge.' });
      }, 50);
    },

    /**
     * Fire the final reply preserving the EXACT content, including an empty
     * string. Unlike final(), this does NOT fall back to a default response —
     * so it can exercise the empty-reply regression path.
     */
    _fireFinal: function(content) {
      _fire('progress', { text: 'Generating response…' });
      setTimeout(function() {
        _fire('final', { content: content });
      }, 50);
    },

    /** Fire a final event immediately, without adding mock progress. */
    rawFinal: function(content) {
      _fire('final', { content: content });
    },

    /** Simulate a backend error */
    error: function(message) {
      _fire('error', { message: message || 'Mock backend error' });
    },

    /** Simulate an abort confirmation */
    abort: function() {
      _fire('aborted', {});
    },

    /** Fire a tool execution progress hint */
    toolProgress: function(text, callId) {
      _fire('progress', {
        text: text || 'exec: echo hello',
        tool_hint: true,
        tool_call_id: callId || 'call_mock_001',
      });
    },

    /**
     * Simulate a real-time log event from the backend.
     * The RuntimeContext's onLog callback will receive this string and
     * parse it into a structured RuntimeLogEntry.
     */
    triggerLog: function(message, level, source) {
      var ts = new Date().toISOString();
      var lvl = level || 'INFO';
      var src = source || 'bridge';
      _fire('log', '[' + ts + '] [' + lvl + '] [' + src + '] ' + message);
    },

    /** Clear all registered callbacks */
    reset: function() {
      _callbacks = { progress: [], final: [], error: [], aborted: [], log: [], qraftStatus: [] };
    },

    getConfigUpdates: function() {
      return JSON.parse(JSON.stringify(_configUpdates));
    },
  };

  window.dispatchEvent(new Event('DOMContentLoaded'));
})();
`.trim();
}
