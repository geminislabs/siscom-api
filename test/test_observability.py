"""Tests unitarios de observabilidad. Sin Collector y sin PostgreSQL."""

import json
import logging
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response

pytestmark = pytest.mark.no_db


def _settings(**overrides):
    base = {
        "OTLP_ENDPOINT": "",
        "DEPLOY_ENV": "test",
        "SERVICE_NAME": "siscom-api",
        "APP_VERSION": "0.1.0",
        "LOG_LEVEL": "INFO",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _aislamiento_otel():
    from app.observability.init import reset_telemetry_for_tests
    from app.observability.logging import reset_logging_for_tests

    reset_telemetry_for_tests()
    reset_logging_for_tests()
    yield
    reset_telemetry_for_tests()
    reset_logging_for_tests()


def test_empty_endpoint_does_not_create_exporter(monkeypatch):
    import opentelemetry.exporter.otlp.proto.http._log_exporter as log_exporter
    from opentelemetry.exporter.otlp.proto.http import metric_exporter, trace_exporter

    from app.observability.init import (
        get_tracer_provider,
        reset_telemetry_for_tests,
        setup_telemetry,
        telemetry_enabled,
    )

    def boom(*_args, **_kwargs):
        raise AssertionError("exporter created")

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", boom)
    monkeypatch.setattr(metric_exporter, "OTLPMetricExporter", boom)
    monkeypatch.setattr(log_exporter, "OTLPLogExporter", boom)

    reset_telemetry_for_tests()
    setup_telemetry(_settings(OTLP_ENDPOINT=""))
    assert telemetry_enabled() is False
    assert get_tracer_provider() is None

    reset_telemetry_for_tests()
    setup_telemetry(_settings(OTLP_ENDPOINT="   "))
    assert telemetry_enabled() is False
    assert get_tracer_provider() is None


def test_setup_telemetry_is_idempotent_and_builds_exporters(monkeypatch):
    import opentelemetry.exporter.otlp.proto.http._log_exporter as log_exporter
    from opentelemetry.exporter.otlp.proto.http import metric_exporter, trace_exporter
    from opentelemetry.sdk._logs.export import LogExportResult
    from opentelemetry.sdk.metrics.export import MetricExportResult
    from opentelemetry.sdk.trace.export import SpanExportResult

    from app.observability.init import (
        get_tracer_provider,
        reset_telemetry_for_tests,
        setup_telemetry,
        telemetry_enabled,
    )

    created: list[str] = []

    class _Exporter:
        def __init__(self, endpoint=None, **_kwargs):
            created.append(endpoint)

        def shutdown(self, *_args, **_kwargs):
            return None

        def force_flush(self, *_args, **_kwargs):
            return True

    class _SpanExporter(_Exporter):
        def export(self, *_args, **_kwargs):
            return SpanExportResult.SUCCESS

    class _MetricExporter(_Exporter):
        # PeriodicExportingMetricReader lee estas preferencias del exporter.
        _preferred_temporality = None
        _preferred_aggregation = None

        def export(self, *_args, **_kwargs):
            return MetricExportResult.SUCCESS

    class _LogExporter(_Exporter):
        def export(self, *_args, **_kwargs):
            return LogExportResult.SUCCESS

    monkeypatch.setattr(trace_exporter, "OTLPSpanExporter", _SpanExporter)
    monkeypatch.setattr(metric_exporter, "OTLPMetricExporter", _MetricExporter)
    monkeypatch.setattr(log_exporter, "OTLPLogExporter", _LogExporter)

    reset_telemetry_for_tests()
    setup_telemetry(_settings(OTLP_ENDPOINT="http://localhost:4318"))
    first = get_tracer_provider()
    assert telemetry_enabled() is True
    assert first is not None
    setup_telemetry(_settings(OTLP_ENDPOINT="http://localhost:4318"))
    assert get_tracer_provider() is first
    assert "http://localhost:4318/v1/traces" in created
    assert "http://localhost:4318/v1/metrics" in created
    assert "http://localhost:4318/v1/logs" in created

    # Cerrar un span pasa por ScrubbingSpanProcessor._on_ending. Si falta,
    # el SDK 1.45 lanza AttributeError y la petición real falla con el collector.
    from opentelemetry import trace as otel_trace

    with otel_trace.get_tracer("siscom-api").start_as_current_span("probe") as span:
        span.set_attribute("token", "sekret")
    assert span.attributes["token"] == "[REDACTED]"


def test_instrument_app_is_noop_without_endpoint():
    from app.observability.init import instrument_app, setup_telemetry

    setup_telemetry(_settings(OTLP_ENDPOINT=""))
    instrument_app(object())


def test_scrubber_redacts_token_and_coordinates():
    from app.observability.logging import (
        JSONFormatter,
        scrub_query_token,
        scrub_span,
        scrub_value,
    )

    assert scrub_value("token", "v4.public.sekret") == "[REDACTED]"
    assert scrub_value("authorization", "Bearer abc") == "[REDACTED]"
    assert scrub_value("latitude", 19.4326) == "[REDACTED]"
    assert scrub_value("longitude", -99.13) == "[REDACTED]"
    assert scrub_value("lat", 1) == "[REDACTED]"
    assert scrub_value("lng", 2) == "[REDACTED]"
    assert scrub_value("platform", "linux") == "linux"
    nested = scrub_value("payload", {"refresh_token": "abc", "ok": 1})
    assert nested["refresh_token"] == "[REDACTED]"
    assert nested["ok"] == 1

    redacted = scrub_query_token("/share/init?token=sekret&x=1")
    assert redacted == "/share/init?token=[REDACTED]&x=1"
    assert "sekret" not in redacted

    fmt = JSONFormatter(service_name="siscom-api", deploy_env="test")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="data_token.rejected",
        args=(),
        exc_info=None,
    )
    record.token = "v4.public.sekret"
    record.outcome = "invalid"
    data = json.loads(fmt.format(record))
    assert data["token"] == "[REDACTED]"
    assert data["outcome"] == "invalid"
    assert "sekret" not in fmt.format(record)

    class _Span:
        def __init__(self):
            self._attributes = {
                "http.target": "/api/v1/public/share-location/init?token=sekret",
                "http.method": "GET",
                "token": "sekret",
            }

    span = _Span()
    scrub_span(span)
    assert "sekret" not in span._attributes["http.target"]
    assert span._attributes["token"] == "[REDACTED]"
    assert span._attributes["http.method"] == "GET"


