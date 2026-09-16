import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  buildAnnotations,
  buildMarkdown,
  collectFlaky,
  emit,
  summarizeReport,
} from '../scripts/e2e-flaky-report.mjs';

// fixture 的形状取自 playwright 1.62.1 真实产出的 test-reports/results.json
// （@playwright/test/lib/runner 的 JSONReporter：suite 树 + spec.file/line/column +
// 每个 test 的 status = outcome()、每种尝试一条 results[]）。
function makeReport(overrides = {}) {
  return {
    config: { rootDir: process.cwd(), projects: [{ name: 'electron', retries: 2 }] },
    suites: [
      {
        title: 'issue-877-rich-preview.spec.ts',
        file: 'tests/e2e/issue-877-rich-preview.spec.ts',
        line: 0,
        column: 0,
        specs: [],
        suites: [
          {
            title: 'issue #877 rich preview',
            file: 'tests/e2e/issue-877-rich-preview.spec.ts',
            line: 16,
            column: 3,
            specs: [
              {
                title: 'DOCX preview renders headings and table structure',
                ok: false,
                file: 'tests/e2e/issue-877-rich-preview.spec.ts',
                line: 121,
                column: 7,
                tests: [
                  {
                    status: 'flaky',
                    expectedStatus: 'passed',
                    projectName: 'electron',
                    results: [
                      {
                        retry: 0,
                        status: 'failed',
                        error: {
                          message:
                            'Error: expect(locator).toBeVisible() failed\n\n' +
                            'Locator: locator(\'[data-testid="file-preview-btn"]\').first()\n' +
                            'Timeout: 20000ms\nError: element(s) not found',
                        },
                      },
                      { retry: 1, status: 'passed', duration: 5321 },
                      { retry: 2, status: 'skipped' },
                    ],
                  },
                ],
              },
              {
                title: 'XLSX preview renders a spreadsheet table with sheet tabs',
                ok: true,
                file: 'tests/e2e/issue-877-rich-preview.spec.ts',
                line: 81,
                column: 7,
                tests: [
                  {
                    status: 'expected',
                    expectedStatus: 'passed',
                    projectName: 'electron',
                    results: [{ retry: 0, status: 'passed', duration: 4210 }],
                  },
                ],
              },
            ],
          },
        ],
      },
      {
        title: 'qraft-login-entry.spec.ts',
        file: 'tests/e2e/qraft-login-entry.spec.ts',
        line: 0,
        column: 0,
        specs: [
          {
            title: '未登录发送给出登录引导气泡',
            ok: false,
            file: 'tests/e2e/qraft-login-entry.spec.ts',
            line: 65,
            column: 7,
            tests: [
              {
                status: 'unexpected',
                expectedStatus: 'passed',
                projectName: 'electron',
                results: [
                  { retry: 0, status: 'failed', error: { message: 'Error: still failing' } },
                  { retry: 1, status: 'failed', error: { message: 'Error: still failing' } },
                ],
              },
            ],
          },
        ],
      },
    ],
    errors: [],
    stats: { duration: 1_056_000, expected: 1, skipped: 51, unexpected: 1, flaky: 1 },
    ...overrides,
  };
}

const originalWorkspace = process.env.GITHUB_WORKSPACE;

// 路径输出取决于 GITHUB_WORKSPACE（CI 里 Actions 一定会设它），所以默认清掉，
// 需要它的用例自己设置 —— 否则同一份断言在本地过、在 CI 挂。
beforeEach(() => {
  delete process.env.GITHUB_WORKSPACE;
});

afterEach(() => {
  if (originalWorkspace === undefined) delete process.env.GITHUB_WORKSPACE;
  else process.env.GITHUB_WORKSPACE = originalWorkspace;
});

describe('collectFlaky', () => {
  it('只挑出 flaky 用例，并拼出与 Playwright 一致的标题路径与位置', () => {
    const flaky = collectFlaky(makeReport());

    expect(flaky).toHaveLength(1);
    expect(flaky[0]).toMatchObject({
      project: 'electron',
      file: 'tests/e2e/issue-877-rich-preview.spec.ts',
      line: 121,
      column: 7,
      title: 'issue #877 rich preview › DOCX preview renders headings and table structure',
    });
  });

  it('只留真正失败的尝试，报错取首行（多行 Locator/Call log 不进摘要）', () => {
    const [entry] = collectFlaky(makeReport());

    expect(entry.attempts).toEqual([
      { retry: 0, status: 'failed', error: 'Error: expect(locator).toBeVisible() failed' },
    ]);
  });

  it('没有 flaky 时返回空数组', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].status = 'expected';
    expect(collectFlaky(report)).toEqual([]);
  });
});

