"""事件循环 lag 看门狗（#1203）——循环冻结必须留下可量化的痕迹。"""

import asyncio
import re
import time

import pytest
from loguru import logger

from miqi.bridge.loop_watchdog import watch_loop_lag


def _capture_warnings():
    messages: list[str] = []
    sink_id = logger.add(lambda m: messages.append(m.record["message"]), level="WARNING")
    return messages, sink_id


@pytest.mark.asyncio
async def test_watchdog_reports_a_blocked_loop():
    """同步阻塞循环——这正是 #1203 的形状：请求集体停摆，且停摆时长可测。"""
    messages, sink_id = _capture_warnings()
    task = asyncio.create_task(watch_loop_lag(interval_s=0.05, threshold_s=0.1))
    try:
        await asyncio.sleep(0.15)  # 让探针先跑起来
        time.sleep(0.45)  # 在循环线程上同步阻塞（不 await，循环停摆）
        await asyncio.sleep(0.2)  # 让探针观察到这次漂移
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logger.remove(sink_id)

    assert messages, "循环被同步阻塞了，看门狗却一声不吭"
    drifts = [
        float(m.group(1))
        for m in (re.search(r"blocked for ([\d.]+)s", s) for s in messages)
        if m
    ]
    assert drifts, f"告警格式不对，取不到停摆时长: {messages}"
    assert max(drifts) >= 0.3, f"量到的停摆时长不合理: {max(drifts)} / {messages}"


@pytest.mark.asyncio
async def test_watchdog_stays_quiet_on_a_healthy_loop():
    """健康循环不该报警——阈值定得低不代表可以乱叫。"""
    messages, sink_id = _capture_warnings()
    task = asyncio.create_task(watch_loop_lag(interval_s=0.05, threshold_s=0.3))
    try:
        await asyncio.sleep(0.4)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        logger.remove(sink_id)

    assert messages == [], f"健康循环不该有告警: {messages}"
