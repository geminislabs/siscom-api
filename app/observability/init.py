"""Bootstrap OpenTelemetry. Idempotente. Sin endpoint = no-op."""

# Los exporters se importan dentro de setup_telemetry: endpoint vacío no carga
# el SDK de exportación.
# ruff: noqa: PLC0415

from __future__ import annotations

import socket
from contextlib import suppress
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.propagate import set_global_textmap
from opentelemetry.trace import Span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from app.observability.logging import scrub_span


class _TelemetryState:
    tracer_provider: Any | None = None
    meter_provider: Any | None = None
    logger_provider: Any | None = None
    started: bool = False
    enabled: bool = False


_state = _TelemetryState()


def telemetry_enabled() -> bool:
    return _state.enabled


def get_tracer_provider() -> Any | None:
    return _state.tracer_provider


def get_meter_provider() -> Any | None:
    return _state.meter_provider


def get_logger_provider() -> Any | None:
    return _state.logger_provider


def _otlp_base(endpoint: str) -> str:
    return endpoint.rstrip("/")


def deployment_environment_of(settings: Any) -> str:
    raw = (getattr(settings, "DEPLOY_ENV", None) or "").strip()
    return raw or "unknown"


class ScrubbingSpanProcessor:
    """Aplica el scrubber al span antes de entregarlo al exporter."""

    def __init__(self, successor: Any) -> None:
        self._successor = successor

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        self._successor.on_start(span, parent_context)

    def _on_ending(self, span: Span) -> None:
        # El SDK 1.45 llama este hook en cada processor al cerrar el span.
        ending = getattr(self._successor, "_on_ending", None)
        if not callable(ending):
            return
        with suppress(Exception):
            ending(span)

    def on_end(self, span: Span) -> None:
        # end() congela los atributos antes de este hook. Sin descongelar,
        # el scrubber traga el TypeError y el secreto sale en el export.
        attributes = getattr(span, "_attributes", None)
        frozen = bool(getattr(attributes, "_immutable", False))
        if frozen:
            attributes._immutable = False
        try:
            with suppress(Exception):
                scrub_span(span)
        finally:
            if frozen:
                attributes._immutable = True
        self._successor.on_end(span)

    def shutdown(self) -> None:
        self._successor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._successor.force_flush(timeout_millis)


def setup_telemetry(settings: Any) -> None:
    """Registra providers OTel. Segunda llamada no-op. Endpoint vacío = silencio."""
    if _state.started:
        return

    endpoint = (getattr(settings, "OTLP_ENDPOINT", None) or "").strip()
    if not endpoint:
        _state.started = True
        _state.enabled = False
        return

    import logging

    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from app.observability.metrics import register_observable_instruments

    version = getattr(settings, "APP_VERSION", None) or getattr(
        settings, "SERVICE_VERSION", None
    )
    resource = Resource.create(
        {
            "service.name": getattr(settings, "SERVICE_NAME", "siscom-api"),
            "service.version": version or "unknown",
            "deployment.environment": deployment_environment_of(settings),
            "host.name": socket.gethostname(),
        }
    )
    base = _otlp_base(endpoint)

    tp = TracerProvider(resource=resource)
    tp.add_span_processor(
        ScrubbingSpanProcessor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
        )
    )
    trace.set_tracer_provider(tp)
    set_global_textmap(TraceContextTextMapPropagator())

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
        export_interval_millis=30_000,
    )
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(mp)

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs"))
    )
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.NOTSET, logger_provider=lp)
    )

    _state.tracer_provider = tp
    _state.meter_provider = mp
    _state.logger_provider = lp
    _state.started = True
    _state.enabled = True
    register_observable_instruments()


def instrument_app(app: Any) -> None:
    """Instrumentors automáticos. No-op si no hay Collector."""
    if not _state.enabled:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.core.database import engine

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_state.tracer_provider,
        excluded_urls="health",
    )
    # El engine de la app es async. El comentario SQL lleva el trace, no los
    # parámetros de la consulta.
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        tracer_provider=_state.tracer_provider,
        enable_commenter=True,
        capture_parameters=False,
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=_state.tracer_provider)
    RequestsInstrumentor().instrument(tracer_provider=_state.tracer_provider)


def _shutdown(provider: Any) -> None:
    if provider is None:
        return
    with suppress(Exception):
        provider.shutdown()


def reset_telemetry_for_tests() -> None:
    """Solo tests: permite volver a llamar setup_telemetry.

    Limpia tambien los instrumentos cacheados en `metrics`: se crean de forma
    perezosa contra el MeterProvider vigente, asi que uno creado antes del
    setup se queda no-op para siempre si no se tira aqui.
    """
    import logging

    from app.observability import metrics as _metrics

    _metrics.reset_instrumentos_para_tests()
    root = logging.getLogger()
    for handler in root.handlers[:]:
        if handler.__class__.__name__ == "LoggingHandler":
            root.removeHandler(handler)

    _shutdown(_state.tracer_provider)
    _shutdown(_state.meter_provider)
    _shutdown(_state.logger_provider)
    _state.tracer_provider = None
    _state.meter_provider = None
    _state.logger_provider = None
    _state.started = False
    _state.enabled = False
