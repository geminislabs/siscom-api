"""
Validador de tokens PASETO v4.local.

Este módulo proporciona funcionalidad para validar tokens PASETO emitidos
por siscom-admin-api para compartir ubicaciones de forma pública.
"""

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

import pyseto
from pyseto import Key

from app.core.config import settings

logger = logging.getLogger(__name__)

# PASETO v4.local usa XChaCha20-Poly1305: la clave es de 32 bytes exactos.
_V4_LOCAL_KEY_BYTES = 32


class InvalidToken(Exception):
    """Errores genéricos de token inválido o corrupto."""

    pass


class ExpiredToken(Exception):
    """El token es válido pero ya expiró."""

    pass


class PasetoValidator:
    """
    Valida tokens PASETO v4.local emitidos por siscom-admin-api.

    Attributes:
        key: Objeto Key de pyseto para validar tokens
    """

    def __init__(self):
        """
        Inicializa el validador con las claves disponibles en el entorno.

        Durante la ventana de migración se aceptan dos claves, y se prueban en
        este orden:

        1. `SHARE_LOCATION_KEY_B64` — clave dedicada a compartir ubicación.
        2. `PASETO_SECRET_KEY` — clave HEREDADA, compartida con
           siscom-admin-api, que también firma sus tokens de servicio
           `internal-*`. Mientras esté configurada aquí, este servicio puede
           emitir tokens administrativos de admin-api.

        Para cerrar la migración basta con vaciar `PASETO_SECRET_KEY` en el
        entorno: no hace falta ningún cambio de código.

        Raises:
            RuntimeError: Si no hay ninguna clave configurada, o si alguna de
                las configuradas tiene un formato inválido.
        """
        self.keys: list[tuple[str, Any]] = []

        # `strict` distingue las dos claves a propósito:
        #
        # - SHARE_LOCATION_KEY_B64 es nueva, así que exigimos el formato
        #   correcto y fallamos al arrancar si no lo es. Una clave mal copiada
        #   produce un validador que rechaza TODOS los tokens sin explicar por
        #   qué, y durante una migración de claves eso es un apagón mudo.
        #
        # - PASETO_SECRET_KEY es la heredada y ya está desplegada. El código
        #   anterior la decodificaba de forma laxa (`base64.b64decode` descarta
        #   en silencio los caracteres no válidos), así que un valor que no sea
        #   base64 real lleva tiempo funcionando como los bytes que salen de esa
        #   decodificación. Mantenemos ese comportamiento para no tumbar el
        #   servicio a mitad de transición, pero avisamos.
        for name, key_b64, strict in (
            ("SHARE_LOCATION_KEY_B64", settings.SHARE_LOCATION_KEY_B64, True),
            ("PASETO_SECRET_KEY", settings.PASETO_SECRET_KEY, False),
        ):
            if not key_b64:
                continue

            try:
                key_bytes = base64.b64decode(key_b64, validate=strict)
            except Exception as e:
                raise RuntimeError(f"Invalid {name} format: {e}") from e

            # La clave de ceros es el marcador de posición que arrastran los
            # `.env.example`. No es débil: es pública. Comprobar solo la
            # longitud la dejaría pasar, porque 32 bytes de ceros miden 32.
            if key_bytes and not any(key_bytes):
                message = f"{name} es la clave de ceros del .env.example"
                if strict:
                    raise RuntimeError(message)
                logger.critical(
                    f"🚨 {message}. Es una clave PÚBLICA: cualquiera puede "
                    "emitir tokens válidos. Trátalo como incidente, no como "
                    "aviso de configuración."
                )

            if len(key_bytes) != _V4_LOCAL_KEY_BYTES:
                message = (
                    f"{name}: PASETO v4.local requiere {_V4_LOCAL_KEY_BYTES} "
                    f"bytes; el valor configurado decodifica a {len(key_bytes)}"
                )
                if strict:
                    raise RuntimeError(message)
                logger.warning(
                    f"⚠️  {message}. La clave heredada no es base64 de "
                    f"{_V4_LOCAL_KEY_BYTES} bytes: se conserva por "
                    "compatibilidad, pero la clave efectiva es más débil de lo "
                    "previsto. Un motivo más para cerrar la migración."
                )

            self.keys.append((name, Key.new(version=4, purpose="local", key=key_bytes)))

        if not self.keys:
            raise RuntimeError(
                "No share-location key configured: set SHARE_LOCATION_KEY_B64 "
                "(or, during migration, PASETO_SECRET_KEY)"
            )

        if any(name == "PASETO_SECRET_KEY" for name, _ in self.keys):
            logger.warning(
                "⚠️  PASETO_SECRET_KEY sigue configurada: clave heredada compartida "
                "con siscom-admin-api. Solo debe estar presente durante la ventana "
                "de migración; elimínala del entorno para cerrarla."
            )

    def _decode(self, token: str) -> bytes:
        """Prueba las claves en orden y devuelve el payload de la primera válida."""
        last_error: Exception | None = None

        for name, key in self.keys:
            try:
                payload = pyseto.decode(key, token).payload
            except Exception as e:  # noqa: PERF203 - probar la siguiente clave
                last_error = e
                continue

            if name == "PASETO_SECRET_KEY":
                logger.warning(
                    "Token de compartir ubicación validado con la clave heredada "
                    "PASETO_SECRET_KEY. Emisor pendiente de migrar a "
                    "SHARE_LOCATION_KEY_B64."
                )
            return payload

        logger.warning(f"Token inválido o malformado: {last_error}")
        raise InvalidToken(f"Invalid or malformed token: {last_error}")

    def validate(self, token: str) -> dict[str, Any]:
        """
        Valida el token PASETO y regresa el payload si es válido.

        Args:
            token: Token PASETO v4.local a validar

        Returns:
            dict: Payload del token con los campos validados

        Raises:
            InvalidToken: Si el token es inválido, corrupto o tiene campos incorrectos
            ExpiredToken: Si el token ha expirado
        """
        raw_payload = self._decode(token)  # bytes
        try:
            payload = json.loads(raw_payload.decode("utf-8"))  # dict
        except Exception as e:
            logger.warning(f"Token inválido o malformado: {e}")
            raise InvalidToken(f"Invalid or malformed token: {e}") from e

        # Verificar que el payload sea un diccionario
        if not isinstance(payload, dict):
            raise InvalidToken("Payload is not a valid dict")

        # Confirmar los campos esperados
        if payload.get("scope") != "public-location-share":
            raise InvalidToken("Invalid token scope")

        if "unit_id" not in payload:
            raise InvalidToken("Missing unit_id in token")

        if "exp" not in payload:
            raise InvalidToken("Missing exp in token")

        # Validación de expiración
        try:
            exp = datetime.fromisoformat(payload["exp"])
        except Exception as e:
            raise InvalidToken("Invalid exp format") from e

        now = datetime.now(UTC)
        if now >= exp:
            logger.info(f"Token expirado. Exp: {exp}, Now: {now}")
            raise ExpiredToken("Token expired")

        logger.info(
            f"Token validado exitosamente para unit_id: {payload.get('unit_id')}"
        )
        return payload


# Instancia global del validador
paseto_validator = PasetoValidator()
