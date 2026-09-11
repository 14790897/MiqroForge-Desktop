"""OpenTelemetry trace + metric export from typed protocol events.

Architecture (Plan 59):
- ``build_telemetry_sink(config)`` is the single factory.  Returns an async
  ``handle(event)`` callable, or ``None`` when disabled or otel is absent.
- ``TelemetrySink`` maps event.type → spans/metrics:
  ``turn_started`` opens a span; ``tool_call_begin`` / ``tool_call_end``
  open/close child spans; ``turn_complete`` / ``turn_aborted`` end the turn
  span; ``error`` sets ERROR status and bumps the error counter.
- All opentelemetry imports are lazy — the module imports cleanly even
  without the optional ``otel`` extra installed.  Internal exceptions are
  swallowed so telemetry never breaks a turn.
"""

from __future__ import annotations

import logging
from typing import Any

from miqi.config.schema import ObservabilityConfig

_logger = logging.getLogger("miqi.telemetry")


# ── TelemetrySink ──────────────────────────────────────────────


class TelemetrySink:
    """Receives typed protocol events and translates them to OTel spans + metrics.

    Created only by ``build_telemetry_sink()``.  Each instance owns a
    ``turn_id → (span, context_token)`` map and a
    ``tool_call_id → span`` map so child tool spans are parented correctly.
    """

    def __init__(
        self,
        *,
        tracer: Any,
        meter: Any,
        instruments: dict[str, Any],
        capture_content: bool = False,
    ):
        self._tracer = tracer
        self._meter = meter
        self._capture_content = capture_content

        # turn_id → (Span, context token)
        self._spans: dict[str, tuple[Any, Any]] = {}
        # tool_call_id → Span
        self._tool_spans: dict[str, Any] = {}

        self._turn_counter = instruments["turn_counter"]
        self._error_counter = instruments["error_counter"]
        self._token_histogram = instruments["token_histogram"]
        self._tool_counter = instruments["tool_counter"]

    async def handle(self, event: Any) -> None:
        """Dispatch event → span/metric.  Exceptions are silently trapped."""
        try:
            event_type = getattr(event, "type", None)
            if event_type == "turn_started":
                self._on_turn_started(event)
            elif event_type == "turn_complete":
                self._on_turn_complete(event)
            elif event_type == "turn_aborted":
                self._on_turn_aborted(event)
            elif event_type == "tool_call_begin":
                self._on_tool_call_begin(event)
            elif event_type == "tool_call_end":
                self._on_tool_call_end(event)
            elif event_type == "error":
                self._on_error(event)
            # All other event types (agent_message_delta, etc.) are ignored.
        except Exception:
            # Telemetry is best-effort; never let it break a turn.
            pass

    # ── span lifecycle ─────────────────────────────────────────

    def _on_turn_started(self, event: Any) -> None:
        span = self._tracer.start_span(
            "miqi.turn",
            attributes={
                "turn_id": getattr(event, "turn_id", ""),
                "thread_id": getattr(event, "thread_id", ""),
                "agent_name": getattr(event, "agent_name", ""),
            },
        )
        # Attach context so child spans inherit the parent automatically.
        ctx = self._trace.set_span_in_context(span)
        token = self._context.attach(ctx)
        self._spans[getattr(event, "turn_id", "")] = (span, token)

    def _on_turn_complete(self, event: Any) -> None:
        entry = self._spans.pop(getattr(event, "turn_id", ""), None)
        if entry is None:
            return
        span, token = entry

        outcome = getattr(event, "outcome", "success")
        span.set_attribute("outcome", outcome)
        tools_used = getattr(event, "tools_used", None) or []
        span.set_attribute("tools_used_count", len(tools_used))

        token_usage = getattr(event, "token_usage", None) or {}
        input_tokens = int(token_usage.get("input", 0))
        output_tokens = int(token_usage.get("output", 0))
        total_tokens = input_tokens + output_tokens
        span.set_attribute("input_tokens", input_tokens)
        span.set_attribute("output_tokens", output_tokens)
        if total_tokens > 0:
            self._token_histogram.record(total_tokens, {"type": "total"})

        # Only set OK if the span isn't already in a terminal error state.
        # (error events end the span with ERROR and remove it from _spans,
        # so this is only reached when no prior error occurred.)
        if span.status.status_code != self._StatusCode.ERROR:
            span.set_status(self._Status(self._StatusCode.OK))
        span.end()
        self._context.detach(token)

        self._turn_counter.add(1, {"outcome": outcome})

    def _on_turn_aborted(self, event: Any) -> None:
        entry = self._spans.pop(getattr(event, "turn_id", ""), None)
        if entry is None:
            return
        span, token = entry

        span.set_attribute("reason", getattr(event, "reason", ""))
        span.set_status(self._Status(self._StatusCode.ERROR, "aborted"))
        span.end()
        self._context.detach(token)

    # ── tool child spans ───────────────────────────────────────

    def _on_tool_call_begin(self, event: Any) -> None:
        turn_id = getattr(event, "turn_id", "")
        parent_entry = self._spans.get(turn_id)
        if parent_entry is None:
            return
        parent_span = parent_entry[0]

        # Attach parent context so the new span is a child.
        ctx = self._trace.set_span_in_context(parent_span)
        child_token = self._context.attach(ctx)
        try:
            span = self._tracer.start_span(
                "miqi.tool_call",
                attributes={
                    "tool_call_id": getattr(event, "tool_call_id", ""),
                    "tool_name": getattr(event, "tool_name", ""),
                    "tool_display": getattr(event, "tool_display", ""),
                },
            )
            self._tool_spans[getattr(event, "tool_call_id", "")] = (span, child_token)
        finally:
            self._context.detach(child_token)

    def _on_tool_call_end(self, event: Any) -> None:
        entry = self._tool_spans.pop(getattr(event, "tool_call_id", ""), None)
        if entry is None:
            return
        span, child_token = entry

        span.set_attribute("tool_name", getattr(event, "tool_name", ""))
        span.set_attribute("success", getattr(event, "success", False))
        duration_ms = getattr(event, "duration_ms", 0)
        span.set_attribute("duration_ms", duration_ms)
        output_size = getattr(event, "output_size", 0)
        span.set_attribute("output_size", output_size)

        if self._capture_content:
            preview = getattr(event, "output_preview", "")
            if preview:
                span.set_attribute("output_preview", preview[:500])

        span.end()

        self._tool_counter.add(
            1,
            {
                "tool_name": getattr(event, "tool_name", ""),
                "success": str(getattr(event, "success", False)),
            },
        )

    # ── error ──────────────────────────────────────────────────

    def _on_error(self, event: Any) -> None:
        error_kind = getattr(event, "error_kind", None) or "none"
        self._error_counter.add(1, {"error_kind": error_kind})

        # If there's a live turn span, mark it ERROR and end it.
        # Use pop so a subsequent turn_complete cannot overwrite the terminal state.
        turn_id = getattr(event, "turn_id", "")
        entry = self._spans.pop(turn_id, None)
        if entry is not None:
            span, token = entry
            message = getattr(event, "message", "")
            span.set_status(self._Status(self._StatusCode.ERROR, message[:256] if message else ""))
            span.end()
            self._context.detach(token)

        # Clean up any orphaned child tool spans
        for tc_id, (tool_span, tool_token) in list(self._tool_spans.items()):
            tool_span.set_status(self._Status(self._StatusCode.ERROR, "parent turn errored"))
            tool_span.end()
            self._context.detach(tool_token)
        self._tool_spans.clear()


