"""Tests de extremo a extremo de la exigencia del data token en los endpoints.

Los tests unitarios comprueban cada pieza; estos comprueban el **cableado**,
que es donde se cuelan los agujeros: que la dependencia esté realmente puesta
en el router, que el 401 y el 403 salgan con el código correcto, y que el IMEI
no vuelva al cliente en la respuesta.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import pyseto
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from pyseto import Key

_KEY = ed25519.Ed25519PrivateKey.generate()
_IMEI = "867564050638581"
_REF = "ref-opaca-a"
_SCOPE = "scope-1"


def _public_b64() -> str:
    return base64.b64encode(
        _KEY.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode()


def _token(**overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "jti": "jti-1",
        "scope_ref": _SCOPE,
        "aud": "siscom-api",
        "iat": now.isoformat(),
        "nbf": now.isoformat(),
        "exp": (now + timedelta(minutes=10)).isoformat(),
    }
    payload.update(overrides)
    pem = _KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signer = Key.new(version=4, purpose="public", key=pem)
    return pyseto.encode(signer, json.dumps(payload).encode()).decode()


class _FakeScopeStore:
    """Solo `ref-opaca-a` está concedida, y resuelve al IMEI."""

    def __init__(self):
        self.granted = {(_SCOPE, "dev", _REF): _IMEI}

    async def resolve(self, scope_ref, kind, ref, max_cache_secs):
        return self.granted.get((scope_ref, kind, ref))


@pytest.fixture
def enforced(monkeypatch):
    """Activa la exigencia con verificador y store de test."""
    from app.core.data_token import DataTokenVerifier

    monkeypatch.setattr(
        "app.core.data_token.settings.DATA_TOKEN_PUBLIC_KEY_B64", _public_b64()
    )
    monkeypatch.setattr("app.core.data_token.settings.DATA_TOKEN_KEY_ID", "")
    monkeypatch.setattr("app.api.deps.settings.DATA_TOKEN_ENFORCED", True)
    monkeypatch.setattr("app.api.deps.data_token_verifier", DataTokenVerifier())
    monkeypatch.setattr("app.api.deps.scope_store", _FakeScopeStore())


@pytest.fixture
def observing(monkeypatch):
    """Modo observación: verifica y registra, pero no rechaza."""
    monkeypatch.setattr("app.api.deps.settings.DATA_TOKEN_ENFORCED", False)


@pytest.mark.unit
class TestEnforcementRejects:
    def test_request_without_a_token_is_rejected(self, client: TestClient, enforced):
        response = client.get(f"/api/v1/communications/latest?device_ids={_REF}")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing data token"

    def test_request_with_a_bogus_token_is_rejected(self, client: TestClient, enforced):
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": "Bearer v4.public.basura"},
        )
        assert response.status_code == 401

    def test_expired_token_is_rejected(self, client: TestClient, enforced):
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token(exp=past)}"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Data token expired"

    def test_ref_outside_the_scope_is_forbidden(self, client: TestClient, enforced):
        """El caso que motiva toda la fase: leer la flota ajena."""
        response = client.get(
            "/api/v1/communications/latest?device_ids=ref-de-otra-marca",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403

    def test_raw_imei_is_not_accepted_as_a_ref(self, client: TestClient, enforced):
        """Con la exigencia activa, enumerar IMEIs deja de funcionar."""
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_IMEI}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403

    def test_one_bad_ref_rejects_the_whole_request(self, client: TestClient, enforced):
        """Sin filtrado parcial: si no, la API es un oráculo de pertenencia."""
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}&device_ids=ref-ajena",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403

    def test_events_endpoint_is_covered_too(self, client: TestClient, enforced):
        """`/events` tenía el mismo agujero y direcciona por unit_ref."""
        response = client.get(
            "/api/v1/events?unit_id=123e4567-e89b-12d3-a456-426614174000"
            "&from=2026-03-01T00:00:00Z&to=2026-03-31T23:59:59Z"
        )
        assert response.status_code == 401

    def test_path_param_endpoints_are_covered(self, client: TestClient, enforced):
        response = client.get(f"/api/v1/devices/{_IMEI}/communications")
        assert response.status_code == 401


@pytest.mark.unit
class TestEnforcementAllows:
    def test_a_granted_ref_reaches_the_handler(self, client: TestClient, enforced):
        """Sin datos en BD la respuesta es vacía, pero pasa autorización."""
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        assert response.json() == []

    def test_response_comes_back_in_the_reference_space(
        self, client: TestClient, enforced, monkeypatch
    ):
        """Regla del contrato: se pide en refs, se responde en refs.

        Si la respuesta trajera el IMEI, sacarlo de la petición no habría
        servido de nada: volvería al navegador por la puerta de atrás.
        """
        from app.models.communications import CommunicationCurrentState

        async def fake_latest(session, device_ids, msg_class=None):
            # El repositorio recibe el id INTERNO, no la referencia.
            assert device_ids == [_IMEI]
            return [CommunicationCurrentState(device_id=_IMEI, latitude=None)]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", fake_latest
        )

        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200

        body = response.json()
        assert body[0]["device_id"] == _REF
        assert _IMEI not in response.text


@pytest.mark.unit
class TestObservationMode:
    def test_requests_without_a_token_still_work(self, client: TestClient, observing):
        """Permite desplegar y medir antes de romper a gac-web."""
        response = client.get(f"/api/v1/communications/latest?device_ids={_IMEI}")
        assert response.status_code == 200

    def test_identifiers_are_not_translated(
        self, client: TestClient, observing, monkeypatch
    ):
        """Sin exigencia, el cliente sigue hablando en IMEIs y nada cambia."""
        from app.models.communications import CommunicationCurrentState

        async def fake_latest(session, device_ids, msg_class=None):
            assert device_ids == [_IMEI]
            return [CommunicationCurrentState(device_id=_IMEI, latitude=None)]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", fake_latest
        )

        response = client.get(f"/api/v1/communications/latest?device_ids={_IMEI}")
        assert response.json()[0]["device_id"] == _IMEI
