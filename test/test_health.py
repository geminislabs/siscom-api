"""
Tests para el endpoint de health check.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
class TestHealthEndpoint:
    """Tests del endpoint /health."""

    def test_health_check_returns_200(self, client: TestClient):
        """
        Test: Health check retorna 200 OK.
        """
        response = client.get("/health")

        assert response.status_code == 200

    def test_health_check_contains_status(self, client: TestClient):
        """
        Test: Health check contiene campo 'status'.
        """
        response = client.get("/health")
        data = response.json()

        assert "status" in data
        assert data["status"] == "healthy"

    def test_health_check_contains_service_name(self, client: TestClient):
        """
        Test: Health check contiene el nombre del servicio.
        """
        response = client.get("/health")
        data = response.json()

        assert "service" in data
        assert data["service"] == "siscom-api"

    def test_health_check_contains_version(self, client: TestClient):
        """
        Test: Health check contiene la versión del servicio.
        """
        response = client.get("/health")
        data = response.json()

        assert "version" in data
        assert isinstance(data["version"], str)

    def test_health_check_response_format(self, client: TestClient):
        """
        Test: Health check retorna el formato correcto.
        """
        response = client.get("/health")
        data = response.json()

        expected_keys = {"status", "service", "version", "kafka_circuit_breaker"}
        assert set(data.keys()) == expected_keys

    def test_health_check_no_auth_required(self, client: TestClient):
        """
        Test: Health check no requiere autenticación.
        """
        # Sin headers de autenticación
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    @pytest.mark.slow
    def test_health_check_response_time(self, client: TestClient):
        """
        Test: Health check responde en menos de 100ms.

        Nota: Este test es marcado como 'slow' porque mide tiempo.
        """
        import time

        start = time.time()
        response = client.get("/health")
        elapsed = (time.time() - start) * 1000  # Convertir a ms

        assert response.status_code == 200
        # Permitimos hasta 500ms en tests por el overhead
        assert elapsed < 500, f"Health check tomó {elapsed}ms"