describe('buildMarkdown', () => {
  it('列出 flaky 用例、计数与首次失败的报错首行', () => {
    const markdown = buildMarkdown(makeReport());

    expect(markdown).toContain('项目 electron');
    expect(markdown).toContain('flaky **1**');
    expect(markdown).toContain('通过 1');
    expect(markdown).toContain('跳过 51');
    expect(markdown).toContain('用时 17m36s');
    expect(markdown).toContain(
      '[electron] tests/e2e/issue-877-rich-preview.spec.ts:121:7 › issue #877 rich preview ' +
        '› DOCX preview renders headings and table structure'
    );
    expect(markdown).toContain('第 1 次尝试 failed：`Error: expect(locator).toBeVisible() failed`');
    // 失败到底的用例由 Playwright 自己报，这一步只负责被重试掩盖的那些。
    expect(markdown).not.toContain('qraft-login-entry');
  });

  it('无 flaky 时给出一句明确的「没有」', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].status = 'expected';
    const markdown = buildMarkdown(report);

    expect(markdown).toContain('flaky **0**');
    expect(markdown).toContain('本次运行没有被重试掩盖的用例。');
  });

  it('清单里的路径与注解一致，都相对仓库根', () => {
    process.env.GITHUB_WORKSPACE = resolve(process.cwd(), '..', '..');

    const markdown = buildMarkdown(makeReport());

    expect(markdown).toContain('apps/desktop/tests/e2e/issue-877-rich-preview.spec.ts:121:7');
  });

  // 超时/取消时 Actions 先发 SIGINT，Playwright 收下后照常落一份部分报告：
  // 没跑到的用例 results 为空、在跑的用例 status 是 interrupted。这种报告不能读成「一切正常」。
  it('跑了一半的报告会标明不完整', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].status = 'expected';
    report.suites[0].suites[0].specs[0].tests[0].results = []; // 没跑到
    report.suites[1].specs[0].tests[0].results = [{ retry: 0, status: 'interrupted' }];

    const markdown = buildMarkdown(report);

    expect(markdown).toContain('这次运行没有跑完');
    expect(markdown).toContain('1 条用例没有任何结果');
    expect(markdown).toContain('1 条被中断');
    expect(markdown).toContain('已经跑完的用例里没有「重试才通过」的。');
    expect(markdown).not.toContain('本次运行没有被重试掩盖的用例。');
  });

  it('跑完整了的报告不会误报「没跑完」', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].status = 'expected'; // 没有 flaky 的那条分支
    const markdown = buildMarkdown(report);

    expect(markdown).not.toContain('没有跑完');
    expect(markdown).toContain('本次运行没有被重试掩盖的用例。');
  });

  // Playwright 给每个 worker 硬注入 FORCE_COLOR=1，报告里的 result.error.message 因此带 ANSI
  // 颜色码（实测：`Error: [2mexpect([22m…`），不剥掉就会在摘要里显示成 `[2m` 噪声。
  it('剥掉报错里的 ANSI 颜色码', () => {
    const esc = String.fromCharCode(27); // ANSI 起始符
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].results[0].error.message =
      `Error: ${esc}[2mexpect(${esc}[22m${esc}[31mlocator${esc}[39m${esc}[2m).${esc}[22mtoBeVisible(` +
      `${esc}[2m)${esc}[22m failed`;

    const markdown = buildMarkdown(report);
    const [annotation] = buildAnnotations(report);

    expect(markdown).toContain('`Error: expect(locator).toBeVisible() failed`');
    expect(markdown).not.toContain(esc);
    expect(annotation).not.toContain(esc);
  });

  // GitHub 的 /markdown 实测：值以反引号开头/结尾时，围栏会和内容的首/尾反引号并成一串，
  // 输出要么被渲染成纯文本、要么吃掉边界的反引号；围栏内侧补一个空格即可。
  it('值以反引号开头或结尾时补空格，围栏不会和内容并成一串', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].results[0].error.message = '`foo` broke';

    const markdown = buildMarkdown(report);

    expect(markdown).toContain('`` `foo` broke ``');
  });

  it('projectName 为空时省掉项目前缀，不输出 []', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].projectName = '';

    const markdown = buildMarkdown(report);
    const [annotation] = buildAnnotations(report);

    expect(markdown).not.toContain('[]');
    expect(annotation).not.toContain('[]');
    expect(annotation).toContain('::warning file=');
  });

  it('一条用例都没收集到时标明没跑起来（stats 全 0 最容易读成一切正常）', () => {
    const markdown = buildMarkdown({
      ...makeReport(),
      suites: [],
      errors: [{ message: 'Error: No tests found' }],
      stats: { duration: 0, expected: 0, skipped: 0, unexpected: 0, flaky: 0 },
    });

    expect(markdown).toContain('这次运行没有跑完');
    expect(markdown).toContain('一条用例都没有被收集到');
    expect(markdown).toContain('run 级错误');
    expect(markdown).not.toContain('本次运行没有被重试掩盖的用例。');
  });

  it('只有 reporter 的 run 级错误时也要标明', () => {
    const report = makeReport();
    report.errors = [{ message: 'Error: browserType.launch: Executable does not exist' }];

    expect(buildMarkdown(report)).toContain('run 级错误');
  });
});

