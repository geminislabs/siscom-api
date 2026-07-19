from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class CommunicationBase:
    """
    Base común con todos los campos compartidos entre Suntech y Queclink.
    Si en el futuro agregas un tercer fabricante, solo heredas esta clase.
    """

    # Identificadores
    uuid = Column(String(255))
    device_id = Column(String(100), index=True)

    # Datos de batería
    backup_battery_voltage = Column(Numeric(5, 2))
    main_battery_voltage = Column(Numeric(5, 2))

    # Datos de red celular
    cell_id = Column(String(50))
    lac = Column(String(10))
    mcc = Column(String(10))
    mnc = Column(String(10))
    rx_lvl = Column(Integer)
    network_status = Column(String(50))

    # Datos GPS
    course = Column(Numeric(6, 2))
    fix_status = Column(String(5))
    gps_datetime = Column(DateTime)
    gps_epoch = Column(BigInteger)
    latitude = Column(Numeric(10, 8))
    longitude = Column(Numeric(11, 8))
    satellites = Column(Integer)
    speed = Column(Numeric(8, 2))

    # Datos del dispositivo
    delivery_type = Column(String(20))
    engine_status = Column(String(10))
    firmware = Column(String(20))
    model = Column(String(10))
    msg_class = Column(String(20))
    msg_counter = Column(Integer)

    # Odómetro y distancia
    odometer = Column(BigInteger)
    total_distance = Column(BigInteger)
    trip_distance = Column(BigInteger)

    # Tiempos
    idle_time = Column(Integer)
    speed_time = Column(Integer)
    trip_hourmeter = Column(Integer)

    # Metadata de conexión
    bytes_count = Column(Integer)
    client_ip = Column(Text)
    client_port = Column(Integer)

    # Timestamps
    decoded_epoch = Column(BigInteger)
    received_epoch = Column(BigInteger)
    received_at = Column(DateTime)
    created_at = Column(DateTime)

    # Datos raw
    raw_message = Column(Text)

    # Campo de alertas heredado de versiones anteriores
    alert_type = Column(String)


class CommunicationSuntech(Base, CommunicationBase):
    """
    Tabla para los registros provenientes de dispositivos Suntech.
    """

    __tablename__ = "communications_suntech"

    id = Column(Integer, primary_key=True, index=True)


class CommunicationQueclink(Base, CommunicationBase):
    """
    Tabla para los registros provenientes de dispositivos Queclink.
    """

    __tablename__ = "communications_queclink"

    id = Column(Integer, primary_key=True, index=True)


class CommunicationCurrentState(Base):
    """
    Tabla para el estado actual (última comunicación) de los dispositivos.

    Esta tabla materializada contiene la comunicación más reciente de cada dispositivo,
    optimizada para consultas rápidas de estado actual.

    Nota: device_id es la clave primaria (no existe campo id).
    """

    __tablename__ = "communications_current_state"

    # Primary Key
    device_id = Column(String, primary_key=True, index=True)

    # Campos del estado actual
    backup_battery_voltage = Column(Numeric)
    course = Column(Numeric)
    delivery_type = Column(String)
    engine_status = Column(String)
    fix_status = Column(String)
    gps_datetime = Column(DateTime)
    gps_epoch = Column(BigInteger)
    latitude = Column(Numeric)
    longitude = Column(Numeric)
    main_battery_voltage = Column(Numeric)
    msg_class = Column(String)
    network_status = Column(String)
    odometer = Column(BigInteger)
    rx_lvl = Column(Integer)
    satellites = Column(Integer)
    speed = Column(Numeric)
    received_epoch = Column(BigInteger)
    received_at = Column(DateTime)
