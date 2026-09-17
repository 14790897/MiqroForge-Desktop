#!/usr/bin/env node
/**
 * 汇总 Playwright JSON 报告里「重试后才通过」的用例（#1107）。
 *
 * electron-e2e / macos-e2e 都带 retries=2：用例首次失败、重试通过时 Playwright 仍把 job 判成
 * success，只在十几分钟、几十万字符的日志中段留一行 `N flaky` —— PR 页面上看不到，也不会有人
 * 去 grep。issue-877-rich-preview、qraft-login-entry 这些用例就这样一直飘着（#1107）。
 *
 * 本脚本读 playwright.config.ts 里 json reporter 产出的 test-reports/results.json，
 * 把 flaky 清单写进 GitHub Step Summary 并发 warning 注解，让每次 flaky 都留下可检索的
 * 痕迹（含首次失败的那次尝试的报错首行）。**只报告，不改变 job 结论** —— 成功/失败仍由
 * Playwright 的退出码决定，脚本自身也永远不会以非 0 退出。
 *
 * 报告可能缺失、也可能只是「跑了一半」的：json reporter 只在 onEnd 落盘，而 Actions
 * 在 timeout / cancel 时先发 SIGINT，Playwright 的 SigIntWatcher 会收下它、把运行标成
 * interrupted 后照常 onEnd —— 于是落盘的是一份部分报告（没跑到的用例 results 为空、
 * 在跑的用例 status 是 interrupted）。两种情况都会在摘要里明说，不会拿残缺数据报一个
 * 好看的 `flaky 0`。CI 里连通性探针那一步带 `--reporter=list`（命令行 reporter 会整组替换
 * config 的 reporter），所以它不产出 JSON 报告，这份报告只可能来自主 E2E 那次运行。
 *
 * 用法：node scripts/e2e-flaky-report.mjs [报告路径]   （默认 test-reports/results.json）
 */
