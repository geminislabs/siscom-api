"""Tests del data token del plano de datos.

Lo que estos tests fijan no es "el camino feliz funciona", sino las propiedades
de las que depende el aislamiento entre marcas rivales:

- fail-closed en todos los modos de fallo, incluida la caída de Valkey;
- rechazo de la petición entera, nunca filtrado parcial;
- el IMEI no sale al cliente cuando la exigencia está activada.
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
    RefTranslation,
    collect_requested_refs,
    extract_bearer_token,
    translate_ws_message,
)
from app.core.data_token import (
    DataTokenVerifier,
    ExpiredDataToken,
    InvalidDataToken,
)

# ── Material criptográfico de test ──────────────────────────────────────────

_SIGNING_KEY = ed25519.Ed25519PrivateKey.generate()
_OTHER_KEY = ed25519.Ed25519PrivateKey.generate()


def _public_b64(private_key) -> str:
    pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(pem).decode()


def _make_token(private_key=_SIGNING_KEY, kid: str | None = None, **overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "jti": "jti-abc123",
        "scope_ref": "scope-xyz",
        "aud": "siscom-api",
        "iat": now.isoformat(),
        "nbf": now.isoformat(),
        "exp": (now + timedelta(minutes=10)).isoformat(),
    }
    payload.update(overrides)

    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    signer = Key.new(version=4, purpose="public", key=pem)
    footer = json.dumps({"kid": kid}).encode() if kid else b""
    return pyseto.encode(signer, json.dumps(payload).encode(), footer=footer).decode()


@pytest.fixture
def verifier(monkeypatch):
    """Verificador configurado con la clave pública de test."""

    def _build(public_b64: str | None = None, key_id: str = ""):
        monkeypatch.setattr(
            "app.core.data_token.settings.DATA_TOKEN_PUBLIC_KEY_B64",
            public_b64 if public_b64 is not None else _public_b64(_SIGNING_KEY),
        )
        monkeypatch.setattr("app.core.data_token.settings.DATA_TOKEN_KEY_ID", key_id)
        monkeypatch.setattr(
            "app.core.data_token.settings.DATA_TOKEN_AUDIENCE", "siscom-api"
        )
        return DataTokenVerifier()

    return _build


# ── Verificación del token ──────────────────────────────────────────────────


@pytest.mark.unit
class TestDataTokenVerification:
    def test_accepts_a_well_formed_token(self, verifier):
        claims = verifier().verify(_make_token())
        assert claims.jti == "jti-abc123"
        assert claims.scope_ref == "scope-xyz"

    def test_rejects_a_token_signed_by_another_key(self, verifier):
        """Solo verificamos: un emisor que no sea admin-api no vale."""
        with pytest.raises(InvalidDataToken):
            verifier().verify(_make_token(private_key=_OTHER_KEY))

    def test_rejects_a_tampered_payload(self, verifier):
        token = _make_token()
        # Cambiar un carácter del cuerpo invalida la firma Ed25519.
        head, body = token.split("v4.public.")
        tampered = f"{head}v4.public.{'A' if body[0] != 'A' else 'B'}{body[1:]}"
        with pytest.raises(InvalidDataToken):
            verifier().verify(tampered)

    def test_rejects_a_foreign_audience(self, verifier):
        """Un token emitido para otro servicio del mismo emisor no sirve aquí."""
        with pytest.raises(InvalidDataToken, match="audience"):
            verifier().verify(_make_token(aud="siscom-admin-api"))

    def test_rejects_an_expired_token(self, verifier):
        past = datetime.now(UTC) - timedelta(minutes=1)
        with pytest.raises(ExpiredDataToken):
            verifier().verify(_make_token(exp=past.isoformat()))

    def test_rejects_a_token_that_is_not_valid_yet(self, verifier):
        future = datetime.now(UTC) + timedelta(minutes=5)
        with pytest.raises(InvalidDataToken, match="not valid yet"):
            verifier().verify(_make_token(nbf=future.isoformat()))

    def test_rejects_a_token_without_scope_ref(self, verifier):
        with pytest.raises(InvalidDataToken, match="scope_ref"):
            verifier().verify(_make_token(scope_ref=""))

    def test_rejects_a_token_without_jti(self, verifier):
        """Sin `jti` no hay forense posible al cruzarlo con admin-api."""
        with pytest.raises(InvalidDataToken, match="jti"):
            verifier().verify(_make_token(jti=""))

    def test_unconfigured_verifier_refuses_every_token(self, verifier):
        """Sin clave pública no se acepta nada: fail closed, no fail open."""
        unconfigured = verifier(public_b64="")
        assert not unconfigured.is_configured
        with pytest.raises(InvalidDataToken):
            unconfigured.verify(_make_token())


@pytest.mark.unit
class TestKeyRotationHooks:
    def test_matching_kid_is_accepted(self, verifier):
        assert verifier(key_id="k1").verify(_make_token(kid="k1")).jti

    def test_unknown_kid_is_rejected_before_signature_check(self, verifier):
        """El footer se lee sin verificar, que es justo para elegir la clave."""
        with pytest.raises(InvalidDataToken, match="Unknown key id"):
            verifier(key_id="k2").verify(_make_token(kid="k1"))

    def test_kid_is_ignored_when_not_configured(self, verifier):
        assert verifier(key_id="").verify(_make_token(kid="cualquiera")).jti


@pytest.mark.unit
class TestExpiryBoundsTheCache:
    def test_seconds_until_expiry_drives_the_cache_ceiling(self, verifier):
        """El emisor recorta el `exp`; la caché no puede sobrevivirlo."""
        soon = datetime.now(UTC) + timedelta(seconds=5)
        claims = verifier().verify(_make_token(exp=soon.isoformat()))
        assert 0 < claims.seconds_until_expiry() <= 5

    def test_expiry_is_never_negative(self, verifier):
        claims = verifier().verify(_make_token())
        far_future = datetime.now(UTC) + timedelta(days=1)
        assert claims.seconds_until_expiry(now=far_future) == 0.0


# ── Transporte de la credencial ─────────────────────────────────────────────


@pytest.mark.unit
class TestBearerExtraction:
    def test_extracts_a_bearer_token(self):
        assert extract_bearer_token("Bearer abc.def") == "abc.def"

    def test_scheme_is_case_insensitive(self):
        assert extract_bearer_token("bearer abc") == "abc"

    def test_ignores_other_schemes(self):
        assert extract_bearer_token("Basic dXNlcjpwYXNz") is None

    def test_handles_a_missing_header(self):
        assert extract_bearer_token(None) is None

    def test_handles_an_empty_credential(self):
        assert extract_bearer_token("Bearer   ") is None


@pytest.mark.unit
class TestRequestedRefCollection:
    class _Query:
        def __init__(self, data):
            self._data = data

        def getlist(self, key):
            return self._data.get(key, [])

    def test_collects_repeated_query_params(self):
        refs = collect_requested_refs(
            {}, self._Query({"device_ids": ["ref-a", "ref-b"]})
        )
        assert refs == [("dev", "ref-a"), ("dev", "ref-b")]

    def test_collects_comma_separated_values(self):
        refs = collect_requested_refs({}, self._Query({"device_ids": ["ref-a,ref-b"]}))
        assert refs == [("dev", "ref-a"), ("dev", "ref-b")]

    def test_collects_path_params(self):
        refs = collect_requested_refs({"device_id": "ref-a"}, self._Query({}))
        assert refs == [("dev", "ref-a")]

    def test_units_are_a_separate_space(self):
        """`/events` direcciona por unit_ref, no por device_ref."""
        refs = collect_requested_refs({}, self._Query({"unit_id": ["u-1"]}))
        assert refs == [("unit", "u-1")]

    def test_deduplicates_without_losing_order(self):
        refs = collect_requested_refs(
            {"device_id": "ref-a"}, self._Query({"device_ids": ["ref-a", "ref-b"]})
        )
        assert refs == [("dev", "ref-a"), ("dev", "ref-b")]


# ── La respuesta sale en el mismo espacio en que entró ──────────────────────


@pytest.mark.unit
class TestWebSocketFrameTranslation:
    @staticmethod
    def _translation():
        translation = RefTranslation()
        translation.add("ref-a", "867564050638581")
        return translation

    def test_translates_device_id_at_the_root(self):
        out = translate_ws_message(
            {"device_id": "867564050638581", "speed": 40}, self._translation()
        )
        assert out == {"device_id": "ref-a", "speed": 40}

    def test_translates_device_id_nested_under_data(self):
        """Los mensajes de posiciones lo traen bajo `data`."""
        out = translate_ws_message(
            {"data": {"device_id": "867564050638581"}}, self._translation()
        )
        assert out["data"]["device_id"] == "ref-a"

    def test_translates_alert_shape_with_a_doubly_nested_id(self):
        out = translate_ws_message(
            {
                "message_type": "alert",
                "data": {"payload": {"device_id": "867564050638581"}},
            },
            self._translation(),
        )
        assert out["data"]["payload"]["device_id"] == "ref-a"

    def test_leaves_unknown_ids_untouched(self):
        """Un IMEI que no está en la traducción no se inventa una referencia."""
        out = translate_ws_message({"device_id": "otro"}, self._translation())
        assert out["device_id"] == "otro"

    def test_is_a_no_op_without_an_active_translation(self):
        """En modo observación el coste por trama es una comprobación booleana."""
        message = {"device_id": "867564050638581"}
        assert translate_ws_message(message, RefTranslation()) is message

    def test_does_not_mutate_the_broker_message(self):
        """El mensaje del broker lo comparten todos los sockets suscritos.

        Mutarlo en sitio traduciría el IMEI al espacio de referencias del primer
        cliente y se lo serviría a los demás.
        """
        original = {"data": {"device_id": "867564050638581"}}
        translate_ws_message(original, self._translation())
        assert original["data"]["device_id"] == "867564050638581"


@pytest.mark.unit
class TestRefTranslation:
    def test_round_trips_both_directions(self):
        translation = RefTranslation()
        translation.add("ref-a", "imei-1")
        assert translation.id_by_ref["ref-a"] == "imei-1"
        assert translation.to_external("imei-1") == "ref-a"

    def test_is_falsy_when_empty(self):
        """Vacía significa "sin exigencia": los valores pasan tal cual."""
        assert not RefTranslation()
        assert RefTranslation().to_external("imei-1") == "imei-1"
