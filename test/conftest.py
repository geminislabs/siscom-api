"""
Configuración de fixtures de pytest para SISCOM API.

Este módulo contiene fixtures reutilizables para tests.
"""

import asyncio
from collections.abc import AsyncGenerator, Generator
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token
from app.main import app

# Importar TODOS los modelos para registrar sus tablas
from app.models.communications import Base, CommunicationQueclink, CommunicationSuntech

# ============================================================================
# Configuración de Base de Datos de Test
# ============================================================================

# URL de base de datos de test (usa una BD separada o in-memory)
TEST_DATABASE_URL = settings.DATABASE_URL.replace(
    f"/{settings.DB_DATABASE}", "/siscom_test"
)

# Engine de test con NullPool para evitar problemas con múltiples tests
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


# ============================================================================
# Configuración de Base de Datos SQLite (para tests nuevos)
# ============================================================================
# Los tests EXISTENTES usan PostgreSQL (arriba).
# Los tests NUEVOS pueden usar SQLite en memoria para mayor rapidez.
#
# Uso:
#   - Para tests con PostgreSQL: usa `db_session` fixture (existente)
#   - Para tests con SQLite: usa `db_session_sqlite` fixture (nuevo)

# SQLite en memoria con aiosqlite
SQLITE_TEST_URL = "sqlite+aiosqlite:///:memory:"

# Engine SQLite con configuración async y pool compartido
sqlite_test_engine = create_async_engine(
    SQLITE_TEST_URL,
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,  # ← Importante: StaticPool para compartir conexión en memoria
)

SqliteTestSessionLocal = sessionmaker(
    sqlite_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ============================================================================
# Fixtures de Event Loop
# ============================================================================


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """
    Crea un event loop para toda la sesión de tests.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# Fixtures de Base de Datos
# ============================================================================


@pytest.fixture(scope="session")
async def setup_test_database():
    """
    Crea las tablas de test al inicio de la sesión de tests
    y las elimina al final.
    """
    # Crear todas las tablas
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    # Eliminar todas las tablas
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db_session(setup_test_database) -> AsyncGenerator[AsyncSession, None]:
    """
    Crea una sesión de base de datos para cada test.
    La sesión se hace rollback al final del test.

    NOTA: Usa PostgreSQL real. Para tests nuevos, considera usar
    `db_session_sqlite` para mayor rapidez.
    """
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def db_session_sqlite() -> AsyncGenerator[AsyncSession, None]:
    """
    Crea una sesión de base de datos SQLite en memoria para cada test.

    USO RECOMENDADO PARA TESTS NUEVOS:
    - Más rápido que PostgreSQL
    - No requiere DB externa
    - Ideal para tests unitarios

    Ejemplo:
        @pytest.mark.asyncio
        async def test_my_feature(db_session_sqlite: AsyncSession):
            # Tu test aquí
    """
    # Crear tablas en esta sesión
    async with sqlite_test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Crear sesión
    async with SqliteTestSessionLocal() as session:
        yield session
        await session.rollback()

    # Limpiar tablas
    async with sqlite_test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
def override_get_db(db_session: AsyncSession):
    """
    Override de la dependencia get_db para usar la BD de test PostgreSQL.
    """

    async def _get_db_override():
        yield db_session

    return _get_db_override


@pytest.fixture
def override_get_db_sqlite(db_session_sqlite: AsyncSession):
    """
    Override de la dependencia get_db para usar SQLite en memoria.

    Para tests nuevos que no requieren PostgreSQL.
    """

    async def _get_db_override():
        yield db_session_sqlite

    return _get_db_override


# ============================================================================
# Fixtures de Cliente HTTP
# ============================================================================


@pytest.fixture
def client(override_get_db) -> Generator:
    """
    Cliente de test síncrono con TestClient usando PostgreSQL.

    Para tests existentes.
    """
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def client_sqlite(override_get_db_sqlite) -> Generator:
    """
    Cliente de test síncrono con TestClient usando SQLite en memoria.

    USO RECOMENDADO PARA TESTS NUEVOS.

    Ejemplo:
        def test_my_endpoint(client_sqlite: TestClient):
            response = client_sqlite.get("/api/v1/health")
            assert response.status_code == 200
    """
    app.dependency_overrides[get_db] = override_get_db_sqlite

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
async def async_client(override_get_db) -> AsyncGenerator:
    """
    Cliente de test asíncrono con httpx.AsyncClient.
    """
    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


# ============================================================================
# Fixtures de Autenticación
# ============================================================================


@pytest.fixture
def valid_token() -> str:
    """
    Crea un JWT token válido para tests.
    """
    return create_access_token({"sub": "test_user", "user_id": 1})


@pytest.fixture
def expired_token() -> str:
    """
    Crea un JWT token expirado para tests.
    """
    data = {"sub": "test_user", "user_id": 1}
    expire = datetime.utcnow() - timedelta(minutes=10)  # Expirado hace 10 minutos
    data.update({"exp": expire})

    from jose import jwt

    return jwt.encode(data, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


@pytest.fixture
def invalid_token() -> str:
    """
    Token JWT inválido (mal firmado).
    """
    return "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature"


@pytest.fixture
def auth_headers(valid_token: str) -> dict:
    """
    Headers con token de autenticación válido.
    """
    return {"Authorization": f"Bearer {valid_token}"}


# ============================================================================
# Fixtures de Datos de Test
# ============================================================================


@pytest.fixture
async def sample_suntech_communication(db_session: AsyncSession):
    """
    Crea un registro de comunicación Suntech de prueba.
    """
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


@pytest.fixture
async def sample_queclink_communication(db_session: AsyncSession):
    """
    Crea un registro de comunicación Queclink de prueba.
    """
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


@pytest.fixture
async def multiple_communications(db_session: AsyncSession):
    """
    Crea múltiples registros de comunicaciones para tests.
    """
    communications = []

    # 3 registros Suntech
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

    # 2 registros Queclink
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


# ============================================================================
# Fixtures de Utilidad
# ============================================================================


@pytest.fixture
def mock_device_ids() -> list[str]:
    """
    Lista de device IDs de prueba.
    """
    return ["867564050638581", "QUECLINK123", "SUNTECH0"]


@pytest.fixture
def sse_headers() -> dict:
    """
    Headers para peticiones Server-Sent Events.
    """
    return {"Accept": "text/event-stream"}
