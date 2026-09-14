#!/usr/bin/env node
/**
 * i18n-scan.mjs — Hardcoded-Chinese (CJK) UI text audit for the desktop renderer & main process.
 *
 * Purpose: enumerate every user-visible hardcoded CJK string in apps/desktop/src so an
 * i18n migration can be scoped from data instead of guesswork. See the companion audit
 * report it writes into <src>/../out/i18n/ (summary.md, unique-strings.md, full.tsv).
 *
 * Scope & exclusions:
 *   - scans .ts / .tsx under --src (default: src)
 *   - excludes test files/dirs, .md/.css, comments
 *   - classifies: jsx-text | attr:<name> | toast | dialog | throw-error | log-internal |
 *     label-prop | literal-const | multiline-blob
 *   - "log-internal" (console/logger calls) is reported but NOT counted in the
 *     user-visible totals — developer-facing noise, not UI copy.
 *   - AI/markdown CONTENT produced at runtime is not in source; literals assigned as
 *     message/payload content (content:/payload:/body:/summary:/text: = "...") are
 *     flagged with risk "persisted-content" — they are quasi-data (persisted into
 *     session history), not plain UI chrome, and need a data-strategy decision.
 *
 * Outputs (UTF-8, generated under out/i18n/):
 *   summary.md         — counts by kind/module/risk + top files (for PR/issue quoting)
 *   unique-strings.md  — deduped copy inventory: text | n | risk | sample locations (translation source list)
 *   full.tsv           — every hit: file<TAB>line<TAB>kind<TAB>module<TAB>risk<TAB>text
 *
 * Usage:   node scripts/i18n-scan.mjs [--src src] [--out out/i18n]
 * Deps:    none (node >= 18)
 *
 * Caveats: context classification is heuristic (nearest preceding call marker within
 * 300 chars). sample-check the full.tsv before quoting precise kind-level numbers;
 * totals and dedupe counts are reliable to ~5-10%.
 */
import { readFileSync, readdirSync, writeFileSync, mkdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const args = process.argv.slice(2);
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const SRC_DIR = opt('--src', 'src');
const OUT_DIR = opt('--out', 'out/i18n');

const CJK = /[\u4e00-\u9fff\u3400-\u4dbf]/;
const RISK_ATTRS = new Set(['placeholder', 'title', 'aria-label', 'aria-description', 'alt', 'description', 'label', 'helperText', 'tooltip']);

// ---------------------------------------------------------------------------
// comment & string-literal aware scanner
// ---------------------------------------------------------------------------
function lineAt(src, pos) {
  let ln = 1;
  for (let i = 0; i < pos && i < src.length; i++) if (src[i] === '\n') ln++;
  return ln;
}

function* scanLiterals(src) {
  const n = src.length;
  let i = 0;
  while (i < n) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') { const j = src.indexOf('\n', i); i = j < 0 ? n : j + 1; continue; }
    if (c === '/' && src[i + 1] === '*') { const j = src.indexOf('*/', i + 2); i = j < 0 ? n : j + 2; continue; }
    if (c === '"' || c === "'" || c === '`') {
      const q = c;
      let j = i + 1;
      let depth = 0; // template ${} nesting
      let out = '';
      while (j < n) {
        const ch = src[j];
        if (ch === '\\') { out += src.slice(j, j + 2); j += 2; continue; }
        if (q === '`') {
          if (ch === '{' && src[j + 1] === '$') { depth++; out += ch; j++; continue; }
          if (ch === '}' && depth > 0) { depth--; out += ch; j++; continue; }
          if (ch === '`' && depth === 0) break;
        } else if (ch === q) break;
        out += ch; j++;
      }
      if (j < n) {
        yield { value: out, start: i, end: j };
        i = j + 1;
        continue;
      }
    }
    i++;
  }
}

// Heuristic: what call/construct is the nearest context *before* the literal?
const CONTEXT_RX = new RegExp(
  '(throw\\s+new\\s+[A-Za-z]+\\s*\\(|showMessageBox\\s*\\(|showErrorBox\\s*\\(|Menu\\.buildFromTemplate\\s*\\(\\s*\\[|' +
  '(?:toast|notify|enqueueToast|pushToast|showToast|toast\\.error|toast\\.success|toast\\.info|message\\.error|message\\.warning|setNotification)\\s*\\(' +
  '|(?:console|logger|log|this\\.options\\.log)\\.(?:log|info|warn|error|debug)\\s*\\(|label\\s*:\\s*)',
  'g');