# ── Factory ────────────────────────────────────────────────────


def build_telemetry_sink(
    config: ObservabilityConfig,
    *,
    span_exporter: Any = None,
    span_processor: Any = None,
    meter: Any = None,
    metric_reader: Any = None,
):
    """Build a TelemetrySink from config, or return None if disabled.

    All opentelemetry imports happen lazily inside this function so the
    module is importable without the optional ``otel`` extra installed.

    Args:
        span_processor: Factory for span processor (default BatchSpanProcessor).
            Tests inject ``SimpleSpanProcessor`` for synchronous export.

    Returns the sink's async ``handle(event)`` callable, or ``None``.
    """
    if not config.enabled:
        return None

    try:
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        from opentelemetry.trace import Status, StatusCode
    except ImportError as e:
        _logger.warning("OpenTelemetry not available, telemetry disabled: %s", e)
        return None

    resource = Resource(attributes={"service.name": config.service_name})
    sampler = ParentBased(TraceIdRatioBased(config.sample_ratio))

    # ── span exporter ──
    if span_exporter is not None:
        exporter = span_exporter
    elif config.endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=config.endpoint)
        except ImportError:
            _logger.warning("OTLP gRPC exporter not available, falling back to console")
            exporter = ConsoleSpanExporter()
    elif config.console_export:
        exporter = ConsoleSpanExporter()
    else:
        exporter = None

    _span_processor = span_processor if span_processor is not None else BatchSpanProcessor
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)
    if exporter is not None:
        tracer_provider.add_span_processor(_span_processor(exporter))

    # ── meter / metric reader ──
    if meter is not None:
        mp = meter
    elif metric_reader is not None:
        mp = MeterProvider(resource=resource, metric_readers=[metric_reader])
    elif config.console_export:
        mp = MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(ConsoleMetricExporter())],
        )
    else:
        # No reader — instruments exist but don't export (no-op for metrics).
        mp = MeterProvider(resource=resource)

    actual_meter = mp.get_meter("miqi.telemetry") if hasattr(mp, "get_meter") else mp

    # Use the provider's own tracer (not the global proxy) so each
    # build_telemetry_sink call is isolated — tests can create multiple
    # sinks without "Overriding of current TracerProvider" warnings.
    tracer = tracer_provider.get_tracer("miqi.telemetry")

    instruments = {
        "turn_counter": actual_meter.create_counter(
            "miqi.turn.count", description="Number of turns"
        ),
        "error_counter": actual_meter.create_counter(
            "miqi.error.count", description="Number of errors"
        ),
        "token_histogram": actual_meter.create_histogram(
            "miqi.token.usage", description="Token usage per turn"
        ),
        "tool_counter": actual_meter.create_counter(
            "miqi.tool_call.count", description="Number of tool calls"
        ),
    }

    sink = TelemetrySink(
        tracer=tracer,
        meter=actual_meter,
        instruments=instruments,
        capture_content=config.capture_content,
    )
    # Inject the modules needed at runtime (avoid top-level imports).
    sink._trace = otel_trace
    sink._context = otel_context
    sink._Status = Status
    sink._StatusCode = StatusCode

    return sink.handle