def test_histogram_uses_template_not_id(monkeypatch):
    from app.core.config import settings
    from app.observability import metrics as metrics_mod

    monkeypatch.setattr(settings, "SERVICE_NAME", "siscom-api")
    monkeypatch.setattr(settings, "DEPLOY_ENV", "test")
    seen: dict = {}

    class _Histogram:
        def record(self, value, attrs):
            seen["value"] = value
            seen["attrs"] = attrs
            seen["buckets"] = buckets_box["buckets"]

    buckets_box: dict = {}

    def _histogram(name, description, buckets):
        buckets_box["name"] = name
        buckets_box["buckets"] = buckets
        return _Histogram()

    monkeypatch.setattr(metrics_mod, "_histogram", _histogram)
    metrics_mod.record_http_server_duration(
        "/api/v1/devices/{device_id}/communications",
        "get",
        200,
        12.5,
    )
    assert buckets_box["name"] == "http_server_duration_milliseconds"
    assert seen["value"] == 12.5
    assert seen["attrs"]["http_route"] == "/api/v1/devices/{device_id}/communications"
    assert "867564050638581" not in seen["attrs"]["http_route"]
    assert seen["attrs"]["service_name"] == "siscom-api"
    assert seen["attrs"]["deployment_environment"] == "test"
    assert seen["attrs"]["http_method"] == "GET"
    assert seen["attrs"]["http_status_code"] == "200"


