"""OpenTelemetry instrumentation for Chibu."""
from chibu.otel.tracing import init_otel, record_execute_span
from chibu.otel.metrics import get_meter, record_prompt, record_tool_call

__all__ = [
    "init_otel",
    "record_execute_span",
    "get_meter",
    "record_prompt",
    "record_tool_call",
]
