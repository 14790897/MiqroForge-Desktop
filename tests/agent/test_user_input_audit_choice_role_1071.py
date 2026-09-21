# #1071 G7 P2 回归：确认卡审计行必须真的记到「用户点了哪一档」。
#
# 背景：resolve_user_input 的审计日志里 choice_role 只从 answers 里读，而
# choice_role 是 UserInputGate.resolve() **在本函数返回之后**才回填进 answers 的
# （见 miqi/kun_runtime/user_input_gate.py 的 resolve）。于是这行审计的 choice_role
# 恒为空串。这里用假 gate 把「解析结果回填晚于审计」这一时序固定下来，断言改为
# 按 choice_id 从卡片 choices 反查后能记到值。
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from miqi.agent import user_input_resolver as uir

_AUDIT_PREFIX = "audit confirm-card resolved:"


class _FakeGate:
    """只实现 resolve_user_input 用到的两个方法；resolve 不写 answers。"""

    def __init__(self, req: Any) -> None:
        self._req = req
        self.resolve_calls: list[tuple[str, dict[str, Any]]] = []

    def pending_request(self, input_id: str) -> Any:
        return self._req

    def resolve(
        self,
        input_id: str,
        answers: dict[str, Any],
        remember: bool = False,
        remember_mode: str = "session",
    ) -> bool:
        self.resolve_calls.append((input_id, answers))
        return True


def _install(monkeypatch, choices: list[dict[str, Any]]) -> _FakeGate:
    gate = _FakeGate(SimpleNamespace(choices=choices))
    monkeypatch.setattr(uir, "_gate", gate)
    return gate


def _audit_line(caplog) -> str:
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith(_AUDIT_PREFIX):
            return message
    raise AssertionError(f"没有审计行；实际日志：{[r.getMessage() for r in caplog.records]}")


def test_choice_role_derived_from_card_choices(monkeypatch, caplog) -> None:
    """修复前这一行是 choice_role=（恒空），用户点的那一档丢失。"""
    _install(
        monkeypatch,
        [
            {"id": "confirm", "label": "执行", "role": "confirm"},
            {"id": "cancel", "label": "取消", "role": "cancel"},
        ],
    )
    with caplog.at_level(logging.INFO, logger="miqi.agent.user_input_resolver"):
        assert uir.resolve_user_input("ui-1", {"choice_id": "cancel"}) is True

    line = _audit_line(caplog)
    assert "choice_id=cancel" in line
    assert "choice_role=cancel" in line


def test_choice_role_wins_over_answers_when_both_present(monkeypatch, caplog) -> None:
    """卡片反查优先：answers 里的陈旧值不能盖过用户实际点击的档位。"""
    _install(
        monkeypatch,
        [{"id": "cancel", "label": "取消", "role": "cancel"}],
    )
    with caplog.at_level(logging.INFO, logger="miqi.agent.user_input_resolver"):
        uir.resolve_user_input(
            "ui-2", {"choice_id": "cancel", "choice_role": "confirm"}
        )

    assert "choice_role=cancel" in _audit_line(caplog)


def test_choice_role_falls_back_to_answers(monkeypatch, caplog) -> None:
    """无档位卡 / 调用方自带 choice_role：answers 兜底仍然生效。"""
    _install(monkeypatch, [])
    with caplog.at_level(logging.INFO, logger="miqi.agent.user_input_resolver"):
        uir.resolve_user_input(
            "ui-3", {"choice_id": "legacy", "choice_role": "confirm"}
        )

    assert "choice_role=confirm" in _audit_line(caplog)


def test_choice_role_empty_when_nothing_known(monkeypatch, caplog) -> None:
    """两条来源都取不到才是空串（诚实记录，不编造档位）。"""
    _install(monkeypatch, [{"id": "confirm", "label": "执行"}])
    with caplog.at_level(logging.INFO, logger="miqi.agent.user_input_resolver"):
        uir.resolve_user_input("ui-4", {"choice_id": "confirm"})

    line = _audit_line(caplog)
    assert line.endswith("choice_role= remember=False"), line
