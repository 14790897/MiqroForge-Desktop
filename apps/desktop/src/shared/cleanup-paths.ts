/**
 * 卸载/清理的路径规划（issue #1177）——单一事实来源，纯 TS，无 Electron 依赖。
 *
 * 主进程（扫描/执行）、渲染器（仅 `import type` 展示）、卸载器 NSIS 脚本
 * （构建时由 scripts/generate-uninstaller-nsh.mjs 读取同目录
 * cleanup-constants.json 生成）共用同一份常量与安全规则。
 *
 * 安全规则（#1103 教训）：删除目标只能是「已知候选」——默认名候选目录
 * （~/.forge | ~/.miqi | ~/.assistant）或注册表/环境变量显式记录的数据根。
 * 路径解析失败、指向家目录/盘根/系统目录时一律跳过并记录原因，
 * **绝不回退到扩大删除范围**（宁可漏删，不可错删）。
 */
import path from 'node:path';
import constants from './cleanup-constants.json';

export const WSL_SANDBOX_DISTRO = constants.wslSandboxDistro;
/** 卸载时探测/清理的默认数据根名候选（#1175 更名后新旧名共存，含 legacy）。 */
export const DATA_ROOT_CANDIDATE_NAMES: string[] = constants.dataRootCandidateNames;
/**
 * 当前 Python 侧的默认数据根名（miqi/utils/helpers.py DEFAULT_DATA_DIR）。
 * 与 CANDIDATE_NAMES 不同：这是「新装默认落点」，#1175 落地时改为 .forge。
 */
export const ACTIVE_DATA_ROOT_DEFAULT_NAME = constants.activeDataRootDefaultName;
export const PACKAGED_USER_DATA_DIR_NAME = constants.packagedUserDataDirName;
export const UPDATER_CACHE_DIR_NAME = constants.updaterCacheDirName;
/** HKCU 注册表键路径（写入 DataRoot 与 #1176 的 InstallPath 共用）。 */
export const REGISTRY_KEY_PATH = constants.registryKeyPath;
export const REGISTRY_VALUE_DATA_ROOT = constants.registryValueDataRoot;

export type CleanupPlatform = 'win32' | 'darwin' | 'linux' | 'other';

export interface CleanupContext {
  platform: CleanupPlatform;
  homeDir: string;
  /** Electron app.getPath('appData')。 */
  appDataDir: string;
  /** Electron app.getPath('userData') 所在的 Local AppData（Windows）。 */
  localAppDataDir: string;
  /** 打包版启动时写入的注册表数据根；卸载器/清理在无此值时退回默认名探测。 */
  registryDataRoot: string | null;
  env: Record<string, string | undefined>;
  /** %SystemRoot%，用于拒绝把系统目录当数据根删除。 */
  systemRoot?: string;
  /** 目录存在性探测（主进程注入 fs.stat；纯模块不碰文件系统）。 */
  dirExists?: (dir: string) => boolean;
}

// ---------------------------------------------------------------------------
// 安全校验
// ---------------------------------------------------------------------------

export interface SafetyDecision {
  safe: boolean;
  /** unsafe 时的原因（进日志/失败报告）。 */
  reason?: string;
}

