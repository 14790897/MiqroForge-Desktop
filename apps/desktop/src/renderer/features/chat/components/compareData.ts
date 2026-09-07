/**
 * 结构化对比数据解析与纯函数（issue #878）。
 *
 * 约定模型在 ```compare 围栏代码块内输出 JSON，字段为：方案名(schemes)、
 * 参数名(name)、参数区间(range / 值内区间)、单位(unit)、来源(source)。
 * 解析失败返回 null，由调用方降级为普通代码块展示。
 */

export interface CompareCitation {
  id: string;
  title?: string;
  url?: string;
  doi?: string;
}

export interface CompareParameter {
  name: string;
  /** 每个方案（列）对应的取值，与 schemes 顺序对齐。 */
  values?: string[];
  unit?: string;
  /** 参数整体的期望区间（用于着色，即使某列值为点值）。 */
  range?: string;
  /** 来源标注 id，对应 citations 里的 id。 */
  source?: string;
}

export interface CompareData {
  title?: string;
  schemes: string[];
  parameters: CompareParameter[];
  citations?: CompareCitation[];
}

export type SortDir = 'asc' | 'desc';

/** 识别为 ```compare / ```compare-json 代码块的语言标识。 */
const COMPARE_LANGS = new Set(['compare', 'compare-json']);

export function isCompareLang(lang: string): boolean {
  return COMPARE_LANGS.has((lang ?? '').toLowerCase());
}

/**
 * 解析 ```compare 围栏块内的 JSON 文本为结构化数据。
 * 顶层解析失败或根类型错误返回 null；字段级缺失/多余字段容忍。
 */
export function parseCompareJson(text: string): CompareData | null {
  if (!text || !text.trim()) return null;

  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return null;
  const obj = raw as Record<string, unknown>;

  // 方案列：字符串数组，缺失时由 values 长度推导。
  let schemes: string[] = [];
  if (Array.isArray(obj.schemes)) {
    schemes = obj.schemes.filter((s): s is string => typeof s === 'string' && s.trim().length > 0);
  }

  // 参数行：parameters 必须是数组；单个无效项跳过。
  if (!Array.isArray(obj.parameters)) return null;
  const parameters: CompareParameter[] = [];
  for (const item of obj.parameters) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) continue;
    const p = item as Record<string, unknown>;
    if (typeof p.name !== 'string' || p.name.trim() === '') continue;

    const param: CompareParameter = { name: p.name.trim() };
    if (Array.isArray(p.values)) {
      param.values = p.values.map((v) => (typeof v === 'string' ? v : String(v ?? '')));
    }
    if (typeof p.unit === 'string' && p.unit.trim() !== '') param.unit = p.unit.trim();
    if (typeof p.range === 'string' && p.range.trim() !== '') param.range = p.range.trim();
    if (typeof p.source === 'string' && p.source.trim() !== '') param.source = p.source.trim();
    parameters.push(param);
  }

  // 未显式给出 schemes 时按最大 values 长度推导列名。
  if (schemes.length === 0) {
    const maxLen = parameters.reduce((m, p) => Math.max(m, p.values?.length ?? 0), 0);
    schemes = Array.from({ length: maxLen }, (_, i) => `方案${i + 1}`);
  }

  // 引用来源（#879 衔接）：仅保留带 id 的合法项。
  const citations: CompareCitation[] = [];
  if (Array.isArray(obj.citations)) {
    for (const item of obj.citations) {
      if (typeof item !== 'object' || item === null || Array.isArray(item)) continue;
      const c = item as Record<string, unknown>;
      if (typeof c.id !== 'string' || c.id.trim() === '') continue;
      const cit: CompareCitation = { id: c.id.trim() };
      if (typeof c.title === 'string' && c.title.trim() !== '') cit.title = c.title.trim();
      if (typeof c.url === 'string' && c.url.trim() !== '') cit.url = c.url.trim();
      if (typeof c.doi === 'string' && c.doi.trim() !== '') cit.doi = c.doi.trim();
      citations.push(cit);
    }
  }

  const result: CompareData = { schemes, parameters };
  if (typeof obj.title === 'string' && obj.title.trim() !== '') result.title = obj.title.trim();
  if (citations.length > 0) result.citations = citations;
  return result;
}

/**
 * 取一个值的「数值下界」用于排序：取字符串中第一个数字。
 * 区间「2–4」→ 2；单值「5.8」→ 5.8；无数字 → Infinity（排底部）。
 */
export function extractRangeLower(value: string | undefined): number {
  if (value == null) return Infinity;
  const m = value.trim().match(/-?\d+(?:\.\d+)?/);
  return m ? Number(m[0]) : Infinity;
}

/** 判断某个单元格值是否表示一个区间（如「2–4」「3~5」「-10—5」）。 */
export function isRangeValue(value: string): boolean {
  return /-?\d+(?:\.\d+)?\s*[-–~—]\s*-?\d+(?:\.\d+)?/.test(value.trim());
}

/**
 * 按某一方案列（columnIndex）对参数行排序，非变异、稳定。
 * 区间值按数值下界比较；缺失/无数字的值排到末尾。
 */
export function sortParameters(
  params: CompareParameter[],
  columnIndex: number,
  dir: SortDir
): CompareParameter[] {
  const sign = dir === 'asc' ? 1 : -1;
  return params
    .map((p, idx) => ({ p, idx }))
    .sort((a, b) => {
      const an = extractRangeLower(a.p.values?.[columnIndex]);
      const bn = extractRangeLower(b.p.values?.[columnIndex]);
      // 缺失/无数字的值始终排末尾，与排序方向无关。
      if (an === Infinity && bn === Infinity) return a.idx - b.idx;
      if (an === Infinity) return 1;
      if (bn === Infinity) return -1;
      if (an !== bn) return (an - bn) * sign;
      return a.idx - b.idx; // 相等时保持原始顺序
    })
    .map(({ p }) => p);
}
