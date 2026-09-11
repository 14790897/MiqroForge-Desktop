import { useState, useEffect, useRef, useCallback, useMemo, type ReactNode } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import { cn } from '../../lib/utils';
import { getCachedConfig, invalidateConfigCache } from '../../lib/configCache';
import { sanitizeUiMessage } from '../../lib/sanitizeUiMessage';
import { QraftLoginButton } from './components/QraftLoginCard';
import {
  RefreshCw,
  Download,
  Save,
  Eye,
  EyeOff,
  Check,
  RotateCcw,
  Archive,
  RotateCcw as Unarchive,
  ExternalLink,
  Copy,
  Shield,
  ShieldOff,
  Sun,
  Moon,
  Monitor,
  CloudSun,
  Snowflake,
  Trash2,
  Terminal,
  Search,
  ChevronDown,
  ChevronRight,
  Settings2,
  Boxes,
  Contrast,
  Cable,
  FolderKanban,
  Bot,
  Palette,
  Wrench,
  Database,
  BookOpen,
  ShieldCheck,
  KeyRound,
  LogIn,
  Puzzle,
  Globe,
  CloudCog,
  Clock,
  ScrollText,
  FileText,
  MessageSquare,
  Scale,
  Package,
  type LucideIcon,
} from 'lucide-react';
import { useRuntime } from '../../contexts/RuntimeContext';
import * as Tabs from '@radix-ui/react-tabs';
import {
  applyTheme,
  applyFontPreferences,
  applyEmojiPreference,
  applyUIPreferences,
  getContrastDefault,
  EMOJI_MODE_KEY,
  POINTER_CURSOR_KEY,
  REDUCE_MOTION_KEY,
  UI_FONT_SIZE_KEY,
  CODE_FONT_SIZE_KEY,
  CONTRAST_KEY,
  SIDEBAR_GLASS_KEY,
  ACCENT_COLOR_KEY,
  BACKGROUND_COLOR_KEY,
  FOREGROUND_COLOR_KEY,
  SURFACE_COLOR_KEY,
  SIDEBAR_COLOR_KEY,
  applyColorPreferences,
  type ThemeMode,
  type FontScale,
  type FontFamilyOption,
  type EmojiMode,
} from '../../lib/uiPreferences';
import { ProvidersPage } from '../providers/ProvidersPage';
import { ModelSelect } from '../providers/components/ModelSelect';
import { useQraftStatus } from '../../hooks/useQraftStatus';
import { ChannelsPage } from '../channels/ChannelsPage';
import { ApprovalsPage } from '../approvals/ApprovalsPage';
import { WorkspacePage } from '../workspace/WorkspacePage';
import { CronPage } from '../cron/CronPage';
import { ExperiencePage } from '../experience/ExperiencePage';
import { SkillsPage } from '../skills/SkillsPage';
import { MemoryPage } from '../memory/MemoryPage';
import AgentPanel from '../agents/AgentPanel';
import { PermissionsPage } from '../permissions/PermissionsPage';
import { PluginMarket } from '../plugins/PluginMarket';
import WslStatusPage from '../wsl/WslStatusPage';
import { FeedbackPage } from '../feedback/FeedbackPage';
import { QraftPage } from './components/QraftPage';
import { PrivacyPage } from './components/PrivacyPage';

export type SettingsTab =
  | 'general'
  | 'providers'
  | 'channels'
  | 'approvals'
  | 'workspace'
  | 'webtools'
  | 'appearance'
  | 'agents'
  | 'skills'
  | 'memory'
  | 'experience'
  | 'permissions'
  | 'plugins'
  | 'qraft'
  | 'cron'
  | 'wsl'
  | 'logs'
  | 'archived'
  | 'privacy'
  | 'docs'
  | 'feedback';

interface SettingsNavItem {
  value: SettingsTab;
  label: string;
  description: string;
  keywords: string[];
  icon: LucideIcon;
}

interface SettingsCategory {
  id: string;
  label: string;
  items: SettingsNavItem[];
}

const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    id: 'conversation',
    label: '对话',
    items: [
      {
        value: 'general',
        label: '通用',
        description: '智能体、工作目录与沙箱',
        keywords: ['智能体', '工作目录', '模型', '沙箱'],
        icon: Settings2,
      },
      {
        value: 'providers',
        label: '模型',
        description: 'Provider 与 API Key',
        keywords: ['provider', 'model', 'key', 'api'],
        icon: Boxes,
      },
      {
        value: 'channels',
        label: '渠道',
        description: '消息渠道接入',
        keywords: ['channel', '渠道'],
        icon: Cable,
      },
      {
        value: 'workspace',
        label: '工作区',
        description: '工作区文件与工具',
        keywords: ['workspace', '文件', '工作区'],
        icon: FolderKanban,
      },
      {
        value: 'agents',
        label: '智能体',
        description: '子智能体与协作',
        keywords: ['agent', 'subagent', '智能体'],
        icon: Bot,
      },
      {
        value: 'appearance',
        label: '外观',
        description: '主题、字号与字体',
        keywords: ['theme', 'font', '字号', '字体', '主题', '外观'],
        icon: Palette,
      },
    ],
  },
  {
    id: 'integrations',
    label: '集成',
    items: [
      {
        value: 'plugins',
        label: '插件',
        description: '插件市场与扩展',
        keywords: ['plugin', '插件'],
        icon: Puzzle,
      },
      {
        value: 'skills',
        label: '技能',
        description: '技能与工作流',
        keywords: ['skill', '技能'],
        icon: Wrench,
      },
      {
        value: 'webtools',
        label: '网页工具',
        description: 'Web 搜索与网页抓取',
        keywords: ['web', 'search', '搜索', '网页'],
        icon: Globe,
      },
      {
        value: 'qraft',
        label: 'MiQroForge 平台',
        description: 'MiQroForge 账号 OAuth2 登录',
        keywords: ['qraft', 'oauth', '账号', '登录', 'miqroera'],
        icon: CloudCog,
      },
      {
        value: 'cron',
        label: '定时任务',
        description: '定时任务与自动化',
        keywords: ['cron', '定时', 'schedule', 'automation'],
        icon: Clock,
      },
      {
        value: 'wsl',
        label: 'WSL',
        description: 'Windows 子系统状态',
        keywords: ['wsl', 'linux', 'ubuntu'],
        icon: Terminal,
      },
    ],
  },
  {
    id: 'data',
    label: '数据',
    items: [
      {
        value: 'memory',
        label: '记忆',
        description: '长期记忆与上下文',
        keywords: ['memory', '记忆', 'context'],
        icon: Database,
      },
      {
        value: 'experience',
        label: '经验',
        description: '经验沉淀与回放',
        keywords: ['experience', '经验'],
        icon: BookOpen,
      },
      {
        value: 'approvals',
        label: '审批',
        description: '执行审批与绕过',
        keywords: ['approval', '审批', 'approve'],
        icon: ShieldCheck,
      },
      {
        value: 'permissions',
        label: '权限',
        description: '文件、网络与执行权限',
        keywords: ['permission', '权限', 'sandbox'],
        icon: KeyRound,
      },
    ],
  },
  {
    id: 'system',
    label: '系统',
    items: [
      {
        value: 'logs',
        label: '日志',
        description: '运行日志与排障',
        keywords: ['log', '日志', 'debug'],
        icon: ScrollText,
      },
      {
        value: 'archived',
        label: '已归档',
        description: '历史归档任务',
        keywords: ['archive', '归档'],
        icon: Archive,
      },
      {
        value: 'privacy',
        label: '隐私协议',
        description: '隐私政策与数据使用',
        keywords: ['privacy', '隐私', '协议', 'legal'],
        icon: Scale,
      },
      {
        value: 'docs',
        label: '文档',
        description: '产品与开发文档',
        keywords: ['docs', 'documentation', '文档'],
        icon: FileText,
      },
      {
        value: 'feedback',
        label: '反馈',
        description: '问题与功能建议',
        keywords: ['feedback', '反馈', 'suggestion'],
        icon: MessageSquare,
      },
    ],
  },
];

