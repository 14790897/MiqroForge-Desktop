"""Event-loop lag watchdog (#1203).

A blocked bridge event loop is invisible from the outside: every request —
including ``chat.abort`` — stops being answered at the same moment, and the
only evidence left behind is the size of the gap between two log lines.  This
task turns that gap into an explicit measurement.

It measures the drift of a periodic ``asyncio.sleep``.  A sleep asked to wait
``interval`` seconds that returns after ``interval + drift`` means the loop was
unable to run this task for ``drift`` seconds — it was blocked, either by a
synchronous call made on the loop thread or by a handler that never yields.

The threshold is deliberately low (1 s).  A healthy bridge does not block its
loop for that long, so every line this emits is a real stall worth explaining:
``Event loop blocked for 220.3s`` is the fault from #1203, not noise.
"""

from __future__ import annotations

import asyncio

from loguru import logger

#: How often to re-arm the probe.  Small enough that the measurement lands
#: close to the stall, large enough to be free on an idle bridge.
DEFAULT_INTERVAL_S = 0.5

#: Stalls shorter than this are not worth a line.
DEFAULT_THRESHOLD_S = 1.0


async def watch_loop_lag(
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    threshold_s: float = DEFAULT_THRESHOLD_S,
) -> None:
    """Log every stall of the running event loop longer than ``threshold_s``.

    Runs forever; cancel the task to stop it.  Never raises on a stall — the
    measurement *is* the product.
    """
    loop = asyncio.get_running_loop()
    while True:
        started = loop.time()
        await asyncio.sleep(interval_s)
        elapsed = loop.time() - started
        drift = elapsed - interval_s
        if drift >= threshold_s:
            logger.warning(
                "Event loop blocked for {:.1f}s: a {:.1f}s sleep took {:.1f}s, "
                "so no bridge request was answered in that window",
                drift,
                interval_s,
                elapsed,
            )
