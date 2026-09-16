import { describe, expect, it } from 'vitest';
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

// 这条链路（#1107）是：连通性探针与主 E2E 跑同一个 config，两者都会写 test-reports/results.json；
// 汇总脚本读的是主 E2E 那份 —— 主步被杀/OOM 时它必须不存在，摘要才会如实报「没找到报告」，
// 而不是把探针那份「只有 1 条用例」的结果当成本次运行的结论。
//
// 隔离靠的是「命令行 reporter 整组替换 config 的 reporter」，这一点在仓库里没有别的地方保证，
// 所以在这里对着**真实的 playwright.config.ts** 验证（--list 不执行用例，几秒内跑完）。
describe('探针与主 E2E 的报告隔离', () => {
  const cwd = process.cwd();
  const reportPath = join(cwd, 'test-reports', 'results.json');
  const playwrightCli = join(cwd, 'node_modules', '@playwright', 'test', 'cli.js');

  const runPlaywright = (args) =>
    spawnSync(process.execPath, [playwrightCli, 'test', '--config=playwright.config.ts', ...args], {
      cwd,
      encoding: 'utf8',
      env: { ...process.env, PLAYWRIGHT_SKIP_WEB_SERVER: '1' },
    });

  const reportState = () => (existsSync(reportPath) ? statSync(reportPath).mtimeMs : null);

  /** 跑会写报告的命令前后做快照/还原 —— 别动开发者本地那些产物。 */
  function preservingReports(run) {
    const htmlDir = join(cwd, 'test-reports', 'html');
    const hadReport = existsSync(reportPath);
    const backup = hadReport ? readFileSync(reportPath) : null;
    const hadHtml = existsSync(htmlDir);
    try {
      return run();
    } finally {
      if (hadReport) writeFileSync(reportPath, backup);
      else rmSync(reportPath, { force: true });
      if (!hadHtml) rmSync(htmlDir, { recursive: true, force: true });
    }
  }

  it('探针那一步（--reporter=list）不产出报告，也不动已有的那份', () => {
    const before = reportState();

    const result = runPlaywright([
      '--project=electron',
      '--grep',
      'AI Connectivity',
      '--reporter=list',
      '--list',
    ]);

    expect(result.status).toBe(0);
    expect(result.stdout).toContain('AI Connectivity');
    expect(reportState()).toBe(before);
  }, 120_000);

  it('主 E2E 那一步（不覆盖 reporter）才会写这份报告', () => {
    preservingReports(() => {
      rmSync(reportPath, { force: true });

      const result = runPlaywright(['--project=electron', '--list']);

      expect(result.status).toBe(0);
      expect(existsSync(reportPath)).toBe(true);
    });
  }, 120_000);
});