def test_route_template_ignores_raw_id():
    import app.main as main_mod

    class _Ruta:
        path = "/api/v1/devices/{device_id}/communications"

    con_ruta = Request(
        {
            "type": "http",
            "headers": [],
            "route": _Ruta(),
            "path": "/api/v1/devices/867564050638581/communications",
        }
    )
    assert (
        main_mod._plantilla_de_ruta(con_ruta)
        == "/api/v1/devices/{device_id}/communications"
    )
    sin_ruta = Request(
        {
            "type": "http",
            "headers": [],
            "path": "/api/v1/devices/867564050638581/communications",
        }
    )
    assert main_mod._plantilla_de_ruta(sin_ruta) == "unmatched"


@pytest.mark.asyncio
async def test_duration_middleware_skips_health_and_keeps_template(monkeypatch):
    import app.main as main_mod

    recorded = []

    def _record(route, method, status_code, duration_ms):
        recorded.append((route, method, status_code, duration_ms))

    monkeypatch.setattr(main_mod, "record_http_server_duration", _record)

    class _Ruta:
        path = "/api/v1/devices/{device_id}/communications"

    business = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/devices/867564050638581/communications",
            "raw_path": b"/api/v1/devices/867564050638581/communications",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
            "route": _Ruta(),
        }
    )

    async def _ok(_request):
        return Response(status_code=201)

    await main_mod.record_http_duration(business, _ok)
    assert recorded[0][0] == "/api/v1/devices/{device_id}/communications"
    assert "867564050638581" not in recorded[0][0]
    assert recorded[0][1] == "GET"
    assert recorded[0][2] == 201

    health = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/health",
            "raw_path": b"/health",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
            "route": SimpleNamespace(path="/health"),
        }
    )
    await main_mod.record_http_duration(health, _ok)
    assert len(recorded) == 1


def test_metric_and_session_helpers_fail_open(monkeypatch):
    from app.observability import metrics as metrics_mod
    from app.observability import spans as spans_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("otel down")

    monkeypatch.setattr(metrics_mod, "_counter", boom)
    monkeypatch.setattr(metrics_mod, "_histogram", boom)
    monkeypatch.setattr(metrics_mod, "_updown", boom)
    monkeypatch.setattr(metrics_mod, "_meter", boom)

    metrics_mod.record_http_server_duration("/api/v1/events", "GET", 200, 1)
    metrics_mod.record_kafka_message("tracking/data", "ok")
    metrics_mod.record_kafka_reconnect()
    metrics_mod.record_data_token_decision("accepted")
    metrics_mod.record_share_access("ok")
    metrics_mod.record_valkey_lookup("miss")
    metrics_mod.record_query_fanout("/api/v1/events", 3)
    metrics_mod.record_ws_message_published("stream")
    metrics_mod.record_ws_message_dropped("share")
    metrics_mod.record_ws_connection_delta("stream", 1)
    metrics_mod.register_observable_instruments()

    monkeypatch.setattr(spans_mod.trace, "get_tracer", boom)
    session = spans_mod.open_ws_session("/api/v1/stream", "stream")
    session.mark_accepted()
    session.set_outcome("error")
    session.close()
    session.close()

    with spans_mod.valkey_resolve_span() as span:
        assert span is None


def test_ws_session_closes_once_and_keeps_outcome(monkeypatch):
    from app.observability import spans as spans_mod

    ends: list[dict] = []

    class _Span:
        def set_attribute(self, key, value):
            self.attrs = getattr(self, "attrs", {})
            self.attrs[key] = value

        def end(self):
            ends.append(dict(self.attrs))

    class _Tracer:
        def start_span(self, name):
            assert name == "ws.session"
            return _Span()

    monkeypatch.setattr(spans_mod.trace, "get_tracer", lambda _name: _Tracer())
    deltas: list[tuple] = []
    monkeypatch.setattr(
        spans_mod,
        "record_ws_connection_delta",
        lambda route, delta: deltas.append((route, delta)),
    )

    session = spans_mod.open_ws_session("/api/v1/stream", "stream")
    session.mark_accepted()
    session.mark_accepted()
    session.set_outcome("auth_rejected")
    session.note_disconnect()
    session.close()
    session.close()

    assert deltas == [("stream", 1), ("stream", -1)]
    assert len(ends) == 1
    assert ends[0]["outcome"] == "auth_rejected"
    assert ends[0]["http_route"] == "/api/v1/stream"
    assert "device" not in ends[0]


