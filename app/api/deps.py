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
from app.core.scope_window import AccessWindow
from app.services.scope_store import RefKind, ScopeStoreUnavailable, scope_store
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

    def __init__(self, *, enforcing: bool = True) -> None:
        # Traducir y exigir son cosas distintas y hay que poder hacer la
        # primera sin la segunda.
        #
        # En modo observación se pueblan igualmente las referencias, porque en
        # cuanto admin-api exponga los `device_ref` el cliente empieza a
        # mandarlos: sin traducción buscaríamos un dispositivo cuyo IMEI es una
        # referencia, no encontraríamos nada, y el mapa se quedaría vacío sin
        # error ni log. Traducir siempre elimina la dependencia de orden de
        # despliegue entre los tres repos.
        #
        # Pero las VENTANAS no se aplican si `enforcing` es falso. Si no, poblar
        # la traducción activaría de rebote los 404, el filtrado de `latest` y
        # el recorte en SQL — enforcement real bajo un flag que promete no
        # cambiar nada.
        self.enforcing = enforcing
        self.id_by_ref: dict[str, str] = {}
        self.ref_by_id: dict[str, str] = {}
        # Ventana temporal concedida para cada identificador interno. Viaja con
        # la traducción porque los dos salen del mismo `HGET` y los dos hacen
        # falta al servir la respuesta.
        self.window_by_id: dict[str, AccessWindow] = {}

    def add(
        self, ref: str, internal_id: str, window: AccessWindow | None = None
    ) -> None:
        self.id_by_ref[ref] = internal_id
        self.ref_by_id[internal_id] = ref
        # `is None`, NO `or`: una ventana vacía es FALSY, así que
        # `window or AccessWindow.always()` convertiría un alcance revocado en
        # acceso ilimitado. Es la colisión entre "no me lo pasaron" y "me
        # pasaron el valor que significa nada".
        self.window_by_id[internal_id] = (
            AccessWindow.always() if window is None else window
        )

    def window_for(self, internal_id: str) -> AccessWindow:
        """Ventana del identificador, o sin límite si no hay traducción activa.

        Sin exigencia el cliente habla en identificadores internos y no hay
        ventana que aplicar, igual que no hay referencia que traducir.
        """
        return self.window_by_id.get(internal_id, AccessWindow.always())

    def live_ids(self, now=None) -> set[str]:
        """Identificadores cuya asignación sigue viva en este instante.

        Es lo que pueden ver `/communications/latest` y el stream: una
        referencia con ventana cerrada conserva su histórico, pero no la
        posición actual de un equipo que ya es de otro.
        """
        return {
            internal_id
            for internal_id, window in self.window_by_id.items()
            if window.allows_now(now)
        }

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
    """Verifica el token y traduce los fallos al código que corresponde.

    401 y 403 dicen "tu credencial no sirve"; 503 dice "este servicio no puede
    contestar ahora". Confundirlos no es cosmético: el cliente de nexus-web
    reacciona a un 401/403 reemitiendo el data token y reintentando, así que
    devolver 401 porque falta una clave en el servidor provoca una tormenta de
    reemisiones contra admin-api para arreglar algo que ninguna credencial
    arregla.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing data token"
        )

    if not data_token_verifier.is_configured:
        logger.error(
            "DATA_TOKEN_ENFORCED está activo pero DATA_TOKEN_PUBLIC_KEY_B64 no "
            "está configurada: no se puede verificar nada."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data plane not configured",
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
        try:
            resolved = await scope_store.resolve(claims.scope_ref, kind, ref, ceiling)
        except ScopeStoreUnavailable as e:
            # Se deniega igual —no se sirve nada sin autorizar—, pero con el
            # código que dice de quién es el problema.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authorization store unavailable",
            ) from e

        if resolved is None:
            logger.warning(
                f"Acceso denegado — jti={claims.jti} tipo={kind} "
                "(referencia no concedida por el scope)"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
            )

        internal_id, window = resolved
        if not window:
            # Reconocida pero sin ninguna ventana: alcance revocado.
            logger.warning(
                f"Acceso denegado — jti={claims.jti} tipo={kind} "
                "(referencia sin ventana de acceso)"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
            )

        translation.add(ref, internal_id, window)

    return translation


async def resolve_for_translation(
    claims: DataTokenClaims, requested: list[tuple[RefKind, str]]
) -> RefTranslation:
    """Resuelve las referencias para TRADUCIR, no para autorizar.

    Es el camino de modo observación. Se diferencia de `resolve_refs` en tres
    cosas, y las tres son deliberadas:

    - **Una referencia que no resuelve no rechaza la petición: pasa tal cual.**
      Es lo que permite que los clientes viejos —que mandan IMEIs— y los nuevos
      —que mandan referencias— funcionen a la vez sin ramas. Un IMEI no es
      campo del hash, así que no resuelve y sigue su camino.
    - **Un corte de Valkey no rompe nada.** Se devuelve lo resuelto hasta ese
      punto: traducir de menos degrada al comportamiento de hoy, que es
      exactamente lo que el modo observación promete.
    - **La traducción sale marcada como no vigente**, así que las ventanas no
      se aplican. Traducir identificadores y acotar accesos son cosas
      distintas.
    """
    translation = RefTranslation(enforcing=False)
    ceiling = cache_ceiling(claims)

    for kind, ref in requested:
        try:
            resolved = await scope_store.resolve(claims.scope_ref, kind, ref, ceiling)
        except ScopeStoreUnavailable as e:
            logger.warning(
                f"[observación] sin traducción por Valkey inaccesible: {e}. "
                "Los identificadores pasan sin traducir."
            )
            break

        if resolved is None:
            # No es una referencia de este alcance —lo más probable, un
            # identificador interno del cliente antiguo—. Pasa sin tocar.
            continue

        internal_id, window = resolved
        translation.add(ref, internal_id, window)

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
    request.state.ref_translation = RefTranslation(
        enforcing=settings.DATA_TOKEN_ENFORCED
    )

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
        request.state.ref_translation = await resolve_for_translation(
            claims, collect_requested_refs(request.path_params, request.query_params)
        )
        return claims

    claims = verify_data_token(token)
    logger.info(f"jti={claims.jti} {request.method} {request.url.path}")

    request.state.ref_translation = await resolve_refs(
        claims, collect_requested_refs(request.path_params, request.query_params)
    )
    return claims


def translation_of(request: Request) -> RefTranslation:
    """Traducción asociada a la petición en curso.

    `is None`, NO `or`. Una traducción vacía es **falsy** —su `__bool__` mira
    si hay referencias—, así que `getattr(...) or RefTranslation()` la
    descartaba y la sustituía por una nueva **con `enforcing=True`**, activando
    las ventanas en modo observación. Cuarta aparición en esta fase del mismo
    error: el valor que significa "nada" comportándose como "todo".
    """
    translation = getattr(request.state, "ref_translation", None)
    if translation is not None:
        return translation

    # La dependencia no llegó a ejecutarse. Se refleja el flag en vez de
    # inventar un default: así este camino no puede ser más ni menos estricto
    # que el configurado.
    return RefTranslation(enforcing=settings.DATA_TOKEN_ENFORCED)


def internal_ids(request: Request, refs: list[str]) -> list[str]:
    """Traduce las referencias de entrada a los ids con los que consultar.

    Sin exigencia activada no hay traducción y los valores pasan tal cual: eso
    es lo que hace que activar el flag sea el único cambio de comportamiento.
    """
    translation = translation_of(request)
    if not translation:
        return refs
    return [translation.id_by_ref.get(ref, ref) for ref in refs]


def live_internal_ids(request: Request, refs: list[str]) -> list[str]:
    """Traduce a identificadores internos y deja solo los de asignación viva.

    Para `/communications/latest`: la posición ACTUAL de un equipo reasignado
    ya no es del dueño anterior, aunque conserve su histórico. Se filtra en vez
    de rechazar la petición entera, por la misma razón que el recorte de
    rangos: el cliente conoce sus propias ventanas, así que aquí no hay ningún
    oráculo de pertenencia que proteger. Pedir cinco equipos y recibir tres no
    le revela nada que no supiera.
    """
    translation = translation_of(request)
    if not translation.enforcing:
        # Traducir sí, filtrar no: en observación las ventanas no se aplican.
        return internal_ids(request, refs)

    live = translation.live_ids()
    return [
        internal_id
        for ref in refs
        if (internal_id := translation.id_by_ref.get(ref, ref)) in live
    ]


def windows_for_request(request: Request) -> dict | None:
    """Ventanas por identificador interno, para acotar la consulta.

    `None` significa "sin restricción temporal" y deja la consulta como estaba
    antes del contrato v1.3. Es lo que devuelve en modo observación, así que
    activar la exigencia es el único cambio de comportamiento.
    """
    translation = translation_of(request)
    if not translation or not translation.enforcing:
        return None
    return dict(translation.window_by_id)


# Los tres estados que el cliente tiene que poder distinguir sin adivinar, y
# por qué cada uno lleva el código que lleva:
#
#   403 → la referencia no está en el alcance. El cliente reemite el token y
#         reintenta UNA vez, porque suele ser un alcance obsoleto que se cura
#         solo al recalcularlo.
#   404 → la referencia es tuya, pero en ese periodo no hay nada que puedas
#         ver. NO debe reintentarse: un token nuevo daría lo mismo.
#   200 con lista vacía → no hubo datos. Es lo único que puede significar sin
#         afirmar algo falso.
#
# Devolver lista vacía por un rango fuera de ventana afirmaría "no hubo
# telemetría en ese periodo" cuando sí la hubo y simplemente no es tuya. El
# recorte PARCIAL en cambio no miente: dar enero–marzo ante una petición de
# enero–diciembre es dar lo tuyo, y el límite superior lo puso quien preguntó.


def _raise_unless_any(request: Request, refs: list[str], visible) -> None:
    """404 salvo que alguna referencia pedida supere el predicado.

    Basta con que una sola aporte algo para seguir adelante: el recorte parcial
    es una respuesta honesta y el cliente conoce sus propias ventanas.
    """
    translation = translation_of(request)
    if not translation or not translation.enforcing:
        return

    for ref in refs:
        if visible(translation.window_for(translation.id_by_ref.get(ref, ref))):
            return

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No data visible for the requested period",
    )


def raise_if_no_live_access(request: Request, refs: list[str]) -> None:
    """Para los endpoints de tiempo presente: `latest` y el stream."""
    _raise_unless_any(request, refs, lambda window: window.allows_now())


def raise_if_range_outside_windows(
    request: Request, refs: list[str], since, until
) -> None:
    """Para los endpoints de histórico con rango explícito."""
    _raise_unless_any(request, refs, lambda window: bool(window.clamp(since, until)))


def raise_if_date_outside_windows(request: Request, refs: list[str], instant) -> None:
    """Para el filtro por fecha suelta de `/devices/{id}/communications`."""
    _raise_unless_any(request, refs, lambda window: window.covers_instant(instant))


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


async def _reject_websocket(
    websocket: WebSocket, code: int, reason: str
) -> tuple[bool, str | None, RefTranslation]:
    """Cierra el handshake sin aceptarlo y devuelve el rechazo."""
    logger.warning(f"WebSocket rechazado ({code}): {reason}")
    await websocket.close(code=code, reason=reason)
    return False, None, RefTranslation()


async def _verify_websocket_claims(
    websocket: WebSocket, token: str | None
) -> DataTokenClaims | tuple[bool, str | None, RefTranslation]:
    """Verifica el token del handshake, o devuelve ya la tupla de rechazo."""
    if not token:
        return await _reject_websocket(websocket, 1008, "Missing data token")

    if not data_token_verifier.is_configured:
        logger.error(
            "DATA_TOKEN_ENFORCED está activo pero DATA_TOKEN_PUBLIC_KEY_B64 no "
            "está configurada: no se puede verificar nada."
        )
        # 1013 (Try Again Later), no 1008: el problema es del servidor.
        return await _reject_websocket(websocket, 1013, "Data plane not configured")

    try:
        return data_token_verifier.verify(token)
    except ExpiredDataToken:
        return await _reject_websocket(websocket, 1008, "Data token expired")
    except InvalidDataToken as e:
        logger.warning(f"Data token de ws rechazado: {e}")
        return await _reject_websocket(websocket, 1008, "Invalid data token")


async def authorize_websocket(
    websocket: WebSocket, device_refs: list[str]
) -> tuple[bool, str | None, RefTranslation]:
    """Autoriza un handshake WebSocket ANTES de aceptarlo.

    Devuelve `(autorizado, marcador_subprotocolo, traducción)`. El marcador debe
    pasarse a `websocket.accept(subprotocol=...)`; si se omite cuando el cliente
    ofreció uno, el navegador cierra la conexión nada más abrirla.

    Un rechazo cierra el handshake sin aceptar, así que el cliente no llega a
    tener conexión. No se puede enviar un cuerpo explicativo —antes de aceptar
    no hay canal por el que mandarlo—, así que el código de cierre es el único
    dato que recibe: **1008 significa "tu credencial no sirve"; 1013,
    "reintenta, el problema es mío"**. Confundirlos manda al cliente a reemitir
    la credencial para arreglar algo que ninguna credencial arregla.
    """
    token, subprotocol = extract_websocket_token(websocket)
    translation = RefTranslation()

    if not settings.DATA_TOKEN_ENFORCED:
        translation = RefTranslation(enforcing=False)
        if token:
            try:
                claims = data_token_verifier.verify(token)
                logger.info(f"[observación] ws jti={claims.jti}")
                # Igual que en HTTP: traducir sí, exigir no. Sin esto, un
                # cliente que ya manda referencias se suscribiría a un
                # `device_id` inexistente y no recibiría ni una trama.
                translation = await resolve_for_translation(
                    claims, [("dev", ref) for ref in device_refs]
                )
            except Exception as e:
                logger.warning(f"[observación] data token de ws no verificable: {e}")
        else:
            logger.info("[observación] conexión WebSocket sin data token")
        return True, subprotocol, translation

    verified = await _verify_websocket_claims(websocket, token)
    if isinstance(verified, tuple):
        return verified
    claims = verified

    logger.info(f"ws jti={claims.jti} refs={len(device_refs)}")

    ceiling = cache_ceiling(claims)
    for ref in device_refs:
        try:
            resolved = await scope_store.resolve(claims.scope_ref, "dev", ref, ceiling)
        except ScopeStoreUnavailable:
            return await _reject_websocket(
                websocket, 1013, "Authorization store unavailable"
            )

        if resolved is None:
            logger.warning(
                f"WebSocket rechazado — jti={claims.jti} "
                "(referencia no concedida por el scope)"
            )
            return await _reject_websocket(websocket, 1008, "Not authorized")

        internal_id, window = resolved

        # El stream es tiempo presente: exige asignación viva, no solo que la
        # referencia haya estado alguna vez en el alcance. Un equipo reasignado
        # conserva su histórico pero deja de emitir hacia el dueño anterior.
        if not window.allows_now():
            logger.warning(
                f"WebSocket rechazado — jti={claims.jti} "
                "(asignación cerrada: sin acceso en vivo)"
            )
            return await _reject_websocket(websocket, 1008, "Not authorized")

        translation.add(ref, internal_id, window)

    return True, subprotocol, translation


def frame_is_within_window(message, translation: RefTranslation) -> bool:
    """¿Sigue viva la asignación del dispositivo de esta trama?

    Se evalúa **por trama**, no solo en el handshake, por la misma razón que se
    vigila el `exp` del token: una asignación puede cerrarse con la conexión ya
    abierta, y en ese momento el equipo deja de ser del suscriptor. Sin esta
    comprobación, una reasignación no surtiría efecto hasta que el cliente se
    reconectara — que en un stream de posiciones puede no ocurrir en horas.

    Sin traducción activa no hay ventanas y todo pasa, que es el comportamiento
    en modo observación.
    """
    if not translation or not translation.enforcing:
        return True

    device_id = _first_device_id(message)
    if device_id is None:
        # Sin identificador no se puede decidir. Se deja pasar porque el
        # enrutado del broker ya la dirigió a este socket: el filtro de ventana
        # no es el que decide a quién va la trama, solo si sigue vigente.
        return True

    return translation.window_for(device_id).allows_now()


def _first_device_id(message):
    """Primer `device_id` que aparece en el árbol del mensaje."""
    if isinstance(message, dict):
        for key, value in message.items():
            if key == "device_id" and isinstance(value, str):
                return value
            found = _first_device_id(value)
            if found is not None:
                return found
    elif isinstance(message, list):
        for item in message:
            found = _first_device_id(item)
            if found is not None:
                return found
    return None


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


class ShareStoreUnavailable(ShareTokenError):
    """No se pudo comprobar el alcance: el problema es de este servicio.

    Se distingue de `ShareTokenInvalid` para no decirle a quien abre un enlace
    que su enlace está roto cuando lo que pasa es que Valkey no responde.
    """


class ShareGrant:
    """Lo que concede un enlace de compartir: un solo dispositivo, hasta `exp`.

    `device_ref` es lo que se devuelve al cliente; `device_id` lo que se
    consulta contra la base de datos. En el formato heredado ambos coinciden,
    porque el token viejo transporta el IMEI directamente.
    """

    def __init__(
        self,
        device_ref: str,
        device_id: str,
        expires_at,
        legacy: bool,
        window: AccessWindow | None = None,
    ):
        self.device_ref = device_ref
        self.device_id = device_id
        self.expires_at = expires_at
        self.legacy = legacy
        # Un enlace compartido también está sujeto a la ventana: si el equipo
        # se reasigna, el enlace debe dejar de emitir posiciones aunque el
        # token siga vigente.
        # `is None`, no `or`: ver la nota en `RefTranslation.add`. Una ventana
        # vacía es falsy y no debe degradar a "sin límite".
        self.window = AccessWindow.always() if window is None else window

        # Se fija al conceder, no por trama: el alcance de una conexión no
        # cambia mientras el token siga vivo, así que resolverlo una vez en el
        # handshake evita ir a Valkey por cada posición del stream. La
        # contrapartida es que "dejar de compartir" no corta las conexiones ya
        # abiertas; el techo de esa latencia es el `exp`, porque el keep-alive
        # cierra el socket al vencer (ver `_send_keepalive` en public.py).
        self._translation = RefTranslation()
        if not legacy:
            # En el formato heredado no hay espacio de referencias que traducir.
            self._translation.add(device_ref, device_id, self.window)

    @property
    def translation(self) -> RefTranslation:
        """Traducción para las tramas de salida del WebSocket público."""
        return self._translation


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

        try:
            resolved = await scope_store.resolve_single(
                claims.scope_ref, "dev", cache_ceiling(claims)
            )
        except ScopeStoreUnavailable as e:
            # No es un token inválido: es que no podemos comprobarlo.
            raise ShareStoreUnavailable(str(e)) from e
        if resolved is None:
            logger.warning(
                f"Enlace de compartir sin alcance resoluble — jti={claims.jti}"
            )
            raise ShareTokenInvalid("Share scope is empty, revoked or too broad")

        device_ref, device_id, window = resolved

        # Un enlace de un equipo recién reasignado no debe seguir emitiendo.
        # El `exp` del token no lo cubre: puede quedarle media hora de vida.
        if not window.allows_now():
            logger.warning(
                f"Enlace de compartir con asignación cerrada — jti={claims.jti}"
            )
            raise ShareTokenInvalid("Device assignment is no longer active")

        logger.info(f"Enlace de compartir válido — jti={claims.jti}")
        return ShareGrant(
            device_ref, device_id, claims.expires_at, legacy=False, window=window
        )

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