// ---- Helpers ----
function getNestedStr(obj: Record<string, unknown>, ...keys: string[]): string {
  let cur: unknown = obj;
  for (const k of keys) {
    if (cur == null || typeof cur !== 'object') return '';
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur == null ? '' : String(cur);
}

import { SettingsToggle } from './components/SettingsToggle';

function SandboxToggle() {
  return (
    <SettingsToggle
      icon={Shield}
      testId="sandbox-toggle"
      label="沙箱"
      getInitial={(cfg) => cfg?.tools?.sandbox?.enabled ?? true}
      onToggle={async (next) => {
        const r: any = await window.miqi.sandbox.setEnabled(next);
        if (r?.error) throw new Error(r.error);
      }}
      pollReady
      readyLabel="已开启（推荐）"
      togglingLabel="正在安装依赖…"
    />
  );
}

function AllowSystemInstallsToggle() {
  return (
    <SettingsToggle
      icon={Package}
      testId="allow-system-installs-toggle"
      label="允许系统包安装"
      getInitial={(cfg) => cfg?.tools?.sandbox?.allowSystemInstalls ?? false}
      onToggle={async (next) => {
        const r: any = await window.miqi.sandbox.setAllowSystemInstalls(next);
        if (r?.error) throw new Error(r.error);
      }}
      readyLabel="已开启"
      togglingLabel="正在保存…"
    />
  );
}

function InlineExecOutputToggle() {
  const toggle = async (next: boolean) => {
    await window.miqi.config.update({ desktop: { ui: { inlineExecOutput: next } } });
    invalidateConfigCache();
  };
  return (
    <SettingsToggle
      icon={Terminal}
      testId="inline-exec-output-toggle"
      label="已开启"
      getInitial={(cfg) => cfg?.desktop?.ui?.inlineExecOutput === true}
      onToggle={toggle}
    />
  );
}

// ---- Trusted directories (tools.extra_roots) ----
function TrustedDirectoriesSection() {
  const [roots, setRoots] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getCachedConfig()
      .then((cfg) => {
        const list = (cfg as any)?.tools?.extraRoots;
        setRoots(Array.isArray(list) ? list.map(String) : []);
      })
      .catch(() => setRoots([]));
  }, []);

  const persist = async (next: string[]) => {
    await window.miqi.config.update({ tools: { extraRoots: next } });
    invalidateConfigCache();
    setRoots(next);
  };

  const addRoot = async () => {
    const dir = await window.miqi.dialog.openDirectory();
    if (!dir) return;
    const cur = roots ?? [];
    if (cur.includes(dir)) return;
    setBusy(true);
    try {
      await persist([...cur, dir]);
    } catch {
      /* ignore */
    }
    setBusy(false);
  };

  const removeRoot = async (dir: string) => {
    const cur = roots ?? [];
    setBusy(true);
    try {
      await persist(cur.filter((r) => r !== dir));
    } catch {
      /* ignore */
    }
    setBusy(false);
  };

  return (
    <div className="pt-4 border-t border-[var(--border-subtle)]">
      <h3
        className="text-subheading text-[var(--text)] mb-1"
        data-testid="settings-trusted-dirs-title"
      >
        信任目录
      </h3>
      <p className="text-xs text-[var(--text-faint)] mb-3">
        AI 写入这些目录之外的位置时会弹出授权确认。允许后选择「本目录不再询问」会自动加入此列表。
      </p>
      {roots !== null && roots.length > 0 && (
        <ul className="flex flex-col gap-1 mb-3">
          {roots.map((dir) => (
            <li
              key={dir}
              className="flex items-center justify-between gap-2 rounded-md border border-[var(--border-subtle)] px-2 py-1.5"
            >
              <span className="text-xs font-mono text-[var(--text)] truncate">{dir}</span>
              <button
                onClick={() => removeRoot(dir)}
                disabled={busy}
                className="p-1 rounded text-[var(--text-faint)] hover:text-[var(--danger)] transition-colors"
                title="移除"
              >
                <Trash2 size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <Button variant="outline" size="sm" onClick={addRoot} disabled={busy}>
        <FolderKanban size={14} />
        添加目录
      </Button>
    </div>
  );
}

// ---- General Config Tab ----
function GeneralTab({
  onReopenSetup,
  onGoToQraft,
}: {
  onReopenSetup?: () => void;
  onGoToQraft: () => void;
}) {
  const [agentName, setAgentName] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [model, setModel] = useState('');
  const [temperature, setTemperature] = useState('');
  const [maxTokens, setMaxTokens] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const { loggedIn, gatewayActive, aiGatewayKnown } = useQraftStatus();
  // #922 网关门控：未登录引导登录；登录且网关 active（或未下发）可改模型。
  const canUseModel = loggedIn && (gatewayActive || !aiGatewayKnown);
  const gatewayBlocked = loggedIn && aiGatewayKnown && !gatewayActive;
  const [saveError, setSaveError] = useState<string | null>(null);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    getCachedConfig()
      .then((cfg) => {
        setAgentName(getNestedStr(cfg, 'agents', 'defaults', 'name'));
        setWorkspace(getNestedStr(cfg, 'agents', 'defaults', 'workspace'));
        setModel(getNestedStr(cfg, 'agents', 'defaults', 'model'));
        const temp = getNestedStr(cfg, 'agents', 'defaults', 'temperature');
        setTemperature(temp);
        const mt = getNestedStr(cfg, 'agents', 'defaults', 'maxTokens');
        setMaxTokens(mt);
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    // 新一轮保存开始时清掉上一次的成功状态与定时器：失败不应残留
    // 「已保存」，旧定时器也不应提前清掉新的成功状态（#933 review）。
    setSaved(false);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    try {
      const defaults: Record<string, unknown> = {
        name: agentName,
        workspace,
        temperature: temperature === '' ? '' : parseFloat(temperature),
        maxTokens: maxTokens === '' ? '' : parseInt(maxTokens),
      };
      // 模型下拉只允许预设选择：未选择（历史遗留模型不在可用目录中）时
      // 不把空值存回配置 —— 后端现在会拒绝空模型（#929 收口）。
      if (model) {
        defaults.model = model;
      }
      await window.miqi.config.update({ agents: { defaults } });
      invalidateConfigCache();
      setSaved(true);
      savedTimerRef.current = setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      // 后端收口（#929）会拒绝无法解析/无凭据的模型值 —— 不能再静默吞掉，
      // 否则用户看到「点了保存没反应」（#929 review）。
      setSaveError(sanitizeUiMessage(err instanceof Error ? err.message : String(err)));
    }
    setSaving(false);
  };

  return (
    <div className="p-6 max-w-lg flex flex-col gap-4">
      <h3 className="text-subheading text-[var(--text)]">智能体配置</h3>

      <div className="flex flex-col gap-1.5">
        <label className="text-size-sm font-medium text-[var(--text-muted)]">智能体名称</label>
        <Input
          value={agentName}
          onChange={(e) => setAgentName(e.target.value)}
          placeholder="miqi"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-size-sm font-medium text-[var(--text-muted)]">工作目录</label>
        <div className="flex gap-2">
          <Input
            value={workspace}
            onChange={(e) => setWorkspace(e.target.value)}
            placeholder="~/.miqi/workspace"
            className="flex-1"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              const dir = await window.miqi.dialog.openFile();
              if (dir) setWorkspace(dir);
            }}
          >
            浏览
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-size-sm font-medium text-[var(--text-muted)]">默认模型</label>
        {canUseModel ? (
          <ModelSelect value={model} onChange={setModel} />
        ) : gatewayBlocked ? (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5">
            <span className="text-sm text-[var(--text-muted)]">
              AI 网关未就绪（平台开通中或不可用），暂不可选模型
            </span>
            <Button
              variant="outline"
              size="sm"
              onClick={onGoToQraft}
              data-testid="general-go-gateway"
            >
              <LogIn size={14} />
              查看平台账号
            </Button>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)] px-3 py-2.5">
            <span className="text-sm text-[var(--text-muted)]">登录后使用平台内置模型</span>
            {/* #1000 未登录拦截：一键浏览器登录（原「去登录」仅跳设置页） */}
            <QraftLoginButton testId="general-login-btn" size="sm" busyLabel="等待授权中…" />
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <label className="text-size-sm font-medium text-[var(--text-muted)]">Temperature</label>
          <Input
            type="number"
            min="0"
            max="2"
            step="0.05"
            value={temperature}
            onChange={(e) => setTemperature(e.target.value)}
            placeholder="0.1"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-size-sm font-medium text-[var(--text-muted)]">Max Tokens</label>
          <Input
            type="number"
            min="256"
            max="200000"
            step="256"
            value={maxTokens}
            onChange={(e) => setMaxTokens(e.target.value)}
            placeholder="8192"
          />
        </div>
      </div>

      <Button onClick={handleSave} disabled={saving} className="self-start mt-2">
        {saved ? <Check size={14} /> : <Save size={14} />}
        {saved ? '已保存' : '保存'}
      </Button>

      {saveError && (
        <div className="rounded-lg px-3 py-2 bg-[var(--accent-soft)] text-xs text-[var(--danger)]">
          {saveError}
        </div>
      )}

      {/* ---- Sandbox ---- */}
      <div className="pt-4 border-t border-[var(--border-subtle)]">
        <h3
          className="text-subheading text-[var(--text)] mb-1"
          data-testid="settings-sandbox-section-title"
        >
          沙箱隔离
        </h3>
        <p className="text-xs text-[var(--text-faint)] mb-3">
          开启后 AI 的文件操作和命令执行在 WSL2 bwrap 沙箱中运行，保护主机安全。
          关闭后直接操作主机文件系统（无隔离，性能更好但风险更高）。
        </p>
        <SandboxToggle />
        <div className="mt-3 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface)] px-3 py-2.5">
          <AllowSystemInstallsToggle />
          <p className="text-size-xs text-[var(--text-muted)] mt-1">
            开启后，AI 可将 apt 等系统包安装请求转交给 WSL，并以{' '}
            <span className="text-[var(--accent)] font-medium">root 权限</span> 执行（仅 Windows +
            WSL）。此权限会{' '}
            <span className="text-[var(--accent)] font-medium">持续保存到后续会话</span>
            。软件包安装脚本可能以 root 权限执行代码。仅在你信任 AI 操作时开启。
          </p>
        </div>
      </div>

      {/* ---- Inline Exec Output ---- */}
      <div className="pt-4 border-t border-[var(--border-subtle)]">
        <h3
          className="text-subheading text-[var(--text)] mb-1"
          data-testid="settings-inline-exec-output-title"
        >
          内联终端输出
        </h3>
        <p className="text-xs text-[var(--text-faint)] mb-3">
          关闭后工具调用的 exec 结果以普通文本显示，不再包裹黑底终端框。
          当沙箱路径策略过滤掉输出时，关闭此开关可避免出现空盒子。
        </p>
        <InlineExecOutputToggle />
      </div>

      <TrustedDirectoriesSection />

      {/* ---- Danger Zone ---- */}
      <div className="mt-6 pt-4 border-t border-[var(--border-subtle)]">
        <h3 className="text-subheading text-[var(--text)] mb-1">重新配置</h3>
        <p className="text-xs text-[var(--text-faint)] mb-3">
          重新运行配置向导，可修改 Python 路径、WSL2 环境和模型 Provider 等初始设置。
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={onReopenSetup}
          className="text-[var(--warning)] border-[var(--warning)] hover:bg-[var(--warning)] hover:bg-opacity-10"
        >
          <RotateCcw size={14} />
          重新运行配置向导
        </Button>
      </div>
    </div>
  );
}

// ---- Web Tools Tab ----
function WebToolsTab() {
  // ---- Web Search ----
  const [searchProvider, setSearchProvider] = useState('auto');
  const [tavilyKey, setTavilyKey] = useState('');
  const [braveKey, setBraveKey] = useState('');
  const [hasDeepseekKey, setHasDeepseekKey] = useState(false);
  const [currentModel, setCurrentModel] = useState('');

  // ---- Web Fetch ----
  const [fetchProvider, setFetchProvider] = useState('builtin');
  const [fetchOllamaBase, setFetchOllamaBase] = useState('');
  const [fetchOllamaKey, setFetchOllamaKey] = useState('');

  // ---- Papers ----
  const [papersProvider, setPapersProvider] = useState('hybrid');
  const [s2ApiKey, setS2ApiKey] = useState('');

  const [showKeys, setShowKeys] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    getCachedConfig()
      .then((cfg) => {
        const storedSearchProvider =
          getNestedStr(cfg, 'tools', 'web', 'search', 'provider') || 'auto';
        // 旧值 hybrid → auto（后端 schema 已归一，前端兜底一次）
        setSearchProvider(storedSearchProvider === 'hybrid' ? 'auto' : storedSearchProvider);
        setTavilyKey(getNestedStr(cfg, 'tools', 'web', 'search', 'tavilyApiKey'));
        setBraveKey(getNestedStr(cfg, 'tools', 'web', 'search', 'braveApiKey'));
        // 对话模型配置了官方 DeepSeek → 联网搜索零配置可用（#844）；
        // 与后端 _is_official_deepseek_base 一致：https + hostname 精确匹配
        //（子串正则会放过 http://api.deepseek.com 等，外部审阅 #844）
        const dsKey =
          getNestedStr(cfg, 'providers', 'deepseek', 'apiKey') ||
          getNestedStr(cfg, 'providers', 'deepseek', 'api_key');
        const dsBase =
          getNestedStr(cfg, 'providers', 'deepseek', 'apiBase') ||
          getNestedStr(cfg, 'providers', 'deepseek', 'api_base') ||
          '';
        let dsOfficial = !dsBase; // base 为空时后端默认官方地址
        if (dsBase) {
          try {
            const u = new URL(dsBase);
            dsOfficial = u.protocol === 'https:' && u.hostname === 'api.deepseek.com';
          } catch {
            dsOfficial = false;
          }
        }
        setHasDeepseekKey(!!dsKey && dsOfficial);
        // 当前对话模型名（对应模型的联网搜索判定，与后端 _model_is_deepseek 一致）
        setCurrentModel(getNestedStr(cfg, 'agents', 'defaults', 'model') || '');
        setFetchProvider(getNestedStr(cfg, 'tools', 'web', 'fetch', 'provider') || 'builtin');
        setFetchOllamaBase(getNestedStr(cfg, 'tools', 'web', 'fetch', 'ollamaApiBase'));
        setFetchOllamaKey(getNestedStr(cfg, 'tools', 'web', 'fetch', 'ollamaApiKey'));
        setPapersProvider(getNestedStr(cfg, 'tools', 'papers', 'provider') || 'hybrid');
        setS2ApiKey(getNestedStr(cfg, 'tools', 'papers', 'semanticScholarApiKey'));
      })
      .catch(() => {});
  }, []);

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    // 与 GeneralTab 一致：新一轮保存开始时清掉上一次的成功状态与定时器
    //（#933 review）。
    setSaved(false);
    if (savedTimerRef.current) clearTimeout(savedTimerRef.current);
    try {
      await window.miqi.config.update({
        tools: {
          web: {
            search: {
              provider: searchProvider,
              tavilyApiKey: tavilyKey,
              braveApiKey: braveKey,
            },
            fetch: {
              provider: fetchProvider,
              ollamaApiBase: fetchOllamaBase,
              ollamaApiKey: fetchOllamaKey,
            },
          },
          papers: {
            provider: papersProvider,
            semanticScholarApiKey: s2ApiKey,
          },
        },
      });
      invalidateConfigCache();
      setSaved(true);
      savedTimerRef.current = setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      // 不再静默吞掉（#929 review）：用户需要看到保存为什么失败
      setSaveError(sanitizeUiMessage(err instanceof Error ? err.message : String(err)));
    }
    setSaving(false);
  };

  const ModeBtn = ({
    value,
    current,
    set,
    label,
  }: {
    value: string;
    current: string;
    set: (v: string) => void;
    label: string;
  }) => (
    <button
      onClick={() => set(value)}
      className={cn(
        'settings-hover-tab px-3 py-1.5 rounded-lg text-body-sm border',
        current === value
          ? 'bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]'
          : 'bg-[var(--surface)] text-[var(--text-muted)] border-[var(--border)] hover:border-[var(--accent)]'
      )}
    >
      {label}
    </button>
  );

  const KeyGuide = ({
    name,
    siteUrl,
    steps,
  }: {
    name: string;
    siteUrl: string;
    steps: string[];
  }) => {
    const [open, setOpen] = useState(false);
    return (
      <div className="text-size-xs">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="text-[var(--accent)] hover:underline cursor-pointer"
        >
          {open ? '收起' : '如何获取'} {name} Key？
        </button>
        {open && (
          <ol className="mt-1.5 list-decimal pl-4 space-y-1 text-[var(--text-muted)]">
            {steps.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
            <li>
              打开{' '}
              <a
                href={siteUrl}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--accent)] underline break-words"
              >
                {siteUrl}
              </a>
            </li>
          </ol>
        )}
      </div>
    );
  };

  // 当前实际生效的搜索引擎（镜像后端 SearchProviderManager._chain 逻辑）
  const currentEngine = (() => {
    if (searchProvider !== 'auto') {
      return searchProvider === 'deepseek'
        ? 'DeepSeek'
        : searchProvider === 'tavily'
          ? 'Tavily'
          : searchProvider === 'brave'
            ? 'Brave'
            : 'DuckDuckGo';
    }
    // 与后端 _model_is_deepseek 一致：trim + 小写后再判定（外部审阅 #844）
    const m = currentModel.trim().toLowerCase();
    const isDeepseekModel =
      m === 'deepseek' || m.startsWith('deepseek/') || m.startsWith('deepseek-');
    if (isDeepseekModel && hasDeepseekKey) return 'DeepSeek';
    if (tavilyKey) return 'Tavily';
    if (braveKey) return 'Brave';
    return 'DuckDuckGo';
  })();
  // auto 下 currentEngine 已含全部引擎判定；非 auto（显式选择）恒视为"已开启"
  const searchEnabled = searchProvider !== 'auto' || currentEngine !== 'DuckDuckGo';

  return (
    <div className="p-6 max-w-lg flex flex-col gap-6">
      {/* ---- Web Search ---- */}
      <section className="flex flex-col gap-3">
        <h3 className="text-subheading text-[var(--text)]">Web 搜索</h3>
        {/* 状态行：默认可见，说人话 */}
        <div className="flex items-start gap-2 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface)] px-3 py-2.5">
          <Globe size={15} className="mt-0.5 shrink-0 text-[var(--text-muted)]" />
          <div className="flex flex-col gap-0.5">
            <p className="text-size-sm font-medium text-[var(--text)]">
              联网搜索：{searchEnabled ? '已开启' : '基础可用'}
              <span className="text-size-xs text-[var(--text-muted)]">
                {' '}
                · 当前引擎：{currentEngine}
              </span>
            </p>
            <p className="text-size-xs text-[var(--text-muted)]">
              {searchProvider === 'deepseek' && !hasDeepseekKey
                ? '未检测到 DeepSeek 对话模型密钥，请先在「模型」页配置后使用。'
                : searchEnabled
                  ? '自动使用你的模型密钥联网搜索，无需额外配置；模型或网络不可用时，自动回落到其它搜索源'
                  : '当前模型未启用官方联网搜索，已自动使用 DuckDuckGo 基础搜索；配置 DeepSeek 对话模型或 Tavily/Brave 密钥后自动升级'}
            </p>
          </div>
        </div>
        {/* 高级设置：默认折叠，只有想自定义引擎的用户才展开 */}
        <details className="group text-size-xs text-[var(--text-muted)]">
          <summary className="flex cursor-pointer select-none list-none items-center gap-1">
            <ChevronRight size={14} className="transition-transform group-open:rotate-90" />
            自定义搜索引擎（可选）
          </summary>
          <div className="mt-3 flex flex-col gap-3">
            <div className="flex gap-2 flex-wrap">
              <ModeBtn value="auto" current={searchProvider} set={setSearchProvider} label="Auto" />
              <ModeBtn
                value="deepseek"
                current={searchProvider}
                set={setSearchProvider}
                label="DeepSeek"
              />
              <ModeBtn
                value="tavily"
                current={searchProvider}
                set={setSearchProvider}
                label="Tavily"
              />
              <ModeBtn
                value="brave"
                current={searchProvider}
                set={setSearchProvider}
                label="Brave"
              />
              <ModeBtn
                value="ddgs"
                current={searchProvider}
                set={setSearchProvider}
                label="DuckDuckGo"
              />
            </div>
            <p className="text-size-xs text-[var(--text-muted)]">
              Auto：优先使用对话模型对应的联网搜索（如 DeepSeek，复用模型密钥）；配置了 Tavily/Brave
              密钥时也会被自动使用；最后 DuckDuckGo 兜底
            </p>
            {searchProvider === 'deepseek' && (
              <p className="text-size-xs text-[var(--text-muted)]">
                仅使用 DeepSeek 联网搜索（失败不回落到其它引擎）；复用对话模型密钥，无需在此填写。
              </p>
            )}
            {(searchProvider === 'auto' || searchProvider === 'tavily') && (
              <div className="flex flex-col gap-1.5">
                <label className="text-size-sm font-medium text-[var(--text-muted)]">
                  Tavily API Key
                </label>
                <div className="flex gap-2">
                  <Input
                    type={showKeys ? 'text' : 'password'}
                    value={tavilyKey}
                    onChange={(e) => setTavilyKey(e.target.value)}
                    placeholder="tvly-..."
                    className="flex-1 font-mono text-xs"
                  />
                  <Button variant="ghost" size="icon" onClick={() => setShowKeys((v) => !v)}>
                    {showKeys ? <EyeOff size={14} /> : <Eye size={14} />}
                  </Button>
                </div>
                <KeyGuide
                  name="Tavily"
                  siteUrl="https://tavily.com"
                  steps={[
                    '注册 / 登录（支持 Google 一键登录）',
                    '控制台左侧菜单点 API Keys',
                    '点 Create API Key 创建密钥',
                    '复制 tvly- 开头的密钥，粘贴到上方输入框',
                  ]}
                />
              </div>
            )}
            {(searchProvider === 'auto' || searchProvider === 'brave') && (
              <div className="flex flex-col gap-1.5">
                <label className="text-size-sm font-medium text-[var(--text-muted)]">
                  Brave API Key
                </label>
                <div className="flex gap-2">
                  <Input
                    type={showKeys ? 'text' : 'password'}
                    value={braveKey}
                    onChange={(e) => setBraveKey(e.target.value)}
                    placeholder="BSA..."
                    className="flex-1 font-mono text-xs"
                  />
                  <Button variant="ghost" size="icon" onClick={() => setShowKeys((v) => !v)}>
                    {showKeys ? <EyeOff size={14} /> : <Eye size={14} />}
                  </Button>
                </div>
                <KeyGuide
                  name="Brave"
                  siteUrl="https://brave.com/search/api/"
                  steps={[
                    '注册 / 登录（免费开始）',
                    '控制台点 Create 生成订阅 key',
                    '复制 BSA 开头的密钥，粘贴到上方输入框',
                  ]}
                />
              </div>
            )}
          </div>
        </details>
      </section>

      {/* ---- Web Fetch ---- */}
      <section className="flex flex-col gap-3 pt-4 border-t border-[var(--border-subtle)]">
        <h3 className="text-subheading text-[var(--text)]">Web Fetch</h3>
        <div className="flex gap-2">
          <ModeBtn value="builtin" current={fetchProvider} set={setFetchProvider} label="内置" />
          <ModeBtn value="ollama" current={fetchProvider} set={setFetchProvider} label="Ollama" />
          <ModeBtn value="hybrid" current={fetchProvider} set={setFetchProvider} label="Hybrid" />
        </div>
        {(fetchProvider === 'ollama' || fetchProvider === 'hybrid') && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-size-sm font-medium text-[var(--text-muted)]">
                Ollama web_fetch Base URL
              </label>
              <Input
                value={fetchOllamaBase}
                onChange={(e) => setFetchOllamaBase(e.target.value)}
                placeholder="https://ollama.com"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-size-sm font-medium text-[var(--text-muted)]">
                Ollama web_fetch API Key
              </label>
              <Input
                type={showKeys ? 'text' : 'password'}
                value={fetchOllamaKey}
                onChange={(e) => setFetchOllamaKey(e.target.value)}
                placeholder="ollama-key..."
                className="font-mono text-xs"
              />
            </div>
          </div>
        )}
      </section>

      {/* ---- Papers ---- */}
      <section className="flex flex-col gap-3 pt-4 border-t border-[var(--border-subtle)]">
        <h3 className="text-subheading text-[var(--text)]">论文研究工具</h3>
        <div className="flex gap-2">
          <ModeBtn
            value="hybrid"
            current={papersProvider}
            set={setPapersProvider}
            label="Hybrid（推荐）"
          />
          <ModeBtn
            value="semantic_scholar"
            current={papersProvider}
            set={setPapersProvider}
            label="S2"
          />
          <ModeBtn value="arxiv" current={papersProvider} set={setPapersProvider} label="arXiv" />
        </div>
        {(papersProvider === 'hybrid' || papersProvider === 'semantic_scholar') && (
          <div className="flex flex-col gap-1.5">
            <label className="text-size-sm font-medium text-[var(--text-muted)]">
              Semantic Scholar API Key（可选）
            </label>
            <Input
              type={showKeys ? 'text' : 'password'}
              value={s2ApiKey}
              onChange={(e) => setS2ApiKey(e.target.value)}
              placeholder="可选，填写后减少限流"
              className="font-mono text-xs"
            />
          </div>
        )}
      </section>

      <Button onClick={handleSave} disabled={saving} className="self-start">
        {saved ? <Check size={14} /> : <Save size={14} />}
        {saved ? '已保存' : '保存所有 Web 设置'}
      </Button>

      {saveError && (
        <div className="rounded-lg px-3 py-2 bg-[var(--accent-soft)] text-xs text-[var(--danger)]">
          {saveError}
        </div>
      )}
    </div>
  );
}

