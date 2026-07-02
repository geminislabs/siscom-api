"""
Modelos SQLAlchemy para la tabla de eventos.
"""

from sqlalchemy import BIGINT, UUID, Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class EventType(Base):
    """Modelo para la tabla event_types."""

    __tablename__ = "event_types"

    id = Column(UUID, primary_key=True)
    code = Column(Text, nullable=False, unique=True, index=True)
    description = Column(Text)
    category = Column(Text)
    created_at = Column(DateTime(timezone=True))


class Event(Base):
    """Modelo para la tabla events."""

    __tablename__ = "events"

    id = Column(UUID, primary_key=True)

    # Origen del evento
    source_type = Column(Text, nullable=False)  # 'device', 'user_device', 'system'
    source_id = Column(Text, nullable=False)
    source_message_id = Column(UUID, nullable=True)

    # Relación opcional
    unit_id = Column(UUID, nullable=True, index=True)

    # Tipo de evento
    event_type_id = Column(UUID, ForeignKey("event_types.id"), nullable=False)

    # Datos adicionales
    payload = Column(JSONB, nullable=True)

    # Tiempos
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    received_at = Column(DateTime(timezone=True))
    source_epoch = Column(BIGINT, nullable=True)
