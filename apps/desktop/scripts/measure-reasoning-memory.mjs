#!/usr/bin/env node
/**
 * #1034 渲染进程内存压测：跑一遍固定口径的注入式测量，并把结果整理成可对比的报告。
 *
 * 口径（与 PR #1118 body 里的 BEFORE 基线完全一致，可直接对比）：
 *   200,000 条 reasoning 事件 @ ~200 msg/s，每条 1 个字符（'x'）；
 *   探针见 tests/e2e/issue-1034-renderer-oom-probe.spec.ts。
 *
 * 采集：renderer workingSetSize(KB)、JS heapUsed/heapTotal/heapLimit、DOM 节点数、
 * 落到 UI 的推理字符数、renderer-crash、注入量与 wall time。
 *
 * 用法（在 apps/desktop 下）：
 *   npm run build                                  # 探针跑的是构建产物
 *   node scripts/measure-reasoning-memory.mjs      # 跑一轮 ~17 分钟并出报告
 *
 * 常用参数：
 *   --target 200000   注入条数
 *   --rate 200        每秒注入条数
 *   --out <dir>       JSONL / 报告输出目录（默认 test-reports/issue1034）
 *   --label after     输出文件名里的标签（默认 after）
 *   --analyze <jsonl> 不跑测量，只解析已有的 JSONL
 *   --skip-build-check 跳过构建产物检查
 *
 * BEFORE 基线怎么复现：切到修复前的提交（如 PR 的分支切点）重新 `npm run build`
 * 后跑同一条命令，用 `--label before` 区分输出即可——探针与注入范式与基线一致。
 *
 * 判定口径：目标不是"完全不涨"，而是"修复后工作集不再随 reasoning 总量线性
 * 爆炸"。所以报告里同时给首/末采样、峰值与「每 1000 条事件的工作集增量」，
 * 并与基线对照打印。
 */

import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP_DIR = resolve(HERE, '..');
const SPEC = 'tests/e2e/issue-1034-renderer-oom-probe.spec.ts';

/** 探针每条事件 1 个字符，而 live 尾窗要到 8000 字（= 8000 条）才开始裁剪：
 *  跑不够长的轮次压根进不到"有界"区间，斜率对比没有意义，只用于自检管道。 */
const WINDOW_ENGAGES_AT = 8_000;

/** 基线（修复前，PR #1118 body「日志/验证证据」一节，同口径 200k×1 字符 @200/s）。 */
const BASELINE = {
  label: 'before (PR body)',
  wallMin: 18.5,
  wsFirstKb: 115_068,
  wsPeakKb: 302_080,
  wsSlopePer1k: 458.3, // KB / 1000 条事件（干净窗口斜率）
  heapUsedBytes: 11_200_000, // 全程恒定
  domNodes: 298, // 全程恒定
  crash: 'none',
};

function parseArgs(argv) {
  const opts = {
    target: 200_000,
    rate: 200,
    label: 'after',
    out: join(APP_DIR, 'test-reports', 'issue1034'),
    analyze: null,
    buildCheck: true,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--target') opts.target = Number(argv[++i]);
    else if (a === '--rate') opts.rate = Number(argv[++i]);
    else if (a === '--out') opts.out = resolve(argv[++i]);
    else if (a === '--label') opts.label = String(argv[++i]);
    else if (a === '--analyze') opts.analyze = resolve(argv[++i]);
    else if (a === '--skip-build-check') opts.buildCheck = false;
    else if (a === '-h' || a === '--help') opts.help = true;
    else throw new Error(`未知参数：${a}`);
  }
  return opts;
}

function usage() {
  console.log(
    [
      '用法：node scripts/measure-reasoning-memory.mjs [options]',
      '',
      '  --target <n>        注入条数（默认 200000）',
      '  --rate <n>          每秒注入条数（默认 200）',
      '  --out <dir>         输出目录（默认 apps/desktop/test-reports/issue1034）',
      '  --label <name>      报告标签（默认 after）',
      '  --analyze <jsonl>   只解析已有 JSONL，不跑测量',
      '  --skip-build-check  跳过构建产物检查',
      '',
      '先 npm run build —— 探针启动的是构建产物，不是源码。',
    ].join('\n')
  );
}

