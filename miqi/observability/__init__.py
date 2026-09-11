"""OpenTelemetry observability (Plan 59).

Optional telemetry: when enabled and opentelemetry-sdk is installed,
runtime events are exported as traces (turn spans + tool child spans)
and metrics to an OTLP endpoint or console. Disabled by default.

The entire feature is a no-op when disabled or when the optional
``otel`` extra is not installed — no runtime behavior change.
"""

from miqi.observability.otel import TelemetrySink, build_telemetry_sink

__all__ = ["build_telemetry_sink", "TelemetrySink"]
