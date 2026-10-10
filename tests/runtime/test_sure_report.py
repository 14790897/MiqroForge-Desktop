"""#阶段3:SURE 报告解析与类型绑定(绑定 schemas/report.schema.json v3)。

用三份**真实采集**的 `sure check --format json` 输出做 fixture
(采集命令见 tests/fixtures/sure/README.md;含中文+空格路径与 findings 场景)。
原则:如实保真——severity/status 是自由字符串(CLI 以展示形态输出,如
"Must fix"),不做过严枚举;未知字段原样保留(extra=allow);
schema_version 不兼容时明确报错,绝不猜测渲染。
"""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sure"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_hello_report_basics():
    from miqi.runtime.sure_report import parse_check_output

    env = parse_check_output(_read("report-check-hello.json"))
    assert env.command == "check"
    assert env.outcome == "not_green"
    assert env.exit_code == 1
    assert env.sure_version == "0.1.2"
    assert env.protocol_version >= 1
    rep = env.details.report
    assert rep.schema_version == 3
    assert rep.aggregate.severity == "not_enough_checked"
    assert rep.aggregate.is_green is False
    assert rep.aggregate.headline
    assert rep.totals.checked == 0 and rep.totals.open_findings == 0
    assert len(env.details.stages) == 12
    assert env.details.stages[0].number == 1
    assert env.details.stages[0].outcome in {"ran", "not_run", "not_part_of_work"}


def test_parse_findings_report_preserves_everything():
    from miqi.runtime.sure_report import parse_check_output

    env = parse_check_output(_read("report-check-findings.json"))
    rep = env.details.report
    assert rep.totals.open_findings == 5
    assert rep.totals.could_not_run == 5
    assert len(rep.findings) == 5
    first = rep.findings[0]
    # 展示形态字符串原样保真(如 "Must fix"/"Cannot confirm"),不做枚举改写
    assert first.severity == "Must fix"
    assert first.status == "Cannot confirm"
    assert first.title
    assert first.what and first.impact and first.next_action
    assert first.is_model_only is False
    assert first.evidence_anchors[0].location == "src/payments.js"
    assert first.evidence_anchors[0].subject == "line_range"
    assert len(rep.not_checked) == 5
    assert rep.not_checked[0].reason


def test_parse_preserves_unknown_fields_for_faithful_rendering():
    """extra=allow:grants/model_use/privacy/support 等未建模字段不得丢失。"""
    from miqi.runtime.sure_report import parse_check_output

    env = parse_check_output(_read("report-check-hello.json"))
    dumped = env.details.model_dump()
    assert dumped["grants"]["execution_mode"] == "inspect_only"
    assert "privacy" in dumped and "support" in dumped
    assert dumped["model_use"]["state"] == "no_provider"


def test_parse_chinese_and_space_path_roundtrip():
    from miqi.runtime.sure_report import parse_check_output

    env = parse_check_output(_read("report-check-cn-path.json"))
    assert "演示 项目" in env.details.project
    assert env.details.report.schema_version == 3


def test_parse_rejects_malformed_json():
    from miqi.runtime.sure_report import SureReportError, parse_check_output

    with pytest.raises(SureReportError):
        parse_check_output("not json at all")


def test_parse_rejects_missing_required_fields():
    from miqi.runtime.sure_report import SureReportError, parse_check_output

    with pytest.raises(SureReportError):
        parse_check_output(json.dumps({"command": "check", "details": {}}))


def test_parse_rejects_unsupported_schema_version():
    from miqi.runtime.sure_report import SureReportError, parse_check_output

    raw = json.loads(_read("report-check-hello.json"))
    raw["details"]["report"]["schema_version"] = 2
    with pytest.raises(SureReportError, match="schema"):
        parse_check_output(json.dumps(raw))


def test_parse_tolerates_trailing_newlines():
    from miqi.runtime.sure_report import parse_check_output

    env = parse_check_output(_read("report-check-hello.json") + "\n\n")
    assert env.command == "check"


def test_parse_sure_version_output():
    from miqi.runtime.sure_report import parse_sure_version

    assert parse_sure_version("sure 0.1.2\n") == "0.1.2"
    assert parse_sure_version("sure 0.12.0") == "0.12.0"
    assert parse_sure_version("garbage") is None
    assert parse_sure_version("") is None


# ── 阶段 4:repair 契约与 recheck 对比 ────────────────────────────────────


def test_parse_repair_report_contracts():
    from miqi.runtime.sure_report import parse_sure_output

    env = parse_sure_output(_read("report-repair-fake-payment.json"))
    assert env.command == "repair"
    assert env.details.purpose == "repair"
    repairs = env.details.repairs
    assert len(repairs) == 5
    first = repairs[0]
    assert first.id.startswith("rep_")
    assert first.issue_id.startswith("fnd_")
    assert first.problem and first.why_it_matters
    assert first.required_fix and first.acceptance
    # schema 之外的字段(Sure 实际输出)必须保留,不得丢
    assert first.rechecks_that_must_pass
    assert first.forbidden_shortcuts
    assert first.evidence and first.evidence[0].get("class") == "observed_fact"


def test_parse_recheck_lifecycle_comparison():
    from miqi.runtime.sure_report import parse_sure_output

    env = parse_sure_output(_read("report-recheck-fake-payment.json"))
    assert env.command == "recheck"
    lc = env.details.lifecycle
    assert lc is not None
    assert lc.closed == []
    assert len(lc.still_open) == 5
    first = lc.still_open[0]
    assert first.id.startswith("fnd_")
    assert first.status == "open"
    assert first.severity and first.title


def test_parse_check_lifecycle_is_none():
    from miqi.runtime.sure_report import parse_sure_output

    env = parse_sure_output(_read("report-check-hello.json"))
    assert env.command == "check"
    assert env.details.lifecycle is None
    assert env.details.repairs == []


def test_parse_check_output_remains_check_only():
    """向后兼容:parse_check_output 仍只接受 check(阶段 3 契约不变)。"""
    from miqi.runtime.sure_report import SureReportError, parse_check_output

    with pytest.raises(SureReportError):
        parse_check_output(_read("report-repair-fake-payment.json"))


def test_parse_sure_output_rejects_unknown_command():
    from miqi.runtime.sure_report import SureReportError, parse_sure_output

    raw = json.loads(_read("report-check-hello.json"))
    raw["command"] = "doctor"
    with pytest.raises(SureReportError):
        parse_sure_output(json.dumps(raw))
