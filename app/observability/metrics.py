"""Métricas de negocio y el histograma RED. Fail-open. StatsD no se toca."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import Observation

from app.core.config import settings

# Los mismos bordes que el frontend: el p95 de Grafana sale de este histograma.
HTTP_DURATION_BUCKETS = (
    5,
    10,
    25,
    50,
    75,
    100,
    250,
    500,
    750,
    1000,
    2500,
    5000,
    7500,
    10000,
)
QUERY_FANOUT_BUCKETS = (1, 5, 10, 25, 50, 100)

DATA_TOKEN_OUTCOMES = frozenset(
    {"accepted", "missing", "invalid", "expired", "scope_denied"}
)
SHARE_OUTCOMES = frozenset({"ok", "expired", "invalid", "unavailable"})
VALKEY_OUTCOMES = frozenset({"ok", "miss", "unavailable"})
KAFKA_OUTCOMES = frozenset({"ok", "decode_error"})
WS_ROUTES = frozenset({"stream", "share"})

_instruments: dict[str, Any] = {}


def _meter():
    return metrics.get_meter("siscom-api")


def _counter(name: str, description: str):
    if name not in _instruments:
        _instruments[name] = _meter().create_counter(name, description=description)
    return _instruments[name]


def _updown(name: str, description: str):
    if name not in _instruments:
        _instruments[name] = _meter().create_up_down_counter(
            name, description=description
        )
    return _instruments[name]


def _histogram(name: str, description: str, buckets: tuple[int, ...]):
    if name not in _instruments:
        _instruments[name] = _meter().create_histogram(
            name,
            unit="ms" if name.endswith("_milliseconds") else "1",
            description=description,
            explicit_bucket_boundaries_advisory=list(buckets),
        )
    return _instruments[name]


def reset_instrumentos_para_tests() -> None:
    """Solo tests: olvida los instrumentos creados contra el provider anterior."""
    _instruments.clear()


def deployment_environment() -> str:
    raw = (getattr(settings, "DEPLOY_ENV", None) or "").strip()
    return raw or "unknown"


def _service_labels() -> dict[str, str]:
    name = (getattr(settings, "SERVICE_NAME", None) or "").strip() or "siscom-api"
    return {
        "service_name": name,
        "deployment_environment": deployment_environment(),
    }


def kafka_topic_label(topic: str | None) -> str:
    """Topic de métrica solo si es uno de los dos configurados; si no, `other`."""
    if topic and topic == settings.KAFKA_TOPIC:
        return topic
    alerts = settings.KAFKA_ALERTS_TOPIC
    if topic and alerts and topic == alerts:
        return topic
    return "other"


def ws_route_label(route: str | None) -> str:
    if route in WS_ROUTES:
        return str(route)
    return "stream"


def record_http_server_duration(
    route: str, method: str, status_code: int, duration_ms: float
) -> None:
    """Histograma que consultan los dashboards. `route` es la plantilla."""
    with suppress(Exception):
        _histogram(
            "http_server_duration_milliseconds",
            "Duración de requests HTTP en milisegundos",
            HTTP_DURATION_BUCKETS,
        ).record(
            float(duration_ms),
            {
                **_service_labels(),
                "http_route": route or "unmatched",
                "http_method": (method or "GET").upper(),
                "http_status_code": str(status_code),
            },
        )


def record_ws_connection_delta(route: str, delta: int) -> None:
    with suppress(Exception):
        _updown(
            "ws_connections",
            "Conexiones WebSocket activas",
        ).add(delta, {"route": ws_route_label(route)})


def record_ws_message_published(route: str) -> None:
    with suppress(Exception):
        _counter(
            "ws_messages_published_total",
            "Mensajes entregados a la cola de un WebSocket",
        ).add(1, {"route": ws_route_label(route)})


def record_ws_message_dropped(route: str) -> None:
    with suppress(Exception):
        _counter(
            "ws_messages_dropped_total",
            "Mensajes descartados porque la cola de 100 estaba llena",
        ).add(1, {"route": ws_route_label(route)})


def record_kafka_message(topic: str | None, outcome: str) -> None:
    if outcome not in KAFKA_OUTCOMES:
        return
    with suppress(Exception):
        _counter(
            "kafka_messages_total",
            "Mensajes Kafka consumidos",
        ).add(1, {"topic": kafka_topic_label(topic), "outcome": outcome})


def record_kafka_reconnect() -> None:
    with suppress(Exception):
        _counter(
            "kafka_reconnects_total",
            "Reconexiones exitosas del consumidor Kafka",
        ).add(1)


def _observe_kafka_circuit(_options):
    # Import perezoso: kafka_client importa este módulo.
    from app.services.kafka_client import kafka_client  # noqa: PLC0415

    with suppress(Exception):
        value = 1 if kafka_client.circuit_breaker_status()["open"] else 0
        return [Observation(value)]
    return [Observation(0)]


def register_observable_instruments() -> None:
    """Gauge del circuit breaker. El mismo dato que StatsD manda cada 10 s."""
    if "kafka_circuit_open" in _instruments:
        return
    with suppress(Exception):
        _instruments["kafka_circuit_open"] = _meter().create_observable_gauge(
            "kafka_circuit_open",
            callbacks=[_observe_kafka_circuit],
            description="1 si el circuit breaker de Kafka está abierto",
        )


def record_data_token_decision(outcome: str) -> None:
    if outcome not in DATA_TOKEN_OUTCOMES:
        return
    with suppress(Exception):
        _counter(
            "data_token_decisions_total",
            "Decisiones de data token",
        ).add(1, {"outcome": outcome})


def record_share_access(outcome: str) -> None:
    if outcome not in SHARE_OUTCOMES:
        return
    with suppress(Exception):
        _counter(
            "share_access_total",
            "Accesos a un enlace de compartir ubicación",
        ).add(1, {"outcome": outcome})


def record_valkey_lookup(outcome: str) -> None:
    if outcome not in VALKEY_OUTCOMES:
        return
    with suppress(Exception):
        _counter(
            "valkey_lookups_total",
            "Lookups de scope en Valkey",
        ).add(1, {"outcome": outcome})


def record_query_fanout(route: str, count: int) -> None:
    """Ancho de la consulta. La plantilla, nunca los device ids."""
    with suppress(Exception):
        _histogram(
            "query_fanout",
            "Cantidad de identificadores pedidos en una consulta",
            QUERY_FANOUT_BUCKETS,
        ).record(int(count), {"route": route})