function checkBuild() {
  const missing = ['out/main/index.js', 'out/renderer/index.html'].filter(
    (p) => !existsSync(join(APP_DIR, p))
  );
  if (missing.length > 0) {
    console.error(
      `[measure] 缺少构建产物：${missing.join(', ')}\n` +
        `[measure] 先跑：cd apps/desktop && npm run build`
    );
    process.exit(2);
  }
}

/** 跑探针（Playwright electron project，单 worker）。 */
function runProbe(opts) {
  const args = ['playwright', 'test', SPEC, '--project=electron', '--workers=1', '--reporter=list'];
  console.log(`[measure] npx ${args.join(' ')}`);
  const startedAt = Date.now();
  const res = spawnSync(process.platform === 'win32' ? 'npx.cmd' : 'npx', args, {
    cwd: APP_DIR,
    stdio: 'inherit',
    env: {
      ...process.env,
      MIQI_1034_TARGET: String(opts.target),
      MIQI_1034_RATE: String(opts.rate),
      MIQI_1034_OUT: opts.out,
      // electron project 不需要 smoke 的静态服务器，别让它占 3458 端口。
      PLAYWRIGHT_SKIP_WEB_SERVER: '1',
    },
    shell: process.platform === 'win32',
  });
  return { status: res.status, startedAt };
}

/** 找出本轮产生的 JSONL（生成时间不早于 runStart）。 */
function findNewestJsonl(outDir, runStartMs) {
  if (!existsSync(outDir)) return null;
  const files = readdirSync(outDir)
    .filter((f) => f.startsWith('issue1034_probe_') && f.endsWith('.jsonl'))
    .map((f) => join(outDir, f))
    .map((p) => ({ p, m: statSync(p).mtimeMs }))
    .filter((x) => x.m >= runStartMs - 5_000)
    .sort((a, b) => b.m - a.m);
  return files.length > 0 ? files[0].p : null;
}

function readJsonl(path) {
  const records = [];
  for (const line of readFileSync(path, 'utf-8').split('\n')) {
    const t = line.trim();
    if (!t) continue;
    try {
      records.push(JSON.parse(t));
    } catch {
      /* 半行（进程被杀）——跳过 */
    }
  }
  return records;
}

function median(xs) {
  if (xs.length === 0) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 === 0 ? (s[mid - 1] + s[mid]) / 2 : s[mid];
}

function analyze(jsonlPath, label) {
  const records = readJsonl(jsonlPath);
  const start = records.find((r) => r.type === 'start') ?? {};
  const summary = records.find((r) => r.type === 'summary') ?? null;
  if (!summary) {
    return {
      ok: false,
      jsonlPath,
      reason: 'JSONL 里没有 summary 记录（测量未跑完 / 进程被杀）',
      snapCount: records.filter((r) => r.type === 'snap').length,
      start,
    };
  }

  const hist = (summary.uiHistory ?? []).filter((s) => s && s.wsKb >= 0);
  const first = hist[0];
  const last = hist[hist.length - 1];
  const peak = hist.reduce((m, s) => Math.max(m, s.wsKb), 0);
  const heapValues = [...new Set(hist.map((s) => s.heapUsed).filter((h) => h >= 0))];
  const domValues = hist.map((s) => s.domNodes).filter((d) => d >= 0);
  const xValues = hist.map((s) => s.xCount).filter((x) => x >= 0);
  const slopeOf = (from, to) =>
    from && to && to.sent > from.sent
      ? ((to.wsKb - from.wsKb) / (to.sent - from.sent)) * 1000
      : NaN;
  // 整轮斜率（含启动预热），与基线口径一致。
  const slope = slopeOf(first, last);
  // 干净窗口斜率：跳过前 10% 的预热/首屏分配，只看稳定段——基线里的
  // 「干净窗口斜率」就是这个口径。
  const cleanFrom = hist.find((s) => s.sent >= summary.target * 0.1) ?? first;
  const cleanSlope = slopeOf(cleanFrom, last);

  return {
    ok: true,
    label,
    jsonlPath,
    startedAtIso: start.startedAtIso ?? summary.startedAtIso,
    target: summary.target,
    sent: summary.sent,
    wallMs: summary.wallMs,
    ticks: summary.ticks,
    sendErrors: summary.sendErrors,
    sendMs: summary.sendMs,
    maxBurstMs: summary.maxBurstMs,
    crash: summary.gone ?? null,
    samples: hist.length,
    wsFirstKb: first?.wsKb ?? null,
    wsLastKb: last?.wsKb ?? null,
    wsPeakKb: peak,
    wsMedianKb: Math.round(median(hist.map((s) => s.wsKb))),
    wsSlopePer1k: Number.isFinite(slope) ? Number(slope.toFixed(2)) : null,
    wsSlopeCleanPer1k: Number.isFinite(cleanSlope) ? Number(cleanSlope.toFixed(2)) : null,
    wsCleanFromSent: cleanFrom?.sent ?? null,
    heapUsedBytes: heapValues.length === 1 ? heapValues[0] : heapValues,
    domNodes:
      domValues.length === 0
        ? null
        : {
            first: domValues[0],
            last: domValues[domValues.length - 1],
            max: Math.max(...domValues),
          },
    uiX: xValues.length === 0 ? null : { first: xValues[0], last: xValues[xValues.length - 1] },
    sentAtLastSample: last?.sent ?? null,
  };
}

