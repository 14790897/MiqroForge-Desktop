import type { ReactNode } from 'react';

/**
 * 通用只读表格外壳（issue #877 / #878 共享）。
 *
 * XLSX/CSV 富预览（SpreadsheetPreview）与参数对比表（CompareTable）共用
 * 这一层表格渲染：边框、主题 token、单元格默认样式统一。各自的数据整形
 * （合并单元格 / 排序 / 区间着色 / 徽标）在调用方完成，通过 cell 字段覆盖。
 */
export interface DataTableCell {
  content: ReactNode;
  rowSpan?: number;
  colSpan?: number;
  /** 单元格背景覆盖（如区间着色、表头行）。 */
  background?: string;
  color?: string;
  fontWeight?: number;
  minWidth?: number;
  maxWidth?: number;
  /** 原生 title 提示（长文本折叠时展示全文）。 */
  title?: string;
}

interface DataTableProps {
  /** 表头单元格，渲染为 <thead><th>；省略则不渲染表头。 */
  headers?: DataTableCell[];
  /** 表体行；null 表示被合并区覆盖、跳过渲染。 */
  rows: (DataTableCell | null)[][];
  /** 无数据时的占位文案。 */
  emptyText?: string;
  /** 滚动容器最大高度（如 '62vh'）。 */
  maxHeight?: string;
  /** 滚动容器附加类名（默认 overflow-x-auto）。 */
  wrapperClassName?: string;
  /** 是否撑满宽度（默认按内容宽度收缩）。 */
  fullWidth?: boolean;
  /** 悬停单元格回调（用于行/列高亮）。 */
  onCellEnter?: (row: number, col: number) => void;
  onCellLeave?: () => void;
}

export function DataTable({
  headers,
  rows,
  emptyText,
  maxHeight,
  wrapperClassName,
  fullWidth,
  onCellEnter,
  onCellLeave,
}: DataTableProps) {
  return (
    <div
      className={wrapperClassName ?? 'overflow-x-auto'}
      style={maxHeight ? { maxHeight } : undefined}
    >
      <table
        className={fullWidth ? 'border-collapse text-xs w-full' : 'border-collapse text-xs'}
        style={{ border: '1px solid var(--border-subtle)' }}
      >
        {headers && headers.length > 0 && (
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th
                  key={i}
                  className="px-3 py-2 text-left font-semibold whitespace-nowrap"
                  style={{ background: 'var(--table-head-bg)' }}
                >
                  {h.content}
                </th>
              ))}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => {
                if (cell === null) return null; // 被合并区覆盖
                return (
                  <td
                    key={c}
                    rowSpan={cell.rowSpan ?? 1}
                    colSpan={cell.colSpan ?? 1}
                    title={cell.title}
                    className="px-3 py-2 align-top border whitespace-pre-wrap break-words"
                    style={{
                      borderColor: 'var(--border-subtle)',
                      background: cell.background ?? 'transparent',
                      color: cell.color,
                      fontWeight: cell.fontWeight,
                      maxWidth: cell.maxWidth ?? 320,
                      minWidth: cell.minWidth ?? 40,
                    }}
                    onMouseEnter={() => onCellEnter?.(r, c)}
                    onMouseLeave={() => onCellLeave?.()}
                  >
                    {cell.content}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && emptyText && (
        <p className="px-3 py-2 text-xs text-text-faint">{emptyText}</p>
      )}
    </div>
  );
}