function isLightColor(color: string): boolean {
  const trimmed = color.trim();
  if (trimmed.startsWith('#')) {
    const hex = trimmed.slice(1);
    let r = 255;
    let g = 255;
    let b = 255;
    if (hex.length === 3) {
      r = parseInt(hex[0] + hex[0], 16);
      g = parseInt(hex[1] + hex[1], 16);
      b = parseInt(hex[2] + hex[2], 16);
    } else if (hex.length === 6) {
      r = parseInt(hex.slice(0, 2), 16);
      g = parseInt(hex.slice(2, 4), 16);
      b = parseInt(hex.slice(4, 6), 16);
    }
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.62;
  }
  const rgb = trimmed.match(/\d+(?:\.\d+)?/g)?.map(Number);
  if (rgb && rgb.length >= 3) {
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255 > 0.62;
  }
  return false;
}

function ColorField({
  label,
  value,
  presets,
  onChange,
}: {
  label: string;
  value: string;
  presets: string[];
  onChange: (color: string) => void;
}) {
  const current = value || presets[0];
  const isSelected = (color: string) => value !== '' && value.toLowerCase() === color.toLowerCase();
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-size-sm font-medium text-[var(--text)]">{label}</label>
        <div className="flex items-center gap-2">
          {/* 当前色值胶囊(参考图式) */}
          <span className="flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface-muted)]/60 px-2 py-0.5">
            <span className="h-3 w-3 rounded-full" style={{ background: current }} />
            <code className="text-size-xs text-[var(--text-muted)]">
              {(current || '').toUpperCase()}
            </code>
          </span>
          <button
            onClick={() => onChange('')}
            disabled={value === ''}
            className="flex shrink-0 items-center gap-1 rounded-md border border-[var(--border)] px-1.5 py-0.5 text-size-xs text-[var(--text-muted)] transition-colors hover:border-[var(--text-faint)] hover:text-[var(--text)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-[var(--border)] disabled:hover:text-[var(--text-muted)]"
            title="恢复默认"
          >
            <RotateCcw size={11} />
            恢复默认
          </button>
        </div>
      </div>
      <div className="grid grid-cols-8 gap-1.5">
        {presets.map((color) => (
          <button
            key={color}
            onClick={() => onChange(color)}
            className="h-6 w-full rounded-md transition-transform hover:scale-110"
            style={{
              background: color,
              outline: isSelected(color) ? '2px solid var(--text)' : 'none',
              outlineOffset: 1,
            }}
            aria-label={`${label} ${color}`}
            aria-pressed={isSelected(color)}
          />
        ))}
        <label
          className="relative h-6 w-full cursor-pointer overflow-hidden rounded-md border border-[var(--border)]"
          title={`自定义${label}`}
        >
          <input
            type="color"
            value={current}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            aria-label={`自定义${label}`}
          />
          <span
            className="absolute inset-0 flex items-center justify-center"
            style={{ background: current }}
          >
            <Palette
              size={12}
              className={`drop-shadow ${isLightColor(current) ? 'text-black/50' : 'text-white/90'}`}
            />
          </span>
        </label>
      </div>
    </div>
  );
}

