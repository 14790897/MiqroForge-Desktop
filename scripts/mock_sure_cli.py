"""阶段 3 E2E:模拟 `sure` 原生 CLI 的假可执行体(**不依赖真实 SURE 安装**)。

验收页的原生 spawn 通道会以与本文件完全相同的接口调用真实 sure.exe;
E2E 通过 SURE_BIN 指向本脚本(经 .cmd 包装)来驱动全链路。

支持:
  mock_sure_cli.py check <项目绝对路径> --format json
      → 单行 JSON,内容为真实采集的报告 fixture;退出码默认 1(not_green,
        与真实一致——"查了但不干净"不是故障)
  mock_sure_cli.py check <项目绝对路径> --format human
      → 一行人话摘要
  mock_sure_cli.py --version
      → "sure 9.9.9-mock"

行为旋钮(环境变量):
  MOCK_SURE_DELAY_MS   输出前等待毫秒(默认 0;供"运行中/进度/取消"场景)
  MOCK_SURE_FIXTURE    fixture 文件名(默认 report-check-findings.json)
  MOCK_SURE_EXIT       退出码覆盖(默认 1)
  MOCK_SURE_STDERR     非空则先往 stderr 写一行(诊断路径用)

取消语义与真实一致:JSON 只在**运行结束时一次发射**,进程被终止则无输出。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "sure"

#: 各命令的默认 fixture(阶段 4:repair/recheck 复用真实采集产物)。
_DEFAULT_FIXTURES = {
    "check": "report-check-findings.json",
    "repair": "report-repair-fake-payment.json",
    "recheck": "report-recheck-fake-payment.json",
}


def _project_arg(argv: list[str]) -> str | None:
    for arg in argv[1:]:
        if not arg.startswith("-"):
            return arg
    return None


def main(argv: list[str]) -> int:
    # 与真 sure.exe(Rust)一致:输出**严格 UTF-8**,与调用方 locale 无关——
    # 白名单环境会剥掉 PYTHONUTF8,若走 locale(cp1252/GBK)中文会崩或碎 JSON。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--version" in argv:
        print("sure 9.9.9-mock")
        return 0
    if not argv or argv[0] not in _DEFAULT_FIXTURES:
        print(f"mock sure: unsupported arguments: {argv}", file=sys.stderr)
        return 2
    command = argv[0]

    delay_ms = int(os.environ.get("MOCK_SURE_DELAY_MS", "0") or "0")
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)

    stderr_line = os.environ.get("MOCK_SURE_STDERR", "")
    if stderr_line:
        print(stderr_line, file=sys.stderr)

    fmt = "human"
    if "--format" in argv:
        idx = argv.index("--format")
        if idx + 1 < len(argv):
            fmt = argv[idx + 1]

    fixture_name = os.environ.get("MOCK_SURE_FIXTURE") or _DEFAULT_FIXTURES[command]
    data = json.loads((FIXTURE_DIR / fixture_name).read_text(encoding="utf-8"))

    project = _project_arg(argv)
    if project:
        data["details"]["project"] = project

    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False))  # 单行 JSON,结束时一次发射
    else:
        rep = data["details"]["report"]
        print(
            f"SURE mock: {rep['aggregate']['severity']} — "
            f"{rep['aggregate']['headline']} "
            f"(findings={rep['totals']['open_findings']})"
        )
    return int(os.environ.get("MOCK_SURE_EXIT", "1") or "1")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
