"""Tests unitarios para el keep-alive del WebSocket público (share-location)."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from app.api.routes.public import _send_keepalive
from app.core.config import settings


class FakeWebSocket:
    """WebSocket falso que registra envíos y el cierre."""

    def __init__(self, fail_send: bool = False):
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self._fail_send = fail_send

    async def send_json(self, data: dict) -> None:
        if self._fail_send:
            raise RuntimeError("socket cerrado")
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


@pytest.mark.unit
class TestPublicKeepalive:
    """El keep-alive debe ser PERIÓDICO (no one-shot) y cerrar al expirar."""

    def test_sends_periodic_pings_then_closes_on_expiry(self, monkeypatch):
        monkeypatch.setattr(settings, "WEBSOCKET_KEEPALIVE_SECS", 0.02)

        async def run_test():
            ws = FakeWebSocket()
            expires_at = datetime.now(UTC) + timedelta(seconds=0.05)

            await _send_keepalive(ws, expires_at)

            pings = [m for m in ws.sent if m["event"] == "ping"]
            expired = [m for m in ws.sent if m["event"] == "expired"]

            # Al menos un ping antes de expirar (prueba que NO es one-shot).
            assert len(pings) >= 1
            assert len(expired) == 1
            assert ws.closed is not None
            assert ws.closed[0] == 1000

        asyncio.run(run_test())

    def test_stops_when_send_fails(self, monkeypatch):
        monkeypatch.setattr(settings, "WEBSOCKET_KEEPALIVE_SECS", 0.01)

        async def run_test():
            ws = FakeWebSocket(fail_send=True)
            expires_at = datetime.now(UTC) + timedelta(seconds=100)

            # No debe colgarse: el fallo de envío corta el bucle limpiamente.
            await asyncio.wait_for(_send_keepalive(ws, expires_at), timeout=1.0)

        asyncio.run(run_test())

    def test_handles_cancellation(self, monkeypatch):
        monkeypatch.setattr(settings, "WEBSOCKET_KEEPALIVE_SECS", 5)

        async def run_test():
            ws = FakeWebSocket()
            expires_at = datetime.now(UTC) + timedelta(seconds=100)

            task = asyncio.create_task(_send_keepalive(ws, expires_at))
            await asyncio.sleep(0.01)
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

            assert ws.sent == []

        asyncio.run(run_test())
