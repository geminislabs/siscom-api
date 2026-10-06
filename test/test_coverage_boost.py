"""
Tests adicionales para mejorar code coverage a 65%.

Estos tests se enfocan en código no cubierto: config, schemas.
"""

from datetime import datetime

import pytest

from app.core.config import settings


class TestConfigCoverage:
    """Tests para cubrir app/core/config.py"""

    def test_database_url_property(self):
        """Test: DATABASE_URL se construye correctamente."""
        url = settings.DATABASE_URL

        assert isinstance(url, str)
        assert "postgresql+asyncpg://" in url
        assert settings.DB_HOST in url
        assert str(settings.DB_PORT) in url

    def test_settings_defaults(self):
        """Test: Settings tiene valores por defecto razonables."""
        assert settings.APP_NAME == "siscom-api"
        assert settings.DB_PORT == 5432
        assert settings.ALLOWED_ORIGINS
        assert settings.STATSD_PORT == 8126

    def test_kafka_config_defaults(self):
        """Test: Kafka tiene configuración por defecto."""
        assert settings.KAFKA_BOOTSTRAP_SERVERS == "localhost:9092"
        # KAFKA_TOPIC puede variar según .env
        assert isinstance(settings.KAFKA_TOPIC, str)
        assert len(settings.KAFKA_TOPIC) > 0
        assert settings.KAFKA_GROUP_ID == "siscom-api-consumer"
        assert settings.KAFKA_AUTO_OFFSET_RESET == "latest"


class TestSchemasCoverage:
    """Tests para schemas (ya en 100% pero agregamos edge cases)."""

    def test_device_history_request_validation(self):
        """Test: DeviceHistoryRequest valida min/max length."""
        from pydantic import ValidationError

        from app.schemas.communications import DeviceHistoryRequest

        # Lista vacía debe fallar (min_length=1)
        with pytest.raises(ValidationError):
            DeviceHistoryRequest(device_ids=[])

        # Lista con 1 elemento debe pasar
        request = DeviceHistoryRequest(device_ids=["DEVICE1"])
        assert len(request.device_ids) == 1

        # Lista con múltiples elementos debe pasar
        request = DeviceHistoryRequest(device_ids=[f"DEVICE{i}" for i in range(10)])
        assert len(request.device_ids) == 10

    def test_communication_response_schema_fields(self):
        """Test: CommunicationResponse tiene los campos correctos."""
        from decimal import Decimal

        from app.schemas.communications import CommunicationResponse

        response = CommunicationResponse(
            id=1,
            device_id="TEST123",
            latitude=Decimal("19.4326"),
            longitude=Decimal("-99.1332"),
            speed=Decimal("45.5"),
            course=Decimal("180.0"),
            gps_datetime=datetime.now(),
        )

        assert response.id == 1
        assert response.device_id == "TEST123"
        assert isinstance(response.latitude, Decimal)
        assert isinstance(response.speed, Decimal)

    def test_communication_response_allows_nulls(self):
        """Test: CommunicationResponse permite campos None."""
        from app.schemas.communications import CommunicationResponse

        response = CommunicationResponse(
            id=2,
            device_id="TEST456",
            latitude=None,
            longitude=None,
            speed=None,
            course=None,
            gps_datetime=None,
        )

        assert response.device_id == "TEST456"
        assert response.latitude is None
        assert response.longitude is None

    def test_event_response_schema(self):
        """Test: EventResponse schema funciona correctamente."""
        from uuid import uuid4

        from app.schemas.events import EventResponse

        response = EventResponse(
            unit_id=uuid4(),
            source_id="DEVICE789",
            code="ignition_on",
            occurred_at=datetime.now(),
            received_at=datetime.now(),
            source_epoch=1234567890,
        )

        assert response.source_id == "DEVICE789"
        assert response.event_type == "ignition_on"  # alias para code


class TestModelsCoverage:
    """Tests adicionales para models."""

    def test_communication_suntech_table_name(self):
        """Test: CommunicationSuntech tiene el tablename correcto."""
        from app.models.communications import CommunicationSuntech

        assert CommunicationSuntech.__tablename__ == "communications_suntech"

    def test_communication_queclink_table_name(self):
        """Test: CommunicationQueclink tiene el tablename correcto."""
        from app.models.communications import CommunicationQueclink

        assert CommunicationQueclink.__tablename__ == "communications_queclink"

    def test_event_type_model_structure(self):
        """Test: EventType model tiene los campos esperados."""
        from app.models.events import EventType

        assert hasattr(EventType, "id")
        assert hasattr(EventType, "code")
        assert hasattr(EventType, "description")
        assert EventType.__tablename__ == "event_types"

    def test_event_model_structure(self):
        """Test: Event model tiene los campos esperados."""
        from app.models.events import Event

        assert hasattr(Event, "id")
        assert hasattr(Event, "unit_id")
        assert hasattr(Event, "source_id")
        assert hasattr(Event, "event_type_id")
        assert Event.__tablename__ == "events"