function samePath(a: string, b: string, platform: CleanupPlatform): boolean {
  if (!a || !b) return false;
  return platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

/** p 等于 base 或位于 base 之内（win32 大小写不敏感）。 */
function isSameOrInside(p: string, base: string, platform: CleanupPlatform): boolean {
  if (samePath(p, base, platform)) return true;
  const prefix = platform === 'win32' ? `${base.toLowerCase()}\\` : `${base}/`;
  return platform === 'win32' ? p.toLowerCase().startsWith(prefix) : p.startsWith(prefix);
}

/**
 * 删除根目录的安全判定。放行条件之外的路径一律拒绝：删除任何「不确定」
 * 的目录的风险远大于留一点残留。
 */
export function isSafeDeletionRoot(dir: string, ctx: CleanupContext): SafetyDecision {
  if (!dir || !dir.trim()) return { safe: false, reason: '路径为空' };
  // Windows 的 path.resolve 对 NUL 不抛错（fs 系统调用层才会失败），
  // 但含 NUL 的路径永远无效——在这里显式拒绝。
  if (dir.includes('\0')) return { safe: false, reason: '路径包含非法字符' };
  let resolved: string;
  try {
    resolved = path.resolve(dir);
  } catch {
    return { safe: false, reason: `路径无法解析: ${dir}` };
  }
  const root = path.parse(resolved).root;
  if (samePath(resolved, root, ctx.platform)) {
    return { safe: false, reason: `拒绝删除文件系统根目录: ${resolved}` };
  }
  if (samePath(resolved, ctx.homeDir, ctx.platform)) {
    return { safe: false, reason: `拒绝删除用户主目录: ${resolved}` };
  }
  if (ctx.systemRoot && isSameOrInside(resolved, ctx.systemRoot, ctx.platform)) {
    return { safe: false, reason: `拒绝删除系统目录: ${resolved}` };
  }
  if (samePath(resolved, ctx.appDataDir, ctx.platform)) {
    return { safe: false, reason: `拒绝删除 %APPDATA% 本身: ${resolved}` };
  }
  if (samePath(resolved, ctx.localAppDataDir, ctx.platform)) {
    return { safe: false, reason: `拒绝删除 %LOCALAPPDATA% 本身: ${resolved}` };
  }
  return { safe: true };
}

// ---------------------------------------------------------------------------
// 数据根解析
// ---------------------------------------------------------------------------

export type DataRootSource = 'registry' | 'env' | 'default' | 'legacy' | 'custom';

export interface DataRootResolution {
  /** 绝对路径（resolve 后）。 */
  path: string;
  source: DataRootSource;
  safe: boolean;
  /** unsafe 时的原因。 */
  reason?: string;
}

/**
 * 显式数据根（注册表 > MIQI_HOME）。无显式配置时返回 null，调用方按
 * 默认名候选逐个探测存在性（见 dataRootDeletionTargets）。
 */
export function resolveExplicitDataRoot(ctx: CleanupContext): DataRootResolution | null {
  const registry = ctx.registryDataRoot?.trim();
  if (registry) {
    const resolved = path.resolve(registry);
    const decision = isSafeDeletionRoot(resolved, ctx);
    return decision.safe
      ? { path: resolved, source: 'registry', safe: true }
      : { path: resolved, source: 'registry', safe: false, reason: decision.reason };
  }
  const envHome = ctx.env['MIQI_HOME']?.trim();
  if (envHome) {
    const resolved = path.resolve(envHome);
    const decision = isSafeDeletionRoot(resolved, ctx);
    return decision.safe
      ? { path: resolved, source: 'env', safe: true }
      : { path: resolved, source: 'env', safe: false, reason: decision.reason };
  }
  return null;
}

/**
 * 卸载/清理应删除的数据根目录全集：
 * - 有显式数据根（注册表/MIQI_HOME）且安全 → 只删它；
 * - 否则 → 所有默认名候选目录（调用方按存在性过滤，每个都单独过安全校验）。
 */
export function dataRootDeletionTargets(ctx: CleanupContext): string[] {
  const explicit = resolveExplicitDataRoot(ctx);
  if (explicit) {
    return explicit.safe ? [explicit.path] : [];
  }
  return DATA_ROOT_CANDIDATE_NAMES.map((name) => path.join(ctx.homeDir, name));
}

/**
 * 候选目录分类：只有 default/legacy/custom 可删除；not-a-candidate 的目录
 * （解析结果不匹配任何已知模式）永不删除。
 */
export function classifyDataRootCandidate(
  dir: string,
  ctx: CleanupContext
): { kind: 'default' | 'legacy' | 'custom' | 'not-a-candidate'; name?: string } {
  let resolved: string;
  try {
    resolved = path.resolve(dir);
  } catch {
    return { kind: 'not-a-candidate' };
  }
  for (const name of DATA_ROOT_CANDIDATE_NAMES) {
    if (samePath(resolved, path.join(ctx.homeDir, name), ctx.platform)) {
      return name === '.assistant' ? { kind: 'legacy', name } : { kind: 'default', name };
    }
  }
  const explicit = resolveExplicitDataRoot(ctx);
  if (explicit && explicit.safe && samePath(resolved, explicit.path, ctx.platform)) {
    return { kind: 'custom' };
  }
  return { kind: 'not-a-candidate' };
}

/**
 * 当前生效的数据根（打包版启动时写入注册表 DataRoot）。
 * 逐行镜像 Python 侧 miqi/utils/helpers.py get_data_path() 的解析顺序：
 * 1. MIQI_HOME 显式设置 → 用它；2. legacy ~/.assistant 存在且默认目录不存在
 * → legacy；3. 否则默认目录。注意与 #1175 联动：Python 默认名改为 .forge 时
 * 同步更新 cleanup-constants.json 的 activeDataRootDefaultName。
 */
export function resolveActiveDataRoot(ctx: CleanupContext): string {
  const envHome = ctx.env['MIQI_HOME']?.trim();
  if (envHome) return path.resolve(envHome);
  const defaultDir = path.join(ctx.homeDir, ACTIVE_DATA_ROOT_DEFAULT_NAME);
  const legacyDir = path.join(ctx.homeDir, '.assistant');
  const exists = ctx.dirExists ?? (() => false);
  if (exists(legacyDir) && !exists(defaultDir)) return legacyDir;
  return defaultDir;
}

// ---------------------------------------------------------------------------
// 清理项规划（应用内清理页 + 卸载器共用）
// ---------------------------------------------------------------------------

export type CleanupItemId =
  'data-root:workspace' | 'data-root:rest' | 'user-data' | 'wsl-distro' | 'updater-cache';

export type CleanupItemKind =
  'workspace' | 'data-root' | 'user-data' | 'wsl-distro' | 'updater-cache';

export interface CleanupItem {
  id: CleanupItemId;
  kind: CleanupItemKind;
  label: string;
  description: string;
  /** 文件系统路径；null = 本平台不适用或非文件系统对象（WSL distro）。 */
  path: string | null;
  /** path 之下必须保留的子路径（data-root:rest 保留 workspace）。 */
  excludes: string[];
  /** 存在性；null = 待运行时探测。 */
  exists: boolean | null;
  defaultChecked: boolean;
  /** false = 删除它必须先退出应用（Windows 文件锁）。 */
  deletableNow: boolean;
}

export function planCleanupItems(ctx: CleanupContext): CleanupItem[] {
  const explicit = resolveExplicitDataRoot(ctx);
  // UI 展示用主数据根：显式配置优先，否则默认名候选（探测后替换）。
  const primaryRoot = explicit?.safe
    ? explicit.path
    : path.join(ctx.homeDir, ACTIVE_DATA_ROOT_DEFAULT_NAME);
  const workspacePath = path.join(primaryRoot, 'workspace');

  return [
    {
      id: 'data-root:workspace',
      kind: 'workspace',
      label: '工作目录（用户文档）',
      description: 'workspace 目录，含用户生成的文件与导出内容',
      path: workspacePath,
      excludes: [],
      exists: null,
      defaultChecked: false,
      deletableNow: true,
    },
    {
      id: 'data-root:rest',
      kind: 'data-root',
      label: '数据根其余内容',
      description: '会话、技能、插件、快照、日志、配置与沙箱状态文件',
      path: primaryRoot,
      excludes: [workspacePath],
      exists: null,
      defaultChecked: true,
      deletableNow: true,
    },
    {
      id: 'user-data',
      kind: 'user-data',
      label: '应用用户数据',
      description: '登录态、界面设置、扣费历史与 Chromium 缓存',
      path: path.join(ctx.appDataDir, PACKAGED_USER_DATA_DIR_NAME),
      excludes: [],
      exists: null,
      defaultChecked: false,
      deletableNow: false,
    },
    {
      id: 'wsl-distro',
      kind: 'wsl-distro',
      label: `WSL 沙箱发行版 ${WSL_SANDBOX_DISTRO}`,
      description: 'Windows 子系统 Linux 沙箱镜像，可能占用数 GB',
      path: null,
      excludes: [],
      exists: null,
      defaultChecked: true,
      deletableNow: true,
    },
    {
      id: 'updater-cache',
      kind: 'updater-cache',
      label: '自动更新缓存',
      description: '安装包下载缓存（#1124 接入 electron-updater 后产生）',
      path: path.join(ctx.localAppDataDir, UPDATER_CACHE_DIR_NAME),
      excludes: [],
      exists: null,
      defaultChecked: true,
      deletableNow: true,
    },
  ];
}
