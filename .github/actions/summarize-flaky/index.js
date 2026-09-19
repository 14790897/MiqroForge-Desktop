// flaky 汇总的 JS action 入口（#1107）。
//
// 为什么是个 action 而不是 workflow 里的 shell 步骤：E2E job 里先跑过 PR 检出的代码，凡是
// 之后经 shell 执行的东西都要经过 PATH / 环境变量，而它们都能被那段代码污染（`gh`、`node`
// 都可能被换成假的）。JS action 由 runner 自己用工具缓存里的 node 绝对路径拉起、代码来自
// `uses: <repo>/.github/actions/summarize-flaky@<受信任 ref>` 拉下来的那份，不经过 shell，
// 因此这一步的代码与解释器都在 PR 代码够不到的地方；报告本身也只在本 job 内传递。
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const workspace = process.env.GITHUB_WORKSPACE || process.cwd();
const reportPath = path.resolve(workspace, process.env.INPUT_REPORT_PATH || '');

// 汇总脚本与被测代码同源：从**本 action 自己的目录**往上找到仓库根，避免再去 checkout 里取。
const scriptPath = path.join(__dirname, '..', '..', '..', 'apps', 'desktop', 'scripts', 'e2e-flaky-report.mjs');

import(pathToFileURL(scriptPath).href)
  .then(({ run }) => run(reportPath))
  .catch((error) => {
    // 汇总只作观察：它自己出问题不该把 job 判红（与脚本本身的设计一致）。
    console.error(`[summarize-flaky] ${error && error.message ? error.message : error}`);
    process.exitCode = 0;
  });
