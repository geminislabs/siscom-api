from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import (
    internal_ids,
    live_internal_ids,
    require_data_token,
    to_external_models,
    windows_for_request,
)
from app.core.database import get_db
from app.schemas.communications import (
    CommunicationFullResponse,
    CommunicationLatestResponse,
    CommunicationResponse,
)
from app.services.repository import get_communications, get_latest_communications

# La verificación va a nivel de router: cubre los cuatro endpoints sin tocar
# ninguna firma, y un endpoint nuevo queda protegido por omisión en vez de por
# acordarse de añadir la dependencia.
router = APIRouter(
    prefix="/api/v1",
    tags=["Communications"],
    dependencies=[Depends(require_data_token)],
)


# ============================================================================
# Endpoints de Comunicaciones - Histórico
# ============================================================================


@router.get("/communications", response_model=list[CommunicationResponse])
async def get_communications_history(  # noqa: B008
    request: Request,
    device_ids: list[str] = Query(
        ...,
        description="Lista de IDs de dispositivos GPS a consultar",
        min_length=1,
        max_length=100,
        examples=[["867564050638581", "DEVICE123"]],
    ),
    db=Depends(get_db),
):
    """
    Obtiene el histórico de comunicaciones de múltiples dispositivos GPS.

    **Método REST:** GET con query parameters

    **Query Parameters:**
    - `device_ids`: Lista de IDs de dispositivos (requerido, mínimo 1, máximo 100)

    **Ejemplo:**
    ```
    GET /api/v1/communications?device_ids=867564050638581&device_ids=DEVICE123
    ```

    **Returns:**
    - Lista de comunicaciones de los dispositivos especificados
    """
    # El histórico se recorta a la ventana de cada dispositivo dentro de la
    # consulta: un equipo reasignado conserva su periodo, no el del dueño
    # siguiente.
    results = await get_communications(
        db,
        internal_ids(request, device_ids),
        windows=windows_for_request(request),
    )
    return to_external_models(
        request, [CommunicationResponse.model_validate(r) for r in results]
    )


@router.get(
    "/devices/{device_id}/communications",
    response_model=list[CommunicationFullResponse] | list[CommunicationResponse],
)
async def get_device_communications(
    request: Request,
    device_id: str,
    received_at: date | None = Query(
        None,
        description="Fecha para filtrar comunicaciones (formato: YYYY-MM-DD). "
        "Si se proporciona, devuelve TODOS los campos disponibles.",
        examples=["2024-12-14"],
    ),
    db=Depends(get_db),
):
    """
    Obtiene el histórico de comunicaciones de UN solo dispositivo GPS.

    **Método REST:** GET con path parameter

    **Path Parameters:**
    - `device_id`: ID del dispositivo GPS

    **Query Parameters:**
    - `received_at`: Fecha opcional para filtrar (YYYY-MM-DD).
      Si se proporciona, devuelve todos los registros de esa fecha con **todos los campos**.

    **Ejemplos:**
    ```
    GET /api/v1/devices/867564050638581/communications
    GET /api/v1/devices/867564050638581/communications?received_at=2024-12-14
    ```

    **Returns:**
    - Sin filtro de fecha: Lista con campos básicos (CommunicationResponse)
    - Con filtro de fecha: Lista con TODOS los campos (CommunicationFullResponse)
    """
    results = await get_communications(
        db,
        internal_ids(request, [device_id]),
        received_at=received_at,
        windows=windows_for_request(request),
    )

    # Si hay filtro de fecha, devolver respuesta completa
    if received_at is not None:
        return to_external_models(
            request, [CommunicationFullResponse.model_validate(r) for r in results]
        )

    # Sin filtro, devolver respuesta básica (comportamiento original)
    return to_external_models(
        request, [CommunicationResponse.model_validate(r) for r in results]
    )


# ============================================================================
# Endpoints de Comunicaciones - Estado Actual (Latest)
# ============================================================================


@router.get("/communications/latest", response_model=list[CommunicationLatestResponse])
async def get_latest_communications_endpoint(  # noqa: B008
    request: Request,
    device_ids: list[str] = Query(
        ...,
        description="Lista de IDs de dispositivos GPS a consultar",
        min_length=1,
        max_length=100,
        examples=[["867564050638581", "DEVICE123"]],
    ),
    db=Depends(get_db),
):
    """
    Obtiene la última comunicación registrada de múltiples dispositivos GPS.

    **Método REST:** GET con query parameters

    **Query Parameters:**
    - `device_ids`: Lista de IDs de dispositivos (requerido, mínimo 1, máximo 100)

    **Ejemplo:**
    ```
    GET /api/v1/communications/latest?device_ids=867564050638581&device_ids=DEVICE123
    ```

    **Diferencias:**
    - `GET /communications`: Retorna TODO el histórico de comunicaciones
    - `GET /communications/latest`: Retorna SOLO la última comunicación de cada dispositivo
    - `GET /communications/stream`: Conexión persistente con actualizaciones continuas (SSE)

    **Caso de uso:** Ideal para dashboards que necesitan mostrar la posición/estado
    actual de múltiples dispositivos sin cargar todo el histórico.

    **Returns:**
    - Lista con la última comunicación de cada dispositivo especificado
    """
    # `latest` es tiempo presente: solo los equipos con asignación viva. Uno
    # reasignado conserva su histórico, pero su posición actual ya no es de su
    # dueño anterior.
    results = await get_latest_communications(
        db, live_internal_ids(request, device_ids)
    )
    return to_external_models(
        request, [CommunicationLatestResponse.model_validate(r) for r in results]
    )


@router.get(
    "/devices/{device_id}/communications/latest",
    response_model=CommunicationLatestResponse,
)
async def get_device_latest_communication(  # noqa: B008
    request: Request,
    device_id: str,
    class_: str = Query(
        "STATUS",
        alias="class",
        description="Filtro por tipo de clase de mensaje (ALERT o STATUS)",
        examples=["STATUS"],
    ),
    db=Depends(get_db),
):
    """
    Obtiene la última comunicación de UN solo dispositivo GPS.

    **Método REST:** GET con path parameter

    **Path Parameters:**
    - `device_id`: ID del dispositivo GPS

    **Query Parameters:**
    - `class`: Tipo de clase de mensaje para filtrar (ALERT o STATUS). Default: STATUS

    **Ejemplos:**
    ```
    GET /api/v1/devices/867564050638581/communications/latest
    GET /api/v1/devices/867564050638581/communications/latest?class=STATUS
    GET /api/v1/devices/867564050638581/communications/latest?class=ALERT
    ```

    **Diferencias:**
    - `GET /devices/{id}/communications`: Retorna TODO el histórico del dispositivo
    - `GET /devices/{id}/communications/latest`: Retorna SOLO la última comunicación
    - `GET /devices/{id}/communications/stream`: Stream en tiempo real del dispositivo

    **Caso de uso:** Ideal para consultar el estado actual de un dispositivo específico.

    **Returns:**
    - Última comunicación del dispositivo especificado
    - Error 404 si el dispositivo no existe o no tiene comunicaciones
    """
    result = await get_latest_communications(
        db, live_internal_ids(request, [device_id]), msg_class=class_
    )

    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No se encontró comunicación para el dispositivo {device_id}",
        )

    return to_external_models(
        request, [CommunicationLatestResponse.model_validate(result[0])]
    )[0]
