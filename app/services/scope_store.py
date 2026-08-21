"""
Resolución de `scope_ref` → refs autorizados contra Valkey.

Contrato acordado con siscom-admin-api:

- `dt:scope:<scope_ref>:dev`  → HASH `device_ref` → `device_id`
- `dt:scope:<scope_ref>:unit` → HASH `unit_ref`   → `unit_id`

Un solo `HGET` **autoriza y resuelve a la vez**: si devuelve `nil` el scope no
concede esa referencia; si devuelve un valor, ese es el identificador interno
con el que consultar la base de datos. Así no hace falta ni migrar tablas ni
llamar a admin-api en el camino caliente.

Reglas que este módulo respeta a propósito:

1. **`HGET` por ref pedido, nunca `HGETALL`.** Una flota grande no se trae
   entera a memoria para resolver tres dispositivos.
2. **`nil` = denegado.** Que el campo o la clave no existan significa scope
   revocado o caducado, no "permitir". La revocación en admin-api es un `DEL`,
   así que confundir ausencia con permiso anularía el único mecanismo de
   revocación.
3. **Fallo de Valkey = denegado, pero dicho aparte.** El fail closed no se
   relaja, pero un corte se propaga como `ScopeStoreUnavailable` en vez de
   confundirse con "no autorizado": el primero es un problema de este servicio
   y el segundo de la credencial, y quien llama debe poder distinguirlos.
4. **Solo se lee `dt:scope:*`.** El índice inverso `dt:owner:*` de admin-api
   queda fuera del alcance de este servicio por diseño, y su ACL de Valkey
   debe reforzarlo. Aprender de él sería aprender de clientes.
"""

import logging
import time
from typing import Literal

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

RefKind = Literal["dev", "unit"]


class ScopeStoreUnavailable(Exception):
    """No se pudo consultar Valkey: sin configurar, caído o con timeout.

    Se distingue a propósito de "la referencia no está en el scope". Las dos
    deniegan el acceso —el fail closed no se relaja—, pero significan cosas
    distintas para quien llama: una es un problema de credencial y la otra un
    problema de este servicio, y confundirlas manda al cliente a reintentar
    con otra credencial un fallo que ninguna credencial arregla.
    """


# (scope_ref, kind, ref) → (id interno o None, instante de caducidad monotónico)
_CacheKey = tuple[str, str, str]

# Marcador de campo para las entradas de `resolve_single`, que no consultan una
# referencia concreta. No puede colisionar con una referencia real.
_SINGLE_SENTINEL = "\x00__single__"


