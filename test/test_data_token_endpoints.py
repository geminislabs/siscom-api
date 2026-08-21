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

    def __init__(self, window=None):
        from app.core.scope_window import AccessWindow

        self.granted = {(_SCOPE, "dev", _REF): _IMEI}
        self.window = AccessWindow.always() if window is None else window

    async def resolve(self, scope_ref, kind, ref, max_cache_secs):
        internal_id = self.granted.get((scope_ref, kind, ref))
        return None if internal_id is None else (internal_id, self.window)


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


@pytest.mark.unit
class TestDegradedStates:
    """Estados intermedios del despliegue que nadie planea pero que ocurren.

    La distinción entre 4xx y 503 no es cosmética: el cliente reacciona a un
    401/403 reemitiendo el data token y reintentando. Si el fallo es del
    servidor —falta la clave pública, Valkey no responde—, ninguna credencial
    nueva lo arregla y el reintento se convierte en una tormenta de reemisiones
    contra admin-api.
    """

    @pytest.fixture
    def enforced_without_key(self, monkeypatch):
        """Interruptor encendido antes de configurar la clave pública."""
        from app.core.data_token import DataTokenVerifier

        monkeypatch.setattr(
            "app.core.data_token.settings.DATA_TOKEN_PUBLIC_KEY_B64", ""
        )
        monkeypatch.setattr("app.api.deps.settings.DATA_TOKEN_ENFORCED", True)
        monkeypatch.setattr("app.api.deps.data_token_verifier", DataTokenVerifier())

    @pytest.fixture
    def enforced_without_valkey(self, monkeypatch, enforced):
        """Clave configurada, pero el store de autorización no responde."""
        from app.services.scope_store import ScopeStoreUnavailable

        class _DownStore:
            async def resolve(self, *args, **kwargs):
                raise ScopeStoreUnavailable("Valkey no disponible")

        monkeypatch.setattr("app.api.deps.scope_store", _DownStore())

    def test_missing_public_key_is_503_not_401(
        self, client: TestClient, enforced_without_key
    ):
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Data plane not configured"

    def test_valkey_outage_is_503_not_403(
        self, client: TestClient, enforced_without_valkey
    ):
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "Authorization store unavailable"

    def test_a_missing_token_is_still_401_when_unconfigured(
        self, client: TestClient, enforced_without_key
    ):
        """Sin credencial el 401 es correcto, aunque el servidor esté a medias."""
        response = client.get(f"/api/v1/communications/latest?device_ids={_REF}")
        assert response.status_code == 401

    def test_a_genuine_denial_is_still_403(self, client: TestClient, enforced):
        """El 503 no se come el caso que de verdad importa."""
        response = client.get(
            "/api/v1/communications/latest?device_ids=ref-ajena",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403

    def test_degraded_states_never_serve_data(
        self, client: TestClient, enforced_without_valkey
    ):
        """Fail closed: degradado deniega, no abre."""
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code >= 400


@pytest.mark.unit
class TestBroadHandlersDoNotFlattenStatusCodes:
    """Un `except Exception` amplio no debe aplanar un código deliberado.

    `/init` envuelve todo su cuerpo en un `except Exception` que devuelve 500.
    Sin re-lanzar antes las `HTTPException`, cualquier código elegido a
    conciencia más arriba —un 503 de "base de datos caída"— salía como 500 y
    volvía a mentir sobre qué hacer al respecto.
    """

    @pytest.fixture
    def share_link(self, monkeypatch, enforced):
        """Enlace compartido válido, con el alcance ya resuelto."""

        class _Store:
            async def resolve_single(self, *args, **kwargs):
                from app.core.scope_window import AccessWindow

                return (_REF, _IMEI, AccessWindow.always())

        monkeypatch.setattr("app.api.deps.scope_store", _Store())
        monkeypatch.setattr(
            "app.api.deps.settings.SHARE_LOCATION_ACCEPT_LEGACY_TOKEN", False
        )
        return f"/api/v1/public/share-location/init?token={_token()}"

    def test_database_outage_is_503_not_500(
        self, client: TestClient, share_link, monkeypatch
    ):
        async def boom(*args, **kwargs):
            raise RuntimeError("base de datos caída")

        monkeypatch.setattr("app.api.routes.public.get_latest_communications", boom)

        response = client.get(share_link)
        assert response.status_code == 503
        assert response.json()["detail"] == "Location data temporarily unavailable"

    def test_database_outage_is_not_reported_as_a_valid_empty_link(
        self, client: TestClient, share_link, monkeypatch
    ):
        """Antes devolvía 200 "valid" con ubicación nula.

        Eso es indistinguible de un dispositivo que nunca ha reportado: quien
        abre el enlace ve un mapa vacío y concluye que el rastreador está roto.
        """

        async def boom(*args, **kwargs):
            raise RuntimeError("base de datos caída")

        monkeypatch.setattr("app.api.routes.public.get_latest_communications", boom)

        response = client.get(share_link)
        assert response.status_code != 200
        assert "valid" not in response.text

    def test_a_device_with_no_data_yet_is_still_a_valid_200(
        self, client: TestClient, share_link, monkeypatch
    ):
        """El 503 no se come el caso legítimo de "todavía sin posición"."""

        async def empty(*args, **kwargs):
            return []

        monkeypatch.setattr("app.api.routes.public.get_latest_communications", empty)

        response = client.get(share_link)
        assert response.status_code == 200
        body = response.json()
        assert body["msg"] == "valid"
        assert body["last_communication"] is None
        assert body["device_id"] == _REF


@pytest.mark.unit
class TestReassignedDeviceWindows:
    """Contrato v1.3: el equipo se reasignó y el dueño anterior sigue mirando.

    La fuga que esto cierra existía hoy: un aparate reasignado a otra
    organización seguía emitiendo telemetría en vivo hacia la anterior, de
    forma indefinida. El histórico del periodo propio sí se conserva, que es lo
    que Jesús confirmó como intencional.
    """

    @pytest.fixture
    def closed_assignment(self, monkeypatch, enforced):
        """El equipo fue del sujeto en enero y ya no lo es."""
        from datetime import datetime

        from app.core.scope_window import AccessWindow, Interval

        cerrada = AccessWindow(
            (
                Interval(
                    datetime(2026, 1, 1, tzinfo=UTC),
                    datetime(2026, 1, 31, tzinfo=UTC),
                ),
            )
        )
        monkeypatch.setattr("app.api.deps.scope_store", _FakeScopeStore(cerrada))

    @pytest.fixture
    def revoked_scope(self, monkeypatch, enforced):
        """Referencia reconocida pero sin ninguna ventana."""
        from app.core.scope_window import AccessWindow

        monkeypatch.setattr(
            "app.api.deps.scope_store", _FakeScopeStore(AccessWindow.none())
        )

    def test_live_position_is_withheld_after_reassignment(
        self, client: TestClient, closed_assignment, monkeypatch
    ):
        """La posición ACTUAL ya no es del dueño anterior."""
        from app.models.communications import CommunicationCurrentState

        consultados = []

        async def spy(session, device_ids, msg_class=None):
            consultados.append(list(device_ids))
            return [CommunicationCurrentState(device_id=d) for d in device_ids]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", spy
        )

        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )

        assert response.status_code == 200
        assert response.json() == []
        # Ni siquiera se llega a consultar la base de datos por ese equipo.
        assert consultados == [[]]

    def test_the_request_itself_is_not_rejected(
        self, client: TestClient, closed_assignment
    ):
        """Se filtra, no se rechaza: el cliente conoce sus propias ventanas.

        Un 403 aquí no protegería nada —no hay oráculo de pertenencia que
        proteger— y rompería el caso legítimo de pedir varios equipos de los
        que solo algunos siguen asignados.
        """
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200

    def test_history_is_still_reachable_after_reassignment(
        self, client: TestClient, closed_assignment, monkeypatch
    ):
        """Conservar el histórico del periodo propio es intencional."""
        consultados = []

        async def spy(session, device_ids, received_at=None, windows=None):
            consultados.append(list(device_ids))
            return []

        monkeypatch.setattr("app.api.routes.communications.get_communications", spy)

        response = client.get(
            f"/api/v1/communications?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        # El histórico SÍ llega al repositorio, a diferencia de `latest`.
        assert consultados == [[_IMEI]]

    def test_a_scope_with_no_windows_is_denied_outright(
        self, client: TestClient, revoked_scope
    ):
        """Sin ventanas es revocado, no "sin restricciones"."""
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403

    def test_an_open_assignment_still_serves_live_data(
        self, client: TestClient, enforced, monkeypatch
    ):
        """El caso normal no se rompe al añadir ventanas."""
        from app.models.communications import CommunicationCurrentState

        async def latest(session, device_ids, msg_class=None):
            return [CommunicationCurrentState(device_id=d) for d in device_ids]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", latest
        )

        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        assert response.json()[0]["device_id"] == _REF
