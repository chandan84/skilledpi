"""OpenTelemetry metrics for Chibu.

Instruments:
  chibu.prompt.count          — counter: prompts submitted (by chiboo, model)
  chibu.prompt.duration_ms    — histogram: end-to-end prompt duration
  chibu.agent.active          — up-down counter: running agent subprocesses
  chibu.tool_call.count       — counter: tool calls (by tool name, chiboo)
  chibu.agent.error.count     — counter: agent-level errors
"""

from __future__ import annotations

import os

_meter = None
_prompt_counter = None
_prompt_duration = None
_active_gauge = None
_tool_counter = None
_error_counter = None


def _init_metrics() -> None:
    global _meter, _prompt_counter, _prompt_duration, _active_gauge, _tool_counter, _error_counter

    if os.getenv("CHIBU_OTEL_ENABLED", "").lower() not in ("1", "true", "yes"):
        return

    try:
        from opentelemetry import metrics
        _meter = metrics.get_meter("chibu")
        _prompt_counter = _meter.create_counter("chibu.prompt.count")
        _prompt_duration = _meter.create_histogram("chibu.prompt.duration_ms", unit="ms")
        _active_gauge = _meter.create_up_down_counter("chibu.agent.active")
        _tool_counter = _meter.create_counter("chibu.tool_call.count")
        _error_counter = _meter.create_counter("chibu.agent.error.count")
    except Exception:  # noqa: BLE001
        pass


def get_meter():
    return _meter


def record_prompt(chiboo: str, model: str, duration_ms: float) -> None:
    attrs = {"chiboo": chiboo, "model": model}
    if _prompt_counter:
        _prompt_counter.add(1, attrs)
    if _prompt_duration:
        _prompt_duration.record(duration_ms, attrs)


def record_tool_call(tool_name: str, chiboo: str) -> None:
    if _tool_counter:
        _tool_counter.add(1, {"tool": tool_name, "chiboo": chiboo})


def record_agent_active(delta: int, chiboo: str) -> None:
    if _active_gauge:
        _active_gauge.add(delta, {"chiboo": chiboo})


def record_error(chiboo: str, model: str) -> None:
    if _error_counter:
        _error_counter.add(1, {"chiboo": chiboo, "model": model})


# Initialise on import (safe no-op when OTel is disabled)
_init_metrics()
