export interface DocLink {
  label: string;
  href: string;
  children?: DocLink[];
}

// 构建期由 electron.vite.config.ts / vitest.config.ts 从仓库根 mkdocs.yml 注入。
const RAW_DOCS_BASE = typeof __DOCS_BASE_URL__ !== 'undefined' ? __DOCS_BASE_URL__ : '';
const RAW_REPO_URL = typeof __REPO_URL__ !== 'undefined' ? __REPO_URL__ : '';

/** 文档站根地址（末尾带 `/`，可直接与 DOCS_TREE 的相对路径拼接）。 */
export const DOCS_BASE_URL =
  RAW_DOCS_BASE && !RAW_DOCS_BASE.endsWith('/') ? `${RAW_DOCS_BASE}/` : RAW_DOCS_BASE;

export const REPO_URL = RAW_REPO_URL;

/** `https://github.com/owner/repo` → `owner/repo` */
export const REPO_LABEL = RAW_REPO_URL.replace(/^https?:\/\/[^/]+\//, '').replace(/\.git$/, '');

export const DOCS_TREE: DocLink[] = [
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
