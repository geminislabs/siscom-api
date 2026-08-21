"""
Dependencias de autorización del plano de datos.

El flujo, idéntico en HTTP y en WebSocket:

1. Sacar el data token del transporte (cabecera `Authorization` en HTTP,
   subprotocolo en WebSocket).
2. Verificar firma y claims → `scope_ref` y `jti`.
3. Por cada referencia pedida, un `HGET` contra Valkey que **autoriza y
   resuelve a la vez**: `device_ref` → `device_id`.
4. Registrar el `jti` en el log de acceso.

Los identificadores viajan en dos espacios distintos y no hay que mezclarlos:

- **Espacio externo** (`device_ref`, `unit_ref`): opaco, es lo único que ve el
  navegador y lo único que aparece en los logs de acceso.
- **Espacio interno** (`device_id` = IMEI, `unit_id`): con lo que están
  indexadas nuestras tablas.

La traducción ocurre aquí, en los bordes, nunca en `repository.py`. Y va en las
dos direcciones: la petición entra en refs y **la respuesta sale en refs**, o el
IMEI volvería al navegador por la puerta de atrás y todo el cambio de
identificadores no habría servido de nada.

Este servicio nunca sabe de quién es el scope. `jti` es opaco aquí: admin-api
guarda `jti → operador → motivo` por su lado, y solo cruzando ambos registros
hay trazabilidad. Por separado, ninguno identifica a nadie.
"""

import logging
from datetime import datetime

from fastapi import HTTPException, Request, WebSocket, status

from app.core.config import settings
from app.core.data_token import (
    DataTokenClaims,
    ExpiredDataToken,
    InvalidDataToken,
    data_token_verifier,
)
from app.services.scope_store import RefKind, scope_store
from app.utils.paseto_validator import ExpiredToken, InvalidToken, paseto_validator

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "bearer "

# Parámetros que transportan referencias, y el HASH de scope que las resuelve.
# Al ir por nombre de parámetro, un endpoint nuevo que reutilice estos nombres
# queda cubierto automáticamente; uno que invente otro nombre NO, y por eso esta
# tabla debe crecer con la API.
_REF_PARAMS: dict[str, RefKind] = {
    "device_id": "dev",
    "device_ids": "dev",
    "unit_id": "unit",
}


class RefTranslation:
    """Traducción bidireccional entre referencias externas e ids internos.

    Se construye durante la autorización, que es cuando tenemos ambos lados de
    cada par, y acompaña a la petición para poder devolver la respuesta en el
    mismo espacio en el que llegó.
    """

    def __init__(self) -> None:
        self.id_by_ref: dict[str, str] = {}
        self.ref_by_id: dict[str, str] = {}

    def add(self, ref: str, internal_id: str) -> None:
        self.id_by_ref[ref] = internal_id
        self.ref_by_id[internal_id] = ref

    def to_external(self, internal_id: str) -> str:
        """Traduce de vuelta al espacio externo.

        Sin traducción conocida devuelve el valor tal cual: es el caso de
        `DATA_TOKEN_ENFORCED=False`, donde el cliente sigue hablando en ids
        internos y no hay nada que traducir.
        """
        return self.ref_by_id.get(internal_id, internal_id)

    def __bool__(self) -> bool:
        return bool(self.id_by_ref)


def extract_bearer_token(authorization: str | None) -> str | None:
    """Extrae el token de una cabecera `Authorization: Bearer <token>`."""
    if not authorization:
        return None
    if not authorization.lower().startswith(_BEARER_PREFIX):
        return None
    return authorization[len(_BEARER_PREFIX) :].strip() or None


def accepted_ws_markers() -> list[str]:
    """Marcadores de subprotocolo aceptados, el preferido primero."""
    markers = [settings.DATA_TOKEN_WS_SUBPROTOCOL]
    markers += [
        m.strip()
        for m in settings.DATA_TOKEN_WS_SUBPROTOCOL_ALIASES.split(",")
        if m.strip()
    ]
    return list(dict.fromkeys(markers))


