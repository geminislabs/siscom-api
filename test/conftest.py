"""
Configuración de fixtures de pytest para SISCOM API.

Este módulo contiene fixtures reutilizables para tests.
"""

import os
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app
from app.models.communications import Base, CommunicationQueclink, CommunicationSuntech
from app.services.kafka_client import kafka_client
from app.utils.metrics import metrics_client

TEST_DB_HOST = os.getenv("DB_HOST", "localhost")
TEST_DB_PORT = os.getenv("DB_PORT", "5432")
TEST_DB_USER = os.getenv("DB_USERNAME", "test")
TEST_DB_PASSWORD = os.getenv("DB_PASSWORD", "test")
TEST_DB_NAME = os.getenv("DB_DATABASE", "siscom_test")

TEST_DATABASE_URL = (
    f"postgresql+asyncpg://{TEST_DB_USER}:{TEST_DB_PASSWORD}"
    f"@{TEST_DB_HOST}:{TEST_DB_PORT}/{TEST_DB_NAME}"
)

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

TestSessionLocal = sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

SQLITE_TEST_URL = "sqlite+aiosqlite:///:memory:"

sqlite_test_engine = create_async_engine(
    SQLITE_TEST_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

SqliteTestSessionLocal = sessionmaker(
    sqlite_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(autouse=True)
def disable_external_services(monkeypatch):
    """Evita que Kafka/StatsD bloqueen o relenticen los tests."""
    monkeypatch.setattr(kafka_client, "connect", lambda: None)
    monkeypatch.setattr(kafka_client, "disconnect", lambda: None)

    async def noop_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(metrics_client, "ensure_connected", noop_async)
    monkeypatch.setattr(metrics_client, "close", noop_async)
    monkeypatch.setattr(metrics_client, "kafka_circuit_breaker_gauge", noop_async)
    monkeypatch.setattr("app.api.routes.stream.start_kafka_broker_bridge", lambda: None)


@pytest_asyncio.fixture
async def setup_test_database():
    """Crea tablas de test si no existen."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture(autouse=True)
async def reset_communications_tables(setup_test_database):
    """Limpia tablas antes de cada test para aislar datos."""
    async with TestSessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE communications_suntech, communications_queclink "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()
    yield


@pytest_asyncio.fixture
async def db_session(setup_test_database) -> AsyncGenerator[AsyncSession, None]:
    """Sesión PostgreSQL para seed de datos en fixtures."""
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
def client() -> Generator:
    """Cliente TestClient; cada request usa su propia sesión async."""

    async def _get_db_override():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db_override

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def override_get_db(db_session: AsyncSession):
    """Override legacy para tests que inyectan db_session explícitamente."""

    async def _get_db_override():
        yield db_session

    return _get_db_override


@pytest_asyncio.fixture
async def db_session_sqlite() -> AsyncGenerator[AsyncSession, None]:
    """Sesión SQLite en memoria para tests nuevos."""
    async with sqlite_test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SqliteTestSessionLocal() as session:
        yield session
        await session.rollback()

    async with sqlite_test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def override_get_db_sqlite(db_session_sqlite: AsyncSession):
    """Override de get_db con SQLite en memoria."""

    async def _get_db_override():
        yield db_session_sqlite

    return _get_db_override


@pytest.fixture
def client_sqlite(override_get_db_sqlite) -> Generator:
    """Cliente TestClient con SQLite."""
    app.dependency_overrides[get_db] = override_get_db_sqlite

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
async def async_client(override_get_db) -> AsyncGenerator:
    """Cliente httpx async."""
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def valid_token() -> str:
    """JWT válido para tests."""
    return create_access_token({"sub": "test_user", "user_id": 1})


@pytest.fixture
def expired_token() -> str:
    """JWT expirado para tests."""
    data = {"sub": "test_user", "user_id": 1}
    expire = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    data.update({"exp": expire})

    from jose import jwt

    return jwt.encode(data, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


@pytest.fixture
def invalid_token() -> str:
    """JWT inválido para tests."""
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature"


@pytest.fixture
def auth_headers(valid_token: str) -> dict:
    """Headers con Bearer token."""
    return {"Authorization": f"Bearer {valid_token}"}


@pytest_asyncio.fixture
async def sample_suntech_communication(db_session: AsyncSession):
    """Comunicación Suntech de prueba."""
    comm = CommunicationSuntech(
        device_id="867564050638581",
        latitude=Decimal("19.4326"),
        longitude=Decimal("-99.1332"),
        speed=Decimal("45.5"),
        course=Decimal("180.0"),
        gps_datetime=datetime(2024, 1, 15, 10, 30, 0),
        gps_epoch=1705318200,
        main_battery_voltage=Decimal("12.5"),
        backup_battery_voltage=Decimal("3.7"),
        odometer=15000,
        trip_distance=500,
        total_distance=150000,
        engine_status="ON",
        fix_status="VALID",
        alert_type=None,
    )

    db_session.add(comm)
    await db_session.commit()
    await db_session.refresh(comm)

    return comm


@pytest_asyncio.fixture
async def sample_queclink_communication(db_session: AsyncSession):
    """Comunicación Queclink de prueba."""
    comm = CommunicationQueclink(
        device_id="QUECLINK123",
        latitude=Decimal("25.6866"),
        longitude=Decimal("-100.3161"),
        speed=Decimal("60.0"),
        course=Decimal("90.0"),
        gps_datetime=datetime(2024, 1, 15, 11, 0, 0),
        gps_epoch=1705320000,
        main_battery_voltage=Decimal("13.0"),
        backup_battery_voltage=Decimal("3.8"),
        odometer=20000,
        trip_distance=1000,
        total_distance=200000,
        engine_status="ON",
        fix_status="VALID",
        alert_type="SPEED",
    )

    db_session.add(comm)
    await db_session.commit()
    await db_session.refresh(comm)

    return comm


@pytest_asyncio.fixture
async def multiple_communications(db_session: AsyncSession):
    """Varias comunicaciones de prueba."""
    communications = []

    for i in range(3):
        comm = CommunicationSuntech(
            device_id=f"SUNTECH{i}",
            latitude=Decimal(f"{19 + i}.{4326 + i}"),
            longitude=Decimal(f"-{99 + i}.{1332 + i}"),
            speed=Decimal(f"{40 + i * 5}"),
            course=Decimal("180.0"),
            gps_datetime=datetime(2024, 1, 15, 10 + i, 30, 0),
            gps_epoch=1705318200 + i * 3600,
            main_battery_voltage=Decimal("12.5"),
            odometer=15000 + i * 1000,
        )
        db_session.add(comm)
        communications.append(comm)

    for i in range(2):
        comm = CommunicationQueclink(
            device_id=f"QUECLINK{i}",
            latitude=Decimal(f"{25 + i}.{6866 + i}"),
            longitude=Decimal(f"-{100 + i}.{3161 + i}"),
            speed=Decimal(f"{50 + i * 10}"),
            course=Decimal("90.0"),
            gps_datetime=datetime(2024, 1, 15, 12 + i, 0, 0),
            gps_epoch=1705320000 + i * 3600,
            main_battery_voltage=Decimal("13.0"),
            odometer=20000 + i * 1000,
        )
        db_session.add(comm)
        communications.append(comm)

    await db_session.commit()

    for comm in communications:
        await db_session.refresh(comm)

    return communications


@pytest.fixture
def mock_device_ids() -> list[str]:
    """Device IDs de prueba."""
    return ["867564050638581", "QUECLINK123", "SUNTECH0"]


@pytest.fixture
def sse_headers() -> dict:
    """Headers para SSE."""
    return {"Accept": "text/event-stream"}
