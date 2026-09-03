"""
Repository para la tabla de eventos con paginación por keyset cursor.
"""

import base64
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import String, and_, asc, cast, desc, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope_window import AccessWindow
from app.models.events import Event, EventType


def _unit_scope_filter(unit_ids: list[UUID], windows: dict | None):
    """Filtro por unidad, acotado a la ventana temporal de cada una.

    `occurred_at` sí lleva zona en esta tabla, así que los límites se comparan
    tal cual, sin la conversión que necesitan las tablas de comunicaciones.

    Un `or_()` vacío es verdadero en SQLAlchemy, así que la ausencia de
    cláusulas devuelve `false()`: sin ventanas concedidas no se ve nada, no se
    ve todo.
    """
    if windows is None:
        return Event.unit_id.in_(unit_ids)

    clauses = []
    for unit_id in unit_ids:
        window: AccessWindow = windows.get(str(unit_id), AccessWindow.none())
        for interval in window.intervals:
            conditions = [Event.unit_id == unit_id]
            if interval.since is not None:
                conditions.append(Event.occurred_at >= interval.since)
            if interval.until is not None:
                conditions.append(Event.occurred_at < interval.until)
            clauses.append(and_(*conditions))

    return or_(*clauses) if clauses else false()


def encode_cursor(occurred_at: datetime, event_id: UUID) -> str:
    """
    Codifica un cursor opaco basado en (occurred_at, id).

    Args:
        occurred_at: fecha/hora del evento
        event_id: UUID del evento

    Returns:
        String Base64-encoded con JSON {oa: ISO datetime, id: UUID}
    """
    cursor_data = {
        "oa": occurred_at.isoformat(),
        "id": str(event_id),
    }
    json_str = json.dumps(cursor_data, separators=(",", ":"))
    return base64.b64encode(json_str.encode()).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """
    Decodifica un cursor opaco.

    Args:
        cursor: String Base64-encoded

    Returns:
        Tupla (occurred_at datetime, event_id UUID)

    Raises:
        ValueError: si el cursor es inválido
    """
    try:
        json_str = base64.b64decode(cursor.encode()).decode()
        data = json.loads(json_str)
        oa = datetime.fromisoformat(data["oa"])
        event_id = UUID(data["id"])
        return oa, event_id
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        raise ValueError(f"Cursor inválido: {e}") from e


async def get_events(  # noqa: PLR0913
    session: AsyncSession,
    *,
    unit_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    limit: int = 20,
    order: str = "desc",
    cursor: str | None = None,
    windows: dict | None = None,
) -> tuple[list[dict], str | None]:
    """
    Obtiene eventos con paginación por keyset cursor.

    Args:
        session: sesión async de SQLAlchemy
        unit_ids: lista de UUIDs de unidades a filtrar
        from_dt: datetime inicial del rango (inclusive)
        to_dt: datetime final del rango (inclusive)
        limit: cantidad de registros a retornar (default 20, max 200)
        order: 'desc' u 'asc' para ordenar por occurred_at
        cursor: cursor opaco para continuar desde un punto anterior
        windows: ventana temporal concedida por unidad. `None` = sin
            restricción temporal, el comportamiento anterior al contrato v1.3

    Returns:
        Tupla (eventos, next_cursor):
        - eventos: lista de dicts con unit_id, source_id, event_type, occurred_at, received_at, source_epoch
        - next_cursor: cursor para la siguiente página, None si no hay más
    """
    # Determinar dirección de ordenamiento
    is_desc = order.lower() == "desc"

    # Construir clausula WHERE base
    # El rango pedido y la ventana concedida se intersectan en SQL: el
    # resultado es el recorte, no un rechazo.
    where_clauses = [
        _unit_scope_filter(unit_ids, windows),
        Event.occurred_at >= from_dt,
        Event.occurred_at <= to_dt,
    ]

    # Procesar cursor si existe
    if cursor:
        try:
            cursor_oa, cursor_id = decode_cursor(cursor)
        except ValueError:
            raise ValueError("Cursor inválido") from None

        # Keyset: comparación de tupla (occurred_at, id)
        # DESC: ir atrás en el tiempo (menor occurred_at o igual con menor id)
        # ASC: ir adelante en el tiempo (mayor occurred_at o igual con mayor id)
        if is_desc:
            where_clauses.append(
                or_(
                    Event.occurred_at < cursor_oa,
                    and_(
                        Event.occurred_at == cursor_oa,
                        cast(Event.id, String) < str(cursor_id),
                    ),
                )
            )
        else:
            where_clauses.append(
                or_(
                    Event.occurred_at > cursor_oa,
                    and_(
                        Event.occurred_at == cursor_oa,
                        cast(Event.id, String) > str(cursor_id),
                    ),
                )
            )

    # Construir query con JOIN
    query = (
        select(
            Event.id,
            Event.unit_id,
            Event.source_id,
            EventType.code,
            Event.occurred_at,
            Event.received_at,
            Event.source_epoch,
        )
        .join(EventType, Event.event_type_id == EventType.id)
        .where(and_(*where_clauses))
    )

    # Ordenamiento
    if is_desc:
        query = query.order_by(desc(Event.occurred_at), desc(Event.id))
    else:
        query = query.order_by(asc(Event.occurred_at), asc(Event.id))

    # Fetch limit+1 para detectar si hay página siguiente
    query = query.limit(limit + 1)

    result = await session.execute(query)
    rows = result.fetchall()

    # Procesar resultados
    events = []
    next_cursor = None

    # Si obtuvimos más de 'limit' filas, cortamos y generamos cursor
    if len(rows) > limit:
        # Descartar la última fila (es solo para detectar que hay más)
        rows = rows[:limit]
        # El cursor es el de la última fila de la página actual
        last_row = rows[-1]
        next_cursor = encode_cursor(last_row[4], last_row[0])  # occurred_at, id

    # Convertir filas a dicts
    for row in rows:
        events.append(
            {
                "unit_id": row[1],
                "source_id": row[2],
                "code": row[3],  # Será mapeado a event_type en Pydantic
                "occurred_at": row[4],
                "received_at": row[5],
                "source_epoch": row[6],
            }
        )

    return events, next_cursor