def extract_websocket_token(websocket: WebSocket) -> tuple[str | None, str | None]:
    """Extrae el data token del subprotocolo del handshake WebSocket.

    El navegador no permite fijar cabeceras en un WebSocket, así que la vía es
    `Sec-WebSocket-Protocol`. Formato:

        Sec-WebSocket-Protocol: <marcador>, <token>

    Se prefiere al query param porque en v4.public el payload va en claro (solo
    firmado): un token en la URL queda legible en los access logs de uvicorn y
    del ALB, y en cabeceras `Referer`.

    Se acepta más de un marcador (`DATA_TOKEN_WS_SUBPROTOCOL_ALIASES`) para que
    renombrarlo no obligue a desplegar clientes y servidor en el mismo instante.

    Returns:
        (token, marcador_a_devolver). El segundo elemento debe devolverse en
        `websocket.accept(subprotocol=...)` o el navegador cierra la conexión
        nada más abrirla.
    """
    raw = websocket.headers.get("sec-websocket-protocol")
    if not raw:
        return None, None

    offered = [p.strip() for p in raw.split(",") if p.strip()]

    for marker in accepted_ws_markers():
        if marker not in offered:
            continue
        marker_at = offered.index(marker)
        token = offered[marker_at + 1] if marker_at + 1 < len(offered) else None
        # Se hace eco del marcador, nunca del token: el subprotocolo elegido
        # viaja en la respuesta del handshake y acabaría en los logs del proxy.
        return token, marker

    return None, None


def collect_requested_refs(
    path_params: dict, query_params
) -> list[tuple[RefKind, str]]:
    """Recoge las referencias que la petición quiere leer.

    Devuelve pares (tipo, referencia) a partir de los parámetros conocidos, de
    path y de query, incluyendo los repetidos (`?device_ids=a&device_ids=b`).
    """
    requested: list[tuple[RefKind, str]] = []

    for name, kind in _REF_PARAMS.items():
        value = path_params.get(name)
        if value:
            requested.append((kind, str(value)))

        for value in query_params.getlist(name):
            # Los clientes también mandan listas separadas por comas.
            for raw_part in str(value).split(","):
                part = raw_part.strip()
                if part:
                    requested.append((kind, part))

    # Sin duplicados, preservando el orden para que los logs sean estables.
    return list(dict.fromkeys(requested))


