import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  memo,
  type ComponentProps,
} from 'react';
import { AgentAvatar } from './components/Avatars';
import { MiQroForgeLogo } from '../../components/MiQroForgeLogo';
import { MarkdownContent } from './components/MarkdownContent';
import { SandboxHtmlFrame } from './components/SandboxHtmlFrame';
import { ThinkBlock } from './components/ThinkBlock';
import { InterruptedTurnCard } from './components/InterruptedTurnCard';
import { DiffView } from './components/DiffView';
import { renderContent } from './components/renderContent';
import { TrackedFileCard } from './components/TrackedFileCard';
import { ConfirmCardArea } from './components/ConfirmCardArea';
import { TurnStatusBar } from './components/TurnStatusBar';
import { ToolCommandBlock } from './components/ToolCommandBlock';
import { useUserInput } from '../../contexts/UserInputContext';
import { ErrorBoundary } from '../../components/ErrorBoundary';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Button } from '../../components/ui/Button';
import { Textarea } from '../../components/ui/Textarea';
import { Tooltip } from '../../components/ui/Tooltip';
import { ContextMenu, type ContextMenuAction } from '../../components/ContextMenu';
import { cn } from '../../lib/utils';
import { Modal } from '../../components/shared';
import { formatRelativeTime } from '../../lib/formatTime';
import {
  ExecutionPolicySelector,
  type ExecutionPolicy,
} from '../../components/ExecutionPolicySelector';
import { ReasoningModeSwitch, type ReasoningMode } from './components/ReasoningModeSwitch';
import {
  Send,
  Square,
  Loader2,
  Copy,
  Check,
  CheckCircle,
  Paperclip,
  X,
  FileText,
  Image,
  LayoutGrid,
  MoreHorizontal,
  Plus,
  Eye,
  GitMerge,
  ChevronDown,
  ChevronRight,
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
  Scissors,
  ClipboardPaste,
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
import { classifyTrackedFiles } from '../../lib/taskAssetClassification';
import { SpreadsheetPreview } from './components/SpreadsheetPreview';
import { DocxPreview } from './components/DocxPreview';
import PaperSearchResult, {
  tryParsePaperSearchResult,
  type PaperSearchPayload,
  type PaperItem,
} from './PaperSearchResult';

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
  /** Parse error message if status === 'error' */
  parseError?: string;
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

const IMAGE_PLACEHOLDER_RES = /\[Image:\s*([^\]]+)\]/g;

/** Extract image attachments from the "[Image: name]" placeholder the sender
 *  embeds. dataUrl stays undefined — it is re-read from the session files dir
 *  lazily after load (#659). */

