"""OpenTelemetry tracing for Chibu agent execution.

Enabled only when CHIBU_OTEL_ENABLED=true.  All functions are safe no-ops
when OTel is disabled — callers never need to guard.
"""

from __future__ import annotations

import contextlib
import os
from contextlib import contextmanager

_tracer = None
_enabled = False


def init_otel(service_name: str = "chibu-pi-agent") -> None:
    global _tracer, _enabled

    if os.getenv("CHIBU_OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name, "chibu.agent": service_name})
        provider = TracerProvider(resource=resource)

        protocol = os.getenv("CHIBU_OTEL_PROTOCOL", "grpc")
        endpoint = os.getenv(
            "CHIBU_OTEL_ENDPOINT",
            "http://localhost:4317" if protocol == "grpc" else "http://localhost:4318",
        )

        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint)
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")

        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("chibu")
        _enabled = True

    except Exception as exc:  # noqa: BLE001
        import logging
        logging.getLogger("chibu.otel").warning("OTel init failed: %s", exc)


@contextmanager
def record_execute_span(agent_id: str, chiboo: str, model: str):
    """Context manager that wraps an agent execute call in a trace span."""
    if not _enabled or _tracer is None:
        yield
        return

    from opentelemetry import trace
    with _tracer.start_as_current_span("chibu.agent.execute") as span:
        span.set_attribute("agent.id", agent_id)
        span.set_attribute("agent.chiboo", chiboo)
        span.set_attribute("agent.model", model)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            raise


def add_event(name: str, attributes: dict | None = None) -> None:
    """Record a span event on the current active span (no-op if no active span)."""
    if not _enabled:
        return
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event(name, attributes=attributes or {})
    except Exception:  # noqa: BLE001
        pass
