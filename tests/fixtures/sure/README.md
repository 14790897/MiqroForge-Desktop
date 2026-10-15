# SURE 报告 fixtures(阶段 3)

本目录是 `sure check --format json` 的**真实采集输出**(SURE 0.1.2,
Windows,采集于 2026-10-10),直接供 `tests/runtime/test_sure_report.py`
解析测试使用。**不要手改内容**——它们同时是"如实保真"的锚。

| 文件 | 场景 | 特点 |
|---|---|---|
| `report-check-hello.json` | 最小项目(`sure-poc/hello`) | severity=`not_enough_checked`、0 findings、12 阶段记录齐全 |
| `report-check-findings.json` | SURE 对抗性 fixture(fake-payment) | 5 findings(Must fix / Can fix later、status=Cannot confirm、证据锚点)、5 not_checked |
| `report-check-cn-path.json` | 中文+空格路径项目(`sure-poc/演示 项目`) | 路径保真回归 |
| `report-repair-fake-payment.json` | 同上 fixture 的 `sure repair` 输出 | `details.repairs[5]` 修复契约(id/issue_id/problem/why_it_matters/required_fix/acceptance/recheck/**rechecks_that_must_pass**/forbidden_shortcuts) |
| `report-recheck-fake-payment.json` | 上两者之后的 `sure recheck`(期间清理过 payments.js) | **`details.lifecycle`**(结构化对比:`closed[]`/`still_open[]`);语义硬:删标记但不跑检查 = 仍 open |

> `report-check-hello/-cn` 中 `grants.settings_file` 的 `���Ӿ�` 是 SURE v0.1.2 自身的
> Windows 用户名 ANSI 读取怪癖(原样保真,勿改成"漂亮"版本)。

采集命令(任一平台,`$SURE` 指向已安装的 sure 可执行文件):

```bash
"$SURE" check "D:\Code\MiQi\sure-poc\hello" --format json > report-check-hello.json
"$SURE" check "D:\Code\Sure\fixtures\adversarial\fake-payment" --format json > report-check-findings.json
"$SURE" check "D:\Code\MiQi\sure-poc\演示 项目" --format json > report-check-cn-path.json
```

注意:`exit_code=1` / `outcome=not_green` 是**查了但不干净**的正常结果,
不是工具故障;报告 schema 版本为 `details.report.schema_version=3`。