class ScopeStore:
    """Cliente de solo lectura sobre las claves de scope en Valkey."""

    def __init__(self) -> None:
        self._client: Redis | None = None
        self._cache: dict[_CacheKey, tuple[str | None, float]] = {}

    def _connect(self) -> Redis | None:
        if self._client is not None:
            return self._client

        if not settings.VALKEY_URL:
            logger.error("VALKEY_URL no configurada: no se puede resolver el scope")
            return None

        self._client = Redis.from_url(
            settings.VALKEY_URL,
            socket_timeout=settings.VALKEY_TIMEOUT_SECS,
            socket_connect_timeout=settings.VALKEY_TIMEOUT_SECS,
            decode_responses=True,
        )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _cache_lookup(self, key: _CacheKey) -> tuple[bool, str | None]:
        """Devuelve (había_entrada, valor). Distingue "no cacheado" de "nil"."""
        entry = self._cache.get(key)
        if entry is None:
            return False, None

        resolved, expires_at = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(key, None)
            return False, None
        return True, resolved

    def _cache_store(self, key: _CacheKey, resolved: str | None, ttl: float) -> None:
        if ttl <= 0:
            return
        self._cache[key] = (resolved, time.monotonic() + ttl)

    async def resolve(
        self,
        scope_ref: str,
        kind: RefKind,
        ref: str,
        max_cache_secs: float,
    ) -> str | None:
        """Autoriza y resuelve una referencia en una sola operación.

        Args:
            scope_ref: referencia opaca de alcance, sacada del data token.
            kind: "dev" para dispositivos, "unit" para unidades.
            ref: la referencia opaca que el cliente quiere leer.
            max_cache_secs: techo de caché para esta consulta. Quien llama debe
                pasar `min(SCOPE_CACHE_TTL_SECS, exp − ahora)`: el emisor
                recorta el `exp` al siguiente límite de ventana horaria, y una
                caché fija se comería esa precisión.

        Returns:
            El identificador interno (`device_id` / `unit_id`) si el scope
            concede la referencia, o `None` si no la concede: campo ausente o
            clave revocada.

        Raises:
            ScopeStoreUnavailable: si no se pudo consultar Valkey. También
                deniega el acceso, pero por un motivo que no es del cliente.
        """
        cache_key: _CacheKey = (scope_ref, kind, ref)

        cached, value = self._cache_lookup(cache_key)
        if cached:
            return value

        client = self._connect()
        if client is None:
            raise ScopeStoreUnavailable("VALKEY_URL no configurada")

        try:
            resolved = await client.hget(f"dt:scope:{scope_ref}:{kind}", ref)
        except Exception as e:
            # Se propaga en vez de devolver None, y sin cachear: un corte no
            # debe quedar congelado como denegación durante los siguientes
            # 30 s, ni convertirse en un 403 que le diga al cliente que su
            # credencial es el problema.
            logger.error(f"Error resolviendo el scope en Valkey: {e}")
            raise ScopeStoreUnavailable(str(e)) from e

        self._cache_store(
            cache_key, resolved, min(max_cache_secs, settings.SCOPE_CACHE_TTL_SECS)
        )
        return resolved

    async def resolve_single(
        self, scope_ref: str, kind: RefKind, max_cache_secs: float
    ) -> tuple[str, str] | None:
        """Resuelve un scope que debe contener **exactamente una** referencia.

        Es el caso de compartir ubicación: la URL pública solo lleva el token,
        sin referencia, así que no hay campo por el que preguntar con `HGET` y
        hay que leer el hash entero.

        Leerlo entero es aceptable aquí, y solo aquí, porque un enlace
        compartido es por definición de un dispositivo. Y esa suposición se
        **comprueba** en vez de darse por buena: si el hash trae más de una
        entrada se deniega. Si admin-api emitiera por error un token de
        compartir con alcance de flota, un enlace público expondría la flota
        entera; preferimos que ese fallo se convierta en un 403 ruidoso.

        Returns:
            (referencia, id_interno) si el scope contiene exactamente una, o
            `None` si no: vacío, revocado, o más de una entrada.

        Raises:
            ScopeStoreUnavailable: si no se pudo consultar Valkey.
        """
        cache_key: _CacheKey = (scope_ref, kind, _SINGLE_SENTINEL)

        cached, value = self._cache_lookup(cache_key)
        if cached:
            return None if value is None else tuple(value.split("\x00", 1))  # type: ignore[return-value]

        client = self._connect()
        if client is None:
            raise ScopeStoreUnavailable("VALKEY_URL no configurada")

        try:
            entries = await client.hgetall(f"dt:scope:{scope_ref}:{kind}")
        except Exception as e:
            logger.error(f"Error resolviendo el scope de compartir en Valkey: {e}")
            raise ScopeStoreUnavailable(str(e)) from e

        resolved: str | None = None
        if len(entries) == 1:
            ref, internal_id = next(iter(entries.items()))
            resolved = f"{ref}\x00{internal_id}"
        elif len(entries) > 1:
            logger.error(
                f"Scope de compartir con {len(entries)} referencias: se esperaba "
                "exactamente una. Denegado."
            )

        self._cache_store(
            cache_key, resolved, min(max_cache_secs, settings.SCOPE_CACHE_TTL_SECS)
        )
        return None if resolved is None else tuple(resolved.split("\x00", 1))  # type: ignore[return-value]


scope_store = ScopeStore()
