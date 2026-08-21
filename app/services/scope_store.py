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
3. **Fallo de Valkey = denegado.** Fail closed, también cuando la causa es un
   timeout o una caída del store.
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

# (scope_ref, kind, ref) → (id interno o None, instante de caducidad monotónico)
_CacheKey = tuple[str, str, str]


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
            concede la referencia. `None` en cualquier otro caso: campo
            ausente, clave revocada, error o timeout de Valkey, o store caído.
        """
        cache_key: _CacheKey = (scope_ref, kind, ref)

        cached, value = self._cache_lookup(cache_key)
        if cached:
            return value

        client = self._connect()
        if client is None:
            return None

        try:
            resolved = await client.hget(f"dt:scope:{scope_ref}:{kind}", ref)
        except Exception as e:
            # Fail closed y sin cachear: un corte de Valkey no debe quedar
            # congelado como denegación durante los siguientes 30 s, ni como
            # permiso.
            logger.error(f"Error resolviendo el scope en Valkey: {e}")
            return None

        self._cache_store(
            cache_key, resolved, min(max_cache_secs, settings.SCOPE_CACHE_TTL_SECS)
        )
        return resolved


scope_store = ScopeStore()