def verify_data_token(token: str | None) -> DataTokenClaims:
    """Verifica el token y traduce los fallos a 401."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing data token"
        )
    try:
        return data_token_verifier.verify(token)
    except ExpiredDataToken as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Data token expired"
        ) from e
    except InvalidDataToken as e:
        logger.warning(f"Data token rechazado: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid data token"
        ) from e


def cache_ceiling(claims: DataTokenClaims) -> float:
    """Techo de caché para esta petición.

    El emisor recorta el `exp` al siguiente límite de ventana horaria de teams,
    así que la caché no puede sobrevivir al propio token: con 30 s fijos nos
    comeríamos la precisión que el emisor acaba de ganar.
    """
    return min(float(settings.SCOPE_CACHE_TTL_SECS), claims.seconds_until_expiry())


async def resolve_refs(
    claims: DataTokenClaims, requested: list[tuple[RefKind, str]]
) -> RefTranslation:
    """Autoriza y resuelve todas las referencias pedidas.

    Se rechaza la petición entera en cuanto una referencia no está autorizada,
    en vez de devolver el subconjunto permitido: filtrar en silencio convertiría
    la API en un oráculo de pertenencia, con el que se reconstruye la flota
    ajena a base de sondearla.
    """
    translation = RefTranslation()
    ceiling = cache_ceiling(claims)

    for kind, ref in requested:
        internal_id = await scope_store.resolve(claims.scope_ref, kind, ref, ceiling)
        if internal_id is None:
            logger.warning(
                f"Acceso denegado — jti={claims.jti} tipo={kind} "
                "(referencia no concedida por el scope)"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
            )
        translation.add(ref, internal_id)

    return translation


async def require_data_token(request: Request) -> DataTokenClaims | None:
    """Dependencia de router para los endpoints HTTP del plano de datos.

    Con `DATA_TOKEN_ENFORCED=False` funciona en modo observación: verifica y
    registra lo que llega, pero no rechaza ni traduce. Sirve para desplegar y
    medir cuántas peticiones vendrían sin token antes de romper a los clientes
    que todavía no lo mandan.
    """
    # Siempre presente, aunque vacía: los handlers la consultan sin tener que
    # preguntar antes si la exigencia está activada.
    request.state.ref_translation = RefTranslation()

    token = extract_bearer_token(request.headers.get("authorization"))

    if not settings.DATA_TOKEN_ENFORCED:
        if token is None:
            logger.info(f"[observación] petición sin data token: {request.url.path}")
            return None
        try:
            claims = data_token_verifier.verify(token)
        except Exception as e:
            logger.warning(f"[observación] data token no verificable: {e}")
            return None
        logger.info(f"[observación] jti={claims.jti} {request.url.path}")
        return claims

    claims = verify_data_token(token)
    logger.info(f"jti={claims.jti} {request.method} {request.url.path}")

    request.state.ref_translation = await resolve_refs(
        claims, collect_requested_refs(request.path_params, request.query_params)
    )
    return claims


def translation_of(request: Request) -> RefTranslation:
    """Traducción asociada a la petición en curso."""
    return getattr(request.state, "ref_translation", None) or RefTranslation()


def internal_ids(request: Request, refs: list[str]) -> list[str]:
    """Traduce las referencias de entrada a los ids con los que consultar.

    Sin exigencia activada no hay traducción y los valores pasan tal cual: eso
    es lo que hace que activar el flag sea el único cambio de comportamiento.
    """
    translation = translation_of(request)
    if not translation:
        return refs
    return [translation.id_by_ref.get(ref, ref) for ref in refs]


def to_external_models(request: Request, models: list) -> list:
    """Devuelve los modelos con `device_id` traducido al espacio externo.

    Regla del contrato: la respuesta sale en el mismo espacio en el que entró la
    petición. Sin traducción activa, los modelos se devuelven intactos.
    """
    translation = translation_of(request)
    if not translation:
        return models
    return [
        model.model_copy(update={"device_id": translation.to_external(model.device_id)})
        for model in models
    ]


async def authorize_websocket(
    websocket: WebSocket, device_refs: list[str]
) -> tuple[bool, str | None, RefTranslation]:
    """Autoriza un handshake WebSocket ANTES de aceptarlo.

    Devuelve `(autorizado, marcador_subprotocolo, traducción)`. El marcador debe
    pasarse a `websocket.accept(subprotocol=...)`; si se omite cuando el cliente
    ofreció uno, el navegador cierra la conexión nada más abrirla.

    Un rechazo cierra el handshake con 1008 sin aceptar, así que el cliente no
    llega a tener conexión. No se puede enviar un cuerpo explicativo: antes de
    aceptar no hay canal por el que mandarlo.
    """
    token, subprotocol = extract_websocket_token(websocket)
    translation = RefTranslation()

    if not settings.DATA_TOKEN_ENFORCED:
        if token:
            try:
                claims = data_token_verifier.verify(token)
                logger.info(f"[observación] ws jti={claims.jti}")
            except Exception as e:
                logger.warning(f"[observación] data token de ws no verificable: {e}")
        else:
            logger.info("[observación] conexión WebSocket sin data token")
        return True, subprotocol, translation

    if not token:
        logger.warning("WebSocket rechazado: sin data token en el subprotocolo")
        await websocket.close(code=1008, reason="Missing data token")
        return False, None, translation

    try:
        claims = data_token_verifier.verify(token)
    except ExpiredDataToken:
        await websocket.close(code=1008, reason="Data token expired")
        return False, None, translation
    except InvalidDataToken as e:
        logger.warning(f"WebSocket rechazado: {e}")
        await websocket.close(code=1008, reason="Invalid data token")
        return False, None, translation

    logger.info(f"ws jti={claims.jti} refs={len(device_refs)}")

    ceiling = cache_ceiling(claims)
    for ref in device_refs:
        internal_id = await scope_store.resolve(claims.scope_ref, "dev", ref, ceiling)
        if internal_id is None:
            logger.warning(
                f"WebSocket rechazado — jti={claims.jti} "
                "(referencia no concedida por el scope)"
            )
            await websocket.close(code=1008, reason="Not authorized")
            return False, None, translation
        translation.add(ref, internal_id)

    return True, subprotocol, translation


def translate_ws_message(message, translation: RefTranslation):
    """Traduce los `device_id` de una trama WebSocket al espacio externo.

    A diferencia del HTTP, aquí la traducción es **por mensaje y en caliente**:
    cada trama que llega de Kafka lleva su propio `device_id`, y el cliente solo
    debe ver referencias.

    Se recorre el árbol porque el `device_id` aparece en sitios distintos según
    el topic de origen (ver `_extract_device_id_from_positions` y
    `_extract_device_id_from_alerts` en `app/api/routes/stream.py`): a veces en
    la raíz, a veces bajo `data`, a veces bajo `payload`. Buscarlo en todas
    partes evita que una forma nueva se cuele con el IMEI dentro.

    Sin traducción activa devuelve el mensaje intacto y sin copiarlo, así que en
    modo observación el coste es una comprobación booleana por trama.
    """
    if not translation:
        return message

    if isinstance(message, dict):
        return {
            key: (
                translation.to_external(value)
                if key == "device_id" and isinstance(value, str)
                else translate_ws_message(value, translation)
            )
            for key, value in message.items()
        }

    if isinstance(message, list):
        return [translate_ws_message(item, translation) for item in message]

    return message


# ── Compartir ubicación ─────────────────────────────────────────────────────


class ShareTokenError(Exception):
    """Base de los errores de token de compartir ubicación."""


class ShareTokenExpired(ShareTokenError):
    """Token bien formado pero vencido."""


class ShareTokenInvalid(ShareTokenError):
    """Token ausente, malformado, mal firmado o sin alcance resoluble."""


class ShareGrant:
    """Lo que concede un enlace de compartir: un solo dispositivo, hasta `exp`.

    `device_ref` es lo que se devuelve al cliente; `device_id` lo que se
    consulta contra la base de datos. En el formato heredado ambos coinciden,
    porque el token viejo transporta el IMEI directamente.
    """

    def __init__(self, device_ref: str, device_id: str, expires_at, legacy: bool):
        self.device_ref = device_ref
        self.device_id = device_id
        self.expires_at = expires_at
        self.legacy = legacy

    @property
    def translation(self) -> RefTranslation:
        """Traducción para las tramas de salida del WebSocket público."""
        translation = RefTranslation()
        if not self.legacy:
            translation.add(self.device_ref, self.device_id)
        return translation


async def resolve_share_token(token: str) -> ShareGrant:
    """Resuelve un token de compartir ubicación, en cualquiera de sus formatos.

    Los dos formatos se distinguen por el prefijo, que es inequívoco:

    - `v4.public.` → **data token**. Alcance por `scope_ref`, resuelto contra
      Valkey. El token no lleva IMEI; lo pone el HASH.
    - `v4.local.`  → **formato heredado**, con el `device_id` dentro del propio
      token. Se acepta mientras `SHARE_LOCATION_ACCEPT_LEGACY_TOKEN` siga
      activo; apagarlo cierra el formato antiguo sin tocar código.

    Cada camino se verifica con su propia clave, así que aceptar ambos no
    debilita ninguno: un token del formato viejo no vale como data token ni al
    revés.
    """
    if not token:
        raise ShareTokenInvalid("Empty token")

    if token.startswith("v4.public."):
        try:
            claims = data_token_verifier.verify(token)
        except ExpiredDataToken as e:
            raise ShareTokenExpired(str(e)) from e
        except InvalidDataToken as e:
            raise ShareTokenInvalid(str(e)) from e

        resolved = await scope_store.resolve_single(
            claims.scope_ref, "dev", cache_ceiling(claims)
        )
        if resolved is None:
            logger.warning(
                f"Enlace de compartir sin alcance resoluble — jti={claims.jti}"
            )
            raise ShareTokenInvalid("Share scope is empty, revoked or too broad")

        device_ref, device_id = resolved
        logger.info(f"Enlace de compartir válido — jti={claims.jti}")
        return ShareGrant(device_ref, device_id, claims.expires_at, legacy=False)

    if not settings.SHARE_LOCATION_ACCEPT_LEGACY_TOKEN:
        raise ShareTokenInvalid("Legacy share tokens are no longer accepted")

    try:
        payload = paseto_validator.validate(token)
    except ExpiredToken as e:
        raise ShareTokenExpired(str(e)) from e
    except InvalidToken as e:
        raise ShareTokenInvalid(str(e)) from e

    device_id = payload.get("device_id")
    if not device_id:
        raise ShareTokenInvalid("Missing device_id")

    expires_at_raw = payload.get("exp")
    if not expires_at_raw:
        raise ShareTokenInvalid("Missing exp")

    # En el formato heredado no hay espacio de referencias: el token trae el
    # IMEI, así que se devuelve tal cual y no hay nada que traducir.
    return ShareGrant(
        device_id, device_id, datetime.fromisoformat(expires_at_raw), legacy=True
    )
