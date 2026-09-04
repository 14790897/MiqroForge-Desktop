import { useState, useEffect, useRef, useCallback } from 'react';
import { RuntimeProvider, useRuntime } from './contexts/RuntimeContext';
import { TooltipProvider } from './components/ui/Tooltip';
import { Sidebar } from './components/Sidebar';
import { StatusBar } from './components/StatusBar';
import { TopBar } from './components/TopBar';
import { ApprovalBypassBanner } from './components/ApprovalBypassBanner';
import { SetupWizard } from './features/setup/SetupWizard';
import { PrivacyConsentGate } from './features/setup/PrivacyConsentGate';
import { ChatConsole } from './features/chat/ChatConsole';
import { SettingsPage, type SettingsTab } from './features/settings/SettingsPage';
import { MCPsPage } from './features/mcps/MCPsPage';
import { ApprovalProvider } from './contexts/ApprovalContext';
import { UserInputProvider } from './contexts/UserInputContext';
import { RestartRequiredProvider } from './contexts/RestartRequiredContext';
import { ConfigHotReloadListener } from './components/ConfigHotReloadListener';
import { InstallWarningToaster } from './components/InstallWarningToaster';
import { ApprovalModal } from './features/approvals/ApprovalModal';
import { CronPage } from './features/cron/CronPage';
import { MemoryPage } from './features/memory/MemoryPage';
import { ExperiencePage } from './features/experience/ExperiencePage';
import { SkillsPage } from './features/skills/SkillsPage';
import WslStatusPage from './features/wsl/WslStatusPage';
import AgentPanel from './features/agents/AgentPanel';
import PlanTracker from './features/plan/PlanTracker';
import { ApprovalsPage } from './features/approvals/ApprovalsPage';
import { PermissionsPage } from './features/permissions/PermissionsPage';
import { PluginMarket } from './features/plugins/PluginMarket';
import { SessionExplorer } from './features/sessions/SessionExplorer';
import { WorkspacePage } from './features/workspace/WorkspacePage';
import { PRIVACY_VERSION, isConsentCurrent, readStoredConsent, recordConsent } from './lib/privacy';

type NavId =
  | 'chat'
  | 'workspace'
  | 'agents'
  | 'plan'
  | 'mcps'
  | 'cron'
  | 'memory'
  | 'experience'
  | 'skills'
  | 'wsl'
  | 'permissions'
  | 'plugins'
  | 'approvals'
  | 'sessions'
  | 'settings';

const PRELOAD_OK = typeof window !== 'undefined' && !!(window as any).miqi;

