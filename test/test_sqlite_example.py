"""
Tests de ejemplo usando SQLite en memoria.

Este archivo demuestra cómo escribir tests nuevos con SQLite.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.communications import CommunicationSuntech


class TestSQLiteExample:
    """Ejemplos de tests usando SQLite en memoria."""

    @pytest.mark.asyncio
    async def test_create_communication_sqlite(self, db_session_sqlite: AsyncSession):
        """
        Ejemplo: Test con acceso directo a la base de datos SQLite.

        ✅ Usa db_session_sqlite para tests nuevos
        ✅ Más rápido que PostgreSQL
        ✅ No requiere DB externa
        """
        from datetime import datetime
        from decimal import Decimal

        # Crear registro
        comm = CommunicationSuntech(
            device_id="TEST_DEVICE_001",
            latitude=Decimal("19.4326"),
            longitude=Decimal("-99.1332"),
            speed=Decimal("45.5"),
            course=Decimal("180.0"),
            gps_datetime=datetime(2024, 1, 15, 10, 30, 0),
            gps_epoch=1705318200,
        )

        db_session_sqlite.add(comm)
        await db_session_sqlite.commit()
        await db_session_sqlite.refresh(comm)

        # Verificar
        assert comm.device_id == "TEST_DEVICE_001"
        assert comm.latitude == Decimal("19.4326")

    def test_simple_example(self, client_sqlite: TestClient):
        """
        Ejemplo: Test simple con cliente SQLite.

        ✅ Usa client_sqlite para tests de endpoints
        """
        # Ejemplo básico - ajusta según tu API
        assert client_sqlite is not None


# ============================================================================
# Comparación: PostgreSQL vs SQLite
# ============================================================================
#
# Para referencia, así se verían los mismos tests con PostgreSQL:
#
# @pytest.mark.asyncio
# async def test_create_communication_postgres(db_session: AsyncSession):
#     # Mismo código pero usa PostgreSQL real
#     pass
#
# def test_simple_example_postgres(client: TestClient):
#     # Mismo código pero usa PostgreSQL real
#     pass
#
# Diferencias:
# - PostgreSQL: ~30s para correr, requiere DB corriendo
# - SQLite: ~1s para correr, no requiere nada
# ============================================================================
