import { useState, useEffect, useRef, useCallback } from 'react';
import { Loader2 } from 'lucide-react';
import { RuntimeProvider, useRuntime } from './contexts/RuntimeContext';
import { TooltipProvider } from './components/ui/Tooltip';
import { Sidebar } from './components/Sidebar';
import { StatusBar } from './components/StatusBar';
import { TopBar } from './components/TopBar';
import { ApprovalBypassBanner } from './components/ApprovalBypassBanner';
import { SetupWizard } from './features/setup/SetupWizard';
import { PrivacyConsentGate } from './features/setup/PrivacyConsentGate';
import { QraftLoginStep } from './features/setup/QraftLoginStep';
import { ChatConsole } from './features/chat/ChatConsole';
import { SettingsPage, type SettingsTab } from './features/settings/SettingsPage';
import { ApprovalProvider } from './contexts/ApprovalContext';
import { UserInputProvider } from './contexts/UserInputContext';
import { RestartRequiredProvider } from './contexts/RestartRequiredContext';
import { ConfigHotReloadListener } from './components/ConfigHotReloadListener';
import { GatewayModelAutoSync } from './components/GatewayModelAutoSync';
import { InstallWarningToaster } from './components/InstallWarningToaster';
import { QraftReloginNotifier } from './components/QraftReloginNotifier';
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
import {
  PRIVACY_VERSION,
  isConsentCurrent,
  readConsentVersion,
  recordConsent,
} from './lib/privacy';
import { useQraftStatus } from './hooks/useQraftStatus';
import {
  DEFAULT_SESSION_KEY,
  resolveUnverifiedRestoreKey,
  shouldArmRestoreTimeout,
  shouldVerifyRestoredSession,
  verifyRestoredSession,
} from './sessionRestore';

type NavId =
  | 'chat'
  | 'workspace'
  | 'agents'
  | 'plan'
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

/** 启动加载屏：环境探测完成前、或登录态未知时占位（不放行主界面）。 */
function StartupLoading() {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        background: 'var(--avatar-dark)',
        fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", ui-sans-serif, system-ui, sans-serif',
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
        <div style={{ fontSize: '13px', color: 'rgba(255,255,255,0.4)' }}>正在启动 MiQroForge…</div>
      </div>
    </div>
  );
}

/**
 * 恢复校验期间聊天区的占位（#1118 第八轮，本轮 #1035 移植）。
 *
 * 这一拍**不挂载 ChatConsole**：ChatConsole 的加载 effect 会对当前 key 直接调
 * `sessions.get`（bridge 侧是 get-or-create），而此刻我们还没验明这个 key 是否
 * 还存在。视觉对齐 ChatConsole 自己的「正在连接…」态，桥起来后通常只持续一次
 * IPC 往返。只在「恢复出来的不是默认哨兵」时出现，全新 profile 看不到它。
 */
function SessionRestorePlaceholder() {
  return (
    <div
      className="flex flex-1 flex-col items-center justify-center gap-2.5"
      style={{ background: 'var(--background)' }}
    >
      <Loader2 size={16} className="animate-spin text-text-faint" />
      <p className="text-xs text-text-faint">正在恢复上次会话…</p>
    </div>
  );
}

/**
 * 恢复校验的兜底时限：桥一直起不来（或反复报错）时不能把启动挂在等待上——
 * 超时即放行，让 ChatConsole 走它自己的 10× 重试 +「正在连接…」路径。
 * 放行前**显式回退到默认哨兵**（#1118 第九轮）：超时意味着恢复出来的 key 从没
 * 被验证过，而它此刻往往正是一个幽灵（桥连不上，list 自然也答不了）——直接放行
 * 等于把幽灵 key 交给 get-or-create。桥正常时这条路径不会走到（校验在 running
 * 后立刻完成）。
 */
const RESTORE_GATE_MAX_MS = 10_000;

/**
 * 已知存在的会话 key：`sessions.list()` ∪ `sessions.listArchived()`。
 * 归档列表也要算「存在」——归档只是收起来，会话并没有消失，不该因为用户归档过
 * 就把他从上次会话里踢回默认态。
 */
async function listKnownSessionKeys(): Promise<string[]> {
  const [list, archived] = await Promise.all([
    window.miqi.sessions.list(),
    window.miqi.sessions.listArchived(),
  ]);
  return [...(list?.sessions ?? []), ...(archived?.sessions ?? [])].map((s) => s.key);
}