function contextKind(src, start) {
  CONTEXT_RX.lastIndex = 0;
  // scan the last 300 chars before the literal for the nearest marker
  const win = src.slice(Math.max(0, start - 300), start);
  let best = null;
  let m;
  while ((m = CONTEXT_RX.exec(win)) !== null) best = { kind: m[1] ?? m[2] ?? m[3] ?? m[4] ?? m[5], idx: m.index };
  if (!best) return null;
  if (best.kind.startsWith('throw')) return 'throw-error';
  if (best.kind === 'showMessageBox' || best.kind === 'showErrorBox') return 'dialog';
  if (best.kind.startsWith('Menu')) return 'menu';
  if (best.kind.startsWith('label')) return 'label-prop';
  if (best.kind && (best.kind.includes('toast') || best.kind.includes('notify'))) return 'toast';
  if (best.kind && best.kind.includes('log')) return 'log-internal';
  return null;
}

// ---------------------------------------------------------------------------
// JSX text nodes: strip comments + all literals (placeholder keeps positions),
// then match CJK text sitting between '>' and '<'.
// ---------------------------------------------------------------------------
function blankLiterals(src) {
  const chars = src.split('');
  for (const lit of scanLiterals(src)) {
    for (let i = lit.start; i <= lit.end && i < chars.length; i++) if (chars[i] !== '\n') chars[i] = ' ';
  }
  // blanks on comment positions
  let out = '';
  let i = 0;
  const n = src.length;
  while (i < n) {
    const c = chars[i];
    if (c === '/' && chars[i + 1] === '/') { while (i < n && chars[i] !== '\n') chars[i++] = ' '; continue; }
    if (c === '/' && chars[i + 1] === '*') {
      let j = src.indexOf('*/', i + 2);
      j = j < 0 ? n : j + 2;
      while (i < j) { if (chars[i] !== '\n') chars[i] = ' '; i++; }
      continue;
    }
    i++;
  }
  return chars.join('');
}

function scanJsxText(src, blanked) {
  const hits = [];
  const rx = />([^<>]*(?:[\u4e00-\u9fff\u3400-\u4dbf])[^<>]*)</g;
  let m;
  while ((m = rx.exec(blanked)) !== null) {
    const text = m[1].trim();
    // drop code-template artifacts (unblanked unterminated template fragments);
    // real UI copy runs at most ~130 chars.
    if (!text || text.length > 300 || text.startsWith('=') || text.includes('={')) continue;
    // JSX text at this point contains no quotes/comments (blanked) — real text.
    hits.push({ text, start: m.index + 1 });
  }
  return hits;
}

// ---------------------------------------------------------------------------
// walk
// ---------------------------------------------------------------------------
const isTest = (p) => /(^|\/)(test|tests|spec|__tests__|e2e)(\/|$)/.test(p) || /\.(spec|test)\.[cm]?[jt]sx?$/.test(p) || p.includes('test-results');

function collect(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (['node_modules', 'out', 'dist', 'test-results', 'test-reports'].includes(name)) continue;
      out.push(...collect(p));
    } else if (/\.(ts|tsx)$/.test(name) && !isTest(p)) {
      out.push(p);
    }
  }
  return out;
}

const files = collect(SRC_DIR).sort();
const rows = []; // {file,line,kind,module,risk,text}

