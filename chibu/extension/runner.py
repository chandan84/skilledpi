"""Extension runner — wraps every agent action with before/after hooks.

Env-var gates:
  CHIBU_DEBUG_LOGS=1   → emit debug spans and log records
  CHIBU_PERF_LOGS=1    → emit performance/duration metrics
  CHIBU_LLM_LOGS=1     → emit full LLM request/response payloads
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("chibu.extension")

# ── OTEL bootstrap ────────────────────────────────────────────────────────────

_otel_tracer = None
_otel_meter = None
_otel_logger_provider = None
_otel_initialised = False


def _init_otel(config: dict) -> None:
    global _otel_tracer, _otel_meter, _otel_initialised

    if _otel_initialised:
        return

    otel_cfg = config.get("otel", {})
    if not otel_cfg.get("enabled", False):
        _otel_initialised = True
        return

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(
            {"service.name": otel_cfg.get("service_name", "chibu-pi-agent")}
        )

        endpoint = otel_cfg.get("endpoint", "http://localhost:4317")
        protocol = otel_cfg.get("protocol", "grpc")

        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter,
            )

            span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            http_endpoint = otel_cfg.get("http_endpoint", "http://localhost:4318")
            span_exporter = OTLPSpanExporter(endpoint=f"{http_endpoint}/v1/traces")
            metric_exporter = OTLPMetricExporter(
                endpoint=f"{http_endpoint}/v1/metrics"
            )

        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        _otel_tracer = trace.get_tracer("chibu.extension")

        interval_ms = otel_cfg.get("export_interval_ms", 5000)
        reader = PeriodicExportingMetricReader(
            metric_exporter, export_interval_millis=interval_ms
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        _otel_meter = metrics.get_meter("chibu.extension")

        logger.info("OTEL initialised → %s (%s)", endpoint, protocol)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OTEL init failed: %s — continuing without telemetry", exc)

    _otel_initialised = True


# ── Extension runner ──────────────────────────────────────────────────────────


class ExtensionRunner:
    """Loads per-agent extensions and manages before/after action hooks."""

    def __init__(self, agent_root: Path, agent_id: str, agent_name: str) -> None:
        self.agent_root = agent_root
        self.agent_id = agent_id
        self.agent_name = agent_name
        self._config: dict = {}
        self._extensions: list[Any] = []
        self._action_counter = 0

        self._debug = os.getenv("CHIBU_DEBUG_LOGS", "0") == "1"
        self._perf = os.getenv("CHIBU_PERF_LOGS", "0") == "1"
        self._llm = os.getenv("CHIBU_LLM_LOGS", "0") == "1"

        self._load_config()
        _init_otel(self._config)
        self._load_extensions()

    # ── config ──────────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        config_path = self.agent_root / ".pi" / "config.yaml"
        if config_path.exists():
            try:
                self._config = yaml.safe_load(config_path.read_text()) or {}
            except Exception:  # noqa: BLE001
                self._config = {}

    # ── extension discovery ──────────────────────────────────────────────────

    def _load_extensions(self) -> None:
        ext_dir = self.agent_root / ".pi" / "extensions"
        if not ext_dir.exists():
            return

        import importlib.util

        for py_file in sorted(ext_dir.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(
                    f"chibu_ext_{py_file.stem}", py_file
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                if hasattr(mod, "Extension"):
                    ext_instance = mod.Extension(
                        agent_id=self.agent_id,
                        agent_name=self.agent_name,
                        config=self._config,
                    )
                    self._extensions.append(ext_instance)
                    logger.debug("Loaded extension: %s", py_file.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to load extension %s: %s", py_file.name, exc)

    # ── public hooks ─────────────────────────────────────────────────────────

    def before_action(
        self,
        action_type: str,
        action_data: dict,
        session_id: str = "",
    ) -> dict:
        """Call before every agent action. Returns context dict passed to after_action."""
        self._action_counter += 1
        ctx: dict = {
            "action_id": self._action_counter,
            "action_type": action_type,
            "agent_id": self.agent_id,
            "session_id": session_id,
            "start_time": time.perf_counter(),
            "start_ts": time.time(),
        }

        if self._debug:
            logger.debug(
                "[before] agent=%s session=%s action_type=%s action_id=%d",
                self.agent_id,
                session_id,
                action_type,
                self._action_counter,
            )

        if self._llm and action_type in ("llm_request",):
            logger.info(
                "[LLM-REQ] session=%s model=%s prompt_tokens=%s",
                session_id,
                action_data.get("model", ""),
                action_data.get("prompt_tokens", "?"),
            )

        if _otel_tracer is not None:
            span = _otel_tracer.start_span(
                f"chibu.{action_type}",
                attributes={
                    "agent.id": self.agent_id,
                    "agent.name": self.agent_name,
                    "session.id": session_id,
                    "action.type": action_type,
                },
            )
            ctx["otel_span"] = span

        # user-defined extensions
        for ext in self._extensions:
            try:
                ext.before_action(action_type, action_data, ctx)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Extension before_action error: %s", exc)

        return ctx

    def after_action(
        self,
        ctx: dict,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        """Call after every agent action."""
        elapsed = time.perf_counter() - ctx.get("start_time", time.perf_counter())
        action_type = ctx.get("action_type", "unknown")
        session_id = ctx.get("session_id", "")

        if self._debug:
            status = "error" if error else "ok"
            logger.debug(
                "[after] agent=%s session=%s action_type=%s status=%s elapsed=%.3fs",
                self.agent_id,
                session_id,
                action_type,
                status,
                elapsed,
            )

        if self._perf:
            logger.info(
                "[PERF] action=%s elapsed=%.4fs agent=%s",
                action_type,
                elapsed,
                self.agent_id,
            )

        if self._llm and action_type in ("llm_response",) and result:
            logger.info(
                "[LLM-RES] session=%s completion_tokens=%s stop_reason=%s",
                session_id,
                result.get("completion_tokens", "?"),
                result.get("stop_reason", "?"),
            )

        span = ctx.get("otel_span")
        if span is not None:
            if error:
                from opentelemetry.trace import StatusCode

                span.set_status(StatusCode.ERROR, str(error))
                span.record_exception(error)
            span.set_attribute("action.elapsed_ms", elapsed * 1000)
            span.end()

        for ext in self._extensions:
            try:
                ext.after_action(ctx, result, error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Extension after_action error: %s", exc)
