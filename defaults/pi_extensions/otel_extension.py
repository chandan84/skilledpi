"""Built-in OTel extension — pre-installed on every Pi agent.

Reads env vars:
  CHIBU_DEBUG_LOGS=1   → attach prompt/response text to spans
  CHIBU_PERF_LOGS=1    → record latency histograms
  CHIBU_LLM_LOGS=1     → log full LLM request and response payloads

OTel endpoint is configured via config.yaml → otel.endpoint.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("chibu.ext.otel")


class Extension:
    """Chibu extension interface: instantiated once at agent startup."""

    name = "otel_extension"
    version = "0.1.0"

    def __init__(self, agent_id: str, agent_name: str, config: dict) -> None:
        self.agent_id = agent_id
        self.agent_name = agent_name
        self._debug = os.getenv("CHIBU_DEBUG_LOGS", "0") in ("1", "true")
        self._perf = os.getenv("CHIBU_PERF_LOGS", "0") in ("1", "true")
        self._llm = os.getenv("CHIBU_LLM_LOGS", "0") in ("1", "true")
        self._tracer = None
        self._latency_hist = None
        self._setup_otel(config)

    def _setup_otel(self, config: dict) -> None:
        otel = config.get("otel", {})
        if not otel.get("enabled", False):
            return
        try:
            from opentelemetry import metrics, trace

            self._tracer = trace.get_tracer(self.name)
            meter = metrics.get_meter(self.name)
            self._latency_hist = meter.create_histogram(
                "chibu.agent.action.latency_ms",
                unit="ms",
                description="End-to-end latency per agent action",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTel meter/tracer setup failed: %s", exc)

    def before_action(self, action_type: str, data: dict, ctx: dict) -> None:
        ctx["_otel_start"] = time.perf_counter()

        if self._tracer:
            try:
                span = self._tracer.start_span(
                    f"chibu.{action_type}",
                    attributes={
                        "agent.id": self.agent_id,
                        "agent.name": self.agent_name,
                        "action.type": action_type,
                    },
                )
                ctx["_otel_span"] = span
                if self._debug:
                    span.set_attribute("action.data_keys", str(list(data.keys())))
                if self._llm and action_type == "llm_request":
                    span.set_attribute("llm.model", data.get("model", ""))
            except Exception:  # noqa: BLE001
                pass

    def after_action(self, ctx: dict, result=None, error=None) -> None:
        elapsed_ms = (time.perf_counter() - ctx.get("_otel_start", time.perf_counter())) * 1000

        if self._perf and self._latency_hist:
            try:
                self._latency_hist.record(
                    elapsed_ms,
                    attributes={
                        "agent.name": self.agent_name,
                        "action.type": ctx.get("action_type", "unknown"),
                    },
                )
            except Exception:  # noqa: BLE001
                pass

        span = ctx.get("_otel_span")
        if span:
            try:
                if error:
                    from opentelemetry.trace import StatusCode
                    span.set_status(StatusCode.ERROR, str(error))
                    span.record_exception(error)
                span.set_attribute("action.elapsed_ms", elapsed_ms)
                if self._llm and result:
                    span.set_attribute(
                        "llm.completion_tokens",
                        result.get("completion_tokens", 0),
                    )
                span.end()
            except Exception:  # noqa: BLE001
                pass

        if self._debug:
            status = "error" if error else "ok"
            logger.debug(
                "[%s] %s %.1fms %s",
                ctx.get("action_type", "?"),
                self.agent_name,
                elapsed_ms,
                status,
            )