for (const file of files) {
  const src = readFileSync(file, 'utf8');
  const module_ = file.split(sep).slice(-2).join('/');
  const blanked = blankLiterals(src);

  // literals
  for (const lit of scanLiterals(src)) {
    if (!CJK.test(lit.value)) continue;
    const text = lit.value;
    const st = text.trim();
    if (!st || /^(https?:|www\.|data:|file:|chrome|moz-|<!--|#)/i.test(st)) continue;
    if (/^[0-9A-Za-z_\-./\\()\[\]{}%$#@!? ]+$/.test(st) && !CJK.test(st.replace(/[%{}]/g, ''))) continue;
    const interp = text.includes('${');
    const multiline = text.includes('\n');
    const line = lineAt(src, lit.start);
    // persisted-content signal: assignment token right before the literal
    const prev = src.slice(Math.max(0, lit.start - 40), lit.start + 1); // include opening quote
    const isContentAssign = /(?:content|payload|body|summary|text)\s*[:=]\s*["'`]*$/.test(prev);
    // JSX-attribute value (placeholder=, title=, aria-label= ...) — local check
    // wins over the wider call-context heuristic; prop objects use `name: '...'`
    // (the colon excludes them here).
    const am = /(?:^|[^\w:-])(placeholder|title|aria-label|aria-description|alt|description|helperText|label)\s*=\s*["'`]$/.exec(prev);
    let kind = am ? 'attr:' + am[1] : contextKind(src, lit.start);
    if (!kind) kind = multiline ? 'multiline-blob' : 'literal-const';
    const risks = [];
    if (interp) risks.push('interp');
    if (multiline) risks.push('multiline');
    if (text.length > 40) risks.push('long');
    if (isContentAssign && !multiline) risks.push('persisted-content');
    if (/[A-Za-z]{2,}/.test(text) && /[\u4e00-\u9fff]{2,}/.test(text)) risks.push('mixed');
    if (/\\n|%s|%d|\{\d?\}|<[a-z]+>/.test(text)) risks.push('format');
    rows.push({ file, line, kind, module: module_, risk: risks.join(','), text: text.replace(/\n/g, '⏎') });
  }

  // jsx text
  for (const h of scanJsxText(src, blanked)) {
    rows.push({
      file, line: lineAt(src, h.start), kind: 'jsx-text',
      module: module_, risk: h.text.includes('{') ? 'interp' : '',
      text: h.text.replace(/\s+/g, ' ').trim(),
    });
  }
}

// ---------------------------------------------------------------------------
// dedupe + classify
// ---------------------------------------------------------------------------
const UI_KINDS = new Set(['jsx-text', 'toast', 'dialog', 'menu', 'throw-error', 'label-prop', 'literal-const']);
const INTERNAL = rows.filter((r) => r.kind === 'log-internal' || (r.kind === 'multiline-blob' && !CJK.test(r.text.slice(0, 120))));
const uiRows = rows.filter((r) => !r.kind.startsWith('attr') ? (UI_KINDS.has(r.kind) && r.kind !== 'multiline-blob') : true);
const all = rows;

const occ = (arr) => arr.length;
const uniq = (arr) => new Set(arr.map((r) => r.text)).size;

const byKind = {};
for (const r of rows) byKind[r.kind] = (byKind[r.kind] || 0) + 1;
const byModule = {};
for (const r of rows) byModule[r.module] = (byModule[r.module] || 0) + 1;
const byRisk = {};
for (const r of rows) for (const k of r.risk.split(',')) if (k) byRisk[k] = (byRisk[k] || 0) + 1;

const byText = new Map();
for (const r of uiRows) {
  const e = byText.get(r.text) || [];
  e.push(r);
  byText.set(r.text, e);
}
const uniqRows = [...byText.entries()].sort((a, b) => b[1].length - a[1].length);

mkdirSync(OUT_DIR, { recursive: true });
const rel = (p) => relative(process.cwd(), p).replace(/\\/g, '/');
const esc = (t) => t.replace(/\t/g, ' ').replace(/[|\[\]]/g, '\\$&');

const pad = (n, w) => String(n).padStart(w);
const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0) + '%';

let md = `# i18n 摸底报告 — hardcoded CJK inventory\n\n`;
md += `> Generated ${new Date().toISOString().slice(0, 10)} by \`node scripts/i18n-scan.mjs\` — scope: \`${rel(join(process.cwd(), SRC_DIR))}\`\n\n`;
md += `## 总览\n\n`;
md += `| 指标 | 值 |\n|---|---|\n`;
md += `| 扫描文件数 | ${files.length} |\n`;
md += `| 命中总数（含日志/内部/多行块） | ${occ(rows)} |\n`;
md += `| 去重后文案数（含日志） | ${uniq(rows)} |\n`;
md += `| **候选 UI 文案 · 出现次数** | **${occ(uiRows)}** |\n`;
md += `| **候选 UI 文案 · 去重后** | **${uniq(uiRows)}** |\n`;
md += `| 内部/日志文本（不计 UI） | ${INTERNAL.length} |\n\n`;
md += `## 按类型\n\n| kind | 出现 | 去重 |\n|---|---|---|\n`;
const kinds = [...new Set(rows.map((r) => r.kind))].sort();
for (const k of kinds) {
  const arr = rows.filter((r) => r.kind === k);
  md += `| ${k} | ${arr.length} | ${uniq(arr)} |\n`;
}
md += `\n## 按模块\n\n| module | 出现 |\n|---|---|\n`;
for (const [k, v] of Object.entries(byModule).sort((a, b) => b[1] - a[1])) md += `| ${k} | ${v} |\n`;
md += `\n## 风险分布\n\n`;
for (const [k, v] of Object.entries(byRisk).sort((a, b) => b[1] - a[1])) md += `- ${k}: ${v}\n`;
md += `\n## 文件分布 Top 20\n\n| file | 候选 UI 出现 | 候选 UI 去重 |\n|---|---|---|\n`;
const byFile = {};
for (const r of uiRows) {
  byFile[rel(r.file)] = byFile[rel(r.file)] || { n: 0, u: new Set() };
  byFile[rel(r.file)].n++;
  byFile[rel(r.file)].u.add(r.text);
}
for (const [f, v] of Object.entries(byFile).sort((a, b) => b[1].n - a[1].n).slice(0, 20)) {
  md += `| ${f} | ${v.n} | ${v.u.size} |\n`;
}
md += `\n*行级明细见 full.tsv;去重文案清单见 unique-strings.md*\n`;
writeFileSync(join(OUT_DIR, 'summary.md'), md);

// unique list
let um = `# 去重文案清单（候选 UI）— ${uniqRows.length} 条\n\n`;
um += `> 用途:翻译/键命名来源。风险列含义:interp=含 \${} 插值 / format=含 %s/\\n 等 / long=>40 字符 / persisted-content=疑似客户端注入并落库的消息内容(chat 渲染层,不建议抽 key 翻译)/ mixed=中英混排。\n\n| # | 文案 | 次数 | 风险 | 示例位置 |\n|---|---|---|---|---|\n`;
uniqRows.slice(0, 4000).forEach(([text, arr], idx) => {
  const risks = [...new Set(arr.flatMap((r) => r.risk.split(',').filter(Boolean)))].join(' ');
  const ex = rel(arr[0].file) + ':' + arr[0].line;
  um += `| ${idx + 1} | ${esc(text.replace(/`/g, '').slice(0, 140))} | ${arr.length} | ${risks} | ${ex} |\n`;
});
writeFileSync(join(OUT_DIR, 'unique-strings.md'), um);

// full tsv
const tsv = ['file\tline\tkind\tmodule\trisk\ttext'];
for (const r of [...all].sort((a, b) => rel(a.file).localeCompare(rel(b.file)) || a.line - b.line)) {
  tsv.push(`${rel(r.file)}\t${r.line}\t${r.kind}\t${r.module}\t${r.risk}\t${r.text}`);
}
writeFileSync(join(OUT_DIR, 'full.tsv'), tsv.join('\n'));

// stdout summary (ASCII-ish for consoles)
console.log(`files scanned   : ${files.length}`);
console.log(`rows total      : ${occ(all)}   (uniq ${uniq(all)})`);
console.log(`log-internal    : ${INTERNAL.length}`);
console.log(`USER-VISIBLE    : ${occ(uiRows)} occurrences / ${uniq(uiRows)} distinct  <= UI scope`);
console.log(`  by kind:`);
for (const k of ['jsx-text', 'toast', 'dialog', 'menu', 'label-prop', 'throw-error', 'literal-const', 'multiline-blob']) {
  if (byKind[k]) console.log(`    ${pad(byKind[k], 5)}  ${k}`);
}
console.log(`  risks: ${Object.entries(byRisk).map(([k, v]) => k + '=' + v).join('  ')}`);
console.log(`reports written: ${rel(join(process.cwd(), OUT_DIR))}/summary.md, unique-strings.md, full.tsv`);
