# SURE 报告 fixtures(阶段 3)

本目录是 `sure check --format json` 的**真实采集输出**(SURE 0.1.2,
Windows,采集于 2026-10-10),直接供 `tests/runtime/test_sure_report.py`
解析测试使用。**不要手改内容**——它们同时是"如实保真"的锚。

| 文件 | 场景 | 特点 |
|---|---|---|
| `report-check-hello.json` | 最小项目(`sure-poc/hello`) | severity=`not_enough_checked`、0 findings、12 阶段记录齐全 |
| `report-check-findings.json` | SURE 对抗性 fixture(fake-payment) | 5 findings(Must fix / Can fix later、status=Cannot confirm、证据锚点)、5 not_checked |
| `report-check-cn-path.json` | 中文+空格路径项目(`sure-poc/演示 项目`) | 路径保真回归 |

采集命令(任一平台,`$SURE` 指向已安装的 sure 可执行文件):

```bash
"$SURE" check "D:\Code\MiQi\sure-poc\hello" --format json > report-check-hello.json
"$SURE" check "D:\Code\Sure\fixtures\adversarial\fake-payment" --format json > report-check-findings.json
"$SURE" check "D:\Code\MiQi\sure-poc\演示 项目" --format json > report-check-cn-path.json
```

注意:`exit_code=1` / `outcome=not_green` 是**查了但不干净**的正常结果,
不是工具故障;报告 schema 版本为 `details.report.schema_version=3`。
