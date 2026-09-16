import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import {
  buildAnnotations,
  buildMarkdown,
  collectFlaky,
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
    expect(buildMarkdown(makeReport())).not.toContain('没有跑完');
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

  it('注解里的报错换行按 GitHub workflow command 规则转义', () => {
    delete process.env.GITHUB_WORKSPACE;

    const [annotation] = buildAnnotations(makeReport());

    expect(annotation).toContain('::warning ');
    expect(annotation.match(/::warning /g)).toHaveLength(1);
    expect(annotation).not.toContain('\n');
    expect(annotation).toContain('第 1 次 failed');
  });
});