function AppShell() {
  const { status } = useRuntime();
  const [activeNav, setActiveNav] = useState<NavId>('chat');
  const [sessionKey, setSessionKey] = useState(() => {
    try {
      return localStorage.getItem('miqi:lastSession') || DEFAULT_SESSION_KEY;
    } catch {
      return DEFAULT_SESSION_KEY;
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
  // #1071: 判定走 readConsentVersion()（localStorage 缓存 + 主进程权威存储），
  // 双开或缓存丢失时不会重复弹门。E2E（MIQI_E2E=1 → preload 暴露 env.isE2E）
  // 跳过确认门，避免全部 E2E 被阻断。
  const [consentVersion, setConsentVersion] = useState<string | null>(() => readConsentVersion());
  const consentBypassed = PRELOAD_OK && window.miqi.env?.isE2E === true;
  const consentOk = consentBypassed || isConsentCurrent(consentVersion);
  // #1095: 登录收口 —— 未登录用户不得进入主界面（不提供「暂不登录」跳过），
  // 只能登录或退出应用。登录态由 preload 在页面脚本前同步取好，首帧即可判定。
  // E2E / smoke 默认绕过（仅未打包环境，见 main/index.ts）。
  const loginBypassed = PRELOAD_OK && window.miqi.env?.loginBypass === true;
  const { status: qraftStatus, loggedIn } = useQraftStatus();
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
    const flipped = hasActivity && !hasActivityRef.current;
    hasActivityRef.current = hasActivity;
    // 首条消息乐观挂载后立即刷新侧栏：会话此刻已向 bridge 落盘，但
    // onChatFinished 要到回合结束才触发——慢模型（思考 1 分钟+）期间侧栏
    // 会一直显示「暂无任务」，新会话卡片要等回合收尾才出现（macos-e2e
    // session-rename 播种 60s 超时的根因）。落盘可能晚于乐观挂载一拍，
    // 补 1.5s / 5s 两个延迟刷新兜底；都是纯读 sessions.list，无副作用。
    if (flipped) {
      setSessionRefreshKey((k) => k + 1);
      window.setTimeout(() => setSessionRefreshKey((k) => k + 1), 1500);
      window.setTimeout(() => setSessionRefreshKey((k) => k + 1), 5000);
    }
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

  // #1118（#1035 移植）：恢复出来的 lastSession 可能指向一个**已经不存在的会话**
  // （会话在 SessionExplorer / 设置页被删、在另一个实例里被删、或工作区换目录后
  // key 不再存在）。bridge 的 sessions.get 对未知 key 走 get_or_create——不报错、
  // 返回空会话，所以渲染层分不出「已删除」和「空会话」：界面照常显示欢迎页，之后
  // 的新建和发送都落在这个幽灵 key 上（等于用被删会话的身份开新会话）。
  // 校验只针对**启动时恢复的那一个 key**、只做一次；用户已经切走就交棒。
  // 判定逻辑见 sessionRestore.ts。三种结论的处置（#1118 第九轮 CR 定的语义）：
  //   - 存在     → 用恢复出来的 key 挂载；
  //   - 不存在   → 回退默认哨兵；
  //   - 没能验证 → **同样显式回退默认哨兵**（有界重试后仍失败 / 桥超时未就绪）。
  // 最后一条是第九轮修的缺陷：旧的兜底只清 `restorePending`、保留原 key，于是
  // ChatConsole 会带着一个**从没验证过**的 key 挂载——get-or-create 的
  // `sessions.get` 又把它当正常会话摸一遍（切走时还会被空会话 GC 删一次），正是
  // 下面两阶段启动要掐掉的形状。默认哨兵没有这个风险（它就是要回退到的目标），
  // 用户的会话在侧边栏仍然可选，所以「回退」是离线/失败路径上唯一安全的放行键。
  //
  // 第八轮（两阶段启动，#1035 移植）：校验是**异步**的，而 ChatConsole 在同一次
  // render 就挂载、加载 effect 立刻对 sessionKey 调 `sessions.get`
  // （get-or-create）——「先加载幽灵、后判定」的顺序必须掐掉。校验出结论前不挂载
  // ChatConsole（占位见 SessionRestorePlaceholder），结论落地才交棒。
  const restoredSessionKeyRef = useRef(sessionKey);
  const restoredSessionCheckedRef = useRef(false);
  const [restorePending, setRestorePending] = useState(
    () => PRELOAD_OK && shouldVerifyRestoredSession(restoredSessionKeyRef.current)
  );
  /**
   * 「没能验证」时的唯一出口：回退默认哨兵（用户已切走则交棒）后再开门。
   * 超时和校验失败两条路径共用，保证「放行时用的是哪个 key」只有一处实现。
   */
  const openGateUnverified = useCallback((reason: string) => {
    const restoredKey = restoredSessionKeyRef.current;
    const currentKey = sessionKeyRef.current;
    const nextKey = resolveUnverifiedRestoreKey(restoredKey, currentKey);
    if (nextKey !== currentKey) {
      console.warn(
        `[MiQroForge] could not verify restored session ${restoredKey} (${reason}) — ` +
          `falling back to ${nextKey} instead of mounting an unverified session`
      );
      setSessionKey(nextKey);
    }
    restoredSessionCheckedRef.current = true; // 「没能验证」也是结论：不再重复校验
    setRestorePending(false);
  }, []);
  // 兜底：桥迟迟不到 running 时不得把启动挂在等待上，超时即**显式回退默认**
  // 后放行（不是带着未验证的 key 放行，见上）。
  // #1118 第十轮：计时器只在**同意门开启后**才武装——桥的启动本身就被同意门
  // 挡着（consent-first，见下面 start 那个 effect），同意前武装等于拿用户在
  // 同意页上的停留时间消耗「等桥」的预算，超时就抢在存在性校验**开始之前**
  // 把恢复出来的 key 判负（判定见 shouldArmRestoreTimeout）。同意门打开会让
  // 本 effect 重跑（consentOk 在依赖里），预算从那一刻重新起算。
  useEffect(() => {
    if (!shouldArmRestoreTimeout(restorePending, consentOk)) return;
    const timer = window.setTimeout(
      () => openGateUnverified(`bridge not running within ${RESTORE_GATE_MAX_MS}ms`),
      RESTORE_GATE_MAX_MS
    );
    return () => window.clearTimeout(timer);
  }, [restorePending, consentOk, openGateUnverified]);
  useEffect(() => {
    if (restoredSessionCheckedRef.current) return;
    if (!PRELOAD_OK || status.state !== 'running') return;
    const restoredKey = restoredSessionKeyRef.current;
    restoredSessionCheckedRef.current = true;
    if (!shouldVerifyRestoredSession(restoredKey)) {
      setRestorePending(false); // 哨兵（或空值）不用校验，直接交棒
      return;
    }
    void (async () => {
      const verdict = await verifyRestoredSession(restoredKey, listKnownSessionKeys);
      // 用户已经切走 → 交棒：只校验启动时恢复的那一个 key，不动他现在的选择。
      if (sessionKeyRef.current === restoredKey) {
        if (verdict === 'fallback') {
          console.warn(
            `[MiQroForge] restored session ${restoredKey} no longer exists — falling back to ${DEFAULT_SESSION_KEY}`
          );
          setSessionKey(DEFAULT_SESSION_KEY);
        } else if (verdict === 'unverified') {
          // 列表拿不到（重试也失败）→ 显式回退默认，绝不带着未验证的 key 放行。
          openGateUnverified('sessions list unavailable');
          return; // openGateUnverified 已经开门
        }
      }
      setRestorePending(false); // 有结论（存在 / 不存在 / 已交棒）→ 开门
    })();
  }, [status.state, openGateUnverified]);
  // 用户已经切走（侧边栏点了别的会话）就立即交棒：校验只针对启动时恢复的那一个
  // key，用户的选择优先。
  const restoringSession = restorePending && sessionKey === restoredSessionKeyRef.current;

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

  // #1095: 登录门 —— 未登录（且未绕过）时停在登录页，不进入主界面。
  // 登录态未知（preload 同步读取失败）时给加载屏：既不闪登录页，也不放行。
  if (!loginBypassed && !loggedIn) {
    return qraftStatus === null ? (
      <StartupLoading />
    ) : (
      <TooltipProvider>
        <QraftLoginStep />
      </TooltipProvider>
    );
  }

  // Loading state
  if (needsSetup === null) {
    return <StartupLoading />;
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
        <GatewayModelAutoSync />
        <InstallWarningToaster
          onOpenSandboxSettings={() => {
            setSettingsTab('general');
            setActiveNav('settings');
          }}
        />
        {/* 平台登录失效的全局告知：横幅常驻可关闭，顶栏 chip 持续提示 */}
        <QraftReloginNotifier
          onOpenQraft={() => {
            setSettingsTab('qraft');
            setActiveNav('settings');
          }}
        />
        <ApprovalProvider>
          <UserInputProvider>
            {/* Full-height flex column */}
            <div className="flex flex-col h-screen" style={{ background: 'var(--background)' }}>
              {/* #989: 工作目录胶囊已下沉到聊天标题区（ChatConsole），顶栏不再重复展示 */}
              <TopBar
                onOpenApprovals={openApprovalSettings}
                onOpenQraft={() => {
                  setSettingsTab('qraft');
                  setActiveNav('settings');
                }}
              />
              <ApprovalBypassBanner onOpenApprovals={openApprovalSettings} />
              {/* Body row */}
              <div className="flex flex-1 overflow-hidden">
                <Sidebar
                  currentSession={sessionKey}
                  currentNav={activeNav}
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
                    {/* #1118 第八轮（#1035 移植）：恢复校验未出结论前不挂载 ChatConsole ——
                        它一挂载就会对 sessionKey 调 get-or-create 的 sessions.get（幽灵 key
                        会被先加载一遍、切走时还会被当成「上一个会话」GC 删除）。 */}
                    {restoringSession ? (
                      <SessionRestorePlaceholder />
                    ) : (
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
                        onOpenQraftSettings={() => {
                          setSettingsTab('qraft');
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
                    )}
                  </div>
                  {activeNav === 'workspace' && <WorkspacePage />}
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

              <StatusBar
                onOpenPoints={() => {
                  setSettingsTab('qraft');
                  setActiveNav('settings');
                }}
              />
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
