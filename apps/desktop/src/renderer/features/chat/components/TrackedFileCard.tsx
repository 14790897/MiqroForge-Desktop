import { BookOpen, X, Pencil, FileText, Eye, GitCompare, Star, FolderOpen } from 'lucide-react';

export interface TrackedFile {
  path: string;
  name: string;
  op: 'read' | 'write' | 'edit' | 'delete';
  lastSeen: number;
  truncated?: boolean;
  /** 产出该文件的工具名（如 create_docx / graph_render / write_file），#879 ③ 追溯 */
  sourceTool?: string;
}

export const OFFICE_FILE_RE_LEGACY = /\.(docx|xlsx|pptx|ppt)$/i;

/** 文件产出工具名 → 中文标签（#879 ③ 来源工具追溯） */
const FILE_TOOL_LABELS: Record<string, string> = {
  write_file: '写入文件',
  edit_file: '编辑文件',
  delete_file: '删除文件',
  apply_patch: '应用补丁',
  create_docx: '创建 Word',
  create_xlsx: '创建 Excel',
  create_pptx: '创建 PPT',
  create_pdf: '创建 PDF',
  pdf_write: '写 PDF',
  docx_write: '写 Word',
  xlsx_write: '写 Excel',
  pptx_write: '写 PPT',
  edit_docx: '编辑 Word',
  append_xlsx: '追加 Excel',
  graph_render: '渲染图',
  paper_download: '下载论文',
  exec: '执行命令',
  skill_manage: '技能管理',
};

export function TrackedFileCard({
  file,
  isResult,
  onPreview,
  onDiff,
  onReveal,
}: {
  file: TrackedFile;
  /** issue #607: result assets get accent border/background + Star + 结果 badge. */
  isResult?: boolean;
  onPreview: () => void;
  onDiff?: () => void;
  /** issue #607: 定位 → reveal the file in the OS file manager (results only). */
  onReveal?: () => void;
}) {
  const opColor: Record<TrackedFile['op'], string> = {
    read: 'var(--info)',
    edit: 'var(--warning)',
    write: 'var(--accent)',
    delete: 'var(--danger)',
  };
  const OpIcon = file.op === 'read' ? BookOpen : file.op === 'delete' ? X : Pencil;
  const OP_LABELS: Record<TrackedFile['op'], string> = {
    read: '读取',
    write: '写入',
    edit: '编辑',
    delete: '删除',
  };
  const displayPath = file.path.replace(/\\/g, '/');
  const isOfficeFile = OFFICE_FILE_RE_LEGACY.test(file.path);

  return (
    <div
      className="rounded-lg p-2.5"
      style={{
        border: isResult
          ? '1px solid color-mix(in srgb, var(--accent) 55%, transparent)'
          : '1px solid var(--border-subtle)',
        background: isResult
          ? 'color-mix(in srgb, var(--accent) 5%, var(--surface))'
          : 'var(--surface)',
      }}
    >
      <div className="flex items-start gap-2 mb-1">
        <FileText size={14} className="shrink-0 mt-0.5" style={{ color: opColor[file.op] }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap mb-0.5">
            {isResult && (
              <Star
                size={12}
                fill="currentColor"
                className="shrink-0"
                style={{ color: 'var(--accent)' }}
              />
            )}
            <span
              className="text-size-2xs font-medium truncate"
              style={{ color: 'var(--text)' }}
              title={displayPath}
            >
              {file.name.length > 30 ? file.name.slice(0, 28) + '…' : file.name}
            </span>
            <span
              className="text-size-2xs px-1.5 py-0.5 rounded font-semibold shrink-0"
              data-testid={`file-op-${file.op}`}
              style={{
                background: `color-mix(in srgb, ${opColor[file.op]} 15%, transparent)`,
                color: opColor[file.op],
              }}
            >
              {OP_LABELS[file.op]}
            </span>
            {file.sourceTool && (
              <span
                className="text-size-2xs px-1.5 py-0.5 rounded font-semibold shrink-0"
                data-testid="file-source-tool"
                style={{ background: 'var(--surface-muted)', color: 'var(--text-faint)' }}
              >
                {FILE_TOOL_LABELS[file.sourceTool] || file.sourceTool}
              </span>
            )}
            {isResult && (
              <span
                className="text-size-2xs px-1.5 py-0.5 rounded font-semibold shrink-0"
                data-testid="file-result-badge"
                style={{
                  background: 'color-mix(in srgb, var(--accent) 15%, transparent)',
                  color: 'var(--accent)',
                }}
              >
                结果
              </span>
            )}
            {isOfficeFile && (
              <span
                className="text-size-2xs px-1.5 py-0.5 rounded font-semibold shrink-0"
                data-testid="file-office-badge"
                style={{ background: 'var(--surface-muted)', color: 'var(--text-faint)' }}
              >
                文档
              </span>
            )}
          </div>
        </div>
      </div>
      {file.truncated ? (
        <div
          className="w-full flex items-center justify-center gap-1 py-1 rounded-md text-size-2xs"
          style={{
            border: '1px solid var(--border-subtle)',
            color: 'var(--text-faint)',
            background: 'var(--surface-muted)',
          }}
          title="路径在进度消息中被截断"
        >
          <span className="text-size-2xs">路径不完整</span>
        </div>
      ) : (
        <div className="flex gap-1.5">
          {onReveal && isResult && (
            <button
              onClick={onReveal}
              className="flex-1 flex items-center justify-center gap-1 py-1 rounded-md text-size-2xs transition-colors"
              style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
              title="在文件管理器中定位"
              data-testid="file-reveal-btn"
            >
              <FolderOpen size={10} />
              定位
            </button>
          )}
          {onDiff && (file.op === 'write' || file.op === 'edit') && (
            <button
              onClick={onDiff}
              disabled={isOfficeFile}
              className="flex-1 flex items-center justify-center gap-1 py-1 rounded-md text-size-2xs transition-colors"
              style={{
                border: '1px solid var(--border)',
                color: isOfficeFile ? 'var(--text-faint)' : 'var(--warning)',
                opacity: isOfficeFile ? 0.55 : 1,
              }}
              title="二进制 Office 文件不支持差异对比"
            >
              <GitCompare size={10} />
              差异
            </button>
          )}
          <button
            onClick={onPreview}
            className="flex-1 flex items-center justify-center gap-1 py-1 rounded-md text-size-2xs transition-colors"
            style={{ border: '1px solid var(--border)', color: 'var(--text-muted)' }}
            title="预览文件"
            data-testid="file-preview-btn"
          >
            <Eye size={10} />
            预览
          </button>
        </div>
      )}
    </div>
  );
}