describe('summarizeReport', () => {
  it('报告不存在时明说没找到，不报一个好看的 flaky 0', () => {
    const { markdown, annotations } = summarizeReport(
      join(tmpdir(), 'miqi-1107-absent', 'results.json')
    );

    expect(markdown).toContain('没有找到 Playwright JSON 报告');
    expect(markdown).not.toContain('flaky **0**');
    expect(annotations).toEqual([]);
  });

  it('报告读不懂时明说读取失败，不报一个好看的 flaky 0', () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-1107-'));
    const file = join(dir, 'results.json');
    writeFileSync(file, '{"suites":');

    try {
      const { markdown } = summarizeReport(file);
      expect(markdown).toContain('读取报告失败');
      expect(markdown).not.toContain('flaky **0**');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe('buildAnnotations', () => {
  it('file 相对仓库根，才能挂到 PR 的文件视图上', () => {
    process.env.GITHUB_WORKSPACE = resolve(process.cwd(), '..', '..');

    const [annotation] = buildAnnotations(makeReport());

    expect(annotation).toContain('file=apps/desktop/tests/e2e/issue-877-rich-preview.spec.ts');
    expect(annotation).toContain('line=121,col=7');
  });

  it('基准是报告里的 config.rootDir，不是 cwd（spec.file 相对 rootDir）', () => {
    process.env.GITHUB_WORKSPACE = resolve(process.cwd(), '..', '..');
    const report = makeReport();
    // 真实报告里出现过 rootDir=tests/smoke、spec.file='../e2e/…' 的组合（那是 configDir）。
    report.config.rootDir = join(process.cwd(), 'tests', 'smoke');
    report.suites[0].file = '../e2e/issue-877-rich-preview.spec.ts';
    report.suites[0].suites[0].specs[0].file = '../e2e/issue-877-rich-preview.spec.ts';

    const [annotation] = buildAnnotations(report);

    expect(annotation).toContain('file=apps/desktop/tests/e2e/issue-877-rich-preview.spec.ts');
  });

  it('注解是一条、且不引入换行', () => {
    const [annotation] = buildAnnotations(makeReport());

    expect(annotation).toContain('::warning ');
    expect(annotation.match(/::warning /g)).toHaveLength(1);
    expect(annotation).not.toContain('\n');
    expect(annotation).toContain('第 1 次 failed');
  });

  // 换行在 firstLine 里已经去掉了，所以「不含换行」本身证明不了转义 —— 用 % 与 CR
  // （它们会原样活到转义那一步）以及带 : 和 , 的路径把转义真正钉住。
  it('注解按 GitHub workflow command 规则转义 %、CR 与属性里的 : ,', () => {
    const report = makeReport();
    report.suites[0].suites[0].specs[0].tests[0].results[0].error.message =
      'Error: 100% done\rmore text';
    report.suites[0].suites[0].specs[0].file = 'tests/e2e/a,b:c.spec.ts';

    const [annotation] = buildAnnotations(report);

    expect(annotation).toContain('file=tests/e2e/a%2Cb%3Ac.spec.ts');
    expect(annotation).toContain('100%25 done%0Dmore text');
  });
});

describe('summarizeReport（读到报告那条路径）', () => {
  it('返回清单 markdown 与非空注解', () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-1107-ok-'));
    const file = join(dir, 'results.json');
    writeFileSync(file, JSON.stringify(makeReport()));

    try {
      const { markdown, annotations } = summarizeReport(file);

      expect(markdown).toContain('flaky **1**');
      expect(annotations).toHaveLength(1);
      expect(annotations[0]).toContain('::warning ');
      expect(annotations[0]).toContain('file=tests/e2e/issue-877-rich-preview.spec.ts');
      expect(annotations[0]).toContain('line=121');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

/** 测试期间屏蔽 stdout/stderr，返回收集到的行。 */
function captureConsole(run: () => void) {
  const out: string[] = [];
  const errs: string[] = [];
  const originalLog = console.log;
  const originalError = console.error;
  console.log = (...args) => out.push(args.join(' '));
  console.error = (...args) => errs.push(args.join(' '));
  try {
    run();
  } finally {
    console.log = originalLog;
    console.error = originalError;
  }
  return { out, errs };
}

describe('emit', () => {
  it('把摘要写进 GITHUB_STEP_SUMMARY，同时把 markdown 与注解打到 stdout', () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-1107-emit-'));
    const summaryPath = join(dir, 'summary.md');
    process.env.GITHUB_STEP_SUMMARY = summaryPath;
    let written = '';

    const { out } = captureConsole(() => {
      emit('# 摘要', ['::warning file=a.ts,line=1::x']);
      written = readFileSync(summaryPath, 'utf8');
    });

    try {
      expect(written).toBe('# 摘要\n');
      expect(out.join('\n')).toContain('# 摘要');
      expect(out.join('\n')).toContain('::warning file=a.ts,line=1::x');
    } finally {
      delete process.env.GITHUB_STEP_SUMMARY;
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('摘要写不进去也不抛 —— 这一步不该把一个绿 job 判红', () => {
    process.env.GITHUB_STEP_SUMMARY = join(tmpdir(), 'miqi-1107-no-such-dir', 'summary.md');

    let thrown: unknown = null;
    const { errs } = captureConsole(() => {
      try {
        emit('# 摘要', []);
      } catch (error) {
        thrown = error;
      }
    });

    expect(thrown).toBeNull();
    expect(errs.join('\n')).toContain('写入 step summary 失败');
  });
});

describe('CLI 入口（CI 实际执行的那条路径）', () => {
  const scriptPath = fileURLToPath(new URL('../scripts/e2e-flaky-report.mjs', import.meta.url));

  const runCli = (cwd: string, summaryPath: string, args: string[] = []) =>
    spawnSync(process.execPath, [scriptPath, ...args], {
      cwd,
      encoding: 'utf8',
      // GITHUB_WORKSPACE 置空，让路径输出与本地一致、断言可确定。
      env: { ...process.env, GITHUB_WORKSPACE: '', GITHUB_STEP_SUMMARY: summaryPath },
    });

  it('无参调用读默认报告路径，写出摘要与注解，exit 0', () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-1107-cli-'));
    const summaryPath = join(dir, 'summary.md');
    mkdirSync(join(dir, 'test-reports'), { recursive: true });
    writeFileSync(join(dir, 'test-reports', 'results.json'), JSON.stringify(makeReport()));

    try {
      const result = runCli(dir, summaryPath);

      expect(result.status).toBe(0);
      expect(readFileSync(summaryPath, 'utf8')).toContain('E2E flaky 检查');
      expect(result.stdout).toContain('::warning file=tests/e2e/issue-877-rich-preview.spec.ts');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it('报告不存在时 exit 0，并在摘要里说明没找到', () => {
    const dir = mkdtempSync(join(tmpdir(), 'miqi-1107-cli-missing-'));
    const summaryPath = join(dir, 'summary.md');

    try {
      const result = runCli(dir, summaryPath);

      expect(result.status).toBe(0);
      expect(readFileSync(summaryPath, 'utf8')).toContain('没有找到 Playwright JSON 报告');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