import { appendFileSync, readFileSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

const DEFAULT_REPORT = 'test-reports/results.json';
const MAX_ERROR_CHARS = 200;
const HEADING = '### E2E flaky 检查（#1107）';

/** GitHub 注解的转义规则：`%`、CR、LF 先转，属性值里还有 `:` 和 `,`。 */
function escapeData(text) {
  return text.replace(/%/g, '%25').replace(/\r/g, '%0D').replace(/\n/g, '%0A');
}

function escapeProperty(text) {
  return escapeData(text).replace(/:/g, '%3A').replace(/,/g, '%2C');
}

/**
 * 报告字段是 PR 可控的：一个换行就能撑破 markdown 围栏，让摘要里出现作者想写的文字
 * （伪造结论、钓鱼链接）。凡是往 markdown 里拼的报告字段都先折成一行。
 */
function oneLine(text) {
  return String(text ?? '').replace(/[\r\n]+/g, ' ');
}

/**
 * 行内代码：文本里本来就有反引号时用更长的围栏，免得把摘要渲染坏。
 *
 * 值本身以反引号开头或结尾时，还要在围栏内侧补一个空格 —— 否则围栏会和内容的首/尾反引号
 * 并成一串，GitHub 实测会把这种输出渲染成纯文本、或吃掉边界的反引号。补空格后 CommonMark
 * 会把这对外侧空格去掉，内容照旧。
 */
function inlineCode(text) {
  const value = oneLine(text);
  const runs = value.match(/`+/g) || [];
  const fence = '`'.repeat(Math.max(1, ...runs.map((run) => run.length + 1)));
  const content = /^`|`$/.test(value) ? ` ${value} ` : value;
  return `${fence}${content}${fence}`;
}

/** Playwright 给每个 worker 硬注入 FORCE_COLOR=1，报告里的报错文本因此带 ANSI 颜色码。 */
function stripAnsi(text) {
  // eslint-disable-next-line no-control-regex
  return String(text).replace(/\[[0-9;]*m/g, '');
}

/** 报错的 message 是多行的（Locator / Call log 之类），只取首行做归因线索。 */
function firstLine(message) {
  const line = stripAnsi(message ?? '')
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  if (!line) return '';
  return line.length > MAX_ERROR_CHARS ? `${line.slice(0, MAX_ERROR_CHARS)}…` : line;
}

/** 用例标签：projectName 为空（config 没写 projects 时的隐式默认）就省掉前缀，别输出 `[]`。 */
function label(entry) {
  return entry.project ? `[${oneLine(entry.project)}] ` : '';
}

function formatDuration(ms) {
  if (!Number.isFinite(ms)) return '?';
  const seconds = Math.round(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m${String(rest).padStart(2, '0')}s` : `${seconds}s`;
}

/**
 * 注解里的 file 要相对仓库根，才能挂到 PR 的文件视图上；spec.file 是相对 config.rootDir 的，
 * 所以基准取报告里的 rootDir，而不是 cwd。
 *
 * 报告可能来自另一台 runner：macOS 的报告会被 ubuntu 上的汇总 job 解析，报告的 rootDir 是
 * `/Users/runner/...` 而本地 workspace 是 `/home/runner/...`，relative() 这时只会给出跨机器的
 * 回溯路径。那就改用仓库名把绝对路径切回仓库根 —— GitHub Actions 的工作区一定是
 * `<...>/<owner>/<repo>/<repo>/...`，仓库名取 `GITHUB_REPOSITORY` 的后半段。
 *
 * 都算不出来时返回原始的 spec.file —— 那不是一个有效的仓库路径，只是为了始终有东西可读。
 */
function repoRelative(file, rootDir) {
  const workspace = process.env.GITHUB_WORKSPACE;
  if (!workspace || !file) return file;
  const absolute = resolve(rootDir, file).split(sep).join('/');

  if (!isAbsolute(file)) {
    const rel = relative(workspace, resolve(rootDir, file)).split(sep).join('/');
    // 两个路径不同盘符时 relative() 会退回绝对路径，这种也挂不上，走下面的兜底。
    if (!rel.startsWith('..') && !isAbsolute(rel)) return rel;
  }

  const repo = (process.env.GITHUB_REPOSITORY || '').split('/')[1];
  if (repo) {
    const at = absolute.lastIndexOf(`/${repo}/`);
    if (at >= 0) {
      const tail = absolute.slice(at + repo.length + 2);
      if (tail && !tail.startsWith('..')) return tail;
    }
  }
  return file;
}

/**
 * 展平 json 报告的 suite 树。顶层的 suite 是「文件」级（title 是文件名），其 title 不进
 * 用例标题 —— 路径信息由 spec.file 表达，这样拼出来的路径与 Playwright 自己的
 * `[project] › file:line:col › describe › title` 一致。
 */
export function collectTests(report) {
  const out = [];
  const walk = (suites, depth, titles) => {
    for (const suite of suites || []) {
      const suiteTitles = depth === 0 || !suite.title ? titles : [...titles, suite.title];
      for (const spec of suite.specs || []) {
        for (const test of spec.tests || []) out.push({ spec, test, titles: suiteTitles });
      }
      walk(suite.suites, depth + 1, suiteTitles);
    }
  };
  walk(report?.suites, 0, []);
  return out;
}

/** flaky = 至少失败过一次、最终仍然通过（与 Playwright 的 test.outcome() === 'flaky' 同义）。 */
function toEntry({ spec, test, titles }) {
  return {
    project: test.projectName || '',
    file: spec.file || '',
    line: spec.line || 0,
    column: spec.column || 0,
    title: [...titles, spec.title].join(' › '),
    // 只留真正失败的尝试（failed / timedOut / interrupted）：它们的报错才是归因线索，
    // passed 与 skipped 都不是。
    attempts: (test.results || [])
      .filter((result) => result.status !== 'passed' && result.status !== 'skipped')
      .map((result) => ({
        retry: result.retry,
        status: result.status,
        error: firstLine(result.error?.message),
      })),
  };
}

function flakyEntries(tests) {
  return tests.filter(({ test }) => test.status === 'flaky').map(toEntry);
}

export function collectFlaky(report) {
  return flakyEntries(collectTests(report));
}

/**
 * 报告是不是「跑了一半」：被 SIGINT 打断的用例 status 是 interrupted；没轮到的用例 results
 * 为空（json reporter 只在 onEnd 落盘，而 Actions 在 timeout / cancel 时先发 SIGINT ——
 * Playwright 收下后把运行标成 interrupted 并照常 onEnd，于是落盘的是一份部分报告）。
 * 这种报告不能当成「一切正常」来读。
 *
 * 「results 为空」不能单独当作没跑完的证据：合法跳过的用例（test.skip / test.fixme，
 * expectedStatus 是 skipped）本来就一条 result 都没有。只有当用例预期会运行（status 与
 * expectedStatus 都不是 skipped）却一条 result 都没有时，才算「没跑到」。
 *
 * 另外两种「一条都没跑起来」的形态也要认：报告里一条用例都没收集到（testMatch / project
 * 改名、文件加载失败），以及 reporter 自己攒的 run 级 errors —— 这两种 stats 全是 0、看起来
 * 最像「一切正常」。
 */
function incompleteness(report, tests) {
  let neverRan = 0;
  let interrupted = 0;
  for (const { test } of tests) {
    const results = test.results || [];
    const shouldHaveRun = test.status !== 'skipped' && test.expectedStatus !== 'skipped';
    if (shouldHaveRun && !results.length) neverRan++;
    else if (results.some((result) => result.status === 'interrupted')) interrupted++;
  }
  return {
    neverRan,
    interrupted,
    noTests: tests.length === 0,
    runErrors: (report?.errors || []).length,
  };
}

function countLine(report, tests, flakyCount) {
  const stats = report?.stats || {};
  const projects = [...new Set(tests.map(({ test }) => oneLine(test.projectName)).filter(Boolean))];
  const parts = [];
  if (projects.length) parts.push(`项目 ${projects.join(' / ')}`);
  parts.push(
    `flaky **${flakyCount}**`,
    `通过 ${stats.expected ?? '?'}`,
    `失败 ${stats.unexpected ?? '?'}`,
    `跳过 ${stats.skipped ?? '?'}`,
    `用时 ${formatDuration(stats.duration)}`
  );
  return parts.join(' · ');
}

export function buildMarkdown(report) {
  const tests = collectTests(report);
  const flaky = flakyEntries(tests);
  const { neverRan, interrupted, noTests, runErrors } = incompleteness(report, tests);
  const incomplete = neverRan || interrupted || noTests || runErrors;
  const rootDir = report?.config?.rootDir || process.cwd();
  const lines = [HEADING, '', countLine(report, tests, flaky.length), ''];

  if (incomplete) {
    const detail = [
      noTests ? '一条用例都没有被收集到' : '',
      neverRan ? `${neverRan} 条用例没有任何结果` : '',
      interrupted ? `${interrupted} 条被中断` : '',
      runErrors ? `reporter 还报了 ${runErrors} 条 run 级错误` : '',
    ]
      .filter(Boolean)
      .join('、');
    lines.push(`⚠️ 这次运行没有跑完（${detail}）—— 下面的数字和清单都不完整，以重跑为准。`, '');
  }

  if (!flaky.length) {
    lines.push(
      incomplete ? '已经跑完的用例里没有「重试才通过」的。' : '本次运行没有被重试掩盖的用例。'
    );
    return lines.join('\n');
  }

  lines.push('以下用例首次失败、重试才通过 —— job 仍是 success，但需要归因：', '');
  for (const entry of flaky) {
    // 路径按仓库根来写（与注解里的 file= 一致），方便直接拿去检索。
    const where = `${repoRelative(entry.file, rootDir)}:${entry.line}:${entry.column}`;
    lines.push(`- ${inlineCode(`${label(entry)}${where} › ${entry.title}`)}`);
    for (const attempt of entry.attempts) {
      const suffix = attempt.error ? `：${inlineCode(attempt.error)}` : '';
      lines.push(`  - 第 ${attempt.retry + 1} 次尝试 ${attempt.status}${suffix}`);
    }
  }
  return lines.join('\n');
}

export function buildAnnotations(report) {
  const rootDir = report?.config?.rootDir || process.cwd();
  return collectFlaky(report).map((entry) => {
    const failures = entry.attempts
      .map(
        (attempt) =>
          `第 ${attempt.retry + 1} 次 ${attempt.status}${attempt.error ? `: ${attempt.error}` : ''}`
      )
      .join('；');
    const props = [
      `file=${escapeProperty(repoRelative(entry.file, rootDir))}`,
      `line=${entry.line}`,
    ];
    if (entry.column) props.push(`col=${entry.column}`);
    props.push(`title=${escapeProperty('Flaky E2E test')}`);
    const message = `${label(entry)}${entry.title} — ${failures}（#1107）`;
    return `::warning ${props.join(',')}::${escapeData(message)}`;
  });
}

/** Step Summary 由 Actions 注入到 GITHUB_STEP_SUMMARY（本地直接跑没有，就只打日志）。 */
export function emit(markdown, annotations) {
  const summaryPath = process.env.GITHUB_STEP_SUMMARY;
  if (summaryPath) {
    // 写不进摘要只影响可读性：这一步（以及 job）的成败不该由它决定。
    try {
      appendFileSync(summaryPath, `${markdown}\n`);
    } catch (error) {
      console.error(`写入 step summary 失败：${error.message}`);
    }
  }
  console.log(markdown);
  for (const annotation of annotations) console.log(annotation);
}

/** 读报告并生成 { markdown, annotations }。读不到/读不懂都返回可读的说明，不抛。 */
export function summarizeReport(reportPath) {
  let report;
  try {
    report = JSON.parse(readFileSync(reportPath, 'utf8'));
  } catch (error) {
    // 报告不存在 = 测试没跑到产出报告（安装/构建/前置步骤就失败了），不是这一步的问题。
    const reason =
      error.code === 'ENOENT'
        ? '没有找到 Playwright JSON 报告，说明测试没有运行到产出报告这一步'
        : `读取报告失败：${error.message}`;
    return { markdown: `${HEADING}\n\n${reason}（${reportPath}）。`, annotations: [] };
  }
  return { markdown: buildMarkdown(report), annotations: buildAnnotations(report) };
}

function main() {
  const { markdown, annotations } = summarizeReport(resolve(process.argv[2] || DEFAULT_REPORT));
  emit(markdown, annotations);
}

// 被单测 import 时不要执行。
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main();
  } catch (error) {
    // 汇总步骤只是让 flaky 可见，不能因为自己的 bug 把一个绿色的 job 判红。
    emit(`${HEADING}\n\n汇总步骤自身出错：${inlineCode(error.message)}`, []);
  }
}