function fmtBytes(n) {
  return typeof n === 'number' ? `${n} B (${(n / 1024 / 1024).toFixed(1)} MiB)` : String(n);
}

function report(r) {
  if (!r.ok) {
    console.error(`[measure] ✗ 无法解析：${r.reason}`);
    console.error(`[measure]   jsonl=${r.jsonlPath} snapshots=${r.snapCount}`);
    return;
  }
  const wallMin = (r.wallMs / 60000).toFixed(1);
  console.log('');
  console.log('════════ #1034 renderer memory measurement ════════');
  console.log(`label        : ${r.label}`);
  console.log(`jsonl        : ${r.jsonlPath}`);
  console.log(`injected     : ${r.sent}/${r.target}   ticks=${r.ticks} sendErrors=${r.sendErrors}`);
  console.log(
    `wall time    : ${wallMin} min (main-thread sendMs=${r.sendMs}, maxBurstMs=${r.maxBurstMs})`
  );
  console.log(`crash        : ${r.crash ? JSON.stringify(r.crash) : 'none'}`);
  console.log(`samples      : ${r.samples} (每 5s 一条，末尾含注入停止后 60s 的回落观察)`);
  console.log('─ renderer working set ─');
  console.log(
    `  first → last : ${r.wsFirstKb} KB → ${r.wsLastKb} KB（末采样时已注入 ${r.sentAtLastSample}）`
  );
  console.log(`  peak/median  : ${r.wsPeakKb} KB / ${r.wsMedianKb} KB`);
  console.log(`  slope        : ${r.wsSlopePer1k} KB / 1000 条事件（整轮，含预热）`);
  console.log(
    `  clean slope  : ${r.wsSlopeCleanPer1k} KB / 1000 条事件（跳过前 10%，从 sent=${r.wsCleanFromSent} 起）`
  );
  console.log('─ JS heap (renderer) ─');
  console.log(`  heapUsed     : ${fmtBytes(r.heapUsedBytes)}`);
  console.log('─ DOM / UI ─');
  console.log(`  domNodes     : ${JSON.stringify(r.domNodes)}`);
  console.log(`  UI 侧 'x' 数 : ${JSON.stringify(r.uiX)}`);
  console.log('─ 对照基线（修复前）──');
  console.log(
    `  before: ws ${BASELINE.wsFirstKb} KB → ${BASELINE.wsPeakKb} KB peak, ` +
      `slope ${BASELINE.wsSlopePer1k} KB/1000 条, heapUsed ${BASELINE.heapUsedBytes} B, ` +
      `domNodes ${BASELINE.domNodes}, wall ${BASELINE.wallMin} min`
  );
  const slopeForVerdict = r.wsSlopeCleanPer1k ?? r.wsSlopePer1k;
  if (slopeForVerdict !== null) {
    if (r.target <= WINDOW_ENGAGES_AT) {
      console.log(
        `  判定：⏭️ 本轮 target=${r.target} ≤ ${WINDOW_ENGAGES_AT}（尾窗要到这个量级才开始裁剪），` +
          `只够自检管道，不做斜率对比`
      );
    } else {
      const verdict =
        Math.abs(slopeForVerdict) < BASELINE.wsSlopePer1k / 5
          ? '✅ 工作集不再随 reasoning 总量线性增长'
          : '⚠️ 斜率仍与基线同量级——需要复核';
      console.log(
        `  判定：${verdict}（干净窗口 ${slopeForVerdict} vs 基线 ${BASELINE.wsSlopePer1k} KB/1000）`
      );
    }
  }
  console.log('═══════════════════════════════════════════════════');
  console.log('');
}

