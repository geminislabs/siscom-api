"""
Verificación del data token del plano de datos.

siscom-admin-api firma tokens PASETO **v4.public** (Ed25519); este servicio
únicamente los verifica con la clave pública. Nunca firma, y nunca tiene la
clave privada.

Diseño deliberado: el token NO contiene identidad de cliente. Sus claims son
`{jti, scope_ref, aud, iat, nbf, exp}` y nada más. `scope_ref` es una
referencia opaca que este servicio resuelve contra Valkey para saber qué
dispositivos puede leer, sin llegar a saber nunca de quién son. Es lo que
permite que siscom-api no conozca organizaciones, cuentas ni usuarios.

No confundir con `app/utils/paseto_validator.py`, que valida los tokens
v4.local (simétricos) de compartir ubicación. Son dos sistemas distintos con
claves distintas.
"""

import base64
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyseto
from pyseto import Key

from app.core.config import settings

logger = logging.getLogger(__name__)


class DataTokenError(Exception):
    """Base de los errores de data token."""


class InvalidDataToken(DataTokenError):
    """Token ausente, malformado, mal firmado o con claims inválidos."""


class ExpiredDataToken(DataTokenError):
    """Token bien formado y bien firmado, pero fuera de su ventana de validez."""


@dataclass(frozen=True)
class DataTokenClaims:
    """Claims útiles de un data token ya verificado.

    Nótese lo que NO hay aquí: usuario, organización, cuenta, lista de
    dispositivos. `scope_ref` es lo único que identifica el alcance, y es
    opaco.
    """

    jti: str
    scope_ref: str
    expires_at: datetime

    def seconds_until_expiry(self, now: datetime | None = None) -> float:
        """Segundos que le quedan de vida al token (0 si ya venció)."""
        now = now or datetime.now(UTC)
        return max(0.0, (self.expires_at - now).total_seconds())


def _parse_timestamp(payload: dict[str, Any], claim: str) -> datetime:
    raw = payload.get(claim)
    if not raw:
        raise InvalidDataToken(f"Missing {claim} claim")
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as e:
        raise InvalidDataToken(f"Invalid {claim} format") from e

    # Un timestamp sin zona es ambiguo; lo tratamos como UTC en vez de dejar
    # que la comparación falle con un TypeError más adelante.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class DataTokenVerifier:
    """Verifica data tokens v4.public emitidos por siscom-admin-api."""

    def __init__(self) -> None:
        self._key: Key | None = None
        key_b64 = settings.DATA_TOKEN_PUBLIC_KEY_B64
        if not key_b64:
            # No es un error de arranque: mientras DATA_TOKEN_ENFORCED sea
            # False el servicio funciona sin clave. El fallo se produce al
            # intentar verificar, que es cuando importa.
            logger.warning(
                "DATA_TOKEN_PUBLIC_KEY_B64 no configurada: no se pueden "
                "verificar data tokens."
            )
            return

        try:
            pem = base64.b64decode(key_b64, validate=True)
            self._key = Key.new(version=4, purpose="public", key=pem)
        except Exception as e:
            raise RuntimeError(f"Invalid DATA_TOKEN_PUBLIC_KEY_B64: {e}") from e

    @property
    def is_configured(self) -> bool:
        return self._key is not None

    def _check_kid(self, token: str) -> None:
        """Comprueba el `kid` del footer antes de verificar la firma.

        El footer es legible sin verificar, que es justo para lo que sirve:
        elegir la clave. Hoy solo hay una, pero el hueco queda hecho para
        cuando haya rotación, y así un token emitido con otra clave da un error
        claro en vez de un fallo de firma genérico.
        """
        expected = settings.DATA_TOKEN_KEY_ID
        if not expected:
            return

        try:
            footer = pyseto.Token.new(token.encode()).footer
            kid = json.loads(footer.decode()).get("kid") if footer else None
        except Exception as e:
            raise InvalidDataToken(f"Unreadable token footer: {e}") from e

        if kid != expected:
            raise InvalidDataToken(f"Unknown key id: {kid!r}")

    def verify(self, token: str, now: datetime | None = None) -> DataTokenClaims:
        """Verifica firma y claims, y devuelve el alcance concedido.

        Raises:
            InvalidDataToken: firma inválida, claims ausentes, audiencia
                equivocada o token aún no válido.
            ExpiredDataToken: el token ya venció.
        """
        if self._key is None:
            raise InvalidDataToken("Data token verification is not configured")

        if not token:
            raise InvalidDataToken("Empty token")

        self._check_kid(token)

        try:
            decoded = pyseto.decode(self._key, token.encode())
            payload = json.loads(decoded.payload.decode())
        except Exception as e:
            raise InvalidDataToken(f"Invalid or malformed token: {e}") from e

        if not isinstance(payload, dict):
            raise InvalidDataToken("Payload is not a valid dict")

        # La audiencia impide que un token emitido para otro servicio del
        # mismo emisor sirva aquí.
        if payload.get("aud") != settings.DATA_TOKEN_AUDIENCE:
            raise InvalidDataToken(f"Invalid audience: {payload.get('aud')!r}")

        scope_ref = payload.get("scope_ref")
        if not scope_ref or not isinstance(scope_ref, str):
            raise InvalidDataToken("Missing scope_ref")

        jti = payload.get("jti")
        if not jti or not isinstance(jti, str):
            raise InvalidDataToken("Missing jti")

        now = now or datetime.now(UTC)
        expires_at = _parse_timestamp(payload, "exp")
        not_before = _parse_timestamp(payload, "nbf")

        if now < not_before:
            raise InvalidDataToken("Token is not valid yet")

        if now >= expires_at:
            raise ExpiredDataToken("Token expired")

        return DataTokenClaims(jti=jti, scope_ref=scope_ref, expires_at=expires_at)


data_token_verifier = DataTokenVerifier()
