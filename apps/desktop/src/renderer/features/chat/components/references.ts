/**
 * #879 引用来源：解析正文 [n] 脚注 + 文末「参考文献」列表，并让 [n] 可点击。
 *
 * #671（PR #843）通过 prompt 让模型在技术方案类回答里输出 `[n]` 脚注 + 文末
 * 参考文献列表，格式为 `[1] 作者, 标题, 期刊/机构, 年份, https://doi.org/xxx`
 * （逗号分隔、URL 结尾）。本模块把这些参考文献解析成结构化对象，供前端
 * 「来源详情」卡片渲染，并提供一个 remark 插件把正文里的 `[n]` 变成可点击链接。
 *
 * 纯函数、无 React 依赖，便于独立单测（参考 compareData.ts 的 parseCompareJson）。
 */

/** 一条参考文献（脚注编号 n 对应列表第 n 条）。 */
export interface CitationReference {
  /** 脚注编号（1 起）。 */
  num: number;
  title?: string;
  authors?: string;
  journal?: string;
  year?: string;
  url?: string;
  doi?: string;
}

/** 从 doi.org / dx.doi.org / 裸 doi: 前缀 URL 提取 DOI（10.xxx/yyy）。 */
export function extractDoi(url: string): string | undefined {
  if (!url) return undefined;
  const strip = (s: string) => s.replace(/[.,;:!?)\]}<>'"\s]+$/, '');
  const viaDoiOrg = url.match(/doi\.org\/(10\.\S+)/i);
  if (viaDoiOrg) return strip(viaDoiOrg[1]);
  const bare = url.match(/^doi:\s*(10\.\S+)/i);
  if (bare) return strip(bare[1]);
  return undefined;
}

const TRAILING_URL_RE = /(https?:\/\/\S+)/i;

/**
 * 从 markdown 里解析文末「参考文献」列表。
 *
 * 只识别「行首 `[n]` + 内容 + 行尾 URL」的行；字段按逗号/分号切分做
 * best-effort 分配（年份靠 4 位数字识别，其余按位置：3 段 → 作者/标题/期刊，
 * 2 段 → 标题/期刊，1 段 → 标题），字段缺失则留空，UI 只显示存在的字段。
 * 兼容 #671 旧格式（标题, 期刊/机构, 年份）与新格式（作者, 标题, 期刊/机构, 年份）。
 */
export function parseReferenceList(markdown: string): CitationReference[] {
  if (!markdown) return [];
  const refs: CitationReference[] = [];
  for (const rawLine of markdown.split(/\r?\n/)) {
    const line = rawLine.trim();
    // 行首 [n] + 内容；排除 markdown 链接引用定义 `[1]: http://...`
    const m = line.match(/^\[(\d+)\]\s*(.+)$/);
    if (!m || m[2].startsWith(':')) continue;
    const num = Number(m[1]);
    const body = m[2];

    const urlMatch = body.match(TRAILING_URL_RE);
    if (!urlMatch) continue; // 没有 URL 的行不是参考文献条目
    const url = urlMatch[1].replace(/[.,;:!?)\]}<>'"\s]+$/, '');
    // URL 之前的文本（去掉末尾分隔符）
    const textPart = body
      .slice(0, urlMatch.index)
      .trim()
      .replace(/[,，;；\s]+$/, '');

    const ref: CitationReference = { num, url, doi: extractDoi(url) };

    // 字段：优先 ；/;（#671 新格式，避免作者名内逗号被误切），字段不足时回退逗号（旧格式）。
    let fields = textPart
      .split(/[；;]/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (fields.length < 3) {
      fields = textPart
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
    }

    // 先挑年份（可能是独立字段，也可能嵌在字段里，如「Journal 2023」）。
    const rest: string[] = [];
    for (const f of fields) {
      const ym = f.match(/^(.*?)((?:19|20)\d{2})(.*)$/);
      if (ym && !ref.year) {
        ref.year = ym[2];
        const tail = (ym[1] + ym[3]).trim();
        if (tail) rest.push(tail);
      } else {
        rest.push(f);
      }
    }

    // 位置分配（best-effort）：3+ 段 → 作者/标题/期刊；2 段 → 标题/期刊；1 段 → 标题。
    if (rest.length >= 3) {
      ref.authors = rest[0];
      ref.title = rest[1];
      ref.journal = rest[2];
    } else if (rest.length === 2) {
      ref.title = rest[0];
      ref.journal = rest[1];
    } else if (rest.length === 1) {
      ref.title = rest[0];
    }

    refs.push(ref);
  }
  return refs;
}

/**
 * remark 插件：把正文里命中 validNums 的 `[n]` 文本节点拆成
 * `text + link(#citation-N) + text`，配合 MarkdownContent 的 `a` 渲染器
 * 渲染成可点击脚注。只遍历 `text` 节点 → 天然跳过 fenced/inline code
 * （它们是 `code`/`inlineCode` 节点，不是 `text`）。
 */
export function remarkCitations(validNums: Set<number>): (tree: unknown) => void {
  const split = (value: string): unknown[] => {
    const out: unknown[] = [];
    const re = /\[(\d+)\]/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(value)) !== null) {
      const n = Number(m[1]);
      if (!validNums.has(n)) continue;
      if (m.index > last) out.push({ type: 'text', value: value.slice(last, m.index) });
      out.push({
        type: 'link',
        url: `#citation-${n}`,
        children: [{ type: 'text', value: `[${n}]` }],
      });
      last = m.index + m[0].length;
    }
    if (last < value.length) out.push({ type: 'text', value: value.slice(last) });
    return out.length ? out : [{ type: 'text', value }];
  };

  const walk = (node: any): void => {
    if (!node || typeof node !== 'object') return;
    if (Array.isArray(node.children)) {
      const next: unknown[] = [];
      for (const child of node.children) {
        if (child && child.type === 'text' && typeof child.value === 'string') {
          next.push(...split(child.value));
        } else {
          walk(child);
          next.push(child);
        }
      }
      node.children = next;
    }
  };

  return (tree) => walk(tree as any);
}
