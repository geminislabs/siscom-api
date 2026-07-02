"""
Tests para los endpoints de comunicaciones.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
class TestCommunicationsEndpointMultiple:
    """Tests para GET /api/v1/communications (múltiples dispositivos)."""

    def test_get_communications_requires_auth(self, client: TestClient):
        """
        Test: Endpoint requiere autenticación.
        """
        response = client.get("/api/v1/communications?device_ids=TEST123")

        assert response.status_code == 403  # FastAPI retorna 403 sin auth

    def test_get_communications_with_valid_token(
        self, client: TestClient, auth_headers: dict, sample_suntech_communication
    ):
        """
        Test: GET con token válido retorna 200.
        """
        response = client.get(
            "/api/v1/communications?device_ids=867564050638581", headers=auth_headers
        )

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_communications_with_expired_token(
        self, client: TestClient, expired_token: str
    ):
        """
        Test: Token expirado retorna 401.
        """
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = client.get(
            "/api/v1/communications?device_ids=TEST123", headers=headers
        )

        assert response.status_code == 401

    def test_get_communications_with_invalid_token(
        self, client: TestClient, invalid_token: str
    ):
        """
        Test: Token inválido retorna 401.
        """
        headers = {"Authorization": f"Bearer {invalid_token}"}
        response = client.get(
            "/api/v1/communications?device_ids=TEST123", headers=headers
        )

        assert response.status_code == 401

    def test_get_communications_returns_correct_data(
        self, client: TestClient, auth_headers: dict, sample_suntech_communication
    ):
        """
        Test: Endpoint retorna los datos correctos.
        """
        response = client.get(
            "/api/v1/communications?device_ids=867564050638581", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 1
        assert data[0]["device_id"] == "867564050638581"
        assert "latitude" in data[0]
        assert "longitude" in data[0]
        assert "speed" in data[0]

    def test_get_communications_multiple_devices(
        self, client: TestClient, auth_headers: dict, multiple_communications
    ):
        """
        Test: Query con múltiples device IDs.
        """
        response = client.get(
            "/api/v1/communications?device_ids=SUNTECH0&device_ids=SUNTECH1&device_ids=QUECLINK0",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 3
        device_ids = {item["device_id"] for item in data}
        assert "SUNTECH0" in device_ids
        assert "SUNTECH1" in device_ids
        assert "QUECLINK0" in device_ids

    def test_get_communications_merges_suntech_and_queclink(
        self,
        client: TestClient,
        auth_headers: dict,
        sample_suntech_communication,
        sample_queclink_communication,
    ):
        """
        Test: Resultados incluyen ambas tablas (Suntech y Queclink).
        """
        response = client.get(
            "/api/v1/communications?device_ids=867564050638581&device_ids=QUECLINK123",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 2
        device_ids = {item["device_id"] for item in data}
        assert "867564050638581" in device_ids  # Suntech
        assert "QUECLINK123" in device_ids  # Queclink

    def test_get_communications_empty_result(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test: Device ID no existente retorna array vacío.
        """
        response = client.get(
            "/api/v1/communications?device_ids=NONEXISTENT999", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json() == []

    def test_get_communications_missing_device_ids(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test: Request sin device_ids retorna 422.
        """
        response = client.get("/api/v1/communications", headers=auth_headers)

        assert response.status_code == 422


@pytest.mark.integration
class TestCommunicationsSingleDeviceEndpoint:
    """Tests para GET /api/v1/devices/{device_id}/communications."""

    def test_get_device_communications_requires_auth(self, client: TestClient):
        """
        Test: Endpoint requiere autenticación.
        """
        response = client.get("/api/v1/devices/TEST123/communications")

        assert response.status_code == 403

    def test_get_device_communications_with_valid_token(
        self, client: TestClient, auth_headers: dict, sample_suntech_communication
    ):
        """
        Test: GET con token válido retorna 200.
        """
        response = client.get(
            "/api/v1/devices/867564050638581/communications", headers=auth_headers
        )

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_device_communications_returns_correct_device(
        self, client: TestClient, auth_headers: dict, multiple_communications
    ):
        """
        Test: Solo retorna comunicaciones del device ID especificado.
        """
        response = client.get(
            "/api/v1/devices/SUNTECH1/communications", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 1
        assert data[0]["device_id"] == "SUNTECH1"

    def test_get_device_communications_nonexistent_device(
        self, client: TestClient, auth_headers: dict
    ):
        """
        Test: Device no existente retorna array vacío.
        """
        response = client.get(
            "/api/v1/devices/NONEXISTENT/communications", headers=auth_headers
        )

        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.integration
class TestSSEStreamEndpoints:
    """Tests para endpoints de Server-Sent Events."""

    def test_sse_stream_multiple_devices_no_auth_required(
        self, client: TestClient, sse_headers: dict
    ):
        """
        Test: Endpoint SSE no requiere autenticación.
        """
        # Nota: TestClient no soporta bien SSE streaming,
        # solo verificamos que acepta la conexión
        response = client.get(
            "/api/v1/communications/stream?device_ids=TEST123",
            headers=sse_headers,
            timeout=1,  # Timeout corto para no esperar indefinidamente
        )

        # Debería aceptar la conexión (200)
        assert response.status_code == 200

    def test_sse_stream_requires_accept_header(self, client: TestClient):
        """
        Test: Verificar que el endpoint acepta el header correcto.
        """
        response = client.get(
            "/api/v1/communications/stream?device_ids=TEST123",
            headers={"Accept": "text/event-stream"},
            timeout=1,
        )

        assert response.status_code == 200

    def test_sse_stream_single_device_no_auth_required(
        self, client: TestClient, sse_headers: dict
    ):
        """
        Test: SSE de un dispositivo no requiere auth.
        """
        response = client.get(
            "/api/v1/devices/TEST123/communications/stream",
            headers=sse_headers,
            timeout=1,
        )

        assert response.status_code == 200

    def test_sse_stream_missing_device_ids(self, client: TestClient):
        """
        Test: Stream sin device_ids retorna 422.
        """
        response = client.get(
            "/api/v1/communications/stream", headers={"Accept": "text/event-stream"}
        )

        assert response.status_code == 422


@pytest.mark.integration
class TestCommunicationsResponseSchema:
    """Tests para verificar el schema de respuestas."""

    def test_response_has_required_fields(
        self, client: TestClient, auth_headers: dict, sample_suntech_communication
    ):
        """
        Test: Respuesta contiene todos los campos requeridos.
        """
        response = client.get(
            "/api/v1/communications?device_ids=867564050638581", headers=auth_headers
        )

        data = response.json()[0]

        required_fields = [
            "id",
            "device_id",
            "latitude",
            "longitude",
            "speed",
            "course",
            "gps_datetime",
        ]

        for field in required_fields:
            assert field in data

    def test_response_handles_null_values(
        self, client: TestClient, auth_headers: dict, db_session
    ):
        """
        Test: Respuesta maneja correctamente valores NULL.
        """
        from app.models.communications import CommunicationSuntech

        # Crear comunicación con valores NULL
        comm = CommunicationSuntech(
            device_id="NULL_TEST",
            latitude=None,
            longitude=None,
            speed=None,
        )
        db_session.add(comm)
        db_session.commit()

        response = client.get(
            "/api/v1/communications?device_ids=NULL_TEST", headers=auth_headers
        )

        data = response.json()[0]

        assert data["device_id"] == "NULL_TEST"
        assert data["latitude"] is None
        assert data["longitude"] is None
        assert data["speed"] is None
