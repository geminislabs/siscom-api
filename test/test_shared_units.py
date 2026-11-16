"""NEXUS-S2-03: unidades compartidas y revocación inmediata.

Dos propiedades, y las dos fallan hacia el lado seguro:

1. **Una unidad compartida es solo telemetría.** Dentro de su ventana se ven
   posiciones e histórico; nunca eventos ni alertas, ni por HTTP ni por
   WebSocket. Los comandos no existen en este servicio.
2. **La revocación no espera a la caché.** Una pausa, revocación o vencimiento
   del permiso se refleja en la siguiente petición y, en un WebSocket abierto,
   en la siguiente revalidación.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.deps import (
    RefTranslation,
    frame_is_exposed,
    redact_shared_frame,
    websocket_revocation_reason,
)
from app.core.data_token import DataTokenClaims
from app.core.scope_window import AccessWindow, Interval, parse_scope_value
from app.services.scope_store import ScopeStore, ScopeStoreUnavailable
from test.test_data_token_endpoints import (
    _IMEI,
    _REF,
    _SCOPE,
    _FakeScopeStore,
    _token,
    enforced,  # noqa: F401 — fixture reutilizado
)
from test.test_share_location_tokens import (
    _data_token,
    share_env,  # noqa: F401 — fixture reutilizado
)

_UNIT = "123e4567-e89b-12d3-a456-426614174000"
_UNIT_INTERNAL = "223e4567-e89b-12d3-a456-426614174999"
_SHARED = AccessWindow((Interval(None, None),), shared=True)


def _claims(minutes: int = 10) -> DataTokenClaims:
    return DataTokenClaims(
        jti="jti-1",
        scope_ref=_SCOPE,
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
    )


# ── Formato del valor de alcance (contrato v1.4) ────────────────────────────


@pytest.mark.unit
class TestAccessLevelParsing:
    def test_shared_access_is_parsed(self):
        parsed = parse_scope_value(
            '{"id":"IMEI1","access":"shared",'
            '"windows":[{"from":"2026-01-01T00:00:00+00:00","to":null}]}'
        )
        assert parsed is not None
        assert parsed[1].shared

    def test_missing_access_means_full(self):
        """Los valores de v1.3 son todos de unidades propias."""
        parsed = parse_scope_value('{"id":"IMEI1","windows":[{"from":null,"to":null}]}')
        assert parsed is not None
        assert not parsed[1].shared

    def test_unknown_access_level_is_denied(self):
        """Un nivel que no conocemos no puede degradar a acceso completo."""
        parsed = parse_scope_value(
            '{"id":"IMEI1","access":"admin","windows":[{"from":null,"to":null}]}'
        )
        assert parsed is None

    def test_shared_without_windows_is_still_no_access(self):
        parsed = parse_scope_value('{"id":"IMEI1","access":"shared","windows":[]}')
        assert parsed is not None
        assert not parsed[1]

    def test_shared_window_is_strict(self):
        """Vencido el permiso, no hay acceso en vivo aunque el token viva."""
        parsed = parse_scope_value(
            '{"id":"IMEI1","access":"shared",'
            '"windows":[{"from":"2026-01-01T00:00:00+00:00",'
            '"to":"2026-01-02T00:00:00+00:00"}]}'
        )
        assert parsed is not None
        assert not parsed[1].allows_now()


# ── Marca de revocación en Valkey ───────────────────────────────────────────


class _Valkey:
    def __init__(self, revoked=(), fail=False):
        self.revoked = set(revoked)
        self.fail = fail
        self.exists_calls = 0

    async def exists(self, key):
        self.exists_calls += 1
        if self.fail:
            raise ConnectionError("Valkey no disponible")
        return 1 if key in self.revoked else 0

    async def hget(self, key, field):
        return "IMEI1"


@pytest.mark.unit
class TestRevocationMarker:
    @pytest.fixture
    def build(self, monkeypatch):
        def _build(**kwargs):
            fake = _Valkey(**kwargs)
            store = ScopeStore()
            monkeypatch.setattr(store, "_connect", lambda: fake)
            return store, fake

        return _build

    async def test_reads_the_scope_revoked_key(self, build):
        store, _ = build(revoked={"dt:scope:s1:revoked"})
        assert await store.is_revoked("s1")
        assert not await store.is_revoked("s2")

    async def test_is_never_cached(self, build):
        """Es justo lo que hace inmediata la revocación."""
        store, fake = build()
        await store.is_revoked("s1")
        await store.is_revoked("s1")
        assert fake.exists_calls == 2

    async def test_revocation_purges_the_membership_cache(self, build):
        store, fake = build()
        await store.resolve("s1", "dev", "ref-a", 30)
        assert store._cache

        fake.revoked.add("dt:scope:s1:revoked")
        assert await store.is_revoked("s1")
        assert not store._cache

    async def test_outage_is_reported_apart(self, build):
        store, _ = build(fail=True)
        with pytest.raises(ScopeStoreUnavailable):
            await store.is_revoked("s1")


# ── HTTP ────────────────────────────────────────────────────────────────────


class _SharedStore(_FakeScopeStore):
    """`ref-opaca-a` es propia; la unidad `_UNIT` la comparte otro equipo."""

    def __init__(self):
        super().__init__()
        self.granted[(_SCOPE, "unit", _UNIT)] = _UNIT_INTERNAL
        self.windows = {_UNIT_INTERNAL: _SHARED, _IMEI: AccessWindow.always()}

    async def resolve(self, scope_ref, kind, ref, max_cache_secs):
        internal_id = self.granted.get((scope_ref, kind, ref))
        return None if internal_id is None else (internal_id, self.windows[internal_id])


@pytest.fixture
def shared_store(monkeypatch, enforced):  # noqa: F811
    store = _SharedStore()
    monkeypatch.setattr("app.api.deps.scope_store", store)
    return store


_EVENTS = (
    f"/api/v1/events?unit_id={_UNIT}&from=2026-03-01T00:00:00Z&to=2026-03-31T23:59:59Z"
)


@pytest.mark.unit
class TestSharedUnitsOverHttp:
    def test_events_of_a_shared_unit_are_forbidden(
        self, client: TestClient, shared_store
    ):
        response = client.get(_EVENTS, headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 403
        assert response.json()["detail"] == "Not available for shared units"

    def test_history_of_a_shared_device_comes_without_alerts(
        self, client: TestClient, shared_store, monkeypatch
    ):
        shared_store.windows[_IMEI] = _SHARED

        async def fake_history(session, device_ids, windows=None):
            return [
                {
                    "id": 1,
                    "device_id": _IMEI,
                    "alert_type": "PANIC",
                }
            ]

        monkeypatch.setattr(
            "app.api.routes.communications.get_communications", fake_history
        )
        response = client.get(
            f"/api/v1/communications?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body[0]["device_id"] == _REF
        assert body[0]["alert_type"] is None
        assert "PANIC" not in response.text

    def test_own_device_keeps_its_alerts(
        self, client: TestClient, shared_store, monkeypatch
    ):
        async def fake_history(session, device_ids, windows=None):
            return [{"id": 1, "device_id": _IMEI, "alert_type": "PANIC"}]

        monkeypatch.setattr(
            "app.api.routes.communications.get_communications", fake_history
        )
        response = client.get(
            f"/api/v1/communications?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.json()[0]["alert_type"] == "PANIC"


@pytest.mark.unit
class TestImmediateRevocationOverHttp:
    def test_a_revoked_scope_is_rejected_on_the_next_request(
        self, client: TestClient, shared_store
    ):
        url = f"/api/v1/communications/latest?device_ids={_REF}"
        headers = {"Authorization": f"Bearer {_token()}"}
        assert client.get(url, headers=headers).status_code == 200

        shared_store.revoked.add(_SCOPE)
        response = client.get(url, headers=headers)
        # 401: el token entero dejó de valer; el cliente pide uno nuevo.
        assert response.status_code == 401
        assert response.json()["detail"] == "Data token revoked"

    def test_observation_mode_does_not_check_revocation(
        self, client: TestClient, monkeypatch
    ):
        monkeypatch.setattr("app.api.deps.settings.DATA_TOKEN_ENFORCED", False)
        response = client.get(f"/api/v1/communications/latest?device_ids={_IMEI}")
        assert response.status_code == 200


# ── WebSocket ───────────────────────────────────────────────────────────────


def _translation(shared: bool) -> RefTranslation:
    translation = RefTranslation()
    translation.add(_REF, _IMEI, _SHARED if shared else AccessWindow.always())
    translation.claims = _claims()
    return translation


_ALERT = {"message_type": "alert", "data": {"device_id": _IMEI, "type": "PANIC"}}
_POSITION = {"data": {"device_id": _IMEI, "latitude": 1, "alert_type": "SOS"}}


@pytest.mark.unit
class TestSharedUnitsOverWebSocket:
    def test_alerts_of_a_shared_device_are_dropped(self):
        assert not frame_is_exposed(_ALERT, _translation(shared=True))

    def test_alerts_of_an_own_device_still_flow(self):
        assert frame_is_exposed(_ALERT, _translation(shared=False))

    def test_positions_of_a_shared_device_still_flow_without_alert_fields(self):
        translation = _translation(shared=True)
        assert frame_is_exposed(_POSITION, translation)
        redacted = redact_shared_frame(_POSITION, translation)
        assert "alert_type" not in redacted["data"]
        assert redacted["data"]["latitude"] == 1

    def test_own_positions_are_untouched(self):
        assert redact_shared_frame(_POSITION, _translation(shared=False)) is _POSITION

    def test_observation_mode_exposes_everything(self):
        translation = RefTranslation(enforcing=False)
        translation.add(_REF, _IMEI, _SHARED)
        assert frame_is_exposed(_ALERT, translation)


@pytest.mark.unit
class TestWebSocketRevalidation:
    @pytest.fixture
    def store(self, monkeypatch):
        store = _FakeScopeStore()
        monkeypatch.setattr("app.api.deps.scope_store", store)
        return store

    async def test_a_live_connection_is_kept(self, store):
        assert await websocket_revocation_reason(_translation(False)) is None

    async def test_revocation_closes_with_1008(self, store):
        store.revoked.add(_SCOPE)
        assert await websocket_revocation_reason(_translation(False)) == (
            1008,
            "Data token revoked",
        )

    async def test_expiry_closes_with_1008(self, store):
        translation = _translation(False)
        translation.claims = _claims(minutes=-1)
        reason = await websocket_revocation_reason(translation)
        assert reason == (1008, "Data token expired")

    async def test_outage_closes_with_1013(self, monkeypatch):
        class _Down:
            async def is_revoked(self, *args):
                raise ScopeStoreUnavailable("caído")

        monkeypatch.setattr("app.api.deps.scope_store", _Down())
        reason = await websocket_revocation_reason(_translation(False))
        assert reason == (1013, "Authorization store unavailable")

    async def test_without_claims_there_is_nothing_to_revalidate(self):
        assert await websocket_revocation_reason(RefTranslation()) is None


# ── Ventana estricta de la unidad compartida (DoD 2) ────────────────────────


_EXPIRED_SHARED = AccessWindow(
    (
        Interval(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 31, tzinfo=UTC),
        ),
    ),
    shared=True,
)


@pytest.mark.unit
class TestSharedWindowIsEnforced:
    """Vencido el permiso, la unidad compartida deja de verse aunque el token
    siga vivo — con DATA_TOKEN_ENFORCED activo."""

    def test_live_position_of_an_expired_share_is_withheld(
        self, client: TestClient, shared_store, monkeypatch
    ):
        from app.models.communications import CommunicationCurrentState

        consultados = []

        async def spy(session, device_ids, msg_class=None):
            consultados.append(list(device_ids))
            return [CommunicationCurrentState(device_id=d) for d in device_ids]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", spy
        )
        shared_store.windows[_IMEI] = _EXPIRED_SHARED

        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 404
        assert consultados == []

    def test_history_outside_the_shared_window_is_rejected(
        self, client: TestClient, shared_store
    ):
        shared_store.windows[_IMEI] = _EXPIRED_SHARED
        response = client.get(
            f"/api/v1/devices/{_REF}/communications?received_at=2026-03-15",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 404

    def test_a_shared_unit_outside_the_scope_is_403(
        self, client: TestClient, shared_store
    ):
        response = client.get(
            "/api/v1/communications/latest?device_ids=ref-no-compartida",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403

    def test_a_paused_share_is_no_access(
        self, client: TestClient, enforced, monkeypatch  # noqa: F811
    ):
        """Pausa = admin-api deja la unidad sin ventanas: denegado."""
        monkeypatch.setattr(
            "app.api.deps.scope_store",
            _FakeScopeStore(AccessWindow((), shared=True)),
        )
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403


@pytest.mark.unit
class TestEventsRegression:
    def test_events_of_own_units_are_not_blocked(
        self, client: TestClient, shared_store, monkeypatch
    ):
        """El bloqueo de eventos solo alcanza a unidades compartidas."""
        shared_store.windows[_UNIT_INTERNAL] = AccessWindow.always()

        async def no_events(*args, **kwargs):
            return [], None

        monkeypatch.setattr("app.api.routes.events.get_events", no_events)
        response = client.get(_EVENTS, headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 200

    def test_events_in_observation_mode_are_not_blocked(self):
        from types import SimpleNamespace

        from app.api.deps import raise_if_any_shared

        translation = RefTranslation(enforcing=False)
        translation.add(_UNIT, _UNIT_INTERNAL, _SHARED)
        request = SimpleNamespace(state=SimpleNamespace(ref_translation=translation))
        raise_if_any_shared(request, [_UNIT])  # no lanza


# ── WebSocket: handshake, watchdog y alertas sin atribuir ───────────────────


class _ClosingSocket:
    def __init__(self, protocol: str | None = None):
        self.headers = {} if protocol is None else {"sec-websocket-protocol": protocol}
        self.closed: tuple[int, str] | None = None
        self.sent: list = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    async def send_json(self, data) -> None:
        self.sent.append(data)

    async def receive(self):
        import asyncio

        await asyncio.sleep(3600)


@pytest.mark.unit
class TestWebSocketHandshakeRevocation:
    def _socket(self):
        from app.core.config import settings

        return _ClosingSocket(f"{settings.DATA_TOKEN_WS_SUBPROTOCOL}, {_token()}")

    async def test_a_revoked_token_cannot_open_a_stream(self, shared_store):
        from app.api.deps import authorize_websocket

        shared_store.revoked.add(_SCOPE)
        ws = self._socket()
        ok, _, _ = await authorize_websocket(ws, [_REF])
        assert not ok
        assert ws.closed == (1008, "Data token revoked")

    async def test_an_outage_rejects_with_1013(self, shared_store, monkeypatch):
        from app.api.deps import authorize_websocket

        async def down(*_):
            raise ScopeStoreUnavailable("caído")

        monkeypatch.setattr(shared_store, "is_revoked", down)
        ws = self._socket()
        ok, _, _ = await authorize_websocket(ws, [_REF])
        assert not ok
        assert ws.closed == (1013, "Authorization store unavailable")

    async def test_a_live_token_opens_with_claims_and_shared_ids(self, shared_store):
        from app.api.deps import authorize_websocket

        shared_store.windows[_IMEI] = _SHARED
        ws = self._socket()
        ok, _, translation = await authorize_websocket(ws, [_REF])
        assert ok
        assert ws.closed is None
        assert translation.claims is not None
        assert translation.is_shared(_IMEI)


@pytest.mark.unit
class TestWatchdogClosesOpenStreams:
    async def test_watchdog_returns_on_revocation(self, monkeypatch):
        from app.api.routes.stream import revalidation_watchdog

        store = _FakeScopeStore()
        store.revoked.add(_SCOPE)
        monkeypatch.setattr("app.api.deps.scope_store", store)
        monkeypatch.setattr("app.api.routes.stream.settings.SCOPE_REVALIDATE_SECS", 0)

        reason = await revalidation_watchdog(_translation(False))
        assert reason == (1008, "Data token revoked")

    async def test_process_loop_closes_the_socket_when_watchdog_fires(self):
        import asyncio

        from fastapi import WebSocketDisconnect

        from app.api.routes.stream import process_websocket_messages

        async def fires():
            return 1008, "Data token revoked"

        ws = _ClosingSocket()
        watchdog = asyncio.ensure_future(fires())
        with pytest.raises(WebSocketDisconnect):
            await asyncio.wait_for(
                process_websocket_messages(ws, [asyncio.Queue()], None, watchdog),
                timeout=2,
            )
        assert ws.closed == (1008, "Data token revoked")


@pytest.mark.unit
class TestUnattributedAlerts:
    def test_an_alert_without_device_is_dropped_if_any_unit_is_shared(self):
        alert = {"message_type": "alert", "data": {"type": "PANIC"}}
        assert not frame_is_exposed(alert, _translation(shared=True))

    def test_an_alert_without_device_flows_if_nothing_is_shared(self):
        alert = {"message_type": "alert", "data": {"type": "PANIC"}}
        assert frame_is_exposed(alert, _translation(shared=False))

    def test_nested_alert_fields_are_stripped(self):
        frame = {
            "data": [
                {"device_id": _IMEI, "pos": {"alert_type": "SOS", "lat": 2}},
            ]
        }
        redacted = redact_shared_frame(frame, _translation(shared=True))
        assert redacted == {"data": [{"device_id": _IMEI, "pos": {"lat": 2}}]}


# ── Enlace público compartido: revocación con la conexión abierta ───────────


@pytest.mark.unit
class TestPublicLinkRevocation:
    def _grant(self, scope_ref=_SCOPE):
        from app.api.deps import ShareGrant

        return ShareGrant(
            _REF,
            _IMEI,
            datetime.now(UTC) + timedelta(minutes=10),
            legacy=scope_ref is None,
            scope_ref=scope_ref,
        )

    async def test_legacy_links_have_nothing_to_revalidate(self):
        from app.api.routes.public import _share_revoked

        assert not await _share_revoked(None)
        assert not await _share_revoked(self._grant(scope_ref=None))

    async def test_revoked_link_is_detected(self, monkeypatch):
        from app.api.routes.public import _share_revoked

        store = _FakeScopeStore()
        store.revoked.add(_SCOPE)
        monkeypatch.setattr("app.api.routes.public.scope_store", store)
        assert await _share_revoked(self._grant())

    async def test_outage_fails_closed(self, monkeypatch):
        from app.api.routes.public import _share_revoked

        class _Down:
            async def is_revoked(self, *_):
                raise ScopeStoreUnavailable("caído")

        monkeypatch.setattr("app.api.routes.public.scope_store", _Down())
        assert await _share_revoked(self._grant())

    async def test_keepalive_closes_a_revoked_link(self, monkeypatch):
        import asyncio

        from app.api.routes.public import _send_keepalive

        store = _FakeScopeStore()
        store.revoked.add(_SCOPE)
        monkeypatch.setattr("app.api.routes.public.scope_store", store)
        monkeypatch.setattr(
            "app.api.routes.public.settings.WEBSOCKET_KEEPALIVE_SECS", 0.01
        )
        ws = _ClosingSocket()
        grant = self._grant()
        await asyncio.wait_for(_send_keepalive(ws, grant.expires_at, grant), 2)
        assert ws.closed == (1008, "Link revoked")
        assert {"event": "revoked", "data": {"message": "Link revoked"}} in ws.sent


@pytest.mark.unit
class TestScopeStoreWithoutValkey:
    async def test_unconfigured_store_is_unavailable_not_unrevoked(self, monkeypatch):
        store = ScopeStore()
        monkeypatch.setattr(store, "_connect", lambda: None)
        with pytest.raises(ScopeStoreUnavailable):
            await store.is_revoked("s1")


# ── Cobertura de todos los endpoints de telemetría (DoD 3) ──────────────────


def _latest_row(device_id: str, msg_class: str = "STATUS"):
    from app.models.communications import CommunicationCurrentState

    return CommunicationCurrentState(device_id=device_id, msg_class=msg_class)


@pytest.mark.unit
class TestEveryTelemetryEndpointRedactsSharedUnits:
    """Las cuatro rutas de telemetría pasan por `to_external_models`; ninguna
    puede dejar salir el campo de alerta de una unidad compartida."""

    @pytest.fixture
    def shared_device(self, shared_store, monkeypatch):
        shared_store.windows[_IMEI] = _SHARED

        async def latest(session, device_ids, msg_class=None):
            return [_latest_row(d) for d in device_ids]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", latest
        )
        return shared_store

    def test_latest_list_comes_without_alerts(self, client: TestClient, shared_device):
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        assert response.json()[0]["device_id"] == _REF
        # El esquema de `latest` no tiene campos de alerta.
        assert "alert_type" not in response.json()[0]

    def test_device_latest_comes_without_alerts(
        self, client: TestClient, shared_device
    ):
        response = client.get(
            f"/api/v1/devices/{_REF}/communications/latest",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        assert "alert_type" not in response.json()

    @pytest.mark.parametrize("msg_class", ["ALERT", "alert"])
    def test_latest_alert_of_a_shared_device_is_forbidden(
        self, client: TestClient, shared_device, msg_class
    ):
        response = client.get(
            f"/api/v1/devices/{_REF}/communications/latest?class={msg_class}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Not available for shared units"

    def test_latest_alert_of_an_own_device_still_works(
        self, client: TestClient, shared_store, monkeypatch
    ):
        async def latest(session, device_ids, msg_class=None):
            return [_latest_row(d, msg_class) for d in device_ids]

        monkeypatch.setattr(
            "app.api.routes.communications.get_latest_communications", latest
        )
        response = client.get(
            f"/api/v1/devices/{_REF}/communications/latest?class=ALERT",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        assert response.json()["msg_class"] == "ALERT"

    def test_device_history_with_date_comes_without_alerts(
        self, client: TestClient, shared_device, monkeypatch
    ):
        async def history(session, device_ids, received_at=None, windows=None):
            return [{"id": 1, "device_id": _IMEI, "alert_type": "PANIC"}]

        monkeypatch.setattr("app.api.routes.communications.get_communications", history)
        response = client.get(
            f"/api/v1/devices/{_REF}/communications?received_at=2026-03-15",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        assert "PANIC" not in response.text


@pytest.mark.unit
class TestRevocationAcrossEndpoints:
    """La marca de revocación corta cualquier endpoint protegido, no solo uno."""

    @pytest.mark.parametrize(
        "url",
        [
            f"/api/v1/communications?device_ids={_REF}",
            f"/api/v1/devices/{_REF}/communications",
            f"/api/v1/devices/{_REF}/communications/latest",
            _EVENTS,
        ],
    )
    def test_revoked_scope_is_401_everywhere(
        self, client: TestClient, shared_store, url
    ):
        shared_store.revoked.add(_SCOPE)
        response = client.get(url, headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Data token revoked"

    def test_store_outage_on_revocation_check_is_503(
        self, client: TestClient, shared_store, monkeypatch
    ):
        async def down(*_):
            raise ScopeStoreUnavailable("caído")

        monkeypatch.setattr(shared_store, "is_revoked", down)
        response = client.get(
            f"/api/v1/communications/latest?device_ids={_REF}",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 503


# ── Puntos de entrega: lo que de verdad sale por el socket ──────────────────


def _done(value):
    import asyncio

    future = asyncio.get_running_loop().create_future()
    future.set_result(value)
    return future


@pytest.mark.unit
class TestStreamDeliveryOfSharedUnits:
    """`_deliver_event` es donde se cablean los filtros: probarlo ahí y no solo
    en las funciones sueltas evita que un refactor deje de llamarlas."""

    async def test_an_alert_of_a_shared_unit_never_reaches_the_socket(self):
        from app.api.routes.stream import _deliver_event

        ws = _ClosingSocket()
        await _deliver_event(ws, _done(_ALERT), _translation(shared=True))
        assert ws.sent == []

    async def test_a_position_of_a_shared_unit_goes_out_redacted_and_external(self):
        from app.api.routes.stream import _deliver_event

        ws = _ClosingSocket()
        await _deliver_event(ws, _done(_POSITION), _translation(shared=True))
        assert len(ws.sent) == 1
        data = ws.sent[0]["data"]["data"]
        assert "alert_type" not in data
        assert data["device_id"] == _REF

    async def test_an_alert_of_an_own_unit_is_delivered(self):
        from app.api.routes.stream import _deliver_event

        ws = _ClosingSocket()
        await _deliver_event(ws, _done(_ALERT), _translation(shared=False))
        assert len(ws.sent) == 1
        assert ws.sent[0]["data"]["data"]["type"] == "PANIC"


@pytest.mark.unit
class TestPublicLinkDeliveryOfSharedUnits:
    def _grant(self):
        from app.api.deps import ShareGrant

        return ShareGrant(
            _REF,
            _IMEI,
            datetime.now(UTC) + timedelta(minutes=10),
            legacy=False,
            window=_SHARED,
            scope_ref=_SCOPE,
        )

    async def _deliver(self, *frames):
        import asyncio

        from app.api.routes.public import _process_queue_messages

        queue: asyncio.Queue = asyncio.Queue()
        for frame in frames:
            queue.put_nowait(frame)
        ws = _ClosingSocket()
        grant = self._grant()
        for _ in frames:
            await _process_queue_messages(ws, [queue], grant.expires_at, grant)
        return ws.sent

    async def test_alerts_do_not_leave_through_a_shared_link(self):
        assert await self._deliver(_ALERT) == []

    async def test_positions_leave_redacted_and_with_the_reference(self):
        sent = await self._deliver(_POSITION)
        assert len(sent) == 1
        data = sent[0]["data"]["data"]
        assert "alert_type" not in data
        assert data["device_id"] == _REF
        assert _IMEI not in str(sent)


# ── Huecos de cobertura: aislamiento de la purga, sockets mixtos, vencimiento ─


@pytest.mark.unit
class TestRevocationIsScopedToItsTeam:
    async def test_revoking_one_scope_keeps_the_others_cache(self, monkeypatch):
        """Revocar el permiso de un equipo no invalida el de los demás."""
        store = ScopeStore()
        fake = _Valkey()
        monkeypatch.setattr(store, "_connect", lambda: fake)
        await store.resolve("s1", "dev", "ref-a", 30)
        await store.resolve("s2", "dev", "ref-a", 30)

        fake.revoked.add("dt:scope:s1:revoked")
        assert await store.is_revoked("s1")
        assert {key[0] for key in store._cache} == {"s2"}

    async def test_an_unrevoked_check_keeps_the_cache(self, monkeypatch):
        store = ScopeStore()
        monkeypatch.setattr(store, "_connect", _Valkey)
        await store.resolve("s1", "dev", "ref-a", 30)
        assert not await store.is_revoked("s1")
        assert store._cache


def _mixed_translation() -> RefTranslation:
    """Un socket con una unidad propia (`_IMEI`) y una compartida."""
    translation = RefTranslation()
    translation.add(_REF, _IMEI, AccessWindow.always())
    translation.add(_UNIT, _UNIT_INTERNAL, _SHARED)
    translation.claims = _claims()
    return translation


@pytest.mark.unit
class TestMixedSocket:
    def test_own_unit_frames_are_untouched(self):
        frame = {"data": {"device_id": _IMEI, "alert_type": "SOS"}}
        assert redact_shared_frame(frame, _mixed_translation()) is frame

    def test_shared_unit_frames_are_redacted(self):
        frame = {"data": {"device_id": _UNIT_INTERNAL, "alert_type": "SOS"}}
        redacted = redact_shared_frame(frame, _mixed_translation())
        assert "alert_type" not in redacted["data"]

    def test_alerts_follow_the_unit_not_the_socket(self):
        translation = _mixed_translation()
        own = {"message_type": "alert", "data": {"device_id": _IMEI}}
        shared = {"message_type": "alert", "data": {"device_id": _UNIT_INTERNAL}}
        assert frame_is_exposed(own, translation)
        assert not frame_is_exposed(shared, translation)


@pytest.mark.unit
class TestPausedShareOverWebSocket:
    async def test_a_paused_share_cannot_open_a_stream(
        self, enforced, monkeypatch  # noqa: F811
    ):
        """Pausa = sin ventanas: el handshake se rechaza aunque el token viva."""
        from app.api.deps import authorize_websocket
        from app.core.config import settings

        monkeypatch.setattr(
            "app.api.deps.scope_store",
            _FakeScopeStore(AccessWindow((), shared=True)),
        )
        ws = _ClosingSocket(f"{settings.DATA_TOKEN_WS_SUBPROTOCOL}, {_token()}")
        ok, _, _ = await authorize_websocket(ws, [_REF])
        assert not ok
        assert ws.closed == (1008, "Not authorized")

    async def test_an_expired_share_cannot_open_a_stream(
        self, enforced, monkeypatch  # noqa: F811
    ):
        from app.api.deps import authorize_websocket
        from app.core.config import settings

        monkeypatch.setattr(
            "app.api.deps.scope_store", _FakeScopeStore(_EXPIRED_SHARED)
        )
        ws = _ClosingSocket(f"{settings.DATA_TOKEN_WS_SUBPROTOCOL}, {_token()}")
        ok, _, _ = await authorize_websocket(ws, [_REF])
        assert not ok
        assert ws.closed == (1008, "Not authorized")


@pytest.mark.unit
class TestExpiredPublicLink:
    async def test_a_link_whose_permission_expired_is_rejected(
        self, share_env  # noqa: F811
    ):
        """El `exp` del token no cubre el vencimiento del permiso."""
        from app.api.deps import ShareTokenInvalid, resolve_share_token

        share_env(window=_EXPIRED_SHARED)
        with pytest.raises(ShareTokenInvalid, match="no longer active"):
            await resolve_share_token(_data_token())


# ── Huecos restantes: eventos mixtos, revocación a mitad de stream ──────────


_OWN_UNIT = "323e4567-e89b-12d3-a456-426614174111"
_OWN_UNIT_INTERNAL = "423e4567-e89b-12d3-a456-426614174222"


@pytest.mark.unit
class TestMixedEventsRequest:
    def test_one_shared_unit_rejects_the_whole_events_request(
        self, client: TestClient, shared_store, monkeypatch
    ):
        """Sin 200 parcial: ocultaría que la unidad compartida no da eventos."""
        shared_store.granted[(_SCOPE, "unit", _OWN_UNIT)] = _OWN_UNIT_INTERNAL
        shared_store.windows[_OWN_UNIT_INTERNAL] = AccessWindow.always()
        consultados = []

        async def spy(*args, **kwargs):
            consultados.append(args)
            return [], None

        monkeypatch.setattr("app.api.routes.events.get_events", spy)
        response = client.get(
            f"/api/v1/events?unit_id={_OWN_UNIT}&unit_id={_UNIT}"
            "&from=2026-03-01T00:00:00Z&to=2026-03-31T23:59:59Z",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Not available for shared units"
        assert consultados == []


@pytest.mark.unit
class TestRevocationDuringAnOpenStream:
    async def test_watchdog_keeps_polling_until_the_scope_is_revoked(self, monkeypatch):
        """La revocación llega con el socket ya abierto: se detecta en el
        siguiente ciclo, sin esperar al `exp` del token."""
        import asyncio

        from app.api.routes.stream import revalidation_watchdog

        store = _FakeScopeStore()
        checks = 0
        original = store.is_revoked

        async def counting(scope_ref):
            nonlocal checks
            checks += 1
            if checks == 3:
                store.revoked.add(_SCOPE)
            return await original(scope_ref)

        store.is_revoked = counting
        monkeypatch.setattr("app.api.deps.scope_store", store)
        monkeypatch.setattr("app.api.routes.stream.settings.SCOPE_REVALIDATE_SECS", 0)

        reason = await asyncio.wait_for(revalidation_watchdog(_translation(True)), 2)
        assert reason == (1008, "Data token revoked")
        assert checks == 3

    async def test_watchdog_closes_when_the_token_expires_mid_stream(self, monkeypatch):
        import asyncio

        from app.api.routes.stream import revalidation_watchdog

        monkeypatch.setattr("app.api.deps.scope_store", _FakeScopeStore())
        monkeypatch.setattr("app.api.routes.stream.settings.SCOPE_REVALIDATE_SECS", 0)
        translation = _translation(True)
        translation.claims = _claims(minutes=-1)

        reason = await asyncio.wait_for(revalidation_watchdog(translation), 2)
        assert reason == (1008, "Data token expired")

    def test_revalidation_interval_has_a_tight_default(self):
        """Es el techo de latencia de la revocación sobre un socket abierto."""
        from app.core.config import Settings

        assert 0 < Settings.model_fields["SCOPE_REVALIDATE_SECS"].default <= 30


@pytest.mark.unit
class TestObservationModeDoesNotRedact:
    def test_shared_rows_keep_their_fields_without_enforcement(self):
        from types import SimpleNamespace

        from app.api.deps import to_external_models
        from app.schemas.communications import CommunicationResponse

        translation = RefTranslation(enforcing=False)
        translation.add(_REF, _IMEI, _SHARED)
        request = SimpleNamespace(state=SimpleNamespace(ref_translation=translation))
        row = CommunicationResponse.model_validate(
            {"id": 1, "device_id": _IMEI, "alert_type": "PANIC"}
        )
        assert to_external_models(request, [row])[0].alert_type == "PANIC"

    def test_shared_frames_are_untouched_without_enforcement(self):
        translation = RefTranslation(enforcing=False)
        translation.add(_REF, _IMEI, _SHARED)
        assert redact_shared_frame(_POSITION, translation) is _POSITION
