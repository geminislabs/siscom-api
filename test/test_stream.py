"""Tests unitarios para streaming WebSocket y routing por device_id."""

import asyncio

import pytest
from fastapi import WebSocketDisconnect

from app.api.routes.stream import (
    WebSocketManager,
    _extract_device_id_from_alerts,
    _extract_device_id_from_positions,
    _normalize_alert_message,
    _resolve_websocket_event_name,
    process_websocket_messages,
)


class FakeWebSocket:
    """WebSocket falso guiado por un guion de `receive()`.

    `receive_script` es una lista de `(delay, message)`: cada `receive()`
    espera `delay` segundos y devuelve `message`. Al agotarse el guion,
    `receive()` bloquea "para siempre" (simula un cliente en silencio).
    """

    def __init__(self, receive_script=None, fail_send=False):
        self.sent: list[dict] = []
        self._script = list(receive_script or [])
        self._fail_send = fail_send

    async def receive(self) -> dict:
        if self._script:
            delay, message = self._script.pop(0)
        else:
            delay, message = 3600, {"type": "websocket.disconnect", "code": 1000}
        if delay:
            await asyncio.sleep(delay)
        return message

    async def send_json(self, data: dict) -> None:
        if self._fail_send:
            raise RuntimeError("socket cerrado")
        self.sent.append(data)


@pytest.mark.unit
class TestWebSocketManager:
    """Valida suscripciones y publicación por device_id."""

    def test_publish_to_subscribed_device(self):
        async def run_test():
            manager = WebSocketManager()
            queues = await manager.subscribe(["dev-1", "dev-2"])

            assert len(queues) == 1

            message = {"data": {"device_id": "dev-1", "lat": 19.21}}
            await manager.publish(message, "dev-1")

            received = await queues[0].get()
            assert received == message

        asyncio.run(run_test())

    def test_publish_ignores_unsubscribed_device(self):
        async def run_test():
            manager = WebSocketManager()
            queues = await manager.subscribe(["dev-1"])

            await manager.publish({"data": {"device_id": "dev-2"}}, "dev-2")

            assert queues[0].qsize() == 0

        asyncio.run(run_test())

    def test_unsubscribe_removes_subscription(self):
        async def run_test():
            manager = WebSocketManager()
            queues = await manager.subscribe(["dev-1"])

            await manager.unsubscribe(["dev-1"], queues)
            await manager.publish({"data": {"device_id": "dev-1"}}, "dev-1")

            assert queues[0].qsize() == 0

        asyncio.run(run_test())


@pytest.mark.unit
class TestPayloadExtraction:
    """Valida extracción de device_id para ambos tipos de mensaje."""

    def test_extract_device_id_from_positions_nested_data(self):
        payload = {"data": {"device_id": "pos-dev-1"}}
        assert _extract_device_id_from_positions(payload) == "pos-dev-1"

    def test_extract_device_id_from_alerts_root(self):
        payload = {"device_id": "alert-dev-1", "alert_type": "Engine OFF"}
        assert _extract_device_id_from_alerts(payload) == "alert-dev-1"

    def test_extract_device_id_from_alerts_nested_payload(self):
        payload = {
            "payload": {
                "device_id": "alert-dev-2",
                "engine_status": "OFF",
            }
        }
        assert _extract_device_id_from_alerts(payload) == "alert-dev-2"

    def test_normalize_alert_message(self):
        raw = {"device_id": "alert-dev-1", "alert_type": "Engine OFF"}
        normalized = _normalize_alert_message(raw, "tracking/alerts")

        assert normalized["message_type"] == "alert"
        assert normalized["source_topic"] == "tracking/alerts"
        assert normalized["data"] == raw

    def test_resolve_websocket_event_name_for_alert(self):
        payload = {"message_type": "alert", "data": {"device_id": "dev-1"}}
        assert _resolve_websocket_event_name(payload) == "alert"

    def test_resolve_websocket_event_name_for_standard_message(self):
        payload = {"data": {"device_id": "dev-1"}}
        assert _resolve_websocket_event_name(payload) == "message"


@pytest.mark.unit
class TestProcessWebsocketMessages:
    """Valida el loop que envía eventos y detecta desconexiones del cliente."""

    def test_stops_immediately_on_client_disconnect(self):
        """Un frame de disconnect debe cortar el loop (fin de fuga de subs)."""

        async def run_test():
            ws = FakeWebSocket([(0, {"type": "websocket.disconnect", "code": 1001})])

            with pytest.raises(WebSocketDisconnect):
                await process_websocket_messages(ws, [])

            assert ws.sent == []

        asyncio.run(run_test())

    def test_delivers_queue_message_then_stops_on_disconnect(self):
        """Los mensajes de cola se envían; luego el disconnect corta el loop."""

        async def run_test():
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put({"data": {"device_id": "dev-1", "lat": 19.21}})
            ws = FakeWebSocket([(0.05, {"type": "websocket.disconnect", "code": 1000})])

            with pytest.raises(WebSocketDisconnect):
                await process_websocket_messages(ws, [queue])

            assert len(ws.sent) == 1
            assert ws.sent[0]["event"] == "message"
            assert ws.sent[0]["data"] == {"data": {"device_id": "dev-1", "lat": 19.21}}

        asyncio.run(run_test())

    def test_incoming_frame_is_not_forwarded_as_event(self):
        """Un frame entrante del cliente se ignora, no se reenvía como evento."""

        async def run_test():
            queue: asyncio.Queue = asyncio.Queue()
            ws = FakeWebSocket(
                [
                    (0, {"type": "websocket.receive", "text": "hola"}),
                    (0.05, {"type": "websocket.disconnect", "code": 1000}),
                ]
            )

            with pytest.raises(WebSocketDisconnect):
                await process_websocket_messages(ws, [queue])

            assert ws.sent == []

        asyncio.run(run_test())

    def test_send_failure_raises_disconnect(self):
        """Si falla el envío (socket cerrado) se propaga WebSocketDisconnect."""

        async def run_test():
            queue: asyncio.Queue = asyncio.Queue()
            await queue.put({"data": {"device_id": "dev-1"}})
            ws = FakeWebSocket(fail_send=True)

            with pytest.raises(WebSocketDisconnect):
                await process_websocket_messages(ws, [queue])

        asyncio.run(run_test())
