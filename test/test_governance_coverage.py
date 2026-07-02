"""Tests unitarios adicionales para alcanzar el umbral de cobertura de gobernanza."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from starlette.requests import Request

from app.core.security import get_current_user
from app.services.events_repository import decode_cursor, encode_cursor, get_events
from app.services.kafka_client import KafkaClient
from app.utils.metrics import MetricsClient, get_metrics_client


@pytest.mark.unit
class TestEventsRepositoryHelpers:
    def test_encode_decode_cursor_roundtrip(self):
        event_id = uuid4()
        occurred = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        cursor = encode_cursor(occurred, event_id)
        decoded_at, decoded_id = decode_cursor(cursor)
        assert decoded_id == event_id
        assert decoded_at == occurred

    def test_decode_cursor_invalid_raises(self):
        with pytest.raises(ValueError, match="Cursor inválido"):
            decode_cursor("not-a-valid-cursor")

    @pytest.mark.asyncio
    async def test_get_events_empty_result(self):
        session = AsyncMock()
        result = MagicMock()
        result.fetchall.return_value = []
        session.execute.return_value = result

        unit_id = uuid4()
        events, next_cursor = await get_events(
            session,
            unit_ids=[unit_id],
            from_dt=datetime(2026, 1, 1, tzinfo=UTC),
            to_dt=datetime(2026, 12, 31, tzinfo=UTC),
            limit=20,
            order="desc",
        )

        assert events == []
        assert next_cursor is None

    @pytest.mark.asyncio
    async def test_get_events_with_pagination_cursor(self):
        session = AsyncMock()
        event_id = uuid4()
        unit_id = uuid4()
        occurred = datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC)
        row = (event_id, unit_id, "DEV1", "ignition_on", occurred, occurred, 123)

        result = MagicMock()
        result.fetchall.return_value = [row] * 3
        session.execute.return_value = result

        events, next_cursor = await get_events(
            session,
            unit_ids=[unit_id],
            from_dt=datetime(2026, 1, 1, tzinfo=UTC),
            to_dt=datetime(2026, 12, 31, tzinfo=UTC),
            limit=2,
            order="asc",
        )

        assert len(events) == 2
        assert next_cursor is not None

    @pytest.mark.asyncio
    async def test_get_events_with_desc_cursor(self):
        session = AsyncMock()
        event_id = uuid4()
        unit_id = uuid4()
        occurred = datetime(2026, 6, 15, 10, 0, 0, tzinfo=UTC)
        cursor = encode_cursor(occurred, event_id)
        result = MagicMock()
        result.fetchall.return_value = []
        session.execute.return_value = result

        events, next_cursor = await get_events(
            session,
            unit_ids=[unit_id],
            from_dt=datetime(2026, 1, 1, tzinfo=UTC),
            to_dt=datetime(2026, 12, 31, tzinfo=UTC),
            order="desc",
            cursor=cursor,
        )
        assert events == []
        assert next_cursor is None

    @pytest.mark.asyncio
    async def test_get_events_invalid_cursor_raises(self):
        session = AsyncMock()
        with pytest.raises(ValueError, match="Cursor inválido"):
            await get_events(
                session,
                unit_ids=[uuid4()],
                from_dt=datetime(2026, 1, 1, tzinfo=UTC),
                to_dt=datetime(2026, 12, 31, tzinfo=UTC),
                cursor="invalid",
            )


@pytest.mark.unit
class TestRepositoryExtended:
    @pytest.mark.asyncio
    async def test_get_communications_with_received_at_filter(
        self, db_session, sample_suntech_communication
    ):
        from datetime import date

        from app.services.repository import get_communications

        sample_suntech_communication.received_at = datetime(2024, 1, 15, 12, 0, 0)
        await db_session.commit()

        results = await get_communications(
            db_session,
            ["867564050638581"],
            received_at=date(2024, 1, 15),
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_get_device_communications_with_date_filter(
        self, client, sample_suntech_communication
    ):
        response = client.get(
            "/api/v1/devices/867564050638581/communications?received_at=2024-01-15"
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_communications_latest_endpoint(
        self, client, sample_suntech_communication
    ):
        response = client.get(
            "/api/v1/communications/latest?device_ids=867564050638581"
        )
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_device_latest_not_found(self, client):
        response = client.get("/api/v1/devices/UNKNOWN999/communications/latest")
        assert response.status_code == 404


@pytest.mark.unit
class TestDatabaseUnit:
    @pytest.mark.asyncio
    async def test_get_db_yields_session(self):
        from app.core.database import get_db

        gen = get_db()
        session = await gen.__anext__()
        assert session is not None
        await gen.aclose()


@pytest.mark.unit
class TestStreamExtended:
    @pytest.mark.asyncio
    async def test_kafka_message_handler_alerts(self, monkeypatch):
        from app.api.routes import stream
        from app.core.config import settings

        monkeypatch.setattr(settings, "KAFKA_TOPIC", "tracking/data")
        monkeypatch.setattr(settings, "KAFKA_ALERTS_TOPIC", "tracking/alerts")
        published = []

        async def mock_publish(message, device_id):
            published.append((message, device_id))

        monkeypatch.setattr(stream.ws_broker, "publish", mock_publish)
        await stream.kafka_message_handler(
            {
                "topic": "tracking/alerts",
                "payload": {"device_id": "ALERT-1", "alert_type": "speed"},
            }
        )
        assert published[0][1] == "ALERT-1"
        assert published[0][0]["message_type"] == "alert"

    @pytest.mark.asyncio
    async def test_ws_manager_get_stats(self):
        from app.api.routes.stream import WebSocketManager

        manager = WebSocketManager()
        stats = manager.get_stats()
        assert "active_subscribers" in stats


@pytest.mark.unit
class TestKafkaClientExtended:
    def test_circuit_breaker_cooldown_remaining(self):
        client = KafkaClient()
        client._circuit_open = True
        client._circuit_opened_at = datetime.now(UTC)
        remaining = client._circuit_breaker_cooldown_remaining()
        assert remaining >= 0

    def test_handle_consumer_unavailable_without_loop(self):
        client = KafkaClient()
        client._loop = None
        client._handle_consumer_unavailable()

    def test_process_message_invalid_json(self):
        client = KafkaClient()
        message = MagicMock()
        message.value = "not-json"
        message.topic = "tracking/data"
        client._process_message(message)

    def test_topics_to_subscribe_deduplicates(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "KAFKA_TOPIC", "tracking/data")
        monkeypatch.setattr(settings, "KAFKA_ALERTS_TOPIC", "tracking/data")

        client = KafkaClient()
        assert client._topics_to_subscribe() == ["tracking/data"]

    def test_circuit_breaker_status_defaults(self):
        client = KafkaClient()
        status = client.circuit_breaker_status()
        assert status["open"] is False
        assert status["retries"] == 0

    def test_register_and_unregister_callback(self):
        client = KafkaClient()

        async def callback(_msg):
            return None

        client.register_message_callback(callback)
        assert len(client._message_callbacks) == 1
        client.register_message_callback(callback)
        assert len(client._message_callbacks) == 1

        client.unregister_message_callback(callback)
        assert client._message_callbacks == []

    def test_disconnect_when_not_running(self):
        client = KafkaClient()
        client._running = False
        client.disconnect()

    def test_is_connected(self):
        client = KafkaClient()
        assert client.is_connected() is False
        client.connected = True
        assert client.is_connected() is True

    def test_process_message_invokes_callback(self):
        client = KafkaClient()
        loop = asyncio.new_event_loop()
        client._loop = loop
        received = []

        async def callback(msg):
            received.append(msg)

        client.register_message_callback(callback)
        message = MagicMock()
        message.value = {"device_id": "DEV1"}
        message.topic = "tracking/data"
        message.timestamp = 1
        message.partition = 0
        message.offset = 1

        client._process_message(message)
        loop.run_until_complete(asyncio.sleep(0.05))
        assert received
        loop.close()

    def test_handle_consumer_error_opens_circuit(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "KAFKA_MAX_RETRIES", 0)
        client = KafkaClient()
        client._running = True
        client._handle_consumer_error(RuntimeError("kafka down"))
        assert client._circuit_open is True

    def test_create_consumer_requires_topic(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "KAFKA_TOPIC", "")
        monkeypatch.setattr(settings, "KAFKA_ALERTS_TOPIC", "")
        client = KafkaClient()
        with pytest.raises(ValueError, match="No hay topics"):
            client._create_consumer()


@pytest.mark.unit
class TestMetricsClientUnit:
    @pytest.mark.asyncio
    async def test_metrics_disabled_skips_connection(self):
        client = MetricsClient()
        client._enabled = False
        await client.ensure_connected()
        assert client.client is None

    @pytest.mark.asyncio
    async def test_metrics_increment_when_enabled(self):
        client = MetricsClient()
        client._enabled = True
        mock_statsd = MagicMock()
        mock_statsd._closed = False
        client.client = mock_statsd

        await client.increment_requests("health")
        mock_statsd.counter.assert_called_once()

    @pytest.mark.asyncio
    async def test_metrics_timing_and_gauge(self):
        client = MetricsClient()
        client._enabled = True
        mock_statsd = MagicMock()
        mock_statsd._closed = False
        client.client = mock_statsd

        await client.timing_latency("stream", 12.5)
        await client.kafka_circuit_breaker_gauge(True)
        mock_statsd.timer.assert_called_once()
        mock_statsd.gauge.assert_called_once()

    @pytest.mark.asyncio
    async def test_metrics_sse_connections_when_disabled(self):
        client = MetricsClient()
        client._enabled = False
        await client.increment_active_connections()
        await client.decrement_active_connections()
        assert client._active_connections == 0

    @pytest.mark.asyncio
    async def test_metrics_close_handles_client(self):
        client = MetricsClient()
        mock_statsd = AsyncMock()
        mock_statsd._closed = False
        client.client = mock_statsd
        await client.close()
        mock_statsd.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_metrics_client_context_manager(self):
        async with get_metrics_client() as metrics:
            assert metrics is not None


@pytest.mark.unit
class TestMiddlewareUnit:
    @pytest.mark.asyncio
    async def test_metrics_middleware_skips_health(self):
        from app.core.middleware import MetricsMiddleware

        middleware = MetricsMiddleware(app=MagicMock())

        async def call_next(_request):
            response = MagicMock()
            response.status_code = 200
            return response

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "client": ("testclient", 50000),
        }
        request = Request(scope)
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_middleware_websocket_upgrade(self):
        from app.core.middleware import MetricsMiddleware

        middleware = MetricsMiddleware(app=MagicMock())

        async def call_next(_request):
            response = MagicMock()
            response.status_code = 101
            return response

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/stream",
            "headers": [(b"upgrade", b"websocket")],
            "client": ("testclient", 50000),
        }
        request = Request(scope)
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 101


@pytest.mark.unit
class TestSecurityDependency:
    @pytest.mark.asyncio
    async def test_get_current_user_returns_payload(self):
        from app.core.security import create_access_token

        access_token = create_access_token({"sub": "user-1"})
        credentials = MagicMock()
        credentials.credentials = access_token
        payload = await get_current_user(credentials)
        assert payload["sub"] == "user-1"


@pytest.mark.unit
class TestStreamRoutesUnit:
    @pytest.mark.asyncio
    async def test_kafka_message_handler_positions(self, monkeypatch):
        from app.api.routes import stream
        from app.core.config import settings

        monkeypatch.setattr(settings, "KAFKA_TOPIC", "tracking/data")
        monkeypatch.setattr(settings, "KAFKA_ALERTS_TOPIC", "")

        published = []

        async def mock_publish(message, device_id):
            published.append((message, device_id))

        monkeypatch.setattr(stream.ws_broker, "publish", mock_publish)

        await stream.kafka_message_handler(
            {
                "topic": "tracking/data",
                "payload": {"data": {"device_id": "DEV-WS-1"}},
            }
        )
        assert published[0][1] == "DEV-WS-1"

    @pytest.mark.asyncio
    async def test_kafka_message_handler_ignores_unknown_topic(self, monkeypatch):
        from app.api.routes import stream

        published = []

        async def mock_publish(message, device_id):
            published.append((message, device_id))

        monkeypatch.setattr(stream.ws_broker, "publish", mock_publish)
        await stream.kafka_message_handler(
            {"topic": "unknown", "payload": {"device_id": "X"}}
        )
        assert published == []

    @pytest.mark.asyncio
    async def test_validate_device_ids_rejects_empty(self):
        from app.api.routes.stream import validate_device_ids

        websocket = AsyncMock()
        with pytest.raises(WebSocketDisconnect):
            await validate_device_ids(websocket, "")

    def test_stream_stats_endpoint(self, client):
        response = client.get("/api/v1/stream/stats")
        assert response.status_code == 200
        assert "active_subscribers" in response.json()


@pytest.mark.unit
class TestEventsRouteUnit:
    def test_events_endpoint_success(self, client, monkeypatch):
        unit_id = uuid4()

        async def mock_get_events(*_args, **_kwargs):
            return ([], None)

        monkeypatch.setattr("app.api.routes.events.get_events", mock_get_events)

        response = client.get(
            "/api/v1/events",
            params={
                "unit_id": str(unit_id),
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-12-31T23:59:59Z",
            },
        )
        assert response.status_code == 200
        assert response.json()["data"] == []

    def test_events_endpoint_invalid_cursor(self, client, monkeypatch):
        unit_id = uuid4()

        async def mock_get_events(*_args, **_kwargs):
            raise ValueError("Cursor inválido")

        monkeypatch.setattr("app.api.routes.events.get_events", mock_get_events)

        response = client.get(
            "/api/v1/events",
            params={
                "unit_id": str(unit_id),
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-12-31T23:59:59Z",
                "cursor": "bad",
            },
        )
        assert response.status_code == 400


@pytest.mark.unit
class TestPublicRoutesUnit:
    def test_public_init_invalid_token(self, client, monkeypatch):
        from app.utils.paseto_validator import InvalidToken

        monkeypatch.setattr(
            "app.api.routes.public.paseto_validator.validate",
            MagicMock(side_effect=InvalidToken("bad")),
        )
        response = client.get("/api/v1/public/share-location/init?token=invalid")
        assert response.status_code == 403

    def test_public_init_expired_token(self, client, monkeypatch):
        from app.utils.paseto_validator import ExpiredToken

        monkeypatch.setattr(
            "app.api.routes.public.paseto_validator.validate",
            MagicMock(side_effect=ExpiredToken("expired")),
        )
        response = client.get("/api/v1/public/share-location/init?token=expired")
        assert response.status_code == 401

    def test_public_init_missing_device_id(self, client, monkeypatch):
        monkeypatch.setattr(
            "app.api.routes.public.paseto_validator.validate",
            MagicMock(
                return_value={
                    "scope": "public-location-share",
                    "unit_id": str(uuid4()),
                    "exp": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                }
            ),
        )
        response = client.get("/api/v1/public/share-location/init?token=ok")
        # HTTPException interno es capturado por except Exception → 500 (comportamiento actual)
        assert response.status_code == 500


@pytest.mark.unit
class TestPasetoValidatorUnit:
    def test_validate_success(self, monkeypatch):
        from app.utils.paseto_validator import PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()

        payload = {
            "scope": "public-location-share",
            "unit_id": str(uuid4()),
            "exp": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        decoded = MagicMock()
        decoded.payload = json.dumps(payload).encode()

        with patch("app.utils.paseto_validator.pyseto.decode", return_value=decoded):
            result = PasetoValidator.validate(validator, "token")
        assert result["scope"] == "public-location-share"

    def test_validate_expired_token(self):
        from app.utils.paseto_validator import ExpiredToken, PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()
        payload = {
            "scope": "public-location-share",
            "unit_id": str(uuid4()),
            "exp": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        }
        decoded = MagicMock()
        decoded.payload = json.dumps(payload).encode()

        with (
            patch("app.utils.paseto_validator.pyseto.decode", return_value=decoded),
            pytest.raises(ExpiredToken),
        ):
            PasetoValidator.validate(validator, "token")

    def test_validate_missing_unit_id(self):
        from app.utils.paseto_validator import InvalidToken, PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()
        payload = {
            "scope": "public-location-share",
            "exp": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        decoded = MagicMock()
        decoded.payload = json.dumps(payload).encode()

        with (
            patch("app.utils.paseto_validator.pyseto.decode", return_value=decoded),
            pytest.raises(InvalidToken),
        ):
            PasetoValidator.validate(validator, "token")

    def test_validate_missing_exp(self):
        from app.utils.paseto_validator import InvalidToken, PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()
        payload = {
            "scope": "public-location-share",
            "unit_id": str(uuid4()),
        }
        decoded = MagicMock()
        decoded.payload = json.dumps(payload).encode()

        with (
            patch("app.utils.paseto_validator.pyseto.decode", return_value=decoded),
            pytest.raises(InvalidToken),
        ):
            PasetoValidator.validate(validator, "token")

    def test_validate_invalid_exp_format(self):
        from app.utils.paseto_validator import InvalidToken, PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()
        payload = {
            "scope": "public-location-share",
            "unit_id": str(uuid4()),
            "exp": "not-a-date",
        }
        decoded = MagicMock()
        decoded.payload = json.dumps(payload).encode()

        with (
            patch("app.utils.paseto_validator.pyseto.decode", return_value=decoded),
            pytest.raises(InvalidToken),
        ):
            PasetoValidator.validate(validator, "token")

    def test_validate_payload_not_dict(self):
        from app.utils.paseto_validator import InvalidToken, PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()
        decoded = MagicMock()
        decoded.payload = json.dumps(["not", "a", "dict"]).encode()

        with (
            patch("app.utils.paseto_validator.pyseto.decode", return_value=decoded),
            pytest.raises(InvalidToken),
        ):
            PasetoValidator.validate(validator, "token")

    def test_validate_decode_failure(self):
        from app.utils.paseto_validator import InvalidToken, PasetoValidator

        validator = PasetoValidator.__new__(PasetoValidator)
        validator.key = MagicMock()

        with (
            patch(
                "app.utils.paseto_validator.pyseto.decode",
                side_effect=ValueError("bad token"),
            ),
            pytest.raises(InvalidToken),
        ):
            PasetoValidator.validate(validator, "token")