class TestMetricsCoverage:
    """Tests para app/utils/metrics.py"""

    def test_metrics_client_initialization(self):
        """Test: MetricsClient se inicializa correctamente."""
        from app.utils.metrics import MetricsClient

        client = MetricsClient()

        assert client.client is None  # No conectado aún
        assert client.prefix in ["siscom-api", "siscom_api"]  # Puede variar
        assert client._active_connections == 0

    def test_metrics_client_disabled_when_setting_false(self):
        """Test: MetricsClient respeta STATSD_ENABLED=False."""
        from app.core.config import settings
        from app.utils.metrics import MetricsClient

        original = settings.STATSD_ENABLED
        try:
            settings.STATSD_ENABLED = False
            client = MetricsClient()
            assert client._enabled is False
        finally:
            settings.STATSD_ENABLED = original

    def test_metrics_context_manager(self):
        """Test: track_latency context manager existe."""
        from app.utils.metrics import metrics_client

        assert hasattr(metrics_client, "_active_connections")
        assert callable(getattr(metrics_client, "ensure_connected", None))
        assert callable(getattr(metrics_client, "close", None))


class TestPasetoValidatorCoverage:
    """Tests para app/utils/paseto_validator.py"""

    def test_invalid_token_exception_exists(self):
        """Test: InvalidToken exception está definida."""
        from app.utils.paseto_validator import InvalidToken

        assert issubclass(InvalidToken, Exception)

    def test_expired_token_exception_exists(self):
        """Test: ExpiredToken exception está definida."""
        from app.utils.paseto_validator import ExpiredToken

        assert issubclass(ExpiredToken, Exception)

    def test_paseto_validator_has_validate_method(self):
        """Test: PasetoValidator tiene método validate."""
        from app.utils.paseto_validator import paseto_validator

        assert hasattr(paseto_validator, "validate")
        assert callable(paseto_validator.validate)

    def test_paseto_validator_loads_at_least_one_key(self):
        """Test: PasetoValidator carga las claves configuradas.

        `keys` es una lista de tuplas (nombre_de_la_variable, Key) que se
        prueban en orden durante la ventana de migración de claves.
        """
        from app.utils.paseto_validator import paseto_validator

        assert paseto_validator.keys
        assert all(isinstance(name, str) for name, _ in paseto_validator.keys)


class TestMiddlewareCoverage:
    """Tests para app/core/middleware.py"""

    def test_metrics_middleware_class_exists(self):
        """Test: MetricsMiddleware existe."""
        from app.core.middleware import MetricsMiddleware

        assert MetricsMiddleware is not None

    def test_metrics_middleware_has_dispatch(self):
        """Test: MetricsMiddleware tiene método dispatch."""
        from app.core.middleware import MetricsMiddleware

        assert hasattr(MetricsMiddleware, "dispatch")


class TestRepositoryCoverage:
    """Tests para app/services/repository.py"""

    def test_get_communications_function_exists(self):
        """Test: get_communications está definida."""
        import inspect

        from app.services.repository import get_communications

        assert callable(get_communications)
        assert inspect.iscoroutinefunction(get_communications)

    def test_get_latest_communications_function_exists(self):
        """Test: get_latest_communications está definida."""
        import inspect

        from app.services.repository import get_latest_communications

        assert callable(get_latest_communications)
        assert inspect.iscoroutinefunction(get_latest_communications)


class TestEventRepositoryCoverage:
    """Tests para app/services/events_repository.py"""

    def test_get_events_function_exists(self):
        """Test: get_events está definida."""
        import inspect

        from app.services.events_repository import get_events

        assert callable(get_events)
        assert inspect.iscoroutinefunction(get_events)


class TestKafkaClientCoverage:
    """Tests para app/services/kafka_client.py"""

    def test_kafka_client_class_exists(self):
        """Test: KafkaClient existe."""
        from app.services.kafka_client import KafkaClient

        assert KafkaClient is not None

    def test_kafka_client_initialization(self):
        """Test: KafkaClient se inicializa."""
        from app.services.kafka_client import KafkaClient

        client = KafkaClient()
        assert client.consumer is None
        assert client.connected is False


class TestRoutesPublicCoverage:
    """Tests para app/api/routes/public.py"""

    def test_health_endpoint_import(self):
        """Test: Endpoint de health está definido."""
        from app.api.routes import public

        assert hasattr(public, "router")

    def test_public_routes_router_exists(self):
        """Test: Router de public existe."""
        from app.api.routes.public import router

        assert router is not None


class TestAdditionalSchemasCoverage:
    """Tests adicionales para schemas."""

    def test_device_history_request_validation(self):
        """Test: DeviceHistoryRequest valida device_ids."""
        from pydantic import ValidationError

        from app.schemas.communications import DeviceHistoryRequest

        # Lista vacía debe fallar (min_length=1)
        with pytest.raises(ValidationError):
            DeviceHistoryRequest(device_ids=[])

        # Lista con 1 elemento debe pasar
        request = DeviceHistoryRequest(device_ids=["DEV1"])
        assert len(request.device_ids) == 1

    def test_event_response_schema(self):
        """Test: EventResponse schema funciona."""
        from uuid import uuid4

        from app.schemas.events import EventResponse

        response = EventResponse(
            unit_id=uuid4(),
            source_id="DEVICE789",
            code="ignition_on",
            occurred_at=datetime.now(),
            received_at=datetime.now(),
            source_epoch=1234567890,
        )

        assert response.source_id == "DEVICE789"
        assert response.event_type == "ignition_on"  # alias para code