// #875 D1：系统包安装 persist/runtime 失败 → App 级 toast 的 window 事件。
export const INSTALL_WARNING_EVENT = 'miqi:system-install-warning';
export type InstallWarningKind = 'persist' | 'runtime';
function extractImageAttachmentsFromContent(content: string): Attachment[] | undefined {
  const names = [...content.matchAll(IMAGE_PLACEHOLDER_RES)].map((m) => m[1].trim());
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
  /** Parsed tool data for card rendering */
  toolData?: unknown;
  /** Original tool-call arguments (e.g. web_fetch's url) — real references */
  toolArgs?: unknown;
  action?: 'open-provider-settings' | 'retry-load';
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
function extractMessageSources(msg: Message): MessageSource[] {
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
  const entryRe = /^\d+\.\s+(.+)$/gm;
  let m: RegExpExecArray | null;
  while ((m = entryRe.exec(content)) !== null) {
    const title = m[1].trim();
    const rest = content.slice(m.index + m[0].length).split(/\n(?=\d+\.\s)/)[0];
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

function createProviderConfigMessage(content?: string): Message {
  return {
    role: 'error',
    content: content || '尚未配置模型服务。请先配置 Provider/API Key 后再发送消息。',
    action: 'open-provider-settings',
    actionLabel: '去配置模型',
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

/* ─── Tracked file from tool hints ───────────────────────────────── */
interface TrackedFile {
  path: string;
  name: string;
  op: 'read' | 'write' | 'edit' | 'delete';
  /** epoch ms of last operation */
  lastSeen: number;
  /** path was truncated in the progress message (ends with ...) */
  truncated?: boolean;
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
 *  Same-named files in different directories stay distinct. */
function mergeTrackedFiles(
  existing: TrackedFile[],
  incoming: Array<{ path: string; name?: string; op?: TrackedFile['op']; lastSeen?: number }>
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
    };
    const existingIdx = out.findIndex((p) => {
      const np2 = normalizeTrackedPath(p.path);
      if (np2 === np) return true;
      const oneIsBare = !np2.includes('/') || !np.includes('/');
      return oneIsBare && basename(np2) === basename(np);
    });
    if (existingIdx >= 0) out[existingIdx] = entry;
    else out.push(entry);
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
        mode={thinking.reasoningMode ?? fallbackMode}
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

// #891 深度审阅 #11：删 flag 门控与保留块须用同一匹配（两处不再手写漂移）。
// 唯一匹配改为一对一：merged 里每条持久化用户行只认领最早一条同内容、时间相近
// 的乐观气泡。此前 .some() 会让同一条持久化副本同时满足多条相同文本的气泡——
// 用户 30s 内两次发送同一句、恢复快照时第二条尚未落盘，两条都会被误判为已持久
// 化而漏掉第二条。返回数组与 frontend 等长：matched[i]===true 表示该条乐观气泡
// 已有专属持久化副本。
function _markUserTwinMatches(frontend: Message[], merged: Message[]): boolean[] {
  const matched = new Array<boolean>(frontend.length).fill(false);
  for (const pm of merged) {
    if (pm.role !== 'user') continue;
    const idx = frontend.findIndex(
      (m, i) =>
        !matched[i] &&
        m.role === 'user' &&
        String(pm.content) === String(m.content) &&
        _isPersistedCopyOf(m.timestamp, pm.timestamp)
    );
    if (idx >= 0) matched[idx] = true;
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
function extractTrackedFilesFromMessages(rawMsgs: any[]): TrackedFile[] {
  const fileMap = new Map<string, TrackedFile>();
  const rank: Record<TrackedFile['op'], number> = { read: 0, edit: 1, write: 2, delete: 3 };

  const upsert = (path: string, op: TrackedFile['op'], timestamp?: string) => {
    const key = normalizeSandboxPath(path).replace(/\\/g, '/');
    const existing = fileMap.get(key);
    if (!existing || rank[op] > rank[existing.op]) {
      fileMap.set(key, {
        path: key,
        name: basename(key),
        op,
        lastSeen: timestamp ? new Date(timestamp).getTime() : Date.now(),
        truncated: false,
      });
    }
  };

  for (const msg of rawMsgs) {
    // Format 1: _tool_hint metadata (persisted progress events)
    const hintText = msg._tool_hint_text || msg.content;
    if (msg._tool_hint && hintText) {
      const parsed = parseToolHint(hintText);
      if (parsed) {
        upsert(parsed.path, parsed.op, msg.timestamp);
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
          upsert(filePath, toolName === 'delete_file' ? 'delete' : 'write', msg.timestamp);
        } else if (_FILE_READ_TOOLS.includes(toolName)) {
          upsert(filePath, 'read', msg.timestamp);
        }
      }
    }

    // Format 3: tool result messages with name field
    if (msg.role === 'tool' && msg.name) {
      const toolName: string = msg.name;
      // Try to extract path from content (often contains the file path)
      const contentPath = parseToolHint(String(msg.content || ''));
      if (contentPath) {
        upsert(contentPath.path, contentPath.op, msg.timestamp);
      } else if (_FILE_WRITE_TOOLS.includes(toolName)) {
        // Tool result without parsable content — try to infer from tool name
        // (best-effort; actual path is in the paired assistant tool_calls message)
      }
    }
  }
  return Array.from(fileMap.values());
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
        : '平台积分不足，任务未执行。请到 设置 → Qraft 平台账号 查看余额。',
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
  onOpenApprovals?: () => void;
  onWorkspaceLoaded?: (workspace: string | null) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  // #875 D1（外部评估 P0/A1）：系统包安装的 persist/runtime 失败标记只写在
  // 工具输出里，模型可能摘要掉——用户会误以为「允许并记住」已永久生效。
  // 扫描消息中的失败标记并发 window 事件，由 App 级 toast 呈现（不依赖模型）。
  const warnedInstallWarnRef = useRef(new Map<string, number>());
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
  // sourcesByMsg cache: keyed by a tool-only signature so the map object is
  // stable across typewriter frames (see sourcesByMsg below).
  const sourcesCacheRef = useRef<{ sig: string; map: Map<Message, MessageSource[]> } | null>(null);
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
  const [input, setInput] = useState('');
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
  // EB-1 欢迎页模式卡选中态：独立于 reasoningMode（深度研究与代码任务都映射 think，
  // 若用 reasoningMode 推导会同时高亮两张卡）。welcomeMode 是组件级 state，跨会话
  // 不随欢迎页重挂而重置（ChatConsole 常驻），见下方 sessionKey effect。
  const [welcomeMode, setWelcomeMode] = useState<'fast' | 'think' | 'code'>(
    reasoningMode === 'think' ? 'think' : 'fast'
  );
  const selectWelcomeMode = (k: 'fast' | 'think' | 'code') => {
    setWelcomeMode(k);
    setReasoningMode(k === 'code' ? 'think' : k);
  };
  // Composer 侧的推理模式切换(ReasoningModeSwitch / 建议提示)同样要同步 welcome
  // 卡高亮——否则空态下先选了「代码任务」再从输入条切 fast,welcomeMode 停在 code、
  // 发送却用 fast,高亮与真实模式不一致(CodeRabbit)。会话已有消息后 welcome 卡不
  // 再渲染,只在 messages.length===0 时回写 welcomeMode。
  const changeReasoningMode = (m: ReasoningMode) => {
    setReasoningMode(m);
    if (messages.length === 0) setWelcomeMode(m);
  };
  // 切到新会话时按当前 reasoningMode 重新派生选中卡：避免沿用上个会话的 code 选择，
  // 却因中途切到 fast 而高亮与发送模式不一致（CodeRabbit）。仅随 sessionKey 触发，
  // 不在同一会话内用 reasoningMode 变化覆盖用户手动选卡。
  useEffect(() => {
    setWelcomeMode(reasoningMode === 'think' ? 'think' : 'fast');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);
  const [streaming, setStreaming] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
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
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(280);
  const panelResizing = useRef(false);
  const [workspacePickerOpen, setWorkspacePickerOpen] = useState(false);
  const [recentWorkspaces, setRecentWorkspaces] = useState<string[]>([]);

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

  // Task Assets panel resize
  const handlePanelResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    panelResizing.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!panelResizing.current) return;
      // panel is on the right, so new width = window width - mouse x
      const newWidth = window.innerWidth - e.clientX;
      setPanelWidth(Math.max(200, Math.min(500, newWidth)));
    };
    const handleMouseUp = () => {
      if (panelResizing.current) {
        panelResizing.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
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
  }, []);
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
  /** preview modal */
  const [previewFile, setPreviewFile] = useState<{
    path: string;
    content?: string;
    dataBase64?: string;
    /** #877: rich render kind — pdf iframe / spreadsheet table / docx blocks. */
    kind?: 'pdf' | 'spreadsheet' | 'document';
    pdfUrl?: string;
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
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // 会话活动感知（restored from pre-#577, issue #677）：流式/消息变化时
  // 上报 App，让"+"能感知未落盘的活动
  useEffect(() => {
    const hasActivity =
      streaming || messages.some((m) => m.role === 'user' || m.role === 'assistant');
    onSessionActivityChange?.(hasActivity);
  }, [streaming, messages, onSessionActivityChange]);
  const { lastAdjustAt, setActiveSession } = useUserInput();
  // 调整提示占位词用 state 驱动（而非直接改 DOM placeholder）——React 不会
  // 主动重写该属性，直改会永久残留（CodeRabbit #711）。
  const [adjustHint, setAdjustHint] = useState(false);
  // 会话隔离（CodeRabbit #666）：切会话 → 清空全部确认卡
  useEffect(() => {
    setActiveSession(sessionKey);
    setAdjustHint(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);
  // 用户点了"调整方案"→ 聚焦输入框并提示输入调整要求（issue #646）。
  // 聚焦在 composer 重新可用（流式结束）之后执行——disabled 状态下
  // focus 无效，回合结束后焦点会丢失（CodeRabbit #711）。
  useEffect(() => {
    if (!lastAdjustAt) return;
    setAdjustHint(true);
  }, [lastAdjustAt]);
  useEffect(() => {
    if (!adjustHint || streaming) return;
    textareaRef.current?.focus();
  }, [adjustHint, streaming]);
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
      const focusInput = () => textareaRef.current?.focus();
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
      textareaRef.current?.focus();
      window.setTimeout(() => {
        void window.miqi.app?.focus?.({ hard: true }).then(() => {
          window.setTimeout(() => textareaRef.current?.focus(), 80);
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
      textareaRef.current?.focus();
      window.setTimeout(() => {
        void window.miqi.app?.focus?.({ hard: true }).then(() => {
          window.setTimeout(() => textareaRef.current?.focus(), 80);
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
  // Monotonic id for lifecycleRef identity checks.
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
  const trackFile = useCallback((path: string, op: TrackedFile['op'], truncated = false) => {
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
          (eitherIsBareFilename && basename(f.path) === basename(clean))
        );
      });
      if (existing) {
        // Upgrade: read < edit < write
        const rank: Record<TrackedFile['op'], number> = { read: 0, edit: 1, write: 2, delete: 3 };
        const nextOp = rank[op] > rank[existing.op] ? op : existing.op;
        return prev.map((f) =>
          f.path === existing.path
            ? { ...f, op: nextOp, lastSeen: Date.now(), truncated: f.truncated && truncated }
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
            (basename(f.path) === basename(normPath) &&
              (!f.path.includes('/') || !normPath.includes('/')))
        );
        if (dup) return prev;
        return [
          ...prev,
          { path: normPath, name: basename(normPath), op, lastSeen: Date.now(), truncated },
        ];
      });
    });
  }, []);

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
      setInput('');
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
        }));
        setTrackedFiles(mergeTrackedFiles(existingFromMessages, backendMapped));

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
  useEffect(() => {
    if (!historyLoaded) return;
    const el = scrollRef.current;
    if (!el) return;
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

  const handleAttachClick = () => fileInputRef.current?.click();

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    Array.from(e.target.files ?? []).forEach((file) => {
      const isImage = file.type.startsWith('image/');
      const isDocument = DOCUMENT_SUFFIXES_RE.test(file.name);
      const isTextLike = TEXT_SUFFIXES_RE.test(file.name) || file.type.startsWith('text/');

      if (isTextLike && !isDocument) {
        // Plain text files — read directly as text
        const reader = new FileReader();
        reader.onload = () =>
          setAttachments((prev) => [
            ...prev,
            { name: file.name, type: 'text', content: reader.result as string, size: file.size },
          ]);
        reader.readAsText(file);
      } else if (isTextLike && isDocument) {
        // Markdown/text files detected as documents — read as text AND as base64 for server fallback
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = (reader.result as string).split(',')[1];
          const textContent = new TextDecoder().decode(
            Uint8Array.from(atob(base64), (c) => c.charCodeAt(0))
          );
          setAttachments((prev) => [
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
          ]);
        };
        reader.readAsDataURL(file);
      } else if (isDocument) {
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = (reader.result as string).split(',')[1];
          const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
          // PDF/MD/text parse instantly client-side → done; Office/RTF needs server → pending
          const isServerParsed = /^(docx|doc|pptx|ppt|xlsx|xls|odt|odp|ods|rtf)$/i.test(ext);
          const parseStatus: Attachment['status'] = isServerParsed ? 'pending' : 'done';

          setAttachments((prev) => [
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
          ]);
        };
        reader.readAsDataURL(file);
      } else if (isImage) {
        const reader = new FileReader();
        reader.onload = () =>
          setAttachments((prev) => [
            ...prev,
            { name: file.name, type: 'image', dataUrl: reader.result as string, size: file.size },
          ]);
        reader.readAsDataURL(file);
      } else {
        const reader = new FileReader();
        reader.onload = () =>
          setAttachments((prev) => [
            ...prev,
            { name: file.name, type: 'text', content: reader.result as string, size: file.size },
          ]);
        reader.readAsText(file);
      }
    });
    e.target.value = '';
  };

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
  }, [clearFinalCleanupTimer, currentReqId]);

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

  // Opens the workspace picker modal — called by the inline "更换" button
  const handleOpenWorkspacePicker = useCallback(async () => {
    const workspaces = await window.miqi.sessions
      .listRecentWorkspaces()
      .then((r) => r?.workspaces ?? [])
      .catch(() => [] as string[]);
    setRecentWorkspaces(workspaces);
    setWorkspacePickerOpen(true);
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
  /** 程序化发送（论文下载 fallback 等）经此 ref 显式传文本，handleSend
   *  一次性消费。不依赖 setInput 后的渲染 flush（旧闭包读 input 是旧值）。 */
  const programmaticTextRef = useRef<string | null>(null);
  /** #740: pending resume-turn id — set by 继续执行, consumed by handleSend
   *  so the resume request flows through the full send pipeline (listeners,
   *  streaming render) instead of a bare chat.send call. */
  const resumeTurnIdRef = useRef<string | null>(null);
  /** Per-session send id of the send currently in its pre-stream pending phase
   *  (issue #364).  A session is "pending" while its optimistic bubble waits on
   *  the non-blocking provider check / thread init.  The double-Enter guard
   *  bails only for a session that owns a pending send (other sessions may
   *  start their own turn), and the stop button cancels only the current
   *  session's pending send.  Comparing the stored id against this closure's
   *  own id lets a superseded / re-sent / cancelled send know it lost the turn. */
  const pendingSendIdsRef = useRef<Map<string, number>>(new Map());
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
    // 移除中断卡——resume 的新回复由流式事件接管渲染
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
    // 发送即清除调整提示——占位词只属于"点了调整方案之后"的输入场景
    setAdjustHint(false);
    // #740: resume consumes the pending resume-turn id (set by 继续执行).
    const _resumeId = resumeTurnIdRef.current;
    resumeTurnIdRef.current = null;
    const payload = retryPayloadRef.current;
    // 程序化发送（论文下载 fallback 等）经 ref 显式传文本：不依赖
    // setInput 后的渲染 flush（旧闭包读到的 input state 是旧值）。
    const programmaticText = programmaticTextRef.current;
    programmaticTextRef.current = null;
    const text = (payload?.text ?? programmaticText ?? input).trim();
    const atts = payload?.attachments ?? attachments;
    if (!text && atts.length === 0 && !_resumeId) {
      retryPayloadRef.current = null;
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
    // Unique id for THIS send, stored in the pending map.  A later send for
    // the same session overwrites it, so this closure can tell it lost the
    // turn (its provider check must not proceed).
    const thisSendId = ++sendSeqRef.current;
    pendingSendIdsRef.current.set(sendSessionKey, thisSendId);
    setSendingFor(sendSessionKey, userMsg.timestamp);
    setStreaming(true);
    // #740: resume has no user bubble (the interrupted card was removed and
    // the streamed reply takes over rendering) — skip the optimistic push.
    if (!_resumeId) setMessages((prev) => [...prev, userMsg]);
    userScrolledUp.current = false;
    setInput('');
    // Reset textarea height after sending
    setTimeout(() => {
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }, 0);
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
    try {
      const result = await window.miqi.providers.list();
      const hasConfiguredProvider = result.providers.some((provider) => provider.configured);
      if (!hasConfiguredProvider) {
        // No configured provider — replace the optimistic bubble with the
        // provider-config guidance.  The send is refused: the user should
        // configure a provider before sending.  The draft is restored to the
        // input so they can re-send once configured.  Only touch the composer /
        // message list if THIS session is still displayed — the user may have
        // switched away while providers.list was pending, and setInput /
        // setAttachments / setMessages act on the currently displayed session.
        pendingSendIdsRef.current.delete(sendSessionKey);
        streamingBySession.delete(sendSessionKey);
        setSendingFor(sendSessionKey, null);
        if (currentSessionRef.current === sendSessionKey) {
          setStreaming(false);
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.timestamp === userMsg.timestamp) {
              return [...prev.slice(0, -1), createProviderConfigMessage()];
            }
            return prev;
          });
          setInput(text);
          setAttachments(atts);
        }
        return;
      }

      // ── #922 AI 网关门禁 ──
      // 登录后网关状态明确非 active（provisioning/failed/disabled）时拒绝发起
      // 会话：把乐观气泡换成网关提示并恢复输入框。未登录 / 平台未下发网关状态
      // 时放行（与模型面板语义一致）。旧 preload/smoke mock 无 qraft 命名空间则跳过。
      const gatewayStatus =
        typeof window.miqi.qraft?.status === 'function'
          ? await window.miqi.qraft.status().catch(() => null)
          : null;
      if (
        gatewayStatus?.loggedIn === true &&
        gatewayStatus.aiGateway &&
        gatewayStatus.aiGateway.status !== 'active'
      ) {
        pendingSendIdsRef.current.delete(sendSessionKey);
        streamingBySession.delete(sendSessionKey);
        setSendingFor(sendSessionKey, null);
        if (currentSessionRef.current === sendSessionKey) {
          setStreaming(false);
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.timestamp === userMsg.timestamp) {
              return [...prev.slice(0, -1), createGatewayBlockedMessage()];
            }
            return prev;
          });
          setInput(text);
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
    // and setInput / setAttachments / setMessages act on the current session.
    if (!isCurrentPendingSend(sendSessionKey, thisSendId)) {
      pendingSendIdsRef.current.delete(sendSessionKey);
      streamingBySession.delete(sendSessionKey);
      setSendingFor(sendSessionKey, null);
      if (currentSessionRef.current === sendSessionKey) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.timestamp === userMsg.timestamp) return prev.slice(0, -1);
          return prev;
        });
        setInput(text);
        setAttachments(atts);
      }
      return;
    }

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
    const turnId = ++turnSeqRef.current;
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
      // awaited, and setInput / setAttachments / setMessages act on the
      // currently displayed session.
      if (currentSessionRef.current === sendSessionKey) {
        setStreaming(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (last?.timestamp === userMsg.timestamp) return prev.slice(0, -1);
          return prev;
        });
        setInput(text);
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

    // Build message content with embedded document text
    for (const att of atts) {
      if (att.type === 'text' && att.content) {
        content += `\n\n[File: ${att.name}]\n\`\`\`\n${att.content}\n\`\`\``;
      } else if (att.type === 'image' && att.dataUrl) {
        content += `\n\n[Image: ${att.name}]`;
      } else if (att.type === 'document' && att.dataBase64) {
        // Decode and extract text client-side
        try {
          const raw = Uint8Array.from(atob(att.dataBase64), (c) => c.charCodeAt(0));
          let extracted = '';
          const ext = att.name.split('.').pop()?.toLowerCase() ?? '';

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

          if (extracted && extracted.trim()) {
            content += `\n\n--- Document: ${att.name} ---\n${extracted.slice(0, 50000)}\n--- End of ${att.name} ---`;
          } else if (ext === 'pdf') {
            content += `\n\n[${att.name}: scanned PDF — OCR will be attempted by the server]`;
          } else {
            content += `\n\n[${att.name}: binary file, server will parse]`;
          }
        } catch {
          content += `\n\n[${att.name}: ${formatFileSize(att.size)} — parsing on server]`;
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
            if (
              last?.role === 'assistant' &&
              last.timestamp === ts &&
              last.content !== fullContent
            ) {
              return [...prev.slice(0, -1), { ...last, content: fullContent }];
            }
            if (last?.role === 'assistant' && last.content !== fullContent) {
              return [...prev.slice(0, -1), { ...last, content: fullContent }];
            }
            if (!last || last.role !== 'assistant') {
              return [...prev, { role: 'assistant', content: fullContent, timestamp: ts }];
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
        if (parsed) trackFile(parsed.path, parsed.op, parsed.truncated);
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
            trackFile(filePath, 'write', false);
          } else if (_FILE_READ_TOOLS.includes(toolName)) {
            trackFile(filePath, 'read', false);
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
                }));
                return mergeTrackedFiles(prev, mapped);
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
          ? createProviderConfigMessage(message)
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
      const sendPromise = window.miqi.chat.send(
        content,
        key,
        threadId ?? undefined,
        executionPolicy,
        chatAttachments.length > 0 ? chatAttachments : undefined,
        workspace ?? undefined,
        reasoningModeRef.current,
        _resumeId ?? undefined
      );

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
        sendCleanup();
        // Identity-scoped: only THIS invocation's listeners — the shared
        // unsubsRef may point at a newer overlapping send.
        cleanupListeners(myUnsubs);
        sendInvocationRegistryRef.current.delete(thisSendId);
        return;
      }
      const errMsg = sanitizeUiMessage(e?.message ?? String(e ?? '未知错误'));
      if (isProviderConfigurationProblem(errMsg, e?.code)) {
        setMessages((prev) => [...prev, createProviderConfigMessage(errMsg)]);
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
    input,
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
    setInput(instruction);
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

  /** Auto-resize textarea to fit content */
  const adjustTextareaHeight = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
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
      // Session-isolated files live under sessions/<safe-key>/files/. The full
      // session-relative path is the ONLY form the bridge reliably reads for
      // bare tracked names (verified: bare-name reads return null at the
      // bridge); bare + session_key is also rejected. Build the full path from
      // the active session key and read it workspace-scoped.
      const safeKey = String(currentSessionRef.current ?? '').replace(/[:\\/]/g, '_');
      const fullRel = safeKey ? `sessions/${safeKey}/files/${bare}` : '';
      const reads: Array<Promise<{ content?: string }>> = [];
      if (fullRel && fullRel !== path) reads.push(window.miqi.files.read(fullRel));
      reads.push(window.miqi.files.read(path));
      if (bare !== path) reads.push(window.miqi.files.read(path, currentSessionRef.current));
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
      // path 本身已是 sessions/<safe>/files/<name> 全路径时,再带 session_key
      // 会被 files.read 二次拼接会话目录而读不到(桥接对全路径+session_key
      // 返回 null),补一个 workspace-scoped 候选并优先尝试(CodeRabbit #889)。
      if (/^sessions\/[^/]+\/files\//.test(path.replace(/\\/g, '/'))) {
        candidates.unshift({ p: path, withSession: false });
      }
      const nameOnly = path.replace(/\\/g, '/').split('/').pop()!;
      if (nameOnly !== path) candidates.push({ p: nameOnly, withSession: true });
      if (!path.startsWith('papers/'))
        candidates.push({ p: `papers/${nameOnly}`, withSession: true });
      if (nameOnly === path) {
        const safeKey = String(currentSessionRef.current ?? '').replace(/[:\\/]/g, '_');
        if (safeKey) {
          candidates.push({ p: `sessions/${safeKey}/files/${nameOnly}`, withSession: false });
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
    const result = await window.miqi.files.openExternal(path);
    if (!result?.opened) {
      setPreviewFile({ path, content: `(Could not open file: ${path})` });
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

  // Handle clipboard paste for files and images (Ctrl+V)
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind !== 'file') continue;
      const file = item.getAsFile();
      if (!file) continue;
      const isDocument = DOCUMENT_SUFFIXES_RE.test(file.name);
      if (isDocument) {
        const reader = new FileReader();
        reader.onload = () => {
          const base64 = (reader.result as string).split(',')[1];
          const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
          const isServerParsed = /^(docx|doc|pptx|ppt|xlsx|xls|odt|odp|ods|rtf)$/i.test(ext);
          const parseStatus: Attachment['status'] = isServerParsed ? 'pending' : 'done';
          setAttachments((prev) => [
            ...prev,
            {
              name: file.name,
              type: 'document',
              dataBase64: base64,
              size: file.size,
              mimeType: file.type || getMimeTypeFromName(file.name),
              status: parseStatus,
            },
          ]);
        };
        reader.readAsDataURL(file);
      } else if (file.type.startsWith('image/')) {
        const reader = new FileReader();
        reader.onload = () =>
          setAttachments((prev) => [
            ...prev,
            {
              name: file.name || 'pasted-image.png',
              type: 'image',
              dataUrl: reader.result as string,
              size: file.size,
            },
          ]);
        reader.readAsDataURL(file);
      }
    }
  }, []);

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

  // Composer right-click edit menu (剪切/复制/粘贴/全选) — restored from
  // #547 after the #577 rewrite dropped it.
  const inputContextItems = useMemo<ContextMenuAction[]>(
    () => [
      {
        label: '剪切',
        icon: <Scissors size={14} />,
        shortcut: 'Ctrl+X',
        onSelect: () => {
          const el = textareaRef.current;
          if (!el) return;
          const s = el.selectionStart,
            e = el.selectionEnd;
          if (s === e) return;
          navigator.clipboard.writeText(el.value.slice(s, e)).catch(() => {});
          el.setRangeText('', s, e, 'end');
          // Let React's onChange pick up the new value — manual setInput can
          // drift from the DOM (deleting then requires two passes).
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.focus();
        },
      },
      {
        label: '复制',
        icon: <Copy size={14} />,
        shortcut: 'Ctrl+C',
        onSelect: () => {
          const el = textareaRef.current;
          if (!el) return;
          const txt = el.value.slice(el.selectionStart, el.selectionEnd);
          if (txt) navigator.clipboard.writeText(txt).catch(() => {});
        },
      },
      {
        label: '粘贴',
        icon: <ClipboardPaste size={14} />,
        shortcut: 'Ctrl+V',
        onSelect: () => {
          const el = textareaRef.current;
          if (!el) return;
          navigator.clipboard
            .readText()
            .then((text) => {
              if (!text) return;
              // Insert at the caret like native Ctrl+V — replace the current
              // selection range instead of always appending at the end.
              const s = el.selectionStart ?? el.value.length;
              const e = el.selectionEnd ?? s;
              el.setRangeText(text, s, e, 'end');
              // Let React's onChange pick up the new value (single source of
              // truth for state vs DOM — avoids double-delete drift).
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.focus();
            })
            .catch(() => {});
        },
      },
      {
        label: '全选',
        icon: <CheckCircle size={14} />,
        shortcut: 'Ctrl+A',
        divider: true,
        onSelect: () => textareaRef.current?.select(),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
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
  const sourcesSig = messages
    .map((m) =>
      m.role === 'progress' ? `${m.toolCallId ?? ''}:${m.content?.length ?? 0}` : m.role
    )
    .join('|');
  const sourcesByMsg = useMemo(() => {
    if (sourcesCacheRef.current?.sig === sourcesSig) return sourcesCacheRef.current.map;
    const map = new Map<Message, MessageSource[]>();
    let pending: MessageSource[] = [];
    let seen = new Set<string>();
    const merge = (next: MessageSource[]) => {
      for (const s of next) {
        if (seen.has(s.url)) continue;
        seen.add(s.url);
        pending.push(s);
      }
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
        if (own.length > 0) map.set(m, own);
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
        map.set(m, pending);
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
      setInput(msg.content);
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
      setInput(userMsg.content);
      setAttachments(userMsg.attachments ?? []);
      requestAnimationFrame(() => handleSendRef.current());
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

  const shareButtonTone = shareStatus === 'idle' ? 'var(--text)' : 'var(--success)';
  const shareButtonBackground = 'var(--surface-muted)';
  const shareButtonBorder = 'var(--border-subtle)';

  return (
    <div
      className="flex flex-col h-full"
      style={previewFile ? { pointerEvents: 'none' } : undefined}
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      onPaste={handlePaste}
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
        <div className="flex flex-col flex-1 overflow-hidden">
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
              <span className="tag-inprogress shrink-0">{'\u8fdb\u884c\u4e2d'}</span>
              <div
                className="flex min-w-0 items-center gap-1.5 shrink-0 text-[12px] leading-none whitespace-nowrap"
                aria-label={taskHeaderInfo.meta}
                style={{ color: 'var(--text-faint)' }}
              >
                <span>{taskHeaderInfo.updatedLabel}</span>
                <span aria-hidden="true">·</span>
                <span>{taskHeaderInfo.fileLabel}</span>
                <span aria-hidden="true">·</span>
                <span>{taskHeaderInfo.pluginLabel}</span>
              </div>
            </div>
            <div
              className="flex shrink-0 items-stretch overflow-hidden rounded-md shadow-[0_1px_0_rgba(18,18,18,0.05)]"
              style={{
                background: shareButtonBackground,
                border: `1px solid ${shareButtonBorder}`,
              }}
            >
              <button
                onClick={handleCopyTaskSummary}
                className="flex h-7 min-w-[96px] items-center justify-center gap-1.5 px-3 text-xs font-semibold transition-colors whitespace-nowrap hover:brightness-95"
                style={{
                  color: shareButtonTone,
                  cursor: 'pointer',
                }}
                title="复制任务摘要"
                aria-label="复制任务摘要"
              >
                {shareStatus === 'idle' ? <Send size={12} /> : <Check size={12} />}
                {shareButtonLabel}
              </button>
              <ContextMenu items={shareMenuItems} minWidth={180}>
                {({ onContextMenu }) => (
                  <Tooltip content="复制摘要、导出 Markdown 或复制上下文">
                    <button
                      onClick={onContextMenu}
                      className="flex h-7 w-7 items-center justify-center transition-colors hover:brightness-95"
                      style={{
                        borderLeft: `1px solid ${shareButtonBorder}`,
                        color: shareStatus === 'idle' ? 'var(--text-muted)' : 'var(--success)',
                      }}
                      title="更多分享方式"
                      aria-label="更多分享方式"
                      aria-haspopup="menu"
                    >
                      <ChevronDown size={12} />
                    </button>
                  </Tooltip>
                )}
              </ContextMenu>
            </div>
            <Tooltip content="显示或隐藏文件面板">
              <button
                onClick={() => setPanelOpen((v) => !v)}
                className="p-1.5 rounded hover:bg-[var(--surface-muted)] transition-colors shrink-0 ml-1"
                title="显示或隐藏文件面板"
                aria-label="显示或隐藏文件面板"
                data-testid="toggle-assets-panel-btn"
              >
                <LayoutGrid size={14} style={{ color: 'var(--text-faint)' }} />
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
              className={`max-w-[760px] mx-auto px-4 py-5 flex flex-col gap-2 ${
                historyLoaded && messages.length === 0 ? 'min-h-full' : ''
              }`}
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
                        key: 'think' as const,
                        icon: '🧠',
                        tag: '深度研究',
                        tagline: '面向复杂任务',
                        desc: '长链路检索、推理与方案推演，先想后答。',
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
                          className={`flex-1 flex flex-col items-center gap-[5px] rounded-xl px-3 py-3 cursor-pointer transition-colors duration-200 border min-h-[132px] ${
                            active ? 'border-[var(--accent)]' : 'border-[var(--border-subtle)]'
                          } hover:border-[var(--accent)]`}
                          style={{
                            background: active
                              ? 'color-mix(in srgb, var(--surface) 92%, var(--accent-soft))'
                              : 'var(--surface)',
                          }}
                        >
                          <span
                            className="inline-flex items-center gap-[6px] text-[13px] font-bold"
                            style={{ color: 'var(--text)' }}
                          >
                            <span className="text-[15px]">{m.icon}</span>
                            {m.tag}
                          </span>
                          <span className="text-[11px] text-text-faint">{m.tagline}</span>
                          <span className="text-[11.5px] text-text-muted leading-snug">
                            {m.desc}
                          </span>
                          {/* ✓ 仅 active 渲染:opacity 隐藏会让文本留在 DOM,
                              toContainText 断言不了"取消选中"。占位 div 保持底部对齐。 */}
                          <div
                            className="mt-auto flex items-center justify-center"
                            style={{ minHeight: 16, color: 'var(--accent)' }}
                          >
                            {active && <span className="text-[11px] font-bold">✓ 已选择</span>}
                          </div>
                        </button>
                      );
                    })}
                  </div>
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
                        execOutputs={execOutputs}
                        inlineExecOutput={inlineExecOutput}
                        sources={sourcesByMsg.get(group.msg) ?? []}
                        toolStepIndex={toolStepByMsg.get(group.msg)}
                        isLast={i === chatGroups.length - 1}
                        onResume={
                          group.msg.interrupted ? () => handleResumeTurn(group.msg) : undefined
                        }
                        onRestart={
                          group.msg.interrupted ? () => handleRestartTurn(group.msg) : undefined
                        }
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
                        onDownloadPaper={handleDownloadPaper}
                        downloadingPaperId={downloadingPaperId}
                        paperDownloadStates={paperDownloadStates}
                        sending={sendingFor(sessionKey)}
                      />
                    </div>
                  )
                )
              )}
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

          {/* Composer */}
          <div
            className="shrink-0 px-5 pb-4 pt-3"
            style={{
              background: 'var(--background)',
            }}
          >
            <div className="max-w-[760px] mx-auto">
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-2 mb-2">
                  {attachments.map((att, i) => {
                    const isDoc = att.type === 'document';
                    const cat = isDoc ? getDocCategory(att.name) : null;
                    const isPending = isDoc && (!att.status || att.status === 'pending');
                    const isParsing = isDoc && att.status === 'parsing';
                    const isDone = isDoc && att.status === 'done';
                    const isError = isDoc && att.status === 'error';

                    return (
                      <div
                        key={i}
                        className="flex items-center gap-2 rounded-lg pl-2 pr-1.5 py-1.5 text-xs group max-w-[240px] cursor-pointer hover:brightness-95 transition-all"
                        style={{
                          background: isDoc && cat ? cat.bg : 'var(--surface-muted)',
                          border: `1px solid ${isDoc && cat ? cat.color + '40' : 'var(--border-subtle)'}`,
                        }}
                        onClick={async (e) => {
                          // Ignore clicks that arrive right after closing preview
                          // (the close button click can fall through to the chip behind)
                          if (previewJustClosed.current) return;
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
                            className="shrink-0 rounded font-bold text-[10px] px-1.5 py-0.5 leading-none"
                            style={{ background: cat.color, color: '#fff' }}
                          >
                            {cat.label}
                          </span>
                        ) : att.type === 'image' ? (
                          att.dataUrl ? (
                            <img
                              src={att.dataUrl}
                              alt={att.name}
                              className="h-12 w-12 shrink-0 rounded object-cover"
                              style={{ border: '1px solid var(--border-subtle)' }}
                            />
                          ) : (
                            <Image
                              size={14}
                              className="shrink-0"
                              style={{ color: 'var(--info)' }}
                            />
                          )
                        ) : (
                          <FileText size={14} className="shrink-0 text-text-faint" />
                        )}

                        {/* Name + size */}
                        <div className="flex flex-col min-w-0 leading-tight">
                          <span className="truncate font-medium text-text">
                            {att.name.length > 28
                              ? att.name.slice(0, 25) + '…' + att.name.slice(-4)
                              : att.name}
                          </span>
                          <span className="text-[10px] text-text-muted">
                            {formatFileSize(att.size)}
                            {isDoc && isParsing && ' · 解析中…'}
                            {isDoc && isDone && ' · 已就绪'}
                            {isDoc && isError && ' · 解析失败'}
                          </span>
                        </div>

                        {/* Status icon — only after send */}
                        {isDoc && isParsing && (
                          <Loader2
                            size={13}
                            className="shrink-0 animate-spin"
                            style={{ color: cat?.color ?? 'var(--text-faint)' }}
                          />
                        )}
                        {isDoc && isDone && (
                          <CheckCircle
                            size={13}
                            className="shrink-0"
                            style={{ color: 'var(--success)' }}
                          />
                        )}
                        {isDoc && isError && (
                          <AlertCircle
                            size={13}
                            className="shrink-0"
                            style={{ color: 'var(--danger)' }}
                          />
                        )}

                        {/* Remove */}
                        <button
                          onClick={() => removeAttachment(i)}
                          className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity hover:bg-[rgba(0,0,0,0.1)] rounded p-0.5"
                        >
                          <X size={11} style={{ color: 'var(--text-faint)' }} />
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Turn status (issue #646: 等待你的确认) */}
              <TurnStatusBar />

              {/* AI-initiated user confirmation cards (issue #646) */}
              <ConfirmCardArea />

              <div
                className="flex flex-col rounded-3xl px-7 py-3.5 transition-all"
                data-testid="chat-input-container"
                style={{
                  background: 'color-mix(in srgb, var(--surface) 85%, transparent)',
                  backdropFilter: 'blur(16px)',
                  WebkitBackdropFilter: 'blur(16px)',
                  border: '1px solid color-mix(in srgb, var(--border) 60%, transparent)',
                  outline: 'none',
                  boxShadow: '0 -4px 20px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04)',
                }}
              >
                {/* Textarea on top — grows up to 1/3 of viewport (DeepSeek style) */}
                <ContextMenu items={inputContextItems} minWidth={160}>
                  {({ onContextMenu }) => (
                    <Textarea
                      ref={textareaRef}
                      value={input}
                      onChange={(e) => {
                        setInput(e.target.value);
                      }}
                      onKeyDown={handleKeyDown}
                      onContextMenu={onContextMenu}
                      placeholder={
                        adjustHint
                          ? '请输入调整要求（例如：市场改为海外、步骤精简到 3 步…）'
                          : '请输入消息或拖入文件...'
                      }
                      rows={1}
                      allowResize={true}
                      className="w-full border-0 bg-transparent p-0! leading-7! focus:ring-0 focus:border-0 min-h-[52px] max-h-[25vh] text-[15px]"
                      style={{ color: 'var(--text)', fieldSizing: 'content' }}
                    />
                  )}
                </ContextMenu>
                {/* Icon row at the bottom — no text, like DeepSeek */}
                <div className="flex items-center gap-3 pt-1.5 mt-0.5 border-t border-[var(--border-subtle)]">
                  <ExecutionPolicySelector
                    policy={executionPolicy}
                    onChange={setExecutionPolicy}
                    onOpenApprovals={onOpenApprovals}
                  />
                  {/* 复杂问题角标（#680 跟进）：轻量气泡挂在模式按钮上，
                      3 秒自动消失，不占输入区。 */}
                  <div className="relative">
                    <ReasoningModeSwitch mode={reasoningMode} onChange={changeReasoningMode} />
                    {complexHint && reasoningMode === 'fast' && (
                      <div
                        className="absolute left-full ml-2 top-1/2 -translate-y-1/2 z-50 flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] whitespace-nowrap"
                        style={{
                          background: '#2f2f3a',
                          border: '1px solid rgba(157,106,223,.45)',
                          color: '#c9a5ef',
                          boxShadow: '0 4px 14px rgba(0,0,0,.35)',
                        }}
                      >
                        {/* 指向按钮的小箭头（左侧） */}
                        <span
                          className="absolute -left-[5px] top-1/2 -translate-y-1/2 w-2 h-2"
                          style={{
                            background: '#2f2f3a',
                            borderLeft: '1px solid rgba(157,106,223,.45)',
                            borderBottom: '1px solid rgba(157,106,223,.45)',
                            transform: 'translateY(-50%) rotate(45deg)',
                          }}
                        />
                        <span>💡 建议</span>
                        <button
                          type="button"
                          onClick={() => {
                            changeReasoningMode('think');
                            setComplexHint(false);
                          }}
                          className="font-semibold cursor-pointer"
                          style={{ color: '#d9b8f5' }}
                        >
                          🧠 深度研究
                        </button>
                        <button
                          type="button"
                          onClick={() => setComplexHint(false)}
                          className="opacity-60 hover:opacity-100 cursor-pointer"
                          aria-label="关闭提示"
                        >
                          ✕
                        </button>
                      </div>
                    )}
                  </div>
                  {/* AI disclaimer — centered in the mode row, fades when typing */}
                  <div className="flex-1 flex items-center justify-center">
                    <span
                      className="text-size-2xs leading-relaxed tracking-wide text-[var(--text-faint)] italic select-none transition-opacity duration-300"
                      style={{ opacity: !input.trim() && attachments.length === 0 ? 1 : 0 }}
                    >
                      AI 也会犯错误，对于重要答案请谨慎验证
                    </span>
                  </div>
                  <button
                    onClick={handleAttachClick}
                    className="shrink-0 p-1.5 rounded hover:bg-[var(--surface-muted)] transition-colors"
                    title="附件或图片"
                    aria-label="附件或图片"
                  >
                    <Paperclip size={15} style={{ color: 'var(--text-faint)' }} />
                  </button>
                  {streaming && !input.trim() && attachments.length === 0 ? (
                    <button
                      onClick={handleAbort}
                      title="停止生成"
                      aria-label="停止生成"
                      className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 hover:bg-[var(--surface-muted)] active:scale-95"
                    >
                      <Square
                        size={12}
                        style={{ color: 'var(--text-muted)' }}
                        fill="currentColor"
                      />
                    </button>
                  ) : (
                    <button
                      onClick={handleSend}
                      disabled={!input.trim() && attachments.length === 0}
                      title={streaming ? '中断当前生成并发送' : '发送'}
                      aria-label={streaming ? '中断当前生成并发送' : '发送'}
                      className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center transition-all duration-200 hover:brightness-110 hover:-translate-y-px active:scale-95 disabled:opacity-30 disabled:hover:brightness-100 disabled:hover:translate-y-0 disabled:shadow-none"
                      style={{
                        background:
                          'linear-gradient(135deg, var(--accent), color-mix(in srgb, var(--accent) 65%, #000))',
                        boxShadow: '0 2px 10px color-mix(in srgb, var(--accent) 35%, transparent)',
                      }}
                    >
                      <Send size={14} style={{ color: '#fff' }} />
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Inline workspace selector — only before the conversation starts */}
            {historyLoaded && messages.length === 0 && (
              <div
                className="flex items-center justify-center mt-2"
                data-testid="inline-workspace-selector"
              >
                <div
                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-xs border shadow-sm"
                  style={{
                    background: 'var(--surface)',
                    borderColor: 'var(--border-subtle)',
                    color: 'var(--text-muted)',
                  }}
                >
                  <Folder size={12} className="shrink-0" />
                  <span
                    className="truncate max-w-[280px]"
                    title={workspace || undefined}
                    data-testid="inline-workspace-path"
                  >
                    {workspace ? `工作目录：${workspace}` : '默认工作目录'}
                  </span>
                  <button
                    type="button"
                    onClick={handleOpenWorkspacePicker}
                    disabled={streaming}
                    className="ml-0.5 text-[var(--accent)] hover:underline disabled:opacity-40 disabled:hover:no-underline"
                    title="更换工作目录"
                    data-testid="inline-workspace-change-btn"
                  >
                    更换
                  </button>
                </div>
              </div>
            )}
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
            className="flex flex-col shrink-0 border-l overflow-y-auto relative"
            style={{
              width: panelWidth,
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
                {resultFiles.length > 0 && (
                  <AssetSection
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
                        onPreview={() => handlePreview(f.path)}
                        onDiff={() => handleShowDiff(f.path)}
                        onReveal={() =>
                          window.miqi.files.openContainingFolder(normalizePath(f.path))
                        }
                      />
                    ))}
                  </AssetSection>
                )}

                {processFiles.length > 0 && (
                  <AssetSection
                    label="过程文件"
                    testKey="process"
                    count={processFiles.length}
                    defaultOpen={resultFiles.length === 0}
                  >
                    {processFiles.map((f) => (
                      <TrackedFileCard
                        key={f.path}
                        file={f}
                        onPreview={() => handlePreview(f.path)}
                        onDiff={() => handleShowDiff(f.path)}
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
                    <div className="text-[10px] font-medium text-text-faint mb-1">结果文件</div>
                    <div className="flex flex-col gap-1.5">
                      {resultWriteEdit.slice(0, 3).map((f) => (
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
                    <div className="text-[10px] font-medium text-text-faint mb-1">过程文件</div>
                    <div className="flex flex-col gap-1.5">
                      {processWriteEdit.slice(0, 3).map((f) => (
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
          className="max-w-[980px] p-0"
        >
          <div
            className="flex flex-col rounded-xl shadow-2xl overflow-hidden"
            style={{
              width: previewFile.kind ? 940 : 820,
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
                      const safeKey = String(currentSessionRef.current ?? '').replace(
                        /[:\\/]/g,
                        '_'
                      );
                      const reads: Array<{ p: string; session?: string }> = [
                        { p: previewFile.path, session: currentSessionRef.current },
                        { p: previewFile.path },
                      ];
                      if (safeKey && nameOnly === previewFile.path) {
                        reads.push({ p: `sessions/${safeKey}/files/${nameOnly}` });
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
                    if (previewFile.dataBase64) {
                      const tmp = `_open_${Date.now()}_${previewFile.path}`;
                      try {
                        await window.miqi.files.write(tmp, '', undefined, previewFile.dataBase64);
                        await window.miqi.files.openExternal(tmp);
                      } catch {
                        /* fallback */
                      }
                    } else {
                      window.miqi.files.openExternal(previewFile.path);
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
              {previewFile.kind === 'pdf' && previewFile.pdfUrl ? (
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

      {/* ── Workspace Picker Modal ── */}
      <Modal
        open={workspacePickerOpen}
        onOpenChange={(o) => {
          if (!o) setWorkspacePickerOpen(false);
        }}
        hideClose
      >
        <div
          className="flex flex-col rounded-xl shadow-2xl"
          style={{
            width: 420,
            maxHeight: '70vh',
            background: 'var(--surface-elevated)',
            border: '1px solid var(--border)',
            pointerEvents: 'auto',
          }}
          onClick={(e) => e.stopPropagation()}
          onMouseDown={(e) => e.stopPropagation()}
          data-testid="workspace-picker-modal"
        >
          <div className="flex items-center justify-between px-4 py-3 border-b shrink-0 border-border-subtle">
            <div className="flex items-center gap-2">
              <Folder size={16} style={{ color: 'var(--accent)' }} />
              <span className="text-sm font-medium text-[var(--text)]">选择工作目录</span>
            </div>
            <button
              onClick={() => setWorkspacePickerOpen(false)}
              className="p-1 rounded hover:bg-[var(--surface-muted)] transition-colors"
            >
              <X size={14} style={{ color: 'var(--text-faint)' }} />
            </button>
          </div>

          <div className="flex-1 overflow-auto p-3 flex flex-col gap-2">
            {/* Recent workspaces */}
            {recentWorkspaces.length > 0 && (
              <>
                <div
                  className="text-[10px] font-semibold uppercase tracking-wider text-text-faint px-1 pt-1 pb-0.5"
                  data-testid="workspace-picker-recent-label"
                >
                  最近使用
                </div>
                {recentWorkspaces.map((ws, idx) => (
                  <button
                    key={ws}
                    onClick={() => createSession(ws)}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg text-left transition-colors hover:bg-[var(--surface-muted)] w-full"
                    data-testid={`workspace-picker-recent-${idx}`}
                  >
                    <FolderCheck
                      size={14}
                      style={{ color: 'var(--text-muted)' }}
                      className="shrink-0"
                    />
                    <span className="text-xs text-[var(--text)] truncate" title={ws}>
                      {ws}
                    </span>
                  </button>
                ))}
                <div className="border-t border-border-subtle my-1" />
              </>
            )}

            {/* Browse button */}
            <button
              onClick={async () => {
                setWorkspacePickerOpen(false);
                try {
                  const dir = await window.miqi.dialog.openDirectory();
                  createSession(dir ?? null);
                } catch {
                  createSession(null);
                }
              }}
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-left transition-colors hover:bg-[var(--surface-muted)] w-full"
              data-testid="workspace-picker-browse"
            >
              <FolderOpen size={14} style={{ color: 'var(--accent)' }} className="shrink-0" />
              <span className="text-xs text-[var(--accent)]">浏览...</span>
            </button>

            {/* Default workspace */}
            <button
              onClick={() => createSession(null)}
              className="flex items-center gap-2 px-3 py-2.5 rounded-lg text-left transition-colors hover:bg-[var(--surface-muted)] w-full"
              data-testid="workspace-picker-default"
            >
              <Folder size={14} style={{ color: 'var(--text-muted)' }} className="shrink-0" />
              <span className="text-xs text-[var(--text-muted)]">使用默认工作目录</span>
            </button>
          </div>
        </div>
      </Modal>
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
  sourcesByMsg: Map<Message, MessageSource[]>;
  searchResultsByCallId: Record<string, string>;
} & Omit<
  ComponentProps<typeof MessageBubble>,
  'msg' | 'sources' | 'toolStepIndex' | 'isLastToolRow' | 'isLast'
>) {
  const [open, setOpen] = useState(true);
  const autoCollapsedRef = useRef(false);
  // Auto-fold once, when the turn completes (a later manual expand is kept).
  useEffect(() => {
    if (done && !autoCollapsedRef.current) {
      autoCollapsedRef.current = true;
      const t = setTimeout(() => setOpen(false), 1500);
      return () => clearTimeout(t);
    }
  }, [done]);

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
            {rows.map((row, i) => (
              <MessageBubble
                key={`${row.timestamp}-${i}`}
                msg={row}
                sources={sourcesByMsg.get(row) ?? []}
                toolStepIndex={i + 1}
                isLastToolRow={i === rows.length - 1}
                isLast={false}
                searchResults={row.toolCallId ? searchResultsByCallId[row.toolCallId] : undefined}
                {...bubbleProps}
              />
            ))}
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
  /** #740: resume/restart an interrupted turn (half-generated reply). */
  onResume?: () => void;
  onRestart?: () => void;
}

const MessageBubble = memo(function MessageBubble({
  msg,
  hideHeader,
  sessionKey,
  execOutputs,
  inlineExecOutput,
  isLast,
  onCopy,
  isCopied,
  onRetry,
  onRetryLoad,
  onRegenerate,
  onOpenProviderSettings,
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
  onResume,
  onRestart,
  reasoningMode,
}: MessageBubbleProps) {
  const [expanded, setExpanded] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
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
  const [copyHovered, setCopyHovered] = useState(false);
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
        title: '回答不满意',
        content:
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
        onResume={onResume}
        onRestart={onRestart}
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
        </div>
      </div>
    );
  }

  if (msg.role === 'subagent') {
    return (
      <div className="flex items-start gap-3">
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
              <div className="flex items-center gap-2 mb-3 pl-2">
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
                  // 经典蓝色框（#547 hover 复制预览）：跟随气泡/正文外框
                  ...(copyHovered ? { boxShadow: '0 0 0 2px var(--accent)' } : {}),
                }}
              >
                {showRawOnError ? (
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
                        <MarkdownContent content={msg.content} />
                      </>
                    ) : (
                      renderContent((msg as any).__cleanContent ?? msg.content)
                    )}
                  </ErrorBoundary>
                )}
              </div>

              {/* 常驻免责声明（#836）—— 每条 AI 回答正文底部 */}
              {!isUser && msg.content !== '' && (
                <div className="mt-0.5" data-testid="chat-disclaimer">
                  <span className="text-size-2xs leading-relaxed text-[var(--text-faint)] select-none">
                    {CHAT_DISCLAIMER_ZH}
                  </span>
                </div>
              )}

              {/* Message action bar — copy / regenerate / feedback / sources.
                Restored from #547 (dropped by the #577 rewrite). */}
              {!isUser && msg.content !== '' && (
                <div
                  className="flex items-center gap-0.5 self-start opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity"
                  data-testid="message-actions"
                >
                  <button
                    onClick={() => onCopy(msg.content, copyIdx ?? turnIndex ?? 0)}
                    onMouseEnter={() => {
                      setCopyHovered(true);
                      selectMessageText();
                    }}
                    onMouseLeave={() => {
                      setCopyHovered(false);
                      deselectMessageText();
                    }}
                    title="复制"
                    aria-label="复制"
                    className="p-1 rounded hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    {isCopied ? (
                      <Check size={13} style={{ color: 'var(--success)' }} />
                    ) : (
                      <Copy size={13} />
                    )}
                  </button>
                  {onRegenerate && (
                    <button
                      onClick={() => onRegenerate?.(msg)}
                      title="重新生成"
                      aria-label="重新生成"
                      className="p-1 rounded hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                    >
                      <RefreshCw size={13} />
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
                    className={`p-1 rounded hover:bg-[var(--surface-muted)] transition-colors ${
                      feedback === 'up' ? 'text-[var(--accent)]' : ''
                    }`}
                  >
                    <ThumbsUp size={13} />
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
                    className={`p-1 rounded hover:bg-[var(--surface-muted)] transition-colors ${
                      feedback === 'down' ? 'text-[var(--danger)]' : ''
                    }`}
                  >
                    <ThumbsDown size={13} />
                  </button>
                  {/* 查看来源 always visible (#547 原版行为) — 无来源时弹窗给提示 */}
                  <button
                    onClick={() => setShowSources(true)}
                    title="查看来源"
                    aria-label="查看来源"
                    className="p-1 rounded hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors"
                  >
                    <ExternalLink size={13} />
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
              className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs hover:bg-[var(--surface-muted)] transition-colors"
            >
              <ExternalLink size={12} className="shrink-0" />
              <span className="truncate">
                {s.tool ? `${s.tool} · ` : ''}
                {s.url}
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
    a.sessionKey === b.sessionKey &&
    a.turnIndex === b.turnIndex &&
    a.copyIdx === b.copyIdx &&
    a.execOutputs === b.execOutputs &&
    a.inlineExecOutput === b.inlineExecOutput &&
    a.isLast === b.isLast &&
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
    a.onDownloadPaper === b.onDownloadPaper
  );
}

/** localStorage key for per-message 👍/👎 feedback (session-scoped entries). */
const MSG_FEEDBACK_KEY = 'miqi:msg-feedback';

/** Strip <think>...</think> reasoning blocks before rendering.
 *  Handles both complete blocks and cross-message orphans
 *  (tags split across streaming chunks). */
