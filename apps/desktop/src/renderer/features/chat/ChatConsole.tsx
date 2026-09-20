import {
  useState,
  useEffect,
  useSyncExternalStore,
  useRef,
  useCallback,
  useMemo,
  memo,
  Fragment,
  type ComponentProps,
} from 'react';
import { createPortal } from 'react-dom';
import { ASSET_PANEL_MIN_WIDTH } from '../../../shared/layout';
import { AgentAvatar } from './components/Avatars';
import { MiQroForgeLogo } from '../../components/MiQroForgeLogo';
import { MarkdownContent } from './components/MarkdownContent';
import { hasUserGroupAfter, lastAssistantGroupIndex } from './lastAssistantGroup';
import { SandboxHtmlFrame } from './components/SandboxHtmlFrame';
import { ThinkBlock } from './components/ThinkBlock';
import { InterruptedTurnCard } from './components/InterruptedTurnCard';
import { DiffView } from './components/DiffView';
import { renderContent } from './components/renderContent';
import { TrackedFileCard } from './components/TrackedFileCard';
import { ConfirmCardArea, ConfirmCardItem, isConfirmCard } from './components/ConfirmCardArea';
import { TurnStatusBar } from './components/TurnStatusBar';
import { QraftLoginButton, QraftLoginCard } from '../settings/components/QraftLoginCard';
import { useQraftStatus } from '../../hooks/useQraftStatus';
import { ToolCommandBlock } from './components/ToolCommandBlock';
import { useUserInput, type UserInputCardEntry } from '../../contexts/UserInputContext';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '../../components/ui/Button';
import { Tooltip } from '../../components/ui/Tooltip';
import { ContextMenu, type ContextMenuAction } from '../../components/ContextMenu';
import { cn } from '../../lib/utils';
import { Modal } from '../../components/shared';
import { formatRelativeTime, formatChatTime } from '../../lib/formatTime';
import { type ExecutionPolicy } from '../../components/ExecutionPolicySelector';
import { type ReasoningMode } from './components/ReasoningModeSwitch';
import { clampPanelWidth, createPanelWindowSync } from './panelWindowSync';
import {
  MODE_SCENES,
  SKILL_ORDER,
  SKILL_SCENE_ICON,
  SKILL_SCENE_TITLE,
  SKILL_STARTERS,
  type StarterTask,
  type WelcomeMode,
} from './welcomeScenes';
import {
  Send,
  Loader2,
  Copy,
  Check,
  CheckCircle,
  X,
  FileText,
  Image,
  LayoutGrid,
  MoreHorizontal,
  Plus,
  Eye,
  GitMerge,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ArrowDown,
  Pencil,
  BookOpen,
  GitCompare,
  Undo2,
  ListChecks,
  Settings,
  ExternalLink,
  FileSpreadsheet,
  FileBarChart,
  FolderOpen,
  Folder,
  FolderCheck,
  AlertCircle,
  FileType,
  Loader,
  ThumbsUp,
  ThumbsDown,
  RefreshCw,
  Star,
  Download,
} from 'lucide-react';
import type {
  ChatProgress,
  ChatFinal,
  ChatError,
  ChatAborted,
  ChatSubagentResult,
  SpreadsheetData,
  DocumentBlocks,
} from '../../../shared/ipc';
import { extractProgressMessage, type ProgressPayload } from './progressUtils';
import { sanitizeUiMessage } from '../../lib/sanitizeUiMessage';
import {
  classifyTrackedFiles,
  dirLabel,
  groupTrackedByDir,
} from '../../lib/taskAssetClassification';
import { sameTrackedFile } from '../../lib/tracked-path';
import { SpreadsheetPreview } from './components/SpreadsheetPreview';
import { DocxPreview } from './components/DocxPreview';
import PaperSearchResult, {
  tryParsePaperSearchResult,
  type PaperSearchPayload,
  type PaperItem,
} from './PaperSearchResult';
import { Composer, type ComposerHandle } from './Composer';

interface Attachment {
  name: string;
  type: 'image' | 'text' | 'document';
  dataUrl?: string;
  content?: string;
  size: number;
  dataBase64?: string;
  mimeType?: string;
  /** Parse status: pending → parsing → done | error */
  status?: 'pending' | 'parsing' | 'done' | 'error';
  /** Server-parsed text content, shown inline after send */
  parsedContent?: string;
  /** Client-side content fingerprint (SHA-256 hex of bytes, #968 复核)：发送前
   * 由 handleSend 预计算暂存，占位装饰 (fp:…) 与去重守卫据此区分同名异内容附件 */
  contentFp?: string;
  /** Parse error message if status === 'error' */
  parseError?: string;
}

/** 内容指纹：优先 crypto.subtle 的 SHA-256（全量字节），不可用时退回双种子 FNV-1a（同样是全量）。
 *  两条通道（浏览器 paste / 主进程剪贴板）都走这里，保证表示一致。 */
async function sha256HexOrFallback(bytes: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (subtle?.digest) {
    try {
      const digest = await subtle.digest('SHA-256', bytes as unknown as BufferSource);
      return Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('');
    } catch {
      /* fall through */
    }
  }
  let h1 = 0x811c9dc5;
  let h2 = 0x7fed2e1f;
  for (let i = 0; i < bytes.length; i++) {
    h1 ^= bytes[i];
    h1 = Math.imul(h1, 0x01000193);
    h2 ^= bytes[bytes.length - 1 - i];
    h2 = Math.imul(h2, 0x01000193);
  }
  return `${bytes.length}-${(h1 >>> 0).toString(16)}-${(h2 >>> 0).toString(16)}`;
}

function bytesFromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** 浏览器 File → 内容指纹（与主进程通道口径一致）。 */
async function fileFingerprint(file: File): Promise<string> {
  return sha256HexOrFallback(new Uint8Array(await file.arrayBuffer()));
}

const DOCUMENT_SUFFIXES_RE =
  /\.(docx|doc|pptx|ppt|xlsx|xls|pdf|odt|odp|ods|md|markdown|mdown|html|htm|csv|json|xml|yaml|yml|env|log|sql|ini|toml|htaccess|sh|bash|txt|text|rtf)$/i;

function getDocCategory(name: string): { label: string; color: string; bg: string } {
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, { label: string; color: string; bg: string }> = {
    pdf: { label: 'PDF', color: 'var(--danger)', bg: 'rgba(255,97,97,0.12)' },
    docx: { label: 'DOC', color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
    doc: { label: 'DOC', color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
    pptx: { label: 'PPT', color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
    ppt: { label: 'PPT', color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
    xlsx: { label: 'XLS', color: 'var(--success)', bg: 'rgba(16,185,129,0.12)' },
    xls: { label: 'XLS', color: 'var(--success)', bg: 'rgba(16,185,129,0.12)' },
    md: { label: 'MD', color: '#a855f7', bg: 'rgba(168,85,247,0.12)' },
    markdown: { label: 'MD', color: '#a855f7', bg: 'rgba(168,85,247,0.12)' },
    mdown: { label: 'MD', color: '#a855f7', bg: 'rgba(168,85,247,0.12)' },
    html: { label: 'HTML', color: 'var(--warning)', bg: 'rgba(245,158,11,0.12)' },
    htm: { label: 'HTML', color: 'var(--warning)', bg: 'rgba(245,158,11,0.12)' },
    csv: { label: 'CSV', color: 'var(--success)', bg: 'rgba(16,185,129,0.12)' },
    json: { label: 'JSON', color: 'var(--warning)', bg: 'rgba(245,158,11,0.12)' },
    xml: { label: 'XML', color: '#6366f1', bg: 'rgba(99,102,241,0.12)' },
    yaml: { label: 'YAML', color: 'var(--info)', bg: 'rgba(59,130,246,0.12)' },
    yml: { label: 'YAML', color: 'var(--info)', bg: 'rgba(59,130,246,0.12)' },
    env: { label: 'ENV', color: '#84cc16', bg: 'rgba(132,204,22,0.12)' },
    log: { label: 'LOG', color: 'var(--text-faint)', bg: 'rgba(138,143,152,0.12)' },
    sql: { label: 'SQL', color: '#0ea5e9', bg: 'rgba(14,165,233,0.12)' },
    ini: { label: 'INI', color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)' },
    toml: { label: 'TOML', color: '#e11d48', bg: 'rgba(225,29,72,0.12)' },
    htaccess: { label: 'HTA', color: '#d946ef', bg: 'rgba(217,70,239,0.12)' },
    sh: { label: 'SH', color: 'var(--info)', bg: 'rgba(59,130,246,0.12)' },
    bash: { label: 'SH', color: 'var(--info)', bg: 'rgba(59,130,246,0.12)' },
    txt: { label: 'TXT', color: 'var(--text-faint)', bg: 'rgba(138,143,152,0.12)' },
    text: { label: 'TXT', color: 'var(--text-faint)', bg: 'rgba(138,143,152,0.12)' },
    rtf: { label: 'RTF', color: '#ec4899', bg: 'rgba(236,72,153,0.12)' },
    odt: { label: 'DOC', color: 'var(--info)', bg: 'rgba(59,130,246,0.12)' },
    odp: { label: 'PPT', color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
    ods: { label: 'XLS', color: 'var(--success)', bg: 'rgba(16,185,129,0.12)' },
  };
  return (
    map[ext] ?? {
      label: ext.toUpperCase() || 'FILE',
      color: 'var(--text-faint)',
      bg: 'var(--surface-muted)',
    }
  );
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Mirror the bridge's `session_files_dir_key` (miqi/session/session_keys.py):
 * fold separators, and for namespaced 3+ segment keys drop the leading client
 * segment.  A local `replace(/[:\\/]/g, '_')` disagrees with the backend for
 * `miqi-desktop:desktop:<ts>` — it keeps the client prefix and yields
 * `miqi-desktop_desktop_<ts>` while the canonical directory is
 * `desktop_<ts>` — so session-scoped reads built from it miss the real
 * directory (#1051 review).
 */
function sessionFilesDirKey(sessionKey: string | null | undefined): string {
  if (!sessionKey) return '';
  const parts = sessionKey.split(':');
  const kept = parts.length >= 3 ? parts.slice(1) : parts;
  return kept
    .join('_')
    .replace(/[<>:"\/\\|?*]/g, '_')
    .trim();
}

/**
 * Parse embedded document content from message body so the UI shows
 * coloured chips instead of raw injection text.  Handles three formats:
 *   1. Client-side preview:  [File: name]\n```\n...\n```
 *   2. Binary/scanned placeholder: [name: binary file, ...] / [name: scanned PDF ...]
 *   3. Server-side parsed:   --- Document: name ---\n...\n--- End of name ---
 *
 * The LLM still receives the full content; only the display is cleaned.
 */
const FILE_BLOCK_RES = [
  /\[File: ([^\]]+)\]\n```\n[\s\S]*?\n```/g,
  /\[([^\]:]+):\s*(?:binary file|scanned PDF)[^\]]*\]/g,
  /--- Document: ([^\n]+) ---\n[\s\S]*?\n--- End of \1 ---/g,
  /--- ([^\n]+) ---\n[\s\S]*?\n--- End of \1 ---/g, // legacy: client-side inject before fix
  /\[Uploaded: ([^\]]+?)\s+[—\-]\s+use\s+pdf_read[^\]]*\]/g, // backend fallback when parse returns empty
];

interface FileChip {
  name: string;
  category: ReturnType<typeof getDocCategory>;
}

// #968 复核（CodeRabbit #969）：图片占位符解析——两分支交替：
// ① 带内容指纹尾 (fp:64hex) 的新装饰：以 fp 尾为锚点反推名称（名称可含 "]"，
//    如 IMG[1].png——旧式从首个 ] 截断会把整条装饰匹配崩坏、图片恢复丢失）；
// ② 旧版无指纹装饰：回到 [^\]]+ 语义（名称含 ] 的旧版装饰维持历史限制）。
// 名称与装饰均不含换行，捕获用 [^\n] 限定。
const IMAGE_PLACEHOLDER_RES = /\[Image:\s*([^\n]*?)\s*\(fp:[0-9a-f]{64}\)\]|\[Image:\s*([^\]]+)\]/g;

/** Extract image attachments from the "[Image: name]" placeholder the sender
 *  embeds. dataUrl stays undefined — it is re-read from the session files dir
 *  lazily after load (#659). */

// #875 D1：系统包安装 persist/runtime 失败 → App 级 toast 的 window 事件。
export const INSTALL_WARNING_EVENT = 'miqi:system-install-warning';
export type InstallWarningKind = 'persist' | 'runtime';
function extractImageAttachmentsFromContent(content: string): Attachment[] | undefined {
  const names = [...content.matchAll(IMAGE_PLACEHOLDER_RES)].map((m) => (m[1] ?? m[2]).trim());
  if (names.length === 0) return undefined;
  return names.map((name) => ({
    name,
    type: 'image' as const,
    dataUrl: undefined,
    size: 0,
    status: 'pending' as const,
  }));
}

function extractFileChips(content: string): { cleanContent: string; chips: FileChip[] } {
  const chips: FileChip[] = [];
  let clean = content;
  for (const re of FILE_BLOCK_RES) {
    clean = clean.replace(re, (_full: string, name: string) => {
      if (!chips.some((c) => c.name === name)) {
        chips.push({ name, category: getDocCategory(name) });
      }
      return '';
    });
  }
  // Image placeholders are rendered as inline previews via attachments,
  // never as raw "[Image: name]" text (#659).
  clean = clean.replace(IMAGE_PLACEHOLDER_RES, '');
  return { cleanContent: clean.trim(), chips };
}

interface Message {
  role: 'user' | 'assistant' | 'progress' | 'error' | 'subagent';
  content: string;
  /** Reasoning mode used when this message was sent (issue #680): fast/think */
  reasoningMode?: 'fast' | 'think';
  attachments?: Attachment[];
  toolHint?: boolean;
  toolCallId?: string;
  /** Tool name for specialized rendering (e.g. 'paper_search') */
  toolName?: string;
  /** 产生该消息的 turn（流式 assistant 消息由 activeTurnId 标记——
   *  2026-08-27 卡片内联关联用：计划/确认是 AI 回答的一部分） */
  turnId?: string;
  /** Parsed tool data for card rendering */
  toolData?: unknown;
  /** Structured web sources (title/url/snippet) from web_search/web_fetch (#879) */
  webSources?: MessageSource[];
  /** Original tool-call arguments (e.g. web_fetch's url) — real references */
  toolArgs?: unknown;
  action?: 'open-provider-settings' | 'retry-load' | 'login';
  actionLabel?: string;
  /** When true the message is collapsed by default (user can click to expand) */
  collapsed?: boolean;
  /** Short label shown when collapsed (e.g. "exec" or "write_file → /path/to/file") */
  summary?: string;
  /** True when this row is a restored tool result (its content is the raw
   *  tool OUTPUT, not a live hint line). Rendered with a terminal-style
   *  expandable box instead of activity parsing. */
  toolOutput?: boolean;
  /** Model chain-of-thought (DeepSeek-R1 / Kimi thinking models). Rendered as
   *  a collapsible thinking block above the message content. Issue #539. */
  reasoning?: string;
  /** Marks the live reasoning bubble during streaming so it can be replaced
   *  by the final assistant message once the turn completes. Issue #539. */
  isLiveReasoning?: boolean;
  /** Seconds elapsed from send to final for the "用时 X 秒" label. */
  reasoningElapsedS?: number;
  /** #740: this assistant bubble is a half-generated reply recovered from an
   *  execution snapshot after an interrupted turn (process exit / abort).
   *  interruptedMeta carries the snapshot payload for the resume card. */
  interrupted?: boolean;
  interruptedMeta?: {
    turnId: string;
    status: string;
    assistantContent: string;
    reasoningContent: string;
    updatedAt: number;
    tokenEstimate?: number;
  };
  timestamp: number;
}

interface MessageSource {
  tool: string;
  url: string;
  /** Structured title from web_search/web_fetch sources (#879) */
  title?: string;
  /** Structured snippet from web_search/web_fetch sources (#879) */
  snippet?: string;
}

// Stable empty array for messages without sources — keeps the `sources` prop
// referentially equal so MessageBubble's memo isn't defeated by a fresh `[]`
// on every keystroke (#1021).
const EMPTY_SOURCES: MessageSource[] = [];

/** sourcesByMsg 的键。progress 行用 toolCallId（后端每工具调用唯一），其余用
 *  timestamp。此前直接拿 Date.now() 的 timestamp 当键：同毫秒创建的两个
 *  progress 行会互相覆盖来源（先一行显示后一行的来源，#879 ③ CodeRabbit）。
 *  加 role 前缀 + toolCallId 去碰撞。 */
function sourcesKey(m: Message): string {
  return m.role === 'progress' ? `p:${m.toolCallId ?? m.timestamp}` : `a:${m.timestamp}`;
}

const TOOL_LABELS: Record<string, string> = {
  web_fetch: '网页抓取',
  web_search: '网页搜索',
  paper_search: '论文搜索',
  paper_get: '论文详情',
  create_docx: '创建 Word 文档',
  create_xlsx: '创建 Excel 表格',
  create_pptx: '创建 PPT',
  create_pdf: '创建 PDF',
  docx_write: '编辑 Word 文档',
  xlsx_write: '编辑 Excel 表格',
  pptx_write: '编辑 PPT',
  pdf_write: '编辑 PDF',
  edit_docx: '编辑 Word 文档',
  append_xlsx: '追加 Excel 数据',
  exec: '执行命令',
  read_file: '读取文件',
  write_file: '写入文件',
  edit_file: '编辑文件',
  delete_file: '删除文件',
  apply_patch: '应用补丁',
  paper_download: '下载论文',
  skill_manage: '管理技能',
};

/** Hostname (no www.) for a URL — used for the favicon + primary label. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** Extract reference URLs from a tool/progress message.
 *  Priority: the URL the tool actually touched (toolArgs) > structured
 *  paper_search cards > links found in result text (fallback). */
export function extractMessageSources(msg: Message): MessageSource[] {
  // Structured web sources (#879): web_search/web_fetch emit title/url/snippet
  // directly — use them verbatim instead of heuristically re-parsing text.
  if (msg.webSources && msg.webSources.length > 0) {
    return msg.webSources;
  }
  const sources: MessageSource[] = [];
  const skip = [
    'api.semanticscholar.org',
    '/graph/v1/',
    'developer.mozilla.org/en-US/docs/Web/HTTP',
    // Search-engine invocation / redirect URLs (the tool's own query, not a result page)
    'bing.com/search',
    'duckduckgo.com/?q=',
    'duckduckgo.com/html',
    'search.brave.com',
    'google.com/search',
    'so.com/s?q=', // 360 搜索调用
    'so.com/link?', // 360 搜索结果跳转链接
    'sogou.com/web?query=',
    'user.guancha.cn/main/search',
    'beian.miit.gov.cn',
    // RSS 聚合噪音：命名空间、图片 CDN、Google News 转发链（base64 文章 ID）
    'purl.org',
    'www.w3.org/2005/Atom',
    'www.w3.org/2000/svg',
    'search.yahoo.com/mrss',
    'lh3.googleusercontent.com',
    'ichef.bbci.co.uk',
    's.rfi.fr/media',
    'news.google.com', // 聚合页 + 转发链，无直接文章
    'rsshub.app', // RSSHub 聚合源
    'feeds.', // feeds.bbci.co.uk 等 RSS 源域名
    'www.81.cn', // 军网栏目页（被抓的聚合列表）
  ];
  // 图片/静态资源 + RSS 文件（*.xml / /rss）不是文章来源。纯域名首页保留
  // ——用户要求工具行能看到具体 URL（#539 反馈）。
  const noiseRe = /\.(jpe?g|png|gif|webp|svg|ico|css|js|xml)([?#]|$)/i;
  const rssPathRe = /\/rss[?/]|\.rss([?#]|$)/i;
  const isNoise = (u: string) =>
    noiseRe.test(u) || rssPathRe.test(u) || skip.some((s) => u.includes(s));
  const clean = (raw: string): string => raw.split('{')[0].replace(/[.,;:!?。，；：、）\]]+$/, '');
  // Deduplicate across all branches + cap: duplicate URLs produce duplicate
  // React keys and one checkUrl request each (CodeRabbit #564 review).
  const seen = new Set<string>();
  const push = (tool: string, url: string) => {
    if (!url || seen.has(url)) return;
    if (isNoise(url)) return;
    seen.add(url);
    sources.push({ tool, url });
  };

  // 1. The exact URL the tool fetched/searched — most trustworthy.
  //    toolArgs may be a single object or an array (merged tool-result group).
  const argsList = Array.isArray(msg.toolArgs) ? msg.toolArgs : [msg.toolArgs];
  for (const argsRaw of argsList) {
    if (!argsRaw || typeof argsRaw !== 'object') continue;
    const args = argsRaw as Record<string, unknown>;
    for (const key of ['url', 'link', 'href', 'query']) {
      const v = args[key];
      if (typeof v === 'string' && /^https?:\/\//i.test(v) && !skip.some((s) => v.includes(s))) {
        push(msg.toolName || 'tool', clean(v));
      }
    }
  }

  // 2. paper_search card data.
  if (msg.toolName === 'paper_search' && msg.toolData) {
    const items = (msg.toolData as { items?: { url?: string; arxiv_id?: string }[] }).items ?? [];
    for (const it of items) {
      const url = it.url || (it.arxiv_id ? `https://arxiv.org/abs/${it.arxiv_id}` : '');
      if (url) push('paper_search', clean(url));
    }
    return sources;
  }
  if (msg.toolName === 'paper_search') return sources; // failed search: no refs

  // 3. Fallback: links inside the result text (deduped, noise filtered).
  const content = String(msg.content ?? '');
  for (const m of content.matchAll(/https?:\/\/[^\s"'<>)\]]+/g)) {
    push(msg.toolName || 'tool', clean(m[0]));
  }
  return sources;
}

/** Parse web_search output ("N. title\n   url\n   body") into structured
 *  result cards for the chain row (deep-search style). */
interface WebSearchItem {
  title: string;
  url: string;
  snippet?: string;
}

function parseWebSearchResults(content: string): WebSearchItem[] {
  const items: WebSearchItem[] = [];
  // 兼容两种输出格式：think 模式 `1. title` / FAST fan-out 兜底 `- title`（#879）
  const entryRe = /^(?:\d+\.|-)\s+(.+)$/gm;
  let m: RegExpExecArray | null;
  while ((m = entryRe.exec(content)) !== null) {
    const title = m[1].trim();
    const rest = content.slice(m.index + m[0].length).split(/\n(?=(?:\d+\.|-)\s)/)[0];
    const lines = rest
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);
    const url = lines.find((l) => /^https?:\/\//i.test(l)) ?? '';
    const snippet = lines.find((l) => !/^https?:\/\//i.test(l)) ?? '';
    if (title && url) items.push({ title, url, snippet });
  }
  return items;
}

function isMissingProviderConfigMessage(message: string) {
  const normalized = message.toLowerCase();
  return normalized.includes('no api key configured');
}

function isProviderConfigurationProblem(message: string, code?: string) {
  if (code === 'NO_API_KEY') return true;
  const normalized = message.toLowerCase();
  return (
    isMissingProviderConfigMessage(message) ||
    normalized.includes('模型服务认证失败') ||
    normalized.includes('authentication') ||
    normalized.includes('invalid api key') ||
    normalized.includes('api key') ||
    normalized.includes('api base') ||
    normalized.includes('当前模型配置')
  );
}

function createProviderConfigMessage(
  content?: string,
  action: 'open-provider-settings' | 'login' = 'open-provider-settings',
  actionLabel?: string
): Message {
  return {
    role: 'error',
    content: content || '尚未配置模型服务。请先配置 Provider/API Key 后再发送消息。',
    action,
    actionLabel: actionLabel ?? (action === 'login' ? '登录 MiQroForge 账号' : '去配置模型'),
    timestamp: Date.now(),
  };
}

/** #922：登录但 AI 网关未 active 时的发送阻断提示（不含可点 action，指向平台页文案）。 */
function createGatewayBlockedMessage(): Message {
  return {
    role: 'error',
    content:
      'AI 网关未就绪（平台开通中或不可用），暂时无法发起会话。请到 设置 → MiQroForge 平台 查看网关状态或重新登录后重试。',
    timestamp: Date.now(),
  };
}

/** 登录失效时的统一拦截文案（发送拦截与流错误路径共用，避免气泡正文与登录按钮语义冲突）。 */
export const RELOGIN_INTERCEPT_TEXT = 'MiQroForge 平台登录已失效，请重新登录后继续会话。';

/**
 * 登录失效拦截的消息列表变换（纯函数，便于单测）：
 *  - 普通发送：乐观 user 气泡按（role + 时间戳）就地替换为重登引导。
 *    从尾部向前查找——等待 qraft.status() 期间其他监听器（如子代理
 *    持久事件）可能追加消息，尾部未必是 user 气泡；
 *  - 恢复中断回合（#740）：无乐观 user 气泡，且 handleResumeTurn 已移除
 *    中断卡——恢复卡片（resumeMsg）并追加重登引导，避免上下文丢失；
 *  - 找不到匹配且无恢复卡片（会话已切换等）：原样返回。
 */
export function applyReloginIntercept(
  prev: Message[],
  userMsg: Message,
  resumeMsg: Message | null
): Message[] {
  let userIndex = -1;
  for (let i = prev.length - 1; i >= 0; i -= 1) {
    if (prev[i].role === 'user' && prev[i].timestamp === userMsg.timestamp) {
      userIndex = i;
      break;
    }
  }
  if (userIndex >= 0) {
    return [
      ...prev.slice(0, userIndex),
      createProviderConfigMessage(RELOGIN_INTERCEPT_TEXT, 'login'),
      ...prev.slice(userIndex + 1),
    ];
  }
  if (resumeMsg) {
    return [...prev, resumeMsg, createProviderConfigMessage(RELOGIN_INTERCEPT_TEXT, 'login')];
  }
  return prev;
}

/* ─── Tracked file from tool hints ───────────────────────────────── */
interface TrackedFile {
  path: string;
  name: string;
  op: 'read' | 'write' | 'edit' | 'delete';
  /** epoch ms of last operation */
  lastSeen: number;
  /** path was truncated in the progress message (ends with ...) */
  truncated?: boolean;
  /** 产出该文件的工具名（如 create_docx / graph_render / write_file），#879 ③ 追溯 */
  sourceTool?: string;
  /** 产出该文件的回合序号（第几个 user 回合，从 0 起），#879 ③ 追溯 */
  turnId?: number;
  /** #1104: agent 通过 declare_result_files 显式声明为结果文件 */
  result?: boolean;
}

const OFFICE_FILE_RE = /\.(docx|xlsx|pptx|ppt|xls|doc|odt|odp|ods)$/i;
const PDF_FILE_RE = /\.pdf$/i;
const TEXT_SUFFIXES_RE =
  /\.(md|markdown|mdown|txt|text|csv|json|yaml|yml|xml|log|env|sql|ini|toml|htaccess|sh|bash|rtf)$/i;
const OFFICE_FILE_RE_LEGACY = /\.(docx|xlsx|pptx|ppt)$/i;

/** Extract text from a PDF buffer by parsing BT/ET text blocks.
 *  Fast client-side extraction — handles text-based PDFs (not scanned). */
function extractPdfText(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer),
    limit = Math.min(bytes.length, 2_000_000);
  let raw = '';
  for (let i = 0; i < limit; i++) raw += String.fromCharCode(bytes[i]);
  const results: string[] = [];
  let pos = 0;
  while (pos < raw.length) {
    const bt = raw.indexOf('BT', pos);
    if (bt === -1) break;
    const et = raw.indexOf('ET', bt + 2);
    if (et === -1) break;
    const block = raw.slice(bt + 2, et);
    for (const m of block.matchAll(/\(([^)]*)\)\s*Tj/g)) if (m[1].trim()) results.push(m[1]);
    for (const m of block.matchAll(/\[([^\]]*)\]\s*TJ/g))
      for (const im of m[1].matchAll(/\(([^)]*)\)/g)) if (im[1].trim()) results.push(im[1]);
    pos = et + 2;
  }
  return results.join(' ') || '';
}

/** Decode base64 → Blob URL (PDF rich preview, #877). Caller revokes the URL. */
function base64ToBlobUrl(dataBase64: string, mimeType: string): string {
  const binary = atob(dataBase64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mimeType }));
}

/** UTF-8-safe bytes → base64 (#877「下载/另存为」text fallback). */
function bytesToBase64(bytes: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

/** #880: 根据文件路径与内容给出「格式不支持」的具体原因。 */
export function unsupportedPreviewReason(path: string, content?: string): string {
  if (content && /^\(Could not open file/.test(content)) {
    return '文件无法打开，可能已被删除或路径无效';
  }
  const ext = (path.split('.').pop() || '').toLowerCase();
  if (
    /^(png|jpe?g|gif|bmp|webp|ico|svg|tiff?|zip|rar|7z|tar|gz|exe|dll|bin|iso|mp3|mp4|avi|mov|mkv|wav)$/.test(
      ext
    )
  ) {
    return '该文件是二进制/媒体格式，应用内无法预览其内容';
  }
  if (/^(xls|ppt|rtf)$/.test(ext)) {
    return '该 Office 文件为旧格式，应用内暂不支持解析';
  }
  return '该文件格式暂不支持应用内预览';
}

function getMimeTypeFromName(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase();
  const mimeMap: Record<string, string> = {
    pdf: 'application/pdf',
    docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    doc: 'application/msword',
    pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    ppt: 'application/vnd.ms-powerpoint',
    xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    xls: 'application/vnd.ms-excel',
    odt: 'application/vnd.oasis.opendocument.text',
    odp: 'application/vnd.oasis.opendocument.presentation',
    ods: 'application/vnd.oasis.opendocument.spreadsheet',
  };
  return ext ? mimeMap[ext] || 'application/octet-stream' : 'application/octet-stream';
}

function getDocIcon(name: string) {
  const ext = name.split('.').pop()?.toLowerCase();
  switch (ext) {
    case 'pdf':
      return FileText;
    case 'xlsx':
    case 'xls':
    case 'csv':
    case 'ods':
      return FileSpreadsheet;
    case 'pptx':
    case 'ppt':
    case 'odp':
      return FileBarChart;
    default:
      return FileType;
  }
}

/** 全局分钟 ticker(#1011,review):所有 TimestampLabel 共享同一个 60s
 *  interval,label 通过 useSyncExternalStore 订阅 —— 避免每条用户消息
 *  各自建立长期 setInterval(长会话数百 timer)。 */
let minuteTickValue = 0;
const minuteTickListeners = new Set<() => void>();
if (typeof window !== 'undefined') {
  window.setInterval(() => {
    minuteTickValue += 1;
    minuteTickListeners.forEach((notify) => notify());
  }, 60_000);
}
function subscribeMinuteTick(notify: () => void): () => void {
  minuteTickListeners.add(notify);
  return () => {
    minuteTickListeners.delete(notify);
  };
}
function getMinuteTickSnapshot(): number {
  return minuteTickValue;
}

/** 用户消息时间标签——订阅全局分钟 tick,跨午夜自动刷新;隔离于 memo
 *  气泡树(不牵动整棵 MessageBubble 重渲染)。 */
const TimestampLabel = memo(function TimestampLabel({ timestamp }: { timestamp: number }) {
  useSyncExternalStore(subscribeMinuteTick, getMinuteTickSnapshot, getMinuteTickSnapshot);
  const label = formatChatTime(timestamp);
  if (!label) return null;
  return (
    <div className="w-full text-center pt-1 pb-0.5">
      <span className="text-[11px] leading-none text-[var(--text-faint)] select-none">{label}</span>
    </div>
  );
});

function relativeTimeLabel(timestamp?: number | string | null, now = Date.now()): string {
  if (timestamp === undefined || timestamp === null) return '尚未更新';
  const value = typeof timestamp === 'number' ? timestamp : Date.parse(timestamp);
  if (!Number.isFinite(value)) return '尚未更新';
  const diff = now - value;
  // For < 2 days, delegate to the shared relative formatter + "更新" suffix
  if (diff < 2 * 86_400_000) {
    return `${formatRelativeTime(timestamp, { suffix: '更新', now })}`;
  }
  // For older entries, keep the "X天前更新" format
  return `${Math.floor(diff / 86_400_000)} 天前更新`;
}

export function buildTaskHeaderMeta(
  updatedAt: number | string | null | undefined,
  fileCount: number,
  activePluginCount: number,
  now = Date.now()
): string {
  const fileLabel = `${fileCount} 个文件`;
  const pluginLabel = `${activePluginCount} 个启用插件`;
  return `${relativeTimeLabel(updatedAt, now)} · ${fileLabel} · ${pluginLabel}`;
}

export function buildTaskShareText({
  title,
  meta,
  messages,
  files,
}: {
  title: string;
  meta: string;
  messages: Message[];
  files: TrackedFile[];
}): string {
  const visibleMessages = messages
    .filter((message) => message.role === 'user' || message.role === 'assistant')
    .slice(-8);
  const messageLines =
    visibleMessages.length > 0
      ? visibleMessages.map((message) => {
          const role = message.role === 'user' ? '用户' : 'MiQroForge';
          const content = message.content.trim().replace(/\s+/g, ' ');
          return `- ${role}: ${content || '(空消息)'}`;
        })
      : ['- 暂无对话内容'];
  const fileLines =
    files.length > 0 ? files.map((file) => `- ${file.name} (${file.op})`) : ['- 暂无文件'];

  return [
    `# ${title}`,
    '',
    meta,
    '',
    '## 最近对话',
    ...messageLines,
    '',
    '## 相关文件',
    ...fileLines,
  ].join('\n');
}

export function buildTaskReproContext({
  sessionKey,
  title,
  meta,
  messages,
  files,
}: {
  sessionKey: string;
  title: string;
  meta: string;
  messages: Message[];
  files: TrackedFile[];
}): string {
  const visibleMessages = messages
    .filter((message) => message.role === 'user' || message.role === 'assistant')
    .slice(-12);
  const messageLines =
    visibleMessages.length > 0
      ? visibleMessages.map((message) => {
          const role = message.role === 'user' ? '用户' : 'MiQroForge';
          const content = message.content.trim().replace(/\s+/g, ' ');
          return `- ${role}: ${content || '(空消息)'}`;
        })
      : ['- 暂无对话内容'];
  const fileLines =
    files.length > 0
      ? files.map((file) => `- [${file.op}] ${file.path || file.name}`)
      : ['- 暂无文件'];

  return [
    '# MiQroForge 任务复现上下文',
    '',
    `- 会话: ${sessionKey}`,
    `- 标题: ${title}`,
    `- 状态: ${meta}`,
    '',
    '## 最近对话',
    ...messageLines,
    '',
    '## 相关文件',
    ...fileLines,
  ].join('\n');
}

export function getTaskShareDownloadName(title: string, timestamp = Date.now()): string {
  const safeTitle =
    title
      .trim()
      .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '-')
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 48) || 'miqi-task';
  const stamp = new Date(timestamp).toISOString().replace(/[:.]/g, '-');
  return `${safeTitle}-${stamp}.md`;
}

/**
 * Extract the thread rows from a `threads.list` result, defensively, so the
 * resume path tolerates either backend page shape. The backend `Page.to_dict()`
 * (thread_protocol.py:94) envelopes rows under `data`; the legacy TS
 * `ThreadListResult` type declared them under `items`. Read both so a
 * field-name mismatch between the running backend and this helper can't
 * silently empty the list and force every session to mint a fresh thread.
 *
 * Pure + exported so the whole `threads.list → extractThreadListRows →
 * pickThreadToResume` wiring is unit-tested with backend-shaped payloads
 * without mounting the React component (see chatConsoleThreadResume.test.ts).
 */
export function extractThreadListRows(listResult: unknown): unknown[] {
  if (Array.isArray(listResult)) return listResult;
  const obj = listResult as Record<string, unknown> | null | undefined;
  const rows = obj?.data ?? obj?.items;
  return Array.isArray(rows) ? rows : [];
}

/**
 * Pick the best non-archived, non-ephemeral stored thread id from a
 * `thread/list` result, for resuming an existing conversation when
 * (re)entering a session (Issue #490).
 *
 * Selection rule (chosen over a plain most-recent sort to survive legacy
 * fragmented sessions): prefer the thread holding the MOST persisted turns
 * (`turnCount`, surfaced by backend `_thread_list`), ties broken by the
 * largest `updatedAt` (fallback `createdAt`). Rationale — a fragmented
 * session has several thread_ids; the most-recently-touched one may be
 * nearly empty (e.g. a thread that only captured the user repeatedly
 * asking "what did we do before"), while the thread with the most turns
 * holds the real conversation the user expects to recall. On a clean
 * single-thread session both heuristics agree. Returns `null` when there
 * is no resumable thread. `items` are the loose rows from the
 * `ThreadView.to_dict` camelCase shape: `id`, `turnCount`, `updatedAt`,
 * `createdAt`, `archived`, `ephemeral`.
 *
 * Pure + exported so the resume-selection rule is unit-tested without
 * mounting the React component. The load `useEffect` calls this on the
 * `threads.list` result and stores the returned id in
 * `currentThreadIdRef` so subsequent `chat.send` reuses it instead of
 * minting a fresh thread_id that would orphan prior history.
 */
export function pickThreadToResume(items: unknown): string | null {
  const rows = (Array.isArray(items) ? items : []) as Array<Record<string, unknown>>;
  const candidates = rows
    .filter(
      (t) =>
        !!t &&
        !t.archived &&
        !t.ephemeral &&
        typeof t.id === 'string' &&
        (t.id as string).length > 0
    )
    .map((t) => ({
      id: t.id as string,
      turns: Number(t.turnCount ?? 0) || 0,
      ts: Number(t.updatedAt ?? t.createdAt ?? 0) || 0,
    }))
    .sort((a, b) => b.turns - a.turns || b.ts - a.ts);
  return candidates.length > 0 ? candidates[0].id : null;
}

/** Extract file path + operation from a tool-hint progress text.
 *  Nanobot tool hints look like:
 *    "Read: /abs/path/to/file.ts"
 *    "Write: src/components/Foo.tsx"
 *    "Edit: README.md"
 *    "Delete: tmp/foo.log"
 *    "Reading file src/foo.ts …"
 *    "Writing file /path/to/bar.py"
 */
function parseToolHint(
  text: string
): { path: string; op: TrackedFile['op']; truncated: boolean } | null {
  const patterns: Array<[RegExp, TrackedFile['op']]> = [
    // "Read: /abs/path/to/file.ts"  or  "Reading file src/foo.ts …"
    [/^(?:Read|Reading(?:\s+file)?)[:\s]+(.+?)(?:\s*….*)?$/i, 'read'],
    [/^(?:Write|Writing(?:\s+file)?)[:\s]+(.+?)(?:\s*….*)?$/i, 'write'],
    [/^(?:Edit|Editing(?:\s+file)?)[:\s]+(.+?)(?:\s*….*)?$/i, 'edit'],
    [/^(?:Delete|Deleting(?:\s+file)?)[:\s]+(.+?)(?:\s*….*)?$/i, 'delete'],
    // nanobot / miqi style: write_file("path"), read_file("path"), edit_file("path")
    [/(?:write|edit|delete|read)_file\s*\(\s*["'](.+?)["']\s*\)/i, 'write'],
    // Office creation tools create files in the workspace.
    [
      /(?:create_docx|create_xlsx|create_pptx|create_pdf|pdf_write|docx_write|xlsx_write|pptx_write)\s*\(\s*["'](.+?)["']\s*\)/i,
      'write',
    ],
    [/(?:edit_docx|append_xlsx)\s*\(\s*["'](.+?)["']\s*\)/i, 'edit'],
    // Office tool success: "Created: file.xlsx (3 sheet(s))"
    [/^(?:Created|Appended):\s+(.+?\.\w{1,6})(?:\s*\(.*\))?$/i, 'write'],
    // Generic fallback: only match clear file-path patterns like
    // "Saved to: file.pdf", "Output: path/to/file.pdf" or "Downloading: file.pdf"
    // where the prefix is a known verb and the path has a directory separator or
    // a known extension.  This avoids false positives from arbitrary curl output.
    [
      /(?:Saving|Saved|Writing|Written|Downloading|Downloaded|Output|Result)(?:\s+to)?[:\s]\s*(.+?\.[a-zA-Z]{1,6})/i,
      'write',
    ],
    // Also match the natural language "file/path: something.ext"
    [/(?:file|path)[:\s]+((?:\S+\/)?\S+\.[a-zA-Z]{1,6})/i, 'read'],
  ];
  for (const [re, op] of patterns) {
    const m = text.match(re);
    if (m) {
      let raw = m[1].trim().replace(/['"]/g, '');
      // Detect truncation (ends with ...)
      const truncated = raw.endsWith('...') || raw.endsWith('…');
      // Strip trailing ellipsis / quotes
      raw = raw
        .replace(/\.{3,}$/g, '')
        .replace(/…$/g, '')
        .trim();
      // Must look like a file path (contains '/' or '\\' or has extension)
      if (raw && /[/\\.]/.test(raw)) {
        // Infer op from the MATCHED verb, not the regex source: the combined
        // `(?:write|edit|delete|read)_file(...)` alternation makes
        // re.source.includes('write') true for EVERY *_file call — read_file
        // was mis-tracked as WRITE (phantom result assets under #607).
        let inferredOp = op;
        const verb = m[0].toLowerCase();
        if (verb.startsWith('read')) inferredOp = 'read';
        else if (verb.startsWith('edit')) inferredOp = 'edit';
        else if (verb.startsWith('delete')) inferredOp = 'delete';
        else if (verb.startsWith('write')) inferredOp = 'write';
        else inferredOp = op; // keep the pattern's declared op (e.g. create_docx → write)
        return { path: raw, op: inferredOp, truncated };
      }
    }
  }
  return null;
}

function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() ?? path;
}

/** #1104: collapsible row standing in for a bulk directory's files. */
function AssetDirGroupRow({
  dir,
  files,
  renderFile,
}: {
  dir: string;
  files: TrackedFile[];
  renderFile: (file: TrackedFile) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="asset-dir-group">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 rounded-lg px-2.5 py-2 transition-colors hover:opacity-90"
        style={{
          background: 'var(--surface-muted)',
          border: '1px solid var(--border-subtle)',
        }}
        title={dir}
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        <Folder size={12} className="shrink-0" style={{ color: 'var(--text-faint)' }} />
        <span className="text-[11px] font-medium truncate flex-1 text-left text-text">
          {dirLabel(dir)}
        </span>
        <span className="text-[10px] shrink-0 text-text-faint">{files.length} 个文件</span>
      </button>
      {open && <div className="flex flex-col gap-2 mt-2">{files.map(renderFile)}</div>}
    </div>
  );
}

/** Normalise a tracked path: backslashes→slashes, strip an absolute workspace
 *  prefix so `C:/…/workspace/sessions/<k>/files/x.html` and
 *  `sessions/<k>/files/x.html` collapse to the same string. */
function normalizeTrackedPath(p: string): string {
  let s = p.replace(/\\/g, '/');
  const wsIdx = s.lastIndexOf('/workspace/');
  if (wsIdx >= 0) s = s.slice(wsIdx + '/workspace/'.length);
  if (s.startsWith('/home/miqi/workspace/')) s = s.slice('/home/miqi/workspace/'.length);
  return s;
}

/** Whether a tracked file actually exists on disk. A missing file resolves to
 *  null at the bridge (sendSafe), so a read returning neither content nor
 *  base64 means it was only referenced, never saved. */
async function fileExists(path: string, sessionKey: string | null | undefined): Promise<boolean> {
  try {
    const r = await window.miqi.files.read(path, sessionKey ?? undefined);
    return !!r && (r.content !== undefined || r.data_base64 !== undefined);
  } catch {
    return false;
  }
}

/** Merge tracked files, collapsing entries that point at the same file:
 *  bare filename vs full session path, or absolute vs relative workspace path.
 *  Same-named files in different directories stay distinct.
 *
 *  `workspaceRoot` is the session's own workspace — needed to tell "the
 *  absolute form of this relative key" from "another file with the same tail"
 *  (#1104 review).  Display-only: it never feeds a containment check. */
function mergeTrackedFiles(
  existing: TrackedFile[],
  incoming: Array<{
    path: string;
    name?: string;
    op?: TrackedFile['op'];
    lastSeen?: number;
    sourceTool?: string;
    turnId?: number;
    /** #1104: agent 显式声明的结果文件标记——合并时 sticky，不被后续流式更新抹掉 */
    result?: boolean;
  }>,
  workspaceRoot?: string | null
): TrackedFile[] {
  const out = [...existing];
  for (const f of incoming) {
    const np = normalizeTrackedPath(f.path ?? '');
    if (!np) continue;
    const entry: TrackedFile = {
      path: np,
      name: f.name ?? basename(np),
      op: f.op ?? 'read',
      lastSeen: f.lastSeen ?? Date.now(),
      sourceTool: f.sourceTool,
      turnId: f.turnId,
    };
    const existingIdx = out.findIndex((p) => {
      const np2 = normalizeTrackedPath(p.path);
      if (sameTrackedFile(np2, np, workspaceRoot)) return true;
      const oneIsBare = !np2.includes('/') || !np.includes('/');
      return oneIsBare && basename(np2) === basename(np);
    });
    // #1104：声明过的结果标记在覆盖合并时必须保留（ledger 加载先于流式更新）
    if (f.result === true || (existingIdx >= 0 && out[existingIdx].result === true)) {
      entry.result = true;
    }
    if (existingIdx >= 0) {
      // 后端下发（backend）不含 sourceTool/turnId 时，保留消息提取（existing）的字段，
      // 否则文件卡片的「相关引用」会在会话加载/最终刷新时被清空（#879 ③ CodeRabbit）。
      out[existingIdx] = {
        ...entry,
        sourceTool: entry.sourceTool ?? out[existingIdx].sourceTool,
        turnId: entry.turnId ?? out[existingIdx].turnId,
      };
    } else out.push(entry);
  }
  return out;
}

/** Normalise a sandbox-internal path to a workspace-relative path.
 *  Strips /home/miqi/workspace/ prefix so the path resolves correctly on the
 *  host filesystem.  Leaves relative paths and non-sandbox absolute paths
 *  unchanged — the IPC handlers resolve them against the workspace root. */
function normalizeSandboxPath(p: string): string {
  if (p === '/home/miqi/workspace') return '.';
  if (p.startsWith('/home/miqi/workspace/')) return p.slice('/home/miqi/workspace/'.length);
  return p;
}

const DEFAULT_SESSION = 'desktop:default';

/** #858/#905 门控决策点：reply-head 思考块组是否渲染。
 *
 * 历史教训：#858 在调用处加了 `reasoningMode !== 'fast'` 门控，fast
 * （极速回答，默认模式）下思考过程整体消失。决策收拢成单点并导出，
 * 让回归测试直接锁定——任何模式都必须渲染（#783 决策），门控若被
 * 加回此处，测试立即失败。
 *
 * 约定（2026-09 复审 P2）：reply-head 渲染必须经本函数判断后再渲染
 * ThinkingBlockGroup——勿改成直接渲染（绕过决策点）或在本函数之外
 * 另加条件（门控改写在别处时本测试无法拦截）。任何对渲染条件的
 * 改动都必须同步更新 ChatConsole.test.ts 的门控决策用例。
 */
export function shouldRenderThinkingGroup(_mode: ReasoningMode): boolean {
  return true;
}

/** 思考块消息组：Agent 头像头部 + ThinkBlock（#858/#905 回归点）。
 * 两种模式（fast/think）都渲染——fast 隐藏思考块的过度修复已被移除，
 * 图标跟随消息自身模式。导出以便回归测试直接覆盖渲染路径。 */
export function ThinkingBlockGroup({
  thinking,
  fallbackMode,
}: {
  thinking: {
    reasoning?: string;
    isLiveReasoning?: boolean;
    reasoningElapsedS?: number;
    reasoningMode?: 'fast' | 'think';
  };
  fallbackMode?: 'fast' | 'think';
}) {
  return (
    // 保持原始两层结构（#905 review）：头部行（头像 + 名字）与
    // ThinkBlock 是平级块——ThinkBlock 自身是 flex 容器（flex-1 /
    // self-stretch / 垂直线），塞进头部 flex row 会破坏宽度与折叠布局。
    <div>
      <div className="flex items-center gap-2 mb-3 pl-2">
        <AgentAvatar />
        <span
          className="text-[16px] font-semibold shrink-0 whitespace-nowrap"
          style={{ color: 'var(--text)' }}
        >
          MiQroForge
        </span>
      </div>
      {/* 思考块两种模式都展示（#783: 极速/深度都展示思考过程，
        fast 隐藏过度已修复）——图标跟随消息自身模式：
        fast 🚀 快速思考 / think 🧠 深度思考（#680 跟进）。 */}
      <ThinkBlock
        reasoning={thinking.reasoning ?? ''}
        defaultOpen={thinking.isLiveReasoning}
        elapsedSeconds={thinking.reasoningElapsedS}
        live={thinking.isLiveReasoning}
        mode={(thinking.reasoningMode ?? fallbackMode) as 'fast' | 'think'}
      />
    </div>
  );
}

function messageContentToString(content: unknown): string {
  return typeof content === 'string' ? content : JSON.stringify(content);
}

interface ToolActivity {
  name: string;
  duration?: string;
}

function toolDisplayName(name: string): string {
  return TOOL_LABELS[name] ?? name;
}

const TASK_VERBS = [
  '写',
  '生成',
  '设计',
  '分析',
  '对比',
  '比较',
  '规划',
  '研究',
  '总结',
  '翻译',
  '编程',
  '实现',
  '构建',
  '开发',
  '评估',
  '论证',
  '调研',
  '优化',
  '解决',
  '制定',
];
const REQUIRE_HINTS = [
  '保存到',
  '导出',
  '写成',
  '生成文档',
  '做成',
  '分点',
  '列出',
  '引用',
  '附上',
  '桌面',
  '文件',
];
const OPEN_QUERIES = ['为什么', '如何', '什么原因', '怎么', '有何影响', '怎样'];

/** 复杂问题多维打分（#680 跟进 v2）：任务动词/对象规模/附加要求/开放问句/
 *  长文本 各计分，总分 ≥3 判复杂——比"≥30 字+关键词"更准。毫秒级，无 LLM。 */
function complexityScore(text: string): number {
  let score = 0;
  if (text.trim().length >= 80) score += 1;
  if (TASK_VERBS.some((w) => text.includes(w))) score += 2;
  // 对象规模：含对比/并列词（中文 \b 边界不适用，直接包含匹配）
  if (/和|与|vs|对比/.test(text)) score += 1;
  if (REQUIRE_HINTS.some((w) => text.includes(w))) score += 1;
  if (OPEN_QUERIES.some((w) => text.includes(w))) score += 1;
  // 多句/编号结构（长指令）
  if ((text.match(/[。\n；;]/g) ?? []).length >= 2) score += 1;
  return score;
}

function isComplexQuestion(text: string): boolean {
  return complexityScore(text) >= 3;
}

/** Per-tool emoji for the chain icons — colorful, tool-call style (社区标准
 *  🔧 表示工具，⚡ 强调执行；文件/文档/网络类用对应物象 emoji）。 */
const TOOL_ICON_EMOJI: Record<string, string> = {
  exec: '⚡',
  read_file: '📄',
  list_dir: '📂',
  write_file: '✍️',
  edit_file: '✍️',
  delete_file: '🗑️',
  apply_patch: '🔧',
  create_docx: '📝',
  docx_write: '📝',
  create_xlsx: '📊',
  xlsx_write: '📊',
  create_pptx: '📽️',
  pptx_write: '📽️',
  create_pdf: '📕',
  pdf_write: '📕',
  web_search: '🔍',
  web_fetch: '🌐',
  paper_search: '🔍',
  paper_get: '📑',
  paper_download: '📥',
  cron: '⏰',
  memory: '💾',
  message: '💬',
  session_search: '🔎',
  skill_manage: '🧰',
  spawn: '👥',
  task_begin: '🚩',
  task_end: '🏁',
  trace_search: '🧭',
};

function toolIconEmoji(name: string): string {
  if (TOOL_ICON_EMOJI[name]) return TOOL_ICON_EMOJI[name];
  // MCP 网关工具（mcp__xxx__yyy）统一用插头图标。
  if (name.startsWith('mcp') || name.includes('gateway')) return '🔌';
  return '🔧';
}

function formatToolDuration(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function parseToolDuration(duration?: string): number {
  const m = duration?.match(/^(\d+(?:\.\d+)?)(ms|s)$/);
  if (!m) return 0;
  return m[2] === 's' ? Number(m[1]) * 1000 : Number(m[1]);
}

function parseToolActivity(content: string): ToolActivity[] {
  return content
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const name = line.match(/^[A-Za-z_][\w.-]*/)?.[0] ?? line.slice(0, 28);
      const ms = line.match(/\((\d+)\s*ms\)/i)?.[1];
      const sec = line.match(/\((\d+(?:\.\d+)?)\s*s\)/i)?.[1];
      return {
        name,
        duration: ms ? formatToolDuration(Number(ms)) : sec ? `${sec}s` : undefined,
      };
    });
}

/** One line per unique tool, keeping the LATEST duration seen for each
 *  (a later occurrence overwrites an earlier one; a missing duration
 *  keeps any earlier value rather than erasing it). */
function groupToolActivities(activities: ToolActivity[]): ToolActivity[] {
  const byName = new Map<string, string | undefined>();
  for (const act of activities) {
    if (!act.name) continue;
    if (act.duration) byName.set(act.name, act.duration);
    else if (!byName.has(act.name)) byName.set(act.name, undefined);
  }
  return [...byName.entries()].map(([name, duration]) => ({
    name,
    duration,
  }));
}

function summarizeToolActivities(activities: ToolActivity[], fallback?: string): string {
  const calls = activities.filter((a) => a.duration);
  const totalMs = calls.reduce((sum, a) => sum + parseToolDuration(a.duration), 0);
  const suffix = totalMs > 0 ? ` · ${formatToolDuration(totalMs)}` : '';
  if (calls.length === 1) return `${toolDisplayName(calls[0].name)}${suffix}`;
  if (calls.length > 1) return `已完成 ${calls.length} 项工具调用${suffix}`;
  return fallback || '工具调用';
}

/** Extract the call's concrete target (exec command, file path) from tool
 *  args so the chain row reads "执行命令 · python x.py" instead of just the
 *  tool name. Values follow HINT_VALUE_KEYS; long ones are truncated. */
function toolCallDetail(args: unknown): string | undefined {
  const list = Array.isArray(args) ? args : args !== undefined ? [args] : [];
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    const obj = item as Record<string, unknown>;
    for (const key of HINT_VALUE_KEYS) {
      const v = obj[key];
      if (typeof v === 'string' && v.trim()) {
        return v.length > 60 ? `${v.slice(0, 60)}…` : v;
      }
    }
  }
  return undefined;
}

/** Full exec command from tool-call arguments — the untruncated text hidden
 *  behind the 60-char collapsed summary (issue #902). Merged groups carry
 *  toolArgs as an array, so walk the list like toolCallDetail does. */
export function toolCommandText(args: unknown): string | undefined {
  const list = Array.isArray(args) ? args : args !== undefined ? [args] : [];
  for (const item of list) {
    if (!item || typeof item !== 'object') continue;
    const v = (item as Record<string, unknown>).command;
    if (typeof v === 'string' && v.trim()) return v;
  }
  return undefined;
}

/** Tool-chain row label: tool name · concrete target · duration. */
function toolChainLabel(activities: ToolActivity[], args: unknown, fallback?: string): string {
  const detail = toolCallDetail(args);
  if (activities.length === 1) {
    const act = activities[0];
    return `${toolDisplayName(act.name)}${detail ? ` · ${detail}` : ''}${
      act.duration ? ` · ${act.duration}` : ''
    }`;
  }
  return `${summarizeToolActivities(activities, fallback)}${detail ? ` · ${detail}` : ''}`;
}

function isAssistantTextMessage(msg: any): boolean {
  // Reasoning-only assistant turns (thinking models may emit
  // reasoning_content with empty content) must still count as text so the
  // collapse logic keeps cross-turn reasoning merges intact (#539).
  const visible = msg?.content ?? msg?.reasoning_content ?? '';
  return msg?.role === 'assistant' && String(visible).trim().length > 0;
}

/**
 * An assistant message that IS tool-related (its content is about tool calls,
 * or it carries tool_calls). We keep it separate from true *text* so the
 * collapse logic can strip intermediate tool-only assistant records.
 */
function isAssistantToolCallMessage(msg: any): boolean {
  return msg?.role === 'assistant' && Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0;
}

/** Merge reasoning segments without duplicating chunks already present. */
function mergeReasoningParts(parts: string[]): string {
  const seen = new Set<string>();
  const merged: string[] = [];
  for (const part of parts) {
    for (const chunk of String(part).split('\n\n---\n\n')) {
      const trimmed = chunk.trim();
      if (!trimmed || seen.has(trimmed)) continue;
      seen.add(trimmed);
      merged.push(trimmed);
    }
  }
  return merged.join('\n\n---\n\n');
}

function collapseAssistantMessagesWithinTurns(rawMsgs: any[]): any[] {
  const result: any[] = [];
  let turnBuffer: any[] = [];

  const flushTurn = () => {
    if (turnBuffer.length === 0) return;

    const lastAssistantTextIndex = (() => {
      for (let i = turnBuffer.length - 1; i >= 0; i -= 1) {
        if (isAssistantTextMessage(turnBuffer[i])) return i;
      }
      return -1;
    })();

    // Reasoning is rendered as a standalone timeline block BEFORE the tool
    // calls, so the final answer never reorders it above the tools. #539
    const reasoningParts: string[] = [];
    let firstReasoningTs: number | null = null;
    const emitted: any[] = [];

    turnBuffer.forEach((msg, index) => {
      if (msg.role === 'assistant' && msg.reasoning_content) {
        reasoningParts.push(String(msg.reasoning_content));
        if (firstReasoningTs === null) firstReasoningTs = msg.timestamp ?? null;
      }
      if (
        isAssistantTextMessage(msg) &&
        isAssistantToolCallMessage(msg) &&
        index !== lastAssistantTextIndex
      ) {
        emitted.push({ ...msg, content: '', reasoning_content: undefined });
        return;
      }
      if (isAssistantTextMessage(msg) && index !== lastAssistantTextIndex) {
        return;
      }
      if (msg.role === 'assistant' && msg.reasoning_content) {
        const { reasoning_content, ...rest } = msg;
        emitted.push(rest);
        return;
      }
      emitted.push(msg);
    });

    if (reasoningParts.length > 0) {
      // #905 review: carry the message-level reasoning mode so history
      // restore renders the correct 🚀/🧠 label per message instead of
      // falling back to the current global mode.
      //
      // NOTE: `turnBuffer` holds RAW persisted messages (sessions.get
      // returns them as-is), whose fields are backend snake_case —
      // reasoning_mode, NOT reasoningMode.  Reading the camelCase field
      // here silently dropped the mode on every history restore.
      const reasoningMode = turnBuffer.find(
        (msg) =>
          msg.role === 'assistant' && (msg.reasoning_content || msg.reasoning) && msg.reasoning_mode
      )?.reasoning_mode;
      result.push({
        role: 'progress',
        content: mergeReasoningParts(reasoningParts),
        reasoning: mergeReasoningParts(reasoningParts),
        reasoningMode,
        timestamp: firstReasoningTs ?? Date.now(),
      });
    }
    result.push(...emitted);

    turnBuffer = [];
  };

  for (const msg of rawMsgs) {
    if (msg?.role === 'user') {
      flushTurn();
      result.push(msg);
      continue;
    }
    turnBuffer.push(msg);
  }
  flushTurn();

  return result;
}

/** Arg keys whose value is the call's target and safe to show in a hint
 *  (file paths, the exec command). Other args only get their name shown —
 *  values like paper titles or URLs are long strings that would leak
 *  into the hint instead of a concise call summary (issue #532). */
const HINT_VALUE_KEYS = ['path', 'file_path', 'filename', 'outPath', 'command', 'url', 'query'];

/** #886: whether the user round starting at *userIdx* was manually stopped.
 *  A stopped round carries the frontend's "已停止。" progress marker between
 *  the user message and the next user message.  When the user regenerates or
 *  retries such a round, the interrupted half-reply must be preserved in the
 *  timeline (the new attempt appends after it) instead of being rewound away.
 */
export function wasTurnStopped(messages: Message[], userIdx: number): boolean {
  let end = messages.length;
  for (let i = userIdx + 1; i < messages.length; i += 1) {
    if (messages[i].role === 'user') {
      end = i;
      break;
    }
  }
  for (let i = userIdx + 1; i < end; i += 1) {
    const m = messages[i];
    if (m.role === 'progress' && String(m.content ?? '').includes('已停止')) return true;
  }
  return false;
}

/** #886: convert backend interrupted-turn snapshots into resumable cards and
 *  insert each at its chronological position (right after its own user
 *  message, before the later successful turns) instead of appending at the
 *  end — the old push put the 中断卡 after the retry's answer, leaving a
 *  duplicate "ghost" user message and a visual discontinuity. */
export function insertInterruptedTurns(merged: Message[], interruptedTurns: any[]): Message[] {
  const cards: Message[] = [];
  for (const _it of interruptedTurns) {
    const _halfContent = String(_it.assistant_content ?? '');
    cards.push({
      role: 'assistant',
      content: _halfContent,
      reasoning: String(_it.reasoning_content ?? '') || undefined,
      // #834: server-measured thinking proxy persisted on the snapshot —
      // the resume card must not fall back to 1s.
      reasoningElapsedS:
        _it.reasoning_elapsed_s != null
          ? Math.max(1, Math.round(Number(_it.reasoning_elapsed_s)))
          : undefined,
      // #905 review / CodeRabbit: persist the mode on the snapshot so an
      // interrupted FAST turn restores with the 🚀/快速思考 label instead of
      // ThinkBlock's default 🧠/深度思考.
      reasoningMode: (_it.reasoning_mode as 'fast' | 'think' | undefined) ?? undefined,
      interrupted: true,
      interruptedMeta: {
        turnId: String(_it.turn_id ?? ''),
        status: String(_it.status ?? 'interrupted'),
        assistantContent: _halfContent,
        reasoningContent: String(_it.reasoning_content ?? ''),
        updatedAt: Number(_it.updated_at ?? 0) * 1000,
        tokenEstimate: _halfContent ? Math.round(_halfContent.length / 4) : 0,
      },
      timestamp: Number(_it.updated_at ?? Date.now() / 1000) * 1000,
    });
  }
  if (cards.length === 0) return merged;
  // Oldest first so each card lands in its own slot in order.
  cards.sort((a, b) => a.timestamp - b.timestamp);
  const out = merged.slice();
  for (const card of cards) {
    let ins = out.length;
    for (let i = 0; i < out.length; i += 1) {
      if (out[i].timestamp > card.timestamp) {
        ins = i;
        break;
      }
    }
    out.splice(ins, 0, card);
  }
  return out;
}

// #891 深度审阅：messagesRef 里的用户消息是否已在 merged 中存在持久化副本。
// 判定 = merged 中存在【任一条】同内容用户消息且时间戳相近（同一机器时钟：
// 前端乐观气泡 Date.now()，后端副本经 sessionMsgsToUi 转 epoch ms，同一次
// 发送的收发时间差秒级）。任一条命中即视为已持久化——保留块运行在完整
// 恢复快照之上，若只对比 merged 最后一条会把旧历史行（内容不同）误判为
// in-flight 而整段重复渲染。时间差大（≥30s）的旧文本副本不算命中——
// 跨轮重复发送的相同文本不会被误判为已持久化。
const _PERSISTED_COPY_TS_TOLERANCE_MS = 30_000;

function _isPersistedCopyOf(frontendTs: number | undefined, copyTs: number | undefined): boolean {
  if (
    frontendTs === undefined ||
    !Number.isFinite(frontendTs) ||
    copyTs === undefined ||
    !Number.isFinite(copyTs)
  ) {
    return false; // 无可靠时间戳 → 保守：不判为已持久化副本（保留前端气泡）
  }
  return Math.abs(copyTs - frontendTs) < _PERSISTED_COPY_TS_TOLERANCE_MS;
}

// #968: 用户消息去重 key——剥离发送侧追加进 content 的装饰段。handleSend 把
// 每类附件/重试提示都追加在 content 尾部，故这里每条规则都「尾锚定」（节头
// 限定为字符串头或前导 \n\n、节尾锚定 $）并迭代剥离：内嵌文件正文里的 ``` 围栏
// 或 "--- End of … ---" 行无法再提前截断惰性匹配（回溯必须抵达真正的尾部），
// 文件名含 "]" 也由贪婪捕获的回溯容忍。若某条剥离失手（如手打的形似装饰文本），
// 后果是 key 不相等 → 气泡与其副本并存（#968 双显示，方向安全），绝不会让
// 不同消息的 key 意外相等而吞掉真实消息（方向危险）。
const _DEDUP_TAIL_SECTION_RES =
  /(?:^|\n\n)(?:\[系统提示：[^\]]*\]|\[Image: [^\n]+\]|\[File: [^\n]+\]\n```\n[\s\S]*?\n```|--- Document: [^\n]+ ---\n[\s\S]*?\n--- End of [^\n]+ ---|\[[^\n]+?: [^\]]*?(?:scanned PDF|binary file|parsing on server)[^\]]*\])\s*$/;

function _userContentDedupKey(content: string): string {
  let s = content;
  let prev: string;
  do {
    prev = s;
    s = s.replace(_DEDUP_TAIL_SECTION_RES, '');
  } while (s !== prev);
  // 无文本纯附件发送：乐观气泡显示 '(attachment)' 占位符，落库副本剥离装饰后
  // 为空串——两侧统一映射到空串才能互认（#968 复核）。
  const trimmed = s.trim();
  return trimmed === '(attachment)' ? '' : trimmed;
}

// #968 复核（CodeRabbit #969）：文档附件内容解码的单一实现——handleSend 拼
// Document 装饰段与去重守卫校验内容都用它，避免两侧解码逻辑漂移（ext 白名单、
// atob/TextDecoder/extractPdfText 与 50k 截断必须完全一致，守卫才能逐字比对）。
function _decodeDocData(dataBase64: string, name: string): { extracted: string; ext: string } {
  const raw = Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0));
  const ext = name.split('.').pop()?.toLowerCase() ?? '';
  let extracted = '';
  if (ext === 'pdf') {
    extracted = extractPdfText(raw.buffer);
  } else if (
    ext === 'md' ||
    ext === 'markdown' ||
    ext === 'mdown' ||
    ext === 'txt' ||
    ext === 'text' ||
    ext === 'html' ||
    ext === 'htm' ||
    ext === 'csv' ||
    ext === 'json' ||
    ext === 'yaml' ||
    ext === 'yml' ||
    ext === 'xml' ||
    ext === 'env' ||
    ext === 'log' ||
    ext === 'sql' ||
    ext === 'ini' ||
    ext === 'toml' ||
    ext === 'htaccess' ||
    ext === 'sh' ||
    ext === 'bash'
  ) {
    extracted = new TextDecoder().decode(raw);
  }
  return { extracted, ext };
}

export async function _sha256HexOfBytes(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

// #968 复核（CodeRabbit #969）：附件内容指纹——全量 SHA-256（采样首尾会被
// 「同名同首尾、仅中段不同」的附件构造性绕过）。渲染线程没有同步摘要，故：
// 发送前由 handleSend 在 await 段调用本函数预计算，结果暂存到附件
// contentFp 并写进装饰 (fp:…)（doc 占位/图片）；守卫侧只比对暂存值（load()
// 合并是同步路径，不能做摘要）。atob 解码失败会抛错，由调用方捕获（fp 缺失
// → 装饰无指纹 → 守卫不认领，方向安全）。图片 dataUrl 不是纯 base64
// （data:image/…;base64, 前缀），走 _sha256HexOfText 直接哈希整个字符串。
export async function _sha256HexOfBase64(dataBase64: string): Promise<string> {
  // new Uint8Array(…) 拷贝定型为 Uint8Array<ArrayBuffer>（TS 5.7 泛型数组：
  // Uint8Array.from 返回 ArrayBufferLike，不满足 BufferSource）
  const raw = new Uint8Array(Uint8Array.from(atob(dataBase64), (c) => c.charCodeAt(0)));
  return _sha256HexOfBytes(raw);
}

export async function _sha256HexOfText(text: string): Promise<string> {
  return _sha256HexOfBytes(new TextEncoder().encode(text));
}

// #968 复核：附件装饰内容守卫。key 会把装饰段（含嵌入的文件内容）整体剥掉，
// 「同文本 + 同附件名」的消息 key 必然碰撞，名字级校验不足以区分内容差异
// （CodeRabbit #969：同文本 + main.py 但 print(1)/print(2) 两种内容时，旧副本
// 会误认领新气泡 → 新消息被吞）。守卫采用「签名计数」语义：每条 live 附件
// 换算成一条唯一装饰签名（相同签名 = 同名字同内容/同指纹的重复附件），持久化
// 副本里每种签名的出现次数必须 ≥ live 条数——一条装饰只认领一个附件，杜绝
// 重复附件共用同一条旧装饰（CodeRabbit #969 round 2：30s 内先发 1 张图再发
// 同文本 2 张相同图时，1 条装饰的旧副本会误认领 2 附件气泡 → 吞真实消息）。
// - text：payload 原样嵌入 att.content → 签名 = 完整 `[File: name]\n```\n
//   ${content}\n```` 段（逐字，含围栏锚点，长度/重叠不误判）
// - document：`--- Document: name ---` 块正文须与 att.dataBase64 重新解码结果
//   （同一 _decodeDocData，50k 截断一致）逐字相等，签名 = 完整块；占位装饰
//   （扫描/二进制/解析失败）无法构造完整文本 → 按 [name: 前缀定位、逐段数
//   (fp:contentFp) 出现次数
// - image：签名 = `[Image: name (fp:hex)]` 完整装饰（字节不走 content，内容
//   以发送侧预计算的全量 SHA-256 指纹代偿）
// 任一签名次数不足 / 无法换算（无指纹、空内容、解码失败）→ 一律不认领
// （方向安全：可能双显示，绝不吞消息）。
function _countOccurrences(haystack: string, needle: string): number {
  let n = 0;
  let from = 0;
  for (;;) {
    const i = haystack.indexOf(needle, from);
    if (i < 0) return n;
    n += 1;
    from = i + needle.length;
  }
}

// 数 [name: 前缀占位中出现指定 fp 的段数。收尾 ] 从段头+长度起找（文件名可含
// ]，report].pdf 不会在文件名内部截断，CodeRabbit #969 Minor）；畸形段（无
// 收尾/超长）跳过。
function _countPlaceholderFp(pmContent: string, name: string, fp: string): number {
  const ph = `[${name}: `;
  let n = 0;
  let searchFrom = 0;
  for (;;) {
    const phIdx = pmContent.indexOf(ph, searchFrom);
    if (phIdx < 0) return n;
    const closeIdx = pmContent.indexOf(']', phIdx + ph.length);
    if (closeIdx >= 0 && closeIdx - phIdx <= 400) {
      const seg = pmContent.slice(phIdx, closeIdx + 1);
      if (seg.includes(`(fp:${fp})`)) n += 1;
    }
    searchFrom = phIdx + ph.length;
  }
}

function _persistedCoversAttachments(
  pmContent: string,
  attachments: Attachment[] | undefined
): boolean {
  if (!attachments || attachments.length === 0) return true;
  const need = new Map<string, number>();
  const fpNeed = new Map<string, number>(); // `name|fp` → 需要条数（占位装饰）
  for (const a of attachments) {
    switch (a.type) {
      case 'image': {
        // 无指纹旧版 live 附件无法验证内容 → 不认领（方向安全）
        if (!a.contentFp) return false;
        const sig = `[Image: ${a.name} (fp:${a.contentFp})]`;
        need.set(sig, (need.get(sig) ?? 0) + 1);
        break;
      }
      case 'text': {
        const c = a.content ?? '';
        if (!c) return false;
        const sig = `[File: ${a.name}]\n\`\`\`\n${c}\n\`\`\``;
        need.set(sig, (need.get(sig) ?? 0) + 1);
        break;
      }
      case 'document': {
        const blockOpen = `--- Document: ${a.name} ---`;
        if (pmContent.includes(blockOpen)) {
          // Document 块嵌内容 → 内容必须逐字一致才认领（CodeRabbit #969）
          if (!a.dataBase64) return false;
          try {
            const { extracted } = _decodeDocData(a.dataBase64, a.name);
            const body = extracted && extracted.trim() ? extracted.slice(0, 50000) : '';
            if (!body) return false; // 空提取发送侧会走占位分支，不应出现块
            const sig = `${blockOpen}\n${body}\n--- End of ${a.name} ---`;
            need.set(sig, (need.get(sig) ?? 0) + 1);
          } catch {
            return false; // 解码异常 → 发送侧走占位分支，不可能有 Document 块
          }
        } else {
          // 占位装饰：同名不同字节的不可提取文档生成相同占位 + 各自 (fp:…)，
          // 按 name+fp 分组数出现条数（同名字同 fp 的重复附件不得共用一条）。
          // key 分隔符用 \x1f（任何 OS 文件名都不合法），避免文件名含 | 解析错位。
          if (!a.contentFp) return false;
          const key = `${a.name}\x1f${a.contentFp}`;
          fpNeed.set(key, (fpNeed.get(key) ?? 0) + 1);
        }
        break;
      }
      default:
        // 未知/未来扩展类型（audio/video/archive/…）无法验证内容 → 不认领。
        // 与全守卫「宁可双显、绝不吞消息」的安全方向一致：若扩展 attachment type
        // 而漏补分支，仅凭 key（文本+时间+文件名）认领可能吞掉真实新消息。
        return false;
    }
  }
  for (const [sig, n] of need) {
    if (_countOccurrences(pmContent, sig) < n) return false;
  }
  for (const [key, n] of fpNeed) {
    const sep = key.indexOf('\x1f');
    const name = key.slice(0, sep);
    const fp = key.slice(sep + 1);
    if (_countPlaceholderFp(pmContent, name, fp) < n) return false;
  }
  return true;
}

// #891 深度审阅 #11：删 flag 门控与保留块须用同一匹配（两处不再手写漂移）。
// 唯一匹配改为一对一：merged 里每条持久化用户行只认领最早一条同 key、时间相近
// 的乐观气泡。此前 .some() 会让同一条持久化副本同时满足多条相同文本的气泡——
// 用户 30s 内两次发送同一句、恢复快照时第二条尚未落盘，两条都会被误判为已持久
// 化而漏掉第二条。返回数组与 frontend 等长：matched[i]===true 表示该条乐观气泡
// 已有专属持久化副本。匹配条件（按代价排序）：①时间相近 O(1) ②归一化 key（#968，
// key 惰性缓存、每行只算一次——load() 在 UI 线程跑，避免每对候选做全文正则）
// ③附件装饰名守卫（#968 复核：图片/文本/文档三类都查，见 _persistedCoversAttachments）。内容比对经 _userContentDedupKey 归一化（#968）。
export function _markUserTwinMatches(frontend: Message[], merged: Message[]): boolean[] {
  const matched = new Array<boolean>(frontend.length).fill(false);
  const keyCache = new Array<string | undefined>(frontend.length).fill(undefined);
  for (const pm of merged) {
    if (pm.role !== 'user') continue;
    const pmContent = String(pm.content ?? '');
    const pmKey = _userContentDedupKey(pmContent);
    for (let i = 0; i < frontend.length; i += 1) {
      if (matched[i]) continue;
      const m = frontend[i];
      if (m.role !== 'user') continue;
      // 时间门控最先（O(1)）——内容剥离是 O(content)，只对时间相近的候选执行
      if (!_isPersistedCopyOf(m.timestamp, pm.timestamp)) continue;
      if (keyCache[i] === undefined) keyCache[i] = _userContentDedupKey(String(m.content ?? ''));
      if (keyCache[i] !== pmKey) continue;
      if (!_persistedCoversAttachments(pmContent, m.attachments)) continue;
      matched[i] = true;
      break;
    }
  }
  return matched;
}

export function sessionMsgsToUi(rawMsgs: any[]): Message[] {
  const result: Message[] = [];
  for (const m of collapseAssistantMessagesWithinTurns(rawMsgs)) {
    const ts = m.timestamp ? new Date(m.timestamp).getTime() : Date.now();

    if (m.role === 'progress') {
      result.push({
        role: 'progress',
        content: String(m.content ?? ''),
        reasoning: m.reasoning ? String(m.reasoning) : undefined,
        reasoningElapsedS: m.reasoningElapsedS,
        reasoningMode: m.reasoningMode, // #905 review: preserve per-message mode
        timestamp: ts,
      });
      continue;
    }

    if (m.role === 'user' || m.role === 'assistant') {
      // Skip assistant messages that have no text content (only tool_calls).
      // Reasoning-only assistant turns (thinking models that emit no reply
      // text) still render a folded thinking block, so admit them too. #539.
      // Note: the old per-tool-call hint row is gone — restored tool results
      // (role 'tool', below) already carry the full "执行命令 · cp …" label,
      // so emitting both made every tool appear twice (#539 用户要求).
      const reasoningContent =
        typeof m.reasoning_content === 'string' && m.reasoning_content.trim().length > 0
          ? m.reasoning_content
          : undefined;
      const hasContent = m.content && String(m.content).trim().length > 0;
      if (m.role === 'user' || hasContent || reasoningContent) {
        const contentStr = messageContentToString(m.content);
        // Restore image attachments from the "[Image: name]" placeholder the
        // sender embeds (dataUrl is not persisted — it is re-read from the
        // session files dir lazily after load, see loadSession #659).
        const attachments =
          m.role === 'user' ? extractImageAttachmentsFromContent(contentStr) : undefined;
        result.push({
          role: m.role as 'user' | 'assistant',
          content: contentStr,
          reasoning: reasoningContent,
          // #905 review / CodeRabbit: preserve the message's own mode even
          // when it has no reasoning_content — the inline 🚀/🧠 badge on the
          // reply must follow the SENT mode, not the live global one
          // (switching the selector then reopening history showed the wrong
          // badge). Raw persisted field is snake_case.
          reasoningMode: m.reasoning_mode,
          timestamp: ts,
          attachments,
        });
      }
    } else if (m.role === 'subagent') {
      // Subagent result messages — render with the subagent style
      result.push({
        role: 'subagent',
        content: messageContentToString(m.content),
        timestamp: ts,
      });
    } else if (m.role === 'tool') {
      // Tool result messages → show as collapsed progress with toolHint
      const toolName = m.name || 'tool';
      const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
      const toolArgs = (m as { arguments?: unknown }).arguments;

      // Detect paper_search results → render as cards (not collapsed)
      if (toolName === 'paper_search') {
        const paperData = tryParsePaperSearchResult(content);
        if (paperData && paperData.items?.length) {
          result.push({
            role: 'progress',
            content: content,
            summary: `📄 Found ${paperData.items.length} papers${paperData.query ? ` for "${paperData.query}"` : ''}`,
            toolHint: true,
            toolName: 'paper_search',
            toolData: paperData,
            collapsed: false,
            // CodeRabbit（9-11）：链卡按 turn 归属需要行级 turnId（恢复路径）
            turnId: String((m as { turn_id?: unknown }).turn_id ?? '') || undefined,
            timestamp: ts,
          });
        } else {
          // Search returned empty or errored — still show normally
          const preview = content.length > 120 ? content.slice(0, 120) + '…' : content;
          result.push({
            role: 'progress',
            content: `paper_search: ${preview}`,
            summary: 'paper_search',
            toolHint: true,
            collapsed: true,
            turnId: String((m as { turn_id?: unknown }).turn_id ?? '') || undefined,
            timestamp: ts,
          });
        }
      } else {
        // Restored tool result: keep the full output for inspection, but the
        // collapsed row must read like the live chain ("执行命令 · cp …"),
        // never parse the OUTPUT text as activity lines (#539 恢复视图).
        const detail = toolCallDetail(toolArgs);
        result.push({
          role: 'progress',
          content: content,
          summary: `${toolDisplayName(toolName)}${detail ? ` · ${detail}` : ''}`,
          toolHint: true,
          toolArgs,
          toolName,
          toolOutput: true,
          collapsed: true,
          turnId: String((m as { turn_id?: unknown }).turn_id ?? '') || undefined,
          timestamp: ts,
        });
      }
    }
    // Ignore other roles (system, etc.)
  }

  // Merge consecutive collapsed progress messages into a single group
  const merged: Message[] = [];
  for (const msg of result) {
    // Restored tool-output rows must stay individual chain steps (each has its
    // own step number + command detail) — never merge them into one blob.
    const merges = !msg.toolOutput;
    if (
      merges &&
      msg.collapsed &&
      merged.length > 0 &&
      !merged[merged.length - 1].toolOutput &&
      merged[merged.length - 1].collapsed
    ) {
      const prev = merged[merged.length - 1];
      // Append content and summary
      prev.content += '\n' + msg.content;
      prev.summary = prev.summary!.includes(',')
        ? prev.summary // already a group, keep it
        : `${prev.summary}, ${msg.summary}`; // merge two single items
      // Use the later timestamp
      prev.timestamp = msg.timestamp;
      // A group containing raw tool output must keep the terminal-style
      // expandable rendering (#539 恢复视图).
      if (msg.toolOutput) prev.toolOutput = true;
      // Keep every tool call's arguments in the group — "查看来源" needs the
      // exact URL each web_fetch/web_search actually touched, not just the first.
      const prevArgs = Array.isArray(prev.toolArgs)
        ? prev.toolArgs
        : prev.toolArgs !== undefined
          ? [prev.toolArgs]
          : [];
      if (msg.toolArgs !== undefined) prevArgs.push(msg.toolArgs);
      if (prevArgs.length > 0) prev.toolArgs = prevArgs;
    } else {
      merged.push({ ...msg });
    }
  }

  // When a group has multiple items, rewrite summary to show a Chinese count
  // (live rows carry details like "执行命令 · cp …", so keep it short).
  for (const msg of merged) {
    if (msg.collapsed && msg.summary && msg.summary.includes(',')) {
      const names = msg.summary.split(', ').filter(Boolean);
      const unique = [...new Set(names)];
      if (unique.length > 1) {
        const first = unique[0].split(' · ')[0] || unique[0];
        msg.summary = `${unique.length} 项工具调用 · ${first} 等`;
      }
    }
  }

  // Restored thinking blocks have no elapsed time — derive it from the turn
  // span (first reasoning record → last message of the turn) so the header
  // always reads "已深度思考 · X 秒" (#539 用户要求).
  const withElapsed = dedupeReasoningBlocks(merged);
  for (let i = 0; i < withElapsed.length; i += 1) {
    const m = withElapsed[i];
    if (m.role !== 'progress' || !m.reasoning || m.reasoningElapsedS !== undefined) continue;
    let endTs = m.timestamp;
    for (let j = i + 1; j < withElapsed.length; j += 1) {
      if (withElapsed[j].role === 'user') break;
      if (withElapsed[j].timestamp > endTs) endTs = withElapsed[j].timestamp;
    }
    const secs = Math.round((endTs - m.timestamp) / 1000);
    if (secs >= 1) m.reasoningElapsedS = secs;
  }
  return withElapsed;
}

function removeTransientTurnMessagesSinceLastUser(messages: Message[]): Message[] {
  const lastUserIndex = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i].role === 'user') return i;
    }
    return -1;
  })();

  const cleaned = messages.reduce((acc, message, index) => {
    if (index <= lastUserIndex) {
      acc.push(message);
      return acc;
    }
    if (message.role === 'assistant') return acc;
    if (message.role !== 'progress') {
      acc.push(message);
      return acc;
    }
    // Thinking blocks stay in place; tool rows collapse after the final.
    if (message.reasoning) {
      acc.push(message);
      return acc;
    }
    if (message.toolHint) acc.push(message);
    return acc;
  }, [] as Message[]);

  return dedupeReasoningBlocks(cleaned);
}

type ChatGroup =
  | { kind: 'msg'; msg: Message }
  | { kind: 'chain'; rows: Message[]; done: boolean }
  | { kind: 'reply-head'; thinking: Message }
  | { kind: 'reply-content'; msg: Message };

/** Group consecutive tool rows into a single chain so the final rendering can
 *  collapse them into one「工具调用 · N」block (live rows stay expanded while
 *  the turn runs; the group is marked done once a non-tool message follows). */
function groupChatMessages(messages: Message[]): ChatGroup[] {
  const out: ChatGroup[] = [];
  let chain: Message[] | null = null;
  let chainDone = false;
  // A progress+reasoning message starts a reply header (avatar + name +
  // "已深度思考") that stays at the TOP of the turn, above any tool rows —
  // the reply's body is emitted later as reply-content, after the tools.
  let pendingReply = false;
  const flush = () => {
    if (chain) {
      out.push({ kind: 'chain', rows: chain, done: chainDone });
      chain = null;
      chainDone = false;
    }
  };
  for (const m of messages) {
    const isToolRow = m.role === 'progress' && !!m.toolHint;
    if (isToolRow) {
      if (!chain) chain = [];
      chain.push(m);
      continue;
    }
    if (chain) chainDone = true;
    flush();
    if (m.role === 'progress' && m.reasoning) {
      out.push({ kind: 'reply-head', thinking: m });
      pendingReply = true;
      continue;
    }
    if (m.role === 'assistant' && pendingReply) {
      out.push({ kind: 'reply-content', msg: m });
      pendingReply = false;
      continue;
    }
    if (m.role === 'user') pendingReply = false;
    out.push({ kind: 'msg', msg: m });
  }
  flush();
  return out;
}

/** Merge adjacent thinking blocks so a turn can never show duplicate headers. */
function dedupeReasoningBlocks(messages: Message[]): Message[] {
  const out: Message[] = [];
  let pending: Message | null = null;
  for (const m of messages) {
    if (m.role === 'progress' && m.reasoning) {
      if (pending) {
        pending.content = `${pending.content}\n${m.content}`;
        pending.reasoning = pending.content;
        pending.reasoningElapsedS = m.reasoningElapsedS ?? pending.reasoningElapsedS;
        pending.timestamp = m.timestamp;
        pending.isLiveReasoning = pending.isLiveReasoning || m.isLiveReasoning;
        continue;
      }
      pending = { ...m };
      out.push(pending);
      continue;
    }
    // Agentic turns think between tool calls, so only a user boundary (or the
    // final assistant message) ends the merge chain — tool rows (progress with
    // toolHint) and subagent/error rows stay inside the same turn's thinking.
    if (m.role === 'user' || m.role === 'assistant') pending = null;
    out.push(m);
  }
  return out;
}

/** Drop snapshot rows already represented in `merged`.  The backend persists
 *  reasoning on assistant messages, so sessionMsgsToUi re-creates a completed
 *  turn's thinking block — splicing the snapshot's copy back in would add one
 *  more "已深度思考" header on every window switch-back.  Live/in-flight
 *  thinking (not yet persisted) has no counterpart in `merged` and is kept. */
function dedupeSnapshotRows(merged: Message[], rows: Message[]): Message[] {
  return rows.filter((row) => {
    if (row.role === 'progress' && row.reasoning) {
      return !merged.some(
        (m) =>
          m.role === 'progress' &&
          m.reasoning &&
          (m.content.startsWith(row.content) || row.content.startsWith(m.content))
      );
    }
    return !merged.some((m) => m.role === row.role && m.content === row.content);
  });
}

/** Promote an existing thinking block, or insert one after the user message.
 *  Updating in place guarantees a turn never renders two thinking headers. */
export function insertStandaloneReasoning(
  messages: Message[],
  reasoning: string,
  elapsedSeconds?: number
): Message[] {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') break;
    if (messages[i].role === 'progress' && messages[i].reasoning) {
      const next = [...messages];
      next[i] = {
        ...next[i],
        isLiveReasoning: false,
        content: reasoning,
        reasoning,
        reasoningElapsedS: elapsedSeconds,
      };
      return next;
    }
  }
  const block: Message = {
    role: 'progress',
    content: reasoning,
    reasoning,
    reasoningElapsedS: elapsedSeconds,
    timestamp: Date.now(),
  };
  let insertAt = messages.length;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === 'user') {
      insertAt = i + 1;
      break;
    }
  }
  return [...messages.slice(0, insertAt), block, ...messages.slice(insertAt)];
}

/** Append a streaming reasoning chunk to the last live thinking bubble. */
export function appendReasoningDelta(
  messages: Message[],
  delta: string,
  ts = Date.now(),
  mode?: ReasoningMode
): Message[] {
  let idx = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].isLiveReasoning) {
      idx = i;
      break;
    }
  }
  if (idx >= 0) {
    const next = [...messages];
    const appended = next[idx].content + delta;
    next[idx] = {
      ...next[idx],
      content: appended,
      reasoning: appended,
      reasoningMode: next[idx].reasoningMode ?? mode,
    };
    return next;
  }
  return [
    ...messages,
    {
      role: 'progress',
      content: delta,
      reasoning: delta,
      reasoningMode: mode,
      isLiveReasoning: true,
      timestamp: ts,
    },
  ];
}

/** File-operation tool names shared between progress-hint parsing and
 *  onFinal tool_call tracking. Keep in sync with the backends that
 *  produce file paths. */
const _FILE_WRITE_TOOLS = [
  'write_file',
  'edit_file',
  'delete_file',
  'apply_patch',
  'create_docx',
  'create_xlsx',
  'create_pptx',
  'create_pdf',
  'pdf_write',
  'docx_write',
  'xlsx_write',
  'pptx_write',
  'edit_docx',
  'append_xlsx',
  'skill_manage',
  'paper_download',
  'exec',
];
const _FILE_READ_TOOLS = ['read_file', 'pdf_read'];

/** Extract a file path from a JSON-stringified tool args object.
 *  Checks common keys: path, file_path, filename, outPath.
 *  For skill_manage, derives the SKILL.md path from the skill name.
 *  For exec, parses the command string for curl -o/-O, wget -O, or > redirect. */
function _extractPathFromArgs(argsStr: string): string | null {
  try {
    const args = JSON.parse(argsStr);

    // skill_manage: derive from name
    if (args.name && (args.action === 'create' || args.action === 'patch')) {
      return `skills/${args.name}/SKILL.md`;
    }

    // Direct path parameters
    const directPath =
      (args.path as string) ||
      (args.file_path as string) ||
      (args.filename as string) ||
      (args.outPath as string) ||
      (args.out_path as string) ||
      (args.output as string);
    if (directPath) return directPath;

    // exec: parse command string for output filenames
    const cmd: string = (args.command as string) || '';
    if (cmd) {
      // Match: -o <file>  (curl/wget explicit output path)
      let m1 = cmd.match(/(?:^|\s)-o\s+(\S+\.\w+)/);
      if (m1) return m1[1].replace(/^["']|["']$/g, '');
      // Match: --output <file>
      m1 = cmd.match(/--output\s+(\S+\.\w+)/);
      if (m1) return m1[1].replace(/^["']|["']$/g, '');
      // Match: -O  (boolean flag — derive filename from last URL basename)
      // Must match O at argument boundary: -O, -LO, -fsSLO, etc.
      if (/(?:^|\s)[a-zA-Z]*O(?:\s+|$)/.test(cmd)) {
        const urls = cmd
          .split(/\s+/)
          .filter((t) => t.startsWith('http://') || t.startsWith('https://'));
        if (urls.length) {
          const name = urls[urls.length - 1].split('/').pop() || '';
          if (name) return name;
        }
      }
      // Match: > <file>  or  >><file>  (shell redirect)
      const m2 = cmd.match(/(?:^|\s)>{1,2}\s*(\S+\.\w+)/);
      if (m2) return m2[1].replace(/^["']|["']$/g, '');
    }

    return null;
  } catch {
    return null;
  }
}

/** Parse tracked files from raw session messages.
 *  Handles three formats:
 *  1. _tool_hint metadata (from progress events, persisted by some backends)
 *  2. tool_calls array on assistant messages (raw provider format)
 *  3. name field on tool result messages (raw provider format)
 */
export function extractTrackedFilesFromMessages(rawMsgs: any[]): TrackedFile[] {
  const fileMap = new Map<string, TrackedFile>();
  const rank: Record<TrackedFile['op'], number> = { read: 0, edit: 1, write: 2, delete: 3 };
  let turnSeq = -1; // 当前回合序号（第几个 user 回合，从 0 起）

  const upsert = (
    path: string,
    op: TrackedFile['op'],
    timestamp?: string,
    tool?: string,
    turnId?: number
  ) => {
    const key = normalizeSandboxPath(path).replace(/\\/g, '/');
    const existing = fileMap.get(key);
    // `>=`（而非 `>`）：write_file/edit_file 都映射成 op='write'，同一路径的
    // 后一次等 rank 操作此前被忽略，导致 sourceTool/turnId 停留在更早消息上、
    // 文件卡片用旧 turnId 取错引用（#879 ③ CodeRabbit）。等 rank 时刷新，
    // 同时保留新事件未提供的元数据（如 _tool_hint 无 tool 名）。
    if (!existing || rank[op] >= rank[existing.op]) {
      fileMap.set(key, {
        path: key,
        name: basename(key),
        op,
        lastSeen: timestamp ? new Date(timestamp).getTime() : Date.now(),
        truncated: false,
        sourceTool: tool ?? existing?.sourceTool,
        turnId: turnId ?? existing?.turnId,
      });
    }
  };

  for (const msg of rawMsgs) {
    if (msg?.role === 'user') turnSeq += 1;
    // Format 1: _tool_hint metadata (persisted progress events)
    const hintText = msg._tool_hint_text || msg.content;
    if (msg._tool_hint && hintText) {
      const parsed = parseToolHint(hintText);
      if (parsed) {
        upsert(parsed.path, parsed.op, msg.timestamp, undefined, turnSeq);
      }
    }

    // Format 2: assistant messages with tool_calls array
    if (Array.isArray(msg.tool_calls)) {
      for (const tc of msg.tool_calls) {
        const fn = tc?.function || tc?.tool?.function || {};
        const toolName: string = fn?.name || '';
        if (!toolName) continue;
        const argsStr: string = fn?.arguments || '{}';
        const filePath = _extractPathFromArgs(argsStr);
        if (!filePath) continue;
        if (_FILE_WRITE_TOOLS.includes(toolName)) {
          upsert(
            filePath,
            toolName === 'delete_file' ? 'delete' : 'write',
            msg.timestamp,
            toolName,
            turnSeq
          );
        } else if (_FILE_READ_TOOLS.includes(toolName)) {
          upsert(filePath, 'read', msg.timestamp, toolName, turnSeq);
        }
      }
    }

    // Format 3: tool result messages with name field
    if (msg.role === 'tool' && msg.name) {
      const toolName: string = msg.name;
      // Try to extract path from content (often contains the file path)
      const contentPath = parseToolHint(String(msg.content || ''));
      if (contentPath) {
        upsert(contentPath.path, contentPath.op, msg.timestamp, toolName, turnSeq);
      } else if (_FILE_WRITE_TOOLS.includes(toolName)) {
        // Tool result without parsable content — try to infer from tool name
        // (best-effort; actual path is in the paired assistant tool_calls message)
      }
    }
  }
  return Array.from(fileMap.values());
}

/** 从消息推导「回合序号 → 该回合的结构化来源」（#879 ③ 冷启动恢复）。
 *  web_sources 实时通过事件下发、不持久化，冷启动后从 web_search / web_fetch
 *  的结果文本重新解析，按回合（user 消息分隔）累积，供文件卡片显示相关引用。 */
export function extractTurnSourcesFromMessages(rawMsgs: any[]): Map<number, MessageSource[]> {
  const map = new Map<number, MessageSource[]>();
  let turnSeq = -1;
  for (const msg of rawMsgs) {
    if (msg?.role === 'user') turnSeq += 1;
    if (msg?.role !== 'tool' || !msg?.name) continue;
    const content = String(msg.content ?? '');
    if (msg.name === 'web_search') {
      const items = parseWebSearchResults(content);
      if (items.length === 0) continue;
      const acc = map.get(turnSeq) ?? [];
      const seen = new Set(acc.map((s) => s.url));
      for (const it of items) {
        if (it.url && !seen.has(it.url)) {
          seen.add(it.url);
          acc.push({ tool: 'web_search', url: it.url, title: it.title, snippet: it.snippet });
        }
      }
      map.set(turnSeq, acc);
    } else if (msg.name === 'web_fetch') {
      try {
        const payload = JSON.parse(content);
        const url = (payload?.finalUrl as string) || (payload?.url as string);
        if (url) {
          const acc = map.get(turnSeq) ?? [];
          const seen = new Set(acc.map((s) => s.url));
          if (!seen.has(url)) {
            seen.add(url);
            acc.push({
              tool: 'web_fetch',
              url,
              title: (payload?.title as string) || url,
              snippet: '',
            });
          }
          map.set(turnSeq, acc);
        }
      } catch {
        /* not JSON, ignore */
      }
    }
  }
  return map;
}

// ── Cross-session in-flight event cache (#378) ──────────────────
// When the user switches sessions mid-stream, the per-send listeners
// silently bail (data.session_key !== currentSessionRef.current).
// This cache captures those events so the session-load effect can
// replay them when the user switches back, avoiding the permanent
// loss of the assistant reply.
//
// Kept module-level so the caches survive a ChatConsole unmount — App.tsx no
// longer keys the component by sessionKey, but route/layout changes can still
// unmount it, and component-scoped refs would drop the events of a dead
// instance.  Both caches are bounded to a fixed number of sessions so a
// long-lived desktop process visiting many sessions does not accumulate
// unbounded event/message payloads.
interface InFlightEvent {
  type: 'progress' | 'final' | 'error' | 'aborted';
  data: unknown;
  timestamp: number;
}
interface InFlightSnapshot {
  events: InFlightEvent[];
  userMsgTimestamp: number;
}
/** Map that drops the oldest key once it exceeds `maxSize` entries. */
function boundedMap<K, V>(maxSize: number): Map<K, V> {
  const map = new Map<K, V>();
  const originalSet = map.set.bind(map);
  map.set = ((key: K, value: V) => {
    // Call the bound original set, NOT map.set (which is now this wrapper) —
    // otherwise every call recurses into itself until the stack overflows.
    originalSet(key, value);
    if (map.size > maxSize) {
      const oldest = map.keys().next().value;
      if (oldest !== undefined) map.delete(oldest);
    }
    return map;
  }) as typeof originalSet;
  return map;
}
const MODULE_CACHE_MAX_SESSIONS = 20;
const moduleInFlightCache = boundedMap<string, InFlightSnapshot>(MODULE_CACHE_MAX_SESSIONS);

// Per-session snapshot of the last-rendered messages.  While on a session,
// its thinking/reply events take the LIVE path (rendered into `messages`)
// and never enter moduleInFlightCache — so switching away and wiping the
// component state would lose them.  On switch we snapshot the current
// session's messages here so switching back restores them instantly
// (module-level, survives the component staying mounted across switches).
const moduleMessagesSnapshot = boundedMap<string, Message[]>(MODULE_CACHE_MAX_SESSIONS);

// Typewriter reveal state per session.  `revealNext` runs in the handleSend
// closure, whose local vars (fullContent/displayed/animId) would die with the
// closure's RAF chain if we stopped it on switch-away.  Holding the state at
// module level lets the animation pause across a switch (by skipping
// setMessages) and RESUME when the user returns — the reply's remaining text
// keeps revealing instead of freezing mid-typewriter.
interface RevealState {
  fullContent: string;
  displayed: string;
  animId: number | null;
  finalDone: boolean;
  /** Last rAF tick timestamp (performance.now) — persists across switch-away
   *  so a resumed typewriter catches up to the full content immediately. */
  lastTickTs: number | null;
}
const revealBySession = boundedMap<string, RevealState>(MODULE_CACHE_MAX_SESSIONS);

// Sessions whose final reply has already been rendered by load() (merged from
// history / cached final).  When such a session's old send listener then
// receives the same final via the live path, it must NOT append a duplicate —
// the reply is already on screen.  Cleared when a new send starts.
const finalHandledSessions = new Set<string>();

// Whether each session currently has a turn in flight (streaming).  Set true
// in handleSend, cleared on final/error/aborted.  The switch-back effect uses
// this as the authoritative "is this session still generating?" signal — the
// heuristic alternatives (cached progress events / snapshot thinking text /
// active typewriter) all miss the early-thinking phase, where the only
// evidence is the indicator itself (snapshot holds just the user bubble).
const streamingBySession = new Set<string>();

/** Convert cached in-flight events into UI messages for immediate display.
 *  Pure — no side effects.  Used to render the thinking/reply synchronously
 *  on session switch so there is no blank-window gap while sessions.get()
 *  resolves.  Exec inline output and doc_progress attachment status are
 *  handled by the load() replay (they update execOutputs/attachments). */
/**
 * 平台积分计费事件 → 消息行。billed 为安静活动行、blocked 为醒目错误行。
 * 供实时处理器与缓存回放（cachedEventsToMessages / splitCachedMessages）
 * 共用，保证切会话后计费通知不丢。
 */
function pointsEventToMessage(pd: ChatProgress): Message | null {
  if (pd.stream !== 'points' || typeof pd.type !== 'string') return null;
  const pointsCost = typeof pd.points_cost === 'number' ? pd.points_cost : 0;
  const pointsBalance = typeof pd.balance === 'number' ? pd.balance : null;
  if (pd.type === 'billed') {
    return {
      role: 'progress',
      content:
        pointsBalance === null
          ? `本次任务已扣 ${pointsCost} 积分`
          : `本次任务已扣 ${pointsCost} 积分，可用余额 ${pointsBalance}`,
      timestamp: Date.now(),
    };
  }
  return {
    role: 'error',
    content:
      typeof pd.message === 'string' && pd.message
        ? pd.message
        : '平台积分不足，任务未执行。请到 设置 → MiQroForge 平台 查看余额。',
    timestamp: Date.now(),
  };
}

function cachedEventsToMessages(events: InFlightEvent[], mode?: ReasoningMode): Message[] {
  const out: Message[] = [];
  for (const ev of events) {
    if (ev.type === 'progress') {
      const pd = ev.data as ChatProgress;
      const pointsMessage = pointsEventToMessage(pd);
      if (pointsMessage) {
        out.push(pointsMessage);
        continue;
      }
      if (pd?.text && !pd?.stream) {
        out.push({
          role: 'progress',
          content: pd.text,
          toolHint: pd?.tool_hint === true,
          toolCallId: pd?.tool_call_id,
          collapsed: pd?.tool_hint === true,
          timestamp: Date.now(),
        });
      }
    } else if (ev.type === 'final') {
      const fd = ev.data as ChatFinal;
      if (fd?.content) {
        out.push({
          role: 'assistant',
          content: fd.content,
          timestamp: Date.now(),
          reasoningMode: mode,
          // 2026-08-27：turn 关联——卡内联进 AI 回答
          turnId: (fd as ChatFinal & { turn_id?: string }).turn_id ?? undefined,
        });
      }
    } else if (ev.type === 'error') {
      const ed = ev.data as any;
      out.push({ role: 'error', content: ed?.message || 'Unknown error', timestamp: Date.now() });
    } else if (ev.type === 'aborted') {
      out.push({ role: 'progress', content: '已停止。', timestamp: Date.now() });
    }
  }
  return out;
}

/** Split cached events into thinking (progress/error/subagent) vs the final
 *  reply.  Used by load() to merge with history in the correct visual order
 *  (thinking ABOVE the reply). */
function splitCachedMessages(events: InFlightEvent[]): {
  thinking: Message[];
  finalReply: string | null;
  /** #834: server-measured thinking proxy, preserved across the off-session
   *  final cache so the restored thinking block doesn't fall back to the
   *  local delta-span approximation. */
  finalReasoning?: string;
  finalReasoningElapsedS?: number;
} {
  const thinking: Message[] = [];
  let finalReply: string | null = null;
  let finalReasoning: string | undefined;
  let finalReasoningElapsedS: number | undefined;
  for (const ev of events) {
    if (ev.type === 'progress') {
      const pd = ev.data as ChatProgress;
      const pointsMessage = pointsEventToMessage(pd);
      if (pointsMessage) {
        thinking.push(pointsMessage);
        continue;
      }
      if (pd?.text && !pd?.stream) {
        thinking.push({
          role: 'progress',
          content: pd.text,
          toolHint: pd?.tool_hint === true,
          toolCallId: pd?.tool_call_id,
          collapsed: pd?.tool_hint === true,
          timestamp: Date.now(),
        });
      }
    } else if (ev.type === 'error') {
      const ed = ev.data as any;
      thinking.push({
        role: 'error',
        content: ed?.message || 'Unknown error',
        timestamp: Date.now(),
      });
    } else if (ev.type === 'aborted') {
      thinking.push({ role: 'progress', content: '已停止。', timestamp: Date.now() });
    } else if (ev.type === 'final') {
      const fd = ev.data as ChatFinal;
      // CR #856-6: capture reasoning even when content is empty (pure
      // thinking turn) — dropping it loses the thinking block entirely.
      if (fd?.content) finalReply = fd.content;
      if (fd?.reasoning) {
        finalReasoning = fd.reasoning;
        if (fd.reasoning_elapsed_s != null) {
          finalReasoningElapsedS = Math.max(1, Math.round(fd.reasoning_elapsed_s));
        }
      }
    }
  }
  return { thinking, finalReply, finalReasoning, finalReasoningElapsedS };
}

/* ─── Main component ─────────────────────────────────────────────── */

/** #989 标题区右侧三件共用一套 ghost 底规格：28px 高、7px 圆角、11px、无边框，
 *  图标 12–13px。此前三者是三种视觉（橙描边胶囊 / 灰描边分体按钮 / 裸图标）。
 *  「统一」之后还要留住层级——三件并不是同等重要的东西，所以底规格之上再分三档：
 *    标题（第一视觉层） > 工作目录＝当前上下文（常驻浅底） > 分享/面板＝操作（纯 ghost）
 */
const HDR_CTL =
  'shrink-0 inline-flex items-center justify-center h-7 rounded-[7px] text-[11px] font-medium ' +
  'text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-muted)] ' +
  'disabled:opacity-45 disabled:hover:bg-transparent';
/** 纯文本 + 图标形态（分享）。 */
const HDR_CTL_LABEL = `${HDR_CTL} gap-1 px-[9px]`;
/** 纯图标形态（压缩态，以及文件面板按钮）。 */
const HDR_CTL_ICON = `${HDR_CTL} w-7 px-0`;
/** 分享右侧的折叠箭头：贴住分享按钮，所以只有 20px 宽。 */
const HDR_CTL_CARET =
  'shrink-0 inline-flex items-center justify-center h-7 w-5 rounded-[7px] ' +
  'text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-muted)]';
/** 工作目录＝「当前上下文」，不是操作：常驻一层浅底，比纯 ghost 多一档存在感，
 *  但仍压不过标题。压缩态同一个底，只是收成图标。 */
const HDR_CTL_CONTEXT = `${HDR_CTL_LABEL} bg-[var(--surface-muted)] hover:bg-[var(--surface-hover)]`;
const HDR_CTL_CONTEXT_ICON = `${HDR_CTL_ICON} bg-[var(--surface-muted)] hover:bg-[var(--surface-hover)]`;
/** 文件面板开关是操作，但同时是「面板开着吗」的状态位：开着时常驻浅底，
 *  关着时纯 ghost，hover 才浮底。 */
const HDR_CTL_TOGGLE = `${HDR_CTL_ICON} hover:bg-[var(--surface-hover)]`;
const HDR_CTL_TOGGLE_ON = `${HDR_CTL_ICON} bg-[var(--surface-muted)] hover:bg-[var(--surface-hover)]`;

/** Upper bound for waiting on a superseded turn's chat.send to settle after
 *  abort(). Normal aborts resolve in well under a second (the send promise
 *  settles at the abort terminal event); the bound only guards against a
 *  wedged backend stalling interrupt-and-resend indefinitely. */
const TURN_ABORT_SETTLE_MS = 3000;

/** Fallback for aborted events WITHOUT a turn_id (legacy/mock bridges): a
 *  stale aborted event from a superseded turn arriving this soon after a new
 *  send started is dropped. Bridges that emit turn ids use the authoritative
 *  invocation-local turn id match instead — no time window involved (#542).
 *  LIMITATION: under this fallback, a legitimately fast backend abort of the
 *  NEW turn within the window is also dropped, leaving streaming=true until
 *  the 60s watchdog fires. Production bridges all emit turn ids, so this is
 *  degradation protection, not a correctness guarantee. */
const TURN_TERMINAL_GRACE_MS = 500;

// 常驻免责声明文案（#836）—— 法务/产品最终确认后替换；后续接入 i18n 时可迁移
const CHAT_DISCLAIMER_ZH = 'AI 生成内容仅供参考，可能存在错误，请自行核实关键信息';

// issue #962：三张模式卡映射到两档推理模式（日常任务与代码任务都走 think）。反向回写时
// think 一律落到中间那张卡，与旧行为一致（原来中间那张是「深度研究」）。
const WELCOME_MODE_REASONING: Record<WelcomeMode, ReasoningMode> = {
  fast: 'fast',
  daily: 'think',
  code: 'think',
};
const reasoningModeToWelcome = (m: ReasoningMode): WelcomeMode =>
  m === 'think' ? 'daily' : 'fast';
/**
 * 从 reasoningMode 反推选中卡时要防一个歧义：日常任务与代码任务都映射 think，光看
 * reasoningMode 分不出用户想要哪张卡。已经停在「代码任务」时就别把它顶掉——否则在
 * 输入条切一次「深度研究」，代码卡会悄悄变成日常任务，代码模式独有的「内置技能」
 * 入口也跟着消失（#962 CodeRabbit）。
 * 原来那条「切到 fast 却还高亮代码卡」的问题不受影响：m === 'fast' 时照样落到 fast。
 *
 * **只用于「同一会话内切 reasoningMode」这条路径。** 会话边界（sessionKey 变化）不能
 * 用它：那里的语义正好相反——reasoningMode 只有 fast/think 两态，而选中卡有三态，
 * daily ─┐
 *        ├─→ think   // 不可逆，反推不出来
 * code  ─┘
 * 带上 prev 只会把上个会话的 code 泄漏进新的空会话（#962 评审 P1）。
 */
const resolveWelcomeMode = (prev: WelcomeMode, m: ReasoningMode): WelcomeMode =>
  m === 'think' && prev === 'code' ? 'code' : reasoningModeToWelcome(m);

export function ChatConsole({
  sessionKey = DEFAULT_SESSION,
  loadTrigger,
  workspace,
  newSessionTrigger,
  onNewSession,
  onSessionActivityChange,
  pendingWorkspace,
  onChatFinished,
  renameVersion,
  onRename,
  onOpenProviderSettings,
  onOpenQraftSettings,
  onOpenApprovals,
  onWorkspaceLoaded,
  onSessionsChanged,
}: {
  sessionKey?: string;
  /** Increment to force a session history reload (e.g. after bridge becomes ready) */
  loadTrigger?: number;
  /** Current workspace path (shown in the inline selector before conversation starts). */
  workspace?: string | null;
  /** Increment to trigger workspace picker → new session flow */
  newSessionTrigger?: number;
  onNewSession?: (newKey: string, workspace?: string | null) => void;
  onSessionActivityChange?: (hasActivity: boolean) => void;
  pendingWorkspace?: { current: { sessionKey: string; workspace: string } | null };
  onChatFinished?: () => void;
  /** Called after an empty session is garbage-collected on switch-away, so
   *  the parent can refresh the sidebar list. */
  onSessionsChanged?: () => void;
  /** Increment to force a title reload after the session is renamed from
   *  the sidebar, so the active header stays in sync. */
  renameVersion?: number;
  /** Called after a successful header inline rename, so the parent can
   *  refresh the sidebar (which reads titles from the backend). */
  onRename?: () => void;
  onOpenProviderSettings?: () => void;
  /** #1000: 跳转设置 → MiQroForge 平台（首屏登录卡片次级入口）。 */
  onOpenQraftSettings?: () => void;
  onOpenApprovals?: () => void;
  onWorkspaceLoaded?: (workspace: string | null) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  // #1000: 首屏登录卡片与发送拦截共用登录态；旧 preload 无 qraft 命名空间时
  // useQraftStatus 内部兜底为空态（视为未登录）。
  const { loggedIn, status: qraftStatus } = useQraftStatus();
  // 流错误路径同步读取最新登录态：handleSend 闭包可能捕获旧值（CodeRabbit #1010）。
  const loggedInRef = useRef(loggedIn);
  loggedInRef.current = loggedIn;
  // 登录已失效（token 刷新失败且未恢复）：流错误路径据此给重登引导而非模型配置指引。
  const requiresReloginRef = useRef(qraftStatus?.requiresRelogin === true);
  requiresReloginRef.current = qraftStatus?.requiresRelogin === true;
  // #875 D1（外部评估 P0/A1）：系统包安装的 persist/runtime 失败标记只写在
  // 工具输出里，模型可能摘要掉——用户会误以为「允许并记住」已永久生效。
  // 扫描消息中的失败标记并发 window 事件，由 App 级 toast 呈现（不依赖模型）。
  const warnedInstallWarnRef = useRef(new Map<string, number>());
  // 2026-08-27：当前 turn 的 id——final 消息打 turnId 标记（卡内联关联用）
  const activeTurnIdRef = useRef<string | null>(null);
  useEffect(() => {
    const warned = warnedInstallWarnRef.current;
    for (const m of messages) {
      const text = String(m.content ?? '');
      let kind: 'persist' | 'runtime' | null = null;
      if (text.includes('授权保存失败')) kind = 'persist';
      else if (text.includes('未能立即生效')) kind = 'runtime';
      if (kind && warned.get(kind) !== m.timestamp) {
        warned.set(kind, m.timestamp);
        window.dispatchEvent(new CustomEvent(INSTALL_WARNING_EVENT, { detail: kind }));
      }
    }
  }, [messages]);
  // 2026-08-26 用户裁决：有确认/计划卡时输入框隐藏（WorkBuddy 式）
  // 2026-08-27：计划/确认是 AI 回答的一部分——卡片内联在产生它的消息后
  const {
    pending: pendingCards,
    resolved: resolvedCards,
    resolve: resolveCard,
    timeoutCard,
  } = useUserInput();
  const allCards = useMemo(
    () => [...Object.values(resolvedCards), ...Object.values(pendingCards)],
    [pendingCards, resolvedCards]
  );
  // 2026-08-27：卡属于 AI 回答——按 turn_id 关联到消息，在消息内部渲染
  const cardsByTurn = useMemo(() => {
    const map = new Map<string, typeof allCards>();
    for (const c of allCards) {
      const t = c.request.turn_id;
      if (t) {
        const arr = map.get(t) ?? [];
        arr.push(c);
        map.set(t, arr);
      }
    }
    return map;
  }, [allCards]);
  const matchedTurnIds = useMemo(() => {
    // #646-v2：本集合的唯一消费方是 ConfirmCardArea 的「确认卡是否已内联」排除
    // 过滤（plan/action 卡不受影响）。工具链只有在**链里真有 ask_user_confirm_card
    // 行**时才内联画确认卡（ToolChain：`isConfirmRow && card`）——因此只有
    // 「确实有确认卡行」的 turn 才能算已匹配：
    //  · 不带工具行的确认卡（写授权卡 / 安装授权卡，filesystem 等经 user-input
    //    通道直接弹卡）永远留在兜底区，否则两边都不画、直接消失（E2E 实测）；
    //  · CR review（#1071, 2026-09-15）：此前还把「有 assistant 消息的 turn」
    //    一并算已匹配，但 MessageBubble 已不渲染确认卡——该条件只会让
    //    「有 assistant 消息 + 链里无确认卡行」的确认卡被误排除 → 卡消失。
    //    故仅保留确认卡行这一个条件。
    const msgTurnIds = new Set<string>();
    const hasConfirmRow = (m: Message) =>
      m.role === 'progress' &&
      (m.toolName === 'ask_user_confirm_card' ||
        (m.content ?? '').includes('ask_user_confirm_card'));
    for (const m of messages) {
      if (m.turnId && hasConfirmRow(m)) msgTurnIds.add(m.turnId);
    }
    const active = activeTurnIdRef.current;
    if (active && messages.some((m) => m.turnId === active && hasConfirmRow(m))) {
      msgTurnIds.add(active);
    }
    return new Set([...cardsByTurn.keys()].filter((t) => msgTurnIds.has(t)));
  }, [cardsByTurn, messages]);

  // #1071 review P1（2026-09-16）：同一张卡只允许一个 DOM 实例。消息内联
  // （MessageBubble 的 inline-cards）与兜底区（ConfirmCardArea）必须共用
  // 同一个「哪些卡会被内联渲染」的谓词——此前 cards= 只滤掉确认卡、兜底区
  // 又对 plan/action 无条件保留，同一张 plan/action 卡会同时出现在两处。
  // 角色门槛：计划/确认是 AI 回答的一部分，用户气泡不承载卡（用户消息从不
  // 带 turnId，此门槛当前是防御性空操作，只为与 inlineCardIds 保持同口径）。
  const inlineCardsForGroup = useCallback(
    (group: { msg: Message }): UserInputCardEntry[] => {
      if (group.msg.role === 'user' || !group.msg.turnId) return [];
      const cards = cardsByTurn.get(group.msg.turnId);
      if (!cards || cards.length === 0) return [];
      // 2026-08-27：确认卡由工具链行渲染（Hermes 式）——inline 只留 plan/action 卡
      return cards.filter((c) => !isConfirmCard(c as never));
    },
    [cardsByTurn]
  );
  // sourcesByMsg cache: keyed by a tool-only signature so the map object is
  // stable across typewriter frames (see sourcesByMsg below).
  const sourcesCacheRef = useRef<{ sig: string; map: Map<string, MessageSource[]> } | null>(null);
  // Tracks the latest messages for the session-switch snapshot.  Kept in
  // sync below; the switch effect snapshots the session we're leaving into
  // moduleMessagesSnapshot so switching back restores it instantly.
  const messagesRef = useRef<Message[]>([]);
  messagesRef.current = messages;
  const [sessionUpdatedAt, setSessionUpdatedAt] = useState<string | null>(null);
  const [customTitle, setCustomTitle] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState(false);
  const [clockTick, setClockTick] = useState(() => Date.now());
  // #570: bump to force a manual reload of the current session's history
  // (used by the "重试" button on the load-failure error bubble).
  const [retryTick, setRetryTick] = useState(0);
  const [executionPolicy, setExecutionPolicy] = useState<ExecutionPolicy>('edit');

  // Reasoning mode (issue #680): ⚡极速回答 / 🧠深度研究. Default fast
  // (user decision: 默认极速版); persisted per app (sessionStorage).
  const [reasoningMode, setReasoningMode] = useState<ReasoningMode>(() => {
    try {
      const saved = sessionStorage.getItem('miqi-reasoning-mode');
      return saved === 'think' ? 'think' : 'fast';
    } catch {
      return 'fast';
    }
  });
  // 同步 ref 镜像：handleSend 里读取（发送时刻的最新模式，不等 useEffect 渲染
  // ——否则"切模式后立刻发送"会用旧闭包/旧 ref 发出 fast）。
  const reasoningModeRef = useRef(reasoningMode);
  reasoningModeRef.current = reasoningMode;
  useEffect(() => {
    try {
      sessionStorage.setItem('miqi-reasoning-mode', reasoningMode);
    } catch {
      // ignore
    }
  }, [reasoningMode]);
  // EB-1 欢迎页模式卡选中态：独立于 reasoningMode（日常任务与代码任务都映射 think，
  // 若用 reasoningMode 推导会同时高亮两张卡）。welcomeMode 是组件级 state，跨会话
  // 不随欢迎页重挂而重置（ChatConsole 常驻），见下方 sessionKey effect。
  const [welcomeMode, setWelcomeMode] = useState<WelcomeMode>(
    reasoningMode === 'think' ? 'daily' : 'fast'
  );
  const selectWelcomeMode = (k: WelcomeMode) => {
    setWelcomeMode(k);
    setReasoningMode(WELCOME_MODE_REASONING[k]);
  };
  // issue #962 起点任务：三层渐进选择（模式 → 子项目 → 子子项目）。未选时是 null，
  // 这样"点了子项目才冒出子子项目行、点了子子项目才显示详细内容"的渐进流程才成立。
  //
  // 存的是「身份」（title）而不是下标：「内置技能」是 skills.list() 回来之后才插到
  // welcomeScenes 最前面的，用下标的话整个列表被挤位一格，用户先选好的场景/任务会
  // 悄悄指到隔壁去（#962 评审 P1：异步 prepend + index state 的状态不一致）。
  // title 在数据里唯一，welcomeScenes.test.ts 守着这条约束，所以当身份用是稳的；
  // 将来数据动态化/本地化时，把它换成显式 id 即可（见该文件头部说明）。
  const [pickedSceneTitle, setPickedSceneTitle] = useState<string | null>(null);
  const [pickedTaskTitle, setPickedTaskTitle] = useState<string | null>(null);
  // issue #962：代码任务的子项目里额外挂一项「内置技能」，内容来自 skills.list()，
  // 但只上架 SKILL_ORDER 白名单里的几个（首屏不铺开全部内置技能），拿不到就整项不显示。
  const [builtinSkillTasks, setBuiltinSkillTasks] = useState<readonly StarterTask[]>([]);
  useEffect(() => {
    let cancelled = false;
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    void (async () => {
      // 两种失败都退避重试：
      //   ① 直接抛（桥还没起来）；
      //   ② **回了个空清单**（桥起来了、但技能索引还没建好）—— 这个尤其阴：不抛错，
      //      看起来就像"这台机器没装技能"，而 effect 只在挂载 / loadTrigger 变化时跑，
      //      一旦静默失败，「内置技能」这一项就整场会话都不出现。
      //      e2e 并行起多个 app 时能稳定复现这种空清单。
      const LAST = 9;
      for (let attempt = 0; attempt <= LAST; attempt++) {
        try {
          const res = await window.miqi.skills.list();
          if (cancelled) return;
          const byName = new Map(
            (res?.skills ?? [])
              // 这里是纯字符串匹配，而 Windows 上 skills.list() 回的是反斜杠路径
              // （Python 侧 str(Path)），只写 '/kwp/' 在 Windows 上一条都匹配不上。
              .filter(
                (s) => s.source === 'builtin' && !s.path.replace(/\\/g, '/').includes('/kwp/')
              )
              .map((s) => [s.name, s] as const)
          );
          // 顺序与上架范围都以 SKILL_ORDER 为准：技能清单本身来自文件系统，顺序不稳定，
          // 而且没装的技能不该在首屏留空位。
          const tasks = SKILL_ORDER.map((name) =>
            byName.has(name) ? (SKILL_STARTERS[name] ?? null) : null
          ).filter((t): t is StarterTask => t !== null);

          if (tasks.length > 0) {
            setBuiltinSkillTasks(tasks);
            return;
          }
          // 空清单：本轮先不上架，退避后再试；最后一次仍为空才认（可能真的没装）
          if (attempt === LAST) return;
        } catch (e) {
          if (cancelled) return;
          if (attempt === LAST) {
            console.warn('[MiQroForge] skills.list() 连续失败，「内置技能」入口本次会话不显示', e);
            return;
          }
        }
        await sleep(400 * (attempt + 1));
      }
    })();
    return () => {
      cancelled = true;
    };
    // 依赖 loadTrigger：App 在 bridge 变成 running 时会把 runtimeReadyKey +1（并顺手
    // 预热技能索引），那一刻才是清单真正可用的时刻，重跑一次比在挂载时死磕更对症。
    // 保留退避重试是因为「running」之后索引仍可能在建，会先回一个空清单。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadTrigger]);
  const welcomeScenes = useMemo(() => {
    const base = MODE_SCENES[welcomeMode];
    if (welcomeMode !== 'code' || builtinSkillTasks.length === 0) return base;
    return [
      // 内置技能排在最前（产品要求）：它是"开箱即用"的入口，优先级高于场景模板
      { title: SKILL_SCENE_TITLE, icon: SKILL_SCENE_ICON, tasks: builtinSkillTasks },
      ...base,
    ];
  }, [welcomeMode, builtinSkillTasks]);
  // 空态欢迎页（起点任务只在这里出现，有消息后胶囊与详细内容都应消失）
  const isWelcomeEmpty = messages.length === 0;
  // 子项目 chips 行的横向翻页（两侧箭头）
  const sceneChipsRef = useRef<HTMLDivElement>(null);
  // 到边就把箭头置灰（#962 评审 P3）：能滚多远取决于内容宽度，只能实时量。
  const [chipScroll, setChipScroll] = useState({ left: false, right: false });
  const syncChipScroll = useCallback(() => {
    const el = sceneChipsRef.current;
    if (!el) return;
    setChipScroll({
      left: el.scrollLeft > 1,
      right: el.scrollLeft < el.scrollWidth - el.clientWidth - 1,
    });
  }, []);
  // 换模式 / 技能清单回来会换掉整排 chips，容器宽度跟着变，重挂载后要重新量一次。
  // 窗口缩放、资产面板开合同样会改宽度，但那两件事既不改 welcomeScenes 也不触发
  // scroll——少了这一层，列变窄、chips 变得可滚了，右箭头却还停在 disabled 上，
  // 后面的场景就点不到了（#962 CodeRabbit）。
  useEffect(() => {
    const el = sceneChipsRef.current;
    syncChipScroll();
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => syncChipScroll());
    ro.observe(el);
    return () => ro.disconnect();
  }, [welcomeScenes, syncChipScroll]);
  const pickedScene =
    pickedSceneTitle === null
      ? null
      : (welcomeScenes.find((s) => s.title === pickedSceneTitle) ?? null);
  const pickedTask =
    pickedScene && pickedTaskTitle !== null
      ? (pickedScene.tasks.find((t) => t.title === pickedTaskTitle) ?? null)
      : null;
  // 传给 Composer 的清空回调必须引用稳定（Composer 是 memo 的，#1042），否则
  // 每次 ChatConsole 渲染都会把 memo 打穿。
  const clearStarterScene = useCallback(() => setPickedSceneTitle(null), []);
  const clearStarterTask = useCallback(() => setPickedTaskTitle(null), []);
  // 换模式 → 两层都清空；换子项目 → 只清子子项目。
  useEffect(() => {
    setPickedSceneTitle(null);
    setPickedTaskTitle(null);
  }, [welcomeMode]);
  useEffect(() => {
    setPickedTaskTitle(null);
  }, [pickedSceneTitle]);
  // 记下"这条任务的提示词是我填进去的"。撤销任务时（胶囊 ×、换 L2、换模式、换会话
  // 都会走到）如果输入框里还是这段原文就一并清掉——否则 UI 显示「没选任务」、输入框
  // 却留着整段 prompt，看着像 × 没生效（#962 评审 P2）。用户手动改过就不动，别误删。
  const appliedTaskAskRef = useRef<string | null>(null);
  useEffect(() => {
    if (pickedTask) return;
    const ask = appliedTaskAskRef.current;
    appliedTaskAskRef.current = null;
    if (ask && composerRef.current?.getText().trim() === ask.trim()) {
      composerRef.current.setText('');
    }
  }, [pickedTask]);
  // Composer 侧的推理模式切换(ReasoningModeSwitch / 建议提示)同样要同步 welcome
  // 卡高亮——否则空态下先选了「代码任务」再从输入条切 fast,welcomeMode 停在 code、
  // 发送却用 fast,高亮与真实模式不一致(CodeRabbit)。会话已有消息后 welcome 卡不
  // 再渲染,只在 messages.length===0 时回写 welcomeMode。
  // useCallback (#1042): Composer is memoized, so this prop must be
  // referentially stable or the memo is defeated on every ChatConsole render.
  // Depends on the message COUNT only — the array identity changes on every
  // streaming frame, the count does not change while typing.
  const changeReasoningMode = useCallback(
    (m: ReasoningMode) => {
      setReasoningMode(m);
      if (messages.length === 0) setWelcomeMode((prev) => resolveWelcomeMode(prev, m));
    },
    [messages.length]
  );
  /** 会话代际：切会话时 +1；异步附件（FileReader / 剪贴板）提交前校验，
   *  避免旧会话的附件落到新会话（ChatConsole 跨会话常驻）。 */
  const sessionGenRef = useRef(0);
  // 切到新会话时按当前 reasoningMode 重新派生选中卡：避免沿用上个会话的 code 选择，
  // 却因中途切到 fast 而高亮与发送模式不一致（CodeRabbit）。仅随 sessionKey 触发，
  // 不在同一会话内用 reasoningMode 变化覆盖用户手动选卡。
  useEffect(() => {
    // 这里**必须**直接派生，不能走 resolveWelcomeMode —— 那个 prev 守卫只对
    // 「同一会话内切 reasoningMode」成立，放到会话边界上就反了：daily 与 code 都映射
    // think，带着 prev 走会把 A 会话的 code 泄漏进新的空会话（欢迎页仍高亮「代码任务」、
    // 「内置技能」也跟着出现，而用户在新会话里可能想干的是日常任务）。这个 effect 的
    // 本意就是「不沿用上个会话的 code 选择」，与守卫的方向正好相反（#962 评审 P1）。
    setWelcomeMode(reasoningModeToWelcome(reasoningMode));
    // develop 的会话代际：切会话时 +1，异步附件（FileReader / 剪贴板）提交前校验，
    // 避免旧会话的附件落到新会话。
    sessionGenRef.current += 1;
    // 会话换了就把起点任务的两层选择一起清掉。ChatConsole 是常驻组件，而上面那个
    // [welcomeMode] 的清理只在模式真的变了时才跑——新会话如果还是 code，上个会话
    // 的选中态和输入框胶囊就会跟着显示出来（#962 CodeRabbit）。
    setPickedSceneTitle(null);
    setPickedTaskTitle(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);
  const [streaming, setStreaming] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  /** 事件级去重：一次「动作」(选择/粘贴) 内同名同大小只挂一次；
   *  跨通道（浏览器 paste 与主进程剪贴板）在 500ms 内算同一动作，避免重复挂载。
   *  不再用“1s 内全局 name:size”去重——那会误杀合法的同名同大小附件。 */
  const attachActionRef = useRef<{ id: string; ts: number; seen: Set<string> }>({
    id: '',
    ts: 0,
    seen: new Set<string>(),
  });
  const newAttachAction = () => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    attachActionRef.current = { id, ts: Date.now(), seen: new Set<string>() };
    return id;
  };
  const acceptInAttachAction = (actionId: string, key: string) => {
    if (attachActionRef.current.id !== actionId) {
      attachActionRef.current = { id: actionId, ts: Date.now(), seen: new Set<string>() };
    }
    const act = attachActionRef.current;
    act.ts = Date.now();
    if (act.seen.has(key)) return false;
    act.seen.add(key);
    return true;
  };
  /** Ctrl+V 有两条通道：原生 paste 与主进程读剪贴板。keydown 只登记一个 token，
   *  真正的 paste 事件决定是否取消 fallback；若浏览器始终没给 paste，再由 timeout
   *  走主进程读取。两条通道共用同一 token，并按「内容特征」(size:mime) 去重，
   *  避免同名不同源（image.png vs pasted-image-*.png）绕过 name:size 去重。 */
  const pendingPasteRef = useRef<{ token: string | null; timer: number | null }>({
    token: null,
    timer: null,
  });
  const recentFpRef = useRef<{ fp: string; ts: number }[]>([]);
  /** 跨通道去重改用**内容指纹**（长度 + 头/尾采样的 FNV-1a），不再用 size+mime 近似：
   *  两张同尺寸不同内容的截图不会被误判为同一份。 */
  const seenFingerprintRecently = (fp: string) => {
    const now = Date.now();
    const arr = recentFpRef.current.filter((e) => now - e.ts < 1500);
    const dup = arr.some((e) => e.fp === fp);
    recentFpRef.current = [...arr, { fp, ts: now }].slice(-12);
    return dup;
  };

  const MAX_ONE_ATTACHMENT_BYTES = 25 * 1024 * 1024;
  /** 附件总量上限（与主进程剪贴板一致）：renderer 侧 input/拖拽/paste 三入口统一。 */
  const MAX_TOTAL_ATTACHMENT_BYTES = 40 * 1024 * 1024;
  const attachmentsRef = useRef<Attachment[]>([]);
  /** 已接受但尚未提交的字节（同步预留）：两个异步 action 并发时，
   *  只读 attachmentsRef 会各自看到旧总量而突破 40MB（review P1）。 */
  const reservedBytesRef = useRef(0);
  useEffect(() => {
    attachmentsRef.current = attachments;
  }, [attachments]);
  const attachmentsBytes = () =>
    attachmentsRef.current.reduce((sum, a) => sum + (a.size || 0), 0) + reservedBytesRef.current;
  /** 同步的「检查 + 预留」单一临界操作：跨 action 并发也不会各自读到旧总量。 */
  const tryReserve = (size: number): boolean => {
    if (attachmentsBytes() + size > MAX_TOTAL_ATTACHMENT_BYTES) return false;
    reservedBytesRef.current += size;
    return true;
  };
  const releaseReservation = (size: number) => {
    reservedBytesRef.current = Math.max(0, reservedBytesRef.current - size);
  };
  /** 附件真正提交进 state 时，释放它自己的预留。不能用「提交后总字节的净变化」推断：
   *  用户先删旧附件、随后 pending 附件提交时净变化为负，会漏释放（reservation 泄漏）。 */
  const commitAttachment = (add: (prev: Attachment[]) => Attachment[], size: number) => {
    setAttachments(add);
    releaseReservation(size);
  };
  const commitIfGen = (gen: number, add: (prev: Attachment[]) => Attachment[], size: number) => {
    if (gen !== sessionGenRef.current) {
      releaseReservation(size);
      return false;
    }
    commitAttachment(add, size);
    return true;
  };
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [downloadingPaperId, setDownloadingPaperId] = useState<string | null>(null);
  /** #668 补：论文下载结果反馈（paperId → done+savePath / failed+error） */
  const [paperDownloadStates, setPaperDownloadStates] = useState<
    Record<string, { status: 'done' | 'failed'; savePath?: string; error?: string }>
  >({});
  /** #696 补：下载完成 toast（成功提示 + 打开文件夹，居中 + 淡入淡出 + 2s） */
  const [downloadToast, setDownloadToast] = useState<{ filename: string; savePath: string } | null>(
    null
  );
  /** #1062：结果/过程文件的「定位」「预览」失败时给出可见提示（此前静默无反应）。 */
  const [assetError, setAssetError] = useState<string | null>(null);
  // 用 `number`：这里配的是 window.setTimeout（DOM 返回 number），而
  // `ReturnType<typeof setTimeout>` 在本工程的 node 类型下解析成 Timeout，赋不进去。
  const assetErrorTimerRef = useRef<number | null>(null);
  const notifyAssetError = useCallback((msg: string) => {
    // 先取消上一条的定时器：否则它会在自己的 4 秒到点时把后设的、仍然相关的
    // 消息提前清掉 —— 那会削弱本 PR 要给的保证（失败一定看得见）。
    if (assetErrorTimerRef.current) window.clearTimeout(assetErrorTimerRef.current);
    setAssetError(msg);
    assetErrorTimerRef.current = window.setTimeout(() => {
      assetErrorTimerRef.current = null;
      setAssetError(null);
    }, 4000);
  }, []);
  const [toastVisible, setToastVisible] = useState(false);

  // Lazily re-read image attachments after session load: the sender embeds
  // only "[Image: name]" in the persisted content; the actual bytes live in
  // the session files dir and are fetched on demand (#659).
  // NOTE: use the sessionKey PROP (render-time value), not
  // currentSessionRef.current — the ref updates in an effect that runs AFTER
  // render, so on session switch the lazy-load would read the previous
  // session's key and fail to find the image files.
  useEffect(() => {
    const activeKey = sessionKey;
    if (!historyLoaded || !activeKey) return;
    let cancelled = false;
    const pendingImages = messages.flatMap((m) =>
      (m.attachments ?? []).filter((a) => a.type === 'image' && !a.dataUrl)
    );
    if (pendingImages.length === 0) return;
    // Bound concurrent restores — an unbounded Promise.all would fire one IPC
    // read per historical image at once, spiking bridge/renderer memory on
    // sessions with many large images (CodeRabbit #661 review).
    const CONCURRENCY = 3;
    let cursor = 0;
    const worker = async () => {
      while (!cancelled) {
        const idx = cursor++;
        if (idx >= pendingImages.length) return;
        const att = pendingImages[idx];
        try {
          const res = await window.miqi.files.read(att.name, activeKey);
          if (cancelled || !res?.data_base64) continue;
          const mime = res.mime_type || 'image/png';
          const dataUrl = `data:${mime};base64,${res.data_base64}`;
          setMessages((prev) =>
            prev.map((m) =>
              m.attachments?.some((a) => a === att)
                ? {
                    ...m,
                    attachments: m.attachments.map((a) =>
                      a === att ? { ...a, dataUrl, status: 'done' as const, size: res.size } : a
                    ),
                  }
                : m
            )
          );
        } catch {
          // Image file missing on disk — keep the placeholder chip.
        }
      }
    };
    void Promise.all(
      Array.from({ length: Math.min(CONCURRENCY, pendingImages.length) }, () => worker())
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historyLoaded, sessionKey]);
  // 面板默认展开;窗口不够宽时由主进程的 syncWindowMin 负责**撑到目标最小宽度**
  // (minOnly 只表示「不应用 panel extra」,并不禁止为满足最小布局扩窗)—— 不藏面板,
  // 否则依赖面板的 e2e/用户路径会直接看不到面板(macOS CI 窗口 < 1100 时曾因此挂 10 条)。
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(280);
  const panelResizing = useRef(false);
  /** 面板 DOM 节点:拖拽中直改其宽度,避免每帧 setPanelWidth 让整个 ChatConsole
   *  (含长回复消息树)重建 VDOM——内容多的对话会因此卡。 */
  const assetsPanelRef = useRef<HTMLDivElement | null>(null);
  const panelWidthRef = useRef(panelWidth);
  /** 附件预览的投送目标:Composer 框内插槽节点(见下方 portal)。 */
  const [attachmentSlot, setAttachmentSlot] = useState<HTMLDivElement | null>(null);
  /** 点「文件面板」打开时置位：等主进程真的把窗口加宽了，才让面板出现。
   *  见下面 onRequestSettled。 */
  const pendingPanelReveal = useRef(false);
  /** 资产面板拖宽的「窗口跟随」串行队列（#989，实现与竞态回归见
   *  panelWindowSync.ts / panelWindowSync.test.ts）：面板变宽就请求主进程把原生
   *  窗口同量加宽，聊天列 flex-1 分到新增宽度而保持原宽。拖拽锚点、latest-wins
   *  合并、松手收尾都在队列里，这里只负责喂鼠标位移与把它接到 DOM/state 上。 */
  const [panelSync] = useState(() =>
    createPanelWindowSync({
      send: (extra) => window.miqi.app.setPanelWindowExtra(extra),
      applyWidth: (width) => {
        const el = assetsPanelRef.current;
        if (el) el.style.width = `${width}px`;
      },
      commitWidth: (width) => {
        if (width !== panelWidthRef.current) setPanelWidth(width);
      },
      // 窗口加宽落地（或被跳过/请求失败）后再显示面板。打开按钮先只发加宽请求，
      // 面板此刻还不渲染——否则面板先出现、聊天列被压窄一瞬，等 IPC 回来窗口才
      // 跟上，正是本 PR 要消掉的那个挤压。最大化/满屏时主进程回 skipped，这里
      // 同样会走到（notifyRequestSettled 覆盖了 skipped 与失败分支），面板照常
      // 显示，不会点不开。
      onRequestSettled: () => {
        if (!pendingPanelReveal.current) return;
        pendingPanelReveal.current = false;
        setPanelOpen(true);
      },
    })
  );
  /** 顶部工作目录胶囊:窄的不是视口而是「聊天列」(被资产面板挤窄、窗口又有 minWidth),
   *  原 md: 视口断点永不触发。量聊天列宽,过窄时把目录路径收成一个小图标。 */
  const chatColRef = useRef<HTMLDivElement | null>(null);
  const capsuleRoRef = useRef<ResizeObserver | null>(null);
  const [subHeaderCompact, setSubHeaderCompact] = useState(false);
  const setChatColRef = useCallback((el: HTMLDivElement | null) => {
    chatColRef.current = el;
    capsuleRoRef.current?.disconnect();
    capsuleRoRef.current = null;
    if (!el) return;
    const ro = new ResizeObserver(() => setSubHeaderCompact(el.clientWidth < 520));
    ro.observe(el);
    capsuleRoRef.current = ro;
    setSubHeaderCompact(el.clientWidth < 520);
  }, []);
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const [workspacePickerAnchor, setWorkspacePickerAnchor] = useState<DOMRect | null>(null);
  const [recentWorkspaces, setRecentWorkspaces] = useState<string[]>([]);
  const workspacePickerOpenRef = useRef(false);

  useEffect(() => {
    workspacePickerOpenRef.current = workspacePickerOpen;
  }, [workspacePickerOpen]);

  useEffect(() => {
    panelWidthRef.current = panelWidth;
  }, [panelWidth]);

  // 上报「面板当前是否占宽」给主进程用于抬高窗口最小宽度(不改窗口宽):冷启动面板默认
  // 展开且没有加宽请求,不报的话缩窗会把聊天列/输入框压到最小宽度以下;关闭时上报 0 还原。
  //
  // 只依赖 panelOpen,且传占用标志(1/0):主进程只用 target > 0,不关心具体宽度,
  // 所以依赖 panelWidth 只会让每次宽度提交多打一次无意义的 IPC(baiye-banned #1047)。
  //
  // 该上报可能会为满足最小布局把窗口撑到目标宽度 —— 这次变化不经拖拽队列的 send(),
  // 因此要把返回的 applied 同步进队列基线,否则首次拖拽会拿旧基线把撑窗量重复计入
  // (CodeRabbit #1047)。
  useEffect(() => {
    void window.miqi.app
      .setPanelWindowExtra(panelOpen ? 1 : 0, true)
      .then((r) => panelSync.syncApplied(r.applied))
      .catch(() => {});
  }, [panelOpen, panelSync]);

  useEffect(() => {
    const timer = window.setInterval(() => setClockTick(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false; // prevent overlapping polls when bridge is slow (#311)
    const loadActivePlugins = async () => {
      if (inFlight) return; // skip if previous request still pending
      try {
        inFlight = true;
        const result = await window.miqi.plugins.list();
        const plugins = (result as unknown as { plugins?: Array<{ status?: string }> })?.plugins;
        if (!cancelled) {
          setActivePluginCount(
            (plugins ?? []).filter((plugin) => plugin.status === 'active').length
          );
        }
      } catch {
        if (!cancelled) setActivePluginCount(0);
      } finally {
        inFlight = false;
      }
    };

    loadActivePlugins();
    const timer = window.setInterval(loadActivePlugins, 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(
    () => () => {
      capsuleRoRef.current?.disconnect();
      capsuleRoRef.current = null;
    },
    []
  );
  // 卸载(nav 离开聊天页)时停掉窗口跟随队列,并把窗口还原到未加宽状态,
  // 避免残宽影响其它页面;冷启动默认面板开启,基线即当前宽度,此还原为 no-op。
  useEffect(
    () => () => {
      panelSync.dispose();
      void window.miqi.app.setPanelWindowExtra(0).catch(() => {});
    },
    [panelSync]
  );

  // Task Assets panel resize
  const handlePanelResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const el = assetsPanelRef.current;
      // 按下点即当前分隔条:队列届时记下实际面板宽 + 主进程此刻已应用的窗口加宽,
      // 拖动时两者作为相对基准,不用绝对宽(冷启动默认面板已占空间,绝对宽会让窗口多扩整块)。
      panelResizing.current = true;
      panelSync.beginDrag({
        clientX: e.clientX,
        width: el ? el.getBoundingClientRect().width : window.innerWidth - e.clientX,
      });
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [panelSync]
  );

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      const anchor = panelSync.anchor;
      if (!panelResizing.current || !anchor) return;
      // 以按下点为锚按鼠标位移增减面板宽(向右移收窄、向左移加宽)。窗口加宽请求与
      // 面板 DOM 宽度都由队列按主进程实际应用到的增量推进,本处不直改 DOM。
      panelSync.dragTo(clampPanelWidth(anchor.width + (anchor.clientX - e.clientX)));
    };
    const handleMouseUp = () => {
      if (!panelResizing.current) return;
      panelResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // 松手不在此刻定格面板:可能还有一次 IPC 在途、applied 还是旧值,按旧值写
      // DOM 会把面板钉住,等窗口真的动完就错位。交给队列在静默后统一收尾。
      panelSync.endDrag();
    };
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      // cleanup if unmounted during drag
      panelResizing.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [panelSync]);
  /** Current in-flight request ID (for abort) */
  const [currentReqId, setCurrentReqId] = useState<string | null>(null);
  /** Per-session timestamp of the pending optimistic user bubble (issue #364)
   *  while a send waits on a slow provider check / thread init.  Keyed by
   *  session so a session switch never shows a stale spinner on another
   *  session's messages, and the icon renders only on the exact bubble whose
   *  timestamp matches. */
  const [sendingBySession, setSendingBySession] = useState<Map<string, number>>(new Map());
  /** Set/clear the pending-bubble marker for one session.  Always produces a
   *  NEW map so the state update triggers a re-render. */
  const setSendingFor = useCallback((key: string, ts: number | null) => {
    setSendingBySession((prev) => {
      const next = new Map(prev);
      if (ts == null) next.delete(key);
      else next.set(key, ts);
      return next;
    });
  }, []);
  /** Timestamp of the pending bubble for `key`, or null. */
  const sendingFor = useCallback(
    (key: string): number | null => sendingBySession.get(key) ?? null,
    [sendingBySession]
  );
  /** files touched by the agent during this session */
  const [trackedFiles, setTrackedFiles] = useState<TrackedFile[]>([]);
  /** 回合序号 → 该回合累积的结构化来源（#879 ③ 文件 → 相关引用）。 */
  const [turnSourcesMap, setTurnSourcesMap] = useState<Map<number, MessageSource[]>>(new Map());
  /** preview modal */
  const [previewFile, setPreviewFile] = useState<{
    path: string;
    content?: string;
    dataBase64?: string;
    /** #877: rich render kind — pdf iframe / spreadsheet table / docx blocks. */
    kind?: 'pdf' | 'spreadsheet' | 'document' | 'image';
    pdfUrl?: string;
    /** kind==='image' 时的图片源（data URL）。 */
    imageUrl?: string;
    spreadsheet?: SpreadsheetData;
    docBlocks?: DocumentBlocks;
  } | null>(null);
  /** File preview modal: show HTML source instead of the rendered iframe. */
  const [htmlSourceMode, setHtmlSourceMode] = useState(false);

  // Revoke the previous PDF blob URL whenever the preview changes or closes
  // (#877) — mirrors the WorkspacePage blob lifecycle.
  useEffect(() => {
    return () => {
      if (previewFile?.pdfUrl) URL.revokeObjectURL(previewFile.pdfUrl);
    };
  }, [previewFile?.pdfUrl]);

  // When preview is open, lock the entire page body so no clicks fall through
  // to elements behind the modal (sidebar, chat area, etc.)
  useEffect(() => {
    if (previewFile) {
      const prev = document.body.style.pointerEvents;
      document.body.style.pointerEvents = 'none';
      return () => {
        document.body.style.pointerEvents = prev;
      };
    }
  }, [previewFile]);

  // Destroy all IPC listeners on unmount to prevent memory leaks and
  // state-updates on an unmounted component (#378 fix, round 2).  Also cancel
  // the active send's watchdog interval + typewriter frame so an in-flight
  // send can't keep calling setMessages after unmount.
  useEffect(() => {
    return () => {
      activeSendCleanupRef.current?.();
      cleanupListeners();
      // Dispose EVERY active send invocation — the unsubsRef singleton only
      // tracks the latest one; cross-session invocations outlive it and must
      // not keep firing watchdogs or calling setMessages after unmount.
      for (const entry of sendInvocationRegistryRef.current.values()) {
        entry.cleanup();
        for (const unsub of entry.unsubs) unsub();
      }
      sendInvocationRegistryRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** diff modal */
  const [diffFile, setDiffFile] = useState<{
    path: string;
    diff: string | null;
    original_content: string | null;
    current_content: string | null;
    has_diff: boolean;
    is_new_file?: boolean;
  } | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [reverting, setReverting] = useState(false);
  // Inline exec output: tool_call_id → accumulated stdout/stderr
  const [execOutputs, setExecOutputs] = useState<
    Record<string, { stdout: string; stderr: string; running: boolean }>
  >({});
  // When false, suppress the bordered inline terminal box for exec outputs.
  // Stored under desktop.ui.inlineExecOutput (opaque desktop-owned settings).
  // Defaults to false to avoid empty-box artifacts when sandbox policy strips
  // stdout/stderr (see issue surfaced after #339).
  const [inlineExecOutput, setInlineExecOutput] = useState(false);
  useEffect(() => {
    window.miqi.config
      ?.get()
      ?.then((cfg: any) => {
        if (cfg?.desktop?.ui?.inlineExecOutput === true) setInlineExecOutput(true);
      })
      .catch(() => {});
  }, []);

  // Refetch when window regains focus, so toggling the setting in the
  // Settings page takes effect without a full app reload.
  useEffect(() => {
    const refetch = () => {
      window.miqi.config
        ?.get()
        ?.then((cfg: any) => setInlineExecOutput(cfg?.desktop?.ui?.inlineExecOutput === true))
        .catch(() => {});
    };
    window.addEventListener('focus', refetch);
    document.addEventListener('visibilitychange', refetch);
    return () => {
      window.removeEventListener('focus', refetch);
      document.removeEventListener('visibilitychange', refetch);
    };
  }, []);
  const [merging, setMerging] = useState(false);
  const [activePluginCount, setActivePluginCount] = useState(0);
  const [shareStatus, setShareStatus] = useState<'idle' | 'copied' | 'exported' | 'context'>(
    'idle'
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);
  const justOpened = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<ComposerHandle>(null);
  // 会话活动感知（restored from pre-#577, issue #677）：流式/消息变化时
  // 上报 App，让"+"能感知未落盘的活动
  useEffect(() => {
    const hasActivity =
      streaming || messages.some((m) => m.role === 'user' || m.role === 'assistant');
    onSessionActivityChange?.(hasActivity);
  }, [streaming, messages, onSessionActivityChange]);
  const { setActiveSession } = useUserInput();
  // #646-v2（2026-09-15 定稿，对齐 Claude Code 的 resubmit 语义）：用户在计划卡里
  // 提交调整意见后，由后端在**同一回合**重新规划（CollaborativeTurnRunner 的
  // _plan_adjustment_pending → 追加一轮 provider → 新计划卡）。前端**不再**聚焦底部
  // 输入框、不再提示"请输入调整要求"——否则与「有卡等待时输入框隐藏」的定稿冲突，
  // 并且诱导用户把同一意见再输一遍（旧 lastAdjustAt/adjustHint 机制已移除）。
  const adjustHint = false;
  // 会话隔离（CodeRabbit #666）：切会话 → 清空全部确认卡
  useEffect(() => {
    setActiveSession(sessionKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);
  // 原生 window.confirm 模态框关闭后，Chromium 可能不把“真实的 OS 激活”交还
  // renderer：键盘事件被吞、点输入条无光标，刷新重建页面才恢复（手动复现）。
  // 早期版本里空/非空输入条是两棵子树，删除对话时旧 textarea 卸载重挂会顺带
  // 触发一次真实焦点重授，故能靠“document.hasFocus() 为 false 再硬激活”兜住。
  // 现在输入条统一为常驻一棵，confirm 关闭时 Chromium 会把焦点“还”给仍挂载的
  // textarea —— document.hasFocus() 读 true，但击键从未被重新授予，仅凭
  // hasFocus() 判据会漏。因此凡从“有内容的会话”落到空欢迎页（删光会话/删除
  // 当前会话），无条件硬激活一次（主进程 blur→focus 逼出真正的激活，重新下发
  // 页面焦点）；其余空态仍按 hasFocus() 缺失才硬激活，避免无谓闪烁。
  const welcomeFocusedFor = useRef<string | null>(null);
  const lastMsgCountRef = useRef<number | null>(null);
  useEffect(() => {
    if (!historyLoaded || streaming) return;
    if (messages.length === 0 && welcomeFocusedFor.current !== sessionKey) {
      welcomeFocusedFor.current = sessionKey ?? null;
      const prevHadMessages = (lastMsgCountRef.current ?? 0) > 0;
      const focusInput = () => composerRef.current?.focus();
      focusInput();
      void window.miqi.app?.focus?.();
      const t1 = window.setTimeout(focusInput, 120);
      const t2 = window.setTimeout(() => {
        focusInput();
        void window.miqi.app?.focus?.();
        const needsHard = prevHadMessages || !document.hasFocus();
        if (needsHard) {
          window.setTimeout(() => {
            void window.miqi.app?.focus?.({ hard: true }).then(() => {
              window.setTimeout(focusInput, 80);
            });
          }, 60);
        }
      }, 420);
      return () => {
        window.clearTimeout(t1);
        window.clearTimeout(t2);
      };
    }
  }, [historyLoaded, streaming, messages, sessionKey]);
  // 从侧栏删除「非当前」会话时不会发生会话切换，上面的入口 effect 不会重跑；但
  // 原生 window.confirm 模态同样会偷走 OS 键盘授予（hasFocus() 读 true、击键却被
  // 吞）。删除路径在 confirm 通过后派发本事件，这里若处于空欢迎页就重发一次硬激活
  // （主进程 blur→focus），与上面 9ad436c7 的修法同源。
  // 监听器无条件注册：若删除发生在历史加载完成前（空态还没就绪），事件不会丢——
  // 记下 pending，等当前会话加载完成且为空时再消费执行（CodeRabbit）。非空会话里
  // 删除其它会话本就不该动当前输入框（入口 effect 只在落到空态时接管），直接忽略。
  const historyLoadedRef = useRef(historyLoaded);
  historyLoadedRef.current = historyLoaded;
  const messageCountRef = useRef(messages.length);
  messageCountRef.current = messages.length;
  const pendingRegrantRef = useRef(false);
  useEffect(() => {
    const runRegrant = () => {
      composerRef.current?.focus();
      window.setTimeout(() => {
        void window.miqi.app?.focus?.({ hard: true }).then(() => {
          window.setTimeout(() => composerRef.current?.focus(), 80);
        });
      }, 60);
    };
    const regrant = () => {
      if (messageCountRef.current > 0) return;
      if (!historyLoadedRef.current) {
        pendingRegrantRef.current = true;
        return;
      }
      runRegrant();
    };
    window.addEventListener('miqi:chat-focus-regrant', regrant);
    return () => window.removeEventListener('miqi:chat-focus-regrant', regrant);
  }, []);
  // 切换会话会重建空态，加载途中攒下的 pending 只属于旧会话——先于消费 effect
  // 清掉，别在别的会话里误触发一次硬激活（同源：消费 effect 仅在「空 + 已加载」时跑）。
  useEffect(() => {
    pendingRegrantRef.current = false;
  }, [sessionKey]);
  useEffect(() => {
    if (historyLoaded && messages.length === 0 && pendingRegrantRef.current) {
      pendingRegrantRef.current = false;
      composerRef.current?.focus();
      window.setTimeout(() => {
        void window.miqi.app?.focus?.({ hard: true }).then(() => {
          window.setTimeout(() => composerRef.current?.focus(), 80);
        });
      }, 60);
    }
  }, [historyLoaded, messages, sessionKey]);
  // 记录上一次提交的 messages 长度（声明于聚焦 effect 之后：effect 按声明顺序
  // 逐个执行，聚焦 effect 先跑、读到的仍是旧值；本 effect 无依赖、每次提交都跑）。
  useEffect(() => {
    lastMsgCountRef.current = messages.length;
  });
  const toolArgsByCallId = useRef<Map<string, unknown>>(new Map());
  /** web_search tool outputs (by tool_call_id) for click-to-expand result
   *  cards on the live tool row (#539). State, not ref — cards must re-render
   *  when the end event lands. */
  const [searchResultsByCallId, setSearchResultsByCallId] = useState<Record<string, string>>({});
  const previewJustClosed = useRef(false);
  const unsubsRef = useRef<Array<() => void>>([]);
  // Which send invocation the unsubs in unsubsRef belong to — a new send must
  // only auto-unsubscribe the PREVIOUS invocation when both target the same
  // session; otherwise a send in session B silently kills session A's
  // in-flight listeners and its terminal events are never processed.
  const unsubsSessionRef = useRef<string | null>(null);
  // EVERY active send invocation's cleanup resources, keyed by its unique
  // send id.  The unsubsRef singleton only remembers the latest invocation —
  // without this registry, cross-session invocations outlive it and their
  // watchdogs/listeners would keep calling setMessages after unmount.  The
  // session key lets abort/stop dispose only the invocation of the session
  // being stopped instead of the latest one.
  const sendInvocationRegistryRef = useRef<
    Map<number, { unsubs: Array<() => void>; cleanup: () => void; sessionKey: string }>
  >(new Map());
  const finalCleanupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Shared across handleSend closures: a new send aborts the previous turn's
  // typewriter reveal (its RAF is closure-local and otherwise leaks a ghost
  // assistant bubble into the next turn).
  const revealAnimIdRef = useRef<number | null>(null);
  // The live turn's watchdog interval. Shared across closures so an interrupt
  // (handleAbort / interrupt-and-resend) can stop the superseded turn's timer —
  // its own sendCleanup never runs because its listeners are already removed.
  const watchdogTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // The current turn's FULL lifecycle (threads.start + chat.send + terminal
  // event), registered before any await that can be superseded. The next
  // send awaits the superseded turn's lifecycle so its terminal event is
  // consumed before new listeners register — and the backend drain task has
  // exited, so the new chat.send is not rejected with TURN_IN_PROGRESS.
  // Kept after a manual stop (only cleared by the owning handleSend in its
  // identity-checked finally) so stop-then-quick-send still serializes.
  const lifecycleRef = useRef<{ id: number; promise: Promise<void>; sessionKey: string } | null>(
    null
  );
  // Monotonic id for lifecycleRef identity checks — never reset, so a session
  // switch cannot reuse an old turn's id and collide with a still-in-flight
  // lifecycle (would let an old settle clear the NEW lifecycle) (#879 ③ CodeRabbit).
  const lifecycleSeqRef = useRef(0);
  // 回合序号（第几个 user 回合，从 0 起）——source 回合索引（turnSourcesMap /
  // 文件卡片「相关引用」）。会话加载时重置；与 lifecycleSeqRef 分离。
  const turnSeqRef = useRef(0);
  const liveReasoningTsRef = useRef<number | null>(null);
  // Anchor of the first reasoning delta of the current turn — thinking
  // duration is measured from this (pure thinking, excluding tool time).
  const thinkingStartedAtRef = useRef<number | null>(null);
  // Timestamp of the latest reasoning delta of the turn — the thinking
  // "end". Using this (instead of final-event time) excludes tool-execution
  // intervals that follow the last reasoning burst (CodeRabbit #662).
  const lastReasoningDeltaAtRef = useRef<number | null>(null);
  // Throttle live-reasoning re-renders: reasoning deltas arrive in a fast
  // stream and each setMessages forces a full messages rebuild + markdown
  // re-render in ThinkBlock.  Buffer deltas and flush on a short timer so the
  // UI updates a few times a second instead of per-chunk (fixes "thinking
  // displays slowly" under heavy reasoning streams).
  const reasoningBufRef = useRef('');
  const reasoningTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flushReasoningRef = useRef<((ts: number) => void) | null>(null);
  // Flush any buffered reasoning deltas into the message list immediately.
  // Used by abort/error/final so the tail of the thinking text is never lost.
  flushReasoningRef.current = (ts: number) => {
    if (reasoningTimerRef.current) {
      clearTimeout(reasoningTimerRef.current);
      reasoningTimerRef.current = null;
    }
    const buffered = reasoningBufRef.current;
    reasoningBufRef.current = '';
    if (buffered) {
      setMessages((prev) => appendReasoningDelta(prev, buffered, ts, reasoningMode));
    }
  };
  const shareFeedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentSessionRef = useRef(sessionKey);
  // Track the active thread ID for new-protocol thread-aware conversations
  const currentThreadIdRef = useRef<string | null>(null);

  const inFlightCacheRef = useRef(moduleInFlightCache);
  const fullContentRef = useRef('');
  // Active send's cleanup (watchdog interval + typewriter RAF), so the
  // unmount effect can cancel in-flight work even when a send is ongoing.
  const activeSendCleanupRef = useRef<(() => void) | null>(null);

  // ── Thread tabs for multi-agent support ──
  interface ThreadTab {
    threadId: string;
    agentType: string;
    label: string;
  }
  const [threads, setThreads] = useState<ThreadTab[]>([
    { threadId: 'main', agentType: 'main', label: '主线程' },
  ]);
  const [activeThreadId, setActiveThreadId] = useState('main');

  useEffect(() => {
    const unsub = window.miqi.agents?.onSpawned((data) => {
      setThreads((prev) => {
        if (prev.find((t) => t.threadId === data.sub_thread_id)) return prev;
        return [
          ...prev,
          {
            threadId: data.sub_thread_id,
            agentType: data.agent_type,
            label: data.task_label || data.agent_type,
          },
        ];
      });
    });
    return () => {
      if (unsub) unsub();
    };
  }, []);

  useEffect(() => {
    const unsub = window.miqi.agents?.onCompleted((data) => {
      setThreads((prev) =>
        prev.map((t) =>
          t.threadId === data.sub_thread_id ? { ...t, label: `${t.label.replace(/ ✓$/, '')} ✓` } : t
        )
      );
    });
    return () => {
      if (unsub) unsub();
    };
  }, []);

  // ── Plan sidebar state ──
  interface PlanStep {
    id: string;
    description: string;
    status: 'pending' | 'in_progress' | 'completed' | 'skipped';
    depends_on: string[];
  }
  const [plan, setPlan] = useState<{ title: string; steps: PlanStep[] } | null>(null);
  const [planOpen, setPlanOpen] = useState(false);

  useEffect(() => {
    const unsub = window.miqi.plan?.onUpdated((data) => {
      if (data.plan) {
        setPlan(data.plan);
        setPlanOpen(true);
      }
    });
    return () => {
      if (unsub) unsub();
    };
  }, []);

  /** Upsert a file into trackedFiles */
  const trackFile = useCallback(
    (
      path: string,
      op: TrackedFile['op'],
      truncated = false,
      turnId?: number,
      sourceTool?: string
    ) => {
      // Normalise sandbox-internal paths before storing so Preview works
      const normPath = normalizeSandboxPath(path);
      // Strip surrounding quotes, trailing ellipsis, and leading ./ for dedup
      const clean = normPath
        .replace(/^["']|["']$/g, '')
        .replace(/\.{3,}$/, '')
        .replace(/[…]$/, '')
        .replace(/^\.\//, '')
        .trim();
      setTrackedFiles((prev) => {
        // Fuzzy match: compare cleaned base name, then exact path
        const existing = prev.find((f) => {
          const fc = f.path
            .replace(/^["']|["']$/g, '')
            .replace(/\.{3,}$/, '')
            .replace(/[…]$/, '')
            .replace(/^\.\//, '')
            .trim();
          // Basename-only matching should only kick in when one side is a bare
          // filename (no directory), e.g. a tool hint reporting just "foo.pdf"
          // that needs to match an existing "papers/foo.pdf" entry. Two paths
          // that both carry (different) directories must not be merged just
          // because they share a filename.
          const eitherIsBareFilename = !clean.includes('/') || !fc.includes('/');
          return (
            f.path === normPath ||
            fc === clean ||
            sameTrackedFile(f.path, normPath, workspace) ||
            sameTrackedFile(fc, clean, workspace) ||
            (eitherIsBareFilename && basename(f.path) === basename(clean))
          );
        });
        if (existing) {
          // Upgrade: read < edit < write
          const rank: Record<TrackedFile['op'], number> = { read: 0, edit: 1, write: 2, delete: 3 };
          const nextOp = rank[op] > rank[existing.op] ? op : existing.op;
          return prev.map((f) =>
            f.path === existing.path
              ? {
                  ...f,
                  op: nextOp,
                  lastSeen: Date.now(),
                  truncated: f.truncated && truncated,
                  turnId: turnId ?? f.turnId,
                  sourceTool: sourceTool ?? f.sourceTool,
                }
              : f
          );
        }
        return prev; // new entries are verified for existence async below
      });
      // New entries: only surface a file that actually exists — tool hints can
      // report a filename that was referenced (e.g. an image inside an HTML page)
      // but never saved. Checked after the write usually lands.
      fileExists(normPath, currentSessionRef.current).then((exists) => {
        if (!exists) return;
        setTrackedFiles((prev) => {
          const dup = prev.some(
            (f) =>
              f.path === normPath ||
              sameTrackedFile(f.path, normPath, workspace) ||
              (basename(f.path) === basename(normPath) &&
                (!f.path.includes('/') || !normPath.includes('/')))
          );
          if (dup) return prev;
          return [
            ...prev,
            {
              path: normPath,
              name: basename(normPath),
              op,
              lastSeen: Date.now(),
              truncated,
              turnId,
              sourceTool,
            },
          ];
        });
      });
    },
    []
  );

  useEffect(() => {
    // True only on an actual sessionKey change.  loadTrigger can bump alone
    // (e.g. bridge became ready) to reload the SAME session — in that case we
    // must NOT wipe the user's typed input / attachments / streaming state,
    // which this PR's new explicit resets would otherwise do on every reload.
    const _sessionChanged = currentSessionRef.current !== sessionKey;
    // Snapshot the session we're leaving so switching back restores the
    // live-rendered thinking/reply instantly.  While on a session its events
    // take the LIVE path (in `messages`), never moduleInFlightCache — so
    // without this snapshot they'd be lost when setMessages([]) runs below.
    if (_sessionChanged && currentSessionRef.current) {
      const leavingKey = currentSessionRef.current;
      moduleMessagesSnapshot.set(leavingKey, messagesRef.current);
      // GC 空会话（对齐 WorkBuddy）：没提问的新对话切走后不应残留在会话
      // 列表。messagesRef 为空只说明当前渲染无内容——加载是异步的，切走太
      // 快时磁盘可能已有消息，所以删前用后端再确认一次，避免误删。
      if (messagesRef.current.length === 0) {
        window.miqi.sessions
          .get(leavingKey)
          .then((d) => {
            if (d && Array.isArray(d.messages) && d.messages.length > 0) return null;
            // get 返回前用户可能已切回 leavingKey 并发出首条消息（已落盘）；
            // 此刻 currentSessionRef 若已指回该 key，删除会误删这条新会话
            // （CodeRabbit）。竞态窗口极窄但护栏成本为零。
            if (currentSessionRef.current === leavingKey) return null;
            return window.miqi.sessions.delete(leavingKey);
          })
          .then(() => onSessionsChanged?.())
          .catch(() => {
            /* bridge 离线时跳过清理，空会话保留 */
          });
      }
    }
    // Update the ref FIRST so the per-handler session_key guard on the
    // CURRENT listeners (from the previous session's handleSend) sees the
    // new session.  Crucially, do NOT call cleanupListeners() here — the
    // old listeners must survive the session switch so they can route
    // orphan events into inFlightCacheRef.  They are torn down naturally
    // by the next handleSend() or by the unmount cleanup effect (#378).
    currentSessionRef.current = sessionKey;
    currentThreadIdRef.current = null; // Reset on session change
    toolArgsByCallId.current.clear(); // drop tool-call args from the previous session
    if (_sessionChanged) {
      setHistoryLoaded(false);
      // ── Instant restore ─────────────────────────────────────────
      // sessions.get() is async, so clearing messages here and waiting would
      // leave a blank window until it resolves.  Restore the snapshot of this
      // session (from when we last left it) or its cached in-flight events
      // NOW so the thinking/reply appears immediately, no blank flash.
      const _targetCache = inFlightCacheRef.current.get(sessionKey);
      const _snapshot = moduleMessagesSnapshot.get(sessionKey);
      // A turn is "live" if (a) cached progress events arrived while we were
      // away with no terminal event yet, OR (b) the snapshot still shows
      // in-progress thinking.  (b) matters because progress events that
      // arrived BEFORE the switch-away took the live path (rendered into
      // messages + snapshot) and never entered the cache — the cache alone
      // would wrongly report "no live turn" and kill the thinking indicator.
      const _cacheLiveTurn =
        !!_targetCache &&
        _targetCache.events.some((e) => e.type === 'progress') &&
        !_targetCache.events.some(
          (e) => e.type === 'final' || e.type === 'error' || e.type === 'aborted'
        );
      let _snapLiveTurn = false;
      if (_snapshot && _snapshot.length > 0) {
        const _snapLastUser = (() => {
          for (let _i = _snapshot.length - 1; _i >= 0; _i -= 1) {
            if (_snapshot[_i].role === 'user') return _i;
          }
          return -1;
        })();
        const _after = _snapshot.slice(_snapLastUser + 1);
        const _hasThinking = _after.some((_m) => _m.role === 'progress' || _m.role === 'subagent');
        const _hasFinalReply = _after.some(
          (_m) => _m.role === 'assistant' && String(_m.content ?? '').trim().length > 0
        );
        // A turn is also live if the typewriter is still revealing a reply
        // (the assistant bubble holds partial text).  Many backends emit no
        // progress events — the "thinking" the user sees is the half-typed
        // assistant reply.  Check revealBySession: if this session still has
        // a running typewriter (final not done), keep streaming on.
        const _reveal = revealBySession.get(sessionKey);
        // Typewriter is active while it still has text to reveal, regardless
        // of whether finalDone is set (finalDone just means content arrived).
        const _typewriterActive =
          !!_reveal && _reveal.displayed.length < _reveal.fullContent.length;
        _snapLiveTurn = (_hasThinking && !_hasFinalReply) || _typewriterActive;
      }
      // Authoritative "is this session still generating?" — handleSend adds the
      // key, final/error/aborted removes it.  This survives every phase of a
      // turn, including early thinking where the snapshot holds only the user
      // bubble and no progress text / typewriter exists yet — the exact phase
      // where the heuristics below (cache progress, snapshot thinking, active
      // typewriter) all report false and the thinking indicator wrongly dies.
      const _hasLiveTurn = streamingBySession.has(sessionKey) || _cacheLiveTurn || _snapLiveTurn;
      if (_snapshot && _snapshot.length > 0) {
        // Exact last-rendered view — best fidelity.
        setMessages(_snapshot);
        setHistoryLoaded(true);
      } else if (_targetCache && _targetCache.events.length > 0) {
        setMessages(cachedEventsToMessages(_targetCache.events, reasoningMode));
        setHistoryLoaded(true);
      } else {
        setMessages([]);
      }
      setSessionUpdatedAt(null);
      // The component survives session switches (App.tsx no longer keys it by
      // sessionKey), so state that used to be wiped by remount must be reset
      // here explicitly — otherwise a previous session's attachments, inline
      // exec output, or streaming flag leak into the newly opened session.
      setAttachments([]);
      setExecOutputs({});
      // A turn is still live only if progress events were cached while we were
      // away AND no terminal event (final/error/aborted) arrived yet — a cached
      // final means the backend already finished and the persisted history
      // renders the reply, so the spinner must stay off.  Unconditionally
      // setting streaming false here made the spinner vanish on switch-back
      // until the reply bubble appeared.
      if (_hasLiveTurn) {
        setStreaming(true);
      } else {
        setStreaming(false);
      }
      setCurrentReqId(null);
      composerRef.current?.clear();
      setThreads([{ threadId: 'main', agentType: 'main', label: '主线程' }]);
      setActiveThreadId('main');
      setPlan(null);
      setPlanOpen(false);
      fullContentRef.current = '';
      // #612 session-rename state must reset per session too (the component no
      // longer remounts on sessionKey change, so without this a rename dialog
      // or custom title from the previous session would leak into this one).
      setCustomTitle(null);
      setEditingTitle(false);
      // NOTE: do NOT clear trackedFiles here — clearing before the async
      // load completes causes a flash of "No files yet" on every session
      // switch.  If the bridge is not ready yet, sendSafe returns null and
      // we would permanently lose the display.  Instead we replace atomically
      // inside load() after the bridge responds.
      justOpened.current = true;
      userScrolledUp.current = false; // reset for new session
    }
    const load = async () => {
      // ── Retry with exponential backoff ──────────────────────────
      // On startup the bridge may not be running yet → sendSafe
      // returns null.  Even when running, transient IPC failures
      // can occur.  Retry so that a slow bridge start or a one-off
      // error doesn't leave the session permanently blank (#480).
      const MAX_RETRIES = 10;
      const BASE_DELAY_MS = 500;
      const MAX_DELAY_MS = 10_000;

      let detail: unknown = null;
      let lastErr: unknown = null;

      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        if (currentSessionRef.current !== sessionKey) return;

        try {
          const pw = pendingWorkspace?.current;
          // Only consume if it belongs to this session — prevents
          // cross-session races and retry-drop on transient failures.
          if (pw && pw.sessionKey === sessionKey) {
            pendingWorkspace.current = null;
            detail = await window.miqi.sessions.get(sessionKey, { workspace: pw.workspace } as any);
          } else {
            detail = await window.miqi.sessions.get(sessionKey);
          }
        } catch (err) {
          lastErr = err;
        }

        if (detail != null) break; // got data — stop retrying

        if (attempt < MAX_RETRIES - 1) {
          const delay = Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS);
          console.warn(
            `[ChatConsole] Load attempt ${attempt + 1}/${MAX_RETRIES} returned null, retrying in ${delay}ms…`
          );
          await new Promise((r) => setTimeout(r, delay));
        }
      }

      if (currentSessionRef.current !== sessionKey) return;

      if (detail == null) {
        console.warn(
          '[ChatConsole] Failed to load session data after retries, last error:',
          lastErr
        );
        // #570: exhausted retries used to be silent — the spinner was swapped
        // for the blank empty state with no explanation.  Surface an explicit
        // error so the user knows the session failed to load.
        setHistoryLoaded(true);
        // #872: don't erase in-flight content — a message sent while the bridge
        // was still down (with any thinking/tool rows already streamed) would
        // otherwise vanish.  Retain the full current-turn sequence ahead of the
        // error banner.  Only the ACTIVE turn's assistant half-reply is dropped
        // (no backend to replay it); earlier history — including prior turns'
        // assistant replies — is kept unchanged (CodeRabbit #891).
        const _errInFlight = streamingBySession.has(sessionKey)
          ? (() => {
              const _msgs = messagesRef.current;
              // Locate the last user message; anything before it is settled
              // history and is preserved verbatim.
              let _lastUserIdx = -1;
              for (let _i = _msgs.length - 1; _i >= 0; _i -= 1) {
                if (_msgs[_i].role === 'user') {
                  _lastUserIdx = _i;
                  break;
                }
              }
              if (_lastUserIdx < 0) return _msgs; // no user yet — keep as-is
              // Keep history up to & including the last user; drop assistant
              // content after it (the half-typed reply of the active turn),
              // but keep non-assistant rows after it (thinking/tool lines).
              return [
                ..._msgs.slice(0, _lastUserIdx + 1),
                ..._msgs.slice(_lastUserIdx + 1).filter((m) => m.role !== 'assistant'),
              ];
            })()
          : [];
        setMessages([
          ..._errInFlight,
          {
            role: 'error',
            content: '会话加载失败：无法连接后台服务，请稍后重试或重启应用。',
            action: 'retry-load',
            actionLabel: '重试',
            timestamp: Date.now(),
          },
        ]);
        // #480: keep trying in the background — a slow-starting bridge will
        // eventually satisfy sessions.get, and the successful load() overwrites
        // this error bubble via setMessages(merged) below.  Guards against a
        // permanent dead-end when the user's only reload trigger (runtimeReadyKey,
        // event-driven with no polling) fired once while the bridge was still
        // warming up.
        const t = setTimeout(() => {
          if (currentSessionRef.current === sessionKey) load();
        }, 10_000);
        return;
      }

      try {
        const rawMsgs: any[] = (detail as any)?.messages ?? [];
        const wsFromSession = (detail as any)?.workspace ?? null;
        onWorkspaceLoaded?.(wsFromSession);
        const uiMsgs = sessionMsgsToUi(rawMsgs);

        // ── Merge snapshot + cached in-flight events with history ──
        // sessions.get() is the authoritative, deduped source — build merged
        // FROM it (uiMsgs) so the persisted full reply is never duplicated.
        // The snapshot carries live-rendered THINKING (progress/error/subagent,
        // never persisted) that sessions.get lacks; insert it right after the
        // last user message so the thinking the user saw before switching away
        // stays visible above the reply.
        //
        // If this session's typewriter is still active (a reply is being
        // revealed), KEEP the snapshot's assistant bubble — instant restore
        // showed it, and rebuilding from history would drop the half-typed
        // reply or shift it, making the thinking/reply appear to jump.  The
        // module-level typewriter resumes and completes it.
        var cached = inFlightCacheRef.current.get(sessionKey);
        const _snap = moduleMessagesSnapshot.get(sessionKey);
        // The typewriter "needs to keep working" whenever it has content to
        // present — i.e. fullContent is non-empty.  Don't key on `displayed <
        // fullContent`: the RAF chain keeps advancing `displayed` even while
        // the UI is skipped, so by the time the user switches back `displayed`
        // may already equal fullContent while the on-screen bubble is still
        // half-typed.  In that case the revealNext completion branch syncs the
        // full text to the bubble — but ONLY if we keep the RAF running (or at
        // least don't cancel it below).
        const _revealState = revealBySession.get(sessionKey);
        const _typewriterHasContent = !!_revealState && _revealState.fullContent.length > 0;
        const _revealActive =
          _typewriterHasContent &&
          _revealState!.displayed.length < _revealState!.fullContent.length;
        var merged = uiMsgs.slice();
        if (_typewriterHasContent && _snap && _snap.length > 0) {
          // Keep the snapshot (which holds the partial reply the typewriter is
          // completing) so the user doesn't see a jump — BUT the snapshot may
          // predate the persisted full reply (sessions.get already has it).
          // Merge: start from uiMsgs (authoritative full history) and carry the
          // snapshot's in-progress thinking/subagent lines above the reply.
          // A bare snapshot-only merge would DROP the persisted full reply,
          // leaving the bubble blank until a restart.
          const _snapNonReply: Message[] = [];
          const _snapLastUser = (() => {
            for (let _i = _snap.length - 1; _i >= 0; _i -= 1) {
              if (_snap[_i].role === 'user') return _i;
            }
            return -1;
          })();
          for (const _sm of _snap.slice(_snapLastUser + 1)) {
            if (_sm.role === 'progress' || _sm.role === 'error' || _sm.role === 'subagent') {
              _snapNonReply.push(_sm);
            }
          }
          const _snapNonReplyDeduped = dedupeSnapshotRows(merged, _snapNonReply);
          if (_snapNonReplyDeduped.length > 0) {
            const insIdx = (() => {
              for (let _i = merged.length - 1; _i >= 0; _i -= 1) {
                if (merged[_i].role === 'user') return _i + 1;
              }
              return merged.length;
            })();
            merged.splice(insIdx, 0, ..._snapNonReplyDeduped);
          }
        }

        if (_snap && _snap.length > 0) {
          const _snapThinking: Message[] = [];
          const lastUserIdx = (() => {
            for (let _i = _snap.length - 1; _i >= 0; _i -= 1) {
              if (_snap[_i].role === 'user') return _i;
            }
            return -1;
          })();
          for (const _sm of _snap.slice(lastUserIdx + 1)) {
            if (_sm.role === 'progress' || _sm.role === 'error' || _sm.role === 'subagent') {
              _snapThinking.push(_sm);
            }
          }
          if (_snapThinking.length > 0 && !_revealActive) {
            const insIdx = (() => {
              for (let _i = merged.length - 1; _i >= 0; _i -= 1) {
                if (merged[_i].role === 'user') return _i + 1;
              }
              return merged.length;
            })();
            // Audit #1: if the turn finished while we were away (cached final
            // present), the snapshotted live thinking block must be closed —
            // otherwise a permanently-stuck "思考中…" appears under the answer.
            const turnDone = !!cached?.events.some((e) => e.type === 'final');
            const _snapThinkingClean = turnDone
              ? _snapThinking.map((_sm) =>
                  _sm.isLiveReasoning ? { ..._sm, isLiveReasoning: false } : _sm
                )
              : _snapThinking;
            // Dedupe against already-merged rows so a switch-back never
            // duplicates the "已深度思考" header.
            merged.splice(insIdx, 0, ...dedupeSnapshotRows(merged, _snapThinkingClean));
          }
        }

        // Thinking carried by the snapshot (live-rendered, never persisted)
        // is already inside `merged`.  Cached events add post-switch progress.
        if (cached && cached.events.length > 0) {
          const _split = splitCachedMessages(cached.events);
          const _finalContent = _split.finalReply ?? '';
          // If the final was persisted, sessions.get already renders it —
          // don't append a duplicate from cache.  When the typewriter is
          // active (_revealActive) the partial reply is on screen and the
          // typewriter will complete it — also skip the cached final so we
          // don't stack a partial bubble + a full duplicate.
          const _alreadyPersisted =
            _revealActive ||
            (_finalContent !== '' &&
              merged.some(
                (_m) => _m.role === 'assistant' && String(_m.content ?? '') === _finalContent.trim()
              ));
          // 计费通知等 thinking 行独立于 final 去重：final 已持久化时
          // 也要回放（否则切会话返回后只看到回复、看不到"已扣分/余额
          // 不足"提示）。快照行已并入 merged，按 toolCallId/内容前缀去重。
          for (const _ctm of _split.thinking) {
            const _dup = merged.some(
              (_m) =>
                (_m.role === 'progress' &&
                  ((_m.toolCallId != null && _m.toolCallId === _ctm.toolCallId) ||
                    _m.content.startsWith(_ctm.content) ||
                    _ctm.content.startsWith(_m.content))) ||
                (_m.role === 'error' && _ctm.role === 'error' && _m.content === _ctm.content)
            );
            if (!_dup) merged.push(_ctm);
          }
          if (!_alreadyPersisted) {
            if (_split.finalReply) {
              merged.push({
                role: 'assistant',
                content: _split.finalReply,
                timestamp: Date.now(),
              });
            }
            // #834 / CR #856-2 + #856-6: the cached final carries the
            // server-measured thinking proxy, but only role==='progress'
            // renders a ThinkBlock.  Attach it to the LAST reasoning block of
            // the CURRENT turn (scan stops at the last user boundary, matching
            // insertStandaloneReasoning) — or insert a standalone block
            // BEFORE the final assistant reply so the thinking stays visually
            // above the answer.
            if (_split.finalReasoning) {
              let _attached = false;
              for (let _ti = merged.length - 1; _ti >= 0; _ti -= 1) {
                if (merged[_ti].role === 'user') break; // current-turn boundary
                const _tm = merged[_ti];
                if (_tm.role === 'progress' && _tm.reasoning) {
                  if (_tm.reasoningElapsedS === undefined) {
                    merged[_ti] = {
                      ..._tm,
                      reasoningElapsedS: _split.finalReasoningElapsedS,
                    };
                  }
                  _attached = true;
                  break;
                }
              }
              if (!_attached) {
                const _standalone: Message = {
                  role: 'progress',
                  content: _split.finalReasoning,
                  reasoning: _split.finalReasoning,
                  reasoningElapsedS: _split.finalReasoningElapsedS,
                  // #905 review: an in-flight cached turn was sent in the
                  // CURRENT mode — stamp it so the restored block shows the
                  // correct 🚀/🧠 instead of whatever mode is live later.
                  reasoningMode,
                  timestamp: Date.now(),
                };
                // Insert before the final assistant reply (the last non-user
                // message of the turn), keeping the visual order thinking →
                // answer.
                let _insAt = merged.length;
                for (let _ti = merged.length - 1; _ti >= 0; _ti -= 1) {
                  if (merged[_ti].role === 'user') {
                    _insAt = _ti + 1;
                    break;
                  }
                }
                merged.splice(_insAt, 0, _standalone);
              }
            }
          }
          // Exec inline output → merge into execOutputs for the session
          for (var _ec = 0; _ec < cached.events.length; _ec += 1) {
            const _eev = cached.events[_ec];
            if (_eev.type === 'progress') {
              const _epd = _eev.data as ChatProgress;
              if (_epd?.stream && _epd?.delta && _epd?.tool_call_id) {
                setExecOutputs(function (_prev) {
                  var _cur = _prev[_epd.tool_call_id!] || { stdout: '', stderr: '', running: true };
                  var _out = _cur.stdout;
                  var _err = _cur.stderr;
                  if (_epd.stream === 'stdout') {
                    _out += _epd.delta || '';
                  } else {
                    _err += _epd.delta || '';
                  }
                  return {
                    ..._prev,
                    [_epd.tool_call_id!]: { stdout: _out, stderr: _err, running: true },
                  };
                });
              } else if (_epd?.type === 'doc_progress' && _epd?.file) {
                // Apply attachment status directly to `merged` — a nested
                // setMessages updater would be overwritten by the plain
                // setMessages(merged) below, silently dropping the restore.
                merged = merged.map(function (_m) {
                  if (_m.role === 'user' && _m.attachments) {
                    var _upd = _m.attachments.map(function (_a) {
                      if (_a.name !== _epd.file || _a.type !== 'document') return _a;
                      var _st: Attachment['status'] =
                        _epd.stage === 'ready' || _epd.stage === 'done'
                          ? 'done'
                          : _epd.stage === 'error'
                            ? 'error'
                            : 'parsing';
                      return {
                        ..._a,
                        status: _st,
                        parseError: _st === 'error' ? (_epd.message ?? '') : _a.parseError,
                      };
                    });
                    return { ..._m, attachments: _upd };
                  }
                  return _m;
                });
              }
            }
          }
          inFlightCacheRef.current.delete(sessionKey);
        }
        // 单一判定：把 messagesRef 每条用户乐观行与其 merged 持久化副本一一
        // 对应（谓词见 _markUserTwinMatches），缓存 final 门控与保留块共用。
        const _userTwinMatches = _markUserTwinMatches(messagesRef.current, merged);
        // A cached final (or persisted history) now renders the full reply —
        // mark the session so the old send listener's live onFinal doesn't
        // append a duplicate when it fires for the same reply.
        if (cached && cached.events.some((e) => e.type === 'final')) {
          // #891 深度审阅 #3：add 与 delete 必须同一守卫——load 窗口内发了
          // 新消息时，不能重新打上"final 已处理"标记（否则新回合的 live
          // final 会被吞、回复不渲染）。
          const _newInflightUser = messagesRef.current.some(
            (m, i) => m.role === 'user' && !_userTwinMatches[i]
          );
          if (!_newInflightUser) {
            finalHandledSessions.add(sessionKey);
            // Audit #3: the cached-final path never runs the live cleanup —
            // clear the streaming flag here so the stop button disappears and
            // the 60s watchdog can't re-arm over a completed turn.  Only clear
            // the flag when every user message in `messagesRef` is already
            // persisted (any-match + time proximity, #891 深度审阅 #1/#2 ——
            // 快照恢复的旧历史行各自都有相近副本，不会被误判为 in-flight）。
            streamingBySession.delete(sessionKey);
          }
        }
        // If a cached final was merged, the FULL reply is already rendered in
        // `merged` — stop this session's typewriter so the revealNext RAF loop
        // (which pauses across switches) doesn't keep revealing over it and
        // duplicate the bubble.  Determine "full reply already rendered" by
        // whether the LAST assistant message equals the typewriter's full
        // content; if merged only holds a half-typed reply, keep the RAF so
        // revealNext completes it.
        const _revealNow = revealBySession.get(sessionKey);
        const _lastAsstContent = (() => {
          for (let _i = merged.length - 1; _i >= 0; _i -= 1) {
            if (merged[_i].role === 'assistant') return String(merged[_i].content ?? '');
          }
          return '';
        })();
        const _mergedHasFullReply =
          !!_revealNow &&
          _revealNow.fullContent.length > 0 &&
          _lastAsstContent === _revealNow.fullContent;
        if (
          _revealNow &&
          (_mergedHasFullReply ||
            (!_typewriterHasContent && (_revealNow.finalDone || _revealNow.displayed.length > 0)))
        ) {
          if (_revealNow.animId !== null) {
            cancelAnimationFrame(_revealNow.animId);
            _revealNow.animId = null;
          }
          if (_revealNow.finalDone || _mergedHasFullReply) {
            setStreaming(false);
          }
        }
        // #740/#886: interrupted-turn snapshots — half-generated replies the
        // user saw before an interruption (process exit / abort) — render as
        // resumable assistant bubbles (中断卡 + 继续执行/重新开始).  Insert each
        // at its chronological position (after its own user message) so a
        // later successful retry appends AFTER the interrupted round instead of
        // the card landing at the end of history.
        const _interruptedTurns = (detail as any)?.interrupted_turns ?? [];
        merged = insertInterruptedTurns(
          merged,
          Array.isArray(_interruptedTurns) ? _interruptedTurns : []
        );
        // #872: preserve in-flight streaming content across load()'s overwrite.
        // With the render gate relaxed, a message sent while the session is
        // still loading is now VISIBLE — but `merged` is built from persisted
        // history only, so `setMessages(merged)` would erase the optimistic user
        // bubble AND any thinking/tool rows already streamed, leaving the reply
        // with no question and the thinking half-rendered.  Re-append every
        // non-assistant message not yet in the persisted history so the stream
        // that follows still lands after it.
        if (streamingBySession.has(sessionKey)) {
          // Dedup key: role + content, refined with time proximity for user
          // messages (#891 深度审阅)。持久化副本与前端气泡同属机器时钟
          // （后端 ISO 经 sessionMsgsToUi 转 epoch ms），同一次发送的收发
          // 时间差秒级。
          // - 用户行：与其专属持久化副本一一对应（_markUserTwinMatches，整体
          //   快照匹配而非只比最后一条——否则旧历史行被整段重复渲染，审阅 #1；
          //   时间相近限定保证跨轮重复的旧文本不被误判，审阅 #5）。同文本多条
          //   气泡共有一条持久化副本时只认领一条，余下按未落盘保留（#891 复核）。
          // - error 行：不保留——错误横幅是 load 失败的瞬时 UI，成功的
          //   retry load 应移除而非被永久嵌入历史（审阅 #8）。
          // - thinking/tool 行：保持内容去重（部分更新导致的瞬时双副本为
          //   已知限制，审阅 #4）。
          const _inFlight = messagesRef.current.filter((m, i) => {
            if (m.role === 'assistant' || m.role === 'error') return false;
            if (m.role === 'user') {
              return !_userTwinMatches[i];
            }
            return !merged.some(
              (pm) => pm.role === m.role && String(pm.content) === String(m.content)
            );
          });
          if (_inFlight.length > 0) merged.push(..._inFlight);
        }
        setMessages(merged);
        // #956: the persisted history is the authoritative answer.  When it
        // ends with a completed reply and nothing is in flight (no live send
        // for this session, no progress-without-final cached while we were
        // away), the switch-back "生成中" state must not survive — otherwise a
        // folder session whose reply was restored from the bound workspace
        // root keeps the send button stuck as "中断当前生成并发送" forever.
        const _histEndsComplete = (() => {
          for (let _i = uiMsgs.length - 1; _i >= 0; _i -= 1) {
            const _m = uiMsgs[_i];
            if (_m.role === 'user') return false; // last turn has no reply yet
            if (_m.role === 'assistant' && String(_m.content ?? '').trim().length > 0) {
              return true;
            }
          }
          return false;
        })();
        const _cacheStillLive =
          !!cached &&
          cached.events.some((e) => e.type === 'progress') &&
          !cached.events.some(
            (e) => e.type === 'final' || e.type === 'error' || e.type === 'aborted'
          );
        if (_histEndsComplete && !streamingBySession.has(sessionKey) && !_cacheStillLive) {
          setStreaming(false);
        }
        // Snapshot is now reconciled into `merged` — clear it so a later
        // load() (loadTrigger refresh) doesn't re-append stale transient
        // progress on top of history.
        moduleMessagesSnapshot.delete(sessionKey);
        setSessionUpdatedAt((detail as any)?.updated_at ?? null);
        // Restore tracked files from dedicated tracked_files.json
        let tfList: any[] = [];
        try {
          const tfResult = await window.miqi.sessions.getTrackedFiles(sessionKey);
          if (currentSessionRef.current !== sessionKey) return;
          tfList = (tfResult as any)?.tracked_files ?? [];
        } catch {
          // backend failure is non-fatal — fall through to message extraction
        }
        // Also extract tracked files from session messages (fallback when
        // tracked_files.json is empty — agent tools don't persist there).
        const fromMessages = extractTrackedFilesFromMessages(rawMsgs);
        // Message-extracted entries can be phantoms (referenced but never
        // saved) — keep only files that exist on disk before merging.
        const existingFromMessages: TrackedFile[] = [];
        for (const f of fromMessages) {
          if (await fileExists(f.path, sessionKey)) existingFromMessages.push(f);
        }
        if (currentSessionRef.current !== sessionKey) return;
        // Merge: backend data takes priority, messages fill gaps. Collapses a
        // bare-filename entry and a full-path entry pointing at the same file.
        const backendMapped = (tfList as any[]).map((f: any) => ({
          path: (f.path as string).replace(/\\/g, '/'),
          name: f.name,
          op: f.op,
          lastSeen: f.lastSeen ?? Date.now(),
          // #1104: declare_result_files 写入的显式结果标记
          result: f.result === true,
        }));
        setTrackedFiles(mergeTrackedFiles(existingFromMessages, backendMapped, workspace));
        // #879 ③ 冷启动恢复：从消息重新推导「回合 → 来源」，供文件卡片显示相关引用。
        setTurnSourcesMap(extractTurnSourcesFromMessages(rawMsgs));
        // 回合序号与会话内 user 消息数对齐（turnSeqRef 跨会话累计，需重置），
        // 否则实时追踪的 turnId 与恢复推导的序号错位。持久化 rawMsgs 可能不含
        // 尚在乐观阶段的 user 消息——取「持久化数 / 可见数」较大者，避免覆盖
        // 活跃 turn 已递增的序号（CodeRabbit）。
        const persistedTurns = (rawMsgs ?? []).filter((m) => m?.role === 'user').length;
        const visibleTurns = messagesRef.current.filter((m) => m.role === 'user').length;
        turnSeqRef.current = Math.max(persistedTurns, visibleTurns) - 1;

        // ── Issue #490: resume this session's most-recent active thread ──
        // currentThreadIdRef is reset to null on every sessionKey/remount
        // (line above) and is never persisted, so without this the next
        // send would call thread/start → mint a fresh random thread_id,
        // orphaning the prior thread's SQLite history and making the model
        // "forget" earlier turns even though the UI still shows them.
        //
        // Look up stored threads for this session and reuse the most
        // recently updated one so chat.send continues accumulating into
        // the SAME (session_id, thread_id). A brand-new session has no
        // stored threads → ref stays null → first send still creates one.
        // This keeps thread isolation intact (B/C content is never pulled
        // into A); only A's own history is reloaded.
        // Guard with a short timeout (Promise.race, same shape as the
        // thread/start guard below) so a slow/hung backend can't block the
        // surrounding flow from reaching setHistoryLoaded(true). This is a
        // best-effort optimization; on timeout or rejection we fall through to
        // the existing first-send thread/start path (ref stays null) — the
        // session still loads, just without thread reuse. 10s is far shorter
        // than thread/start's 30s (which budgets sandbox first-init) because
        // threads/list is a cheap SQLite read, not a sandbox spawn.
        let resumeTimer: ReturnType<typeof setTimeout> | null = null;
        try {
          const listRes = await Promise.race([
            window.miqi.threads.list({
              session_key: currentSessionRef.current,
            }),
            new Promise<never>((_, reject) => {
              resumeTimer = setTimeout(() => reject(new Error('thread/list timeout')), 10_000);
            }),
          ]);
          if (resumeTimer) clearTimeout(resumeTimer);
          if (currentSessionRef.current !== sessionKey) return; // switched away
          // backend `Page.to_dict()` (thread_protocol.py:94) envelopes rows
          // under `data`; read via extractThreadListRows so resume matches
          // the real backend shape (and is unit-tested end-to-end).
          const listRows = extractThreadListRows(listRes);
          const resumeId = pickThreadToResume(listRows);
          if (resumeId) {
            currentThreadIdRef.current = resumeId;
          }
        } catch (err) {
          // Non-fatal: timeout or rejection → ref stays null → first send
          // still uses the thread/start path. Don't block rendering.
          if (resumeTimer) clearTimeout(resumeTimer);
          console.warn('[ChatConsole] Failed to resume thread:', err);
        }
      } catch (err) {
        console.warn('[ChatConsole] Failed to load session data:', err);
      }
      setHistoryLoaded(true);
    };
    load();
    // loadTrigger lets the parent force a reload (e.g. after bridge becomes ready)
    // retryTick lets the "重试" button force a reload after load exhaustion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey, loadTrigger, retryTick]);

  // Scroll to bottom: (a) unconditionally after opening a session,
  // (b) during streaming only if the user hasn't manually scrolled up.
  // 空态（欢迎页）例外：它比可视区高，粘底会把品牌区顶出屏幕（issue #962 实测
  // scrollTop 落在最大值，logo 与标题看不见）。进入空态时会有一整块 DOM 替换
  // （「正在连接…」→ 欢迎页），浏览器的滚动锚定会把位置挪走且时机不定，所以这里
  // 在随后两帧再钉一次顶部；之后不再干预，用户自己滚下去读「场景方案」不受影响。
  // justOpened 不消费，留给第一条消息再触发一次粘底。
  useEffect(() => {
    if (!historyLoaded) return;
    const el = scrollRef.current;
    if (!el) return;
    if (messages.length === 0) {
      el.scrollTop = 0;
      userScrolledUp.current = false;
      const pin = () => {
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
      };
      const raf = requestAnimationFrame(pin);
      const timer = window.setTimeout(pin, 250);
      return () => {
        cancelAnimationFrame(raf);
        window.clearTimeout(timer);
      };
    }
    if (justOpened.current) {
      justOpened.current = false;
      el.scrollTop = el.scrollHeight + el.clientHeight; // clamped to max
      userScrolledUp.current = false;
    } else if (!userScrolledUp.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [historyLoaded, messages]);

  // Detect manual scroll-up / scroll-back-to-bottom
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (distFromBottom < 40) {
        userScrolledUp.current = false;
      } else if (distFromBottom > 80) {
        userScrolledUp.current = true;
      }
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  // Persistent listener for subagent results — must NOT be cleaned up
  // when the main chat completes, because subagents finish asynchronously.
  useEffect(() => {
    const unsub = window.miqi.chat.onSubagentResult((data: ChatSubagentResult) => {
      if (data.session_key && data.session_key !== currentSessionRef.current) return;
      const statusIcon = data.status === 'ok' ? '✅' : '❌';
      const label = data.label || data.task_id;
      const content = `${statusIcon} Subagent "${label}" ${data.status === 'ok' ? 'completed' : 'failed'}:\n\n${data.result}`;
      setMessages((prev) => [...prev, { role: 'subagent', content, timestamp: Date.now() }]);
    });
    return () => {
      unsub();
    };
  }, []);

  const clearFinalCleanupTimer = useCallback(() => {
    if (finalCleanupTimerRef.current) {
      clearTimeout(finalCleanupTimerRef.current);
      finalCleanupTimerRef.current = null;
    }
  }, []);

  const showShareFeedback = useCallback((status: 'copied' | 'exported' | 'context') => {
    if (shareFeedbackTimerRef.current) {
      clearTimeout(shareFeedbackTimerRef.current);
    }
    setShareStatus(status);
    shareFeedbackTimerRef.current = setTimeout(() => {
      setShareStatus('idle');
      shareFeedbackTimerRef.current = null;
    }, 2000);
  }, []);

  const cleanupListeners = useCallback(
    (onlyMine?: Array<() => void>) => {
      clearFinalCleanupTimer();
      if (shareFeedbackTimerRef.current) {
        clearTimeout(shareFeedbackTimerRef.current);
        shareFeedbackTimerRef.current = null;
      }
      if (onlyMine) {
        // Identity-scoped: unsubscribe THIS invocation's listeners only.  The
        // shared unsubsRef may already point at a NEWER send's listeners
        // (overlapping sends across sessions) — those must survive.
        for (const unsub of onlyMine) unsub();
        if (unsubsRef.current === onlyMine) {
          unsubsRef.current = [];
          unsubsSessionRef.current = null;
        }
        return;
      }
      for (const unsub of unsubsRef.current) unsub();
      unsubsRef.current = [];
      unsubsSessionRef.current = null;
    },
    [clearFinalCleanupTimer]
  );

  // useCallback (#1042): stable prop for the memoized Composer.
  const handleAttachClick = useCallback(() => fileInputRef.current?.click(), []);

  // 统一把 File 转成 Attachment：input change / 剪贴板 / 拖拽共用。
  const attachFromFile = useCallback(
    (file: File, actionId: string, gen: number = sessionGenRef.current): boolean => {
      if (file.size > MAX_ONE_ATTACHMENT_BYTES) return false;
      if (!acceptInAttachAction(actionId, `${file.name}:${file.size}`)) return false;
      {
        const isImage = file.type.startsWith('image/');
        const isDocument = DOCUMENT_SUFFIXES_RE.test(file.name);
        const isTextLike = TEXT_SUFFIXES_RE.test(file.name) || file.type.startsWith('text/');

        if (isTextLike && !isDocument) {
          // Plain text files — read directly as text
          const reader = new FileReader();
          // 读取失败：释放已预留的字节，避免预留位泄漏把后续附件挡住
          reader.onerror = () => releaseReservation(file.size);
          reader.onload = () =>
            commitIfGen(
              gen,
              (prev) => [
                ...prev,
                {
                  name: file.name,
                  type: 'text',
                  content: reader.result as string,
                  size: file.size,
                },
              ],
              file.size
            );
          reader.readAsText(file);
        } else if (isTextLike && isDocument) {
          // Markdown/text files detected as documents — read as text AND as base64 for server fallback
          const reader = new FileReader();
          // 读取失败：释放已预留的字节，避免预留位泄漏把后续附件挡住
          reader.onerror = () => releaseReservation(file.size);
          reader.onload = () => {
            const base64 = (reader.result as string).split(',')[1];
            const textContent = new TextDecoder().decode(
              Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
            );
            commitIfGen(
              gen,
              (prev) => [
                ...prev,
                {
                  name: file.name,
                  type: 'document',
                  dataBase64: base64,
                  content: textContent,
                  dataUrl: reader.result as string,
                  size: file.size,
                  mimeType: file.type || getMimeTypeFromName(file.name),
                  status: 'pending' as const,
                },
              ],
              file.size
            );
          };
          reader.readAsDataURL(file);
        } else if (isDocument) {
          const reader = new FileReader();
          // 读取失败：释放已预留的字节，避免预留位泄漏把后续附件挡住
          reader.onerror = () => releaseReservation(file.size);
          reader.onload = () => {
            const base64 = (reader.result as string).split(',')[1];
            const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
            // PDF/MD/text parse instantly client-side → done; Office/RTF needs server → pending
            const isServerParsed = /^(docx|doc|pptx|ppt|xlsx|xls|odt|odp|ods|rtf)$/i.test(ext);
            const parseStatus: Attachment['status'] = isServerParsed ? 'pending' : 'done';

            commitIfGen(
              gen,
              (prev) => [
                ...prev,
                {
                  name: file.name,
                  type: 'document',
                  dataUrl: reader.result as string,
                  dataBase64: base64,
                  size: file.size,
                  mimeType: file.type || getMimeTypeFromName(file.name),
                  status: parseStatus,
                },
              ],
              file.size
            );
          };
          reader.readAsDataURL(file);
        } else if (isImage) {
          const reader = new FileReader();
          // 读取失败：释放已预留的字节，避免预留位泄漏把后续附件挡住
          reader.onerror = () => releaseReservation(file.size);
          reader.onload = () =>
            commitIfGen(
              gen,
              (prev) => [
                ...prev,
                {
                  name: file.name,
                  type: 'image',
                  dataUrl: reader.result as string,
                  size: file.size,
                },
              ],
              file.size
            );
          reader.readAsDataURL(file);
        } else {
          // 未知类型：保留字节按文档收下（避免“粘贴/拖入后毫无反应”）
          const reader = new FileReader();
          // 读取失败：释放已预留的字节，避免预留位泄漏把后续附件挡住
          reader.onerror = () => releaseReservation(file.size);
          reader.onload = () => {
            const base64 = (reader.result as string).split(',')[1];
            const name = file.name || `pasted-file-${Date.now()}`;
            commitIfGen(
              gen,
              (prev) => [
                ...prev,
                {
                  name,
                  type: 'document',
                  dataBase64: base64,
                  dataUrl: reader.result as string,
                  size: file.size,
                  mimeType: file.type || getMimeTypeFromName(name),
                  status: 'done' as const,
                },
              ],
              file.size
            );
          };
          reader.readAsDataURL(file);
        }
      }
      return true;
    },
    []
  );

  /** 批内同步累计总量：一次多选/多文件粘贴时 React state 尚未提交，必须用本地 running 值
   *  判断，否则同一批里每个文件都看到旧的 attachmentsRef 而逐个放行（review 09:49 P1）。 */
  const attachBatch = (files: File[], actionId: string): number => {
    let accepted = 0;
    for (const f of files) {
      if (f.size > MAX_ONE_ATTACHMENT_BYTES) continue;
      if (!tryReserve(f.size)) break;
      if (attachFromFile(f, actionId)) accepted += 1;
      else releaseReservation(f.size); // Token 去重被拒：释放预留
    }
    return accepted;
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const actionId = newAttachAction();
    attachBatch(Array.from(e.target.files ?? []), actionId);
    e.target.value = '';
  };

  // 全局剪贴板粘贴（Ctrl+V）：文件/图片。用 window 监听以覆盖“焦点不在输入框”的情况；
  // 仅当剪贴板含文件时拦截，纯文本粘贴不受影响。
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const dt = e.clipboardData;
      if (!dt) return;
      const files: File[] = [];
      for (let i = 0; i < (dt.items?.length ?? 0); i++) {
        const it = dt.items[i];
        if (it.kind === 'file') {
          const f = it.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length === 0 && dt.files?.length) files.push(...Array.from(dt.files));
      // 没有文件 → 交给浏览器默认行为（纯文本/路径文本都照常粘贴，绝不吞）
      if (files.length === 0) return;
      // 复用 keydown 登记的 token（若有），并取消主进程读取的 fallback
      const pending = pendingPasteRef.current;
      const actionId = pending.token ?? newAttachAction();
      if (pending.timer !== null) window.clearTimeout(pending.timer);
      pendingPasteRef.current = { token: null, timer: null };
      // 剪贴板里确实有文件：交给下面的内容指纹流程处理，并阻止默认粘贴
      e.preventDefault();
      const gen = sessionGenRef.current;
      void (async () => {
        for (const f of files) {
          if (f.size > MAX_ONE_ATTACHMENT_BYTES) continue;
          if (!tryReserve(f.size)) break;
          let fp: string;
          try {
            fp = await fileFingerprint(f);
          } catch {
            fp = `${f.size}:${f.name}`; // 读字节失败时退回元信息指纹
          }
          if (gen !== sessionGenRef.current) {
            releaseReservation(f.size);
            break; // 期间切了会话：丢弃
          }
          if (seenFingerprintRecently(fp)) {
            releaseReservation(f.size);
            continue;
          }
          if (!attachFromFile(f, actionId, gen)) releaseReservation(f.size);
        }
      })();
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [attachFromFile]);

  // 主进程读系统剪贴板（Ctrl+V）：Windows「复制文件」/截图在 Chromium 的 paste 事件里
  // 拿不到，改由主进程读 CF_HDROP/剪贴板图片，再作为附件挂上。
  const attachBase64 = useCallback(
    async (
      name: string,
      base64: string,
      mime: string,
      size: number,
      actionId: string,
      gen: number = sessionGenRef.current
    ): Promise<boolean> => {
      if (size > MAX_ONE_ATTACHMENT_BYTES) return false;
      if (seenFingerprintRecently(await sha256HexOrFallback(bytesFromBase64(base64)))) return false;
      if (!acceptInAttachAction(actionId, `${name}:${size}`)) return false;
      if (mime.startsWith('image/')) {
        commitIfGen(
          gen,
          (prev) => [
            ...prev,
            { name, type: 'image', dataUrl: `data:${mime};base64,${base64}`, size },
          ],
          size
        );
        return true;
      }
      const ext = name.split('.').pop()?.toLowerCase() ?? '';
      const isServerParsed = /^(docx|doc|pptx|ppt|xlsx|xls|odt|odp|ods|rtf)$/i.test(ext);
      commitIfGen(
        gen,
        (prev) => [
          ...prev,
          {
            name,
            type: 'document',
            dataBase64: base64,
            dataUrl: `data:${mime};base64,${base64}`,
            size,
            mimeType: mime,
            status: isServerParsed ? 'pending' : 'done',
          },
        ],
        size
      );
      return true;
    },
    []
  );

  // 剪贴板 → 附件（主进程读）：Ctrl+V 与右键「粘贴」共用；返回是否挂了文件
  const pasteClipboardFiles = useCallback(
    async (token?: string): Promise<boolean> => {
      const gen = sessionGenRef.current;
      try {
        const res = await window.miqi.clipboard.readFiles();
        // 读取期间切了会话 → 丢弃这次剪贴板结果
        if (gen !== sessionGenRef.current) return false;
        const items = [...(res?.files ?? []), ...(res?.image ? [res.image] : [])];
        if (items.length === 0) return false;
        const actionId = token ?? newAttachAction();
        let any = false;
        for (const f of items) {
          if (gen !== sessionGenRef.current) break;
          if (f.size > MAX_ONE_ATTACHMENT_BYTES) continue;
          if (!tryReserve(f.size)) break;
          if (await attachBase64(f.name, f.base64, f.mime, f.size, actionId, gen)) any = true;
          else releaseReservation(f.size);
        }
        return any;
      } catch {
        return false;
      }
    },
    [attachBase64]
  );

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'v') return;
      // 只登记一个 token；真正的 paste 事件会消费它并取消 fallback。
      const token = newAttachAction();
      const p = pendingPasteRef.current;
      if (p.timer !== null) window.clearTimeout(p.timer);
      p.token = token;
      p.timer = window.setTimeout(() => {
        if (pendingPasteRef.current.token === token) {
          pendingPasteRef.current = { token: null, timer: null };
          void pasteClipboardFiles(token);
        }
      }, 400);
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown, true);
      const p = pendingPasteRef.current;
      if (p.timer !== null) window.clearTimeout(p.timer);
    };
  }, [pasteClipboardFiles]);

  const removeAttachment = (idx: number) =>
    setAttachments((prev) => prev.filter((_, i) => i !== idx));

  const handleAbort = useCallback(async () => {
    // Scope cleanup to THIS session's invocation(s): unsubsRef and
    // watchdogTimerRef point at the LATEST send overall — a newer send in
    // another session must not lose its listeners/watchdog when the user
    // stops this one (send in A → send in B → back to A → stop A).
    for (const [sendId, entry] of sendInvocationRegistryRef.current) {
      if (entry.sessionKey !== currentSessionRef.current) continue;
      entry.cleanup();
      for (const unsub of entry.unsubs) unsub();
      sendInvocationRegistryRef.current.delete(sendId);
    }
    clearFinalCleanupTimer();
    if (revealAnimIdRef.current !== null) {
      cancelAnimationFrame(revealAnimIdRef.current);
      revealAnimIdRef.current = null;
    }
    // Keep the lifecycle promise in place — a stop-then-quick-send must still
    // await the aborted turn's settlement so its terminal event (and the
    // backend drain task) cannot race the replacement send.
    try {
      // Pass the current thread id so the backend aborts the SAME thread the
      // streaming turn registered its cancel event under — without it the
      // abort resolves to "default" and misses the turn entirely (#542).
      await window.miqi.chat.abort(
        currentSessionRef.current,
        currentThreadIdRef.current ?? undefined
      );
    } catch {
      /* ignore */
    }
    // Mark any still-pending (pre-stream) send in THIS session as cancelled so
    // its provider check, when it eventually resolves, bails instead of
    // sending (issue #364).  The pending id is kept in the map until that check
    // resolves and removes it — clearing the entry here would let a
    // double-Enter slip through.
    const currentKey = currentSessionRef.current;
    const hadPendingSend = pendingSendIdsRef.current.has(currentKey);
    // Overwrite this session's pending id with a tombstone value (0) so the
    // pending provider check sees it lost the turn.  A different session's
    // pending send is untouched.
    if (hadPendingSend) pendingSendIdsRef.current.set(currentKey, 0);
    setStreaming(false);
    setSendingFor(currentKey, null);
    setCurrentReqId(null);
    flushReasoningRef.current?.(Date.now());
    liveReasoningTsRef.current = null;
    // Only append "已停止" when aborting a send that actually reached the
    // backend.  A stop during the pre-stream pending phase cancels a send that
    // never started — the optimistic bubble is removed by the provider check's
    // cancel path, and a stray progress message would be left behind.
    if (!hadPendingSend) {
      setMessages((prev) => [
        ...prev.filter((m) => !m.isLiveReasoning),
        { role: 'progress', content: '已停止。', timestamp: Date.now() },
      ]);
    }
  }, [clearFinalCleanupTimer]);

  // Respond to new-session trigger from App/Sidebar — create directly, no picker.
  // NOTE: this intentionally does NOT gate on `streaming`. Switching sessions
  // mid-stream is an expected workflow (covered by session-streaming-isolation
  // E2E); the new ChatConsole unmount aborts the in-flight render, and backend
  // isolation guarantees the stream never leaks into the new session.
  useEffect(() => {
    if (newSessionTrigger && newSessionTrigger > 0) {
      createSession(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newSessionTrigger]);

  // 打开工作目录下拉（锚定在点中的胶囊下方，替代原居中 Modal）。再次点击
  // 同一胶囊即收起；每次展开前刷新“最近使用”。
  const handleOpenWorkspacePicker = useCallback(async (el?: HTMLElement | null) => {
    if (workspacePickerOpenRef.current) {
      setWorkspacePickerOpen(false);
      return;
    }
    setWorkspacePickerAnchor(el ? el.getBoundingClientRect() : null);
    setWorkspacePickerOpen(true);
    try {
      const workspaces = await window.miqi.sessions
        .listRecentWorkspaces()
        .then((r) => r?.workspaces ?? [])
        .catch(() => [] as string[]);
      setRecentWorkspaces(workspaces);
    } catch {
      setRecentWorkspaces([]);
    }
  }, []);

  const createSession = useCallback(
    (workspace?: string | null) => {
      // Close the workspace picker explicitly.  App.tsx removed key={sessionKey}
      // so ChatConsole stays mounted across session switches — there is no
      // remount to reset workspacePickerOpen, so the modal would otherwise stay
      // open after choosing a workspace (#378).
      setWorkspacePickerOpen(false);
      const newKey = `desktop:${Date.now()}`;
      currentThreadIdRef.current = null;
      cleanupListeners();
      onNewSession?.(newKey, workspace ?? null);
    },
    [cleanupListeners, onNewSession]
  );

  const handleDeleteSession = useCallback(async () => {
    const key = currentSessionRef.current;
    if (!key) return;
    if (!window.confirm('确定删除此对话？此操作不可撤销。')) return;
    try {
      await window.miqi.sessions.delete(key);
    } catch {
      /* ignore */
    }
    createSession(null);
  }, [createSession]);

  /** Payload for programmatic sends (e.g. regenerate) — bypasses input state */
  const retryPayloadRef = useRef<{
    text: string;
    attachments: Attachment[];
    retry?: boolean;
  } | null>(null);
  const handleSendRef = useRef<() => void>(() => {});
  /** 发送文本经此 ref 显式传入 handleSend 并一次性消费：既承载程序化发送
   *  （论文下载 fallback 等），也承载 Composer 的用户输入（#1021 下沉后
   *  input 状态不再住在 ChatConsole）。不依赖 state 更新后的渲染 flush。 */
  const programmaticTextRef = useRef<string | null>(null);
  /** #740: pending resume-turn id — set by 继续执行, consumed by handleSend
   *  so the resume request flows through the full send pipeline (listeners,
   *  streaming render) instead of a bare chat.send call. */
  const resumeTurnIdRef = useRef<string | null>(null);
  // 恢复中断回合时被移除的中断卡：登录失效拦截需恢复它并追加重登引导
  //（resume 无乐观 user 气泡，否则上下文丢失、登录按钮无处可点）。
  const resumeRemovedMsgRef = useRef<Message | null>(null);
  /** Per-session send id of the send currently in its pre-stream pending phase
   *  (issue #364).  A session is "pending" while its optimistic bubble waits on
   *  the non-blocking provider check / thread init.  The double-Enter guard
   *  bails only for a session that owns a pending send (other sessions may
   *  start their own turn), and the stop button cancels only the current
   *  session's pending send.  Comparing the stored id against this closure's
   *  own id lets a superseded / re-sent / cancelled send know it lost the turn. */
  const pendingSendIdsRef = useRef<Map<string, number>>(new Map());
  /** 编辑重答原子化(#828):handleSend 同步段的接受结果——
   *  被 pending guard 等预检拒绝时标 'rejected',handleEdit 据此回滚截断。 */
  const editSendOutcomeRef = useRef<'accepted' | 'rejected' | null>(null);
  /** 编辑重答回滚点(待绑定,#1011)。handleEdit 设置,handleSend 在生成
   *  thisSendId 后转入 editRollbacksRef 按 sendId 绑定。 */
  const editPendingRollbackRef = useRef<{ snapshot: Message[]; sessionKey: string } | null>(null);
  /** 编辑重答回滚点(按 sendId 绑定,#1011;CodeRabbit:发送可跨 session
   *  重叠,单槽会被别的 send 误消费——每个 send 只取自己绑定的回滚点)。 */
  const editRollbacksRef = useRef<Map<number, { snapshot: Message[]; sessionKey: string }>>(
    new Map()
  );
  /** Monotonic id for pendingSendIdsRef — distinguishes "this send" from any
   *  newer send that started for the same session. */
  const sendSeqRef = useRef(0);
  /** True while THIS send is still the current pending send for its session —
   *  false once it streamed, was cancelled, or was superseded by a newer send. */
  const isCurrentPendingSend = (key: string, id: number) =>
    pendingSendIdsRef.current.get(key) === id;

  // #740: 中断 turn 的「继续执行 / 重新开始」。
  // 继续执行 → 经 handleSend 主流程发 resume 请求（复用流式监听/渲染），
  // 后端以快照半截内容为上下文续答（replan）；重新开始 → 删除快照并移除卡片。
  const handleResumeTurn = useCallback((msg: Message) => {
    const meta = msg.interruptedMeta;
    if (!meta?.turnId) return;
    resumeTurnIdRef.current = meta.turnId;
    // 移除中断卡——resume 的新回复由流式事件接管渲染。卡片暂存 ref：
    // 若预检被登录失效拦截，需恢复卡片并追加重登引导（applyReloginIntercept）。
    resumeRemovedMsgRef.current = msg;
    setMessages((prev) => prev.filter((m) => m !== msg));
    handleSendRef.current();
  }, []);

  const handleRestartTurn = useCallback(
    async (msg: Message) => {
      const meta = msg.interruptedMeta;
      if (!meta?.turnId) return;
      try {
        await window.miqi.chat.discardResume(meta.turnId, sessionKey);
        setMessages((prev) => prev.filter((m) => m !== msg));
      } catch (e) {
        console.error('[resume] discard failed', e);
      }
    },
    [sessionKey]
  );

  // #680 跟进：复杂问题建议切 🧠（fast 的 3 轮保险丝/2048 tokens 可能不够）
  const [complexHint, setComplexHint] = useState(false);
  // 角标 5 秒自动消失
  useEffect(() => {
    if (!complexHint) return;
    const t = setTimeout(() => setComplexHint(false), 5000);
    return () => clearTimeout(t);
  }, [complexHint]);

  const handleSend = useCallback(async () => {
    // #646-v2（2026-09-15 定稿）：调整意见已在计划卡内提交、由后端同轮重规划，
    // 发送路径不再需要清理任何"调整提示"状态。
    // #740: resume consumes the pending resume-turn id (set by 继续执行).
    const _resumeId = resumeTurnIdRef.current;
    resumeTurnIdRef.current = null;
    const payload = retryPayloadRef.current;
    // 发送文本经 ref 显式传入（程序化发送 + Composer 用户输入）：不依赖
    // state 更新后的渲染 flush（旧闭包读到的 input state 是旧值）。
    const programmaticText = programmaticTextRef.current;
    programmaticTextRef.current = null;
    // payload(编辑重答/重试)文本原样发送,不经 trim —— 保留用户刻意的
    // 首尾空格/换行(CodeRabbit #1011);trim 仅用于空输入校验。
    const text = payload?.text ?? (programmaticText ?? '').trim();
    const atts = payload?.attachments ?? attachments;
    if (!text.trim() && atts.length === 0 && !_resumeId) {
      retryPayloadRef.current = null;
      editSendOutcomeRef.current = 'rejected';
      return;
    }
    // 复杂问题 + 极速模式 → 提示建议切 🧠 深度研究（不阻断，可忽略）
    if (reasoningModeRef.current === 'fast' && !_resumeId && isComplexQuestion(text)) {
      setComplexHint(true);
    } else {
      setComplexHint(false);
    }
    // Double-Enter / double-click while the SAME session's previous send is
    // still in its pending (pre-stream) phase: bail so a second send can't
    // spawn a duplicate optimistic bubble (issue #364).  The guard is scoped to
    // the session — a pending send in session A must not block the user from
    // starting a fresh turn in session B.
    if (pendingSendIdsRef.current.has(currentSessionRef.current)) {
      // A blocked regenerate must not leak its payload into the next manual
      // send — clear it before bailing (CodeRabbit #681).
      retryPayloadRef.current = null;
      editSendOutcomeRef.current = 'rejected';
      return;
    }
    // Retry/regenerate: nudge the model to answer differently — the stored
    // user message stays clean, only the outbound content gets the hint.
    const retryHint = payload?.retry
      ? '\n\n[系统提示：这是重试请求。请换一个角度重新回答，不要复述之前的答案。]'
      : '';

    // ── Optimistic UI (issue #364) ────────────────────────────────────────
    // The user's message is committed to the UI and the input is cleared
    // IMMEDIATELY, before any async work (providers.list / threads.start).
    // On a cold start the bridge can take seconds to init, and previously the
    // send sat silently waiting on it — so the user pressed Enter again and
    // got duplicate messages/tasks.
    //
    // Ordering matters: the SECOND send (double Enter) must NOT append
    // another bubble.  The turn is stamped as streaming right here, so the
    // textarea + handleKeyDown + send-button guard on `streaming` all see it
    // synchronously after this first synchronous pass.  `streaming` also
    // drives the send button turning into a "stop" button, and `sending`
    // (stamped with the bubble's timestamp) drives the in-progress spinner on
    // the just-appended user bubble.
    //
    // The bubble is replaced below if the provider check fails (no configured
    // provider → provider-config error bubble).
    // The component survives session switches, so a turn's closure can
    // outlive the session it belongs to.  Capture the session this send
    // targets NOW — the `sessionKey` prop closure may be stale (not in the
    // useCallback deps) and the session-switch effect mutates
    // currentSessionRef.  The watchdog below must not warn into another
    // session after the user switched away.
    const sendSessionKey = currentSessionRef.current;

    // The optimistic user bubble — committed to the UI immediately.  Stamped
    // with `userMsg.timestamp` so a late-failing provider check can match and
    // replace it, and so `revealNext` can anchor the assistant reply right
    // after it (timestamp + 1).  `sending` is stamped with this same timestamp
    // so the spinner renders only on this exact bubble (per-session scoping —
    // see MessageBubble's `sending` check).
    const userMsg: Message = {
      role: 'user',
      content: text || '(attachment)',
      attachments: [...atts],
      reasoningMode: reasoningModeRef.current,
      timestamp: Date.now(),
    };

    const wasStreaming = streaming;
    retryPayloadRef.current = null;
    editSendOutcomeRef.current = 'accepted';
    // Unique id for THIS send, stored in the pending map.  A later send for
    // the same session overwrites it, so this closure can tell it lost the
    // turn (its provider check must not proceed).
    const thisSendId = ++sendSeqRef.current;
    // 编辑重答(#1011):把待绑定回滚点绑到本次 send —— 按 sendId 绑定,
    // 避免跨 session 的其它 send 消费/清除它(CodeRabbit)。
    const pendingEditRollback = editPendingRollbackRef.current;
    if (pendingEditRollback && pendingEditRollback.sessionKey === sendSessionKey) {
      editRollbacksRef.current.set(thisSendId, pendingEditRollback);
    }
    editPendingRollbackRef.current = null;
    pendingSendIdsRef.current.set(sendSessionKey, thisSendId);
    setSendingFor(sendSessionKey, userMsg.timestamp);
    setStreaming(true);
    // #740: resume has no user bubble (the interrupted card was removed and
    // the streamed reply takes over rendering) — skip the optimistic push.
    if (!_resumeId) setMessages((prev) => [...prev, userMsg]);
    userScrolledUp.current = false;
    composerRef.current?.clear();
    setAttachments([]);
    // Save a snapshot before clearing — chat.send needs it later.  Use the
    // resolved `atts` (which handles the retry-payload path), not the state,
    // so the bubble and the backend payload always match (CodeRabbit #681).
    const sentAttachments = [...atts];
    // The session is marked in-flight synchronously with the bubble so a
    // session-switch / reload during the slow provider check still knows this
    // turn is pending.  Dropping the "final already handled" mark lets this
    // turn's live final render.
    streamingBySession.add(sendSessionKey);
    finalHandledSessions.delete(sendSessionKey);
    // Only auto-unsubscribe the previous invocation's listeners when it was
    // THIS session's send (same-session supersede).  Unsubscribing across
    // sessions strands the other session's in-flight turn: its terminal
    // events are never processed, its send cleanup never runs, and its 60s
    // watchdog survives to fire a false "后端 60s 无响应" later.
    if (unsubsSessionRef.current === sendSessionKey) cleanupListeners();
    // A new send supersedes any in-flight typewriter for this session — cancel
    // the RAF chain so the previous reply stops typing the moment a new message
    // is sent, and reset its state so the new turn does NOT inherit the old
    // fullContent/displayed (#542).  Unconditional: the old turn's lifecycle may
    // already have settled (final received but still revealing), which skips the
    // supersede block below and would otherwise leave the old reply typing until
    // the new turn's final overwrites it.
    {
      const prevReveal = revealBySession.get(sendSessionKey);
      if (prevReveal?.animId != null) {
        cancelAnimationFrame(prevReveal.animId);
        if (revealAnimIdRef.current === prevReveal.animId) revealAnimIdRef.current = null;
      }
      revealBySession.set(sendSessionKey, {
        fullContent: '',
        displayed: '',
        animId: null,
        finalDone: false,
        lastTickTs: null,
      });
    }
    // ── /Optimistic UI ────────────────────────────────────────────────────

    // The user bubble is in the list now (stamped with `userMsg.timestamp`),
    // and the turn is marked streaming.  If the provider check below finds no
    // configured provider, the bubble is replaced with the provider-config
    // guidance.

    // The provider check (no configured provider → immediate config bubble)
    // was previously AWAITED before any UI update.  It is now non-blocking:
    // the optimistic UI has already shown the message, and this resolves in
    // the background.  If it rejects, the send proceeds anyway — the bridge
    // surfaces the underlying runtime error through the stream/error path.
    //
    // #1011 P1(review):记录 chat.send 是否真正送出 —— 只有确定「未派发」
    // 的失败(此前的附件/内容构造/thread start 等)才允许恢复编辑快照;
    // chat.send 调用之后的 reject(bridge/IPC/timeout)可能请求已送达后端,
    // 此时恢复旧列表会与后端状态分叉,一律不做。
    let turnDispatched = false;
    try {
      // #922/#1000：网关状态先取一次，供「未登录 → 登录引导」与
      // 「已登录但网关未就绪 → 网关提示」两个分支共用。旧 preload/
      // smoke mock 无 qraft 命名空间时为 null（视为未登录）。
      //
      // ⚠️ 这三个卡口（重登 / 网关门禁 / 无可用模型）都是 **await 之后** 才判定的，
      // 而 handleAbort 会把本会话的 pending id 覆盖成 tombstone 0。若期间用户
      // 中断了本次发送，陈旧结果再回来就会：删掉 pending 记录、清掉
      // streamingBySession、并把**已取消**的乐观气泡换成拦截卡。所以三个分支
      // 都必须先过 isCurrentPendingSend —— 失守时一律落到下面那条守卫，
      // 由它按「本次发送已作废」删气泡、还输入框。
      const gatewayStatus =
        typeof window.miqi.qraft?.status === 'function'
          ? await window.miqi.qraft.status().catch(() => null)
          : null;
      // ── 登录已失效拦截 ──
      // token 刷新失败且未恢复（requiresRelogin）时拦截发送：把乐观气泡换成
      // 重登引导（一键登录成功后气泡自动移除）。先于网关门禁/无 provider 判定
      // —— 失效后网关状态仍是旧快照里的 active，必须优先给出重登指引。
      // 快照读取失败（qraft.status() 抛错）时回退到订阅状态 refs，拦截不失效。
      const gatewayLoggedIn = gatewayStatus?.loggedIn ?? loggedInRef.current;
      const gatewayRequiresRelogin = gatewayStatus?.requiresRelogin ?? requiresReloginRef.current;
      if (
        gatewayLoggedIn &&
        gatewayRequiresRelogin &&
        isCurrentPendingSend(sendSessionKey, thisSendId)
      ) {
        pendingSendIdsRef.current.delete(sendSessionKey);
        streamingBySession.delete(sendSessionKey);
        setSendingFor(sendSessionKey, null);
        if (currentSessionRef.current === sendSessionKey) {
          setStreaming(false);
          // 恢复中断回合（#740）：无乐观 user 气泡，取回被 handleResumeTurn
          // 移除的中断卡并追加重登引导；随后复位 ref 防陈旧引用。
          const resumeRemovedMsg = _resumeId ? resumeRemovedMsgRef.current : null;
          resumeRemovedMsgRef.current = null;
          setMessages((prev) => applyReloginIntercept(prev, userMsg, resumeRemovedMsg));
          composerRef.current?.setText(text);
          setAttachments(atts);
        }
        return;
      }
      // ── #922 AI 网关门禁 ──
      // 登录后网关状态明确非 active（provisioning/failed/disabled）时拒绝发起
      // 会话：把乐观气泡换成网关提示并恢复输入框。未登录 / 平台未下发网关状态
      // 时放行（与模型面板语义一致）。先于无 provider 判定（CodeRabbit #1010）：
      // 已登录但网关未就绪 + 无 provider 时给网关修复指引，而非泛泛的
      // 「未配置模型服务」。
      if (
        gatewayStatus?.loggedIn === true &&
        gatewayStatus.aiGateway &&
        gatewayStatus.aiGateway.status !== 'active' &&
        isCurrentPendingSend(sendSessionKey, thisSendId)
      ) {
        pendingSendIdsRef.current.delete(sendSessionKey);
        streamingBySession.delete(sendSessionKey);
        setSendingFor(sendSessionKey, null);
        if (currentSessionRef.current === sendSessionKey) {
          const rollback = editRollbacksRef.current.get(thisSendId);
          editRollbacksRef.current.delete(thisSendId);
          setStreaming(false);
          if (rollback && rollback.sessionKey === sendSessionKey) {
            // 编辑重答:恢复截断前的完整列表,错误提示追加在末尾(#1011)
            setMessages([...rollback.snapshot, createGatewayBlockedMessage()]);
          } else {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.timestamp === userMsg.timestamp) {
                return [...prev.slice(0, -1), createGatewayBlockedMessage()];
              }
              return prev;
            });
          }
          composerRef.current?.setText(text);
          setAttachments(atts);
        }
        return;
      }
      const result = await window.miqi.providers.list();
      // 判定「当前默认模型能否发起会话」而不是「有没有已配置的本地 provider」：
      // 登录后经平台 AI 网关路由的默认模型不需要任何本地凭据（make_provider
      // 的网关分支），只看 configured 会把「登录即可用」误拦成「未配置模型
      // 服务」——用户登录后仍被要求配置模型即由此而来。后端用与运行时同一套
      // 判定（含网关路由）给出 active_model_resolvable；旧版 bridge 无该字段
      // 时回退到 configured（保持原行为）。
      const modelServable =
        result.active_model_resolvable ?? result.providers.some((provider) => provider.configured);
      // 与上面两个网关卡口同一个道理：providers.list() 也是 await 出来的，
      // 期间用户可能已中断或又发了新消息 —— 陈旧结果不能去动 pending/streaming，
      // 更不能把已取消的乐观气泡替换成引导卡。失守时落到下面那条
      // isCurrentPendingSend 守卫，由它按「本次发送已作废」收尾。
      if (!modelServable && isCurrentPendingSend(sendSessionKey, thisSendId)) {
        // No servable model — replace the optimistic bubble with the
        // provider-config guidance.  The send is refused: the user should
        // configure a provider before sending.  The draft is restored to the
        // input so they can re-send once configured.  Only touch the composer /
        // message list if THIS session is still displayed — the user may have
        // switched away while providers.list was pending, and the composer /
        // setAttachments / setMessages act on the currently displayed session.
        // #1000：未登录时没有 Provider 可配置（#835 合规收口后凭据配置已移除），
        // 拦截气泡直接给出一键登录按钮，登录后经网关自动获得平台内置模型。
        // 已登录时凭据配置同样不存在，引导落点是「设置 → 模型」选平台内置模型。
        const guidance =
          gatewayStatus?.loggedIn === true
            ? createProviderConfigMessage(
                '当前默认模型没有可用的模型服务。请到 设置 → 模型 选择平台内置模型后重试。',
                'open-provider-settings',
                '去选择模型'
              )
            : createProviderConfigMessage(
                '尚未登录平台账号。登录 MiQroForge 账号后即可使用平台内置模型发起会话，模型调用将经平台 AI 网关转发。',
                'login'
              );
        pendingSendIdsRef.current.delete(sendSessionKey);
        streamingBySession.delete(sendSessionKey);
        setSendingFor(sendSessionKey, null);
        if (currentSessionRef.current === sendSessionKey) {
          const rollback = editRollbacksRef.current.get(thisSendId);
          editRollbacksRef.current.delete(thisSendId);
          setStreaming(false);
          if (rollback && rollback.sessionKey === sendSessionKey) {
            // 编辑重答:恢复截断前的完整列表,错误提示追加在末尾(#1011)
            setMessages([...rollback.snapshot, guidance]);
          } else {
            setMessages((prev) => {
              const last = prev[prev.length - 1];
              if (last?.timestamp === userMsg.timestamp) {
                return [...prev.slice(0, -1), guidance];
              }
              return prev;
            });
          }
          composerRef.current?.setText(text);
          setAttachments(atts);
        }
        return;
      }
    } catch {
      // If provider status cannot be read, keep the original send path so the
      // bridge can surface the underlying runtime error.
    }
    // The user hit stop while the provider check was still pending (or this
    // send was superseded by a newer one for the same session) — cancel the
    // optimistic bubble and restore the composer (the send never started).
    // Only touch the composer / message list if THIS session is still
    // displayed — the user may have switched away while the check was pending,
    // and the composer / setAttachments / setMessages act on the current session.
    if (!isCurrentPendingSend(sendSessionKey, thisSendId)) {
      pendingSendIdsRef.current.delete(sendSessionKey);
      streamingBySession.delete(sendSessionKey);
      setSendingFor(sendSessionKey, null);
      if (currentSessionRef.current === sendSessionKey) {
        const rollback = editRollbacksRef.current.get(thisSendId);
        editRollbacksRef.current.delete(thisSendId);
        if (rollback && rollback.sessionKey === sendSessionKey) {
          // 编辑重答被 stop/superseded:恢复截断前的完整列表(#1011)
          setMessages(rollback.snapshot);
        } else {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.timestamp === userMsg.timestamp) return prev.slice(0, -1);
            return prev;
          });
        }
        composerRef.current?.setText(text);
        setAttachments(atts);
      }
      return;
    }

    // 编辑回滚点保留至真正派发(#1011 P1,review):预派发检查通过≠已发出,
    // 附件/内容构造/thread start/send 仍可能失败,过早清除会导致无法恢复。
    // (本处不再删除,改在 chat.send 发出成功后清除)

    // If a reveal animation is still running from the previous response,
    // cancel it and abort the in-flight request so we can start fresh.  A
    // supersede is ONLY valid for a prior turn in THIS SESSION — lifecycleRef
    // is global (the most recent turn across all sessions), so a new send in
    // session B must not abort/cancel session A's still-streaming turn.
    const supersededLifecycle = lifecycleRef.current;
    const supersedeSameSession =
      supersededLifecycle != null && supersededLifecycle.sessionKey === sendSessionKey;
    if (wasStreaming && supersededLifecycle && supersedeSameSession) {
      // A prior turn is still in flight and the user sent a new message —
      // supersede it before starting this turn (the optimistic bubble is
      // already shown).  Only the abort itself is awaited here; the prior
      // turn's settle is awaited below.
      if (revealAnimIdRef.current !== null) {
        cancelAnimationFrame(revealAnimIdRef.current);
        revealAnimIdRef.current = null;
      }
      if (watchdogTimerRef.current !== null) {
        clearInterval(watchdogTimerRef.current);
        watchdogTimerRef.current = null;
      }
      cleanupListeners();
      try {
        // Pass the session key — without it the backend resolves no session
        // and rejects the abort with UNAUTHORIZED, leaving the old stream
        // running while the new turn starts.  Also pass the current thread id
        // so the abort hits the turn's registered thread instead of the
        // backend's "default" fallback (which misses every real thread) (#542).
        await window.miqi.chat.abort(
          currentSessionRef.current,
          currentThreadIdRef.current ?? undefined
        );
      } catch {
        /* ignore */
      }
    }
    // Wait for the superseded turn's FULL lifecycle (threads.start + chat.send
    // + terminal event) to settle — even when a manual stop happened earlier,
    // or while a threads.start was still pending. It resolves at that turn's
    // terminal event, so the old turn's terminal event is consumed before the
    // new turn registers listeners — and the backend's drain task has exited,
    // so the new chat.send is not rejected with TURN_IN_PROGRESS. Bounded so a
    // wedged backend cannot stall interrupt-and-resend (see TURN_ABORT_SETTLE_MS).
    // A cross-session lifecycle (from a session the user switched away from)
    // resolves on its own — do NOT block this send on it.
    if (supersededLifecycle && supersedeSameSession) {
      try {
        await Promise.race([
          supersededLifecycle.promise,
          new Promise<void>((resolve) => setTimeout(resolve, TURN_ABORT_SETTLE_MS)),
        ]);
      } catch {
        /* ignore */
      }
    }
    // This turn's lifecycle — registered BEFORE any await (threads.start,
    // chat.send) so a subsequent interrupt-and-resend can always serialize
    // against it, even mid thread-init.
    const turnId = ++lifecycleSeqRef.current;
    // source 回合索引随每次发送前进（会话加载时已对齐 user 消息数），与上面
    // 的 lifecycle 身份分账——见 lifecycleSeqRef 注释。
    turnSeqRef.current += 1;
    let resolveLifecycle: () => void = () => {};
    const lifecyclePromise = new Promise<void>((resolve) => {
      resolveLifecycle = resolve;
    });
    const lifecycle = { id: turnId, promise: lifecyclePromise, sessionKey: sendSessionKey };
    lifecycleRef.current = lifecycle;
    const settleLifecycle = () => {
      if (lifecycleRef.current?.id === turnId) lifecycleRef.current = null;
      resolveLifecycle();
    };
    // The user hit stop (or a newer send took over) while the aborts above were
    // awaited — handleAbort wrote a tombstone (0) into pendingSendIdsRef for
    // this session and kept the entry so a later send would still bail.  Detect
    // that lost-turn here: drop the optimistic bubble, clear the marker (the
    // tombstone would otherwise block every later send in this session), restore
    // the composer, and settle this lifecycle so a later send does not wait on
    // it.  Do NOT proceed to threads.start/chat.send for a cancelled send.
    if (!isCurrentPendingSend(sendSessionKey, thisSendId)) {
      pendingSendIdsRef.current.delete(sendSessionKey);
      streamingBySession.delete(sendSessionKey);
      setSendingFor(sendSessionKey, null);
      // Only restore the composer / message list if THIS session is still
      // displayed — the user may have switched away while the aborts were
      // awaited, and the composer / setAttachments / setMessages act on the
      // currently displayed session.
      if (currentSessionRef.current === sendSessionKey) {
        const rollback = editRollbacksRef.current.get(thisSendId);
        editRollbacksRef.current.delete(thisSendId);
        setStreaming(false);
        if (rollback && rollback.sessionKey === sendSessionKey) {
          // 编辑重答被取消(#1011):恢复截断前的完整列表
          setMessages(rollback.snapshot);
        } else {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.timestamp === userMsg.timestamp) return prev.slice(0, -1);
            return prev;
          });
        }
        composerRef.current?.setText(text);
        setAttachments(atts);
      }
      settleLifecycle();
      return;
    }
    // The pending (pre-stream) phase is over — the turn now has a lifecycle and
    // will stream normally.  Clear the pending marker so an abort during the
    // stream uses the normal path (not the cancel-pending path).
    pendingSendIdsRef.current.delete(sendSessionKey);
    setSendingFor(sendSessionKey, null);
    // Stamp this turn now (BEFORE any await below) so listeners registered
    // later can drop terminal events from the superseded turn.  The turn id
    // and start time are invocation-local: overlapping sends across sessions
    // used to overwrite the shared refs, making each other's terminals look
    // stale.
    const sendStartedAt = Date.now();
    let myTurnId: string | null = null;

    // Generate a client-side req_id so we can abort this specific request
    const reqId = `req_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    setCurrentReqId(reqId);

    // New turn — reset the pure-thinking anchor and the live-reasoning marker:
    // without the reset, a turn without reasoning would inherit the previous
    // turn's hadLiveReasoning=true and show a spurious thinking duration.
    // Also cancel any pending reasoning flush from the superseded turn so its
    // buffered deltas can't leak into the new turn (CodeRabbit #662).
    if (reasoningTimerRef.current) {
      clearTimeout(reasoningTimerRef.current);
      reasoningTimerRef.current = null;
    }
    reasoningBufRef.current = '';
    thinkingStartedAtRef.current = null;
    lastReasoningDeltaAtRef.current = null;
    liveReasoningTsRef.current = null;

    let content = text + retryHint;

    // #968 复核（CodeRabbit #969）：先为 document 附件预计算全量 SHA-256 内容
    // 指纹并暂存到附件（contentFp）——渲染线程无同步摘要，只能在此 await 段算；
    // 拼装饰与去重守卫都读暂存值（守卫在 load() 同步合并路径，不能做摘要）。
    // 重试回合的附件已带 contentFp → 跳过重算。解码失败 → fp 缺失 → 占位无指纹
    // → 守卫不认领（方向安全）。
    for (const att of atts) {
      if (att.type === 'document' && att.dataBase64 && !att.contentFp) {
        try {
          att.contentFp = await _sha256HexOfBase64(att.dataBase64);
        } catch {
          /* 保留 undefined */
        }
      } else if (att.type === 'image' && att.dataUrl && !att.contentFp) {
        // 图片字节以 dataUrl 形式存在（#968 复核 CodeRabbit #969）：装饰只带
        // 文件名无法区分同名异字节，同样预计算指纹写进 [Image: name (fp:…)]
        try {
          att.contentFp = await _sha256HexOfText(att.dataUrl);
        } catch {
          /* 保留 undefined */
        }
      }
    }

    // Build message content with embedded document text
    for (const att of atts) {
      if (att.type === 'text' && att.content) {
        content += `\n\n[File: ${att.name}]\n\`\`\`\n${att.content}\n\`\`\``;
      } else if (att.type === 'image' && att.dataUrl) {
        // 图片装饰内嵌内容指纹 (fp:…)（CodeRabbit #969）——同名异字节图片
        // 不得互认；IMAGE_PLACEHOLDER_RES 名称解析已容忍该尾（向后兼容）
        const fpTag = att.contentFp ? ` (fp:${att.contentFp})` : '';
        content += `\n\n[Image: ${att.name}${fpTag}]`;
      } else if (att.type === 'document' && att.dataBase64) {
        // Decode and extract text client-side（解码逻辑与去重守卫共用 _decodeDocData，
        // 见上——守卫需按相同规则重解以逐字校验内容，单一实现防漂移 #968 复核）
        try {
          const { extracted, ext } = _decodeDocData(att.dataBase64, att.name);
          if (extracted && extracted.trim()) {
            content += `\n\n--- Document: ${att.name} ---\n${extracted.slice(0, 50000)}\n--- End of ${att.name} ---`;
          } else {
            // 占位装饰内嵌内容指纹 (fp:…) —— 守卫按指纹区分同名不同内容的附件
            //（CodeRabbit #969）；key 剥离的占位规则仍可整段移除。
            const fpTag = att.contentFp ? ` (fp:${att.contentFp})` : '';
            if (ext === 'pdf') {
              content += `\n\n[${att.name}: scanned PDF${fpTag} — OCR will be attempted by the server]`;
            } else {
              content += `\n\n[${att.name}: binary file, server will parse${fpTag}]`;
            }
          }
        } catch {
          const fpTag = att.contentFp ? ` (fp:${att.contentFp})` : '';
          content += `\n\n[${att.name}: ${formatFileSize(att.size)} — parsing on server${fpTag}]`;
        }
      }
    }

    // Turn is already optimistically committed (see the optimistic UI block at
    // the top of handleSend) — the user bubble is in `messages` and the input
    // is cleared.  Mark the session as streaming so a session-switch / reload
    // knows this turn is in flight, and drop any "final already handled" mark
    // from a previous turn so this turn's live final is rendered.
    streamingBySession.add(sendSessionKey); // turn in flight — survives switch
    finalHandledSessions.delete(sendSessionKey); // new turn — allow live final

    // Typewriter state is held at module level (revealBySession) so it
    // survives a session switch-away — the animation pauses (skips setMessages
    // while away) and RESUMES when the user returns.
    const _reveal = revealBySession.get(sendSessionKey) ?? {
      fullContent: '',
      displayed: '',
      animId: null,
      finalDone: false,
      lastTickTs: null,
    };
    revealBySession.set(sendSessionKey, _reveal);
    let fullContent = _reveal.fullContent;
    let displayed = _reveal.displayed;
    let animId = _reveal.animId;
    let finalDone = _reveal.finalDone;
    let streamErrorHandled = false;
    fullContentRef.current = fullContent;
    // Timestamp when the turn started so we can compute "用时 X 秒".
    const turnStartMs = Date.now();
    // ── Time-driven typewriter cadence ────────────────────────────────────
    // Chromium drops requestAnimationFrame to ~1Hz while the window is
    // minimized / fully occluded (background throttling). The old per-frame
    // step (4 chars/frame) therefore became ~4 chars/s in the background —
    // the stream looked frozen until the window came back. Advance by
    // wall-clock time instead: 4 chars per 60fps frame = ~240 chars/s of
    // real time, independent of the rAF cadence. `lastTickTs` persists in
    // _reveal so a resumed typewriter (switch-back / component remount)
    // catches up to the full content on the first frame.
    const MS_PER_CHAR = 1000 / (4 * 60);
    let lastTickTs = _reveal.lastTickTs ?? performance.now();

    // Reveal the assistant reply with a typewriter animation. The bubble is
    // created lazily — only once the first chunk of content is available — so
    // we never render an empty assistant bubble (which previously flashed as a
    // blank message box before the first animation frame filled it in; see
    // issue #109). If the reply has no text, no bubble is shown at all.
    const persistReveal = () => {
      _reveal.fullContent = fullContent;
      _reveal.displayed = displayed;
      _reveal.animId = animId;
      _reveal.finalDone = finalDone;
      _reveal.lastTickTs = lastTickTs;
    };
    const revealNext = () => {
      // The component survives session switches, so this typewriter loop can
      // outlive the session it belongs to.  Keep the RAF chain RUNNING across
      // a switch-away — only skip the setMessages when we're not on the send's
      // own session.  If we stopped the chain on switch-away (animId = null;
      // return), nothing would ever restart it when the user switches back,
      // so a half-typed reply would never finish revealing.  The user sees
      // the remaining content continue the moment they return.
      //
      // ── Time-driven advance (background-throttling fix) ──
      // `displayed` moves by wall-clock time (~240 chars/s), NOT by a fixed
      // per-frame step, so the reveal keeps up with the stream even while
      // the window is minimized/occluded and Chromium throttles rAF to
      // ~1Hz.  The advance runs even while switched away (the setMessages
      // below is skipped), so a half-typed reply catches up in memory and
      // renders immediately on switch-back.
      const now = performance.now();
      if (displayed.length < fullContent.length) {
        const chars = Math.max(1, Math.floor((now - lastTickTs) / MS_PER_CHAR));
        displayed += fullContent.slice(displayed.length, displayed.length + chars);
      }
      lastTickTs = now;

      if (currentSessionRef.current === sendSessionKey) {
        if (displayed.length >= fullContent.length) {
          // Reveal finished.  If the UI's assistant bubble is still partial
          // (the RAF chain advanced `displayed` in memory while we were away,
          // skipping setMessages), sync it to the full text in one update so
          // the remaining content appears immediately on switch-back.  If the
          // bubble doesn't exist yet (load() rebuilt the list without it),
          // create it prefilled with the full reply.
          const ts = userMsg.timestamp + 1;
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            // #1071 review P1（2026-09-16）：binding 必须用 invocation-local 的 myTurnId——
            // activeTurnIdRef 跨 invocation 共享，别的 turn 的 turn_started 会改写它，
            // 导致本消息被绑到错误的 turn。
            if (
              last?.role === 'assistant' &&
              last.timestamp === ts &&
              last.content !== fullContent
            ) {
              return [
                ...prev.slice(0, -1),
                { ...last, content: fullContent, turnId: myTurnId ?? last.turnId },
              ];
            }
            // #1071 review P1（2026-09-16）：分支②同一竞态，一律用 invocation-local myTurnId。
            if (last?.role === 'assistant' && last.content !== fullContent) {
              return [
                ...prev.slice(0, -1),
                { ...last, content: fullContent, turnId: myTurnId ?? last.turnId },
              ];
            }
            if (!last || last.role !== 'assistant') {
              return [
                ...prev,
                {
                  role: 'assistant',
                  content: fullContent,
                  timestamp: ts,
                  // #1071 review P1（2026-09-16）：新建 bubble 的 turnId 同样取 invocation-local myTurnId。
                  turnId: myTurnId ?? undefined,
                },
              ];
            }
            return prev;
          });
          if (finalDone) {
            setStreaming(false);
            setSendingFor(sendSessionKey, null);
            scheduleFinalCleanup();
          }
          animId = null;
          persistReveal();
          return;
        }
        persistReveal();
        const snap = displayed;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          // Update the LAST assistant bubble regardless of timestamp.  After a
          // switch-back, load() may have rendered the persisted full reply with
          // a different timestamp than this typewriter's ts — matching on ts
          // would MISS it and append a duplicate bubble.  If the last message
          // is already this exact content, no-op; if it's an assistant (the
          // reply being revealed), replace its content with the latest chunk.
          if (last?.role === 'assistant') {
            if (last.content === snap) return prev;
            return [...prev.slice(0, -1), { ...last, content: snap }];
          }
          // First chunk: insert the assistant bubble prefilled with content,
          // never as an empty placeholder.
          return [...prev, { role: 'assistant', content: snap, timestamp: Date.now() }];
        });
      }
      // Always reschedule — even while away — so the animation resumes the
      // moment the user returns to this session.
      animId = requestAnimationFrame(revealNext);
      revealAnimIdRef.current = animId;
      persistReveal();
    };

    // The exact routing key this invocation passes to chat.send.  For
    // thread-scoped sessions it differs from sendSessionKey
    // (`desktop:<threadId>` vs the session key), so the handlers must filter
    // on THIS value, not sendSessionKey.  Every IPC handler drops events
    // tagged with a different key before the cache/live branch — otherwise
    // overlapping sends across sessions would each process (and settle on)
    // the other's events.
    const routingKey =
      activeThreadId === 'main' ? currentSessionRef.current : `desktop:${activeThreadId}`;

    // Track last progress event time for watchdog
    let lastEventAt = Date.now();
    // 思考过程实时可见后，普通等待不再提示（用户要求 #539）：只在真正
    // 卡死（60s 无任何事件）时给出强警告，避免噪音。
    const NO_PROGRESS_STRONG_MS = 60_000; // 60s — "really stuck" warning
    let warnMsgId: number | null = null; // timestamp of the last warning message
    let watchdogTimer: ReturnType<typeof setInterval> | null = null;

    // Helper: append watchdog message (idempotent — deduplicates via warnMsgId ref)
    const appendWatchdogMsg = (content: string) => {
      if (warnMsgId !== null) return; // already shown
      warnMsgId = Date.now();
      setMessages((prev) => [...prev, { role: 'error' as const, content, timestamp: warnMsgId! }]);
    };

    // Start watchdog timer
    watchdogTimer = setInterval(() => {
      // sendCleanup() clears watchdogTimer; if the interval fires after
      // that but before the OS dequeues it, bail immediately (#454).
      if (!watchdogTimer) return;
      if (finalDone) {
        clearInterval(watchdogTimer);
        watchdogTimer = null;
        return;
      }
      // The component now survives session switches (App.tsx removed
      // key={sessionKey}), so this turn's watchdog can outlive the session
      // it belongs to.  Never append warnings into a different session —
      // the user switched away; the warning belongs to this turn's own
      // session, which is handled when they switch back.
      if (currentSessionRef.current !== sendSessionKey) return;
      // While away, this session's events were routed into inFlightCacheRef
      // (not the live path), so lastEventAt was NOT updated — the watchdog
      // would otherwise falsely report "后端 60s 无响应" the moment we switch
      // back, even though the backend kept producing events.  Treat any
      // recent cached event as activity.
      const _cached = inFlightCacheRef.current.get(sendSessionKey);
      if (_cached && _cached.events.length > 0) {
        const latest = _cached.events[_cached.events.length - 1];
        if (Date.now() - latest.timestamp < NO_PROGRESS_STRONG_MS) {
          lastEventAt = Date.now();
        }
      }
      const elapsed = Date.now() - lastEventAt;
      if (elapsed >= NO_PROGRESS_STRONG_MS) {
        appendWatchdogMsg('⚠️ 后端 60s 无响应，可中止并检查运行日志。');
      }
    }, 5_000); // check every 5s
    watchdogTimerRef.current = watchdogTimer;

    // Kill ONLY this invocation's watchdog interval.  The success path uses
    // this instead of sendCleanup(): by the time the send promise resolves,
    // onFinal has already run (the bridge dispatches the terminal event
    // before settling the promise) and scheduled the typewriter reveal — a
    // full sendCleanup() there would cancel that animation frame and freeze
    // the final answer mid-reveal.
    const clearWatchdogTimer = () => {
      if (watchdogTimer) {
        clearInterval(watchdogTimer);
        // Identity check BEFORE nulling the local — the shared ref may
        // already point at a replacement turn's watchdog.
        if (watchdogTimerRef.current === watchdogTimer) watchdogTimerRef.current = null;
        watchdogTimer = null;
      }
    };

    const sendCleanup = () => {
      clearWatchdogTimer();
      // Also stop the typewriter frame — otherwise an unmount while a send is
      // in flight leaves the RAF loop scheduling on an unmounted component.
      if (animId !== null) {
        cancelAnimationFrame(animId);
        animId = null;
      }
      // Identity check BEFORE nulling the shared ref — a newer send may have
      // already claimed it, and a settled old invocation's cleanup must not
      // strand the newer send's watchdog (which relies on this ref for
      // unmount cleanup).
      if (activeSendCleanupRef.current === sendCleanup) activeSendCleanupRef.current = null;
      // NOTE: cleanupListeners() is deliberately NOT called here.
      // The typewriter completing does not mean the turn is over —
      // another final may still arrive (e.g. tool-call then final-text).
      // Listeners are torn down only on abort / error / new-session.
    };
    activeSendCleanupRef.current = sendCleanup;

    const scheduleFinalCleanup = () => {
      if (finalCleanupTimerRef.current) return;
      finalCleanupTimerRef.current = setTimeout(() => {
        finalCleanupTimerRef.current = null;
        sendCleanup();
        if (onChatFinished) onChatFinished();
      }, 100);
    };

    const unsubProgress = window.miqi.chat.onProgress((data: ChatProgress) => {
      // Foreign-session event — another invocation's stream.  Drop it here;
      // the owning invocation's handlers process it.  Untagged legacy events
      // fall through (back-compat: treated as this send's own).
      if (data.session_key && data.session_key !== routingKey) return;
      // Accepted events route under THIS invocation's UI session owner.  The
      // routing key can differ from the session key for thread-scoped sends
      // (desktop:<threadId>) — routing by it would cache events under a key
      // load() never looks up, silently dropping the stream on switch-back.
      const _owner = sendSessionKey;
      if (_owner !== currentSessionRef.current) {
        var buf = inFlightCacheRef.current.get(_owner);
        if (!buf) {
          buf = { events: [], userMsgTimestamp: 0 };
          inFlightCacheRef.current.set(_owner, buf);
        }
        buf.events.push({ type: 'progress', data, timestamp: Date.now() });
        return;
      }
      lastEventAt = Date.now();
      // #798: retract a previously-shown watchdog warning once events
      // resume — a recovered backend must not leave a stale error behind.
      if (warnMsgId !== null) {
        const retracted = warnMsgId;
        warnMsgId = null;
        setMessages((prev) =>
          prev.filter((m) => !(m.role === 'error' && m.timestamp === retracted))
        );
      }
      // The backend has started streaming — the send was accepted.  Clear the
      // pending spinner on the optimistic user bubble (issue #364).
      setSendingFor(_owner, null);
      if (isCurrentPendingSend(_owner, thisSendId)) {
        pendingSendIdsRef.current.delete(_owner);
      }

      // ── Document progress events ───────────────────────────────
      if (data.type === 'doc_progress' && data.file) {
        setAttachments((prev) =>
          prev.map((a) => {
            if (a.name !== data.file || a.type !== 'document') return a;
            const stage = data.stage ?? 'parsing';
            const status =
              stage === 'ready' || stage === 'done'
                ? 'done'
                : stage === 'error'
                  ? 'error'
                  : 'parsing';
            return {
              ...a,
              status,
              parseError: status === 'error' ? (data.message ?? '') : a.parseError,
            };
          })
        );
        return;
      }

      // ── Platform points billing notices ─────────────────────────
      // 平台计费事件（当前仅 Slurm MCP 作业运行扣 10 分）通过 progress
      // 事件推送结果：billed = 已扣费（安静的活动行）；blocked = 扣费
      // 未完成（余额不足/登录过期/计费服务不可用，醒目错误行）——作业已
      // 进入运行，扣费失败不阻断任务（见 main/ipc/index.ts 的
      // slurm_job_running 处理）。渲染逻辑与缓存回放共用
      // pointsEventToMessage，保证切会话后通知不丢。
      {
        const pointsMessage = pointsEventToMessage(data);
        if (pointsMessage) {
          setMessages((prev) => [...prev, pointsMessage]);
          return;
        }
      }

      // ── Turn start (turn lifecycle) ─────────────────────────────────
      // The backend announces the active turn id when it starts. Terminal
      // events (final/aborted/error) tagged with a different turn id are
      // stale and dropped — see the guards in the final/aborted/error
      // listeners (#542).  Tracked per invocation so another session's
      // turn_started can't make this turn's terminals look stale.
      if (data.stream === 'turn' && typeof data.turn_id === 'string') {
        myTurnId = data.turn_id;
        activeTurnIdRef.current = data.turn_id;
        return;
      }

      // ── Live reasoning stream (thinking models) ──────────────────────
      // Append every delta to the LAST live thinking bubble in the message
      // list. The scan is deliberately state-driven (not a closure-local
      // timestamp) so StrictMode re-invocation or an effect re-creation can
      // never spawn a second "思考中…" block.
      //
      // Throttled: reasoning deltas arrive in a fast stream; appending each
      // one triggers a full messages rebuild + markdown re-render in
      // ThinkBlock, which makes thinking display slowly.  Buffer and flush on
      // a short timer.
      if (data.stream === 'reasoning' && typeof data.delta === 'string') {
        const ts = Date.now();
        liveReasoningTsRef.current = ts;
        // First reasoning delta of the turn — anchor pure thinking duration.
        if (thinkingStartedAtRef.current === null) {
          thinkingStartedAtRef.current = ts;
        }
        // Track the thinking "end": the final delta before the turn's last
        // tool pause, so reasoningElapsedS excludes tool-execution time.
        lastReasoningDeltaAtRef.current = ts;
        reasoningBufRef.current += data.delta;
        if (!reasoningTimerRef.current) {
          const flushSession = sessionKey;
          reasoningTimerRef.current = setTimeout(() => {
            reasoningTimerRef.current = null;
            // Audit guard: only flush into the SAME session we were streaming
            // in — a switch inside the 60ms window must not leak A's thinking
            // into B's message list.
            if (currentSessionRef.current !== flushSession) return;
            const buffered = reasoningBufRef.current;
            reasoningBufRef.current = '';
            if (buffered) {
              setMessages((prev) => appendReasoningDelta(prev, buffered, ts, reasoningMode));
            }
          }, 60); // ~16 fps effective — smooth without per-chunk re-render
        }
        return;
      }

      // Handle stream deltas from exec (Phase 7 inline tool progress)
      if (data.stream && data.delta && data.tool_call_id) {
        const stream = data.stream;
        const delta = data.delta;
        const toolCallId = data.tool_call_id;
        setExecOutputs((prev) => {
          const current = prev[toolCallId] || { stdout: '', stderr: '', running: true };
          const streamKey = stream === 'stdout' ? 'stdout' : 'stderr';
          return {
            ...prev,
            [toolCallId]: {
              ...current,
              [streamKey]: current[streamKey] + delta,
            },
          };
        });
        return;
      }

      // #879: web_sources structured sources from WebSearchTool/WebFetchTool.
      // Arrives as a progress delta ({delta, tool_call_id, tool_hint}) which
      // extractProgressMessage() returns null for — handle it before that gate.
      if (data.delta && typeof data.delta === 'string' && data.tool_call_id) {
        try {
          const inner = JSON.parse(data.delta);
          if (
            inner?.type === 'web_sources' &&
            Array.isArray(inner.payload?.sources) &&
            inner.payload.sources.length
          ) {
            const structured: MessageSource[] = inner.payload.sources.map(
              (s: { title?: string; url?: string; snippet?: string; tool?: string }) => ({
                tool: s.tool || 'web_search',
                url: s.url || '',
                title: s.title,
                snippet: s.snippet,
              })
            );
            const webToolName = structured[0]?.tool || 'web_search';
            // #879 ③：按回合累积 sources，供文件卡片显示「相关引用」。
            const turnSeq = turnSeqRef.current;
            setTurnSourcesMap((prev) => {
              const next = new Map(prev);
              const acc = next.get(turnSeq) ?? [];
              const seen = new Set(acc.map((s) => s.url));
              for (const s of structured) {
                if (s.url && !seen.has(s.url)) {
                  seen.add(s.url);
                  acc.push(s);
                }
              }
              next.set(turnSeq, acc);
              return next;
            });
            setMessages((prev) => {
              for (let i = prev.length - 1; i >= 0; i -= 1) {
                const m = prev[i];
                if (m.role === 'progress' && m.toolHint && m.toolCallId === data.tool_call_id) {
                  const next = [...prev];
                  next[i] = { ...m, webSources: structured, toolName: webToolName };
                  return next;
                }
              }
              return prev;
            });
          }
        } catch {
          /* not JSON, ignore */
        }
      }

      // Try structured extraction first, then fall back to raw text
      const extracted = extractProgressMessage(data as ProgressPayload);

      if (extracted) {
        const msgRole =
          extracted.role === 'error'
            ? ('error' as const)
            : extracted.role === 'warning'
              ? ('progress' as const) // warnings render as progress with warning style
              : ('progress' as const);
        // Detect paper_search result from backend events
        let toolName: string | undefined;
        let toolData: unknown;
        // Path A: item/toolResult notification (from turn_event_adapter)
        if (!toolData && data.tool_hint && data.text && !data.stream) {
          const parsed = tryParsePaperSearchResult(data.text);
          if (parsed?.items?.length) {
            toolName = 'paper_search';
            toolData = parsed;
          }
        }
        // Path B: toolExecution/outputDelta from PaperSearchTool itself
        if (!toolData && data.delta && typeof data.delta === 'string') {
          try {
            const inner = JSON.parse(data.delta);
            if (inner?.type === 'paper_search_result' && inner.payload) {
              toolName = 'paper_search';
              toolData = inner.payload;
            }
          } catch {
            /* not JSON, ignore */
          }
        }

        const toolMsg: Message = {
          role: msgRole,
          content: extracted.role === 'warning' ? `⚠️ ${extracted.message}` : extracted.message,
          toolHint: data.tool_hint || toolName === 'paper_search',
          toolCallId: data.tool_call_id,
          toolName,
          toolData,
          toolArgs: data.tool_args
            ? data.tool_args
            : data.tool_call_id
              ? toolArgsByCallId.current.get(data.tool_call_id)
              : undefined,
          // CodeRabbit（9-11）：链卡按 turn 归属需要行级 turnId（实时路径）
          // #1071 review P1（2026-09-16）：binding 必须用 invocation-local 的 myTurnId——
          // activeTurnIdRef 跨 invocation 共享，别的 turn 的 turn_started 会改写它，
          // 导致本消息被绑到错误的 turn。（review 只点了 reveal 三处，此处同一竞态一并统一）
          turnId: myTurnId ?? undefined,
          timestamp: Date.now(),
        };
        setMessages((prev) => {
          // Tool begin/end events share a tool_call_id: update the existing
          // row instead of stacking a second block, keeping one chain node
          // per tool call.
          if (toolMsg.toolHint && toolMsg.toolCallId) {
            for (let i = prev.length - 1; i >= 0; i -= 1) {
              const m = prev[i];
              if (m.role === 'progress' && m.toolHint && m.toolCallId === toolMsg.toolCallId) {
                const next = [...prev];
                next[i] = {
                  ...m,
                  content: toolMsg.content,
                  toolName: toolMsg.toolName ?? m.toolName,
                  toolData: toolMsg.toolData ?? m.toolData,
                  toolArgs: toolMsg.toolArgs ?? m.toolArgs,
                };
                return next;
              }
            }
          }
          return [...prev, toolMsg];
        });
        // End event carries the tool result — stash web_search output so the
        // row can expand into result cards on click (#539).
        const endCallId = data.tool_call_id;
        const endOutput = data.tool_output;
        if (endOutput && endCallId) {
          setSearchResultsByCallId((prev) => ({
            ...prev,
            [endCallId]: endOutput,
          }));
        }
      } else if (data.tool_hint || data.stream) {
        // tool_hint without text still deserves a line (old behavior for exec hints)
        // but skip completely empty/stream-only events
        return;
      }
      // Otherwise skip — no displayable content

      // Parse file operations from tool hints
      if (data.tool_hint && data.text) {
        const parsed = parseToolHint(data.text);
        if (parsed) trackFile(parsed.path, parsed.op, parsed.truncated, turnSeqRef.current);
      }
    });

    const unsubFinal = window.miqi.chat.onFinal((data: ChatFinal) => {
      // Foreign-session terminal — another invocation's turn; its handler
      // settles it.  Untagged legacy events fall through (back-compat).
      if (data.session_key && data.session_key !== routingKey) return;
      // Route under the UI session owner — see the progress listener.
      const _owner = sendSessionKey;
      if (_owner !== currentSessionRef.current) {
        var buf = inFlightCacheRef.current.get(_owner);
        if (!buf) {
          buf = { events: [], userMsgTimestamp: 0 };
          inFlightCacheRef.current.set(_owner, buf);
        }
        buf.events.push({ type: 'final', data, timestamp: Date.now() });
        return;
      }
      // Final from a superseded turn (e.g. a pre-abort final racing a quick
      // resend): the backend's turn id is authoritative — drop it so the
      // replacement turn's UI state is untouched (#542). Strict match: a
      // tagged event must equal THIS invocation's own turn id; while the
      // turn's turn_started has not arrived yet (id is null), any tagged
      // terminal event is by definition stale. The superseded turn's
      // lifecycle promise is settled by its own closure.
      //
      // BACKEND CONTRACT: turn_started (task_runner.py emits TurnStartedEvent
      // before any model call) always precedes every terminal event of a turn.
      // If that ever changes (e.g. an error emitted before turn creation),
      // this strict-match logic silently drops the legitimate event.
      if (data.turn_id && data.turn_id !== myTurnId) {
        return;
      }
      clearFinalCleanupTimer();
      if (animId !== null) {
        cancelAnimationFrame(animId);
        animId = null;
      }
      fullContent = data.content;
      displayed = '';
      finalDone = true;
      persistReveal();
      streamingBySession.delete(_owner);
      setCurrentReqId(null);
      // If load() already rendered this final (merged from history/cache), the
      // reply is on screen — don't append a duplicate via the live path.  Just
      // stop streaming; the bubble is already complete.
      if (finalHandledSessions.has(_owner)) {
        finalHandledSessions.delete(_owner);
        setStreaming(false);
        setSendingFor(sendSessionKey, null);
        streamingBySession.delete(_owner);
        scheduleFinalCleanup();
        return;
      }
      // Final answer arrived — drop the watchdog "waiting" hint; it must only
      // be visible while the backend is actually working (#539 用户要求).
      if (warnMsgId !== null) {
        const watchdogId = warnMsgId;
        warnMsgId = null;
        setMessages((prev) =>
          prev.filter((m) => !(m.role === 'error' && m.timestamp === watchdogId))
        );
      }
      // Keep the thinking block at its original position in the timeline
      // (before tool calls). A live bubble is finalized in place; otherwise
      // the block is inserted right after the user message. The assistant
      // bubble never re-renders reasoning, so there is no layout jump.
      const hadLiveReasoning = liveReasoningTsRef.current !== null;
      const finalReasoningElapsedS =
        // CR #856-7: normalize the server value the same way as the cache /
        // snapshot paths (≥1s, rounded) so live and restored views agree.
        data.reasoning_elapsed_s != null
          ? Math.max(1, Math.round(data.reasoning_elapsed_s))
          : data.reasoning || hadLiveReasoning
            ? // Pure thinking span: first→last reasoning delta. Falls back to the
              // final-event time when no live reasoning was seen. Never 0s.
              // (#834) Server-measured value arrives as reasoning_elapsed_s and
              // is preferred — this local span is only the transport-time
              // fallback for buffered providers.
              Math.max(
                1,
                Math.round(
                  ((lastReasoningDeltaAtRef.current ?? Date.now()) -
                    (thinkingStartedAtRef.current ?? turnStartMs)) /
                    1000
                )
              )
            : undefined;
      thinkingStartedAtRef.current = null;
      lastReasoningDeltaAtRef.current = null;
      // Close any live reasoning block — whether or not this render's session
      // set liveReasoningTsRef.  A live block can be restored from the
      // snapshot/cache after a switch-back, in which case the ref is null but
      // the block's isLiveReasoning is still true; without this it would stay
      // stuck showing "思考中…" even after the reply finished.
      const _closeLiveReasoning = (prev: Message[]) =>
        prev.some((m) => m.isLiveReasoning)
          ? prev.map((m) =>
              m.isLiveReasoning
                ? {
                    ...m,
                    isLiveReasoning: false,
                    content: data.reasoning || m.content,
                    reasoning: data.reasoning || m.content,
                    reasoningElapsedS: finalReasoningElapsedS,
                  }
                : m
            )
          : prev;
      if (hadLiveReasoning || data.reasoning) {
        // Order matters (audit P0-3): FLUSH the buffered reasoning deltas
        // FIRST (they append to the still-live block), THEN close the live
        // block.  The old order (close-then-flush) made appendReasoningDelta
        // create a brand-new isLiveReasoning block on the closed state —
        // a permanently stuck "思考中…" under the answer.
        flushReasoningRef.current?.(Date.now());
        setMessages((prev) => {
          const cleaned = _closeLiveReasoning(prev);
          if (hadLiveReasoning) return cleaned;
          // data.reasoning present without a live block → insert standalone.
          if (
            data.reasoning &&
            !cleaned.some(
              (m) => m.role === 'progress' && m.reasoning && m.reasoning === data.reasoning
            )
          ) {
            return insertStandaloneReasoning(cleaned, data.reasoning, finalReasoningElapsedS);
          }
          return cleaned;
        });
        liveReasoningTsRef.current = null;
      }
      if (data.tool_calls?.length) {
        // Track file operations from tool_calls for Task Assets panel.
        // Office tools (create_docx, etc.) don't always produce progress
        // hints that match parseToolHint patterns, so we extract file
        // paths directly from the final tool call list.
        for (const tc of (data.tool_calls ?? []) as any[]) {
          const fn = tc?.function || tc?.tool?.function || {};
          const toolName: string = fn?.name || '';
          if (!toolName) continue;
          // Remember call args so the matching tool result can show the exact
          // URL the tool touched (web_fetch etc.) in "查看来源".
          const callId: string = tc?.id || '';
          if (callId && fn?.arguments) {
            try {
              toolArgsByCallId.current.set(callId, JSON.parse(fn.arguments));
            } catch {
              toolArgsByCallId.current.set(callId, fn.arguments);
            }
          }
          const filePath: string = _extractPathFromArgs(fn?.arguments || '{}') || '';
          if (!filePath) continue;
          if (_FILE_WRITE_TOOLS.includes(toolName)) {
            trackFile(filePath, 'write', false, turnSeqRef.current, toolName);
          } else if (_FILE_READ_TOOLS.includes(toolName)) {
            trackFile(filePath, 'read', false, turnSeqRef.current, toolName);
          }
        }

        // Reload tracked files from the backend — _persist_tracked_file saves the
        // correct session-relative path (e.g. sessions/<key>/files/report.pdf) while
        // _extractPathFromArgs only sees the bare filename from AI tool call args.
        // Merge backend data on top: it wins when keys collide.
        window.miqi.sessions.getTrackedFiles(currentSessionRef.current!).then(
          (tfResult: any) => {
            const tfList: any[] = tfResult?.tracked_files ?? [];
            if (tfList.length) {
              setTrackedFiles((prev) => {
                const mapped = tfList.map((f: any) => ({
                  path: (f.path as string).replace(/\\/g, '/'),
                  name: f.name,
                  op: f.op,
                  lastSeen: f.lastSeen ?? Date.now(),
                  // #1104: declare_result_files 写入的显式结果标记（回合结束刷新
                  // 必须带上，否则标记只活到下一次刷新）
                  result: f.result === true,
                }));
                return mergeTrackedFiles(prev, mapped, workspace);
              });
            }
          },
          () => {
            /* non-fatal */
          }
        );

        setMessages((prev) => {
          const cleaned = removeTransientTurnMessagesSinceLastUser(prev);
          // Only append collapsed tool-call group if streaming didn't
          // already render toolHint progress for this turn (avoids dupes).
          const hasToolHints = cleaned.some((m) => m.role === 'progress' && m.toolHint);
          if (hasToolHints) return cleaned;
          const toolMessages = sessionMsgsToUi([
            {
              role: 'assistant',
              content: '',
              tool_calls: data.tool_calls,
              timestamp: new Date().toISOString(),
            },
          ]);
          return [...cleaned, ...toolMessages];
        });
      } else {
        setMessages((prev) => removeTransientTurnMessagesSinceLastUser(prev));
      }
      // Do NOT push an empty assistant bubble here — revealNext creates the
      // bubble lazily once the first chunk is available, so we never flash a
      // blank message box. Handle the empty-reply case (no text at all)
      // immediately instead of waiting on an animation that has nothing to show.
      if (!fullContent) {
        setStreaming(false);
        setSendingFor(sendSessionKey, null);
        scheduleFinalCleanup();
        return;
      }
      setStreaming(true);
      // Restart the typewriter clock so the remaining content reveals at
      // typewriter speed instead of jumping straight to the full text (the
      // chain may have been idle for a while — e.g. it broke on catch-up and
      // lastTickTs is stale).  Persist immediately: if the user switches
      // sessions before the next animation callback, revealBySession must
      // already hold the fresh timestamp or the resumed first frame would
      // treat the idle interval as reveal time and skip the typewriter.
      lastTickTs = performance.now();
      animId = requestAnimationFrame(revealNext);
      revealAnimIdRef.current = animId;
      persistReveal();
    });

    const unsubError = window.miqi.chat.onError((data: ChatError) => {
      // Foreign-session terminal — another invocation's turn; its handler
      // settles it.  Untagged legacy events fall through (back-compat).
      if (data.session_key && data.session_key !== routingKey) return;
      // Route under the UI session owner — see the progress listener.
      const _owner = sendSessionKey;
      if (_owner !== currentSessionRef.current) {
        var buf = inFlightCacheRef.current.get(_owner);
        if (!buf) {
          buf = { events: [], userMsgTimestamp: 0 };
          inFlightCacheRef.current.set(_owner, buf);
        }
        buf.events.push({ type: 'error', data, timestamp: Date.now() });
        return;
      }
      // Error from a superseded turn (e.g. an abort-induced error racing a
      // quick resend): drop it so the replacement turn's UI state is
      // untouched (#542). Strict match — see the final listener.
      if (data.turn_id && data.turn_id !== myTurnId) {
        return;
      }
      streamErrorHandled = true;
      if (animId !== null) cancelAnimationFrame(animId);
      const message = sanitizeUiMessage(data.message);
      flushReasoningRef.current?.(Date.now());
      liveReasoningTsRef.current = null;
      setMessages((prev) => [
        ...prev.filter((m) => !m.isLiveReasoning),
        isProviderConfigurationProblem(message, data.code)
          ? createProviderConfigMessage(
              requiresReloginRef.current ? RELOGIN_INTERCEPT_TEXT : message,
              loggedInRef.current && !requiresReloginRef.current
                ? 'open-provider-settings'
                : 'login',
              // 已登录且凭据未失效时：凭据配置入口已不存在（#835 收口），
              // NO_API_KEY 只可能是当前模型不走平台网关，落点是重选模型
              // 而非「配置模型」。登录失效时走重登引导，不覆盖其标签。
              loggedInRef.current && !requiresReloginRef.current ? '去选择模型' : undefined
            )
          : { role: 'error', content: message, timestamp: Date.now() },
      ]);
      setStreaming(false);
      setSendingFor(sendSessionKey, null);
      streamingBySession.delete(sendSessionKey);
      sendCleanup();
      // Identity-scoped: only THIS invocation's listeners — the shared
      // unsubsRef may point at a newer overlapping send.
      cleanupListeners(myUnsubs);
      sendInvocationRegistryRef.current.delete(thisSendId);
    });

    const unsubAborted = window.miqi.chat.onAborted((_data: ChatAborted) => {
      // Foreign-session terminal — another invocation's turn; its handler
      // settles it.  Untagged legacy events fall through (back-compat).
      if (_data.session_key && _data.session_key !== routingKey) return;
      // Route under the UI session owner — see the progress listener.
      const _owner = sendSessionKey;
      if (_owner !== currentSessionRef.current) {
        var buf = inFlightCacheRef.current.get(_owner);
        if (!buf) {
          buf = { events: [], userMsgTimestamp: 0 };
          inFlightCacheRef.current.set(_owner, buf);
        }
        buf.events.push({ type: 'aborted', data: _data, timestamp: Date.now() });
        return;
      }
      // Stale terminal event from a superseded turn: stop-then-quick-send
      // orphans the old aborted event, which would otherwise drop
      // streaming=false and append a spurious "已停止。" bubble mid-reply.
      // The backend's turn id is authoritative; the grace window is only a
      // fallback for bridges that don't emit turn ids (legacy/mocks).
      if (_data.turn_id) {
        // Strict match — a tagged event must equal THIS invocation's own
        // turn id; while the turn's turn_started has not arrived yet (id is
        // null), any tagged terminal event is by definition stale.
        if (_data.turn_id !== myTurnId) {
          return;
        }
      } else if (Date.now() - sendStartedAt < TURN_TERMINAL_GRACE_MS) {
        return;
      }
      if (animId !== null) cancelAnimationFrame(animId);
      setStreaming(false);
      setSendingFor(sendSessionKey, null);
      streamingBySession.delete(sendSessionKey);
      setCurrentReqId(null);
      flushReasoningRef.current?.(Date.now());
      liveReasoningTsRef.current = null;
      setMessages((prev) => [
        ...prev.filter((m) => !m.isLiveReasoning),
        { role: 'progress', content: '已停止。', timestamp: Date.now() },
      ]);
      sendCleanup();
    });

    // Capture THIS invocation's unsubs locally: by the time this send's
    // promise settles, unsubsRef may point at a NEWER send's listeners, so
    // self-cleanup must never go through the shared ref.
    const myUnsubs = [unsubProgress, unsubFinal, unsubError, unsubAborted];
    unsubsRef.current = myUnsubs;
    unsubsSessionRef.current = sendSessionKey;
    // Register this invocation so unmount (and settle) can dispose its
    // resources even when it is no longer the latest send.
    sendInvocationRegistryRef.current.set(thisSendId, {
      unsubs: myUnsubs,
      cleanup: sendCleanup,
      sessionKey: sendSessionKey,
    });

    try {
      // On first message for a new conversation, create a thread with
      // a title derived from the user's first prompt.
      let threadId = currentThreadIdRef.current;
      if (threadId == null) {
        try {
          const title = (text || '新会话').trim().slice(0, 60);
          // Non-blocking: start thread with a timeout so chat.send
          // isn't delayed by a slow bridge restart.  Falls through to
          // chat.send without thread_id on failure.
          // 30s timeout gives sandbox first-init (WSL apt-get 60-120s)
          // a better chance without holding up the UI forever (#311).
          const threadResult = await Promise.race([
            window.miqi.threads.start({
              title,
              session_key: currentSessionRef.current,
            }),
            new Promise<never>((_, reject) =>
              setTimeout(() => reject(new Error('thread/start timeout')), 30_000)
            ),
          ]);
          // Extract thread id from the result
          const thread = (threadResult as any)?.thread;
          if (thread) {
            threadId = thread.id || thread.threadId;
            if (threadId) {
              currentThreadIdRef.current = threadId;
            }
          }
        } catch {
          // If thread/start fails, fall through to chat.send without thread_id
        }
      }

      // Same routing key the listeners filter on — the send call and the
      // handlers must agree, or this turn's own stream would be dropped as
      // foreign before it reaches the cache/live branch.
      const key = routingKey;
      const chatAttachments = sentAttachments
        .filter((a) => (a.type === 'document' && a.dataBase64) || (a.type === 'image' && a.dataUrl))
        .map((a) => ({
          name: a.name,
          data_base64:
            a.type === 'image' && a.dataUrl ? (a.dataUrl.split(',')[1] ?? a.dataUrl) : a.dataBase64,
          mime_type: a.mimeType,
        }));

      // Mark all doc attachments as parsing
      if (sentAttachments.some((a) => a.type === 'document')) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.role === 'user' && last.attachments) {
            const updated = last.attachments.map((a) =>
              a.type === 'document' ? { ...a, status: 'parsing' as const } : a
            );
            return [...prev.slice(0, -1), { ...last, attachments: updated }];
          }
          return prev;
        });
      }

      // Fire send — server parses synchronously in _chat_send_handler
      // #1011 P1(baiye-banned review):判定点必须早于「请求可能已送出」的
      // 第一刻。chat.send 内部是 ipcRenderer.invoke → main → bridge.send,
      // 一旦调用,即使 Promise 之后 reject,请求也可能已被后端接收并开始
      // turn —— 此时恢复旧 snapshot 会造成前后端状态分叉。因此:
      //   · chat.send 调用之前的失败(附件/内容构造/thread start)= 确定未派发 → 允许恢复
      //   · 调用之后的一切失败(resolve 或 reject 皆然)= 可能已派发 → 不恢复
      //     (与普通发送失败语义一致:保留列表 + 错误提示)
      // #1011 P3(baiye-banned 终审):区分「同步 throw」——preload 的 chat.send
      // 是普通函数,参数序列化失败 / API 缺失会在调用时同步抛出,此时请求
      // 从未进入 IPC;仅在调用成功返回 Promise 后才标记 dispatched,
      // 同步 throw 交由外层 catch 走「确定未派发」的恢复路径。
      let sendPromise: Promise<unknown>;
      try {
        sendPromise = window.miqi.chat.send(
          content,
          key,
          threadId ?? undefined,
          executionPolicy,
          chatAttachments.length > 0 ? chatAttachments : undefined,
          workspace ?? undefined,
          reasoningModeRef.current,
          _resumeId ?? undefined
        );
        turnDispatched = true;
      } catch (syncSendError) {
        // 同步 throw:未进入 IPC —— 保持 turnDispatched=false,允许恢复
        throw syncSendError;
      }
      // 请求已发出 —— 清除本次 send 的编辑回滚点(此后失败一律不恢复,
      // 见上方 turnDispatched 注释;此处删除防 Map 泄漏)
      editRollbacksRef.current.delete(thisSendId);

      // Mark as done after a tick — server parsing is synchronous, already complete
      if (sentAttachments.some((a) => a.type === 'document')) {
        setTimeout(() => {
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === 'user' && last.attachments) {
              const updated = last.attachments.map((a) =>
                a.type === 'document' && a.status === 'parsing'
                  ? { ...a, status: 'done' as const }
                  : a
              );
              return [...prev.slice(0, -1), { ...last, attachments: updated }];
            }
            return prev;
          });
        }, 100);
      }

      await sendPromise;
      settleLifecycle();
      // The turn's promise settled, but the terminal LISTENER may never have
      // run: a later send unsubscribed it (cross-session listener kill), or
      // the turn-id guard dropped the terminal event.  Without this cleanup
      // the invocation's 60s watchdog survives as a zombie and, once the user
      // is back on this session with the in-flight cache flushed, fires a
      // false "后端 60s 无响应" into the message list while the backend is
      // streaming fine.  Deliberately NOT sendCleanup(): onFinal ran before
      // this promise resolved (the bridge dispatches the terminal event
      // first) and already scheduled the typewriter reveal — sendCleanup()
      // would cancel that animation frame and freeze the final answer
      // mid-reveal.  Only THIS invocation's watchdog and listeners are
      // cleaned up here.
      clearWatchdogTimer();
      for (const unsub of myUnsubs) unsub();
      if (unsubsRef.current === myUnsubs) {
        unsubsRef.current = [];
        unsubsSessionRef.current = null;
      }
      sendInvocationRegistryRef.current.delete(thisSendId);
    } catch (e: any) {
      if (animId !== null) cancelAnimationFrame(animId);
      if (streamErrorHandled) {
        settleLifecycle();
        setStreaming(false);
        setSendingFor(sendSessionKey, null);
        // 流错误已在流处理器内渲染(turn 已派发)——仅清理本次回滚点防泄漏
        editRollbacksRef.current.delete(thisSendId);
        sendCleanup();
        // Identity-scoped: only THIS invocation's listeners — the shared
        // unsubsRef may point at a newer overlapping send.
        cleanupListeners(myUnsubs);
        sendInvocationRegistryRef.current.delete(thisSendId);
        return;
      }
      const errMsg = sanitizeUiMessage(e?.message ?? String(e ?? '未知错误'));
      // 编辑重答(#1011 P1):仅当「确定未派发」(chat.send 尚未送出)时才恢复
      // 截断前的完整列表;已送出后的 reject 可能请求已达后端,恢复会造成
      // 前后端状态分叉 —— 此时保留截断后的列表 + 错误提示。
      const sendFailRollback = editRollbacksRef.current.get(thisSendId);
      editRollbacksRef.current.delete(thisSendId);
      const sendFailRollbackApplies =
        !turnDispatched &&
        !!sendFailRollback &&
        sendFailRollback.sessionKey === sendSessionKey &&
        currentSessionRef.current === sendSessionKey;
      if (isProviderConfigurationProblem(errMsg, e?.code)) {
        const failMsg = createProviderConfigMessage(
          requiresReloginRef.current ? RELOGIN_INTERCEPT_TEXT : errMsg,
          loggedInRef.current && !requiresReloginRef.current ? 'open-provider-settings' : 'login'
        );
        setMessages((prev) =>
          sendFailRollbackApplies ? [...sendFailRollback!.snapshot, failMsg] : [...prev, failMsg]
        );
      } else if (sendFailRollbackApplies) {
        setMessages([
          ...sendFailRollback!.snapshot,
          { role: 'error' as const, content: errMsg, timestamp: Date.now() },
        ]);
      } else if (e?.code) {
        setMessages((prev) => [
          ...prev,
          { role: 'error' as const, content: errMsg, timestamp: Date.now() },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: 'error' as const, content: errMsg, timestamp: Date.now() },
        ]);
      }
      settleLifecycle();
      setStreaming(false);
      setSendingFor(sendSessionKey, null);
      sendCleanup();
      // Identity-scoped: only THIS invocation's listeners — the shared
      // unsubsRef may point at a newer overlapping send.
      cleanupListeners(myUnsubs);
      sendInvocationRegistryRef.current.delete(thisSendId);
    }
  }, [
    attachments,
    streaming,
    cleanupListeners,
    onChatFinished,
    executionPolicy,
    workspace,
    reasoningMode,
  ]);

  // Keep handleSendRef fresh for programmatic sends (regenerate)
  useEffect(() => {
    handleSendRef.current = () => handleSend();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleSend]);

  // #1042: stable props for the memoized Composer. handleSend is a useCallback
  // and programmaticTextRef is a ref, so handleComposerSubmit only changes when
  // handleSend itself does.
  const handleComposerSubmit = useCallback(
    (text: string) => {
      programmaticTextRef.current = text;
      handleSend();
    },
    [handleSend]
  );
  const handleComplexHintDismiss = useCallback(() => setComplexHint(false), []);

  // ── Download paper via chat ─────────────────────────────────────
  const handleDownloadPaper = useCallback(
    (paper: PaperItem) => {
      const title = (paper.title || 'this paper').trim();
      const filenameBase =
        paper.arxiv_id ||
        paper.id ||
        paper.doi ||
        title.replace(/[\\/:*?"<>|]/g, '_').slice(0, 80) ||
        'paper';
      // #667: 有开放 PDF 直链 → 直接下载（Electron downloadURL，零 token 零 AI）。
      // 只接受有效的 HTTP(S) URL——无效直链不阻断后续候选/fallback
      // （CodeRabbit #668 review）。
      const candidates = [
        paper.open_access_pdf_url,
        paper.pdf_url,
        paper.arxiv_id ? `https://arxiv.org/pdf/${paper.arxiv_id}` : '',
      ].filter((u): u is string => !!u && /^https?:\/\//i.test(u));
      const directUrl = candidates[0];
      if (directUrl) {
        setDownloadingPaperId(paper.id || null);
        const filename = `${filenameBase}.pdf`;
        window.miqi.downloads
          .download(directUrl, filename)
          .then((res) => {
            if (res?.ok) {
              setDownloadingPaperId(null);
              // #668 补：成功反馈——按钮变「✓ 已下载」+ 打开文件夹
              setPaperDownloadStates((prev) => ({
                ...prev,
                [`${sessionKey}:${paper.id || ''}`]: { status: 'done', savePath: res.savePath },
              }));
              // #696 补：下载完成 toast（居中，1.5s 后淡出，2s 移除）
              if (res.savePath) {
                setDownloadToast({ filename, savePath: res.savePath });
                setToastVisible(true);
                setTimeout(() => setToastVisible(false), 1500);
                setTimeout(() => setDownloadToast(null), 2000);
              }
            } else {
              // 失败不再静默 fallback：显示失败原因（用户可决定是否让 AI 下载）；
              // 用户主动取消保存对话框 → 回默认状态（不算失败）
              setDownloadingPaperId(null);
              if (res.error === 'cancelled') {
                setPaperDownloadStates((prev) => {
                  const next = { ...prev };
                  delete next[`${sessionKey}:${paper.id || ''}`];
                  return next;
                });
              } else {
                setPaperDownloadStates((prev) => ({
                  ...prev,
                  [`${sessionKey}:${paper.id || ''}`]: {
                    status: 'failed',
                    error: res.error ?? '直链下载失败',
                  },
                }));
              }
            }
          })
          .catch((e: unknown) => {
            setDownloadingPaperId(null);
            setPaperDownloadStates((prev) => ({
              ...prev,
              [`${sessionKey}:${paper.id || ''}`]: {
                status: 'failed',
                error: e instanceof Error ? e.message : '直链下载异常',
              },
            }));
          });
        return;
      }
      // 无直链：fallback 让 AI 下载
      aiDownload(paper, title);
    },
    [sessionKey]
  );

  // Fallback: ask the AI to download the paper.
  const aiDownload = (paper: PaperItem, title: string) => {
    const pid = paper.arxiv_id || paper.id || paper.doi || title;
    const instruction = `请下载论文《${title}》的 PDF 文件。paperId: ${pid}`;
    setDownloadingPaperId(paper.id || null);
    // Set input and trigger send on next tick so React state propagates
    composerRef.current?.setText(instruction);
    setTimeout(() => {
      const text = instruction.trim();
      if (!text) {
        programmaticTextRef.current = null;
        setDownloadingPaperId(null);
        return;
      }
      // 经 handleSend 主流程发送（而非直连 chat.send）：网关门禁、乐观气泡
      // 与流式渲染路径一致（#922）。文本经 programmaticTextRef 显式传入。
      programmaticTextRef.current = instruction;
      handleSendRef.current();
      // 发出即清理下载指示：无论网关拦截（handleSend 恢复草稿）还是发送
      // 失败，指示都不悬挂；流式回复由 handleSend 的监听链负责渲染。
      setDownloadingPaperId(null);
    }, 0);
  };

  /** Normalise a sandbox-internal path to a host path that can be opened.
   *  Strips /home/miqi/workspace/ prefix so the path resolves correctly on the
   *  host filesystem.  Leaves relative paths and non-sandbox absolute paths
   *  unchanged — they are handled by the IPC handlers. */
  const normalizePath = useCallback((p: string): string => {
    return normalizeSandboxPath(p);
  }, []);

  const handlePreview = useCallback(async (rawPath: string) => {
    const path = normalizePath(rawPath);

    // HTML files: read the content directly so the preview can render it in
    // a sandboxed iframe. Try several resolutions because session isolation
    // (#731) changes where the file lives:
    //   1. as passed (full session-relative path) — resolves against the
    //      workspace root without a session key;
    //   2. as passed + current session key — resolves a bare name into the
    //      current session's files dir.
    // The old openExternal fallback cannot find session-isolated files at all.
    if (/\.html?$/i.test(path)) {
      const bare = path.split(/[\\/]/).pop()!;
      // Session-isolated files live under sessions/<safe-key>/files/. Build the
      // full workspace-relative path from the active session key and read it
      // WITH the session key: the bridge resolves a workspace-relative path
      // that lands inside the caller's own session directory (issue #1051),
      // and session-scoped reads are the only ones allowed to touch
      // sessions/ — a session-less read of that subtree is now rejected.
      const safeKey = sessionFilesDirKey(currentSessionRef.current);
      const fullRel = safeKey ? `sessions/${safeKey}/files/${bare}` : '';
      const reads: Array<Promise<{ content?: string }>> = [];
      if (fullRel && fullRel !== path)
        reads.push(window.miqi.files.read(fullRel, currentSessionRef.current ?? undefined));
      // Session-scoped read before the session-less one: the tracked path may
      // be a bare name, which only resolves with the session key.
      reads.push(window.miqi.files.read(path, currentSessionRef.current ?? undefined));
      reads.push(window.miqi.files.read(path));
      for (const attempt of reads) {
        try {
          const readResult = await attempt;
          if (readResult?.content) {
            setHtmlSourceMode(false);
            setPreviewFile({ path, content: readResult.content });
            return;
          }
        } catch {
          /* try next resolution */
        }
      }
    }

    // For document files (PDF, Word, Excel, Markdown, etc.):
    // try in-app parsing first — more reliable than system-open which
    // depends on OS file associations.  Fall back to system default
    // application only when parsing is unavailable.
    const isDocFile = DOCUMENT_SUFFIXES_RE.test(path);
    if (isDocFile) {
      // Collect candidate paths: the tracked path, then try common subdirs
      // (paper_search saves to workspace/papers/, office tools to workspace/ root).
      // Session-isolated files (#731) live under sessions/<safe-key>/files/ —
      // the full session-relative path is the ONLY form the bridge reliably
      // reads for bare tracked names (bare+session_key returns null at the
      // bridge, same finding as the HTML preview branch), so that candidate
      // is resolved workspace-scoped without a session key.
      const candidates: Array<{ p: string; withSession: boolean }> = [
        { p: path, withSession: true },
      ];
      // #1051: a full session-relative path (sessions/<safe>/files/<name>) is
      // resolved against the caller's own session directory by the bridge, so
      // it is read WITH the session key like any other candidate.
      const nameOnly = path.replace(/\\/g, '/').split('/').pop()!;
      if (nameOnly !== path) candidates.push({ p: nameOnly, withSession: true });
      if (!path.startsWith('papers/'))
        candidates.push({ p: `papers/${nameOnly}`, withSession: true });
      if (nameOnly === path) {
        const safeKey = sessionFilesDirKey(currentSessionRef.current);
        if (safeKey) {
          candidates.push({ p: `sessions/${safeKey}/files/${nameOnly}`, withSession: true });
        }
      }

      // #877: PDF — proper paginated rendering via the workspace iframe blob
      // approach (Chromium's built-in PDF viewer).  Text parsing stays as the
      // fallback for scanned PDFs / read failures.
      if (PDF_FILE_RE.test(path)) {
        for (const candidate of candidates) {
          try {
            const res = await window.miqi.files.read(
              candidate.p,
              candidate.withSession ? currentSessionRef.current : undefined
            );
            if (res?.data_base64) {
              setPreviewFile({
                path: candidate.p,
                kind: 'pdf',
                pdfUrl: base64ToBlobUrl(res.data_base64, res.mime_type || 'application/pdf'),
                // Keep the bytes alongside the blob URL: 「系统应用打开」 only
                // takes the reliable openBytes path when they are present, and
                // otherwise falls back to openExternal(candidate.p) — which
                // cannot resolve a bare name for a session-scoped file (#1131).
                dataBase64: res.data_base64,
              });
              return;
            }
          } catch {
            continue; // try next candidate
          }
        }
        // no binary read — fall through to the text parse below
      }

      for (const candidate of candidates) {
        try {
          const result = await window.miqi.documents.parse(
            candidate.p,
            candidate.withSession ? currentSessionRef.current : undefined,
            {
              preview: true,
              structured: true,
            }
          );
          // #877: rich renderers — spreadsheet table for XLSX/CSV, ordered
          // blocks for DOCX.  Fall back to plain text when the backend can't
          // produce structure (e.g. .xls/.odt have no structured support).
          if (
            result?.structured?.kind === 'spreadsheet' &&
            /\.(xlsx|xls|csv|ods)$/i.test(candidate.p)
          ) {
            setPreviewFile({
              path: candidate.p,
              kind: 'spreadsheet',
              spreadsheet: result.structured,
              content: result.text,
            });
            return;
          }
          if (result?.structured?.kind === 'document' && /\.(docx|doc|odt)$/i.test(candidate.p)) {
            setPreviewFile({
              path: candidate.p,
              kind: 'document',
              docBlocks: result.structured,
              content: result.text,
            });
            return;
          }
          if (result?.text) {
            setPreviewFile({ path: candidate.p, content: result.text });
            return;
          }
        } catch {
          continue; // try next candidate
        }
      }
    }
    // Open with system default application as fallback
    // #1062: 带上会话 key——文件夹绑定会话的产物在会话自己的工作区里，不带 key
    // 主进程只按全局工作区做包含性校验，会把它们判成「工作区之外」。
    let result: { opened?: boolean; error?: string } | null = null;
    try {
      result = (await window.miqi.files.openExternal(path, currentSessionRef.current)) ?? null;
    } catch (e: any) {
      result = { opened: false, error: e?.message ?? String(e) };
    }
    if (!result?.opened) {
      const outside = /outside workspace/i.test(String(result?.error ?? ''));
      setPreviewFile({
        path,
        content: outside
          ? `(无法预览：该文件在会话工作区之外，应用无权读取)\n\n${path}`
          : `(Could not open file: ${path})`,
      });
    }
  }, []);

  const closePreview = useCallback((e?: React.MouseEvent) => {
    e?.stopPropagation();
    e?.preventDefault();
    previewJustClosed.current = true;
    setPreviewFile(null);
    setTimeout(() => {
      previewJustClosed.current = false;
    }, 300);
  }, []);

  const handleShowDiff = useCallback(async (path: string) => {
    setDiffLoading(true);
    try {
      const result = await window.miqi.files.diff(path, currentSessionRef.current);
      setDiffFile({
        path,
        diff: result.diff,
        original_content: result.original_content,
        current_content: result.current_content,
        has_diff: result.has_diff,
        is_new_file: (result as any).is_new_file,
      });
    } catch {
      setDiffFile({
        path,
        diff: null,
        original_content: null,
        current_content: null,
        has_diff: false,
      });
    } finally {
      setDiffLoading(false);
    }
  }, []);

  const closeDiff = () => setDiffFile(null);

  const handleRevert = useCallback(async () => {
    if (!diffFile || reverting) return;
    setReverting(true);
    try {
      const result = await window.miqi.files.revert(diffFile.path, currentSessionRef.current);
      if (result.reverted) {
        // Refresh the diff view
        await handleShowDiff(diffFile.path);
        // Update tracked files list (file is now back to HEAD)
        setTrackedFiles((prev) => prev.filter((f) => f.path !== diffFile.path));
        // Refresh preview if open
        if (previewFile?.path === diffFile.path) {
          const content = await window.miqi.files.read(diffFile.path, currentSessionRef.current);
          setPreviewFile({
            path: diffFile.path,
            content: content.content ?? '当前文件不是文本内容，无法在聊天预览中显示。',
          });
        }
      }
    } catch {
      // Silently fail - revert button is best-effort
    } finally {
      setReverting(false);
    }
  }, [diffFile, reverting, handleShowDiff, previewFile]);

  /** Accept ALL tracked file changes at once — keep files, discard snapshots. */
  const handleMergeAll = useCallback(async () => {
    if (merging) return;
    const toAccept = trackedFiles.filter(
      (f) => f.op === 'write' || f.op === 'edit' || f.op === 'delete'
    );
    if (toAccept.length === 0) return;
    setMerging(true);
    try {
      await Promise.allSettled(
        toAccept.map((f) => window.miqi.files.accept(f.path, currentSessionRef.current))
      );
      // Reset accepted files to 'read' so they stay visible in Referenced Context
      const acceptedPaths = new Set(toAccept.map((f) => f.path));
      setTrackedFiles((prev) =>
        prev.map((f) => (acceptedPaths.has(f.path) ? { ...f, op: 'read' as const } : f))
      );
    } finally {
      setMerging(false);
    }
  }, [merging, trackedFiles]);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    if (!files.length || !fileInputRef.current) return;
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    fileInputRef.current.files = dt.files;
    fileInputRef.current.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const handleCopy = useCallback(async (text: string, idx: number) => {
    // Electron clipboard bridge via main process — navigator.clipboard fails
    // under file:// (non-secure context) in packaged builds; only show
    // feedback when the write actually succeeded (or the IPC call rejects).
    try {
      const res = await window.miqi.clipboard.writeText(text);
      if (!res?.ok) return;
    } catch {
      return;
    }
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  }, []);

  /** Stable no-arg reload trigger for error bubbles (#570). */
  const retryLoad = useCallback(() => setRetryTick((t) => t + 1), []);

  /** #1000：登录引导气泡内一键登录成功后移除该气泡（用户即可重发消息）。 */
  const handleLoginGuidanceDone = useCallback(
    (msg: Message) => {
      if (currentSessionRef.current !== sessionKey) return; // 已切走：保留给该会话
      setMessages((prev) => prev.filter((m) => m.timestamp !== msg.timestamp));
    },
    [sessionKey]
  );

  // Associate each assistant answer with the tool URLs that preceded it in
  // the same turn. Memoized — extractMessageSources scans full tool outputs,
  // which would otherwise re-run on every animation frame while streaming.
  // Tool-only signature: the typewriter advances the LAST assistant message
  // on every animation frame (setMessages per frame), which would rebuild
  // this map and give every bubble a fresh `sources` array — defeating the
  // React.memo on MessageBubble below.  Tool rows update their content while
  // streaming, so their content length IS part of the signature; assistant
  // body length is NOT (extraction never depends on it).
  const sourcesSig = useMemo(
    () =>
      messages
        .map((m) =>
          m.role === 'progress'
            ? `${m.toolCallId ?? ''}:${m.content?.length ?? 0}:${m.webSources?.length ?? 0}`
            : m.role
        )
        .join('|'),
    [messages]
  );
  const sourcesByMsg = useMemo(() => {
    if (sourcesCacheRef.current?.sig === sourcesSig) return sourcesCacheRef.current.map;
    const map = new Map<string, MessageSource[]>();
    let pending: MessageSource[] = [];
    let seen = new Set<string>();
    const merge = (next: MessageSource[]) => {
      // own 已经过上面的 filter 去重（跨工具行），这里直接累积即可；
      // 若再走 seen 去重会与 filter 共享 seen、全部跳过，导致 pending 恒空。
      pending.push(...next);
    };
    for (const m of messages) {
      if (m.role === 'progress') {
        // Tool rows also carry their own references (web_search/web_fetch
        // results) so the chain can show clickable sources inline. Cross-row
        // dedupe: the same RSS link must not repeat on every fetched row.
        const own = extractMessageSources(m).filter((s) => {
          if (seen.has(s.url)) return false;
          seen.add(s.url);
          return true;
        });
        if (own.length > 0) map.set(sourcesKey(m), own);
        merge(own);
      } else if (m.role === 'user') {
        pending = [];
        seen = new Set();
      } else if (m.role === 'assistant') {
        // Attach the turn's accumulated sources to EVERY assistant message —
        // intermediate messages (retry / error explanations) and the final
        // answer all reference the same tool results (#678 用户反馈: 中间
        // "搜索异常改用…" 消息点查看来源竟是空的). Reset happens at the
        // next user message.
        map.set(sourcesKey(m), pending);
      }
    }
    return map;
  }, [sourcesSig]);
  if (sourcesCacheRef.current?.sig !== sourcesSig) {
    sourcesCacheRef.current = { sig: sourcesSig, map: sourcesByMsg };
  }

  // Number tool rows within each user turn so they render as a workflow
  // chain (1, 2, 3…) instead of anonymous stacked blocks.
  const toolStepByMsg = useMemo(() => {
    const map = new Map<Message, number>();
    let step = 0;
    for (const m of messages) {
      if (m.role === 'user') {
        step = 0;
      } else if (m.role === 'progress' && m.toolHint) {
        step += 1;
        map.set(m, step);
      }
    }
    return map;
  }, [messages]);

  // Tool rows grouped into collapsible「工具调用 · N」chains for rendering.
  const chatGroups = useMemo(() => groupChatMessages(messages), [messages]);
  // #1071 review P1（2026-09-16）：会被内联渲染的卡 id 集合——ConfirmCardArea
  // 用它把「已经在消息里画过」的卡从兜底区排除，保证同一张卡只有一个 DOM 实例。
  // 与 cards= 传参共用 inlineCardsForGroup，两边谓词不可能再漂移；
  // 未被内联的卡（无对应 assistant 消息）不在集合里，继续留在兜底区（防卡消失）。
  const inlineCardIds = useMemo(() => {
    const ids = new Set<string>();
    for (const group of chatGroups) {
      if (group.kind === 'chain' || group.kind === 'reply-head') continue;
      for (const c of inlineCardsForGroup(group)) {
        const id = c.request.input_id;
        if (id) ids.add(String(id));
      }
    }
    return ids;
  }, [chatGroups, inlineCardsForGroup]);
  // #843：活跃 assistant = 最后一条 assistant 分组（追加子代理行/重复 assistant 不影响）
  const lastAssistantIdx = useMemo(() => lastAssistantGroupIndex(chatGroups), [chatGroups]);
  // R5 P2：其后已出现 user 分组时不回溯（新回合 assistant 未挂上的窗口内，
  // 上一条已完成的回答不进入 streaming 态）
  const assistantTailActive = useMemo(
    () => lastAssistantIdx >= 0 && !hasUserGroupAfter(chatGroups, lastAssistantIdx),
    [chatGroups, lastAssistantIdx]
  );

  /** Retry a user message: rewind to it, resend automatically with a
   *  "answer differently" hint so the model doesn't repeat itself. */

  const handleRetry = useCallback(
    async (msg: Message) => {
      if (streaming) return;
      cleanupListeners();
      const idx = messagesRef.current.indexOf(msg);
      if (idx >= 0) {
        // #886: a stopped round keeps its interrupted half-reply in the
        // timeline — the retried attempt appends after it instead of
        // rewinding and dropping the "已停止" context.
        setMessages((prev) => (wasTurnStopped(prev, idx) ? prev : prev.slice(0, idx)));
      }
      composerRef.current?.setText(msg.content);
      setAttachments(msg.attachments ?? []);
    },
    [streaming, cleanupListeners]
  );

  const handleRegenerate = useCallback(
    async (assistantMsg: Message) => {
      if (streaming) return;
      const msgs = messagesRef.current;
      const idx = msgs.indexOf(assistantMsg);
      if (idx < 0) return;
      let userIdx = -1;
      for (let i = idx - 1; i >= 0; i--) {
        if (msgs[i].role === 'user') {
          userIdx = i;
          break;
        }
      }
      if (userIdx < 0) return;
      const userMsg = msgs[userIdx];
      retryPayloadRef.current = {
        text: userMsg.content,
        attachments: userMsg.attachments ?? [],
        retry: true,
      };
      // #886: regenerating a manually-stopped turn must not rewind and drop
      // the interrupted round — keep it and let handleSend append the new
      // attempt after it.  Only a completed answer is replaced in place.
      setMessages((prev) => (wasTurnStopped(prev, userIdx) ? prev : prev.slice(0, userIdx)));
      composerRef.current?.setText(userMsg.content);
      setAttachments(userMsg.attachments ?? []);
      requestAnimationFrame(() => handleSendRef.current());
    },
    [streaming]
  );

  /* 编辑用户消息并重新回答(#828 学 Hermes edit → rewind → resubmit):
     截断到该消息之前,用编辑后的文本重新发送 —— 复用 regenerate 机制。
     原子化(外部审查 P1):发送前预检 pending guard;handleSend 若在同步段
     被拒(editSendOutcomeRef='rejected'),回滚截断恢复原消息列表。 */
  const handleEdit = useCallback(
    async (original: Message, newText: string) => {
      if (streaming) return;
      // 预检:同 session 有 pending 发送时不得截断(截断后必然被 handleSend 拒绝)
      if (pendingSendIdsRef.current.has(currentSessionRef.current)) return;
      const text = newText;
      // 仅用 trim 判空,不改变实际 payload(保留用户刻意换行/空格)
      if (!text.trim()) return;
      const msgs = messagesRef.current;
      const idx = msgs.indexOf(original);
      if (idx < 0) return;
      const snapshot = msgs;
      retryPayloadRef.current = {
        text,
        attachments: original.attachments ?? [],
        // 编辑是"修改后重新提问",不是重试 — 不带"换角度重新回答"提示词
        retry: false,
      };
      setMessages((prev) => prev.slice(0, idx));
      // 记录回滚点:异步预派发失败时恢复(见 handleSend 的 provider/网关检查)
      editPendingRollbackRef.current = { snapshot, sessionKey: currentSessionRef.current };
      // 同步原子调用:handleSend 经 retryPayload 读文本,不依赖 setInput 渲染
      // flush —— 不排 RAF(窗口不可见时 RAF 可能不触发,导致"截断但不发送")。
      editSendOutcomeRef.current = null;
      handleSendRef.current();
      // handleSend 的同步段此时已执行完:被拒 → 回滚,避免"截断成功、重发失败"
      if (editSendOutcomeRef.current === 'rejected') {
        retryPayloadRef.current = null;
        setMessages(snapshot);
        editPendingRollbackRef.current = null;
      }
    },
    [streaming]
  );

  /* session display name — persisted custom title wins, else first user
     message, else timestamp fallback */
  const sessionTitle = useMemo(() => {
    if (customTitle) return customTitle;
    const firstUserMsg = messages.find((m) => m.role === 'user');
    if (firstUserMsg) {
      return firstUserMsg.content.trim().slice(0, 60);
    }
    // Fallback: format timestamp from session key
    const raw = sessionKey.replace(/^desktop:/, '');
    const ts = parseInt(raw, 10);
    if (!isNaN(ts) && raw.length >= 13) {
      return new Intl.DateTimeFormat('zh-CN', {
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(ts));
    }
    return raw.replace(/_/g, ' ') || '新任务';
  }, [customTitle, messages, sessionKey]);

  /* ── session title inline rename (from sidebar rename or header edit) ── */
  const lastRenameVersion = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (renameVersion === lastRenameVersion.current) return;
    lastRenameVersion.current = renameVersion;
    let cancelled = false;
    (async () => {
      try {
        const detail = await window.miqi.sessions.get(sessionKey);
        if (cancelled) return;
        const metaTitle = (detail as any)?.metadata?.title;
        setCustomTitle(typeof metaTitle === 'string' && metaTitle.trim() ? metaTitle : null);
      } catch {
        if (!cancelled) setCustomTitle(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [renameVersion, sessionKey]);

  const titleInputRef = useRef<HTMLInputElement>(null);
  const titleSubmitLock = useRef(false);
  useEffect(() => {
    if (editingTitle) {
      titleSubmitLock.current = false; // re-arm for the new edit session
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingTitle]);
  const handleTitleConfirm = useCallback(
    async (value: string) => {
      // Enter unmounts the input, which can fire a trailing blur → guard
      // against confirming the same edit twice.
      if (titleSubmitLock.current) return;
      titleSubmitLock.current = true;
      const trimmed = value.trim();
      if (trimmed) {
        try {
          await window.miqi.sessions.rename(sessionKey, trimmed.slice(0, 100));
          setCustomTitle(trimmed.slice(0, 100));
          onRename?.();
        } catch {
          /* ignore */
        }
      }
      setEditingTitle(false);
    },
    [sessionKey, onRename]
  );

  const taskHeaderInfo = useMemo(() => {
    const latestMessageAt = messages.reduce<number | null>((latest, message) => {
      if (!Number.isFinite(message.timestamp)) return latest;
      return latest === null || message.timestamp > latest ? message.timestamp : latest;
    }, null);
    const updatedAt = latestMessageAt ?? sessionUpdatedAt;
    return {
      updatedLabel: relativeTimeLabel(updatedAt, clockTick),
      fileLabel: `${trackedFiles.length} 个文件`,
      pluginLabel: `${activePluginCount} 个启用插件`,
      meta: buildTaskHeaderMeta(updatedAt, trackedFiles.length, activePluginCount, clockTick),
    };
  }, [activePluginCount, clockTick, messages, sessionUpdatedAt, trackedFiles.length]);

  // issue #607: 任务资产按 结果/过程 分类展示（纯前端启发式，见 taskAssetClassification.ts）
  const { results: resultFiles, process: processFiles } = useMemo(
    () => classifyTrackedFiles(trackedFiles),
    [trackedFiles]
  );
  // #1104：过程文件里的批量目录（如 bvse_sites/ 20 个 cif）折成目录行；
  // 祖先判定用全量追踪路径（结果是交付根顶层产物时也要能撑起折叠）
  const allTrackedPaths = useMemo(() => trackedFiles.map((f) => f.path), [trackedFiles]);
  const { loose: processLoose, groups: processGroups } = useMemo(
    () => groupTrackedByDir(processFiles, allTrackedPaths),
    [processFiles, allTrackedPaths]
  );
  // 「修改建议」区只关心本次会话 write/edit 过的文件；按分类拆成两组
  // （用户反馈 2026-08-13：合并前结果/过程混排，合并（op→read）后才分类）。
  const writeEditFiles = useMemo(
    () => trackedFiles.filter((f) => f.op === 'write' || f.op === 'edit'),
    [trackedFiles]
  );
  const resultWriteEdit = useMemo(
    () => writeEditFiles.filter((f) => resultFiles.some((r) => r.path === f.path)),
    [writeEditFiles, resultFiles]
  );
  const processWriteEdit = useMemo(
    () => writeEditFiles.filter((f) => !resultFiles.some((r) => r.path === f.path)),
    [writeEditFiles, resultFiles]
  );
  // #1104：修改建议里的批量目录同样折行（祖先判定同样用全量路径）
  const { loose: processWriteEditLoose, groups: processWriteEditGroups } = useMemo(
    () => groupTrackedByDir(processWriteEdit, allTrackedPaths),
    [processWriteEdit, allTrackedPaths]
  );
  // 分享/导出默认只包含结果文件；无结果文件时回退为全部文件
  const shareFiles = resultFiles.length > 0 ? resultFiles : trackedFiles;

  const getTaskShareSummary = useCallback(
    () =>
      buildTaskShareText({
        title: sessionTitle,
        meta: taskHeaderInfo.meta,
        messages,
        // issue #607: 默认只包含结果文件；无结果文件时回退为全部文件
        files: shareFiles,
      }),
    [messages, sessionTitle, taskHeaderInfo.meta, shareFiles]
  );

  const handleCopyTaskSummary = useCallback(async () => {
    await navigator.clipboard.writeText(getTaskShareSummary());
    showShareFeedback('copied');
  }, [getTaskShareSummary, showShareFeedback]);

  const handleExportTaskMarkdown = useCallback(() => {
    const text = getTaskShareSummary();
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = getTaskShareDownloadName(sessionTitle);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showShareFeedback('exported');
  }, [getTaskShareSummary, sessionTitle, showShareFeedback]);

  const handleCopyReproContext = useCallback(async () => {
    const text = buildTaskShareText({
      title: sessionTitle,
      meta: taskHeaderInfo.meta,
      messages,
      files: shareFiles,
    });
    const context = buildTaskReproContext({
      sessionKey,
      title: sessionTitle,
      meta: taskHeaderInfo.meta,
      messages,
      files: shareFiles,
    });
    await navigator.clipboard.writeText(context || text);
    showShareFeedback('context');
  }, [messages, sessionKey, sessionTitle, showShareFeedback, taskHeaderInfo.meta, shareFiles]);

  /** issue #607: 复制摘要（含全部文件）— 显式包含过程文件的 opt-in 入口 */
  const handleCopyTaskSummaryAll = useCallback(async () => {
    const text = buildTaskShareText({
      title: sessionTitle,
      meta: taskHeaderInfo.meta,
      messages,
      files: trackedFiles,
    });
    await navigator.clipboard.writeText(text);
    showShareFeedback('copied');
  }, [messages, sessionTitle, taskHeaderInfo.meta, trackedFiles, showShareFeedback]);

  const shareMenuItems = useMemo<ContextMenuAction[]>(
    () => [
      { label: '复制摘要', shortcut: '推荐', onSelect: handleCopyTaskSummary },
      { label: '导出 Markdown', onSelect: handleExportTaskMarkdown },
      { label: '复制摘要（含全部文件）', onSelect: handleCopyTaskSummaryAll },
      {
        label: '复制上下文',
        shortcut: `${messages.filter((message) => message.role === 'user' || message.role === 'assistant').length} 条`,
        divider: true,
        onSelect: handleCopyReproContext,
      },
    ],
    [
      handleCopyReproContext,
      handleCopyTaskSummary,
      handleCopyTaskSummaryAll,
      handleExportTaskMarkdown,
      messages,
    ]
  );

  const shareButtonLabel =
    shareStatus === 'copied'
      ? '已复制摘要'
      : shareStatus === 'exported'
        ? '已导出'
        : shareStatus === 'context'
          ? '已复制上下文'
          : '分享任务';

  const shareButtonTone = shareStatus === 'idle' ? 'var(--text-muted)' : 'var(--success)';

  return (
    <div
      className="flex flex-col h-full"
      style={previewFile ? { pointerEvents: 'none' } : undefined}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept="image/*,text/*,.md,.markdown,.mdown,.txt,.text,.py,.ts,.js,.json,.csv,.yaml,.yml,.toml,.xml,.env,.log,.sql,.ini,.htaccess,.sh,.bash,.rtf,.pdf,.docx,.pptx,.xlsx,.doc,.ppt,.xls,.odt,.odp,.ods,.html,.htm"
        className="hidden"
        onChange={handleFileChange}
      />

      {/* ── Thread tabs ── */}
      {threads.length > 1 && (
        <div className="flex gap-1 px-2 pt-1 overflow-x-auto border-b border-[var(--border)] shrink-0">
          {threads.map((t) => (
            <button
              key={t.threadId}
              onClick={() => setActiveThreadId(t.threadId)}
              className={cn(
                'px-3 py-1.5 text-xs rounded-t whitespace-nowrap transition-colors',
                activeThreadId === t.threadId
                  ? 'bg-[var(--surface)] text-[var(--text)] border-t border-x border-[var(--border)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--text)] hover:bg-[var(--surface-hover)]'
              )}
            >
              {t.label}
              {t.threadId !== 'main' && (
                <button
                  className="ml-1.5 text-[var(--text-muted)] hover:text-[var(--danger)]"
                  onClick={(e) => {
                    e.stopPropagation();
                    setThreads((prev) => prev.filter((th) => th.threadId !== t.threadId));
                    if (activeThreadId === t.threadId) setActiveThreadId('main');
                  }}
                >
                  ×
                </button>
              )}
            </button>
          ))}
        </div>
      )}

      {/* ── Top header bar: Logo | Search | Badges | User ── */}
      <div
        className="flex items-center gap-3 px-5 h-10 border-b shrink-0"
        style={{
          background: 'var(--surface-elevated)',
          borderColor: 'var(--border-subtle)',
        }}
      >
        {/* Left: Logo */}
        <span
          className="text-sm font-bold whitespace-nowrap shrink-0 text-text"
          data-testid="app-title"
        >
          MiQroForge Desktop
        </span>

        {/* Center: Search */}
        <div
          className="flex-1 max-w-[400px] mx-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs"
          style={{
            background: 'var(--surface-muted)',
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-faint)',
          }}
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          >
            <circle cx="11" cy="11" r="8" />
            <path d="m21 21-4.3-4.3" />
          </svg>
          <span className="select-none">搜索或输入命令...</span>
        </div>

        {/* Right: Badges + user + actions */}
        <div className="flex items-center gap-2 shrink-0">
          {/* User avatar + name */}
          <div className="flex items-center gap-1.5 pl-2 ml-1 border-l border-border-subtle">
            <div
              className="w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold text-white shrink-0"
              style={{ background: 'var(--avatar-dark)' }}
            >
              A
            </div>
            <span className="text-xs whitespace-nowrap text-text-muted">Admin</span>
          </div>

          {/* More menu */}
          <ContextMenu
            items={[
              {
                label: '分享对话',
                onSelect: () => {
                  const text = buildTaskShareText({
                    title: sessionTitle || sessionKey,
                    meta: sessionKey,
                    messages,
                    files: trackedFiles,
                  });
                  navigator.clipboard.writeText(text);
                  showShareFeedback('copied');
                },
              },
              {
                label: '导出对话',
                onSelect: () => {
                  const text = buildTaskShareText({
                    title: sessionTitle || sessionKey,
                    meta: sessionKey,
                    messages,
                    files: trackedFiles,
                  });
                  const link = document.createElement('a');
                  link.download = getTaskShareDownloadName(sessionTitle || sessionKey);
                  link.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
                  link.click();
                  URL.revokeObjectURL(link.href);
                  showShareFeedback('exported');
                },
              },
              {
                label: '归档',
                divider: true,
                onSelect: async () => {
                  try {
                    await window.miqi.sessions.archive(sessionKey);
                    createSession(null);
                  } catch {
                    /* ignore */
                  }
                },
              },
              {
                label: '删除对话',
                danger: true,
                onSelect: async () => {
                  if (!window.confirm('删除此对话？操作不可恢复。')) return;
                  try {
                    await window.miqi.sessions.delete(sessionKey);
                    createSession(null);
                  } catch (e) {
                    console.error('删除失败:', e);
                  }
                },
              },
            ]}
          >
            {({ onContextMenu }) => (
              <Tooltip content="更多对话操作">
                <button
                  className="p-1.5 rounded hover:bg-[var(--surface-muted)] transition-colors"
                  onClick={onContextMenu}
                  aria-label="更多对话操作"
                  title="更多对话操作"
                >
                  <MoreHorizontal size={14} style={{ color: 'var(--text-faint)' }} />
                </button>
              </Tooltip>
            )}
          </ContextMenu>
        </div>
      </div>

      {/* ── Main area: chat + right panel ── */}
      <div className="flex flex-1 overflow-hidden">
        {/* Chat area */}
        <div ref={setChatColRef} className="flex flex-col flex-1 overflow-hidden">
          {/* ── Sub header: task title + status (inside chat area) ── */}
          <div
            className="flex items-center gap-3 px-5 min-h-12 border-b shrink-0"
            style={{
              background: 'var(--surface)',
              borderColor: 'var(--border-subtle)',
            }}
          >
            <div className="min-w-0 flex-1 flex items-center gap-2.5">
              {editingTitle ? (
                <input
                  ref={titleInputRef}
                  defaultValue={sessionTitle}
                  maxLength={100}
                  className="text-[16px] font-semibold leading-[1.35] text-text bg-transparent border border-[var(--accent)] rounded px-1.5 py-0.5 min-w-0 focus:outline-none"
                  data-testid="title-inline-input"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleTitleConfirm((e.target as HTMLInputElement).value);
                    if (e.key === 'Escape') {
                      // Cancel: guard so the trailing blur from unmounting the
                      // input doesn't commit the abandoned edit.
                      titleSubmitLock.current = true;
                      setEditingTitle(false);
                    }
                  }}
                  onBlur={(e) => handleTitleConfirm(e.target.value)}
                />
              ) : (
                <h2
                  role="button"
                  tabIndex={0}
                  className="text-[16px] font-semibold truncate leading-[1.35] text-text cursor-pointer hover:text-[var(--accent)] transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded"
                  data-testid="chat-title"
                  title="\u70b9\u51fb\u91cd\u547d\u540d"
                  onClick={() => setEditingTitle(true)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setEditingTitle(true);
                    }
                  }}
                >
                  {sessionTitle}
                </h2>
              )}
              {/* \u300c\u8fdb\u884c\u4e2d\u300d\u53ea\u5728\u56de\u5408\u771f\u7684\u5728\u8dd1\u65f6\u51fa\u73b0\u2014\u2014\u4e4b\u524d\u662f\u786c\u7f16\u7801\u5e38\u663e\uff0c\u4f1a\u8bdd\u7a7a\u95f2
                  \u4e5f\u6302\u7740\u72b6\u6001\u6807\u7b7e\uff08E2E \u7684 waitForResponseComplete \u4e00\u76f4\u6309\u300c\u56de\u5408\u7ed3\u675f
                  \u540e\u5e94\u9690\u85cf\u300d\u5199\u7684\uff0c\u53ea\u662f\u88ab try/catch \u541e\u4e86\uff09\u3002 */}
              {streaming && <span className="tag-inprogress shrink-0">{'\u8fdb\u884c\u4e2d'}</span>}
              {/* \u66f4\u65b0\u65f6\u95f4\uff1a\u653e\u5728\u5de5\u4f5c\u76ee\u5f55\u80f6\u56ca\u524d\u9762\u3001\u968f\u4f1a\u8bdd\u8eab\u4efd\u5c55\u793a\uff08\u53f3\u4fa7\u53ea\u7559\u7ed9\u64cd\u4f5c\uff09\u3002
                  \u53ea\u9732\u65f6\u95f4\uff0c\u5b8c\u6574 \u6587\u4ef6/\u63d2\u4ef6 \u7edf\u8ba1\u6536\u8fdb tooltip\u3002 */}
              {/* \u66f4\u65b0\u65f6\u95f4 / \u6587\u4ef6\u6570\uff1a\u5e38\u9a7b\u5143\u4fe1\u606f\u3002\u538b\u7f29\u6001**\u6574\u6761\u9690\u85cf**\uff08\u5b8c\u6574\u5185\u5bb9\u4ecd\u5728
                  title / aria-label \u91cc\uff09\u2014\u2014\u5b83\u5b9e\u6d4b\u5360 110px\uff0c\u800c\u804a\u5929\u5217\u7a84\u5230\u8fd9\u4e2a\u6863\u4f4d\u65f6
                  \u6807\u9898\u53ea\u5269 82px\uff0c\u7b49\u4e8e\u8ba9\u4f4e\u4ef7\u503c\u7684\u8f85\u52a9\u4fe1\u606f\u6324\u6389\u4e3b\u4fe1\u606f\u3002\u8fd9\u4e5f\u987a\u5e26\u53bb\u6389\u4e86
                  \u539f\u6765\u7684 `hidden md:` \u2014\u2014 \u90a3\u662f\u89c6\u53e3\u65ad\u70b9\uff0c\u800c\u7a97\u53e3\u6709 minWidth\uff0c\u5b83\u6c38\u4e0d\u89e6\u53d1\u3002 */}
              {messages.length > 0 && !subHeaderCompact && (
                <span
                  className="inline-flex shrink-0 items-center gap-1 text-[11px] leading-none whitespace-nowrap"
                  aria-label={taskHeaderInfo.meta}
                  title={taskHeaderInfo.meta}
                  data-testid="chat-header-updated-at"
                  style={{ color: 'var(--text-faint)' }}
                >
                  <span aria-hidden className="opacity-50">
                    {'\u00b7'}
                  </span>
                  {taskHeaderInfo.updatedLabel}
                  <span aria-hidden className="opacity-50">
                    {'\u00b7'}
                  </span>
                  <span className="shrink-0">{taskHeaderInfo.fileLabel}</span>
                </span>
              )}
            </div>
            {/* \u5bf9\u8bdd\u6001\u5de5\u4f5c\u76ee\u5f55\u80f6\u56ca\uff08B \u65b9\u6848\uff09\uff1a\u4f1a\u8bdd\u5df2\u4ea7\u751f\u6d88\u606f\u540e\u5728\u5b50\u6807\u9898\u680f\u5c55\u793a\u5f53\u524d\u76ee\u5f55\uff0c
                  \u7a7a\u6001\u4e0d\u6e32\u67d3\uff08\u6b22\u8fce\u9875\u80f6\u56ca\u72ec\u7acb\u5728\u8f93\u5165\u6846\u4e0a\u65b9\uff09\u3002\u70b9\u51fb\u6362\u76ee\u5f55 \u2192 \u73b0\u6709 picker\uff0c
                  \u9009\u62e9\u5373\u5efa\u7ed1\u5230\u65b0\u76ee\u5f55\u7684\u4f1a\u8bdd\u3002 */}
            {/* 对话态工作目录胶囊（#989 A 方案「安静工具条」）：会话已产生消息后在子标题栏
                展示当前目录，空态不渲染（欢迎页胶囊独立在输入框上方）。点击换目录 → 现有
                picker，选择即建绑到新目录的会话。 */}
            {messages.length > 0 && (
              <button
                type="button"
                onClick={(e) => {
                  if (!streaming) void handleOpenWorkspacePicker(e.currentTarget);
                }}
                disabled={streaming}
                title={workspace ? `工作目录：${workspace}` : '默认工作目录'}
                aria-label="工作目录"
                data-testid="chat-header-workspace-capsule"
                className={cn(
                  subHeaderCompact ? HDR_CTL_CONTEXT_ICON : `${HDR_CTL_CONTEXT} min-w-0`
                )}
                style={{
                  // 压缩态只剩一个文件夹图标：已选目录用正文色、默认目录用更浅的 faint，
                  // 两种状态仍能分辨（不再靠橙色描边 + 橙点这一套）。
                  color: subHeaderCompact
                    ? workspace
                      ? 'var(--text)'
                      : 'var(--text-faint)'
                    : workspace
                      ? 'var(--text-muted)'
                      : 'var(--text-faint)',
                }}
              >
                <Folder size={12} className="shrink-0" />
                {!subHeaderCompact && (
                  <span className="truncate max-w-[170px]" data-testid="chat-header-workspace-path">
                    {workspace ?? '默认工作目录'}
                  </span>
                )}
                {!subHeaderCompact && <ChevronDown size={12} className="shrink-0 opacity-50" />}
              </button>
            )}
            {/* 分享：宽版是「图标 + 文字 + 折叠箭头」，压缩态只剩一个图标——右键仍可打开
                分享菜单，不会因为收起而丢功能。 */}
            <ContextMenu items={shareMenuItems} minWidth={180}>
              {({ onContextMenu }) => (
                <Tooltip content={shareButtonLabel}>
                  <button
                    onClick={handleCopyTaskSummary}
                    onContextMenu={subHeaderCompact ? onContextMenu : undefined}
                    className={subHeaderCompact ? HDR_CTL_ICON : HDR_CTL_LABEL}
                    style={{
                      color: shareButtonTone,
                      cursor: 'pointer',
                    }}
                    title={shareButtonLabel}
                    aria-label={shareButtonLabel}
                  >
                    {shareStatus === 'idle' ? <Send size={12} /> : <Check size={12} />}
                    {!subHeaderCompact && (
                      <span className="whitespace-nowrap">{shareButtonLabel}</span>
                    )}
                  </button>
                </Tooltip>
              )}
            </ContextMenu>
            {!subHeaderCompact && (
              <ContextMenu items={shareMenuItems} minWidth={180}>
                {({ onContextMenu }) => (
                  <Tooltip content="复制摘要、导出 Markdown 或复制上下文">
                    <button
                      onClick={onContextMenu}
                      className={HDR_CTL_CARET}
                      style={{
                        color: shareStatus === 'idle' ? 'var(--text-faint)' : 'var(--success)',
                      }}
                      title="更多分享方式"
                      aria-label="更多分享方式"
                      aria-haspopup="menu"
                    >
                      <ChevronDown size={11} />
                    </button>
                  </Tooltip>
                )}
              </ContextMenu>
            )}
            <Tooltip content="显示或隐藏文件面板">
              <button
                onClick={() => {
                  if (panelOpen) {
                    // 关闭：面板先撤、聊天列立刻拿回宽度，窗口随后收回。这个顺序
                    // 只会让聊天空出一瞬；反过来先收窗会把面板压在聊天列上多撑一拍。
                    setPanelOpen(false);
                    panelSync.request(0);
                    return;
                  }
                  // 打开：先只发窗口加宽请求，面板由 onRequestSettled 在窗口真的
                  // 让出宽度之后再显示 —— 聊天列全程不变，没有那一瞬的挤压。
                  pendingPanelReveal.current = true;
                  panelSync.request(panelWidth);
                }}
                className={cn(panelOpen ? HDR_CTL_TOGGLE_ON : HDR_CTL_TOGGLE, 'ml-1')}
                title="显示或隐藏文件面板"
                aria-label="显示或隐藏文件面板"
                data-testid="toggle-assets-panel-btn"
              >
                <LayoutGrid size={13} />
              </button>
            </Tooltip>
          </div>
          {/* Messages */}
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto"
            style={{ background: 'var(--background)' }}
          >
            <div
              className={`max-w-[760px] mx-auto px-4 pt-5 flex flex-col gap-3 ${
                historyLoaded && messages.length === 0 ? 'min-h-full' : ''
              }`}
              style={{ paddingBottom: '20vh' }}
            >
              {/* Only show the "connecting" spinner while loading AND no messages
                  yet.  A user can send before the session's load() finishes
                  (historyLoaded false), and the optimistic bubble is already in
                  `messages` — with the old `!historyLoaded` gate it was hidden
                  behind the spinner until load() resolved (#872). */}
              {!historyLoaded && messages.length === 0 ? (
                <div className="flex flex-col items-center justify-center min-h-[300px] gap-2.5">
                  <Loader2 size={16} className="animate-spin text-text-faint" />
                  <p className="text-xs text-text-faint">正在连接…</p>
                </div>
              ) : messages.length === 0 ? (
                <div className="relative flex flex-1 flex-col items-center justify-center text-center min-h-[400px] gap-5">
                  {/* EB-1 光晕衬底 */}
                  <div
                    className="pointer-events-none absolute top-0 left-1/2 -translate-x-1/2 w-[680px] h-[360px]"
                    style={{
                      background:
                        'radial-gradient(closest-side, var(--accent-soft), transparent 72%)',
                    }}
                  />
                  {/* 品牌区维持原来的两层竖排（56px 方块 + 30px 标题）。此前为压缩空态高度
                      改成过一行 lockup，按产品意见改回——代价是空态内容高约 +86px。 */}
                  <div
                    className="relative w-14 h-14 rounded-2xl flex items-center justify-center shadow-sm"
                    style={{
                      background: 'var(--surface)',
                      border: '1px solid var(--border-subtle)',
                    }}
                  >
                    <MiQroForgeLogo size={34} />
                  </div>
                  <div className="relative flex flex-col items-center gap-2">
                    <p
                      className="text-[30px] font-extrabold tracking-[-0.02em] leading-tight"
                      style={{ color: 'var(--text)' }}
                    >
                      让 <span style={{ color: 'var(--accent)' }}>MiQroForge</span> 帮你干活
                    </p>
                    <p className="text-[13px] text-text-muted">先选一种做事方式，再告诉我任务</p>
                  </div>
                  {/* #1000 首屏登录入口：未登录时欢迎区直接展示登录卡片，不再藏在设置页深处 */}
                  {!loggedIn && <QraftLoginCard onGoToQraft={onOpenQraftSettings} />}
                  <div className="relative flex gap-[10px] w-full max-w-[560px]">
                    {[
                      {
                        key: 'fast' as const,
                        icon: '⚡',
                        tag: '极速问答',
                        tagline: '面向快速解答',
                        desc: '即时回答问题、改少量代码，低延迟优先。',
                      },
                      {
                        key: 'daily' as const,
                        icon: '📋',
                        tag: '日常任务',
                        tagline: '面向日常办公',
                        desc: '文档、表格、邮件、幻灯片等日常事务，说清需求就交付。',
                      },
                      {
                        key: 'code' as const,
                        icon: '💻',
                        tag: '代码任务',
                        tagline: '面向工程交付',
                        desc: '实现 / 重构 / 测试全流程，产出可审阅变更。',
                      },
                    ].map((m) => {
                      const active = welcomeMode === m.key;
                      return (
                        <button
                          key={m.key}
                          type="button"
                          onClick={() => selectWelcomeMode(m.key)}
                          className={`flex-1 flex flex-col items-center gap-[5px] rounded-xl px-3 py-3 cursor-pointer transition-colors duration-200 border min-h-[108px] ${
                            active ? 'border-[var(--accent)]' : 'border-[var(--border-subtle)]'
                          } hover:border-[var(--accent)]`}
                          style={{
                            // 选中卡：底色自上而下由浅入深（底部更浓），配合描边与淡投影
                            // 让"已选中"在余光里也能看出来。
                            background: active
                              ? 'linear-gradient(to bottom, color-mix(in srgb, var(--surface) 84%, var(--accent-soft)), color-mix(in srgb, var(--surface) 60%, var(--accent-soft)))'
                              : 'var(--surface)',
                            boxShadow: active
                              ? '0 2px 10px color-mix(in srgb, var(--accent) 16%, transparent)'
                              : undefined,
                          }}
                        >
                          <span
                            className="inline-flex items-center gap-[6px] text-[13px] font-bold"
                            style={{ color: 'var(--text)' }}
                          >
                            <span className="text-[15px]">{m.icon}</span>
                            {m.tag}
                          </span>
                          {/* ✓ 仅 active 渲染:opacity 隐藏会让文本留在 DOM,
                              toContainText 断言不了"取消选中"。并进 tagline 这一行而不是
                              单独占一行（未选中的卡原本也要为它预留 18px 占位高度，
                              实测卡片 132 → 108px）；选中态另靠更重的底纹 + 描边 + 淡投影。 */}
                          <span className="flex items-center gap-1.5 text-[11px] text-text-faint">
                            <span>{m.tagline}</span>
                            {active && (
                              <span className="font-bold" style={{ color: 'var(--accent)' }}>
                                ✓ 已选择
                              </span>
                            )}
                          </span>
                          <span className="text-[11.5px] text-text-muted leading-snug">
                            {m.desc}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  {/* issue #962 起点任务：两级子选项——左栏场景（L2，上下滚动），右栏
                      该场景下的任务（L3，每条带一句详情）。点任务填入输入框并聚焦。 */}
                  {/* 起点任务（对齐 WorkBuddy）：子选项从 148px 左栏压成一行横向 chips，
                      贴在输入框上方；任务列表与「场景方案」移到它上面。原来的双栏面板
                      固定 268~320px，是空态首屏溢出的最大来源。 */}
                  {/* issue #962 起点任务（三层渐进，按产品描述）：
                      ① 点三大项 → 子项目横排一行，右侧箭头翻页；
                      ② 点子项目 → 它变成输入框里的可移除胶囊，同时冒出它的子子项目行；
                      ③ 点子子项目 → 详细内容显示在输入框里。
                      原来的 268px 双栏面板被这两行 chips 取代——它正是空态溢出的最大来源。 */}
                  <div
                    data-testid="welcome-starters"
                    aria-label="起点任务"
                    className="relative w-full max-w-[560px] flex items-center gap-1.5"
                  >
                    <button
                      type="button"
                      aria-label="上一组子项目"
                      disabled={!chipScroll.left}
                      onClick={() =>
                        sceneChipsRef.current?.scrollBy({ left: -170, behavior: 'smooth' })
                      }
                      className="starter-chip shrink-0 w-7 h-7 flex items-center justify-center rounded-full cursor-pointer text-text-faint hover:text-[var(--text)] disabled:opacity-35 disabled:cursor-default disabled:pointer-events-none"
                      style={{ border: '1px solid transparent' }}
                    >
                      <ChevronLeft size={13} />
                    </button>
                    <div
                      key={welcomeMode}
                      ref={sceneChipsRef}
                      // 藏掉横向滚动条（两侧箭头就是它的替代物）：webkit 伪元素 + 标准属性各写一份。
                      // py-3/-my-3 是给阴影留的余地：overflow-x 一旦不是 visible，overflow-y 会被
                      // 算成 auto，chips 的 ring 与投影上下都会被这个滚动容器直接裁掉（#962 反馈
                      // 「上下框子看不见」）；拿负 margin 把多出来的 24px 抵回去，布局高度不变。
                      className="flex-1 min-w-0 flex items-center gap-1.5 overflow-x-auto scroll-smooth py-3 -my-3 [&::-webkit-scrollbar]:hidden"
                      onScroll={syncChipScroll}
                      style={{
                        animation: 'welcome-chips-in 220ms ease-out',
                        scrollbarWidth: 'none',
                      }}
                    >
                      {welcomeScenes.map((s) => {
                        const on = s.title === pickedSceneTitle;
                        return (
                          <button
                            key={s.title}
                            type="button"
                            onClick={() => setPickedSceneTitle(on ? null : s.title)}
                            aria-pressed={on}
                            className={cn(
                              'starter-chip flex items-center gap-2 shrink-0 rounded-full px-4 py-2 text-[13px] cursor-pointer',
                              on ? 'font-semibold' : 'text-text-muted hover:text-[var(--text)]'
                            )}
                            style={{
                              // 底色与描边都交给 .starter-chip（表面色 + ring + 投影），
                              // 这里只留 1px 透明边占住原来 border 的布局宽度，选中时不跳尺寸。
                              border: '1px solid transparent',
                              color: on ? 'var(--text)' : undefined,
                            }}
                          >
                            <span
                              className="shrink-0 text-[12px] leading-none"
                              style={on ? undefined : { opacity: 0.55, filter: 'saturate(0.45)' }}
                            >
                              {s.icon}
                            </span>
                            {s.title}
                          </button>
                        );
                      })}
                    </div>
                    <button
                      type="button"
                      aria-label="下一组子项目"
                      disabled={!chipScroll.right}
                      onClick={() =>
                        sceneChipsRef.current?.scrollBy({ left: 170, behavior: 'smooth' })
                      }
                      className="starter-chip shrink-0 w-7 h-7 flex items-center justify-center rounded-full cursor-pointer text-text-faint hover:text-[var(--text)] disabled:opacity-35 disabled:cursor-default disabled:pointer-events-none"
                      style={{ border: '1px solid transparent' }}
                    >
                      <ChevronRight size={13} />
                    </button>
                  </div>

                  {/* 子子项目：换成**可展开的卡片**（#962 反馈：只靠大小区分不够）。
                      层级感来自"控件类型变了"——L2 是横排 chips，L3 是纵列的带 ⌄ 卡片，
                      对齐 WorkBuddy 第三层的做法；展开后直接看到提示词与任务详情。 */}
                  {pickedScene && (
                    <div
                      key={`${welcomeMode}-${pickedSceneTitle}`}
                      className="relative w-full max-w-[560px] flex flex-col gap-1.5 pl-4"
                      style={{
                        animation: 'welcome-chips-in 220ms ease-out',
                        // 这条竖线只是把 L3 归到选中的 L2 名下，跟卡片一样走中性灰（#962：所有都灰）
                        borderLeft: '2px solid color-mix(in srgb, var(--text) 14%, transparent)',
                      }}
                    >
                      {pickedScene.tasks.map((t) => {
                        const open = t.title === pickedTaskTitle;
                        return (
                          <div
                            key={t.title}
                            data-open={open ? '' : undefined}
                            className="starter-card rounded-lg overflow-hidden text-left"
                            style={{ border: '1px solid transparent' }}
                          >
                            <button
                              type="button"
                              onClick={() => {
                                if (open) {
                                  setPickedTaskTitle(null);
                                  return;
                                }
                                setPickedTaskTitle(t.title);
                                // 展开的同时把提示词放进输入框（#962 反馈：详细的提示信息
                                // 得在对话框里）。用替换而不是追加，连点不会堆成一长串。
                                // #1021/#1042 之后 input state 住在 Composer 里，只能走 ref 写。
                                appliedTaskAskRef.current = t.ask;
                                composerRef.current?.setText(t.ask);
                                composerRef.current?.focus();
                              }}
                              aria-expanded={open}
                              className="w-full flex items-center gap-2 px-3 py-2 text-left text-[12.5px] leading-snug cursor-pointer transition-colors duration-150"
                              style={{
                                color: open ? 'var(--text)' : 'var(--text-muted)',
                                fontWeight: open ? 600 : 400,
                              }}
                            >
                              <span className="shrink-0 text-[12px] leading-none">{t.icon}</span>
                              <span className="flex-1 truncate">{t.title}</span>
                              <span className="shrink-0" style={{ color: 'var(--text-faint)' }}>
                                {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                              </span>
                            </button>
                            {open && (
                              <div
                                className="px-3 pb-3 flex flex-col gap-2.5"
                                style={{ borderTop: '1px solid var(--border-subtle)' }}
                              >
                                {(
                                  [
                                    ['适用场景', t.scenario],
                                    ['你会得到', t.deliverable],
                                    ['需要你提供', t.needs],
                                  ] as const
                                ).map(([label, value]) => (
                                  <div key={label} className="flex flex-col gap-0.5">
                                    <span className="text-[10.5px] font-semibold tracking-wide text-text-faint">
                                      {label}
                                    </span>
                                    <span className="text-[12px] leading-[1.7] text-text-muted">
                                      {value}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              ) : (
                chatGroups.map((group, i) =>
                  group.kind === 'chain' ? (
                    <ToolChainGroup
                      key={`chain-${group.rows[0]?.timestamp ?? i}-${i}`}
                      rows={group.rows}
                      done={group.done}
                      sessionKey={sessionKey}
                      reasoningMode={reasoningMode}
                      sourcesByMsg={sourcesByMsg}
                      searchResultsByCallId={searchResultsByCallId}
                      execOutputs={execOutputs}
                      inlineExecOutput={inlineExecOutput}
                      onCopy={handleCopy}
                      copyIdx={i}
                      isCopied={copiedIdx === i}
                      onRetry={undefined}
                      onRegenerate={undefined}
                      onOpenProviderSettings={onOpenProviderSettings}
                      onDownloadPaper={handleDownloadPaper}
                      downloadingPaperId={downloadingPaperId}
                      paperDownloadStates={paperDownloadStates}
                    />
                  ) : group.kind === 'reply-head' ? (
                    <div key={`head-${group.thinking.timestamp}-${i}`}>
                      {shouldRenderThinkingGroup(reasoningMode) && (
                        <ThinkingBlockGroup
                          thinking={group.thinking}
                          fallbackMode={reasoningMode}
                        />
                      )}
                    </div>
                  ) : (
                    <div key={`${group.msg.timestamp}-${i}`}>
                      <MessageBubble
                        msg={group.msg}
                        hideHeader={group.kind === 'reply-content'}
                        sessionKey={sessionKey}
                        turnIndex={i}
                        onEdit={handleEdit}
                        execOutputs={execOutputs}
                        inlineExecOutput={inlineExecOutput}
                        sources={sourcesByMsg.get(sourcesKey(group.msg)) ?? EMPTY_SOURCES}
                        toolStepIndex={toolStepByMsg.get(group.msg)}
                        isLast={i === chatGroups.length - 1}
                        streaming={streaming && i === lastAssistantIdx && assistantTailActive}
                        onResume={group.msg.interrupted ? handleResumeTurn : undefined}
                        onRestart={group.msg.interrupted ? handleRestartTurn : undefined}
                        reasoningMode={reasoningMode}
                        searchResults={
                          group.msg.toolCallId
                            ? searchResultsByCallId[group.msg.toolCallId]
                            : undefined
                        }
                        onCopy={handleCopy}
                        copyIdx={i}
                        isCopied={copiedIdx === i}
                        onRetry={handleRetry}
                        onRetryLoad={retryLoad}
                        onRegenerate={handleRegenerate}
                        onOpenProviderSettings={onOpenProviderSettings}
                        onLoginSuccess={handleLoginGuidanceDone}
                        onDownloadPaper={handleDownloadPaper}
                        downloadingPaperId={downloadingPaperId}
                        paperDownloadStates={paperDownloadStates}
                        sending={sendingFor(sessionKey)}
                        // #1071 review P1（2026-09-16）：内联哪几张卡统一走
                        // inlineCardsForGroup——兜底区（ConfirmCardArea）的
                        // inlineCardIds 由同一个 helper 推导，保证同源。
                        cards={inlineCardsForGroup(group)}
                      />
                    </div>
                  )
                )
              )}

              {/* 用户明确：#646 确认卡属于「回答界面」——timelines/resolved 跟随消息流；
                  pending 确认卡在输入框位置（variant=bottom，Composer 区） */}
              {/* 兜底渲染：只画**没有被消息内联**的卡——已被内联的场次（含
                  plan/action 卡）经 inlineCardIds 排除，同一张卡不会有两份
                  DOM；turn 进行中尚未挂到 assistant 消息上的卡继续在这里可见，
                  AI 消息生成后自动移入消息内部（防「卡消失」回归，#1071）。 */}
              <ConfirmCardArea matchedTurnIds={matchedTurnIds} inlineCardIds={inlineCardIds} />
            </div>
          </div>

          {/* 渐变晕染分界线：固定在输入框上方，消息滚到附近时柔和淡出到背景 */}
          <div
            className="pointer-events-none shrink-0 -mt-10 h-10"
            style={{
              background:
                'linear-gradient(to bottom, color-mix(in srgb, var(--background) 0%, transparent) 0%, color-mix(in srgb, var(--background) 0%, transparent) 40%, var(--background) 100%)',
            }}
          />

          {/* Composer——确认卡弹出时隐藏（WorkBuddy 式：界面只有一个卡片） */}
          <div
            className="shrink-0 px-5 pb-4 pt-3"
            style={{
              background: 'var(--background)',
            }}
          >
            {Object.keys(pendingCards).length > 0 && (
              <div
                className="max-w-[760px] mx-auto text-center text-[11px] py-3"
                style={{ color: '#a0a6b0' }}
              >
                等待你的确认…
              </div>
            )}
            <div
              className="max-w-[760px] min-w-[min(360px,100%)] mx-auto"
              style={Object.keys(pendingCards).length > 0 ? { display: 'none' } : undefined}
            >
              {attachments.length > 0 &&
                (() => {
                  // 附件预览渲染到输入框「内部」:portal 投到 Composer 的框内插槽,
                  // 插槽尚未挂载时先原地渲染一帧兜底。
                  const preview = (
                    <div className="flex flex-wrap gap-1.5 mb-1.5 max-h-[104px] overflow-y-auto">
                      {attachments.map((att, i) => {
                        const isDoc = att.type === 'document';
                        const cat = isDoc ? getDocCategory(att.name) : null;
                        const isPending = isDoc && (!att.status || att.status === 'pending');
                        const isParsing = isDoc && att.status === 'parsing';
                        const isDone = isDoc && att.status === 'done';
                        const isError = isDoc && att.status === 'error';
                        // 格式标签（WorkBuddy 风：只显示名字 + 格式，不显示大小）
                        const extTag = (att.name.split('.').pop() || '').toUpperCase().slice(0, 4);

                        return (
                          <div
                            key={i}
                            className="flex items-center gap-1.5 rounded-md pl-1.5 pr-1 py-1 text-[11px] group max-w-[196px]"
                            style={{
                              background: isDoc && cat ? cat.bg : 'var(--surface-muted)',
                              border: `1px solid ${isDoc && cat ? cat.color + '40' : 'var(--border-subtle)'}`,
                            }}
                          >
                            <button
                              type="button"
                              aria-label={`预览 ${att.name}`}
                              className="flex items-center gap-1.5 min-w-0 flex-1 cursor-pointer bg-transparent border-0 p-0 text-left hover:brightness-95 transition-all"
                              onClick={async (e) => {
                                // Ignore clicks that arrive right after closing preview
                                // (the close button click can fall through to the chip behind)
                                if (previewJustClosed.current) return;
                                // 图片：点击打开预览（芯片内不再显示缩略图，保证所有文件芯片等高）
                                if (att.type === 'image') {
                                  const imgExt = (att.name.split('.').pop() || '').toLowerCase();
                                  const mime =
                                    imgExt === 'jpg' || imgExt === 'jpeg'
                                      ? 'image/jpeg'
                                      : imgExt === 'gif'
                                        ? 'image/gif'
                                        : imgExt === 'webp'
                                          ? 'image/webp'
                                          : imgExt === 'bmp'
                                            ? 'image/bmp'
                                            : 'image/png';
                                  const imageUrl =
                                    att.dataUrl ||
                                    (att.dataBase64
                                      ? `data:${mime};base64,${att.dataBase64}`
                                      : undefined);
                                  // 同时带上 base64：预览里的「下载/另存为」「系统应用打开」
                                  // 需要它写临时文件再交给系统打开（仅有 imageUrl 时
                                  // openExternal 只会拿到文件名而失败）。
                                  const imageBase64 =
                                    att.dataBase64 ||
                                    (att.dataUrl ? att.dataUrl.split(',')[1] : undefined);
                                  if (imageUrl) {
                                    setPreviewFile({
                                      path: att.name,
                                      kind: 'image',
                                      imageUrl,
                                      dataBase64: imageBase64,
                                    });
                                    return;
                                  }
                                }
                                if (!isDoc || !att.dataBase64) return;
                                const ext = att.name.split('.').pop()?.toLowerCase() ?? '';

                                // #877: PDF → proper paginated rendering (iframe blob)
                                if (ext === 'pdf') {
                                  try {
                                    setPreviewFile({
                                      path: att.name,
                                      kind: 'pdf',
                                      pdfUrl: base64ToBlobUrl(att.dataBase64, 'application/pdf'),
                                      dataBase64: att.dataBase64,
                                    });
                                    return;
                                  } catch {
                                    /* fall through to client-side text */
                                  }
                                }

                                // #877: Office/CSV → backend structured parse of the
                                // in-memory bytes (rich table / document render).
                                if (/^(xlsx|xls|ods|csv|docx|doc|odt)$/i.test(ext)) {
                                  try {
                                    const result = await window.miqi.documents.parse(
                                      att.name,
                                      undefined,
                                      {
                                        preview: true,
                                        structured: true,
                                        dataBase64: att.dataBase64,
                                      }
                                    );
                                    if (result?.structured) {
                                      if (result.structured.kind === 'spreadsheet') {
                                        setPreviewFile({
                                          path: att.name,
                                          kind: 'spreadsheet',
                                          spreadsheet: result.structured,
                                          content: result.text,
                                          dataBase64: att.dataBase64,
                                        });
                                        return;
                                      }
                                      setPreviewFile({
                                        path: att.name,
                                        kind: 'document',
                                        docBlocks: result.structured,
                                        content: result.text,
                                        dataBase64: att.dataBase64,
                                      });
                                      return;
                                    }
                                    // No structure (e.g. .xls/.odt) — use the backend text
                                    if (result?.text) {
                                      setPreviewFile({
                                        path: att.name,
                                        content: result.text.slice(0, 50000),
                                        dataBase64: att.dataBase64,
                                      });
                                      return;
                                    }
                                  } catch {
                                    /* fall through to client-side text */
                                  }
                                }

                                let previewText = '';

                                // Client-side extraction only (fast, no server round-trip)
                                try {
                                  const raw = Uint8Array.from(atob(att.dataBase64), (c) =>
                                    c.charCodeAt(0)
                                  );
                                  if (ext === 'pdf') {
                                    previewText = extractPdfText(raw.buffer);
                                  } else if (
                                    /^(md|markdown|mdown|txt|text|csv|json|ya?ml|xml|py|ts|js|log|html|htm|env|sql|ini|toml|htaccess|sh|bash)$/i.test(
                                      ext
                                    )
                                  ) {
                                    previewText = new TextDecoder().decode(raw);
                                  } else {
                                    previewText = '(Office 文件 —— 发送后服务端解析)';
                                  }
                                } catch {
                                  previewText = '(无法预览)';
                                }
                                if (!previewText || !previewText.trim()) {
                                  previewText = '(扫描件或二进制文件，无文本内容)';
                                }
                                setPreviewFile({
                                  path: att.name,
                                  content: previewText.slice(0, 50000),
                                  dataBase64: att.dataBase64,
                                });
                              }}
                            >
                              {/* File type badge */}
                              {isDoc && cat ? (
                                <span
                                  className="shrink-0 rounded font-bold text-[9px] px-1 py-[1px] leading-none"
                                  style={{ background: cat.color, color: '#fff' }}
                                >
                                  {cat.label}
                                </span>
                              ) : att.type === 'image' ? (
                                <Image
                                  size={12}
                                  className="shrink-0"
                                  style={{ color: 'var(--info)' }}
                                />
                              ) : (
                                <FileText size={12} className="shrink-0 text-text-faint" />
                              )}

                              {/* Name + format（不显示大小，参考 WorkBuddy） */}
                              <div className="flex items-center gap-1.5 min-w-0 leading-tight">
                                <span className="truncate font-medium text-text">
                                  {att.name.length > 22
                                    ? att.name.slice(0, 18) + '…' + att.name.slice(-3)
                                    : att.name}
                                </span>
                                {!isDoc && extTag && (
                                  <span
                                    className="shrink-0 rounded font-bold text-[9px] px-1 py-[1px] leading-none"
                                    style={{
                                      background: 'var(--surface-3)',
                                      color: 'var(--text-muted)',
                                    }}
                                  >
                                    {extTag}
                                  </span>
                                )}
                              </div>

                              {/* Status icon — only after send */}
                              {isDoc && isParsing && (
                                <Loader2
                                  size={11}
                                  className="shrink-0 animate-spin"
                                  style={{ color: cat?.color ?? 'var(--text-faint)' }}
                                />
                              )}
                              {isDoc && isDone && (
                                <CheckCircle
                                  size={11}
                                  className="shrink-0"
                                  style={{ color: 'var(--success)' }}
                                />
                              )}
                              {isDoc && isError && (
                                <AlertCircle
                                  size={11}
                                  className="shrink-0"
                                  style={{ color: 'var(--danger)' }}
                                />
                              )}
                            </button>

                            {/* Remove */}
                            <button
                              type="button"
                              aria-label={`移除 ${att.name}`}
                              onClick={(e) => {
                                // The chip container opens the preview on click —
                                // without stopPropagation the remove click bubbles
                                // up and pops the preview modal for the just-removed
                                // file (and the modal then eats further input, e.g.
                                // the attachment.spec cleanup loop in CI).
                                e.stopPropagation();
                                removeAttachment(i);
                              }}
                              className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-[rgba(0,0,0,0.1)] rounded p-0.5"
                            >
                              <X size={10} style={{ color: 'var(--text-faint)' }} />
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  );
                  return attachmentSlot ? createPortal(preview, attachmentSlot) : preview;
                })()}

              {/* Turn status (issue #646: 等待你的确认) */}
              <TurnStatusBar />

              {/* #646-v2：兜底 ConfirmCardArea 渲染在消息区尾部（见上方
                  matchedTurnIds 处，带 turn 过滤）——此处不再重复渲染 */}

              {/* 欢迎态工作目录胶囊：独立于输入框、在它正上方（同宽左对齐，不嵌进卡内）。
                  首条消息后隐藏——会话进行中改由子标题栏胶囊承接。与子标题栏用同一套
                  ghost 规格（28px / 圆角 7 / 11px），只是这里常驻一层浅底色——空态下它
                  是这一屏唯一的目录入口，全透明会看不见。整体是一个按钮：原先「外层
                  div role=button 里再嵌一个 button」是嵌套交互元素，读屏会念成两个控件。 */}
              {historyLoaded && messages.length === 0 && (
                <div className="flex items-center pb-2.5" data-testid="inline-workspace-selector">
                  <button
                    type="button"
                    aria-label="工作目录"
                    title={workspace ? `工作目录：${workspace}` : '默认工作目录'}
                    onClick={(e) => {
                      if (!streaming) void handleOpenWorkspacePicker(e.currentTarget);
                    }}
                    disabled={streaming}
                    data-testid="inline-workspace-change-btn"
                    className={cn(HDR_CTL_CONTEXT, 'min-w-0')}
                    style={{
                      color: workspace ? 'var(--text-muted)' : 'var(--text-faint)',
                    }}
                  >
                    <Folder size={12} className="shrink-0" />
                    <span
                      className="truncate max-w-[220px]"
                      title={workspace ?? undefined}
                      data-testid="inline-workspace-path"
                    >
                      {workspace ?? '默认工作目录'}
                    </span>
                    <ChevronDown size={12} className="shrink-0 opacity-50" />
                  </button>
                </div>
              )}
              <Composer
                ref={composerRef}
                streaming={streaming}
                hasAttachments={attachments.length > 0}
                adjustHint={adjustHint}
                executionPolicy={executionPolicy}
                onExecutionPolicyChange={setExecutionPolicy}
                onOpenApprovals={onOpenApprovals}
                reasoningMode={reasoningMode}
                onReasoningModeChange={changeReasoningMode}
                complexHint={complexHint}
                onComplexHintDismiss={handleComplexHintDismiss}
                onAttachClick={handleAttachClick}
                onSubmit={handleComposerSubmit}
                onAbort={handleAbort}
                // #962 起点任务胶囊：只在空态显示；过条件而不是传 isWelcomeEmpty，
                // 是因为 Composer 只需要「画不画这颗胶囊」，不需要知道消息数量。
                starterScene={isWelcomeEmpty ? pickedScene : null}
                starterTask={isWelcomeEmpty ? pickedTask : null}
                onClearStarterScene={clearStarterScene}
                onClearStarterTask={clearStarterTask}
                onPasteClipboard={pasteClipboardFiles}
                attachmentSlotRef={setAttachmentSlot}
              />
            </div>
          </div>
        </div>

        {/* ── Plan Sidebar ── */}
        {planOpen && plan && (
          <div className="w-72 border-l border-[var(--border)] bg-[var(--surface)] flex flex-col shrink-0">
            <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
              <span className="text-sm font-semibold truncate">{plan.title}</span>
              <button
                onClick={() => setPlanOpen(false)}
                className="text-[var(--text-muted)] hover:text-[var(--text)]"
              >
                <X size={14} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {plan.steps.map((step) => (
                <div key={step.id} className="flex items-start gap-2 text-xs py-1">
                  <span
                    className={cn(
                      'mt-0.5 w-4 h-4 rounded-full flex items-center justify-center text-[10px] shrink-0',
                      step.status === 'completed' && 'bg-green-500 text-white',
                      step.status === 'in_progress' && 'bg-blue-500 text-white animate-pulse',
                      step.status === 'pending' && 'bg-gray-300 text-gray-600',
                      step.status === 'skipped' && 'bg-gray-200 text-gray-400'
                    )}
                  >
                    {step.status === 'completed' ? '✓' : step.status === 'in_progress' ? '●' : '○'}
                  </span>
                  <span
                    className={cn(
                      step.status === 'skipped' && 'line-through text-[var(--text-muted)]',
                      step.status === 'in_progress' && 'font-medium'
                    )}
                  >
                    {step.description}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Right panel: Task Assets ── */}
        {panelOpen && (
          <div
            data-testid="task-assets-panel"
            ref={assetsPanelRef}
            className="flex flex-col shrink border-l overflow-y-auto relative"
            style={{
              width: panelWidth,
              minWidth: ASSET_PANEL_MIN_WIDTH,
              background: 'var(--panel-bg)',
              borderColor: 'var(--panel-border)',
            }}
          >
            {/* Resize handle — left edge */}
            <div
              onMouseDown={handlePanelResizeStart}
              className="absolute top-0 left-0 w-1.5 h-full cursor-col-resize hover:bg-[var(--accent)]/30 transition-colors z-10"
              style={{ marginLeft: -2 }}
            />
            <div
              className="flex items-center justify-between px-4 py-3 border-b shrink-0"
              style={{ borderColor: 'var(--panel-border)' }}
            >
              <div className="flex items-center gap-1.5 text-text-muted">
                <LayoutGrid size={13} />
                <span className="text-xs font-semibold text-text" data-testid="task-assets-title">
                  任务资产
                </span>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className="text-xs font-medium text-text-faint"
                  data-testid="task-assets-stats"
                >
                  {resultFiles.length} 个结果 / {processFiles.length} 个过程
                </span>
              </div>
            </div>

            {trackedFiles.length === 0 ? (
              <div className="flex flex-col items-center justify-center flex-1 px-4 py-8 text-center gap-4">
                <FileText size={28} style={{ color: 'var(--text-faint)', opacity: 0.35 }} />
                <div className="flex flex-col items-center gap-1">
                  <p
                    className="text-[13px] font-medium text-text-muted"
                    data-testid="task-assets-empty"
                  >
                    暂无文件
                  </p>
                  <p className="text-[11px] text-text-faint">Agent 操作会显示在这里</p>
                </div>
              </div>
            ) : (
              <>
                {/* issue #607: 结果资产（默认展开、星标强调） + 过程资产（默认折叠） */}
                {/* #1104: 稳定 key——结果区随 resultFiles 出现/消失时不得让过程区
                    按位置重挂载（否则用户手动展开的状态被重置回默认折叠） */}
                {resultFiles.length > 0 && (
                  <AssetSection
                    key="asset-section-result"
                    label="结果文件"
                    testKey="result"
                    count={resultFiles.length}
                    defaultOpen
                    accent
                  >
                    {resultFiles.map((f) => (
                      <TrackedFileCard
                        key={f.path}
                        file={f}
                        isResult
                        citations={turnSourcesMap.get(f.turnId ?? -1) ?? []}
                        onPreview={() => handlePreview(f.path)}
                        onDiff={() => handleShowDiff(f.path)}
                        onReveal={async () => {
                          // #1062：过去对工作区外文件这里会 reject 被丢弃 → 点了没反应；
                          // 现在统一收结构化结果，失败时给出可见提示。
                          // #1131：`sessions.workspace` 对**非文件夹绑定**的默认工作区
                          // 会话返回 null，主进程于是把相对路径锚到全局工作区根；而会话
                          // 隔离的产物实际在 `sessions/<key>/files/` 下，台账里存的又是
                          // 裸文件名（create_pdf 等文档工具相对会话 files 根记账）→ 一律
                          // File not found。与预览/下载保持一致，补一个会话相对候选。
                          // 路径由本会话 key 推出，主进程的包含性校验不变，渲染层没被放宽。
                          const raw = normalizePath(f.path);
                          const nameOnly = raw.replace(/\\/g, '/').split('/').pop()!;
                          const safeKey = sessionFilesDirKey(currentSessionRef.current);
                          const candidates = [raw];
                          if (safeKey && nameOnly === raw) {
                            candidates.push(`sessions/${safeKey}/files/${nameOnly}`);
                          }
                          let lastError = '';
                          for (const candidate of candidates) {
                            try {
                              const res = await window.miqi.files.openContainingFolder(
                                candidate,
                                // #1062: 带上会话 key，主进程才能把文件夹绑定会话的
                                // 工作区算进允许根；传的是会话而非根，渲染层无法放宽校验。
                                currentSessionRef.current
                              );
                              if (res?.revealed) return;
                              lastError = String(res?.error ?? '');
                            } catch (e: any) {
                              lastError = String(e?.message ?? e);
                            }
                          }
                          const outside = /outside workspace/i.test(lastError);
                          notifyAssetError(
                            outside
                              ? '无法定位：该文件在会话工作区之外'
                              : `定位失败：${lastError || '未知原因'}`
                          );
                        }}
                      />
                    ))}
                  </AssetSection>
                )}

                {processFiles.length > 0 && (
                  <AssetSection
                    key="asset-section-process"
                    label="过程文件"
                    testKey="process"
                    count={processFiles.length}
                    defaultOpen={resultFiles.length === 0}
                  >
                    {/* #1104：目录级聚合——bvse_sites/ 这类批量目录折成一行，
                        不再逐个铺开几十张卡片（用户反馈 2026-09-16） */}
                    {processLoose.map((f) => (
                      <TrackedFileCard
                        key={f.path}
                        file={f}
                        citations={turnSourcesMap.get(f.turnId ?? -1) ?? []}
                        onPreview={() => handlePreview(f.path)}
                        onDiff={() => handleShowDiff(f.path)}
                      />
                    ))}
                    {processGroups.map((g) => (
                      <AssetDirGroupRow
                        key={g.dir}
                        dir={g.dir}
                        files={g.files}
                        renderFile={(f) => (
                          <TrackedFileCard
                            key={f.path}
                            file={f}
                            onPreview={() => handlePreview(f.path)}
                            onDiff={() => handleShowDiff(f.path)}
                          />
                        )}
                      />
                    ))}
                  </AssetSection>
                )}
              </>
            )}

            {/* Proposed changes summary — grouped by 结果/过程 (#607 user feedback:
                合并前这里混排 write/edit，合并（op→read）后才分类) */}
            <div className="flex-1" />
            {writeEditFiles.length > 0 && (
              <div
                data-testid="task-assets-changes"
                className="border-t mx-3 mt-2 pt-3 pb-3"
                style={{ borderColor: 'var(--panel-border)' }}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-1.5">
                    <span
                      className="w-1.5 h-1.5 rounded-full"
                      style={{ background: 'var(--warning)' }}
                    />
                    <span className="text-xs font-semibold text-text">修改建议</span>
                  </div>
                  <span className="text-[10px] text-text-faint">
                    {writeEditFiles.length} 个文件
                  </span>
                </div>
                {resultWriteEdit.length > 0 && (
                  <div className="mb-2">
                    <div className="text-[10px] font-medium text-text-faint mb-1">
                      结果文件 ({resultWriteEdit.length})
                    </div>
                    <div className="flex flex-col gap-1.5">
                      {resultWriteEdit.map((f) => (
                        <div
                          key={f.path}
                          className="flex items-center gap-1.5 rounded-lg px-2.5 py-2"
                          style={{
                            background: 'var(--surface-muted)',
                            border: '1px solid var(--border-subtle)',
                          }}
                        >
                          <FileText
                            size={11}
                            style={{ color: 'var(--info)' }}
                            className="shrink-0"
                          />
                          <span className="text-[11px] truncate flex-1 text-text" title={f.path}>
                            {f.name}
                          </span>
                          <span
                            className="text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0"
                            style={{
                              background:
                                f.op === 'write' ? 'var(--accent)' : 'rgba(234,179,8,0.15)',
                              color: f.op === 'write' ? 'var(--accent-text)' : 'var(--warning)',
                            }}
                          >
                            {f.op.toUpperCase()}
                          </span>
                          <button
                            onClick={() => handleShowDiff(f.path)}
                            className="p-1 rounded transition-colors shrink-0 text-text-faint"
                            title="Compare diff"
                          >
                            <GitCompare size={11} />
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {processWriteEdit.length > 0 && (
                  <div>
                    <div className="text-[10px] font-medium text-text-faint mb-1">
                      过程文件 ({processWriteEdit.length})
                    </div>
                    <div className="flex flex-col gap-1.5">
                      {processWriteEditLoose.map((f) => (
                        <div
                          key={f.path}
                          className="flex items-center gap-1.5 rounded-lg px-2.5 py-2"
                          style={{
                            background: 'var(--surface-muted)',
                            border: '1px solid var(--border-subtle)',
                          }}
                        >
                          <FileText
                            size={11}
                            style={{ color: 'var(--info)' }}
                            className="shrink-0"
                          />
                          <span className="text-[11px] truncate flex-1 text-text" title={f.path}>
                            {f.name}
                          </span>
                          <span
                            className="text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0"
                            style={{
                              background:
                                f.op === 'write' ? 'var(--accent)' : 'rgba(234,179,8,0.15)',
                              color: f.op === 'write' ? 'var(--accent-text)' : 'var(--warning)',
                            }}
                          >
                            {f.op.toUpperCase()}
                          </span>
                          <button
                            onClick={() => handleShowDiff(f.path)}
                            className="p-1 rounded transition-colors shrink-0 text-text-faint"
                            title="Compare diff"
                          >
                            <GitCompare size={11} />
                          </button>
                        </div>
                      ))}
                      {/* #1104：批量目录折行，避免修改建议被几十个批次文件淹没 */}
                      {processWriteEditGroups.map((g) => (
                        <AssetDirGroupRow
                          key={g.dir}
                          dir={g.dir}
                          files={g.files}
                          renderFile={(f) => (
                            <div
                              key={f.path}
                              className="flex items-center gap-1.5 rounded-lg px-2.5 py-2"
                              style={{
                                background: 'var(--surface-muted)',
                                border: '1px solid var(--border-subtle)',
                              }}
                            >
                              <FileText
                                size={11}
                                style={{ color: 'var(--info)' }}
                                className="shrink-0"
                              />
                              <span
                                className="text-[11px] truncate flex-1 text-text"
                                title={f.path}
                              >
                                {f.name}
                              </span>
                              {/* 与散列行同款控件：操作徽标 + 直接看差异（CodeRabbit 复审） */}
                              <span
                                className="text-[9px] px-1.5 py-0.5 rounded font-medium shrink-0"
                                style={{
                                  background:
                                    f.op === 'write' ? 'var(--accent)' : 'rgba(234,179,8,0.15)',
                                  color: f.op === 'write' ? 'var(--accent-text)' : 'var(--warning)',
                                }}
                              >
                                {f.op.toUpperCase()}
                              </span>
                              <button
                                onClick={() => handleShowDiff(f.path)}
                                className="p-1 rounded transition-colors shrink-0 text-text-faint"
                                title="Compare diff"
                              >
                                <GitCompare size={11} />
                              </button>
                            </div>
                          )}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Merge all */}
            <div className="px-3 pb-4 shrink-0">
              <button
                onClick={handleMergeAll}
                disabled={merging || trackedFiles.length === 0}
                className={cn(
                  'w-full py-2 rounded-xl text-xs font-semibold flex items-center justify-center gap-2 transition duration-200',
                  merging || trackedFiles.length === 0 ? 'cursor-not-allowed' : 'hover:opacity-90'
                )}
                style={{
                  background:
                    merging || trackedFiles.length === 0 ? 'var(--surface-muted)' : 'var(--accent)',
                  color:
                    merging || trackedFiles.length === 0
                      ? 'var(--text-faint)'
                      : 'var(--accent-text)',
                  opacity: merging || trackedFiles.length === 0 ? 0.5 : 1,
                }}
              >
                {merging ? <Loader2 size={13} className="animate-spin" /> : <GitMerge size={13} />}
                {merging ? '合并中...' : '合并所有更改'}
              </button>
              {trackedFiles.length === 0 && (
                <div className="flex items-center justify-center mt-2 py-1.5">
                  <span className="text-xs text-text-faint">跟踪文件变更后将在此显示合并选项</span>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── File Preview Modal ── */}
      {previewFile && (
        <Modal
          open={!!previewFile}
          onOpenChange={(o) => {
            if (!o) closePreview();
          }}
          hideClose
          className="max-w-[980px] p-0 bg-transparent border-0 shadow-none"
        >
          <div
            data-testid="file-preview-modal"
            className="flex flex-col rounded-xl shadow-2xl overflow-hidden"
            style={{
              width: '100%',
              maxWidth: previewFile.kind ? 940 : 820,
              maxHeight: '85vh',
              background: 'var(--surface-elevated)',
              border: '1px solid var(--border)',
              pointerEvents: 'auto',
            }}
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b shrink-0 border-border-subtle">
              <div className="flex items-center gap-2 min-w-0 flex-1">
                {PDF_FILE_RE.test(previewFile.path) ? (
                  <FileText size={14} style={{ color: 'var(--danger)' }} className="shrink-0" />
                ) : /\.(xlsx|xls|csv|ods)$/i.test(previewFile.path) ? (
                  <FileSpreadsheet
                    size={14}
                    style={{ color: 'var(--success)' }}
                    className="shrink-0"
                  />
                ) : /\.(pptx|ppt|odp)$/i.test(previewFile.path) ? (
                  <FileBarChart size={14} style={{ color: '#f97316' }} className="shrink-0" />
                ) : (
                  <FileType size={14} style={{ color: 'var(--info)' }} className="shrink-0" />
                )}
                <span
                  className="text-[11px] font-mono break-all leading-relaxed text-text-muted"
                  title={previewFile.path}
                >
                  {previewFile.path}
                </span>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {/\.html?$/i.test(previewFile.path) && (
                  <div className="flex items-center gap-1 rounded-md border border-[var(--border-subtle)] overflow-hidden mr-1">
                    <button
                      type="button"
                      onClick={() => setHtmlSourceMode(false)}
                      className={`px-2 py-1 text-[11px] ${!htmlSourceMode ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'}`}
                    >
                      预览
                    </button>
                    <button
                      type="button"
                      onClick={() => setHtmlSourceMode(true)}
                      className={`px-2 py-1 text-[11px] ${htmlSourceMode ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'}`}
                    >
                      源码
                    </button>
                  </div>
                )}
                <button
                  onClick={async () => {
                    // #877: 下载/另存为 — native save dialog via main process
                    let base64 = previewFile.dataBase64;
                    if (!base64) {
                      const nameOnly = previewFile.path.replace(/\\/g, '/').split('/').pop()!;
                      const safeKey = sessionFilesDirKey(currentSessionRef.current);
                      const reads: Array<{ p: string; session?: string }> = [
                        { p: previewFile.path, session: currentSessionRef.current },
                        { p: previewFile.path },
                      ];
                      if (safeKey && nameOnly === previewFile.path) {
                        reads.push({
                          p: `sessions/${safeKey}/files/${nameOnly}`,
                          session: currentSessionRef.current,
                        });
                      }
                      for (const read of reads) {
                        try {
                          const res = await window.miqi.files.read(read.p, read.session, {
                            asBinary: true,
                          });
                          if (res?.data_base64) {
                            base64 = res.data_base64;
                            break;
                          }
                        } catch {
                          /* try next */
                        }
                      }
                    }
                    if (!base64 && previewFile.content) {
                      base64 = bytesToBase64(new TextEncoder().encode(previewFile.content));
                    }
                    if (!base64) return;
                    const name = previewFile.path.split(/[\\/]/).pop() || 'download';
                    await window.miqi.files.saveAs(name, base64);
                  }}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--accent)] hover:bg-[var(--accent-soft)] transition-colors"
                  title="保存到本地"
                >
                  <Download size={12} />
                  <span>下载/另存为</span>
                </button>
                <button
                  onClick={async () => {
                    // 有字节流 → openBytes（主进程写临时文件并交系统打开）；
                    // 被拒（含危险扩展名拦截）时不回退，避免绕过安全校验。
                    if (previewFile.dataBase64) {
                      const name = previewFile.path.split(/[\\/]/).pop() || 'file';
                      try {
                        const res = await window.miqi.files.openBytes(name, previewFile.dataBase64);
                        if (res?.opened) return;
                        // 被拒也要说话：静默 return 正是 #1062 要消灭的那种失败。
                        if (res?.error) {
                          notifyAssetError(`打开失败：${res.error}`);
                          return;
                        }
                      } catch {
                        /* fall through to path */
                      }
                    }
                    try {
                      // #1062：必须带会话 key。不带的话主进程只按全局工作区校验，
                      // 绑定文件夹会话里的合法文件也会被判「工作区之外」——而空
                      // catch 会把这次失败整个吞掉，点了没反应。
                      const res = await window.miqi.files.openExternal(
                        previewFile.path,
                        currentSessionRef.current
                      );
                      if (!res?.opened) {
                        notifyAssetError(`打开失败：${res?.error ?? '未知原因'}`);
                      }
                    } catch (e: any) {
                      notifyAssetError(`打开失败：${e?.message ?? String(e)}`);
                    }
                  }}
                  className="flex items-center gap-1 px-2 py-1 rounded text-[11px] text-[var(--accent)] hover:bg-[var(--accent-soft)] transition-colors"
                  title="用系统默认应用打开"
                >
                  <ExternalLink size={12} />
                  <span>系统应用打开</span>
                </button>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    e.preventDefault();
                    closePreview(e);
                  }}
                  className="p-1 rounded hover:bg-[var(--surface-muted)] transition-colors"
                >
                  <X size={14} style={{ color: 'var(--text-faint)' }} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-auto">
              {previewFile.kind === 'image' && previewFile.imageUrl ? (
                <div
                  className="flex items-center justify-center p-4"
                  style={{
                    background: 'var(--surface-muted)',
                    maxHeight: '75vh',
                    overflow: 'auto',
                  }}
                >
                  <img
                    src={previewFile.imageUrl}
                    alt={previewFile.path}
                    style={{ maxWidth: '100%', maxHeight: '72vh', borderRadius: 8 }}
                  />
                </div>
              ) : previewFile.kind === 'pdf' && previewFile.pdfUrl ? (
                <iframe
                  src={previewFile.pdfUrl}
                  title={previewFile.path}
                  className="w-full border-0"
                  style={{ height: '70vh', background: 'var(--surface)' }}
                />
              ) : previewFile.kind === 'spreadsheet' && previewFile.spreadsheet ? (
                <SpreadsheetPreview sheets={previewFile.spreadsheet.sheets} />
              ) : previewFile.kind === 'document' && previewFile.docBlocks ? (
                <DocxPreview blocks={previewFile.docBlocks.blocks} />
              ) : /\.html?$/i.test(previewFile.path) ? (
                htmlSourceMode ? (
                  <pre className="p-4 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all text-text-muted">
                    {previewFile.content}
                  </pre>
                ) : (
                  <SandboxHtmlFrame
                    html={previewFile.content ?? ''}
                    className="w-full border-0"
                    maxHeight="70vh"
                  />
                )
              ) : previewFile.content && !/^\(Could not open file/.test(previewFile.content) ? (
                <pre className="p-4 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all text-text-muted">
                  {previewFile.content}
                </pre>
              ) : (
                <div className="flex flex-col items-center justify-center gap-2 p-8 text-center">
                  <AlertCircle size={18} style={{ color: 'var(--warning)' }} />
                  <p className="text-xs text-[var(--text-muted)]">
                    {unsupportedPreviewReason(previewFile.path, previewFile.content)}
                  </p>
                  <p className="text-[11px] text-[var(--text-faint)]">
                    请使用上方「下载/另存为」保存到本地，或用「系统应用打开」在外部程序查看
                  </p>
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* ── Diff Modal ── */}
      {diffFile && (
        <Modal
          open={!!diffFile}
          onOpenChange={(o) => {
            if (!o) closeDiff();
          }}
          hideClose
          className="max-w-[920px] p-0"
        >
          <div
            className="flex flex-col rounded-xl shadow-2xl overflow-hidden"
            style={{
              width: 920,
              maxHeight: '85vh',
              background: 'var(--surface-elevated)',
              border: '1px solid var(--border)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b shrink-0 border-border-subtle">
              <div className="flex items-center gap-2 min-w-0">
                <GitCompare size={14} style={{ color: 'var(--warning)' }} className="shrink-0" />
                <span className="text-sm font-medium truncate text-text" title={diffFile.path}>
                  {diffFile.path.split(/[/\\]/).pop()}
                </span>
                {!diffLoading && diffFile.has_diff && (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0"
                    style={{
                      background: 'rgba(234,179,8,0.15)',
                      color: 'var(--warning)',
                    }}
                  >
                    MODIFIED
                  </span>
                )}
                {!diffLoading && diffFile.has_diff && (diffFile as any).is_new_file && (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0"
                    style={{
                      background: 'rgba(16,185,129,0.15)',
                      color: 'var(--success)',
                    }}
                  >
                    NEW FILE
                  </span>
                )}
                {!diffLoading && !diffFile.has_diff && (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0"
                    style={{
                      background: 'var(--surface-muted)',
                      color: 'var(--text-faint)',
                    }}
                  >
                    NO CHANGES
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!diffLoading && diffFile.has_diff && (
                  <button
                    onClick={handleRevert}
                    disabled={reverting}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
                    style={{
                      background: reverting ? 'var(--surface-muted)' : 'rgba(255,97,97,0.15)',
                      color: reverting ? 'var(--text-faint)' : 'var(--danger)',
                      border: '1px solid var(--danger)',
                    }}
                    title="还原到 HEAD（撤销所有改动）"
                  >
                    <Undo2 size={12} className={reverting ? 'animate-spin' : ''} />
                    {reverting ? '正在还原…' : '还原'}
                  </button>
                )}
                <button
                  onClick={closeDiff}
                  className="p-1 rounded hover:bg-[var(--surface-muted)] transition-colors shrink-0"
                >
                  <X size={14} style={{ color: 'var(--text-faint)' }} />
                </button>
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-auto">
              {diffLoading ? (
                <div className="flex items-center justify-center h-48">
                  <Loader2 size={24} className="animate-spin text-text-faint" />
                  <span className="ml-2 text-sm text-text-faint">Loading diff...</span>
                </div>
              ) : diffFile.diff ? (
                <DiffView diff={diffFile.diff} />
              ) : diffFile.original_content !== null && diffFile.current_content !== null ? (
                /* No snapshot diff but we have both versions — show side by side */
                <div className="flex h-full" style={{ minHeight: 400 }}>
                  <div className="flex-1 p-4 overflow-auto border-r border-border-subtle">
                    <div className="text-[10px] font-semibold uppercase tracking-wider mb-2 text-text-faint">
                      Original
                    </div>
                    <pre className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-all text-text-muted">
                      {diffFile.original_content || '(empty)'}
                    </pre>
                  </div>
                  <div className="flex-1 p-4 overflow-auto">
                    <div className="text-[10px] font-semibold uppercase tracking-wider mb-2 text-text-faint">
                      Current
                    </div>
                    <pre className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-all text-text">
                      {diffFile.current_content || '(empty)'}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-center h-48">
                  <span className="text-sm text-text-faint">
                    {diffFile.original_content === null && diffFile.current_content === null
                      ? '无快照可用 — 此文件未在本会话中修改'
                      : '未检测到变更'}
                  </span>
                </div>
              )}
            </div>
          </div>
        </Modal>
      )}

      {/* ── 工作目录下拉（参考 #940 收敛：点胶囊就近弹出小面板，替代居中 Modal） ── */}
      {workspacePickerOpen && (
        <WorkspacePickerMenu
          anchor={workspacePickerAnchor}
          recent={recentWorkspaces}
          current={workspace ?? null}
          onClose={() => setWorkspacePickerOpen(false)}
          onPick={(ws) => createSession(ws)}
          onDefault={() => createSession(null)}
          onBrowse={async () => {
            setWorkspacePickerOpen(false);
            try {
              const dir = await window.miqi.dialog.openDirectory();
              createSession(dir ?? null);
            } catch {
              createSession(null);
            }
          }}
        />
      )}
      {/* #1062：结果/过程文件「定位 / 预览」失败提示（此前静默无反应） */}
      {assetError && (
        <div
          className="fixed inset-0 z-[100] flex items-end justify-center pb-24 pointer-events-none"
          style={{ animation: 'msgIn .25s cubic-bezier(.22,.8,.32,1)' }}
          data-testid="asset-error-toast"
        >
          <div
            className="flex items-center gap-3 rounded-xl px-5 py-3 shadow-lg pointer-events-auto"
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--danger)',
              boxShadow: '0 12px 40px rgba(0,0,0,.15)',
            }}
          >
            <span
              className="w-6 h-6 rounded-full flex items-center justify-center text-sm shrink-0"
              style={{ background: 'var(--danger-bg)', color: 'var(--danger)' }}
            >
              !
            </span>
            <div className="text-[13px] max-w-[420px]" style={{ color: 'var(--text)' }}>
              {assetError}
            </div>
          </div>
        </div>
      )}
      {/* #696 补：下载完成 toast（屏幕居中 + 淡入淡出 + 2s 停留） */}
      {downloadToast && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center pointer-events-none"
          style={{ animation: 'msgIn .25s cubic-bezier(.22,.8,.32,1)' }}
        >
          <div
            className="flex items-center gap-3 rounded-xl px-5 py-3.5 shadow-lg pointer-events-auto"
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--success)',
              boxShadow: '0 12px 40px rgba(0,0,0,.15)',
              opacity: toastVisible ? 1 : 0,
              transition: 'opacity .4s ease',
            }}
          >
            <span
              className="w-7 h-7 rounded-full flex items-center justify-center text-sm shrink-0"
              style={{ background: 'var(--success-bg)', color: 'var(--success-text)' }}
            >
              ✓
            </span>
            <div className="min-w-0">
              <div className="text-[14px] font-semibold" style={{ color: 'var(--text)' }}>
                下载成功
              </div>
              <div
                className="text-[12px] truncate max-w-[260px]"
                style={{ color: 'var(--text-muted)' }}
              >
                {downloadToast.filename}
              </div>
            </div>
            <button
              onClick={() => window.miqi.files?.openContainingFolder(downloadToast.savePath)}
              className="text-[12.5px] font-medium px-3 py-1.5 rounded-lg cursor-pointer shrink-0 transition-all"
              style={{
                background: 'var(--success-bg)',
                color: 'var(--success-text)',
                border: 'none',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.8')}
              onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
            >
              打开文件夹
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Sub-components ──────────────────────────────────────────────── */

/** Renders a unified diff string with syntax-highlighted +/- lines. */

/** issue #607: collapsible asset section — 结果文件 (accent + default open) / 过程文件. */
function AssetSection({
  label,
  testKey,
  count,
  defaultOpen,
  accent,
  children,
}: {
  label: string;
  testKey: 'result' | 'process';
  count: number;
  defaultOpen: boolean;
  accent?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div data-testid={`asset-section-${testKey}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-4 pt-3 pb-1.5 text-[10px] font-semibold uppercase tracking-widest text-text-faint hover:text-text-muted transition-colors"
        data-testid={`asset-section-toggle-${testKey}`}
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
        {accent && (
          <Star
            size={11}
            fill="currentColor"
            className="shrink-0"
            style={{ color: 'var(--accent)' }}
          />
        )}
        <span className="shrink-0">{label}</span>
        <span className="shrink-0 opacity-70">{count}</span>
      </button>
      {open && <div className="px-3 pb-3 flex flex-col gap-2">{children}</div>}
    </div>
  );
}

/** Tool-chain group: while the turn runs the numbered steps stay visible;
 *  once the final answer arrives the whole chain collapses into one
 *  「工具调用 · N」block (click to re-expand). #539 用户要求。 */
function ToolChainGroup({
  rows,
  done,
  sourcesByMsg,
  searchResultsByCallId,
  ...bubbleProps
}: {
  rows: Message[];
  done: boolean;
  sourcesByMsg: Map<string, MessageSource[]>;
  searchResultsByCallId: Record<string, string>;
} & Omit<
  ComponentProps<typeof MessageBubble>,
  'msg' | 'sources' | 'toolStepIndex' | 'isLastToolRow' | 'isLast'
>) {
  const [open, setOpen] = useState(true);
  const autoCollapsedRef = useRef(false);
  // 2026-08-27：确认/计划是工具的一部分（Hermes 式）——审批条在工具行下。
  // 卡按 createdAt 顺序与 ask_user_confirm_card 工具行一一对应（单 turn 主场景）。
  const {
    pending: chainPending,
    resolved: chainResolved,
    resolve: chainResolve,
    timeoutCard: chainTimeout,
  } = useUserInput();
  // 2026-08-27 Hermes 式：含确认卡的工具链不自动收起——审批条消失后行保留
  const hasConfirmRow = rows.some(
    (r) =>
      r.toolName === 'ask_user_confirm_card' || (r.content ?? '').includes('ask_user_confirm_card')
  );
  const chainCards = useMemo(() => {
    // CodeRabbit（9-11）：① 只保留确认卡——plan/action 卡由 MessageBubble 渲染，
    // 混入会让 confirmRowIdx 与 confirm 行错位；② 按本链 turn 过滤——多链同屏
    // 时防止把别条链的卡挂进来（行级 turnId：实时走 activeTurnIdRef，恢复走
    // raw.turn_id）。
    const chainTurnId = rows.find((r) => r.turnId)?.turnId;
    return [...Object.values(chainResolved), ...Object.values(chainPending)]
      .filter((c) => isConfirmCard(c as never))
      .filter((c) => !chainTurnId || c.request.turn_id === chainTurnId)
      .sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0));
  }, [chainPending, chainResolved, rows]);
  // 工具链内 ask_user_confirm_card 行（按顺序）→ 卡队列索引
  const confirmRowIdxRef = useRef(0);
  confirmRowIdxRef.current = 0;
  // Auto-fold once, when the turn completes (a later manual expand is kept).
  useEffect(() => {
    if (done && !autoCollapsedRef.current && !hasConfirmRow) {
      autoCollapsedRef.current = true;
      const t = setTimeout(() => setOpen(false), 1500);
      return () => clearTimeout(t);
    }
  }, [done, hasConfirmRow]);

  const label = `工具调用 · ${rows.length}`;
  return (
    <div className="my-0.5 flex min-w-0 pl-2">
      <div className="flex w-4 flex-col items-center self-stretch">
        <span className="text-[13px] leading-none">🔧</span>
        <span
          className="mt-0.5 w-[2px] flex-1 min-h-2 rounded-full"
          style={{ background: 'var(--border-subtle)' }}
        />
      </div>
      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 py-0.5 text-xs cursor-pointer select-none transition-opacity hover:opacity-75"
          style={{ color: 'var(--info)' }}
          aria-expanded={open}
        >
          <span>{label}</span>
          <ChevronDown
            size={11}
            className="shrink-0 transition-transform opacity-60"
            style={{ transform: open ? 'none' : 'rotate(-90deg)' }}
          />
        </button>
        {open && (
          <div className="mt-0.5 flex flex-col">
            {rows.map((row, i) => {
              const isConfirmRow =
                row.toolName === 'ask_user_confirm_card' ||
                (row.content ?? '').includes('ask_user_confirm_card');
              const card = isConfirmRow ? chainCards[confirmRowIdxRef.current++] : undefined;
              return (
                <div key={`${row.timestamp}-${i}`}>
                  <MessageBubble
                    msg={row}
                    // develop 侧 #879 起按 sourcesKey(row) 取来源（同一 msg 对象在
                    // 不同会话/重放下的 key 不同）；本分支的内联确认卡沿用原结构。
                    sources={sourcesByMsg.get(sourcesKey(row)) ?? EMPTY_SOURCES}
                    toolStepIndex={i + 1}
                    isLastToolRow={i === rows.length - 1}
                    isLast={false}
                    searchResults={
                      row.toolCallId ? searchResultsByCallId[row.toolCallId] : undefined
                    }
                    {...bubbleProps}
                  />
                  {isConfirmRow && card && (
                    <div className="mt-1">
                      <ConfirmCardItem
                        entry={card as never}
                        resolve={chainResolve}
                        timeoutCard={chainTimeout}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

interface MessageBubbleProps {
  msg: Message;
  /** Reasoning mode of the active conversation — fast hides thinking blocks
   *  (issue #680: 极速回答不展示思考过程). */
  reasoningMode?: ReasoningMode;
  /** True when this bubble is the reply-content of a split turn (the
   *  avatar/name/thinking header was already rendered by its reply-head). */
  hideHeader?: boolean;
  /** Current session key — scopes persisted 👍/👎 feedback to this session. */
  sessionKey: string;
  /** Stable per-turn index (chatGroups 下标) — reload-stable feedback key. */
  turnIndex?: number;
  /** Copy-feedback index — chatGroups index; chain rows reuse the group's. */
  copyIdx?: number;
  /** Timestamp of the pending optimistic user bubble (issue #364) — the
   *  spinner shows only on the bubble whose timestamp matches, so a session
   *  switch never shows it on another session's messages. */
  sending?: number | null;
  /** Whether this session currently has a turn in flight (ChatConsole
   *  streaming state, mirrored from streamingBySession). Used to render the
   *  streaming mermaid source preview only on the message being generated. */
  streaming?: boolean;
  execOutputs: Record<string, { stdout: string; stderr: string; running: boolean }>;
  inlineExecOutput: boolean;
  isLast: boolean;
  onCopy: (text: string, idx: number) => void;
  isCopied: boolean;
  onRetry?: (msg: Message) => void;
  /** #570: reload the current session's history (the error bubble's 重试 button). */
  onRetryLoad?: () => void;
  onRegenerate?: (msg: Message) => void;
  onOpenProviderSettings?: () => void;
  /** #1000：错误气泡内一键登录成功后移除该引导气泡。 */
  onLoginSuccess?: (msg: Message) => void;
  onDownloadPaper?: (paper: PaperItem) => void;
  downloadingPaperId?: string | null;
  /** #668 补：论文下载结果反馈（paperId → done/failed） */
  paperDownloadStates?: Record<
    string,
    { status: 'done' | 'failed'; savePath?: string; error?: string }
  >;
  /** Reference URLs collected from the tool calls preceding this answer */
  sources?: MessageSource[];
  /** Workflow step number when this progress row is a tool call. */
  toolStepIndex?: number;
  /** True when this is the last tool row of the turn — hides the ↓ arrow. */
  isLastToolRow?: boolean;
  /** web_search result text for this row (click-to-expand cards). */
  searchResults?: string;
  /** 2026-08-27：本 turn 的确认/计划卡——AI 回答的一部分（内容后、操作栏前） */
  cards?: UserInputCardEntry[];
  /** #740/#1042: resume/restart an interrupted turn (half-generated reply).
  /** 编辑用户消息并重新回答(#828)。 */
  onEdit?: (msg: Message, newText: string) => void;
  /** #740: resume/restart an interrupted turn (half-generated reply).
   *  Takes the message (#1042) so the render site can pass its stable
   *  useCallback reference instead of building an inline lambda — that keeps
   *  the comparator below honest (every prop it compares is stable). */
  onResume?: (msg: Message) => void;
  onRestart?: (msg: Message) => void;
}

const MessageBubble = memo(function MessageBubble({
  msg,
  hideHeader,
  sessionKey,
  execOutputs,
  inlineExecOutput,
  isLast,
  streaming,
  onCopy,
  isCopied,
  onRetry,
  onRetryLoad,
  onRegenerate,
  onOpenProviderSettings,
  onLoginSuccess,
  onDownloadPaper,
  downloadingPaperId,
  paperDownloadStates,
  sources,
  toolStepIndex,
  isLastToolRow,
  searchResults,
  turnIndex,
  copyIdx,
  sending,
  onEdit,
  onResume,
  onRestart,
  reasoningMode,
  cards,
}: MessageBubbleProps) {
  const [expanded, setExpanded] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  // 2026-08-27：卡属于 AI 回答——消息内部渲染（resolve/timeout 直接取自 context）
  const { resolve: resolveCard, timeoutCard } = useUserInput();
  // 编辑态(#828):用户消息原地变输入框,提交后截断重发
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState('');
  // Message action bar (copy/regenerate/feedback/sources) — restored from
  // #547 after #577 dropped the whole bar, leaving only a hover-only copy
  // button (#577 功能回归修复).  Feedback is persisted to localStorage
  // (survives session switches/restarts) and 👎 opens a lightweight report
  // that is actually submitted to the backend feedback channel.
  // 反馈持久化键：优先用会话内轮次序号（chatGroups 下标，重载后顺序稳定），
  // 避免用 msg.timestamp——实时流式是前端合成时间戳（userTs+1），重载后是
  // 后端 ISO 时间戳，两者永不相等，导致切换会话/重启后点赞状态丢失 (#547 恢复 review)。
  // 工具链行（turnIndex 未传）不渲染反馈 UI，键值无所谓，沿用 timestamp 兜底。
  const feedbackKey =
    turnIndex !== undefined ? `${sessionKey}:turn:${turnIndex}` : `${sessionKey}:${msg.timestamp}`;
  const [feedback, setFeedback] = useState<'up' | 'down' | null>(() => {
    try {
      const map = JSON.parse(localStorage.getItem(MSG_FEEDBACK_KEY) || '{}');
      return map[feedbackKey] ?? null;
    } catch {
      return null;
    }
  });
  const [showSources, setShowSources] = useState(false);
  const [showDislike, setShowDislike] = useState(false);
  const [dislikeText, setDislikeText] = useState('');
  const [dislikeSending, setDislikeSending] = useState(false);
  const [dislikeDone, setDislikeDone] = useState(false);
  const [dislikeError, setDislikeError] = useState('');

  // ── Hook-count uniformity ─────────────────────────────────────────────
  // These three hooks must run BEFORE the role-based early returns below
  // (progress / error / subagent).  A fiber reconciled across a role change
  // (e.g. a window-switch restore that swaps a bubble's role at the same
  // key) would otherwise call 9 hooks on the early-return path vs 12 on the
  // main path — React throws "Rendered fewer hooks than expected".
  const bubbleRef = useRef<HTMLDivElement>(null);
  const capturedSelectionRef = useRef('');
  // #880: 消息渲染失败兜底——「显示原文」切换为查看原始 markdown/HTML 文本
  const [showRawOnError, setShowRawOnError] = useState(false);

  const persistFeedback = (v: 'up' | 'down' | null) => {
    try {
      const map = JSON.parse(localStorage.getItem(MSG_FEEDBACK_KEY) || '{}');
      if (v === null) delete map[feedbackKey];
      else map[feedbackKey] = v;
      localStorage.setItem(MSG_FEEDBACK_KEY, JSON.stringify(map));
    } catch {
      /* storage unavailable */
    }
  };

  const submitDislike = async () => {
    setDislikeSending(true);
    setDislikeError('');
    try {
      // Real feedback loop: report to the backend feedback channel (Feishu
      // Bitable via feedback.submit).  Lightweight — no required text.
      await window.miqi.feedback.submit({
        category: 'suggestion',
        content:
          '回答不满意\n' +
          (dislikeText.trim() || '（未填写具体说明）') +
          `\n\n— 消息摘要：${msg.content.slice(0, 200)}`,
        app_version: typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev',
      });
      setDislikeDone(true);
    } catch (e: any) {
      // Persisted feedback stands; surface the send failure so the user
      // knows the report did not reach the team.
      setDislikeError(e?.message || '反馈提交失败，请稍后重试');
    } finally {
      setDislikeSending(false);
    }
  };

  if (msg.interrupted) {
    return (
      <InterruptedTurnCard
        meta={
          msg.interruptedMeta ?? {
            turnId: '',
            status: 'interrupted',
          }
        }
        reasoning={msg.reasoning}
        content={String(msg.content ?? '')}
        elapsedSeconds={msg.reasoningElapsedS}
        mode={msg.reasoningMode}
        onResume={onResume ? () => onResume(msg) : undefined}
        onRestart={onRestart ? () => onRestart(msg) : undefined}
      />
    );
  }

  if (msg.role === 'progress') {
    // Thinking blocks live in the timeline as their own quiet block, both
    // while streaming and after the turn finishes. Issue #539. Fast mode
    // hides them entirely (#680: 极速回答不展示思考过程).
    if (msg.reasoning) {
      return (
        <ThinkBlock
          reasoning={msg.reasoning}
          defaultOpen={msg.isLiveReasoning}
          mode={msg.reasoningMode ?? reasoningMode}
          elapsedSeconds={
            msg.reasoningElapsedS ??
            // Restored/fast turns without a persisted duration: use a fixed
            // minimum instead of deriving age from Date.now() — historical
            // blocks would otherwise show hours/days and grow on re-render
            // (CodeRabbit #662).  Always show a time (audit 跟进: 思考时间
            // 必须写出来).
            1
          }
          live={msg.isLiveReasoning}
        />
      );
    }
    // ── Paper search result: render formatted cards ──────────────
    if (msg.toolName === 'paper_search' && msg.toolData) {
      return (
        <PaperSearchResult
          data={msg.toolData as PaperSearchPayload}
          onDownloadPaper={onDownloadPaper || (() => {})}
          downloadingId={downloadingPaperId || null}
          paperDownloadStates={paperDownloadStates}
          downloadStateKeyPrefix={sessionKey}
        />
      );
    }

    const isCollapsed = msg.collapsed && !expanded;
    const activities = groupToolActivities(parseToolActivity(msg.content));
    // Restored tool results carry raw OUTPUT in content — never parse that
    // into pseudo-activities; the summary already reads "执行命令 · cp …".
    const toolLabel = msg.toolOutput
      ? msg.summary || '工具调用'
      : toolChainLabel(activities, msg.toolArgs, msg.summary);
    const isToolRow = !!msg.toolHint;
    if (isToolRow) {
      const iconName = msg.toolName || activities[0]?.name || '';
      const isSearch = msg.toolName === 'web_search';
      // web_search output renders as clickable result cards. Live rows read
      // the stashed end-event output; restored rows parse the stored content.
      // Both stack under the label row with the left rule running through
      // (用户要求：URL 往下堆叠、竖线贯穿、点击搜索行直接出结果卡片).
      const results =
        isSearch && !isCollapsed ? parseWebSearchResults(searchResults ?? msg.content) : [];
      const canExpandSearch = isSearch && results.length > 0;
      // Full exec command (issue #902): the label shows a 60-char summary, the
      // expanded block shows the untruncated command + a copy button.  Rows
      // with no command args (legacy sessions) still expand to show output.
      const cmdText = msg.toolArgs !== undefined ? toolCommandText(msg.toolArgs) : undefined;
      const canExpandCmd = typeof cmdText === 'string' && cmdText.length > 0;
      const canExpand = canExpandCmd || !!msg.toolOutput;
      return (
        <div className="flex items-start gap-2 py-0.5">
          <div className="flex w-4 flex-col items-center self-stretch">
            <span className="text-[13px] leading-none">{toolIconEmoji(iconName)}</span>
            {toolStepIndex ? (
              <span
                className="mt-0.5 text-[9px] leading-none tabular-nums"
                style={{ color: 'var(--info)' }}
              >
                {String(toolStepIndex).padStart(2, '0')}
              </span>
            ) : null}
            <span
              className="mt-0.5 w-[2px] flex-1 min-h-2 rounded-full"
              style={{ background: 'var(--border-subtle)' }}
            />
            {!isLastToolRow && (
              <ArrowDown
                size={10}
                className="shrink-0"
                style={{ color: 'var(--info)', opacity: 0.55 }}
              />
            )}
          </div>
          <div className="min-w-0 flex-1">
            <button
              type="button"
              onClick={
                canExpandSearch
                  ? () => setSearchOpen((v) => !v)
                  : canExpand
                    ? () => setExpanded((v) => !v)
                    : undefined
              }
              className={cn(
                'block min-w-0 text-left text-[11px] leading-4 break-all transition-opacity',
                (canExpandSearch || canExpand) && 'cursor-pointer select-none hover:opacity-80'
              )}
              style={{ color: 'var(--info)' }}
              aria-expanded={canExpandSearch ? searchOpen : canExpand ? expanded : undefined}
            >
              {toolLabel}
              {(canExpandSearch || canExpand) && (
                <ChevronDown
                  size={11}
                  className="ml-1 inline-block shrink-0 align-middle transition-transform opacity-60"
                  style={{
                    transform: canExpandSearch
                      ? searchOpen
                        ? 'none'
                        : 'rotate(-90deg)'
                      : expanded
                        ? 'none'
                        : 'rotate(-90deg)',
                  }}
                />
              )}
            </button>
            {expanded && cmdText !== undefined && (
              <ToolCommandBlock
                command={cmdText}
                onCopy={(t) => onCopy(t, copyIdx ?? 0)}
                copied={isCopied}
              />
            )}
            {searchOpen && results.length > 0 && (
              <div className="mt-1 flex flex-col gap-1.5">
                {results.map((r) => (
                  <a
                    key={r.url}
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    title={r.url}
                    className="block rounded-lg border p-2 transition-colors hover:border-[var(--info)]"
                    style={{ borderColor: 'var(--border-subtle)' }}
                  >
                    <div
                      className="flex items-center gap-1.5 text-[11px]"
                      style={{ color: 'var(--info)' }}
                    >
                      <img
                        src={`https://${hostOf(r.url)}/favicon.ico`}
                        alt=""
                        loading="lazy"
                        className="h-3 w-3 rounded-[3px]"
                        onError={(e) => {
                          (e.currentTarget as HTMLImageElement).style.display = 'none';
                        }}
                      />
                      <span className="truncate font-medium">{hostOf(r.url)}</span>
                    </div>
                    <div className="mt-0.5 truncate text-xs font-medium">{r.title}</div>
                    {r.snippet && (
                      <div
                        className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        {r.snippet}
                      </div>
                    )}
                  </a>
                ))}
              </div>
            )}
            {sources && sources.length > 0 && (
              <div className="mt-1 flex flex-col gap-1">
                {sources.map((s) => (
                  <a
                    key={s.url}
                    href={s.url}
                    target="_blank"
                    rel="noreferrer"
                    title={s.url}
                    className="flex min-w-0 items-center gap-1.5 text-[11px] leading-4 transition-opacity hover:opacity-80"
                    style={{ color: 'var(--info)' }}
                  >
                    <img
                      src={`https://${hostOf(s.url)}/favicon.ico`}
                      alt=""
                      loading="lazy"
                      className="h-3 w-3 shrink-0 rounded-[3px]"
                      onError={(e) => {
                        (e.currentTarget as HTMLImageElement).style.display = 'none';
                      }}
                    />
                    <span className="shrink-0 font-medium">{hostOf(s.url)}</span>
                    <span className="truncate opacity-70">{s.url.replace(/^https?:\/\//, '')}</span>
                  </a>
                ))}
              </div>
            )}
            {inlineExecOutput && msg.toolCallId && execOutputs[msg.toolCallId] && (
              <div className="mt-1 p-2 bg-black/80 text-green-400 text-[11px] font-mono rounded max-h-48 overflow-y-auto border border-gray-700">
                <pre
                  className="whitespace-pre-wrap"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    borderRadius: 0,
                    padding: 0,
                    margin: 0,
                  }}
                >
                  {execOutputs[msg.toolCallId].stdout}
                  {execOutputs[msg.toolCallId].stderr ? (
                    <span className="text-red-400">{execOutputs[msg.toolCallId].stderr}</span>
                  ) : null}
                </pre>
                {execOutputs[msg.toolCallId].running && (
                  <span className="inline-block w-1.5 h-3 bg-green-400 animate-pulse ml-0.5 align-middle" />
                )}
              </div>
            )}
            {!isCollapsed && msg.toolOutput && results.length === 0 && (
              <div
                className="mt-1 max-h-48 overflow-y-auto rounded border border-gray-700 bg-black/80 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-all"
                style={{ color: '#d1d5db' }}
              >
                <span
                  className="mb-1 block text-[10px] uppercase tracking-wide opacity-70"
                  style={{ color: '#9ca3af' }}
                >
                  输出
                </span>
                {msg.content}
              </div>
            )}
          </div>
        </div>
      );
    }
    return (
      <div className="min-w-0 text-xs">
        <button
          type="button"
          onClick={msg.collapsed ? () => setExpanded((v) => !v) : undefined}
          className={cn(
            'inline-flex max-w-full items-center gap-1 px-1 py-0.5 text-[11px] transition-opacity',
            msg.collapsed && 'cursor-pointer select-none',
            isToolRow ? 'hover:opacity-80' : 'hover:opacity-75'
          )}
          style={isToolRow ? { color: 'var(--info)' } : { color: 'var(--text-muted)' }}
        >
          {isToolRow ? (
            <span className="text-[12px] leading-none">
              {toolIconEmoji(activities[0]?.name ?? '')}
            </span>
          ) : isLast ? (
            <Loader2 size={11} className="shrink-0 animate-spin opacity-70" />
          ) : (
            <CheckCircle size={11} className="shrink-0 opacity-70" />
          )}
          {/* Tool rows show the derived chain label; plain progress rows
              (warnings, billing notices, …) show their full content — the
              chain-label pipeline truncates to 28 chars and would cut off
              the message body (#921). */}
          {isToolRow ? (
            <span className="truncate">{toolLabel}</span>
          ) : (
            <span className="whitespace-pre-wrap break-words">{msg.content}</span>
          )}
          {msg.collapsed &&
            (isCollapsed ? (
              <ChevronRight size={11} className="shrink-0 opacity-60" />
            ) : (
              <ChevronDown size={11} className="shrink-0 opacity-60" />
            ))}
        </button>
        {!isCollapsed && isToolRow && !msg.toolOutput && activities.length > 0 && (
          <div className="mt-0.5 flex flex-col gap-0.5 pl-0.5">
            {activities.map((act, i) => (
              <span key={i} className="text-[11px]" style={{ color: 'var(--info)' }}>
                {toolDisplayName(act.name)}
                {act.duration ? ` · ${act.duration}` : ''}
              </span>
            ))}
          </div>
        )}
        {/* Inline exec output (Phase 7.4) — gated by ui.inlineExecOutput setting */}
        {inlineExecOutput && msg.toolCallId && execOutputs[msg.toolCallId] && (
          <div className="ml-5 mt-1 p-2 bg-black/80 text-green-400 text-[11px] font-mono rounded max-h-48 overflow-y-auto border border-gray-700">
            <pre
              className="whitespace-pre-wrap"
              style={{
                background: 'transparent',
                border: 'none',
                borderRadius: 0,
                padding: 0,
                margin: 0,
              }}
            >
              {execOutputs[msg.toolCallId].stdout}
              {execOutputs[msg.toolCallId].stderr ? (
                <span className="text-red-400">{execOutputs[msg.toolCallId].stderr}</span>
              ) : null}
            </pre>
            {execOutputs[msg.toolCallId].running && (
              <span className="inline-block w-1.5 h-3 bg-green-400 animate-pulse ml-0.5 align-middle" />
            )}
          </div>
        )}
      </div>
    );
  }
  if (msg.role === 'error') {
    return (
      <div className="flex items-start gap-3">
        <AgentAvatar />
        <div
          className="text-sm rounded-2xl px-4 py-3"
          style={{
            background: 'var(--danger-bg)',
            color: 'var(--danger)',
            border: '1px solid var(--danger)',
          }}
        >
          <div className="whitespace-pre-wrap break-words">{msg.content}</div>
          {msg.action === 'retry-load' && onRetryLoad && (
            <button
              type="button"
              onClick={onRetryLoad}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              style={{
                background: 'var(--danger)',
                color: 'var(--danger-bg)',
              }}
            >
              <RefreshCw size={13} />
              {msg.actionLabel ?? '重试'}
            </button>
          )}
          {msg.action === 'open-provider-settings' && onOpenProviderSettings && (
            <button
              type="button"
              onClick={onOpenProviderSettings}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors"
              style={{
                background: 'var(--danger)',
                color: 'var(--danger-bg)',
              }}
            >
              <Settings size={13} />
              {msg.actionLabel ?? '配置 Provider'}
            </button>
          )}
          {/* #1000 未登录拦截：错误气泡内直接一键浏览器登录（自管理忙碌/反馈态），
              登录成功后移除本引导气泡 */}
          {msg.action === 'login' && (
            <QraftLoginButton
              testId="chat-error-login-btn"
              size="sm"
              busyLabel="等待授权中…"
              className="mt-3"
              onLoggedIn={() => onLoginSuccess?.(msg)}
            />
          )}
        </div>
      </div>
    );
  }

  if (msg.role === 'subagent') {
    return (
      <div className="flex items-start gap-3" data-testid="subagent-result">
        <GitMerge size={18} style={{ color: 'var(--accent)', marginTop: 6 }} />
        <div
          className="text-sm rounded-2xl px-4 py-3 prose prose-sm max-w-none break-words overflow-x-auto"
          style={{
            background: 'var(--surface-muted)',
            color: 'var(--text)',
            border: '1px solid var(--border-subtle)',
            maxWidth: '82%',
            minWidth: 0,
          }}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
        </div>
      </div>
    );
  }

  const isUser = msg.role === 'user';
  const hasCodeBlock = /```[\s\S]*?```/.test(msg.content);

  // 复制选区（restored from pre-#577, issue #677）：选中即复制选中、
  // 否则复制全文。hover 不得清掉菜单打开时捕获的选区。
  const selectMessageText = () => {
    const textEl = bubbleRef.current?.querySelector('[data-message-body]') as HTMLElement | null;
    if (!textEl) return;
    const range = document.createRange();
    range.selectNodeContents(textEl);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
  };
  const deselectMessageText = () => {
    window.getSelection()?.removeAllRanges();
  };
  const copyWithSelection = () => {
    const selected = capturedSelectionRef.current;
    onCopy(selected.length > 0 ? selected : msg.content, copyIdx ?? turnIndex ?? 0);
    deselectMessageText();
  };

  /** #574 dev tools: copy the message's raw metadata as formatted JSON
   *  (metadata only — the body is covered by 复制文本, so including it
   *  here would only duplicate content in a truncated form). */
  const copyRawMessage = () => {
    const payload = {
      role: msg.role,
      toolCallId: msg.toolCallId ?? null,
      toolName: msg.toolName ?? null,
      timestamp: msg.timestamp,
    };
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2)).catch(() => {});
  };

  /** #574 dev tools: copy the message's localized timestamp string. */
  const copyTimestamp = () => {
    const local = new Date(msg.timestamp).toLocaleString('zh-CN', { hour12: false });
    navigator.clipboard.writeText(local).catch(() => {});
  };

  const contextItems: ContextMenuAction[] = isUser
    ? [
        {
          label: '复制文本',
          onEnter: selectMessageText,
          onLeave: deselectMessageText,
          onSelect: copyWithSelection,
        },
        { label: '复制原始消息', onSelect: copyRawMessage },
        { label: '复制时间戳', onSelect: copyTimestamp },
        { label: '重试', onSelect: () => onRetry?.(msg) },
      ]
    : [
        {
          label: '复制文本',
          onEnter: selectMessageText,
          onLeave: deselectMessageText,
          onSelect: copyWithSelection,
        },
        { label: '复制原始消息', onSelect: copyRawMessage },
        { label: '复制时间戳', onSelect: copyTimestamp },
        ...(hasCodeBlock
          ? [
              {
                label: '复制代码',
                onSelect: () => {
                  const codeMatch = msg.content.match(/```[\s\S]*?```/g);
                  if (codeMatch) {
                    const code = codeMatch
                      .map((b) => b.replace(/```\w*\n?/g, '').replace(/```$/g, ''))
                      .join('\n\n');
                    navigator.clipboard.writeText(code).catch(() => {});
                  }
                },
              },
            ]
          : []),
      ];

  return (
    <>
      {/* 用户消息时间戳——居中显示在上一回答与本提问之间(ChatGPT 式),
          组件自维护分钟 tick,不牵动整棵 memo 气泡树 */}
      {isUser && <TimestampLabel timestamp={msg.timestamp} />}
      <ContextMenu items={contextItems}>
        {({ onContextMenu }) => (
          <div
            ref={bubbleRef}
            className={cn(
              'flex min-w-0 gap-3',
              isUser ? 'items-start justify-end' : 'flex-col items-start',
              // reply-content (thinking/tools already rendered the icon rail) —
              // indent the body so it lines up with the thinking/tool labels.
              hideHeader && !isUser && 'pl-4'
            )}
            onContextMenu={(e) => {
              // Capture any manual selection before hover-preview can replace it
              capturedSelectionRef.current = window.getSelection()?.toString() ?? '';
              onContextMenu(e);
            }}
            data-testid={isUser ? 'chat-message-user' : 'chat-message-assistant'}
          >
            {!isUser && !hideHeader && (
              <div className="flex items-center gap-2 mb-3 pl-2" data-testid="assistant-avatar-row">
                <AgentAvatar />
                <span
                  className="text-[16px] font-semibold shrink-0 whitespace-nowrap"
                  style={{ color: 'var(--text)' }}
                >
                  MiQroForge
                </span>
              </div>
            )}

            {/* Pending spinner — the optimistic user bubble is shown before the
              backend has accepted the send; a small spinning icon (no text)
              outside the bubble tells the user it's on its way.  It appears
              only while this exact bubble (matched by timestamp) is still
              pending (issue #364). */}
            {isUser && sending === msg.timestamp && (
              <Loader2 size={14} className="animate-spin shrink-0 self-center text-text-faint" />
            )}

            <div
              className={cn(
                'group flex min-w-0 flex-col gap-1.5',
                isUser ? 'items-end max-w-[calc(100%-48px)]' : 'w-full'
              )}
            >
              {/* image attachments */}
              {msg.attachments
                ?.filter((a) => a.type === 'image')
                .map((att, i) =>
                  att.dataUrl ? (
                    <img
                      key={i}
                      src={att.dataUrl}
                      alt={att.name}
                      className="rounded-xl max-w-[280px] max-h-[200px] object-cover"
                      style={{ border: '1px solid var(--border-subtle)' }}
                    />
                  ) : (
                    // Restoring / read-failed image — placeholder instead of a
                    // broken <img> (same fallback as the composer, CodeRabbit #661).
                    <div
                      key={i}
                      className="flex h-24 w-24 shrink-0 items-center justify-center rounded-xl"
                      style={{
                        border: '1px solid var(--border-subtle)',
                        background: 'var(--surface-muted)',
                      }}
                    >
                      <Image size={18} style={{ color: 'var(--info)' }} />
                    </div>
                  )
                )}
              {/* text attachments */}
              {msg.attachments
                ?.filter((a) => a.type === 'text')
                .map((att, i) => (
                  <div
                    key={i}
                    className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs"
                    style={{
                      background: 'var(--surface-muted)',
                      border: '1px solid var(--border-subtle)',
                      color: 'var(--text-muted)',
                    }}
                  >
                    <FileText size={12} className="shrink-0 text-text-faint" />
                    <span className="truncate min-w-0" title={att.name}>
                      {att.name}
                    </span>
                  </div>
                ))}
              {/* document attachments */}
              {msg.attachments
                ?.filter((a) => a.type === 'document')
                .map((att, i) => {
                  const cat = getDocCategory(att.name);
                  const isDone = !att.status || att.status === 'done';
                  const isParsing = att.status === 'parsing';
                  return (
                    <div
                      key={i}
                      className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition-all duration-500"
                      style={{
                        background: isDone && cat ? cat.bg : 'var(--surface-muted)',
                        border: `1px solid ${isDone && cat ? cat.color + '40' : 'var(--border-subtle)'}`,
                        color: isDone && cat ? cat.color : 'var(--text-muted)',
                        opacity: isDone ? 1 : 0.7,
                      }}
                    >
                      <span
                        className="shrink-0 rounded font-bold text-[10px] px-1 py-0.5 leading-none text-white"
                        style={{ background: isDone && cat ? cat.color : 'var(--text-faint)' }}
                      >
                        {cat ? cat.label : 'FILE'}
                      </span>
                      <span className="truncate min-w-0" title={att.name}>
                        {att.name}
                      </span>
                      <span className="shrink-0 whitespace-nowrap">
                        ({formatFileSize(att.size)})
                      </span>
                      {isParsing && (
                        <Loader2 size={11} className="shrink-0 animate-spin text-text-muted" />
                      )}
                      {isDone && (
                        <CheckCircle
                          size={11}
                          className="shrink-0"
                          style={{ color: 'var(--success)' }}
                        />
                      )}
                    </div>
                  );
                })}
              {/* Always clean injected document text from content — shown as chips only when attachments are missing */}
              {isUser &&
                (() => {
                  const { cleanContent, chips } = extractFileChips(msg.content);
                  // Always store cleaned content so the bubble renders without injected text
                  (msg as any).__cleanContent = cleanContent;
                  // Only show historical chips when there are no real attachments (avoids duplicates)
                  if (chips.length === 0 || (msg.attachments && msg.attachments.length > 0))
                    return null;
                  return chips.map((chip, i) => (
                    <div
                      key={`hist-${i}`}
                      className="flex min-w-0 max-w-full items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs"
                      style={{
                        background: chip.category.bg,
                        border: `1px solid ${chip.category.color}40`,
                        color: chip.category.color,
                      }}
                    >
                      <span
                        className="shrink-0 rounded font-bold text-[10px] px-1 py-0.5 leading-none text-white"
                        style={{ background: chip.category.color }}
                      >
                        {chip.category.label}
                      </span>
                      <span className="truncate min-w-0" title={chip.name}>
                        {chip.name}
                      </span>
                      <CheckCircle
                        size={11}
                        className="shrink-0"
                        style={{ color: 'var(--success)' }}
                      />
                    </div>
                  ));
                })()}

              {/* Main bubble — AI 侧去气泡：正文直接落在 chat 背景上撑满列宽（issue #772） */}
              <div
                data-message-body
                className={cn(
                  'text-sm transition-shadow',
                  isUser && 'rounded-2xl rounded-br-none px-4 py-3'
                )}
                style={{
                  lineHeight: 'var(--leading-relaxed)',
                  ...(isUser
                    ? { background: 'var(--bubble-user-bg)', color: 'var(--bubble-user-text)' }
                    : { color: 'var(--bubble-ai-text)' }),
                }}
              >
                {isUser && editing ? (
                  /* 编辑态(#828):原地变输入框,提交 = 截断到此处并用新文本重新回答 */
                  <div className="flex flex-col gap-2 min-w-[320px] max-w-full">
                    <textarea
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      autoFocus
                      rows={3}
                      data-testid="edit-message-input"
                      className="w-full resize-none rounded-lg px-3 py-2 text-sm bg-[var(--surface)] text-[var(--text)] border border-[var(--border)] focus:outline-none focus:border-[var(--accent)]"
                      style={{ lineHeight: 'var(--leading-relaxed)' }}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') setEditing(false);
                        if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                          if (
                            editText.trim() !== '' &&
                            editText !== extractFileChips(msg.content).cleanContent
                          ) {
                            onEdit?.(msg, editText);
                          }
                          setEditing(false);
                        }
                      }}
                    />
                    <div className="flex items-center justify-end gap-2">
                      <button
                        onClick={() => setEditing(false)}
                        className="px-3 py-1.5 text-xs rounded-lg bg-[var(--surface)] text-[var(--text-muted)] hover:text-[var(--text)] transition-colors"
                      >
                        取消
                      </button>
                      <button
                        onClick={() => {
                          if (
                            editText.trim() !== '' &&
                            editText !== extractFileChips(msg.content).cleanContent
                          ) {
                            onEdit?.(msg, editText);
                          }
                          setEditing(false);
                        }}
                        disabled={
                          editText.trim() === '' ||
                          editText === extractFileChips(msg.content).cleanContent
                        }
                        data-testid="edit-message-submit"
                        className="px-3 py-1.5 text-xs rounded-lg bg-[var(--accent)] text-white disabled:opacity-40 transition-opacity"
                      >
                        重新回答
                      </button>
                    </div>
                  </div>
                ) : showRawOnError ? (
                  <div>
                    <pre
                      className="p-3 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all overflow-auto"
                      style={{
                        color: 'var(--text-muted)',
                        background: 'var(--surface-muted)',
                        maxHeight: '60vh',
                      }}
                    >
                      {msg.content}
                    </pre>
                    <button
                      onClick={() => setShowRawOnError(false)}
                      className="mt-1 text-xs underline"
                      style={{ color: 'var(--accent)' }}
                    >
                      返回
                    </button>
                  </div>
                ) : (
                  <ErrorBoundary
                    fallback={(error, reset) => (
                      <div
                        className="text-xs p-2 rounded"
                        style={{ color: 'var(--danger)', background: 'var(--danger-bg)' }}
                      >
                        ⚠ 消息渲染失败
                        <button
                          onClick={reset}
                          className="ml-2 underline"
                          style={{ color: 'var(--accent)' }}
                        >
                          重试
                        </button>
                        <button
                          onClick={() => setShowRawOnError(true)}
                          className="ml-2 underline"
                          style={{ color: 'var(--accent)' }}
                        >
                          显示原文
                        </button>
                      </div>
                    )}
                  >
                    {msg.role === 'assistant' && msg.content === '' && !msg.reasoning ? (
                      <span className="inline-block w-2 h-4 bg-[var(--accent)] animate-pulse rounded-sm" />
                    ) : msg.role === 'assistant' ? (
                      <>
                        {/* Reasoning-mode icon (issue #680): shown only when there
                          is NO thinking block above (the block's icon already
                          carries 🚀/🧠 by mode — avoids duplicate badges). The
                          icon follows the message's OWN mode, not the live
                          app-wide mode (audit P0-2).
                          #905 follow-up: reply-content messages (hideHeader)
                          always sit under a reply-head thinking block whose
                          header already shows 🚀/🧠 — a second inline 🚀
                          right above the answer (below the tool rows) is a
                          duplicate badge. Check the ACTUAL presence of the
                          block above, not msg.reasoning (which lives on the
                          separate progress row and is always undefined here). */}
                        {(msg.reasoningMode ?? reasoningMode) === 'fast' &&
                          !msg.reasoning &&
                          !hideHeader && (
                            <span
                              className="mr-1 text-[11px] leading-none select-none"
                              style={{ color: '#d9a520' }}
                            >
                              🚀
                            </span>
                          )}
                        {/* #671: streaming = 本条是最后一条且会话正在生成 ——
                            正在生成的回答流式期间 mermaid/svg 显示源码；历史消息不塌回。
                            CodeRabbit 修订：改用真实生成信号 streaming（2722/2724 由
                            turn 生命周期驱动），不再用乐观 sending 时间戳 ——
                            sending 是用户回合信号，assistant 回复期间可能已为 null。 */}
                        <MarkdownContent
                          content={msg.content}
                          streaming={streaming}
                          sources={sources}
                        />
                      </>
                    ) : (
                      renderContent((msg as any).__cleanContent ?? msg.content)
                    )}
                  </ErrorBoundary>
                )}
              </div>

              {/* 2026-08-27：计划/确认是 AI 回答的一部分——卡在消息内容后、操作栏前 */}
              {cards && cards.length > 0 && (
                <div className="flex flex-col gap-1 w-full mt-1" data-testid="inline-cards">
                  {cards.map((c) => (
                    <ConfirmCardItem
                      key={c.request.input_id}
                      entry={c as never}
                      resolve={resolveCard}
                      timeoutCard={timeoutCard}
                    />
                  ))}
                </div>
              )}

              {/* 用户消息操作 — 复制 / 编辑(仅鼠标靠近/hover 消息时显示,#828;
                  编辑态下隐藏,避免与编辑框叠在一起) */}
              {isUser && msg.content !== '' && !editing && (
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity mt-1">
                  <button
                    onClick={() =>
                      // 复制与编辑同一套 cleanContent 语义(review):
                      // 附件消息的 content 含内部序列化块,不能把内部标记复制出去
                      onCopy(extractFileChips(msg.content).cleanContent, copyIdx ?? turnIndex ?? 0)
                    }
                    title="复制"
                    aria-label="复制"
                    className="flex items-center justify-center w-8 h-8 rounded-lg bg-[var(--surface-muted)]/70 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors"
                  >
                    {isCopied ? (
                      <Check size={14} style={{ color: 'var(--success)' }} />
                    ) : (
                      <Copy size={14} />
                    )}
                  </button>
                  {onEdit && (
                    <button
                      onClick={() => {
                        setEditing(true);
                        // 附件消息的 content 含序列化文件块 — 编辑器只带可见文本,
                        // 附件原样保留在 original.attachments(CodeRabbit #1011)
                        setEditText(extractFileChips(msg.content).cleanContent);
                      }}
                      disabled={streaming}
                      title={streaming ? '生成中,暂不可编辑' : '编辑并重新回答'}
                      aria-label="编辑并重新回答"
                      data-testid="edit-message-btn"
                      className="flex items-center justify-center w-8 h-8 rounded-lg bg-[var(--surface-muted)]/70 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-[var(--surface-muted)]/70"
                    >
                      <Pencil size={14} />
                    </button>
                  )}
                </div>
              )}

              {/* 常驻免责声明（#836）—— 每条 AI 回答正文底部 */}
              {!isUser && msg.content !== '' && (
                <div className="mt-0.5" data-testid="chat-disclaimer">
                  <span className="text-size-2xs leading-relaxed text-[var(--text-faint)] select-none">
                    {CHAT_DISCLAIMER_ZH}
                  </span>
                </div>
              )}

              {/* Message action bar — copy / regenerate / feedback / sources.
                #828: 按钮放大(浅灰底大点击区)、复制不再 hover 选中/高亮框、
                常驻显示(不随 hover 出现消失) */}
              {!isUser && msg.content !== '' && (
                <div
                  className="flex items-center gap-1.5 self-start mt-3 mb-3"
                  data-testid="message-actions"
                >
                  <button
                    onClick={() => onCopy(msg.content, copyIdx ?? turnIndex ?? 0)}
                    title="复制"
                    aria-label="复制"
                    className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--surface-muted)]/70 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    {isCopied ? (
                      <Check size={16} style={{ color: 'var(--success)' }} />
                    ) : (
                      <Copy size={16} />
                    )}
                  </button>
                  {onRegenerate && (
                    <button
                      onClick={() => onRegenerate?.(msg)}
                      title="重新生成"
                      aria-label="重新生成"
                      className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--surface-muted)]/70 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                    >
                      <RefreshCw size={16} />
                    </button>
                  )}
                  <button
                    onClick={() => {
                      const next = feedback === 'up' ? null : 'up';
                      setFeedback(next);
                      persistFeedback(next);
                    }}
                    title="喜欢"
                    aria-label="喜欢"
                    className={`flex items-center justify-center w-9 h-9 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)]/50 transition-colors ${
                      feedback === 'up'
                        ? 'text-[var(--accent)] bg-[var(--accent-soft)]'
                        : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]'
                    }`}
                  >
                    <ThumbsUp size={16} />
                  </button>
                  <button
                    onClick={() => {
                      const next = feedback === 'down' ? null : 'down';
                      setFeedback(next);
                      persistFeedback(next);
                      if (next === 'down') {
                        setDislikeText('');
                        setDislikeDone(false);
                        setShowDislike(true);
                      }
                    }}
                    title="不喜欢"
                    aria-label="不喜欢"
                    className={`flex items-center justify-center w-9 h-9 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-muted)]/50 transition-colors ${
                      feedback === 'down'
                        ? 'text-[var(--danger)] bg-[var(--danger-bg)]'
                        : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]'
                    }`}
                  >
                    <ThumbsDown size={16} />
                  </button>
                  {/* 查看来源 always visible (#547 原版行为) — 无来源时弹窗给提示 */}
                  <button
                    onClick={() => setShowSources(true)}
                    title="查看来源"
                    aria-label="查看来源"
                    className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--surface-muted)]/70 text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <ExternalLink size={16} />
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </ContextMenu>

      {/* Sources modal — tools used for this answer + reference URLs (#547). */}
      <Modal
        open={showSources}
        onOpenChange={setShowSources}
        title={`查看来源${(sources ?? []).length > 0 ? `（${(sources ?? []).length}）` : ''}`}
      >
        <div className="flex flex-col gap-1.5 max-h-[50vh] overflow-y-auto">
          {(sources ?? []).map((s, i) => (
            <a
              key={`${s.url}-${i}`}
              href={s.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-start gap-2 rounded-lg px-2.5 py-2 text-xs hover:bg-[var(--surface-muted)] transition-colors"
            >
              <ExternalLink size={12} className="shrink-0 mt-0.5" />
              <span className="min-w-0 flex-1">
                <span className="block truncate">
                  {s.tool ? `${s.tool} · ` : ''}
                  {s.title || s.url}
                </span>
                {s.title && s.title !== s.url && (
                  <span className="block truncate text-[var(--text-muted)]">{s.url}</span>
                )}
                {s.snippet && (
                  <span className="block truncate text-[var(--text-muted)]">{s.snippet}</span>
                )}
              </span>
            </a>
          ))}
          {(sources ?? []).length === 0 && (
            <p className="text-xs text-[var(--text-muted)]">该回答未使用网络工具，没有参考资料。</p>
          )}
        </div>
      </Modal>

      {/* Dislike feedback modal — lightweight report actually submitted to
        the backend feedback channel (not just a local toggle). */}
      <Modal open={showDislike} onOpenChange={setShowDislike} title="反馈：回答不满意">
        <div className="flex flex-col gap-3">
          <p className="text-xs text-[var(--text-muted)]">
            感谢反馈。可以补充说明哪里不满意（可选），我们会将这条反馈连同消息内容一起提交。
          </p>
          <textarea
            value={dislikeText}
            onChange={(e) => setDislikeText(e.target.value)}
            placeholder="可选：说明不满意的地方（例如：答案不准确、缺少引用……）"
            rows={3}
            disabled={dislikeSending || dislikeDone}
            className="w-full rounded-lg px-3 py-2 text-sm bg-[var(--surface-muted)] border border-[var(--border-subtle)] focus:outline-none focus:border-[var(--accent)]"
          />
          {dislikeDone ? (
            <p className="text-xs" style={{ color: 'var(--success)' }}>
              ✓ 已提交反馈
            </p>
          ) : (
            <>
              {dislikeError && (
                <p className="text-xs" style={{ color: 'var(--danger)' }}>
                  {dislikeError}
                </p>
              )}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setShowDislike(false)}
                  disabled={dislikeSending}
                  className="px-3 py-1.5 rounded-lg text-xs hover:bg-[var(--surface-muted)] transition-colors"
                >
                  取消
                </button>
                <button
                  type="button"
                  onClick={submitDislike}
                  disabled={dislikeSending}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors disabled:opacity-50"
                  style={{ background: 'var(--danger)', color: 'var(--danger-bg)' }}
                >
                  {dislikeSending ? '提交中…' : '提交反馈'}
                </button>
              </div>
            </>
          )}
        </div>
      </Modal>
    </>
  );
}, areMessageBubblePropsEqual);

/* 工作目录选择下拉：点胶囊就近弹出的紧凑面板（对齐 #940 收敛稿——不再用居中的
 * 420px Modal）。锚定在胶囊下方，收录「最近使用 + 浏览… + 使用默认工作目录」。
 * 用全屏透明遮罩挡掉下层点击：点遮罩（含胶囊）收起、点面板内行执行动作。 */
interface WorkspacePickerMenuProps {
  anchor: DOMRect | null;
  recent: string[];
  current: string | null;
  onClose: () => void;
  onPick: (ws: string) => void;
  onDefault: () => void;
  onBrowse: () => void;
}

function WorkspacePickerMenu({
  anchor,
  recent,
  current,
  onClose,
  onPick,
  onDefault,
  onBrowse,
}: WorkspacePickerMenuProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  // 先在胶囊下方摆放，渲染后按视口尺寸收边（面板宽度为 max-content，需实测）。
  const [pos, setPos] = useState<{ left: number; top: number }>(() => {
    if (!anchor) return { left: 8, top: 8 };
    return { left: anchor.left, top: anchor.bottom + 6 };
  });

  // 摆放：先在胶囊下方就位，渲染后按视口收边。用 ResizeObserver 而不是把尺寸塞进
  // deps——「最近使用」是异步拉回来的，面板高度在打开后还会长一次；只在挂载时量
  // 一次会漏掉那次增长，面板会从胶囊下方一路长到视口外，底下几行点不到。
  useEffect(() => {
    const node = panelRef.current;
    if (!node || !anchor) return;
    const place = () => {
      const rect = node.getBoundingClientRect();
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      const left = Math.max(8, Math.min(anchor.left, vw - rect.width - 8));
      const below = anchor.bottom + 6;
      const above = anchor.top - rect.height - 6;
      // 下方放不下再整体翻到胶囊上方
      const top = below + rect.height <= vh - 8 || above < 8 ? below : Math.max(8, above);
      setPos((p) => (p.left === left && p.top === top ? p : { left, top }));
    };
    place();
    const ro = new ResizeObserver(place);
    ro.observe(node);
    return () => ro.disconnect();
  }, [anchor]);

  // Esc / 滚动 / 窗口尺寸变化时收起
  useEffect(() => {
    if (!anchor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    const onReposition = () => onClose();
    window.addEventListener('resize', onReposition);
    window.addEventListener('scroll', onReposition, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onReposition);
      window.removeEventListener('scroll', onReposition, true);
    };
  }, [anchor, onClose]);

  if (!anchor) return null;

  const normCurrent = current?.toLowerCase();

  return createPortal(
    <>
      {/* 全屏遮罩：点遮罩即关闭，且拦下点击不让它落到胶囊上（避免点同一个
          胶囊时“先关后开”又弹回来）。 */}
      <div className="fixed inset-0 z-[59]" onMouseDown={onClose} />
      <div
        ref={panelRef}
        role="menu"
        aria-label="选择工作目录"
        data-testid="workspace-picker-modal"
        className="fixed z-[60] overflow-y-auto overflow-x-hidden rounded-xl border bg-[var(--surface-elevated)] p-1.5 shadow-[0_12px_30px_rgba(0,0,0,0.16)]"
        style={{
          left: pos.left,
          top: pos.top,
          minWidth: 288,
          maxWidth: 'min(360px, calc(100vw - 16px))',
          // 菜单只封顶宽度是不够的：「最近使用」可以很长，没有高度上限 + 内部滚动
          // 时会一路长出视口底部，底下几行点不到（原来的居中 Modal 有 70vh +
          // overflow:auto，换成 anchored menu 时把这个保护丢了）。
          maxHeight: 'min(420px, calc(100vh - 16px))',
          borderColor: 'var(--border)',
        }}
      >
        {recent.length > 0 && (
          <>
            <div
              className="px-2 pt-0.5 pb-1 text-[9.5px] font-bold uppercase tracking-[0.06em] text-text-faint select-none"
              data-testid="workspace-picker-recent-label"
            >
              最近使用
            </div>
            {recent.map((ws, idx) => {
              const isCur = !!current && ws.toLowerCase() === normCurrent;
              return (
                <button
                  key={ws}
                  type="button"
                  role="menuitem"
                  aria-current={isCur ? 'true' : undefined}
                  onClick={() => onPick(ws)}
                  data-testid={`workspace-picker-recent-${idx}`}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] transition-colors',
                    isCur ? 'bg-[var(--accent-soft)]' : 'hover:bg-[var(--surface-muted)]'
                  )}
                >
                  {isCur ? (
                    <FolderCheck
                      size={13}
                      style={{ color: 'var(--accent)' }}
                      className="shrink-0"
                    />
                  ) : (
                    <Folder size={13} style={{ color: 'var(--text-muted)' }} className="shrink-0" />
                  )}
                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-[var(--text)]',
                      isCur && 'font-medium'
                    )}
                    title={ws}
                  >
                    {ws}
                  </span>
                  {/* 当前项靠「行底色 + FolderCheck + 右侧勾」三重表达，不再单写
                      一个「当前」字样——那是第四个信号，读起来反而吵。 */}
                  {isCur && (
                    <Check
                      size={13}
                      aria-hidden
                      className="shrink-0"
                      style={{ color: 'var(--accent)' }}
                    />
                  )}
                </button>
              );
            })}
            <div className="my-1 border-t border-border-subtle" />
          </>
        )}
        <button
          type="button"
          role="menuitem"
          onClick={onBrowse}
          data-testid="workspace-picker-browse"
          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] font-semibold text-[var(--accent)] transition-colors hover:bg-[var(--surface-muted)]"
        >
          <FolderOpen size={13} className="shrink-0" />
          浏览…
        </button>
        <button
          type="button"
          role="menuitem"
          onClick={onDefault}
          data-testid="workspace-picker-default"
          className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-muted)]"
        >
          <Folder size={13} style={{ color: 'var(--text-muted)' }} className="shrink-0" />
          使用默认工作目录
        </button>
      </div>
    </>,
    document.body
  );
}

/**
 * #1071 review P1（2026-09-16）：`cards` 的浅数组比较。
 *
 * 必须**逐项**比身份，不能比数组引用：渲染点写的是
 * `cards={inlineCardsForGroup(group)}`（L8097），而 `inlineCardsForGroup` 每次
 * 调用都 `filter` 出一个新数组——比引用会恒为 false，反而把 #538 的 memo 优化
 * 整个废掉。逐项比身份的正确性来自数据源：卡条目出自 `cardsByTurn`（`useMemo`
 * 于 `allCards`），卡没变则条目对象不变 → 相等 → 气泡跳过渲染；卡晚到或
 * pending→confirmed/cancelled/modify 时产生新条目 → 不等 → 重渲染。
 *
 * 修的是真缺陷：`inlineCardIds`（L7064）在 memo 之外算，兜底区已把该卡排除，
 * 气泡若不重渲染，这张卡就哪里都不显示。
 */
export function cardsEqual(a?: UserInputCardEntry[], b?: UserInputCardEntry[]): boolean {
  if (a === b) return true;
  // undefined 与 [] 同为「无卡」，视为相等（渲染点目前恒传数组，此处是防御）
  const left = a ?? [];
  const right = b ?? [];
  if (left.length !== right.length) return false;
  return left.every((entry, i) => entry === right[i]);
}

/**
 * Memo comparator: skip re-render unless a rendering-relevant prop changed.
 * All callbacks are stable useCallback references; msg/sources/searchResults
 * stay referentially stable while the typewriter streams (see sourcesByMsg's
 * tool-only signature), so untouched bubbles skip render entirely during
 * animation frames — the #538 全量重渲染 fix for long sessions.
 */
function areMessageBubblePropsEqual(a: MessageBubbleProps, b: MessageBubbleProps): boolean {
  return (
    a.msg === b.msg &&
    cardsEqual(a.cards, b.cards) &&
    a.sessionKey === b.sessionKey &&
    a.turnIndex === b.turnIndex &&
    a.copyIdx === b.copyIdx &&
    a.execOutputs === b.execOutputs &&
    a.inlineExecOutput === b.inlineExecOutput &&
    a.isLast === b.isLast &&
    a.streaming === b.streaming &&
    a.sources === b.sources &&
    a.toolStepIndex === b.toolStepIndex &&
    a.isLastToolRow === b.isLastToolRow &&
    a.searchResults === b.searchResults &&
    a.downloadingPaperId === b.downloadingPaperId &&
    a.paperDownloadStates === b.paperDownloadStates &&
    a.sending === b.sending &&
    a.isCopied === b.isCopied &&
    a.onCopy === b.onCopy &&
    a.onRetry === b.onRetry &&
    a.onRetryLoad === b.onRetryLoad &&
    a.onRegenerate === b.onRegenerate &&
    a.onOpenProviderSettings === b.onOpenProviderSettings &&
    a.onDownloadPaper === b.onDownloadPaper &&
    // #1042: both are now stable references (the render site passes the
    // useCallback and MessageBubble binds the message itself), so comparing
    // them is safe and closes the last gap in this comparator.
    a.onResume === b.onResume &&
    a.onRestart === b.onRestart &&
    a.onEdit === b.onEdit
  );
}

/** localStorage key for per-message 👍/👎 feedback (session-scoped entries). */
const MSG_FEEDBACK_KEY = 'miqi:msg-feedback';

/** Strip <think>...</think> reasoning blocks before rendering.
 *  Handles both complete blocks and cross-message orphans
 *  (tags split across streaming chunks). */
