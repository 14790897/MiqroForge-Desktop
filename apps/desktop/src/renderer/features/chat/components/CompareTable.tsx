import { useMemo, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import type { CompareCitation, CompareData, CompareParameter, SortDir } from './compareData';
import { isRangeValue, sortParameters } from './compareData';

/** 单元格文本超过该长度时折叠，点击「展开」显示完整内容。 */
const LONG_CELL = 24;

interface Props {
  data: CompareData;
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

export function CompareTable({ data }: Props) {
  const { schemes, parameters } = data;
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const citations = useMemo(() => {
    const m = new Map<string, CompareCitation>();
    for (const c of data.citations ?? []) m.set(c.id, c);
    return m;
  }, [data.citations]);

  const rows = useMemo(
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

  const toggleExpand = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const isLong = (v: string | undefined) => (v ?? '').length > LONG_CELL;
  const rowHasLong = (p: CompareParameter) =>
    isLong(p.name) || (p.values ?? []).some((v) => isLong(v));

  const cellBackground = (r: number, c: number, isRange: boolean) => {
    if (hover && (hover.row === r || hover.col === c)) return 'var(--surface-hover)';
    if (isRange) return 'var(--accent-soft)';
    return 'transparent';
  };

  const renderValue = (p: CompareParameter, c: number, isExpanded: boolean) => {
    const raw = p.values?.[c] ?? '';
    if (!isExpanded && isLong(raw)) {
      return (
        <span title={raw}>
          {raw.slice(0, LONG_CELL)}
          {'…'}
        </span>
      );
    }
    return raw || ' ';
  };

  return (
    <div
      className="my-2 rounded-[10px] overflow-hidden"
      style={{ border: '1px solid var(--table-border)' }}
    >
      {data.title && (
        <div
          className="px-3 py-2 text-xs font-semibold"
          style={{ borderBottom: '1px solid var(--border-subtle)' }}
        >
          {data.title}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="text-xs w-full border-collapse">
          <thead>
            <tr>
              <th
                className="px-3 py-2 text-left font-semibold whitespace-nowrap"
                style={{ background: 'var(--table-head-bg)' }}
              >
                参数
              </th>
              {schemes.map((scheme, i) => (
                <th
                  key={i}
                  className="px-3 py-2 text-left font-semibold whitespace-nowrap"
                  style={{ background: 'var(--table-head-bg)' }}
                >
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
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p, r) => {
              const isExpanded = expanded.has(p.name);
              const hasLong = rowHasLong(p);
              return (
                <tr key={`${p.name}-${r}`}>
                  <td
                    className="px-3 py-2 align-top whitespace-pre-wrap break-words"
                    style={{
                      borderTop: r === 0 ? undefined : '1px solid var(--border-subtle)',
                      background: cellBackground(r, 0, false),
                      minWidth: 96,
                    }}
                    onMouseEnter={() => setHover({ row: r, col: 0 })}
                    onMouseLeave={() => setHover(null)}
                  >
                    <div className="font-medium">{p.name}</div>
                    <div className="mt-0.5 flex flex-col gap-0.5">
                      {p.unit && <span className="text-[10px] text-text-faint">{p.unit}</span>}
                      <SourceBadge source={p.source} citations={citations} />
                    </div>
                    {hasLong && (
                      <button
                        type="button"
                        onClick={() => toggleExpand(p.name)}
                        className="mt-1 text-[10px] underline"
                        style={{ color: 'var(--text-faint)' }}
                      >
                        {isExpanded ? '收起' : '展开'}
                      </button>
                    )}
                  </td>
                  {schemes.map((_, c) => {
                    const raw = p.values?.[c] ?? '';
                    const isRange = isRangeValue(raw) || !!p.range;
                    return (
                      <td
                        key={c}
                        className="px-3 py-2 align-top whitespace-pre-wrap break-words"
                        style={{
                          borderTop: r === 0 ? undefined : '1px solid var(--border-subtle)',
                          borderLeft: '1px solid var(--border-subtle)',
                          background: cellBackground(r, c + 1, isRange),
                          minWidth: 72,
                        }}
                        onMouseEnter={() => setHover({ row: r, col: c + 1 })}
                        onMouseLeave={() => setHover(null)}
                      >
                        {renderValue(p, c, isExpanded)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {parameters.length === 0 && (
        <p className="px-3 py-2 text-xs text-text-faint">（无对比数据）</p>
      )}
      <div
        className="px-3 py-1.5 text-[10px]"
        style={{ borderTop: '1px solid var(--border-subtle)', color: 'var(--text-faint)' }}
      >
        浅色底纹表示参数区间/范围；点击列头可排序。
      </div>
    </div>
  );
}
