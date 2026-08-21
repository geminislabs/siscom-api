"""
Rutas públicas para compartir ubicaciones de dispositivos GPS.

Este módulo proporciona endpoints públicos que no requieren autenticación JWT
pero usan tokens PASETO para autorización temporal.
"""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.api.deps import (
    ShareGrant,
    ShareStoreUnavailable,
    ShareTokenExpired,
    ShareTokenInvalid,
    resolve_share_token,
    translate_ws_message,
)
from app.api.routes.stream import ws_broker
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.repository import get_latest_communications

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public/share-location", tags=["Public"])


@router.get("/init")
async def init_share_location(
    token: str = Query(..., description="Token PASETO para validar acceso"),
):
    """
    Inicializa una sesión de compartición de ubicación validando el token PASETO.

    Este endpoint valida un token PASETO v4.local emitido por siscom-admin-api,
    retorna información sobre su validez, expiración y la última ubicación del dispositivo.

    **Query Parameters:**
    - `token`: Token PASETO v4.local (requerido)

    **Ejemplo:**
    ```
    GET /api/v1/public/share-location/init?token=v4.local.xxx...
    ```

    **Responses:**
    - 200: Token válido con datos de ubicación
        ```json
        {
            "msg": "valid",
            "expires_at": "2024-12-31T23:59:59+00:00",
            "device_id": "867564050638581",
            "last_communication": {
                "device_id": "867564050638581",
                "latitude": 19.4326,
                "longitude": -99.1332,
                "speed": 45.5,
                "course": 180.0,
                "gps_datetime": "2024-01-15T10:30:00",
                "received_at": "2024-01-15T10:30:01",
                "engine_status": "ON",
                "fix_status": "VALID",
                "satellites": 12
            }
        }
        ```
    - 401: Token expirado
        ```json
        {
            "detail": "Token expired"
        }
        ```
    - 403: Token inválido
        ```json
        {
            "detail": "Invalid token"
        }
        ```
    - 404: Dispositivo sin comunicaciones
        ```json
        {
            "detail": "No communication found for device"
        }
        ```

    **Returns:**
    - Información de validez del token, fecha de expiración y última ubicación
    """
    try:
        grant = await resolve_share_token(token)
        expires_at = grant.expires_at.isoformat()

        logger.info(f"Enlace de compartir válido. Expira en: {expires_at}")

        # Obtener la última comunicación del dispositivo
        async with SessionLocal() as db:
            try:
                results = await get_latest_communications(db, [grant.device_id])

                if not results or len(results) == 0:
                    # Token válido pero sin datos de ubicación
                    return {
                        "msg": "valid",
                        "expires_at": expires_at,
                        "device_id": grant.device_ref,
                        "last_communication": None,
                    }

                # Formatear la última comunicación. El identificador sale en el
                # espacio de la referencia, nunca el interno: un enlace
                # compartido es público, y el IMEI no debe viajar en él.
                point = results[0]
                last_communication = {
                    "device_id": grant.device_ref,
                    "latitude": float(point.latitude) if point.latitude else None,
                    "longitude": float(point.longitude) if point.longitude else None,
                    "speed": float(point.speed) if point.speed else None,
                    "course": float(point.course) if point.course else None,
                    "gps_datetime": (
                        point.gps_datetime.isoformat() if point.gps_datetime else None
                    ),
                    "received_at": (
                        point.received_at.isoformat() if point.received_at else None
                    ),
                    "engine_status": point.engine_status,
                    "fix_status": point.fix_status,
                    "satellites": point.satellites,
                    "backup_battery_voltage": (
                        float(point.backup_battery_voltage)
                        if point.backup_battery_voltage
                        else None
                    ),
                    "main_battery_voltage": (
                        float(point.main_battery_voltage)
                        if point.main_battery_voltage
                        else None
                    ),
                    "odometer": point.odometer,
                }

                return {
                    "msg": "valid",
                    "expires_at": expires_at,
                    "device_id": grant.device_ref,
                    "last_communication": last_communication,
                }

            except Exception as db_error:
                logger.error(f"Error al obtener comunicación: {db_error}")
                # Token válido pero error al obtener datos
                return {
                    "msg": "valid",
                    "expires_at": expires_at,
                    "device_id": grant.device_ref,
                    "last_communication": None,
                }

    except ShareStoreUnavailable as e:
        # 503, no 403: el enlace puede ser perfectamente válido; lo que falla
        # es nuestra capacidad de comprobarlo. Un 403 le diría a quien abre el
        # enlace que ya no vale, y dejaría de intentarlo.
        logger.error(f"No se pudo comprobar el alcance del enlace: {e}")
        raise HTTPException(
            status_code=503, detail="Authorization store unavailable"
        ) from None

    except ShareTokenExpired:
        logger.warning("Intento de acceso con token expirado")
        raise HTTPException(status_code=401, detail="Token expired") from None

    except ShareTokenInvalid as e:
        logger.warning(f"Intento de acceso con token inválido: {str(e)}")
        raise HTTPException(status_code=403, detail="Invalid token") from None

    except Exception as e:
        logger.error(f"Error inesperado al validar token: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# ============================================================================
# Funciones auxiliares para WebSocket público
# ============================================================================


async def _validate_share_token(websocket: WebSocket, token: str) -> ShareGrant | None:
    """
    Resuelve el token de compartir y devuelve el alcance concedido.

    Si el token es inválido cierra el WebSocket con 1008 **sin aceptarlo**, así
    que la conexión no llega a establecerse.

    Args:
        websocket: Conexión WebSocket
        token: Token de compartir, en formato data token o heredado

    Returns:
        El `ShareGrant` si es válido, None si no lo es
    """
    try:
        return await resolve_share_token(token)
    except ShareStoreUnavailable as e:
        # 1013 (Try Again Later): reintentar sirve, pedir otro enlace no.
        logger.error(f"No se pudo comprobar el alcance del enlace: {e}")
        await websocket.close(code=1013, reason="Authorization store unavailable")
        return None
    except ShareTokenExpired:
        logger.warning("Intento de WebSocket público con token expirado")
        await websocket.close(code=1008, reason="Token expired")
        return None
    except ShareTokenInvalid as e:
        logger.warning(f"Intento de WebSocket público con token inválido: {str(e)}")
        await websocket.close(code=1008, reason="Invalid token")
        return None


async def _send_keepalive(websocket: WebSocket, expires_at: datetime) -> None:
    """
    Envía pings keep-alive de forma periódica y cierra al expirar el token.

    Corre en bucle mientras la conexión siga viva: cada
    `WEBSOCKET_KEEPALIVE_SECS` segundos emite un ping para mantener el socket
    activo por debajo del idle timeout del proxy/ALB. Al vencer el token envía
    `expired` y cierra la conexión.

    Args:
        websocket: Conexión WebSocket
        expires_at: Fecha de expiración del token
    """
    try:
        while True:
            await asyncio.sleep(settings.WEBSOCKET_KEEPALIVE_SECS)

            if datetime.now(UTC) >= expires_at:
                # send y close en suppress independientes: si el aviso de
                # expiración falla (socket ya cerrado), igual intentamos cerrar.
                with suppress(Exception):
                    await websocket.send_json(
                        {"event": "expired", "data": {"message": "Token expired"}}
                    )
                with suppress(Exception):
                    await websocket.close(code=1000, reason="Token expired")
                return

            try:
                await websocket.send_json(
                    {"event": "ping", "data": {"type": "keep-alive"}}
                )
            except Exception as e:
                logger.warning(f"Error al enviar keep-alive público: {e}")
                return
    except asyncio.CancelledError:
        logger.debug("Task de keep-alive público cancelado")
        raise


async def _process_queue_messages(
    websocket: WebSocket,
    queues: list[asyncio.Queue],
    expires_at: datetime,
    grant: ShareGrant,
) -> bool:
    """
    Procesa mensajes de las colas del broker.

    Args:
        websocket: Conexión WebSocket
        queues: Colas suscritas al broker
        expires_at: Fecha de expiración del token
        grant: Alcance concedido, con la traducción de identificadores

    Returns:
        True si debe terminar el loop, False si debe continuar
    """
    translation = grant.translation
    if not queues:
        await asyncio.sleep(1)
        return False

    done, pending = await asyncio.wait(
        [asyncio.create_task(q.get()) for q in queues],
        timeout=60.0,
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()

    # Timeout sin mensajes → verificar expiración
    if not done:
        if datetime.now(UTC) >= expires_at:
            logger.info("Token expirado durante WebSocket público")
            # El task de keep-alive también vigila la expiración y puede haber
            # cerrado el socket antes; toleramos el envío sobre socket cerrado.
            with suppress(Exception):
                await websocket.send_json(
                    {"event": "expired", "data": {"message": "Token expired"}}
                )
            return True
        return False

    # Procesar mensajes recibidos
    for task in done:
        try:
            event = task.result()
            # Un enlace compartido es público: la trama sale con la referencia,
            # nunca con el identificador interno.
            event = translate_ws_message(event, translation)
            await websocket.send_json({"event": "message", "data": event})
            logger.debug("Mensaje WebSocket público enviado")
        except Exception as e:
            logger.error(f"Error al procesar mensaje de cola: {e}")

    return False


# ============================================================================
# Endpoint WebSocket público
# ============================================================================


@router.websocket("/stream")
async def websocket_shared_location(
    websocket: WebSocket,
    token: str = Query(..., description="Token PASETO para validar acceso"),
):
    """
    WebSocket para recibir ubicación en tiempo real de un link compartido.

    Este endpoint establece una conexión WebSocket que envía actualizaciones
    de ubicación en tiempo real para un dispositivo específico, autenticado
    mediante un token PASETO temporal.

    **Ventajas sobre SSE:**
    - ✅ Full-duplex (bidireccional)
    - ✅ Sin problemas de buffering en ALB/nginx
    - ✅ Menor overhead de red
    - ✅ Mejor soporte en móviles
    - ✅ Backpressure natural

    **Query Parameters:**
    - `token`: Token PASETO v4.local (requerido)

    **Ejemplo de conexión:**
    ```
    ws://localhost:8000/api/v1/public/share-location/stream?token=v4.local.xxx...
    ```

    **Protocolo de mensajes:**
    - Servidor envía mensajes JSON cuando hay eventos de ubicación
    - Formato: `{"event": "message", "data": {...}}`
    - Keep-alive automático cada 60 segundos: `{"event": "ping", "data": {"type": "keep-alive"}}`
    - Token expirado: `{"event": "expired", "data": {"message": "Token expired"}}`

    **Códigos de cierre WebSocket:**
    - 1008 (Policy Violation): Token inválido o expirado antes de conectar
    - 1000 (Normal): Token expiró durante la conexión

    **Ejemplo de uso en JavaScript:**
    ```javascript
    const token = 'v4.local.xxx...';
    const ws = new WebSocket(`ws://localhost:8000/api/v1/public/share-location/stream?token=${token}`);

    ws.onopen = () => console.log('Conectado');

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.event === 'message') {
            console.log('Ubicación:', data.data);
        } else if (data.event === 'expired') {
            console.log('Token expirado, reconectar con nuevo token');
        } else if (data.event === 'ping') {
            console.log('Keep-alive recibido');
        }
    };

    ws.onclose = (event) => {
        console.log('Desconectado:', event.code, event.reason);
    };
    ```
    """
    # 1. Validar token ANTES de aceptar la conexión
    grant = await _validate_share_token(websocket, token)
    if grant is None:
        return
    expires_at = grant.expires_at

    # 2. Aceptar conexión WebSocket
    await websocket.accept()
    logger.info(f"WebSocket público conectado. Expira: {expires_at}")

    # 3. Suscribirse al broker por identificador interno, que es con lo que
    #    llegan los mensajes de Kafka.
    device_list = [grant.device_id]
    queues = await ws_broker.subscribe(device_list)

    # 4. Task para keep-alive con verificación de expiración
    keepalive_task = asyncio.create_task(_send_keepalive(websocket, expires_at))

    try:
        while True:
            should_stop = await _process_queue_messages(
                websocket, queues, expires_at, grant
            )
            if should_stop:
                break
    except WebSocketDisconnect:
        logger.info("WebSocket público desconectado")
    except Exception as e:
        logger.error(f"Error en WebSocket público: {e}", exc_info=True)
    finally:
        keepalive_task.cancel()
        await ws_broker.unsubscribe(device_list, queues)
        with suppress(Exception):
            await websocket.close()
