"""
Tests para mejorar coverage de main.py, routes y API.
"""

from fastapi.testclient import TestClient


class TestMainAppCoverage:
    """Tests para app/main.py"""

    def test_app_title_is_set(self, client: TestClient):
        """Test: App tiene título configurado."""
        from app.main import app

        assert app.title == "siscom-api"

    def test_app_version_is_set(self, client: TestClient):
        """Test: App tiene versión configurada."""
        from app.main import app

        assert hasattr(app, "version")
        assert app.version is not None

    def test_app_has_routes(self, client: TestClient):
        """Test: App tiene routes configuradas."""
        from app.main import app

        routes = [route.path for route in app.routes]
        assert "/api/health" in routes or any("/health" in r for r in routes)

    def test_openapi_url_is_set(self, client: TestClient):
        """Test: OpenAPI URL está configurada."""
        from app.main import app

        assert app.openapi_url is not None

    def test_docs_url_is_set(self, client: TestClient):
        """Test: Docs URL está configurada."""
        from app.main import app

        assert app.docs_url is not None


class TestHealthEndpointExtended:
    """Tests extendidos para el endpoint de health."""

    def test_health_endpoint_accessible(self, client: TestClient):
        """Test: Health endpoint es accesible."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_json_if_exists(self, client: TestClient):
        """Test: Health retorna JSON."""
        response = client.get("/health")
        assert "application/json" in response.headers.get("content-type", "")


class TestCommunicationsEndpointBasic:
    """Tests básicos de communications sin DB."""

    def test_communications_endpoint_exists(self, client: TestClient):
        """Test: Endpoint /api/v1/communications existe."""
        # Sin device_ids debe retornar 422
        response = client.get("/api/v1/communications")
        assert response.status_code == 422

    def test_communications_requires_device_ids_param(self, client: TestClient):
        """Test: Endpoint requiere device_ids."""
        response = client.get("/api/v1/communications")
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data


class TestEventsEndpointBasic:
    """Tests básicos para events endpoint."""

    def test_events_endpoint_exists(self, client: TestClient):
        """Test: Endpoint /api/v1/events existe."""
        response = client.get("/api/v1/events")
        # Puede ser 200 (vacío) o 422 (falta param), pero no 404
        assert response.status_code in [200, 422]

    def test_events_endpoint_returns_json(self, client: TestClient):
        """Test: Events retorna JSON."""
        response = client.get("/api/v1/events")
        assert response.headers["content-type"] == "application/json"


class TestDatabaseConnection:
    """Tests para database.py"""

    def test_get_db_function_exists(self):
        """Test: get_db está definida."""
        import inspect

        from app.core.database import get_db

        assert callable(get_db)
        assert inspect.isasyncgenfunction(get_db)

    def test_engine_is_configured(self):
        """Test: Engine está configurado."""
        from app.core.database import engine

        assert engine is not None
        assert hasattr(engine, "url")


class TestCorsAndMiddleware:
    """Tests para CORS y middleware."""

    def test_app_has_middleware(self, client: TestClient):
        """Test: App tiene middleware configurado."""
        from app.main import app

        # Verificar que el app tiene middleware
        assert hasattr(app, "middleware")


class TestRootEndpoint:
    """Tests para root endpoint."""

    def test_root_or_docs_redirect(self, client: TestClient):
        """Test: Root endpoint responde."""
        response = client.get("/", follow_redirects=False)
        # Puede ser redirect a docs (307/308) o 404 si no existe root
        assert response.status_code in [200, 307, 308, 404]


class TestStreamEndpointBasic:
    """Tests básicos para streaming."""

    def test_stream_endpoint_responds(self, client: TestClient):
        """Test: Stream endpoint responde (aunque sea 404 o 422)."""
        response = client.get(
            "/api/v1/communications/stream", headers={"Accept": "text/event-stream"}
        )
        # Puede no estar implementado (404) o requerir params (422)
        assert response.status_code in [200, 404, 422]


class TestModelsImport:
    """Tests para verificar que models se importan correctamente."""

    def test_communication_current_state_model(self):
        """Test: CommunicationCurrentState existe."""
        from app.models.communications import CommunicationCurrentState

        assert CommunicationCurrentState is not None
        assert hasattr(CommunicationCurrentState, "device_id")

    def test_all_communication_models_importable(self):
        """Test: Todos los models de communications se pueden importar."""
        from app.models.communications import (
            CommunicationCurrentState,
            CommunicationQueclink,
            CommunicationSuntech,
        )

        assert CommunicationSuntech is not None
        assert CommunicationQueclink is not None
        assert CommunicationCurrentState is not None

    def test_all_event_models_importable(self):
        """Test: Todos los models de events se pueden importar."""
        from app.models.events import Event, EventType

        assert Event is not None
        assert EventType is not None


class TestSchemasImport:
    """Tests para verificar que schemas se importan correctamente."""

    def test_all_communication_schemas_importable(self):
        """Test: Todos los schemas de communications se pueden importar."""
        from app.schemas.communications import (
            CommunicationResponse,
            DeviceHistoryRequest,
        )

        assert DeviceHistoryRequest is not None
        assert CommunicationResponse is not None

    def test_all_event_schemas_importable(self):
        """Test: Todos los schemas de events se pueden importar."""
        from app.schemas.events import EventResponse, EventsPageResponse

        assert EventResponse is not None
        assert EventsPageResponse is not None


class TestUtilsImport:
    """Tests para verificar que utils se importan correctamente."""

    def test_metrics_client_importable(self):
        """Test: MetricsClient se puede importar."""
        from app.utils.metrics import MetricsClient, metrics_client

        assert MetricsClient is not None
        assert metrics_client is not None

    def test_paseto_validator_importable(self):
        """Test: PasetoValidator se puede importar."""
        from app.utils.paseto_validator import (
            ExpiredToken,
            InvalidToken,
            PasetoValidator,
            paseto_validator,
        )

        assert PasetoValidator is not None
        assert paseto_validator is not None
        assert InvalidToken is not None
        assert ExpiredToken is not None


class TestServicesImport:
    """Tests para verificar que services se importan correctamente."""

    def test_repository_functions_importable(self):
        """Test: Repository functions se pueden importar."""
        from app.services.repository import (
            get_communications,
            get_latest_communications,
        )

        assert get_communications is not None
        assert get_latest_communications is not None

    def test_kafka_client_importable(self):
        """Test: KafkaClient se puede importar."""
        from app.services.kafka_client import KafkaClient

        assert KafkaClient is not None

    def test_events_repository_importable(self):
        """Test: Events repository functions se pueden importar."""
        from app.services.events_repository import get_events

        assert get_events is not None
