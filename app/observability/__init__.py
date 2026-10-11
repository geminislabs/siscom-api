"""Observabilidad operacional (OpenTelemetry). Sin PII."""

from app.observability.init import (
    get_logger_provider,
    get_meter_provider,
    get_tracer_provider,
    instrument_app,
    reset_telemetry_for_tests,
    setup_telemetry,
    telemetry_enabled,
)
from app.observability.logging import configure_json_logging
from app.observability.metrics import (
    record_data_token_decision,
    record_http_server_duration,
    record_kafka_message,
    record_kafka_reconnect,
    record_query_fanout,
    record_share_access,
    record_valkey_lookup,
    record_ws_connection_delta,
    record_ws_message_dropped,
    record_ws_message_published,
)

__all__ = [
    "configure_json_logging",
    "get_logger_provider",
    "get_meter_provider",
    "get_tracer_provider",
    "instrument_app",
    "record_data_token_decision",
    "record_http_server_duration",
    "record_kafka_message",
    "record_kafka_reconnect",
    "record_query_fanout",
    "record_share_access",
    "record_valkey_lookup",
    "record_ws_connection_delta",
    "record_ws_message_dropped",
    "record_ws_message_published",
    "reset_telemetry_for_tests",
    "setup_telemetry",
    "telemetry_enabled",
]