// ---- Appearance Tab ----
function AppearanceTab() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    try {
      return (localStorage.getItem('miqi-theme') as ThemeMode) ?? 'system';
    } catch {
      return 'system';
    }
  });
  const [fontScale, setFontScale] = useState<FontScale>(() => {
    try {
      return (localStorage.getItem('miqi-font-scale') as FontScale) ?? 'md';
    } catch {
      return 'md';
    }
  });
  const [fontFamily, setFontFamily] = useState<FontFamilyOption>(() => {
    try {
      return (localStorage.getItem('miqi-font-family') as FontFamilyOption) ?? 'system';
    } catch {
      return 'system';
    }
  });
  const [emojiMode, setEmojiMode] = useState<EmojiMode>(() => {
    try {
      return (localStorage.getItem(EMOJI_MODE_KEY) as EmojiMode) ?? 'color';
    } catch {
      return 'color';
    }
  });
  const [pointerCursor, setPointerCursor] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(POINTER_CURSOR_KEY);
      return raw === null ? true : raw === 'true';
    } catch {
      return true;
    }
  });
  const [reduceMotion, setReduceMotion] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(REDUCE_MOTION_KEY);
      return raw === null ? false : raw === 'true';
    } catch {
      return false;
    }
  });
  const [sidebarGlass, setSidebarGlass] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(SIDEBAR_GLASS_KEY);
      return raw === null ? true : raw === 'true';
    } catch {
      return true;
    }
  });
  const [uiFontSize, setUiFontSize] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(UI_FONT_SIZE_KEY);
      return raw === null ? 14 : Number(raw) || 14;
    } catch {
      return 14;
    }
  });
  const [codeFontSize, setCodeFontSize] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(CODE_FONT_SIZE_KEY);
      return raw === null ? 14 : Number(raw) || 14;
    } catch {
      return 14;
    }
  });
  const [contrast, setContrast] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(CONTRAST_KEY);
      if (raw !== null) return Number(raw) || 45;
      const storedTheme = (localStorage.getItem('miqi-theme') ?? 'system') as ThemeMode;
      return getContrastDefault(storedTheme);
    } catch {
      return 45;
    }
  });
  const [accentColor, setAccentColor] = useState<string>(() => {
    try {
      return localStorage.getItem(ACCENT_COLOR_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [bgColor, setBgColor] = useState<string>(() => {
    try {
      return localStorage.getItem(BACKGROUND_COLOR_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [fgColor, setFgColor] = useState<string>(() => {
    try {
      return localStorage.getItem(FOREGROUND_COLOR_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [surfaceColor, setSurfaceColor] = useState<string>(() => {
    try {
      return localStorage.getItem(SURFACE_COLOR_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const [sidebarColor, setSidebarColor] = useState<string>(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLOR_KEY) ?? '';
    } catch {
      return '';
    }
  });
  const initializing = useRef(true);

  function storeSetting(key: string, value: string) {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage unavailable — the preference still applies for this session */
    }
  }

  useEffect(() => {
    if (initializing.current) return;
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (initializing.current) return;
    applyFontPreferences(fontScale, fontFamily);
  }, [fontScale, fontFamily]);

  useEffect(() => {
    if (initializing.current) return;
    applyEmojiPreference(emojiMode);
  }, [emojiMode]);

  useEffect(() => {
    if (initializing.current) return;
    applyUIPreferences({
      pointerCursor,
      reduceMotion,
      uiFontSize,
      codeFontSize,
      contrast,
      sidebarGlass,
    });
  }, [
    theme,
    pointerCursor,
    reduceMotion,
    uiFontSize,
    codeFontSize,
    contrast,
    sidebarGlass,
    accentColor,
    bgColor,
    fgColor,
    surfaceColor,
    sidebarColor,
  ]);

  // Runs after applyUIPreferences (which owns the derived text colors) so a
  // custom foreground re-applies its muted/faint shades on top. theme is a
  // dependency so switching themes re-derives them against the new background.
  useEffect(() => {
    if (initializing.current) return;
    applyColorPreferences({
      accent: accentColor,
      background: bgColor,
      foreground: fgColor,
      surface: surfaceColor,
      sidebar: sidebarColor,
    });
  }, [accentColor, bgColor, fgColor, surfaceColor, sidebarColor, theme]);

  useEffect(() => {
    initializing.current = false;
  }, []);

  const modes: Array<{
    value: ThemeMode;
    label: string;
    icon: ReactNode;
    preview: { side: string; main: string; bars: string };
  }> = [
    {
      value: 'light',
      label: '浅色',
      icon: <Sun size={16} />,
      preview: { side: '#f7f8f9', main: '#ffffff', bars: '#e4e5e8' },
    },
    {
      value: 'light-soft',
      label: '浅色·柔和',
      icon: <CloudSun size={16} />,
      preview: { side: '#ececef', main: '#f0f0f2', bars: '#d7d8db' },
    },
    {
      value: 'light-ice',
      label: '浅色·冰蓝',
      icon: <Snowflake size={16} />,
      preview: { side: '#e8edf8', main: '#f0f3fc', bars: '#c9d2e4' },
    },
    {
      value: 'dark',
      label: '深色',
      icon: <Moon size={16} />,
      preview: { side: '#16181a', main: '#0f1011', bars: '#2a2c30' },
    },
    {
      value: 'system',
      label: '跟随系统',
      icon: <Monitor size={16} />,
      preview: { side: '#f7f8f9', main: '#ffffff', bars: '#e4e5e8' },
    },
  ];

  const fontOptions: Array<{ value: FontFamilyOption; label: string }> = [
    { value: 'system', label: '系统默认' },
    { value: 'yahei', label: '微软雅黑' },
    { value: 'dengxian', label: '等线' },
    { value: 'simhei', label: '黑体' },
    { value: 'kaiti', label: '楷体' },
    { value: 'simsun', label: '宋体' },
    { value: 'fangsong', label: '仿宋' },
    { value: 'youyuan', label: '幼圆' },
    { value: 'source', label: '思源黑体' },
    { value: 'pingfang', label: '苹方（苹果系统）' },
    { value: 'inter', label: 'Inter（仅西文）' },
  ];

  const emojiModes: Array<{ value: EmojiMode; label: string; icon: ReactNode }> = [
    { value: 'color', label: '彩色', icon: <Palette size={16} /> },
    { value: 'mono', label: '黑白', icon: <Contrast size={16} /> },
  ];

  return (
    <div className="p-6 max-w-lg flex flex-col gap-4">
      <h3 className="text-subheading text-[var(--text)]">外观</h3>
      <div className="flex flex-col gap-1.5">
        <label className="text-size-sm font-medium text-[var(--text-muted)]">主题</label>
        <div className="grid grid-cols-5 gap-1 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)]/50 p-1">
          {modes.map(({ value, label, icon, preview }) => (
            <button
              key={value}
              onClick={() => {
                setTheme(value);
                storeSetting('miqi-theme', value);
                applyTheme(value);
                if (localStorage.getItem(CONTRAST_KEY) === null) {
                  setContrast(getContrastDefault(value));
                }
              }}
              aria-pressed={theme === value}
              title={label}
              className={cn(
                'flex flex-col items-center gap-1 rounded-lg px-1 py-2 transition duration-200',
                theme === value
                  ? 'bg-[var(--accent-soft)] ring-1 ring-[var(--accent)]/50'
                  : 'hover:bg-[var(--surface)]/60 hover:ring-1 hover:ring-[var(--border-subtle)]'
              )}
            >
              {/* 线框图预览卡片:侧栏条+主区+占位条(参考图式) */}
              <span
                className="relative w-full h-10 rounded-lg overflow-hidden ring-1 ring-[var(--border-subtle)] shrink-0"
                aria-hidden="true"
              >
                {/* 侧栏条 */}
                <span
                  className="absolute inset-y-0 left-0 w-[30%]"
                  style={{ background: preview.side }}
                />
                {/* 主区 */}
                <span
                  className="absolute inset-y-0 left-[30%] right-0"
                  style={{ background: preview.main }}
                />
                {/* 主区占位条(模拟内容) */}
                <span
                  className="absolute left-[38%] top-2 h-[3px] rounded-full w-[44%]"
                  style={{ background: preview.bars }}
                />
                <span
                  className="absolute left-[38%] top-4 h-[3px] rounded-full w-[56%]"
                  style={{ background: preview.bars }}
                />
                <span
                  className="absolute left-[38%] top-6 h-[3px] rounded-full w-[38%]"
                  style={{ background: preview.bars }}
                />
                {/* 跟随系统:右半覆盖深色 */}
                {value === 'system' && (
                  <>
                    <span
                      className="absolute inset-y-0 left-1/2 w-1/2"
                      style={{ background: '#0f1011' }}
                    />
                    <span
                      className="absolute left-[54%] top-2 h-[3px] rounded-full w-[38%]"
                      style={{ background: '#2a2c30' }}
                    />
                    <span
                      className="absolute left-[54%] top-4 h-[3px] rounded-full w-[46%]"
                      style={{ background: '#2a2c30' }}
                    />
                  </>
                )}
                {theme === value && (
                  <span className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-[var(--accent)] flex items-center justify-center">
                    <Check size={9} strokeWidth={3.5} className="text-white" />
                  </span>
                )}
              </span>
              <span className="flex items-center gap-1 text-size-xs font-medium text-[var(--text-muted)]">
                {icon}
                <span className="hidden sm:inline">{label}</span>
              </span>
            </button>
          ))}
        </div>
      </div>

      <ColorField
        label="强调色"
        value={accentColor}
        presets={[
          '#EA653D', // 品牌橙(MiQroForge 品牌默认)
          '#F97316',
          '#FF9800',
          '#FFC107',
          '#F59E0B',
          '#E91E63',
          '#E15B8C',
          '#9C27B0',
          '#673AB7',
          '#3F51B5',
          '#339CFF',
          '#2196F3',
          '#00BCD4',
          '#0B7F91', // 辅助品牌色
          '#009688',
          '#4CAF50',
        ]}
        onChange={(color) => {
          setAccentColor(color);
          storeSetting(ACCENT_COLOR_KEY, color);
        }}
      />
      <ColorField
        label="背景色"
        value={bgColor}
        presets={[
          '#F7F8F9',
          '#FAFAFB',
          '#FFFFFF',
          '#F0F1F4',
          '#EDEEF1',
          '#E9EAED',
          '#F5F5F0',
          '#E8EDF2',
          '#D3DFEE',
          '#FFFFFF',
          '#1A1A1A',
          '#17171A',
          '#181818',
          '#2A2A2A',
          '#282C32',
          '#3B3F43',
        ]}
        onChange={(color) => {
          setBgColor(color);
          storeSetting(BACKGROUND_COLOR_KEY, color);
        }}
      />
      <ColorField
        label="面板色"
        value={surfaceColor}
        presets={[
          '#FFFFFF',
          '#F2F2F0',
          '#F7F8FA',
          '#EEF3FA',
          '#FDF1E3',
          '#F6F5F1',
          '#E8E8E4',
          '#D9E2EC',
          '#242424',
          '#222226',
          '#202020',
          '#2E2E2E',
          '#33373C',
          '#383D43',
          '#4B5158',
        ]}
        onChange={(color) => {
          setSurfaceColor(color);
          storeSetting(SURFACE_COLOR_KEY, color);
        }}
      />
      <ColorField
        label="侧边栏色"
        value={sidebarColor}
        presets={[
          '#FAFAF9',
          '#FFFFFF',
          '#F5F6F8',
          '#E8EEF7',
          '#D3DFEE',
          '#FDF3E8',
          '#F2F1EC',
          '#E9E9E4',
          '#242424',
          '#26282D',
          '#2A2A48',
          '#383D43',
          '#43484E',
          '#17171A',
          '#4B5158',
        ]}
        onChange={(color) => {
          setSidebarColor(color);
          storeSetting(SIDEBAR_COLOR_KEY, color);
        }}
      />
      <ColorField
        label="前景色"
        value={fgColor}
        presets={[
          '#121212',
          '#17181C',
          '#1A1C1F',
          '#282C32',
          '#31363E',
          '#3B3F46',
          '#4A4F57',
          '#555555',
          '#6B7280',
          '#888888',
          '#A0A0A0',
          '#C0C4CC',
          '#E4E4E0',
          '#F5F5F5',
          '#FFFFFF',
        ]}
        onChange={(color) => {
          setFgColor(color);
          storeSetting(FOREGROUND_COLOR_KEY, color);
        }}
      />

      <div className="flex flex-col gap-1.5">
        <label className="text-size-sm font-medium text-[var(--text-muted)]">表情样式</label>
        <div className="flex items-stretch gap-0.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-muted)]/50 p-1">
          {emojiModes.map(({ value, label, icon }) => (
            <button
              key={value}
              onClick={() => {
                setEmojiMode(value);
                storeSetting(EMOJI_MODE_KEY, value);
                applyEmojiPreference(value);
              }}
              aria-pressed={emojiMode === value}
              className={cn(
                'flex-1 flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-body-sm font-medium transition duration-200',
                emojiMode === value
                  ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--surface)]/50'
              )}
            >
              {icon}
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="flex items-center justify-between text-size-sm font-medium text-[var(--text-muted)]">
          <span>UI 字号</span>
          <span className="text-size-2xs text-[var(--text-faint)]">{uiFontSize}px</span>
        </label>
        <input
          type="range"
          min={12}
          max={20}
          step={1}
          value={uiFontSize}
          style={{ ['--range-fill' as string]: `${((uiFontSize - 12) / 8) * 100}%` }}
          onChange={(e) => {
            const next = Number(e.target.value);
            setUiFontSize(next);
            storeSetting(UI_FONT_SIZE_KEY, String(next));
          }}
          className="range w-full"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="flex items-center justify-between text-size-sm font-medium text-[var(--text-muted)]">
          <span>代码字体大小</span>
          <span className="text-size-2xs text-[var(--text-faint)]">{codeFontSize}px</span>
        </label>
        <input
          type="range"
          min={12}
          max={18}
          step={1}
          value={codeFontSize}
          style={{ ['--range-fill' as string]: `${((codeFontSize - 12) / 6) * 100}%` }}
          onChange={(e) => {
            const next = Number(e.target.value);
            setCodeFontSize(next);
            storeSetting(CODE_FONT_SIZE_KEY, String(next));
          }}
          className="range w-full"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="flex items-center justify-between text-size-sm font-medium text-[var(--text-muted)]">
          <span>对比度</span>
          <span className="text-size-2xs text-[var(--text-faint)]">{contrast}%</span>
        </label>
        <input
          type="range"
          min={25}
          max={100}
          step={1}
          value={contrast}
          style={{ ['--range-fill' as string]: `${((contrast - 25) / 75) * 100}%` }}
          onChange={(e) => {
            const next = Number(e.target.value);
            setContrast(next);
            storeSetting(CONTRAST_KEY, String(next));
          }}
          className="range w-full"
        />
      </div>

      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-size-sm font-medium text-[var(--text-muted)]">使用指针光标</div>
          <div className="text-size-2xs text-[var(--text-faint)]">悬停交互元素时切换为指针光标</div>
        </div>
        <button
          role="switch"
          aria-checked={pointerCursor}
          onClick={() => {
            const next = !pointerCursor;
            setPointerCursor(next);
            storeSetting(POINTER_CURSOR_KEY, String(next));
          }}
          className={cn(
            'relative h-5 w-9 shrink-0 rounded-full transition-colors',
            pointerCursor ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
          )}
        >
          <span
            className={cn(
              'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform',
              pointerCursor && 'translate-x-4'
            )}
          />
        </button>
      </div>

      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-size-sm font-medium text-[var(--text-muted)]">减少动态效果</div>
          <div className="text-size-2xs text-[var(--text-faint)]">减少动画效果或匹配系统设置</div>
        </div>
        <button
          role="switch"
          aria-checked={reduceMotion}
          onClick={() => {
            const next = !reduceMotion;
            setReduceMotion(next);
            storeSetting(REDUCE_MOTION_KEY, String(next));
          }}
          className={cn(
            'relative h-5 w-9 shrink-0 rounded-full transition-colors',
            reduceMotion ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
          )}
        >
          <span
            className={cn(
              'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform',
              reduceMotion && 'translate-x-4'
            )}
          />
        </button>
      </div>

      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-size-sm font-medium text-[var(--text-muted)]">半透明侧边栏</div>
          <div className="text-size-2xs text-[var(--text-faint)]">侧边栏使用半透明毛玻璃效果</div>
        </div>
        <button
          role="switch"
          aria-checked={sidebarGlass}
          onClick={() => {
            const next = !sidebarGlass;
            setSidebarGlass(next);
            storeSetting(SIDEBAR_GLASS_KEY, String(next));
          }}
          className={cn(
            'relative h-5 w-9 shrink-0 rounded-full transition-colors',
            sidebarGlass ? 'bg-[var(--accent)]' : 'bg-[var(--border)]'
          )}
        >
          <span
            className={cn(
              'absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform',
              sidebarGlass && 'translate-x-4'
            )}
          />
        </button>
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-size-sm font-medium text-[var(--text-muted)]">字体</label>
        <div className="relative">
          <select
            value={fontFamily}
            onChange={(e) => {
              const next = e.target.value as FontFamilyOption;
              setFontFamily(next);
              storeSetting('miqi-font-family', next);
              applyFontPreferences(fontScale, next);
            }}
            className="w-full appearance-none rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 pr-9 text-body-sm text-[var(--text)] shadow-sm transition-colors hover:border-[var(--text-faint)] focus:border-[var(--border-strong)] focus:outline-none"
            aria-label="字体"
          >
            {fontOptions.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <ChevronDown
            size={14}
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-faint)]"
          />
        </div>
      </div>
    </div>
  );
}

// ---- Logs Tab (virtualized for scroll performance) ----
const LOG_ROW_ESTIMATE = 28; // estimated height for a single-line row (py-1.5 + font-mono text-xs)

function LogsTab() {
  const { entries, refreshLogs } = useRuntime();
  const hasInitialScroll = useRef(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [copiedLogs, setCopiedLogs] = useState(false);
  const [logTab, setLogTab] = useState<'all' | 'frontend' | 'backend'>('all');
  const [level, setLevel] = useState<'all' | 'INFO' | 'WARN' | 'ERROR'>('all');
  const [source, setSource] = useState<'all' | 'bridge' | 'renderer' | 'sandbox' | 'main' | 'tool'>(
    'all'
  );
  const [sessionKey, setSessionKey] = useState('');
  const [keyword, setKeyword] = useState('');
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set());
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // Reset expanded rows whenever filters change
  useEffect(() => {
    setExpandedRows(new Set());
  }, [logTab, level, source, sessionKey, keyword]);

  // Auto-refresh: periodically poll for new log entries (tail -f effect)
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      refreshLogs();
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, refreshLogs]);

  // Memoize filtered + sorted list so it only recalc's when inputs change
  const filtered = useMemo(() => {
    return entries
      .filter((entry) => {
        if (logTab === 'frontend' && !(entry.source === 'renderer' || entry.source === 'main'))
          return false;
        if (
          logTab === 'backend' &&
          !(entry.source === 'bridge' || entry.source === 'sandbox' || entry.source === 'tool')
        )
          return false;
        if (level !== 'all' && entry.level !== level) return false;
        if (source !== 'all' && entry.source !== source) return false;
        if (sessionKey && !(entry.sessionKey ?? '').includes(sessionKey)) return false;
        if (keyword && !entry.message.toLowerCase().includes(keyword.toLowerCase())) return false;
        return true;
      })
      .sort((a, b) => {
        const aTime = Date.parse(a.timestamp);
        const bTime = Date.parse(b.timestamp);
        if (Number.isNaN(aTime)) return Number.isNaN(bTime) ? 0 : 1;
        if (Number.isNaN(bTime)) return -1;
        return bTime - aTime;
      });
  }, [entries, logTab, level, source, sessionKey, keyword]);

  // Virtualizer — only render rows visible in the viewport.
  // No measureElement: log rows are all single-line (py-1.5 + text-xs),
  // so the static estimate is accurate. A ResizeObserver-based measurer
  // would fire during render and trigger internal flushSync calls.
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => LOG_ROW_ESTIMATE,
    overscan: 15,
  });

  // Keep latest virtualizer in a ref so effects can use it without re-running
  // when the virtualizer instance changes (avoiding flushSync-in-render errors).
  const virtualizerRef = useRef(virtualizer);
  virtualizerRef.current = virtualizer;

  // On first mount, scroll to top to show the latest logs.
  useEffect(() => {
    if (!hasInitialScroll.current && filtered.length > 0) {
      hasInitialScroll.current = true;
      queueMicrotask(() => {
        virtualizerRef.current.scrollToIndex(0, { align: 'start' });
      });
    }
  }, [filtered.length]);

  const toggleRow = useCallback((id: number) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const formatTime = useCallback((iso: string) => {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const date = d.toLocaleDateString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
    });
    const time = d.toLocaleTimeString('zh-CN', { hour12: false });
    return `${date} ${time}`;
  }, []);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(
      filtered
        .map((entry) => `[${entry.timestamp}] [${entry.level}] [${entry.source}] ${entry.message}`)
        .join('\n')
    );
    setCopiedLogs(true);
    setTimeout(() => setCopiedLogs(false), 1500);
  };

  const handleExportTxt = () => {
    const text = filtered
      .map((entry) => `[${entry.timestamp}] [${entry.level}] [${entry.source}] ${entry.message}`)
      .join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `miqi-logs-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportJson = () => {
    const json = JSON.stringify(filtered, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `miqi-logs-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportLog = () => {
    const text = filtered
      .map((entry) => `[${entry.timestamp}] [${entry.level}] [${entry.source}] ${entry.message}`)
      .join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `miqi-logs-${new Date().toISOString().slice(0, 10)}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const levelBadge = useCallback((lvl: string) => {
    const colors: Record<string, string> = {
      INFO: 'bg-emerald-500',
      WARN: 'bg-amber-500',
      ERROR: 'bg-red-500',
    };
    return (
      <span className="inline-flex items-center gap-1">
        <span className={cn('w-1.5 h-1.5 rounded-full', colors[lvl] || 'bg-[var(--text-faint)]')} />
        {lvl}
      </span>
    );
  }, []);

  return (
    <div className="flex flex-col h-full">
      {/* Sub-tab bar */}
      <div className="flex items-center gap-2 px-6 py-2 border-b border-[var(--border-subtle)]">
        <span className="text-xs text-[var(--text-muted)] mr-1">视图：</span>
        {[
          { value: 'all' as const, label: '全部' },
          { value: 'frontend' as const, label: '前端日志' },
          { value: 'backend' as const, label: '后端日志' },
        ].map((tab) => (
          <button
            key={tab.value}
            onClick={() => setLogTab(tab.value)}
            className={cn(
              'settings-hover-tab px-3 py-1 rounded-lg text-body-sm border',
              logTab === tab.value
                ? 'bg-[var(--accent-soft)] text-[var(--accent)] border-[var(--accent)]'
                : 'bg-[var(--surface)] text-[var(--text-muted)] border-[var(--border)] hover:border-[var(--accent)]'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-3 border-b border-[var(--border-subtle)]">
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="rounded"
            />
            自动刷新
          </label>
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value as 'all' | 'INFO' | 'WARN' | 'ERROR')}
            className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
          >
            <option value="all">全部级别</option>
            <option value="INFO">信息</option>
            <option value="WARN">警告</option>
            <option value="ERROR">错误</option>
          </select>
          <select
            value={source}
            onChange={(e) =>
              setSource(
                e.target.value as 'all' | 'bridge' | 'renderer' | 'sandbox' | 'main' | 'tool'
              )
            }
            className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
          >
            <option value="all">全部来源</option>
            <option value="bridge">桥接</option>
            <option value="renderer">渲染器</option>
            <option value="main">主进程</option>
            <option value="sandbox">沙盒</option>
            <option value="tool">工具</option>
          </select>
          <input
            value={sessionKey}
            onChange={(e) => setSessionKey(e.target.value)}
            placeholder="session"
            className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
          />
          <input
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="关键字"
            className="rounded border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs"
          />
          <Button
            variant="ghost"
            size="icon"
            onClick={() => refreshLogs()}
            data-testid="refresh-logs"
          >
            <RefreshCw size={14} />
          </Button>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleCopy}>
            {copiedLogs ? <Check size={14} /> : <Copy size={14} />}
            {copiedLogs ? '已复制' : '复制日志'}
          </Button>
          <Button variant="outline" size="sm" onClick={handleExportTxt}>
            <Download size={14} /> 导出 TXT
          </Button>
          <Button variant="outline" size="sm" onClick={handleExportLog}>
            <Download size={14} /> 导出 LOG
          </Button>
          <Button variant="outline" size="sm" onClick={handleExportJson}>
            <Download size={14} /> 导出 JSON
          </Button>
        </div>
      </div>

      {/* Virtualized table view */}
      <div className="flex-1 min-h-0 flex flex-col">
        {/* Sticky header outside the scroll container so it stays fixed */}
        {filtered.length > 0 && (
          <table className="w-full text-xs font-mono table-fixed">
            <thead className="bg-[var(--surface)] border-b border-[var(--border-subtle)]">
              <tr className="text-[var(--text-muted)]">
                <th className="text-left px-4 py-2 font-medium w-[100px]">时间</th>
                <th className="text-left px-2 py-2 font-medium w-[70px]">级别</th>
                <th className="text-left px-2 py-2 font-medium w-[85px]">来源</th>
                <th className="text-left px-4 py-2 font-medium">消息</th>
              </tr>
            </thead>
          </table>
        )}

        {/* Scrollable body with virtualizer */}
        <div
          ref={scrollContainerRef}
          className="flex-1 overflow-auto [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[var(--border)] hover:[&::-webkit-scrollbar-thumb]:bg-[var(--text-faint)]"
        >
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center text-[var(--text-muted)] py-16 text-xs">
              暂无匹配日志。请调整过滤条件或先启动运行时。
            </div>
          ) : (
            <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
              <table className="w-full text-xs font-mono table-fixed">
                <tbody>
                  {virtualizer.getVirtualItems().map((virtualRow) => {
                    const entry = filtered[virtualRow.index];
                    const isExpanded = expandedRows.has(entry.id);
                    const rowBg =
                      entry.level === 'ERROR'
                        ? 'bg-red-500/5 hover:bg-red-500/10'
                        : entry.level === 'WARN'
                          ? 'bg-amber-500/5 hover:bg-amber-500/10'
                          : 'hover:bg-[var(--surface-muted)]';
                    return (
                      <tr
                        key={entry.id}
                        data-index={virtualRow.index}
                        onClick={() => toggleRow(entry.id)}
                        className={cn(
                          'border-b border-[var(--border-subtle)] cursor-pointer transition-colors',
                          rowBg
                        )}
                        style={{
                          position: 'absolute',
                          top: 0,
                          left: 0,
                          width: '100%',
                          transform: `translateY(${virtualRow.start}px)`,
                        }}
                      >
                        <td
                          className="px-4 py-1.5 text-[var(--text-faint)] whitespace-nowrap w-[100px]"
                          title={entry.timestamp}
                        >
                          {formatTime(entry.timestamp)}
                        </td>
                        <td className="px-2 py-1.5 whitespace-nowrap w-[70px]">
                          {levelBadge(entry.level)}
                        </td>
                        <td
                          className={cn(
                            'px-2 py-1.5 whitespace-nowrap w-[85px]',
                            entry.level === 'ERROR'
                              ? 'text-[var(--danger)]'
                              : entry.level === 'WARN'
                                ? 'text-[var(--warning)]'
                                : 'text-[var(--text-muted)]'
                          )}
                        >
                          {entry.source}
                        </td>
                        <td className="px-4 py-1.5 text-[var(--text)]">
                          <span className={isExpanded ? '' : 'line-clamp-1 break-all'}>
                            {entry.message}
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- Archived Sessions Tab ----
function ArchivedTab({ onRestore }: { onRestore?: (key: string) => void }) {
  const [sessions, setSessions] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await window.miqi.sessions.listArchived();
      setSessions(r?.sessions ?? []);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleRestore = async (key: string, title: string) => {
    try {
      await window.miqi.sessions.unarchive(key);
      await load();
      onRestore?.(key);
    } catch (e: any) {
      alert(`恢复失败: ${e?.message || e}`);
    }
  };

  const handleDelete = async (key: string, title: string) => {
    if (!window.confirm(`永久删除对话「${title}」？此操作不可撤销。`)) return;
    try {
      await window.miqi.sessions.delete(key);
      await load();
    } catch (e: any) {
      alert(`删除失败: ${e?.message || e}`);
    }
  };

  function formatTime(iso?: string): string {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  return (
    <div className="p-4 max-w-2xl flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h3 className="text-subheading text-[var(--text)] flex items-center gap-2">
          <Archive size={16} />
          已归档对话
        </h3>
        <button
          onClick={load}
          disabled={loading}
          className="p-1.5 rounded-md hover:bg-[var(--surface-muted)] transition-colors text-text-faint"
          title="刷新"
        >
          <RefreshCw size={14} className={cn(loading && 'animate-spin')} />
        </button>
      </div>

      {sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 px-4 rounded-xl border border-dashed border-[var(--border-subtle)] bg-[var(--surface-muted)]/30">
          <div className="w-10 h-10 rounded-full bg-[var(--surface-muted)] flex items-center justify-center mb-3 text-text-faint">
            <Archive size={18} />
          </div>
          <p className="text-size-sm font-medium text-[var(--text-muted)] mb-1">暂无已归档的对话</p>
          <p className="text-size-2xs text-[var(--text-faint)]">
            在侧边栏右键对话选择"归档"即可移至此
          </p>
        </div>
      ) : (
        <div className="flex flex-col rounded-xl border border-[var(--border-subtle)] overflow-hidden">
          {sessions.map((s) => (
            <div
              key={s.key}
              className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[var(--surface-muted)]/50"
              style={{ borderBottom: '1px solid var(--border-subtle)' }}
            >
              <div className="flex-1 min-w-0">
                <p className="text-size-sm truncate font-medium text-text">{s.title || s.key}</p>
                <p className="text-size-2xs text-text-faint">{formatTime(s.updated_at)}</p>
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => handleRestore(s.key, s.title || s.key)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-size-2xs font-medium transition duration-150 hover:bg-[var(--surface)] hover:shadow-sm text-text-muted"
                  title="恢复对话"
                >
                  <Unarchive size={13} />
                  恢复
                </button>
                <button
                  onClick={() => handleDelete(s.key, s.title || s.key)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-size-2xs font-medium transition duration-150 hover:bg-[var(--danger-bg)] text-text-faint"
                  title="永久删除"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Docs Tab ----
const DOCS_BASE = 'https://mygithub.sixiangjia.de/MiQi/';

interface DocLink {
  label: string;
  href: string;
  children?: DocLink[];
}

const DOCS_TREE: DocLink[] = [
  { label: '🚀 快速开始', href: 'getting-started/' },
  {
    label: '🏗️ 系统架构',
    href: 'architecture/',
    children: [
      { label: '整体架构', href: 'architecture/' },
      { label: '数据流', href: 'architecture/data-flow/' },
      { label: '项目结构', href: 'architecture/project-structure/' },
    ],
  },
  {
    label: '🐍 Python 后端',
    href: 'backend/agent/',
    children: [
      { label: 'Agent 引擎', href: 'backend/agent/' },
      { label: '工具系统', href: 'backend/tools/' },
      { label: 'Provider 系统', href: 'backend/providers/' },
      { label: '记忆系统', href: 'backend/memory/' },
      { label: '会话管理', href: 'backend/session/' },
      { label: '任务追踪', href: 'backend/trace/' },
      { label: 'Bridge 通信', href: 'backend/bridge/' },
    ],
  },
  {
    label: '💻 Electron 前端',
    href: 'frontend/overview/',
    children: [
      { label: '前端概览', href: 'frontend/overview/' },
      { label: 'IPC 通信', href: 'frontend/ipc/' },
      { label: '功能页面', href: 'frontend/features/' },
      { label: 'SkillHub', href: 'frontend/skillhub/' },
    ],
  },
  { label: '🔌 MCP 集成', href: 'mcp-integration/' },
  {
    label: '⚙️ 配置与部署',
    href: 'configuration/',
    children: [
      { label: '配置参考', href: 'configuration/' },
      { label: 'Docker 部署', href: 'deployment/docker/' },
      { label: '桌面打包', href: 'deployment/packaging/' },
    ],
  },
  {
    label: '🛠️ 开发指南',
    href: 'developer-guide/',
    children: [
      { label: '开发环境搭建', href: 'developer-guide/' },
      { label: '贡献指南', href: 'contributing/' },
    ],
  },
  { label: '📝 更新日志', href: 'changelog/' },
];

function DocsTab() {
  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <div className="px-6 pt-5 pb-3 shrink-0">
        <div className="flex items-center justify-between">
          <h3 className="text-subheading text-[var(--text)]">MiQroForge Desktop 文档</h3>
          <a
            href={DOCS_BASE}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
          >
            <ExternalLink size={12} />
            完整文档站点
          </a>
        </div>
        <p className="text-xs text-[var(--text-faint)] mt-1">点击章节在浏览器中打开对应文档页面</p>
      </div>

      <div className="px-6 pb-6 flex flex-col gap-3">
        {DOCS_TREE.map((section) => (
          <div
            key={section.href}
            className="settings-hover-card border border-[var(--border-subtle)] rounded-lg overflow-hidden"
          >
            <a
              href={DOCS_BASE + section.href}
              target="_blank"
              rel="noopener noreferrer"
              className="block px-4 py-2.5 text-xs font-semibold text-[var(--text)] bg-[var(--surface-muted)] hover:bg-[var(--accent)]/10 transition-colors"
            >
              {section.label}
            </a>
            {section.children && (
              <div className="flex flex-col">
                {section.children.map((child) => (
                  <a
                    key={child.href}
                    href={DOCS_BASE + child.href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block px-4 py-2 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--surface-muted)] transition-colors border-t border-[var(--border-subtle)]"
                  >
                    {child.label}
                  </a>
                ))}
              </div>
            )}
          </div>
        ))}

        <div className="mt-2 pt-4 border-t border-[var(--border-subtle)]">
          <a
            href="https://github.com/14790897/miqi"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 text-xs text-[var(--text-faint)] hover:text-[var(--accent)] transition-colors"
          >
            <ExternalLink size={12} />
            GitHub 仓库：14790897/miqi
          </a>
        </div>
      </div>
    </div>
  );
}

// ---- Main ----
export function SettingsPage({
  onReopenSetup,
  tab = 'general',
}: {
  onReopenSetup?: () => void;
  tab?: SettingsTab;
}) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(tab);
  const [searchQuery, setSearchQuery] = useState('');
  const [collapsedCategories, setCollapsedCategories] = useState<Set<string>>(new Set());
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setActiveTab(tab);
  }, [tab]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const normalizedQuery = searchQuery.trim().toLowerCase();
  const visibleCategories = useMemo(() => {
    if (!normalizedQuery) return SETTINGS_CATEGORIES;
    return SETTINGS_CATEGORIES.map((category) => ({
      ...category,
      items: category.items.filter((item) => {
        return (
          item.label.toLowerCase().includes(normalizedQuery) ||
          item.description.toLowerCase().includes(normalizedQuery) ||
          item.keywords.some((keyword) => keyword.toLowerCase().includes(normalizedQuery))
        );
      }),
    })).filter((category) => category.items.length > 0);
  }, [normalizedQuery]);

  const toggleCategory = (id: string) => {
    setCollapsedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const goToQraft = () => setActiveTab('qraft');

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-7 py-5 border-b border-[var(--border-subtle)] flex items-center gap-4">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold leading-[1.25] text-[var(--text)]">设置</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">配置 MiQroForge 智能体和外观</p>
        </div>
        <div className="relative ml-auto w-[320px] max-w-full shrink-0">
          <Search
            className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none"
            size={14}
            style={{ color: 'var(--text-faint)' }}
          />
          <Input
            ref={searchRef}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="搜索设置"
            aria-label="搜索设置"
            className="pl-9 pr-12"
          />
          <kbd className="absolute right-2.5 top-1/2 -translate-y-1/2 text-size-2xs text-[var(--text-faint)] border border-[var(--border-subtle)] rounded px-1.5 py-0.5">
            Ctrl K
          </kbd>
        </div>
      </div>

      <Tabs.Root
        value={activeTab}
        onValueChange={(v) => setActiveTab(v as SettingsTab)}
        className="flex flex-1 min-h-0"
      >
        <div className="w-[232px] shrink-0 overflow-y-auto border-r border-[var(--border-subtle)] bg-[var(--surface)] p-2 space-y-5">
          {visibleCategories.length === 0 ? (
            <div className="px-3 py-8 text-xs text-[var(--text-faint)] text-center">
              未找到匹配的设置
            </div>
          ) : (
            visibleCategories.map((category) => {
              const isCollapsed = !normalizedQuery && collapsedCategories.has(category.id);
              return (
                <div key={category.id} className="min-w-0">
                  <button
                    onClick={() => toggleCategory(category.id)}
                    className="flex w-full items-center gap-1.5 px-2 py-1.5 text-caption font-semibold text-[var(--text-faint)] hover:text-[var(--text-muted)] transition-colors"
                    aria-expanded={!isCollapsed}
                  >
                    <ChevronDown
                      size={12}
                      className={cn(
                        'transition-transform duration-150',
                        isCollapsed && '-rotate-90'
                      )}
                    />
                    {category.label}
                  </button>
                  {!isCollapsed && (
                    <Tabs.List className="mt-1 space-y-0.5">
                      {category.items.map((item) => (
                        <Tabs.Trigger
                          key={item.value}
                          value={item.value}
                          className={cn(
                            'group relative flex w-full items-center gap-2.5 rounded-[9px] px-2.5 py-1.5 text-left text-body-sm font-medium transition-colors duration-150',
                            'text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]',
                            'data-[state=active]:bg-[var(--accent-soft)] data-[state=active]:text-[var(--accent-strong)]'
                          )}
                        >
                          <item.icon
                            size={14}
                            className={cn(
                              'shrink-0 text-[var(--text-faint)]',
                              'group-data-[state=active]:text-[var(--accent-strong)]'
                            )}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate">{item.label}</span>
                            <span className="block truncate text-caption font-normal text-[var(--text-faint)]">
                              {item.description}
                            </span>
                          </span>
                        </Tabs.Trigger>
                      ))}
                    </Tabs.List>
                  )}
                </div>
              );
            })
          )}
        </div>

        <Tabs.Content value="general" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 通用设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <GeneralTab onReopenSetup={onReopenSetup} onGoToQraft={goToQraft} />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="providers" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 模型设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <ProvidersPage onGoToQraft={goToQraft} />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="channels" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 渠道设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <ChannelsPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="approvals" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 审批设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <ApprovalsPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="workspace" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 工作区设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <WorkspacePage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="agents" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 智能体设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <AgentPanel />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="skills" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 技能设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <SkillsPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="memory" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 记忆设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <MemoryPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="experience" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 经验设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <ExperiencePage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="permissions" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 权限设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <PermissionsPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="plugins" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 插件设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <PluginMarket />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="qraft" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ MiQroForge 设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <QraftPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="cron" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 定时任务设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <CronPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="wsl" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ WSL设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <WslStatusPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="webtools" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ Web工具设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <WebToolsTab />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="appearance" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 外观设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <AppearanceTab />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="logs" className="flex-1 min-h-0 flex flex-col">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 日志设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <LogsTab />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="archived" className="flex-1 overflow-y-auto">
          <ArchivedTab />
        </Tabs.Content>
        <Tabs.Content value="privacy" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 隐私协议加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <PrivacyPage />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="docs" className="flex-1 min-h-0 flex flex-col">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 文档设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <DocsTab />
          </ErrorBoundary>
        </Tabs.Content>
        <Tabs.Content value="feedback" className="flex-1 overflow-y-auto">
          <ErrorBoundary
            fallback={(error, reset) => (
              <div className="p-6 text-sm" style={{ color: 'var(--danger)' }}>
                ⚠️ 反馈设置加载失败: {error.message}
                <button
                  onClick={reset}
                  className="ml-2 underline"
                  style={{ color: 'var(--accent)' }}
                >
                  重试
                </button>
              </div>
            )}
          >
            <FeedbackPage />
          </ErrorBoundary>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
