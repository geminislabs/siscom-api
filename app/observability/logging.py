"""JSON formatter estructurado: única fuente del root logger."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from app.observability.request_id import current_request_id

# Fragmentos largos: coincidencia por substring. Los cortos (`lat`, `lng`,
# `otp`, `rfc`) van por componente, porque "lat" dentro de "platform" no es
# una coordenada.
SCRUB_KEY_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "api_key",
    "email",
    "latitude",
    "longitude",
    "curp",
)

SCRUB_KEY_PARTS = frozenset({"lat", "lng", "otp", "rfc"})

REDACTED = "[REDACTED]"

_TOKEN_QUERY = re.compile(r"(?i)(token=)[^&\s#]+")
_KEY_PARTS = re.compile(r"[^a-z0-9]+")

_RECORD_SKIP = {
    "name",
    "msg",
    "args",
    "created",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "exc_info",
    "exc_text",
    "thread",
    "threadName",
    "message",
    "taskName",
    "asctime",
    "extra_data",
}


class _LoggingState:
    configured: bool = False


_logging_state = _LoggingState()

_MIN_ACCESS_ARGS = 3
_ACCESS_PATH_INDEX = 2


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if any(fragment in lowered for fragment in SCRUB_KEY_FRAGMENTS):
        return True
    parts = [part for part in _KEY_PARTS.split(lowered) if part]
    return any(part in SCRUB_KEY_PARTS for part in parts)


def scrub_query_token(value: str) -> str:
    """Reemplaza `token=` en una query antes de exportar. El resto se queda."""
    return _TOKEN_QUERY.sub(r"\1" + REDACTED, value)


def scrub_value(key: str, value: Any) -> Any:
    if _key_is_sensitive(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: scrub_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_value(key, item) for item in value]
    if isinstance(value, str) and "token=" in value.lower():
        return scrub_query_token(value)
    return value


def scrub_span(span: Any) -> None:
    """Tapa atributos sensibles y `token=` de la query antes de exportar."""
    attributes = getattr(span, "_attributes", None)
    if attributes is None:
        return
    try:
        keys = list(attributes.keys())
    except Exception:
        return
    for key in keys:
        try:
            value = attributes[key]
            attributes[key] = scrub_value(str(key), value)
        except Exception:
            continue


def _active_trace_ids() -> tuple[str, str]:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx or not ctx.is_valid:
        return "", ""
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


class JSONFormatter(logging.Formatter):
    """Formatter JSON con trace_id, scrubber y campos de servicio."""

    def __init__(
        self,
        service_name: str = "siscom-api",
        deploy_env: str = "unknown",
    ) -> None:
        super().__init__()
        self.service_name = service_name
        self.deploy_env = deploy_env

    def format(self, record: logging.LogRecord) -> str:
        trace_id, span_id = _active_trace_ids()
        message = record.getMessage()
        if "token=" in message.lower():
            message = scrub_query_token(message)
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
            "service.name": self.service_name,
            "deployment.environment": self.deploy_env,
            "trace_id": trace_id,
            "span_id": span_id,
            "request_id": current_request_id(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_data["extra"] = scrub_value("extra", record.extra_data)

        for key, value in record.__dict__.items():
            if key in _RECORD_SKIP or key.startswith("_"):
                continue
            log_data[key] = scrub_value(key, value)

        return json.dumps(log_data, ensure_ascii=False, default=str)


class HealthCheckFilter(logging.Filter):
    """Suprime los logs de acceso del endpoint /health cuando son exitosos."""

    def filter(self, record: logging.LogRecord) -> bool:
        path = ""
        if (
            record.name == "uvicorn.access"
            and isinstance(record.args, tuple)
            and len(record.args) >= _MIN_ACCESS_ARGS
        ):
            path = str(record.args[_ACCESS_PATH_INDEX]).split("?", 1)[0]
        if path == "/health":
            return False

        message = record.getMessage()
        return not (" /health" in message and "HTTP/" in message)


class ScrubFilter(logging.Filter):
    """Redacta claves sensibles y `token=` antes de que el record se exporte."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and "token=" in record.msg.lower():
            record.msg = scrub_query_token(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                (
                    scrub_query_token(item)
                    if isinstance(item, str) and "token=" in item.lower()
                    else item
                )
                for item in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: scrub_value(str(key), value) for key, value in record.args.items()
            }
        for key, value in list(record.__dict__.items()):
            if key in _RECORD_SKIP or key.startswith("_"):
                continue
            if _key_is_sensitive(key) or (
                isinstance(value, str) and "token=" in value.lower()
            ):
                setattr(record, key, scrub_value(key, value))
        return True


def _replace_filter(logger: logging.Logger, kind: type[logging.Filter]) -> None:
    logger.filters = [item for item in logger.filters if not isinstance(item, kind)]
    logger.addFilter(kind())


def configure_json_logging(settings: Any, level: str | None = None) -> None:
    """Configura el root logger. Idempotente. Corre aunque no haya Collector."""
    if _logging_state.configured and level is None:
        return

    deploy_env = getattr(settings, "DEPLOY_ENV", None) or "unknown"
    raw_level = (level or getattr(settings, "LOG_LEVEL", "INFO") or "INFO").upper()
    if deploy_env != "local" and raw_level == "DEBUG":
        raw_level = "INFO"

    numeric = getattr(logging, raw_level, logging.INFO)
    service_name = getattr(settings, "SERVICE_NAME", "siscom-api")

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JSONFormatter(service_name=service_name, deploy_env=deploy_env)
    )
    root_logger.addHandler(handler)
    _replace_filter(root_logger, ScrubFilter)

    logging.getLogger("uvicorn").setLevel(logging.INFO)
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_access.setLevel(logging.INFO)
    _replace_filter(uvicorn_access, HealthCheckFilter)
    _replace_filter(uvicorn_access, ScrubFilter)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _logging_state.configured = True


def reset_logging_for_tests() -> None:
    _logging_state.configured = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
