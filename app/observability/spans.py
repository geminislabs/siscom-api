"""Spans manuales. Fail-open. Un WebSocket es una sesión, no un mensaje."""

from __future__ import annotations

import time
from contextlib import contextmanager, suppress

from opentelemetry import trace

from app.observability.metrics import record_ws_connection_delta, ws_route_label

_WS_OUTCOMES = frozenset({"ok", "auth_rejected", "client_disconnect", "error"})


def _tracer():
    return trace.get_tracer("siscom-api")


def set_span_outcome(span, outcome: str) -> None:
    if span is None:
        return
    with suppress(Exception):
        span.set_attribute("outcome", outcome)


@contextmanager
def valkey_resolve_span():
    """Un span por lookup de request o de handshake. No por trama del stream.

    El cierre del span es fail-open: un fallo de telemetría no puede convertir
    una resolución de Valkey en error ni tapar `ScopeStoreUnavailable`.
    """
    span_cm = None
    try:
        span_cm = _tracer().start_as_current_span(
            "valkey.resolve",
            record_exception=False,
            set_status_on_exception=False,
        )
        span = span_cm.__enter__()
    except Exception:
        yield None
        return
    try:
        try:
            yield span
        except Exception:
            set_span_outcome(span, "unavailable")
            raise
    finally:
        with suppress(Exception):
            span_cm.__exit__(None, None, None)


class WsSession:
    """Span `ws.session` de accept a disconnect. `close` es idempotente."""

    def __init__(self, http_route: str, route_label: str) -> None:
        self.http_route = http_route
        self.route_label = ws_route_label(route_label)
        self._outcome = "ok"
        self._closed = False
        self._accepted = False
        self._start = time.perf_counter()
        self._span = None
        try:
            self._span = _tracer().start_span("ws.session")
            self._span.set_attribute("http_route", http_route)
        except Exception:
            self._span = None

    def mark_accepted(self) -> None:
        if self._accepted:
            return
        self._accepted = True
        record_ws_connection_delta(self.route_label, 1)

    def set_outcome(self, outcome: str) -> None:
        if self._closed or outcome not in _WS_OUTCOMES:
            return
        self._outcome = outcome

    def note_disconnect(self) -> None:
        """El cierre del cliente no pisa un rechazo o un error ya decidido."""
        if self._outcome == "ok":
            self.set_outcome("client_disconnect")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with suppress(Exception):
            if self._accepted:
                record_ws_connection_delta(self.route_label, -1)
        span = self._span
        self._span = None
        if span is None:
            return
        with suppress(Exception):
            duration_ms = (time.perf_counter() - self._start) * 1000
            span.set_attribute("outcome", self._outcome)
            span.set_attribute("http_route", self.http_route)
            span.set_attribute("duration_ms", duration_ms)
            span.end()


def open_ws_session(http_route: str, route_label: str) -> WsSession:
    try:
        return WsSession(http_route, route_label)
    except Exception:
        session = WsSession.__new__(WsSession)
        session.http_route = http_route
        session.route_label = ws_route_label(route_label)
        session._outcome = "ok"
        session._closed = False
        session._accepted = False
        session._start = time.perf_counter()
        session._span = None
        return session
