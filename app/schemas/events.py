"""
Schemas Pydantic para el módulo de eventos.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventResponse(BaseModel):
    """
    Schema para la respuesta de un evento.

    Representa un registro individual de evento con:
    - unit_id: identificador de la unidad asociada al evento
    - source_id: identificador de la fuente del evento
    - event_type: código de tipo de evento (desde event_types)
    - occurred_at: fecha/hora cuando ocurrió el evento
    - received_at: fecha/hora cuando se recibió
    - source_epoch: timestamp unix desde la fuente
    """

    unit_id: UUID | None = None
    source_id: str
    event_type: str = Field(..., alias="code")
    occurred_at: datetime
    received_at: datetime | None = None
    source_epoch: int | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "unit_id": "123e4567-e89b-12d3-a456-426614174000",
                "source_id": "DEVICE123",
                "event_type": "ignition_on",
                "occurred_at": "2026-03-15T10:30:45Z",
                "received_at": "2026-03-15T10:30:50Z",
                "source_epoch": 1710499845,
            }
        },
    )


class EventsPageResponse(BaseModel):
    """
    Schema para la respuesta paginada de eventos.

    Contiene:
    - data: lista de eventos en la página actual
    - next_cursor: cursor opaco para obtener la siguiente página (None si no hay más)
    """

    data: list[EventResponse]
    next_cursor: str | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data": [
                    {
                        "unit_id": "123e4567-e89b-12d3-a456-426614174000",
                        "source_id": "DEVICE123",
                        "event_type": "ignition_on",
                        "occurred_at": "2026-03-15T10:30:45Z",
                        "received_at": "2026-03-15T10:30:50Z",
                        "source_epoch": 1710499845,
                    }
                ],
                "next_cursor": "eyJvYSI6ICIyMDI2LTAzLTE0VDEwOjMwOjQ1WiIsICJpZCI6ICJhZWIxYzcyZC1lOWQyLTRlOGYtOGMxZi0wOGU0ZmVjYTc4ZWYifQ==",
            }
        },
    )
