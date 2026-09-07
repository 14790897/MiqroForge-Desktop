import { useMemo, useState } from 'react';
import { Check, Copy, ExternalLink } from 'lucide-react';
import type { CompareCitation, CompareData, CompareParameter, SortDir } from './compareData';
import { compareToTsv, isRangeValue, sortParameters } from './compareData';
import { DataTable, type DataTableCell } from './DataTable';

/** 单元格文本超过该长度时折叠，点击该格内「展开」显示完整内容。 */
const LONG_CELL = 20;

interface Props {
  data: CompareData;
  /** 原始 ```compare JSON 文本，用于「源码」视图核对。 */
  rawText?: string;
}

/** 来源徽标：命中 citations 显示标题，否则显示 id；缺失显示「未标注」。 */
function SourceBadge({
  source,
  citations,
}: {
  source: string | undefined;
  citations: Map<string, CompareCitation>;
}) {
  if (!source) {
    return <span className="text-[10px] text-text-faint">未标注</span>;
  }
  const cit = citations.get(source);
  const label = cit?.title ?? source;
  if (cit?.url) {
    return (
      <a
        href={cit.url}
        target="_blank"
        rel="noreferrer"
        title={cit.doi ? `DOI: ${cit.doi}` : cit.url}
        className="inline-flex items-center gap-0.5 max-w-[160px] truncate text-[10px] underline decoration-dotted"
        style={{ color: 'var(--accent)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <span className="truncate">{label}</span>
        <ExternalLink size={10} className="shrink-0" />
      </a>
    );
  }
  return (
    <span
      className="inline-block max-w-[160px] truncate text-[10px]"
      style={{ color: 'var(--accent)' }}
      title={label}
    >
      {label}
    </span>
  );
}

export function CompareTable({ data, rawText }: Props) {
  const { schemes, parameters } = data;
  const [mode, setMode] = useState<'table' | 'source'>('table');
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null);
  // 展开的是「单元格」而非「行」：key = `${row}-${col}`。
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);

  const citations = useMemo(() => {
    const m = new Map<string, CompareCitation>();
    for (const c of data.citations ?? []) m.set(c.id, c);
    return m;
  }, [data.citations]);

  const displayParams = useMemo(
    () => (sortCol === null ? parameters : sortParameters(parameters, sortCol, sortDir)),
    [parameters, sortCol, sortDir]
  );

  const handleSort = (col: number) => {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('asc');
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(compareToTsv(data));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const toggleExpand = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const isLong = (v: string | undefined) => (v ?? '').length > LONG_CELL;

  const cellBackground = (r: number, c: number, isRange: boolean) => {
    if (hover && (hover.row === r || hover.col === c)) return 'var(--surface-hover)';
    if (isRange) return 'var(--accent-soft)';
    return 'transparent';
  };

  // 长文本在单元格内部折叠：展开/收起按钮放在该格偏下处。
  const renderValue = (p: CompareParameter, c: number, r: number) => {
    const raw = p.values?.[c] ?? '';
    if (!isLong(raw)) return raw || ' ';
    const key = `${r}-${c}`;
    const isExpanded = expanded.has(key);
    return (
      <span className="flex flex-col">
        <span>{isExpanded ? raw : `${raw.slice(0, LONG_CELL)}…`}</span>
        <button
          type="button"
          onClick={() => toggleExpand(key)}
          className="self-start mt-1 text-[10px] underline"
          style={{ color: 'var(--text-faint)' }}
        >
          {isExpanded ? '收起' : '展开'}
        </button>
      </span>
    );
  };

  const headers: DataTableCell[] = [
    { content: '参数' },
    ...schemes.map((scheme, i) => ({
      content: (
        <button
          type="button"
          onClick={() => handleSort(i)}
          className="inline-flex items-center gap-1 hover:underline"
          title="点击按该列排序"
        >
          <span>{scheme}</span>
          <span className="text-[10px] font-normal opacity-70">
            {sortCol === i ? (sortDir === 'asc' ? '↑' : '↓') : '↕'}
          </span>
        </button>
      ),
    })),
  ];

  const rows: (DataTableCell | null)[][] = displayParams.map((p, r) => {
    const nameCell: DataTableCell = {
      content: (
        <>
          <div className="font-medium">{p.name}</div>
          <div className="mt-0.5 flex flex-col gap-0.5">
            {p.unit && <span className="text-[10px] text-text-faint">{p.unit}</span>}
            <SourceBadge source={p.source} citations={citations} />
          </div>
        </>
      ),
      minWidth: 100,
      maxWidth: 160,
      background: cellBackground(r, 0, false),
    };
    const valueCells: DataTableCell[] = schemes.map((_, c) => {
      const raw = p.values?.[c] ?? '';
      const isRange = isRangeValue(raw) || !!p.range;
      return {
        content: renderValue(p, c, r),
        minWidth: 96,
        maxWidth: 200,
        background: cellBackground(r, c + 1, isRange),
      };
    });
    return [nameCell, ...valueCells];
  });

  const toggleBtn = (active: boolean) =>
    `px-2 py-0.5 text-xs rounded ${
      active
        ? 'bg-[var(--accent)] text-white'
        : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)]'
    }`;

  return (
    <div
      className="my-2 rounded-[10px] overflow-hidden"
      style={{ border: '1px solid var(--table-border)' }}
    >
      <div
        className="flex items-center justify-between px-3 py-1.5 border-b"
        style={{ borderColor: 'var(--border-subtle)' }}
      >
        <span className="text-xs font-semibold">{data.title ?? '参数对比'}</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setMode('table')}
            className={toggleBtn(mode === 'table')}
          >
            表格
          </button>
          <button
            type="button"
            onClick={() => setMode('source')}
            className={toggleBtn(mode === 'source')}
          >
            源码
          </button>
          <button
            type="button"
            onClick={handleCopy}
            className="inline-flex items-center gap-1 text-[11px] rounded px-1.5 py-0.5 transition-colors hover:bg-[var(--surface-muted)]"
            style={{ color: copied ? 'var(--success)' : 'var(--text-muted)' }}
            title="复制为表格（TSV）"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
      </div>

      {mode === 'table' ? (
        <>
          <DataTable
            headers={headers}
            rows={rows}
            emptyText="（无对比数据）"
            fullWidth
            onCellEnter={(r, c) => setHover({ row: r, col: c })}
            onCellLeave={() => setHover(null)}
          />
          <div
            className="px-3 py-1.5 text-[10px]"
            style={{ borderTop: '1px solid var(--border-subtle)', color: 'var(--text-faint)' }}
          >
            浅色底纹表示参数区间/范围；点击列头可排序。
          </div>
        </>
      ) : (
        <pre className="max-h-[420px] overflow-auto p-3 text-xs font-mono leading-relaxed">
          {rawText ?? JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}
