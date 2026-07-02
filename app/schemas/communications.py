"""Schemas Pydantic para el módulo de comunicaciones."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DeviceHistoryRequest(BaseModel):
    """
    Schema para la solicitud de historial de dispositivos.

    Valida que device_ids sea una lista con al menos 1 dispositivo.
    """

    device_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Lista de IDs de dispositivos GPS",
        examples=[["867564050638581", "DEVICE123"]],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"device_ids": ["867564050638581", "DEVICE123", "GPS001"]}
        }
    )


class CommunicationResponse(BaseModel):
    """
    Schema para la respuesta de comunicaciones GPS (histórico básico).

    Representa un registro de comunicación de un dispositivo GPS
    de las tablas Suntech o Queclink con campos básicos.
    """

    id: int
    device_id: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    speed: Decimal | None = None
    course: Decimal | None = None
    gps_datetime: datetime | None = None
    main_battery_voltage: Decimal | None = None
    backup_battery_voltage: Decimal | None = None
    odometer: int | None = None
    trip_distance: int | None = None
    total_distance: int | None = None
    engine_status: str | None = None
    fix_status: str | None = None
    alert_type: str | None = None

    model_config = ConfigDict(
        from_attributes=True,  # Para SQLAlchemy models
        json_schema_extra={
            "example": {
                "id": 1,
                "device_id": "867564050638581",
                "latitude": 19.4326,
                "longitude": -99.1332,
                "speed": 45.5,
                "course": 180.0,
                "gps_datetime": "2024-01-15T10:30:00",
                "main_battery_voltage": 12.5,
                "backup_battery_voltage": 3.7,
                "odometer": 15000,
                "trip_distance": 500,
                "total_distance": 150000,
                "engine_status": "ON",
                "fix_status": "VALID",
                "alert_type": None,
            }
        },
    )


class CommunicationFullResponse(BaseModel):
    """
    Schema para la respuesta completa de comunicaciones GPS.

    Incluye TODOS los campos disponibles en la tabla de comunicaciones.
    Se usa cuando se consulta por fecha específica (received_at).
    """

    id: int
    uuid: str | None = None
    device_id: str

    # Datos de batería
    backup_battery_voltage: Decimal | None = None
    main_battery_voltage: Decimal | None = None

    # Datos de red celular
    cell_id: str | None = None
    lac: str | None = None
    mcc: str | None = None
    mnc: str | None = None
    rx_lvl: int | None = None
    network_status: str | None = None

    # Datos GPS
    course: Decimal | None = None
    fix_status: str | None = None
    gps_datetime: datetime | None = None
    gps_epoch: int | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    satellites: int | None = None
    speed: Decimal | None = None

    # Datos del dispositivo
    delivery_type: str | None = None
    engine_status: str | None = None
    firmware: str | None = None
    model: str | None = None
    msg_class: str | None = None
    msg_counter: int | None = None

    # Odómetro y distancia
    odometer: int | None = None
    total_distance: int | None = None
    trip_distance: int | None = None

    # Tiempos
    idle_time: int | None = None
    speed_time: int | None = None
    trip_hourmeter: int | None = None

    # Metadata de conexión
    bytes_count: int | None = None
    client_ip: str | None = None
    client_port: int | None = None

    # Timestamps
    decoded_epoch: int | None = None
    received_epoch: int | None = None
    received_at: datetime | None = None
    created_at: datetime | None = None

    # Datos raw
    raw_message: str | None = None

    # Campo de alertas heredado de versiones anteriores
    alert_type: str | None = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "uuid": "550e8400-e29b-41d4-a716-446655440000",
                "device_id": "867564050638581",
                "backup_battery_voltage": 3.7,
                "main_battery_voltage": 12.5,
                "cell_id": "12345",
                "lac": "1234",
                "mcc": "334",
                "mnc": "020",
                "rx_lvl": -65,
                "network_status": "CONNECTED",
                "course": 180.0,
                "fix_status": "VALID",
                "gps_datetime": "2024-01-15T10:30:00",
                "gps_epoch": 1705318200,
                "latitude": 19.4326,
                "longitude": -99.1332,
                "satellites": 12,
                "speed": 45.5,
                "delivery_type": "GPRS",
                "engine_status": "ON",
                "firmware": "1.0.0",
                "model": "ST300",
                "msg_class": "STATUS",
                "msg_counter": 100,
                "odometer": 15000,
                "total_distance": 150000,
                "trip_distance": 500,
                "idle_time": 0,
                "speed_time": 3600,
                "trip_hourmeter": 100,
                "bytes_count": 256,
                "client_ip": "192.168.1.1",
                "client_port": 8080,
                "decoded_epoch": 1705318200,
                "received_epoch": 1705318201,
                "received_at": "2024-01-15T10:30:01",
                "created_at": "2024-01-15T10:30:01",
                "raw_message": None,
                "alert_type": None,
            }
        },
    )


class CommunicationLatestResponse(BaseModel):
    """
    Schema para la respuesta de última comunicación (current_state).

    Representa el estado actual de un dispositivo GPS desde la tabla
    communications_current_state. No incluye 'id' ya que device_id es la PK.
    """

    device_id: str
    backup_battery_voltage: Decimal | None = None
    course: Decimal | None = None
    delivery_type: str | None = None
    engine_status: str | None = None
    fix_status: str | None = None
    gps_datetime: datetime | None = None
    gps_epoch: int | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    main_battery_voltage: Decimal | None = None
    msg_class: str | None = None
    network_status: str | None = None
    odometer: int | None = None
    rx_lvl: int | None = None
    satellites: int | None = None
    speed: Decimal | None = None
    received_epoch: int | None = None
    received_at: datetime | None = None

    model_config = ConfigDict(
        from_attributes=True,  # Para SQLAlchemy models
        json_schema_extra={
            "example": {
                "device_id": "867564050638581",
                "latitude": 19.4326,
                "longitude": -99.1332,
                "speed": 45.5,
                "course": 180.0,
                "gps_datetime": "2024-01-15T10:30:00",
                "gps_epoch": 1705318200,
                "main_battery_voltage": 12.5,
                "backup_battery_voltage": 3.7,
                "odometer": 15000,
                "engine_status": "ON",
                "fix_status": "VALID",
                "satellites": 12,
                "rx_lvl": -65,
                "network_status": "CONNECTED",
                "msg_class": "HEARTBEAT",
                "delivery_type": "GPRS",
                "received_epoch": 1705318201,
                "received_at": "2024-01-15T10:30:01",
            }
        },
    )
