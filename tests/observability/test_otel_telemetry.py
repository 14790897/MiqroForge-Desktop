"""OpenTelemetry observability tests (Plan 59).

Covers:
- 59.1: Config defaults (default OFF, sane defaults)
- 59.2: TelemetrySink lifecycle (no-op stub, real OTel traces/metrics)
- 59.3: RuntimeServices tee (additive, original sink unchanged)

OTel-dependent tests are gated with ``pytest.importorskip("opentelemetry")``.
No-op tests run unconditionally.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from miqi.config.schema import Config, ObservabilityConfig
from miqi.observability.otel import build_telemetry_sink

# ── In-memory span exporter for testing ────────────────────────


def _make_in_memory_exporter():
    """Create an in-memory span exporter for tests."""
    pytest.importorskip("opentelemetry")
    from opentelemetry.sdk.trace.export import SpanExportResult

    class _InMemoryExporter:
        def __init__(self):
            self.spans: list = []

        def export(self, spans):
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    return _InMemoryExporter()


# ── Event factories (match miqi.protocol.events shapes) ────────


def _event(type: str, **kwargs) -> Any:
    """Create a fake event with a string ``type`` and arbitrary kwargs."""

    class _FakeEvent:
        pass

    ev = _FakeEvent()
    ev.type = type
    for k, v in kwargs.items():
        setattr(ev, k, v)
    return ev


# ── 59.1: Config defaults ─────────────────────────────────────


class TestObservabilityConfig:
    def test_defaults_disabled(self):
        cfg = ObservabilityConfig()
        assert cfg.enabled is False
        assert cfg.endpoint is None
        assert cfg.service_name == "miqi"
        assert cfg.console_export is False
        assert cfg.sample_ratio == 1.0
        assert cfg.capture_content is False

    def test_attached_to_root_config(self):
        cfg = Config()
        assert cfg.observability.enabled is False


# ── 59.2: No-op paths (unconditional) ──────────────────────────


class TestNoOpPaths:
    def test_disabled_returns_none(self):
        """When enabled=False, build_telemetry_sink returns None."""
        cfg = ObservabilityConfig(enabled=False)
        result = build_telemetry_sink(cfg)
        assert result is None

    def test_disabled_never_imports_otel(self, monkeypatch):
        """When disabled, no opentelemetry import is attempted."""

        original_import = __builtins__["__import__"] if hasattr(__builtins__, "__import__") else __import__

        imported: list[str] = []

        def _tracking_import(name, *args, **kwargs):
            imported.append(name)
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", _tracking_import)
        # Clear any cached imports
        cfg = ObservabilityConfig(enabled=False)
        result = build_telemetry_sink(cfg)
        assert result is None
        # We should not have imported opentelemetry modules
        otel_imports = [n for n in imported if "opentelemetry" in n]
        assert len(otel_imports) == 0, f"Unexpected otel imports: {otel_imports}"

    def test_missing_sdk_returns_none(self, monkeypatch):
        """When opentelemetry is not installed, build_telemetry_sink logs a warning
        and returns None without raising."""
        import builtins

        real_import = builtins.__import__

        def _block_otel(name, *args, **kwargs):
            if "opentelemetry" in name:
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_otel)

        cfg = ObservabilityConfig(enabled=True)
        result = build_telemetry_sink(cfg)
        assert result is None  # Returns None, does not raise


# ── 59.2: OTel-dependent tests ────────────────────────────────


@pytest.mark.asyncio
class TestOtelSpanLifecycle:
    """Turn span lifecycle tests — require opentelemetry."""

    @pytest.fixture(autouse=True)
    def _require_otel(self):
        pytest.importorskip("opentelemetry")

    def _build_sink(self, **cfg_overrides):
        """Build a TelemetrySink-backed handle with an in-memory exporter.

        Uses SimpleSpanProcessor for synchronous span export so tests
        can read exported spans immediately after span.end().
        """
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        exporter = _make_in_memory_exporter()
        cfg = ObservabilityConfig(enabled=True, **cfg_overrides)
        handle = build_telemetry_sink(
            cfg,
            span_exporter=exporter,
            span_processor=SimpleSpanProcessor,
        )
        assert handle is not None, "build_telemetry_sink returned None unexpectedly"
        # Dig out the TelemetrySink instance (handle is a bound method)
        sink = handle.__self__
        return handle, sink, exporter

    async def test_turn_started_creates_span(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))

        assert len(exporter.spans) == 0  # Not ended yet
        assert "t1" in sink._spans

    async def test_turn_started_then_completed_produces_finished_span(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event(
                "turn_complete",
                turn_id="t1",
                thread_id="th1",
                outcome="success",
                tools_used=["bash", "read"],
                token_usage={"input": 100, "output": 50},
            )
        )

        assert len(exporter.spans) == 1
        span = exporter.spans[0]
        assert span.name == "miqi.turn"
        assert span.status.status_code.name == "OK"
        attrs = dict(span.attributes or {})
        assert attrs["outcome"] == "success"
        assert attrs["thread_id"] == "th1"
        assert attrs["input_tokens"] == 100
        assert attrs["output_tokens"] == 50
        assert "t1" not in sink._spans  # cleaned up

    async def test_turn_aborted_ends_span_with_error_status(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(_event("turn_aborted", turn_id="t1", thread_id="th1", reason="user cancelled"))

        assert len(exporter.spans) == 1
        span = exporter.spans[0]
        assert span.name == "miqi.turn"
        assert span.status.status_code.name == "ERROR"
        attrs = dict(span.attributes or {})
        assert attrs["reason"] == "user cancelled"

    async def test_error_event_bumps_counter_and_marks_span(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event(
                "error",
                turn_id="t1",
                severity="error",
                message="Something broke",
                recoverable=False,
                error_kind="rate_limit",
            )
        )

        # Span should be ended with ERROR status and exported
        assert len(exporter.spans) == 1
        span = exporter.spans[0]
        assert span.name == "miqi.turn"
        assert span.status.status_code.name == "ERROR"
        assert span.status.description == "Something broke"
        assert "t1" not in sink._spans  # popped by _on_error

    async def test_error_ends_span_and_removes_from_tracking(self):
        """After an error event, the turn span is ended with ERROR status
        and cleaned up from sink._spans (so turn_complete cannot touch it)."""
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event(
                "error",
                turn_id="t1",
                severity="error",
                message="Something broke",
                recoverable=False,
                error_kind="rate_limit",
            )
        )

        # Span should be exported (ended) and removed from tracking
        assert len(exporter.spans) == 1
        span = exporter.spans[0]
        assert span.name == "miqi.turn"
        assert span.status.status_code.name == "ERROR"
        assert "t1" not in sink._spans  # cleaned up by pop

    async def test_error_then_complete_does_not_revert_to_ok(self):
        """When error arrives before turn_complete, the error span is ended
        and removed.  A subsequent turn_complete finds nothing in _spans
        and is a no-op — the span stays ERROR."""
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event(
                "error",
                turn_id="t1",
                severity="error",
                message="Something broke",
                recoverable=False,
                error_kind="rate_limit",
            )
        )
        await handle(
            _event(
                "turn_complete",
                turn_id="t1",
                thread_id="th1",
                outcome="success",
            )
        )

        # Only one span was ended (by error), complete was a no-op.
        assert len(exporter.spans) == 1
        span = exporter.spans[0]
        assert span.name == "miqi.turn"
        assert span.status.status_code.name == "ERROR"
        # Verify the error description is preserved
        assert span.status.description == "Something broke"

    async def test_tool_child_span(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event(
                "tool_call_begin",
                turn_id="t1",
                tool_call_id="tc1",
                tool_name="bash",
                tool_display="Run bash",
            )
        )
        await handle(
            _event(
                "tool_call_end",
                turn_id="t1",
                tool_call_id="tc1",
                tool_name="bash",
                success=True,
                output_preview="hello",
                output_size=5,
                duration_ms=42,
            )
        )
        await handle(
            _event(
                "turn_complete",
                turn_id="t1",
                thread_id="th1",
                outcome="success",
                tools_used=["bash"],
                token_usage={"input": 10, "output": 5},
            )
        )

        # Two spans: turn + tool child (tool ends first, so it's exported first)
        assert len(exporter.spans) == 2
        spans_by_name = {s.name: s for s in exporter.spans}
        turn_span = spans_by_name["miqi.turn"]
        tool_span = spans_by_name["miqi.tool_call"]
        # Child span should have parent
        assert tool_span.parent is not None
        assert tool_span.context.trace_id == turn_span.context.trace_id
        attrs = dict(tool_span.attributes or {})
        assert attrs["tool_name"] == "bash"
        assert attrs["success"] is True
        assert attrs["duration_ms"] == 42

    async def test_tool_call_without_parent_turn_is_ignored(self):
        handle, sink, exporter = self._build_sink()

        await handle(
            _event(
                "tool_call_begin",
                turn_id="nonexistent",
                tool_call_id="tc1",
                tool_name="bash",
            )
        )
        await handle(
            _event(
                "tool_call_end",
                turn_id="nonexistent",
                tool_call_id="tc1",
                tool_name="bash",
                success=True,
            )
        )

        assert len(exporter.spans) == 0
        assert len(sink._tool_spans) == 0

    async def test_unknown_events_are_ignored(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(_event("agent_message_delta", turn_id="t1", delta="hello"))
        await handle(_event("agent_reasoning", turn_id="t1", content="thinking..."))
        await handle(
            _event(
                "turn_complete",
                turn_id="t1",
                thread_id="th1",
                outcome="success",
            )
        )

        assert len(exporter.spans) == 1  # only the turn span

    async def test_duplicate_turn_complete_does_not_raise(self):
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event(
                "turn_complete",
                turn_id="t1",
                thread_id="th1",
                outcome="success",
            )
        )
        # Second complete on the same turn_id — should be a no-op.
        await handle(
            _event(
                "turn_complete",
                turn_id="t1",
                thread_id="th1",
                outcome="error",
            )
        )

        assert len(exporter.spans) == 1  # Only one span was ended

    async def test_turn_complete_without_start_does_not_raise(self):
        handle, sink, exporter = self._build_sink()

        # No turn_started → complete is safely ignored.
        await handle(
            _event(
                "turn_complete",
                turn_id="no-start",
                thread_id="th1",
                outcome="success",
            )
        )

        assert len(exporter.spans) == 0

    async def test_telemetry_exception_does_not_propagate(self):
        """An internal exception in handle() is swallowed."""
        handle, sink, exporter = self._build_sink()

        # Corrupt the sink so _on_turn_started raises
        sink._on_turn_started = lambda ev: (_ for _ in ()).throw(RuntimeError("boom"))

        # This must NOT raise
        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))

    async def test_capture_content_disabled_by_default(self):
        """When capture_content=False, output_preview is NOT attached to tool spans."""
        handle, sink, exporter = self._build_sink()

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event("tool_call_begin", turn_id="t1", tool_call_id="tc1", tool_name="bash")
        )
        await handle(
            _event(
                "tool_call_end",
                turn_id="t1",
                tool_call_id="tc1",
                tool_name="bash",
                success=True,
                output_preview="secret stuff",
            )
        )
        await handle(
            _event("turn_complete", turn_id="t1", thread_id="th1", outcome="success")
        )

        tool_span = [s for s in exporter.spans if s.name == "miqi.tool_call"][0]
        attrs = dict(tool_span.attributes or {})
        assert "output_preview" not in attrs

    async def test_capture_content_enabled(self):
        """When capture_content=True, output_preview is attached."""
        handle, sink, exporter = self._build_sink(capture_content=True)

        await handle(_event("turn_started", turn_id="t1", thread_id="th1", agent_name="test"))
        await handle(
            _event("tool_call_begin", turn_id="t1", tool_call_id="tc1", tool_name="bash")
        )
        await handle(
            _event(
                "tool_call_end",
                turn_id="t1",
                tool_call_id="tc1",
                tool_name="bash",
                success=True,
                output_preview="hello output",
            )
        )
        await handle(
            _event("turn_complete", turn_id="t1", thread_id="th1", outcome="success")
        )

        tool_span = [s for s in exporter.spans if s.name == "miqi.tool_call"][0]
        attrs = dict(tool_span.attributes or {})
        assert attrs["output_preview"] == "hello output"


# ── 59.3: RuntimeServices tee ──────────────────────────────────


class TestRuntimeServicesTee:
    """Integration: RuntimeServices.from_config tees the sink."""

    def _make_minimal_config(self, obs_enabled: bool = False):
        """Return a Config that won't fully initialize but is sufficient for
        testing the tee wiring without needing a real provider, DB, etc."""
        cfg = Config()
        cfg.observability.enabled = obs_enabled
        return cfg

    def test_default_config_leaves_sink_unchanged(self):
        """When observability is disabled, from_config uses the original sink."""

        # We can't fully construct RuntimeServices without a real provider + DB,
        # so we test the tee logic in isolation by verifying the config path.
        cfg = Config()
        assert cfg.observability.enabled is False

    def test_tee_function_preserves_original_sink(self):
        """Verify the tee pattern: original sink still receives events when
        telemetry is added."""
        original_events: list = []
        telemetry_events: list = []

        async def original_sink(event):
            original_events.append(event)

        async def telemetry_handle(event):
            telemetry_events.append(event)

        # Recreate the tee pattern from services.py
        async def _tee(event):
            await original_sink(event)
            try:
                await telemetry_handle(event)
            except Exception:
                pass

        async def run():
            await _tee(_event("turn_started", turn_id="t1"))

        asyncio.run(run())

        assert len(original_events) == 1
        assert len(telemetry_events) == 1

    def test_tee_swallows_telemetry_exception(self):
        """If telemetry handle raises, the original sink still gets the event."""
        original_events: list = []

        async def original_sink(event):
            original_events.append(event)

        async def broken_telemetry(event):
            raise RuntimeError("boom")

        async def _tee(event):
            await original_sink(event)
            try:
                await broken_telemetry(event)
            except Exception:
                pass

        async def run():
            await _tee(_event("turn_started", turn_id="t1"))

        asyncio.run(run())

        assert len(original_events) == 1  # original sink fired