function AppShell() {
  const { status } = useRuntime();
  const [activeNav, setActiveNav] = useState<NavId>('chat');
  const [sessionKey, setSessionKey] = useState(() => {
    try {
      return localStorage.getItem('miqi:lastSession') || 'desktop:default';
    } catch {
      return 'desktop:default';
    }
  });
  const [sessionRefreshKey, setSessionRefreshKey] = useState(0);
  const [renameVersion, setRenameVersion] = useState(0);
  const [runtimeReadyKey, setRuntimeReadyKey] = useState(0);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(() => {
    // Blocking python.check() stalls the render tree on cold starts
    // (see load_config() cache in miqi/config/loader.py).  Restore
    // the persisted setup flag so the UI becomes interactive immediately.
    try {
      const stored = localStorage.getItem('miqi:configReady');
      if (stored === 'true') return false;
      if (stored === 'false') return true;
    } catch {
      /* localStorage unavailable */
    }
    return null; // first launch — must check
  });
  const [canSkipSetup, setCanSkipSetup] = useState(false); // true when re-running wizard from settings
  const [settingsTab, setSettingsTab] = useState<SettingsTab>('general');
  // #837: 隐私协议同意门 — 同意状态本地持久化；协议版本更新时重新确认。
  // E2E（MIQI_E2E=1 → preload 暴露 env.isE2E）跳过确认门，避免全部 E2E 被阻断。
  const [consentVersion, setConsentVersion] = useState<string | null>(() => readStoredConsent());
  const consentBypassed = PRELOAD_OK && window.miqi.env?.isE2E === true;
  const consentOk = consentBypassed || isConsentCurrent(consentVersion);
  const [workspace, setWorkspace] = useState<string | null>(null);
  const [newSessionTrigger, setNewSessionTrigger] = useState(0);
  const pendingWorkspace = useRef<{ sessionKey: string; workspace: string } | null>(null);
  // #615: guards for the "+" reuse-empty-session check — a lock prevents
  // re-entrancy (double-click), the ref prevents acting on a stale request
  // after the user already switched sessions mid-check.
  const newSessionLockRef = useRef(false);
  const hasActivityRef = useRef(false); // 前端活动信号（流式/未落盘消息），restored from pre-#577 (issue #677)
  useEffect(() => {
    hasActivityRef.current = false; // 切会话后重置活动信号
  }, [sessionKey]);
  const handleSessionActivityChange = useCallback((hasActivity: boolean) => {
    hasActivityRef.current = hasActivity;
  }, []);
  const sessionKeyRef = useRef(sessionKey);

  useEffect(() => {
    sessionKeyRef.current = sessionKey;
  }, [sessionKey]);

  // Persist last active session so the app restores it on next launch
  useEffect(() => {
    try {
      localStorage.setItem('miqi:lastSession', sessionKey);
    } catch {
      /* localStorage unavailable */
    }
  }, [sessionKey]);

  // When the bridge becomes ready, trigger a session history reload in ChatConsole
  useEffect(() => {
    if (status.state === 'running') {
      setRuntimeReadyKey((k) => k + 1);
      // #859: 预热技能索引——启动时后台拉一次技能列表，触发后端构建进程级
      // 共享索引，避免打开「技能」面板时才首次全量扫描。PRELOAD_OK 守卫：
      // bridge 缺失时跳过，避免同步解引用 TypeError。
      if (PRELOAD_OK) {
        void window.miqi.skills.list().catch(() => {});
      }
    }
  }, [status.state]);

  useEffect(() => {
    if (PRELOAD_OK) {
      const apiKeys = Object.keys(window.miqi).join(', ');
      console.log(`[MiQroForge] preload OK — exposed namespaces: ${apiKeys}`);
    } else {
      console.error(
        '[MiQroForge] preload MISSING — window.miqi is undefined. ' +
          'Check that contextBridge.exposeInMainWorld executed.'
      );
      setNeedsSetup(false);
      return;
    }

    // #837: consent-first — 隐私协议未同意前不启动后端、不做环境探测。
    // consentOk 变化后（同意/绕过生效）再执行，此前门页已挡住整个应用。
    if (!consentOk) return;

    const check = async () => {
      try {
        // Start the bridge in parallel with python.check — on cold starts
        // check() can block for seconds (bundled bridge cold start), and
        // serializing it before runtime.start() delayed the whole app (#603).
        window.miqi.runtime.start().catch(() => {});
        const result = await window.miqi.python.check();
        const skipSetup = result.config_exists;
        setNeedsSetup(!skipSetup);
        try {
          localStorage.setItem('miqi:configReady', String(skipSetup));
        } catch {
          /* localStorage unavailable */
        }
      } catch {
        setNeedsSetup(true);
      }
    };
    check();
  }, [consentOk]);

  const handleSetupComplete = () => {
    setNeedsSetup(false);
    setCanSkipSetup(false);
    setActiveNav('chat');
    try {
      localStorage.setItem('miqi:configReady', 'true');
    } catch {
      /* ignore */
    }
  };

  const handleNewSession = async () => {
    if (newSessionLockRef.current) return;
    if (activeNav !== 'chat') setActiveNav('chat');
    const requestedKey = sessionKey;
    newSessionLockRef.current = true;
    try {
      // 前端活动信号（流式/未落盘消息）——有活动直接新建，不等后端查询
      //（restored from pre-#577, issue #677）
      if (hasActivityRef.current) {
        setNewSessionTrigger((k) => k + 1);
        return;
      }
      // #615: reuse the current session when it has no real messages on disk
      // — do NOT spawn endless empty sessions (regressed by the #577 rewrite).
      // Query the backend (source of truth) instead of frontend state.
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const detail = await Promise.race([
            window.miqi.sessions.get(requestedKey),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error('sessions.get timeout')), 1500)
            ),
          ]);
          if (sessionKeyRef.current !== requestedKey) return; // switched mid-check
          if (detail && Array.isArray(detail.messages) && detail.messages.length > 0) {
            setNewSessionTrigger((k) => k + 1); // real conversation → create new session
          }
          return; // empty → reuse current session (stay on the chat page)
        } catch {
          if (sessionKeyRef.current !== requestedKey) return;
          if (attempt < 2) await new Promise((r) => setTimeout(r, 250 * (attempt + 1)));
        }
      }
      // Bridge unavailable — fall back to reuse, never to endless empty sessions.
    } finally {
      newSessionLockRef.current = false;
    }
  };

  const handleSessionCreated = (newKey: string, workspace?: string | null) => {
    setWorkspace(workspace ?? null);
    if (workspace) pendingWorkspace.current = { sessionKey: newKey, workspace };
    else pendingWorkspace.current = null;
    setNewSessionTrigger(0); // reset so new ChatConsole instance doesn't re-open picker
    setSessionKey(newKey);
    setSessionRefreshKey((k) => k + 1);
  };

  // Deleting the currently open session: the sidebar already removed the
  // record + refreshed its list, but App still points sessionKey at the
  // deleted key, so ChatConsole keeps showing its messages.  Route through
  // the existing new-session machinery to land on a fresh empty session
  // (which renders the welcome hero) instead of a stale deleted key.
  const handleSessionDeleted = useCallback((key: string) => {
    if (key !== sessionKeyRef.current) return;
    setNewSessionTrigger((k) => k + 1);
  }, []);

  const openApprovalSettings = () => {
    setSettingsTab('approvals');
    setActiveNav('settings');
  };

  // Preload missing
  if (!PRELOAD_OK) {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: 'var(--background)' }}
      >
        <div className="flex flex-col items-center gap-4 max-w-sm text-center px-6">
          <div
            className="w-12 h-12 rounded-xl flex items-center justify-center"
            style={{ background: 'var(--danger-bg)' }}
          >
            <span className="text-xl font-bold" style={{ color: 'var(--danger)' }}>
              !
            </span>
          </div>
          <div>
            <h2 className="text-base font-semibold mb-1 text-text">预加载桥接不可用</h2>
            <p className="text-sm text-text-muted">
              应用预加载脚本注入失败。 <br />
              请重启应用。如问题持续，请检查预加载脚本路径或重新安装。{' '}
            </p>
          </div>
          <div className="text-xs text-text-faint">按 Ctrl+Shift+I 打开 DevTools 查看错误。</div>
        </div>
      </div>
    );
  }

  // Privacy consent gate (#837) — blocks the app until the current agreement
  // version is accepted. Covers portable/zip/MSI (no NSIS license page) and
  // upgrades of installed builds; NSIS users accept during installation and
  // see this once more in-app for the local persistence record.
  // 必须先于 loading 屏判定：同意前 needsSetup 恒为 null（环境探测被
  // consent-first 挡住），先判 loading 会导致门永远不可达。
  if (!consentOk) {
    return (
      <TooltipProvider>
        <PrivacyConsentGate
          onAgree={() => {
            recordConsent();
            setConsentVersion(PRIVACY_VERSION);
          }}
        />
      </TooltipProvider>
    );
  }

  // Loading state
  if (needsSetup === null) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          background: 'var(--avatar-dark)',
          fontFamily:
            'Inter, "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '12px',
          }}
        >
          <div
            style={{
              width: '44px',
              height: '44px',
              borderRadius: '10px',
              background: 'rgba(255,255,255,0.1)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontSize: '20px',
              fontWeight: 700,
            }}
          >
            M
          </div>
          <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.4)' }}>
            Loading MiQroForge…
          </div>
        </div>
      </div>
    );
  }

  // Setup wizard
  if (needsSetup) {
    return (
      <TooltipProvider>
        <SetupWizard
          onComplete={handleSetupComplete}
          onExit={
            canSkipSetup
              ? () => {
                  setNeedsSetup(false);
                  setCanSkipSetup(false);
                }
              : undefined
          }
        />
      </TooltipProvider>
    );
  }

  // Main app
  return (
    <TooltipProvider>
      <RestartRequiredProvider>
        <ConfigHotReloadListener />
        <InstallWarningToaster
          onOpenSandboxSettings={() => {
            setSettingsTab('general');
            setActiveNav('settings');
          }}
        />
        <ApprovalProvider>
          <UserInputProvider>
            {/* Full-height flex column */}
            <div className="flex flex-col h-screen" style={{ background: 'var(--background)' }}>
              <TopBar onOpenApprovals={openApprovalSettings} workspace={workspace ?? undefined} />
              <ApprovalBypassBanner onOpenApprovals={openApprovalSettings} />
              {/* Body row */}
              <div className="flex flex-1 overflow-hidden">
                <Sidebar
                  currentSession={sessionKey}
                  onSessionSelect={(key) => {
                    setWorkspace(null);
                    setSessionKey(key);
                    setActiveNav('chat');
                    setSessionRefreshKey((k) => k + 1);
                  }}
                  onNavChange={(id) => {
                    if (id === 'settings') setSettingsTab('general');
                    setActiveNav(id as NavId);
                  }}
                  refreshKey={sessionRefreshKey + runtimeReadyKey * 100000}
                  onNewSession={handleNewSession}
                  onRenamed={() => setRenameVersion((v) => v + 1)}
                  onSessionDeleted={handleSessionDeleted}
                />

                <main
                  className="flex-1 flex flex-col overflow-hidden"
                  style={{ background: 'var(--background)' }}
                >
                  <div
                    className={
                      activeNav === 'chat' ? 'flex flex-col flex-1 overflow-hidden' : 'hidden'
                    }
                  >
                    <ChatConsole
                      sessionKey={sessionKey}
                      loadTrigger={runtimeReadyKey}
                      workspace={workspace}
                      newSessionTrigger={newSessionTrigger}
                      onNewSession={(newKey: string, workspace?: string | null) =>
                        handleSessionCreated(newKey, workspace)
                      }
                      onSessionActivityChange={handleSessionActivityChange}
                      pendingWorkspace={pendingWorkspace}
                      onChatFinished={() => setSessionRefreshKey((k) => k + 1)}
                      onSessionsChanged={() => setSessionRefreshKey((k) => k + 1)}
                      renameVersion={renameVersion}
                      onRename={() => setSessionRefreshKey((k) => k + 1)}
                      onOpenProviderSettings={() => {
                        setSettingsTab('providers');
                        setActiveNav('settings');
                      }}
                      onOpenApprovals={() => {
                        setSettingsTab('approvals');
                        setActiveNav('settings');
                      }}
                      onWorkspaceLoaded={(ws) => {
                        if (ws) setWorkspace(ws);
                      }}
                    />
                  </div>
                  {activeNav === 'workspace' && <WorkspacePage />}
                  {activeNav === 'mcps' && <SettingsPage tab="mcps" />}
                  {activeNav === 'cron' && <CronPage />}
                  {activeNav === 'memory' && <SettingsPage tab="memory" />}
                  {activeNav === 'experience' && <SettingsPage tab="experience" />}
                  {activeNav === 'skills' && <SettingsPage tab="skills" />}
                  {activeNav === 'wsl' && <SettingsPage tab="wsl" />}
                  {activeNav === 'agents' && <SettingsPage tab="agents" />}
                  {activeNav === 'plan' && <PlanTracker />}
                  {activeNav === 'approvals' && <ApprovalsPage />}
                  {activeNav === 'permissions' && <SettingsPage tab="permissions" />}
                  {activeNav === 'plugins' && <SettingsPage tab="plugins" />}
                  {activeNav === 'sessions' && (
                    <SessionExplorer
                      onOpenSession={(key: string) => {
                        setWorkspace(null);
                        setSessionKey(key);
                        setActiveNav('chat');
                      }}
                    />
                  )}
                  {activeNav === 'settings' && (
                    <SettingsPage
                      tab={settingsTab}
                      onReopenSetup={() => {
                        setCanSkipSetup(true);
                        setNeedsSetup(true);
                      }}
                    />
                  )}
                </main>
              </div>

              <StatusBar />
            </div>
            <ApprovalModal />
          </UserInputProvider>
        </ApprovalProvider>
      </RestartRequiredProvider>
    </TooltipProvider>
  );
}

export default function App() {
  return (
    <RuntimeProvider>
      <AppShell />
    </RuntimeProvider>
  );
}
