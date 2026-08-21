"""Tests del recorte del histórico contra la base de datos real.

Los tests del modelo (`test_scope_window.py`) comprueban la aritmética de
intervalos. Estos comprueban que esa aritmética llega de verdad al SQL: que las
filas fuera de ventana **no se traen**, no que se filtren después.

La distinción importa. Filtrar en Python daría el mismo resultado visible y
sería igual de correcto de cara al cliente, pero cargaría en memoria telemetría
de otra organización en cada consulta. El recorte tiene que ocurrir en la
consulta.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scope_window import AccessWindow, Interval
from app.models.communications import CommunicationSuntech
from app.services.repository import get_communications

_IMEI_A = "867564050638581"
_IMEI_B = "867564050638582"


def _naive(day: int) -> datetime:
    """`received_at` es naive en estas tablas."""
    return datetime(2026, 1, day)


def _aware(day: int) -> datetime:
    """Los límites de ventana llegan con zona."""
    return datetime(2026, 1, day, tzinfo=UTC)


@pytest.fixture
async def historial(db_session: AsyncSession):
    """Un mes de comunicaciones de dos dispositivos, una por día."""
    for imei in (_IMEI_A, _IMEI_B):
        for day in (5, 15, 25):
            db_session.add(
                CommunicationSuntech(
                    device_id=imei,
                    latitude=19.4326,
                    longitude=-99.1332,
                    received_at=_naive(day),
                )
            )
    await db_session.commit()


def _days(results) -> list[int]:
    return sorted(r.received_at.day for r in results)


@pytest.mark.unit
@pytest.mark.database
class TestHistoryClamping:
    async def test_without_windows_everything_is_returned(
        self, db_session: AsyncSession, historial
    ):
        """El comportamiento anterior a v1.3, que sigue siendo el de hoy."""
        results = await get_communications(db_session, [_IMEI_A])
        assert _days(results) == [5, 15, 25]

    async def test_a_closed_window_hides_later_data(
        self, db_session: AsyncSession, historial
    ):
        """La fuga que esto cierra: el equipo se reasignó el día 20."""
        windows = {_IMEI_A: AccessWindow((Interval(None, _aware(20)),))}
        results = await get_communications(db_session, [_IMEI_A], windows=windows)
        assert _days(results) == [5, 15]

    async def test_a_window_hides_earlier_data_too(
        self, db_session: AsyncSession, historial
    ):
        """El dueño nuevo no ve lo de antes de recibirlo."""
        windows = {_IMEI_A: AccessWindow((Interval(_aware(20), None),))}
        results = await get_communications(db_session, [_IMEI_A], windows=windows)
        assert _days(results) == [25]

    async def test_a_gap_between_two_windows_is_excluded(
        self, db_session: AsyncSession, historial
    ):
        """ "Estuvo, se fue, volvió": el hueco del medio no se concede.

        Es donde falla la implementación de intervalo único, y falla
        concediendo de más.
        """
        windows = {
            _IMEI_A: AccessWindow(
                (
                    Interval(None, _aware(10)),
                    Interval(_aware(20), None),
                )
            )
        }
        results = await get_communications(db_session, [_IMEI_A], windows=windows)
        assert _days(results) == [5, 25]

    async def test_an_empty_window_returns_nothing(
        self, db_session: AsyncSession, historial
    ):
        """Alcance revocado: cero filas, no todas.

        Un `or_()` sin cláusulas es VERDADERO en SQLAlchemy, así que este es el
        test que separa "sin acceso" de "acceso a la tabla entera".
        """
        windows = {_IMEI_A: AccessWindow.none()}
        results = await get_communications(db_session, [_IMEI_A], windows=windows)
        assert results == []

    async def test_a_device_missing_from_the_map_returns_nothing(
        self, db_session: AsyncSession, historial
    ):
        """Sin entrada en el mapa se deniega, no se concede por omisión."""
        results = await get_communications(db_session, [_IMEI_A], windows={})
        assert results == []

    async def test_each_device_gets_its_own_window(
        self, db_session: AsyncSession, historial
    ):
        """Lo que hace imposible expresarlo con un `IN` y un rango global."""
        windows = {
            _IMEI_A: AccessWindow((Interval(None, _aware(10)),)),
            _IMEI_B: AccessWindow((Interval(_aware(20), None),)),
        }
        results = await get_communications(
            db_session, [_IMEI_A, _IMEI_B], windows=windows
        )

        por_dispositivo = {}
        for r in results:
            por_dispositivo.setdefault(r.device_id, []).append(r.received_at.day)

        assert sorted(por_dispositivo[_IMEI_A]) == [5]
        assert sorted(por_dispositivo[_IMEI_B]) == [25]

    async def test_one_device_revoked_does_not_affect_the_other(
        self, db_session: AsyncSession, historial
    ):
        windows = {
            _IMEI_A: AccessWindow.none(),
            _IMEI_B: AccessWindow.always(),
        }
        results = await get_communications(
            db_session, [_IMEI_A, _IMEI_B], windows=windows
        )
        assert {r.device_id for r in results} == {_IMEI_B}

    async def test_the_boundary_is_half_open(self, db_session: AsyncSession, historial):
        """`until` excluido: el día del corte no lo ven las dos partes."""
        cerrado = {_IMEI_A: AccessWindow((Interval(None, _aware(15)),))}
        abierto = {_IMEI_A: AccessWindow((Interval(_aware(15), None),))}

        assert _days(
            await get_communications(db_session, [_IMEI_A], windows=cerrado)
        ) == [5]
        assert _days(
            await get_communications(db_session, [_IMEI_A], windows=abierto)
        ) == [15, 25]

    async def test_windows_combine_with_the_date_filter(
        self, db_session: AsyncSession, historial
    ):
        """`received_at` y la ventana se intersectan, no se sustituyen."""
        windows = {_IMEI_A: AccessWindow((Interval(None, _aware(20)),))}

        dentro = await get_communications(
            db_session, [_IMEI_A], received_at=_naive(15).date(), windows=windows
        )
        fuera = await get_communications(
            db_session, [_IMEI_A], received_at=_naive(25).date(), windows=windows
        )

        assert _days(dentro) == [15]
        assert fuera == []