function writeReports(r, outDir) {
  if (!r.ok) return null;
  const stamp = (r.startedAtIso ?? new Date().toISOString()).replace(/[:.]/g, '-');
  const base = join(outDir, `issue1034_summary_${r.label}_${stamp}`);
  writeFileSync(`${base}.json`, JSON.stringify({ ...r, baseline: BASELINE }, null, 2));
  writeFileSync(
    `${base}.md`,
    [
      `# #1034 renderer memory — ${r.label}`,
      '',
      `| 指标 | ${r.label} | before (PR body) |`,
      '| --- | --- | --- |',
      `| 注入量 | ${r.sent}/${r.target} | 200000/200000 |`,
      `| wall time | ${(r.wallMs / 60000).toFixed(1)} min | ${BASELINE.wallMin} min |`,
      `| renderer-ws 首采样 | ${r.wsFirstKb} KB | ${BASELINE.wsFirstKb} KB |`,
      `| renderer-ws 峰值 | ${r.wsPeakKb} KB | ${BASELINE.wsPeakKb} KB |`,
      `| renderer-ws 末采样 | ${r.wsLastKb} KB | — |`,
      `| 斜率 (KB/1000 条，整轮) | ${r.wsSlopePer1k} | ${BASELINE.wsSlopePer1k} |`,
      `| 斜率 (KB/1000 条，干净窗口) | ${r.wsSlopeCleanPer1k} | ${BASELINE.wsSlopePer1k} |`,
      `| heapUsed | ${JSON.stringify(r.heapUsedBytes)} | ${BASELINE.heapUsedBytes} |`,
      `| domNodes | ${JSON.stringify(r.domNodes)} | ${BASELINE.domNodes} |`,
      `| crash | ${r.crash ? JSON.stringify(r.crash) : 'none'} | ${BASELINE.crash} |`,
      '',
      `原始 JSONL：\`${r.jsonlPath}\``,
      '',
    ].join('\n')
  );
  return base;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    usage();
    return;
  }

  let jsonlPath = opts.analyze;
  if (!jsonlPath) {
    if (opts.buildCheck) checkBuild();
    console.log(
      `[measure] 口径：${opts.target} 条 @ ${opts.rate} msg/s，1 char/event → ` +
        `预计 ${(opts.target / opts.rate / 60).toFixed(1)} 分钟`
    );
    const { startedAt } = runProbe(opts);
    jsonlPath = findNewestJsonl(opts.out, startedAt);
    if (!jsonlPath) {
      console.error(
        `[measure] 没找到新的 JSONL（目录 ${opts.out}）——探针可能没跑起来，见上面的 Playwright 输出`
      );
      process.exitCode = 1;
      return;
    }
  } else if (!existsSync(jsonlPath)) {
    console.error(`[measure] 找不到 ${jsonlPath}`);
    process.exitCode = 2;
    return;
  }

  const result = analyze(jsonlPath, opts.label);
  report(result);
  const base = writeReports(result, opts.out);
  if (base) console.log(`[measure] 报告已写入：${base}.json / ${base}.md`);
  if (!result.ok || result.crash) process.exitCode = 1;
}

main();
