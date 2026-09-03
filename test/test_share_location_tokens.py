"""Tests del doble formato de token en compartir ubicación.

Un enlace compartido es público por definición: quien lo tiene, entra. Por eso
lo que se fija aquí es que el IMEI no viaje nunca —ni en el token, ni en la
respuesta— y que el alcance no pueda ser más ancho de un dispositivo.

Los dos formatos conviven durante la migración y se distinguen por el prefijo:
`v4.public.` es data token, `v4.local.` es el heredado.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import pyseto
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from pyseto import Key

from app.api.deps import (
    ShareTokenExpired,
    ShareTokenInvalid,
    resolve_share_token,
)

_KEY = ed25519.Ed25519PrivateKey.generate()
_LEGACY_KEY_B64 = base64.b64encode(b"clave-heredada-de-32-bytes-ok!!!").decode()
_IMEI = "867564050638581"
_REF = "ref-compartida"
_SCOPE = "scope-share-1"


def _public_b64() -> str:
    return base64.b64encode(
        _KEY.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode()


def _data_token(**overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "jti": "jti-share",
        "scope_ref": _SCOPE,
        "aud": "siscom-api",
        "iat": now.isoformat(),
        "nbf": now.isoformat(),
        # 30 min: el destinatario es una persona que abre un enlace, no un
        # cliente que sepa refrescar.
        "exp": (now + timedelta(minutes=30)).isoformat(),
    }
    payload.update(overrides)
    pem = _KEY.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signer = Key.new(version=4, purpose="public", key=pem)
    return pyseto.encode(signer, json.dumps(payload).encode()).decode()


def _legacy_token(**overrides) -> str:
    payload = {
        "scope": "public-location-share",
        "unit_id": "123e4567-e89b-12d3-a456-426614174000",
        "device_id": _IMEI,
        "exp": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
    }
    payload.update(overrides)
    key = Key.new(version=4, purpose="local", key=base64.b64decode(_LEGACY_KEY_B64))
    return pyseto.encode(key, json.dumps(payload).encode()).decode()


class _FakeScopeStore:
    def __init__(self, entries: dict[str, str] | None = None, window=None):
        from app.core.scope_window import AccessWindow

        self.entries = {_REF: _IMEI} if entries is None else entries
        self.window = AccessWindow.always() if window is None else window

    async def resolve_single(self, scope_ref, kind, max_cache_secs):
        if scope_ref != _SCOPE or len(self.entries) != 1:
            return None
        ref, internal_id = next(iter(self.entries.items()))
        return ref, internal_id, self.window


@pytest.fixture
def share_env(monkeypatch):
    """Configura ambos formatos y devuelve un configurador del store."""
    from app.core.data_token import DataTokenVerifier
    from app.utils.paseto_validator import PasetoValidator

    monkeypatch.setattr(
        "app.core.data_token.settings.DATA_TOKEN_PUBLIC_KEY_B64", _public_b64()
    )
    monkeypatch.setattr("app.core.data_token.settings.DATA_TOKEN_KEY_ID", "")
    monkeypatch.setattr("app.api.deps.data_token_verifier", DataTokenVerifier())

    monkeypatch.setattr(
        "app.utils.paseto_validator.settings.SHARE_LOCATION_KEY_B64", _LEGACY_KEY_B64
    )
    monkeypatch.setattr("app.utils.paseto_validator.settings.PASETO_SECRET_KEY", "")
    monkeypatch.setattr("app.api.deps.paseto_validator", PasetoValidator())

    def _configure(entries=None, accept_legacy=True, window=None):
        monkeypatch.setattr(
            "app.api.deps.settings.SHARE_LOCATION_ACCEPT_LEGACY_TOKEN", accept_legacy
        )
        monkeypatch.setattr(
            "app.api.deps.scope_store", _FakeScopeStore(entries, window)
        )

    return _configure


@pytest.mark.unit
class TestDataTokenFormat:
    async def test_resolves_the_shared_device_from_the_scope(self, share_env):
        share_env()
        grant = await resolve_share_token(_data_token())
        assert grant.device_id == _IMEI
        assert grant.device_ref == _REF
        assert not grant.legacy

    async def test_the_token_itself_carries_no_imei(self, share_env):
        """En v4.public la carga va firmada pero EN CLARO.

        El enlace es público, así que cualquiera puede leer el cuerpo del token.
        El IMEI no está porque no hay campo donde ponerlo: solo el puntero
        opaco.
        """
        token = _data_token()
        body = token.split(".")[2]
        decoded = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        assert _IMEI.encode() not in decoded
        assert b"device_id" not in decoded

    async def test_expired_token_is_rejected(self, share_env):
        share_env()
        past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        with pytest.raises(ShareTokenExpired):
            await resolve_share_token(_data_token(exp=past))

    async def test_foreign_audience_is_rejected(self, share_env):
        share_env()
        with pytest.raises(ShareTokenInvalid):
            await resolve_share_token(_data_token(aud="otro-servicio"))

    async def test_revoked_scope_kills_the_link_immediately(self, share_env):
        """`DELETE /units/{id}/share-location` vacía el scope."""
        share_env(entries={})
        with pytest.raises(ShareTokenInvalid):
            await resolve_share_token(_data_token())

    async def test_a_scope_wider_than_one_device_is_refused(self, share_env):
        """Si el alcance trae varios, se deniega en vez de elegir el primero.

        Un enlace público con alcance de flota expondría la flota entera; que
        ese fallo salga como 403 ruidoso es preferible a que funcione.
        """
        share_env(entries={"ref-a": "imei-1", "ref-b": "imei-2"})
        with pytest.raises(ShareTokenInvalid):
            await resolve_share_token(_data_token())


@pytest.mark.unit
class TestLegacyFormat:
    async def test_is_accepted_during_the_migration(self, share_env):
        share_env()
        grant = await resolve_share_token(_legacy_token())
        assert grant.device_id == _IMEI
        assert grant.legacy

    async def test_has_no_reference_space_so_nothing_is_translated(self, share_env):
        """El token viejo trae el IMEI: no hay referencia que devolver."""
        share_env()
        grant = await resolve_share_token(_legacy_token())
        assert grant.device_ref == _IMEI
        assert not grant.translation

    async def test_can_be_closed_by_configuration_alone(self, share_env):
        """Paso final de la migración: sin tocar código."""
        share_env(accept_legacy=False)
        with pytest.raises(ShareTokenInvalid, match="no longer accepted"):
            await resolve_share_token(_legacy_token())

    async def test_data_tokens_keep_working_once_legacy_is_closed(self, share_env):
        share_env(accept_legacy=False)
        assert (await resolve_share_token(_data_token())).device_id == _IMEI


@pytest.mark.unit
class TestFormatsDoNotCrossOver:
    async def test_a_legacy_token_is_not_valid_as_a_data_token(self, share_env):
        """Cada formato se verifica con su clave: aceptar ambos no debilita."""
        share_env(accept_legacy=False)
        with pytest.raises(ShareTokenInvalid):
            await resolve_share_token(_legacy_token())

    async def test_garbage_is_rejected(self, share_env):
        share_env()
        with pytest.raises(ShareTokenInvalid):
            await resolve_share_token("v4.public.basura")

    async def test_empty_token_is_rejected(self, share_env):
        share_env()
        with pytest.raises(ShareTokenInvalid):
            await resolve_share_token("")


@pytest.mark.unit
class TestGrantTranslation:
    async def test_outgoing_frames_carry_the_reference(self, share_env):
        """Las tramas del WebSocket público salen con la referencia."""
        from app.api.deps import translate_ws_message

        share_env()
        grant = await resolve_share_token(_data_token())
        frame = translate_ws_message(
            {"data": {"device_id": _IMEI, "speed": 40}}, grant.translation
        )
        assert frame["data"]["device_id"] == _REF
        assert _IMEI not in json.dumps(frame)


@pytest.mark.unit
class TestShareStoreOutage:
    """Un corte de Valkey no es un enlace roto.

    Devolver 403 a quien abre un enlace compartido le dice que su enlace ya no
    vale y deja de intentarlo. Si lo que falla es nuestra capacidad de
    comprobarlo, la respuesta correcta es "vuelve luego".
    """

    @pytest.fixture
    def store_down(self, share_env, monkeypatch):
        from app.services.scope_store import ScopeStoreUnavailable

        class _DownStore:
            async def resolve_single(self, *args, **kwargs):
                raise ScopeStoreUnavailable("Valkey no disponible")

        share_env()
        monkeypatch.setattr("app.api.deps.scope_store", _DownStore())

    async def test_outage_is_reported_apart_from_an_invalid_token(self, store_down):
        from app.api.deps import ShareStoreUnavailable

        with pytest.raises(ShareStoreUnavailable):
            await resolve_share_token(_data_token())

    async def test_outage_does_not_masquerade_as_invalid(self, store_down):
        """`ShareStoreUnavailable` hereda de `ShareTokenError`, no de Invalid."""
        from app.api.deps import ShareStoreUnavailable

        assert not issubclass(ShareStoreUnavailable, ShareTokenInvalid)

    async def test_the_legacy_path_does_not_touch_valkey(self, store_down):
        """El token heredado se valida con su clave; el store no interviene."""
        grant = await resolve_share_token(_legacy_token())
        assert grant.device_id == _IMEI
