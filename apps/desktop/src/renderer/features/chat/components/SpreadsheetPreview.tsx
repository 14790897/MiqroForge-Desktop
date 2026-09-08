import { useState } from 'react';
import type { CellMerge, StructuredSheet } from '../../../../shared/ipc';
import { DataTable, type DataTableCell } from './DataTable';

/**
 * 合并单元格布局计算（issue #877）：把 merges 转成每个单元格的
 * rowSpan/colSpan，被覆盖的单元格返回 null（渲染时跳过）。
 * 超出网格范围的 merge 会被裁剪。
 */
export function buildMergeLayout(
  rows: string[][],
  merges: CellMerge[] | undefined
): Array<Array<{ rowSpan: number; colSpan: number } | null>> {
  const rowCount = rows.length;
  const colCount = rows.reduce((m, r) => Math.max(m, r.length), 0);
  // 默认 {1,1} = 普通单元格；null = 被合并区覆盖，渲染时跳过。
  const layout: Array<Array<{ rowSpan: number; colSpan: number } | null>> = Array.from(
    { length: rowCount },
    () => Array.from({ length: colCount }, () => ({ rowSpan: 1, colSpan: 1 }))
  );
  for (const merge of merges ?? []) {
    const r1 = Math.max(0, Math.min(merge.start_row, rowCount - 1));
    const c1 = Math.max(0, Math.min(merge.start_col, colCount - 1));
    const r2 = Math.max(r1, Math.min(merge.end_row, rowCount - 1));
    const c2 = Math.max(c1, Math.min(merge.end_col, colCount - 1));
    const rowSpan = r2 - r1 + 1;
    const colSpan = c2 - c1 + 1;
    if (rowSpan <= 1 && colSpan <= 1) continue;
    layout[r1][c1] = { rowSpan, colSpan };
    for (let r = r1; r <= r2; r++) {
      for (let c = c1; c <= c2; c++) {
        if (r !== r1 || c !== c1) layout[r][c] = null; // covered
      }
    }
  }
  return layout;
}

interface Props {
  sheets: StructuredSheet[];
}

/** 只读表格预览（XLSX/CSV）：sheet 切换 + 合并单元格近似渲染。 */
export function SpreadsheetPreview({ sheets }: Props) {
  const [active, setActive] = useState(0);
  const sheet = sheets[Math.min(active, sheets.length - 1)];
  const rows = sheet?.rows ?? [];
  const layout = buildMergeLayout(rows, sheet?.merges);

  const tableRows: (DataTableCell | null)[][] = rows.map((row, r) =>
    Array.from({ length: layout[r]?.length ?? row.length }, (_, c) => {
      const span = layout[r]?.[c];
      if (span === null) return null; // covered by a merge
      const cell = row[c] ?? ''; // 短行缺列按空单元格渲染 (#889)
      const isHeader = r === 0;
      return {
        content: cell === '' ? ' ' : cell,
        rowSpan: span?.rowSpan ?? 1,
        colSpan: span?.colSpan ?? 1,
        background: isHeader ? 'var(--surface-muted)' : 'transparent',
        color: isHeader ? 'var(--text)' : 'var(--text-muted)',
        fontWeight: isHeader ? 600 : 400,
        title: cell,
      };
    })
  );

  return (
    <div className="flex flex-col" style={{ background: 'var(--surface)' }}>
      {sheets.length > 1 && (
        <div className="flex items-center gap-1 px-3 pt-2 pb-1 overflow-x-auto shrink-0">
          {sheets.map((s, i) => (
            <button
              key={s.name}
              type="button"
              onClick={() => setActive(i)}
              className={`px-3 py-1 rounded-t text-xs whitespace-nowrap transition-colors ${
                i === Math.min(active, sheets.length - 1)
                  ? 'bg-[var(--surface-elevated)] text-text font-semibold border border-b-0 border-[var(--border-subtle)]'
                  : 'text-text-muted hover:text-text'
              }`}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <DataTable
        rows={tableRows}
        emptyText="（空表）"
        maxHeight="62vh"
        wrapperClassName="overflow-auto p-2"
      />
    </div>
  );
}
