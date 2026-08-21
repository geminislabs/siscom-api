"""Tests de la ventana de migración de claves de compartir ubicación.

Durante la transición el validador acepta la clave dedicada nueva
(`SHARE_LOCATION_KEY_B64`) y la heredada (`PASETO_SECRET_KEY`, compartida con
siscom-admin-api). Cerrar la migración consiste en vaciar la heredada del
entorno, sin tocar código: eso es lo que estos tests fijan.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import pyseto
import pytest
from pyseto import Key

from app.utils.paseto_validator import InvalidToken, PasetoValidator

NEW_KEY = base64.b64encode(b"share-location-key-32-bytes-ok!!").decode()
LEGACY_KEY = base64.b64encode(b"legacy-admin-shared-key-32-byte!").decode()


def _make_token(key_b64: str, **overrides) -> str:
    payload = {
        "scope": "public-location-share",
        "unit_id": "123e4567-e89b-12d3-a456-426614174000",
        "device_id": "867564050638581",
        "exp": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }
    payload.update(overrides)
    key = Key.new(version=4, purpose="local", key=base64.b64decode(key_b64))
    return pyseto.encode(key, json.dumps(payload).encode()).decode()


@pytest.fixture
def configure_keys(monkeypatch):
    """Configura las claves del validador y devuelve una instancia nueva."""

    def _configure(new: str = "", legacy: str = ""):
        monkeypatch.setattr(
            "app.utils.paseto_validator.settings.SHARE_LOCATION_KEY_B64", new
        )
        monkeypatch.setattr(
            "app.utils.paseto_validator.settings.PASETO_SECRET_KEY", legacy
        )
        return PasetoValidator()

    return _configure


@pytest.mark.unit
class TestMigrationWindow:
    """Con ambas claves configuradas, se aceptan tokens firmados con cualquiera."""

    def test_accepts_token_signed_with_new_key(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy=LEGACY_KEY)
        payload = validator.validate(_make_token(NEW_KEY))
        assert payload["device_id"] == "867564050638581"

    def test_accepts_token_signed_with_legacy_key(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy=LEGACY_KEY)
        payload = validator.validate(_make_token(LEGACY_KEY))
        assert payload["device_id"] == "867564050638581"

    def test_new_key_is_tried_first(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy=LEGACY_KEY)
        assert [name for name, _ in validator.keys] == [
            "SHARE_LOCATION_KEY_B64",
            "PASETO_SECRET_KEY",
        ]

    def test_rejects_token_signed_with_an_unrelated_key(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy=LEGACY_KEY)
        other = base64.b64encode(b"some-other-unrelated-key-32-bytes").decode()[:44]
        other = base64.b64encode(b"some-other-unrelated-key-32-byte!").decode()
        with pytest.raises(InvalidToken):
            validator.validate(_make_token(other))


@pytest.mark.unit
class TestMigrationClosed:
    """Vaciar PASETO_SECRET_KEY cierra la escalada, sin cambios de código."""

    def test_legacy_tokens_stop_being_accepted(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy="")
        with pytest.raises(InvalidToken):
            validator.validate(_make_token(LEGACY_KEY))

    def test_new_tokens_keep_working(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy="")
        assert validator.validate(_make_token(NEW_KEY))["scope"] == (
            "public-location-share"
        )

    def test_only_the_dedicated_key_remains_loaded(self, configure_keys):
        validator = configure_keys(new=NEW_KEY, legacy="")
        assert [name for name, _ in validator.keys] == ["SHARE_LOCATION_KEY_B64"]


@pytest.mark.unit
class TestKeyConfiguration:
    def test_legacy_only_still_works_before_the_new_key_is_deployed(
        self, configure_keys
    ):
        validator = configure_keys(new="", legacy=LEGACY_KEY)
        assert validator.validate(_make_token(LEGACY_KEY))["device_id"]

    def test_no_key_configured_is_a_startup_error(self, configure_keys):
        with pytest.raises(RuntimeError, match="No share-location key configured"):
            configure_keys(new="", legacy="")

    def test_malformed_new_key_is_a_startup_error(self, configure_keys):
        """La clave nueva sí es estricta: un valor mal copiado no arranca."""
        with pytest.raises(RuntimeError, match="SHARE_LOCATION_KEY_B64"):
            configure_keys(new="no-es-base64-valido!!", legacy=LEGACY_KEY)

    def test_new_key_of_wrong_length_is_a_startup_error(self, configure_keys):
        short = base64.b64encode(b"demasiado-corta").decode()
        with pytest.raises(RuntimeError, match="SHARE_LOCATION_KEY_B64"):
            configure_keys(new=short, legacy=LEGACY_KEY)

    def test_legacy_key_stays_bug_compatible(self, configure_keys, caplog):
        """La heredada NO es estricta: romperla tumbaría producción.

        El código anterior decodificaba con `base64.b64decode` laxo, que
        descarta los caracteres no válidos en silencio. Valores que no son
        base64 real llevan tiempo en uso, así que se conservan — con aviso.
        """
        # Base64 válido pero de longitud equivocada: es la forma que tiene la
        # clave heredada real, que decodifica a menos de 32 bytes.
        validator = configure_keys(
            new="", legacy=base64.b64encode(b"clave-heredada-corta").decode()
        )
        assert [name for name, _ in validator.keys] == ["PASETO_SECRET_KEY"]
        assert any("más débil de lo previsto" in r.message for r in caplog.records)
