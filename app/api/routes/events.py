"""
Rutas para el módulo de eventos.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_db
from app.schemas.events import EventsPageResponse
from app.services.events_repository import get_events

router = APIRouter(prefix="/api/v1", tags=["Events"])


@router.get("/events", response_model=EventsPageResponse)
# PLR0917: los parámetros los inyecta FastAPI, no se pasan posicionalmente.
async def get_events_handler(  # noqa: PLR0913, PLR0917
    unit_id: list[UUID] = Query(
        ...,
        description="Lista de UUIDs de unidades a filtrar",
        min_length=1,
        max_length=100,
        examples=[["123e4567-e89b-12d3-a456-426614174000"]],
    ),
    from_dt: datetime = Query(
        ...,
        alias="from",
        description="Fecha/hora inicial del rango (ISO 8601, incluida). "
        "Ejemplo: 2026-03-01T00:00:00Z",
    ),
    to_dt: datetime = Query(
        ...,
        alias="to",
        description="Fecha/hora final del rango (ISO 8601, incluida). "
        "Ejemplo: 2026-03-31T23:59:59Z",
    ),
    limit: int = Query(
        20,
        ge=1,
        le=200,
        description="Cantidad de registros a retornar por página (default 20, máximo 200)",
    ),
    order: Literal["asc", "desc"] = Query(
        "desc",
        description="Orden de los eventos: 'desc' (más recientes primero, default) "
        "o 'asc' (más antiguos primero)",
    ),
    cursor: str | None = Query(
        None,
        description="Cursor opaco para continuar desde una página anterior. "
        "Obtenido de next_cursor en la respuesta anterior.",
    ),
    db=Depends(get_db),
):
    """
    Obtiene eventos para múltiples unidades en un rango de fechas.

    **Query Parameters:**
    - `unit_id`: Lista de UUIDs de unidades (requerido, al menos 1)
    - `from`: Fecha/hora inicial en ISO 8601 (requerido)
    - `to`: Fecha/hora final en ISO 8601 (requerido)
    - `limit`: Cantidad de registros por página (1-200, default 20)
    - `order`: Orden ascendente o descendente (default "desc")
    - `cursor`: Cursor opaco para paginación (opcional)

    **Ejemplo:**
    ```
    GET /api/v1/events?unit_id=123e4567-e89b-12d3-a456-426614174000&unit_id=223e4567-e89b-12d3-a456-426614174001&from=2026-03-01T00:00:00Z&to=2026-03-31T23:59:59Z&limit=50&order=desc
    ```

    **Validaciones:**
    - `unit_id` debe ser UUID válido (FastAPI retorna 422 si no)
    - `from` y `to` deben ser ISO 8601 válido (FastAPI retorna 422 si no)
    - `limit` debe estar entre 1 y 200

    **Returns:**
    - `EventsPageResponse` con lista de eventos y cursor para la siguiente página (si existe)
    """
    try:
        events, next_cursor = await get_events(
            db,
            unit_ids=unit_id,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            order=order,
            cursor=cursor,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return EventsPageResponse(
        data=events,  # type: ignore
        next_cursor=next_cursor,
    )