def test_kafka_topic_label_is_closed(monkeypatch):
    from app.core.config import settings
    from app.observability import metrics as metrics_mod
    from app.observability.metrics import kafka_topic_label, record_kafka_message

    monkeypatch.setattr(settings, "KAFKA_TOPIC", "tracking/data")
    monkeypatch.setattr(settings, "KAFKA_ALERTS_TOPIC", "alerts")
    assert kafka_topic_label("tracking/data") == "tracking/data"
    assert kafka_topic_label("alerts") == "alerts"
    assert kafka_topic_label("otro-topic") == "other"
    assert kafka_topic_label(None) == "other"

    seen = []

    class _Counter:
        def add(self, _n, attrs=None):
            seen.append(attrs)

    monkeypatch.setattr(metrics_mod, "_counter", lambda *_a, **_k: _Counter())
    record_kafka_message("otro-topic", "decode_error")
    record_kafka_message("tracking/data", "nope")
    assert seen == [{"topic": "other", "outcome": "decode_error"}]


def test_query_fanout_uses_template_buckets(monkeypatch):
    from app.observability import metrics as metrics_mod

    captured = {}

    class _Histogram:
        def record(self, value, attrs):
            captured["value"] = value
            captured["attrs"] = attrs

    def _histogram(name, _description, buckets):
        captured["name"] = name
        captured["buckets"] = buckets
        return _Histogram()

    monkeypatch.setattr(metrics_mod, "_histogram", _histogram)
    metrics_mod.record_query_fanout("/api/v1/communications/latest", 25)
    assert captured["name"] == "query_fanout"
    assert captured["buckets"] == (1, 5, 10, 25, 50, 100)
    assert captured["attrs"] == {"route": "/api/v1/communications/latest"}
    assert captured["value"] == 25


def test_request_id_is_accepted_or_replaced_whole():
    from app.observability.request_id import MAX_REQUEST_ID, normalizar_request_id

    assert normalizar_request_id("abc-123_XYZ.7") == "abc-123_XYZ.7"
    for malo in (
        None,
        "",
        "   ",
        "abc\ndef",
        "a b",
        "x" * (MAX_REQUEST_ID + 1),
        "<script>",
    ):
        generado = normalizar_request_id(malo)
        assert generado != (malo or "")
        assert len(generado) == 36


def test_cors_allows_trace_headers_only_as_addition():
    import app.main as main_mod

    cors = [
        mw for mw in main_mod.app.user_middleware if mw.cls.__name__ == "CORSMiddleware"
    ]
    assert cors
    headers = cors[0].kwargs["allow_headers"]
    assert headers == [
        "Authorization",
        "Content-Type",
        "traceparent",
        "tracestate",
        "x-request-id",
    ]
    assert cors[0].kwargs["allow_methods"] == ["GET"]
    assert "X-Request-ID" in cors[0].kwargs["expose_headers"]


@pytest.mark.asyncio
async def test_health_keeps_fields_and_adds_environment(monkeypatch):
    from app.core.config import settings
    from app.main import health_check

    monkeypatch.setattr(settings, "DEPLOY_ENV", "test")
    payload = await health_check()
    assert payload["status"] in {"healthy", "degraded"}
    assert payload["service"] == settings.APP_NAME
    assert payload["version"] == settings.APP_VERSION
    assert payload["environment"] == "test"
    assert "kafka_circuit_breaker" in payload
    assert "open" in payload["kafka_circuit_breaker"]


def test_json_logging_emits_trace_fields():
    from app.observability.logging import JSONFormatter, configure_json_logging

    configure_json_logging(_settings(), level="INFO")
    formatter = JSONFormatter(service_name="siscom-api", deploy_env="test")
    record = logging.LogRecord(
        name="test.observability",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg="db.startup",
        args=(),
        exc_info=None,
    )
    record.outcome = "ok"
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "db.startup"
    assert payload["service.name"] == "siscom-api"
    assert payload["deployment.environment"] == "test"
    assert "trace_id" in payload
    assert "request_id" in payload
    assert payload["outcome"] == "ok"
