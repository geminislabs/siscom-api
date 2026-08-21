"""Tests de las ventanas temporales de acceso (contrato v1.3).

El caso que motiva todo esto: un equipo se reasigna de una organización a otra.
La anterior conserva su histórico pero no debe ver lo que el aparato reporta
desde que dejó de ser suyo. Casi todos los tests de aquí son variantes de esa
situación, porque es donde la lógica de intervalos se equivoca concediendo de
más.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.scope_window import (
    AccessWindow,
    Interval,
    parse_scope_value,
)


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=UTC)


# Un equipo con dos periodos separados: estuvo, se fue, y volvió abierto.
# Es la forma que rompe cualquier implementación que asuma un intervalo único.
_IDA_Y_VUELTA = AccessWindow(
    (
        Interval(_dt(1), _dt(10)),
        Interval(_dt(20), None),
    )
)


@pytest.mark.unit
class TestNowPredicate:
    """`/communications/latest` y el WebSocket exigen asignación viva."""

    def test_open_ended_window_allows_now(self):
        window = AccessWindow((Interval(_dt(1), None),))
        assert window.allows_now(_dt(15))

    def test_closed_window_does_not_allow_now(self):
        """El caso de la fuga: el equipo ya se reasignó."""
        window = AccessWindow((Interval(_dt(1), _dt(10)),))
        assert not window.allows_now(_dt(15))

    def test_empty_window_denies(self):
        """Sin ventanas es alcance revocado, no alcance sin restricciones."""
        assert not AccessWindow.none().allows_now(_dt(15))

    def test_gap_between_intervals_denies(self):
        """Entre los dos periodos el equipo era de otro."""
        assert not _IDA_Y_VUELTA.allows_now(_dt(15))

    def test_second_interval_allows_now(self):
        assert _IDA_Y_VUELTA.allows_now(_dt(25))

    def test_the_instant_of_reassignment_is_excluded(self):
        """`until` excluido: el momento del corte no lo conceden ambas partes."""
        window = AccessWindow((Interval(_dt(1), _dt(10)),))
        assert window.allows_now(_dt(10) - timedelta(microseconds=1))
        assert not window.allows_now(_dt(10))

    def test_the_instant_of_assignment_is_included(self):
        window = AccessWindow((Interval(_dt(10), None),))
        assert window.allows_now(_dt(10))

    def test_always_allows_now(self):
        """El comportamiento anterior a v1.3, sin ventana."""
        assert AccessWindow.always().allows_now(_dt(15))


@pytest.mark.unit
class TestClamping:
    """Se recorta el rango pedido, no se rechaza la petición."""

    def test_request_wider_than_the_window_is_trimmed(self):
        window = AccessWindow((Interval(_dt(5), _dt(10)),))
        granted = window.clamp(_dt(1), _dt(20))
        assert granted == (Interval(_dt(5), _dt(10)),)

    def test_request_narrower_than_the_window_is_untouched(self):
        window = AccessWindow((Interval(_dt(1), _dt(20)),))
        granted = window.clamp(_dt(5), _dt(10))
        assert granted == (Interval(_dt(5), _dt(10)),)

    def test_request_entirely_outside_grants_nothing(self):
        """Vacío, no error: el cliente ya conoce su propia ventana."""
        window = AccessWindow((Interval(_dt(1), _dt(10)),))
        assert window.clamp(_dt(15), _dt(20)) == ()

    def test_a_request_spanning_a_gap_returns_two_pieces(self):
        """Lo que rompe la implementación de intervalo único.

        Pedir todo enero con "estuvo, se fue, volvió" debe devolver los dos
        tramos, no uno que se trague el hueco del medio.
        """
        granted = _IDA_Y_VUELTA.clamp(_dt(1), _dt(31))
        assert granted == (
            Interval(_dt(1), _dt(10)),
            Interval(_dt(20), _dt(31)),
        )

    def test_the_gap_itself_is_never_granted(self):
        assert _IDA_Y_VUELTA.clamp(_dt(12), _dt(18)) == ()

    def test_open_ended_window_clamps_to_the_requested_end(self):
        window = AccessWindow((Interval(_dt(1), None),))
        assert window.clamp(_dt(5), _dt(10)) == (Interval(_dt(5), _dt(10)),)

    def test_open_ended_request_clamps_to_the_window_end(self):
        window = AccessWindow((Interval(_dt(1), _dt(10)),))
        assert window.clamp(None, None) == (Interval(_dt(1), _dt(10)),)

    def test_unbounded_on_both_sides_stays_unbounded(self):
        assert AccessWindow.always().clamp(None, None) == (Interval(None, None),)

    def test_empty_window_grants_nothing(self):
        assert AccessWindow.none().clamp(_dt(1), _dt(31)) == ()

    def test_touching_boundaries_do_not_grant_an_empty_sliver(self):
        """Pedir justo desde el final de la ventana no concede nada."""
        window = AccessWindow((Interval(_dt(1), _dt(10)),))
        assert window.clamp(_dt(10), _dt(20)) == ()


@pytest.mark.unit
class TestSingleInstant:
    """El filtro `received_at` de un día suelto, no un rango."""

    def test_instant_inside_a_window(self):
        assert _IDA_Y_VUELTA.covers_instant(_dt(5))

    def test_instant_in_the_gap(self):
        assert not _IDA_Y_VUELTA.covers_instant(_dt(15))

    def test_instant_after_a_closed_window(self):
        window = AccessWindow((Interval(_dt(1), _dt(10)),))
        assert not window.covers_instant(_dt(25))


@pytest.mark.unit
class TestParsing:
    """El punto provisional. Lo que se fija aquí es la SEMÁNTICA."""

    def test_a_bare_identifier_means_no_time_limit(self):
        """Es lo que admin-api escribe hoy: desplegar esto no cambia nada."""
        parsed = parse_scope_value("867564050638581")
        assert parsed is not None
        internal_id, window = parsed
        assert internal_id == "867564050638581"
        assert window.allows_now()

    def test_an_object_with_an_open_window(self):
        parsed = parse_scope_value(
            '{"id": "IMEI1", "w": [{"since": "2026-01-01T00:00:00+00:00"}]}'
        )
        assert parsed is not None
        internal_id, window = parsed
        assert internal_id == "IMEI1"
        assert window.allows_now(_dt(15))

    def test_an_object_with_a_closed_window(self):
        parsed = parse_scope_value(
            '{"id": "IMEI1", "w": [{"since": "2026-01-01T00:00:00+00:00",'
            ' "until": "2026-01-10T00:00:00+00:00"}]}'
        )
        assert parsed is not None
        _, window = parsed
        assert not window.allows_now(_dt(15))
        assert window.allows_now(_dt(5))

    def test_multiple_intervals_survive_parsing(self):
        parsed = parse_scope_value(
            '{"id": "IMEI1", "w": ['
            '{"since": "2026-01-01T00:00:00+00:00", "until": "2026-01-10T00:00:00+00:00"},'
            '{"since": "2026-01-20T00:00:00+00:00"}]}'
        )
        assert parsed is not None
        _, window = parsed
        assert len(window.intervals) == 2
        assert not window.allows_now(_dt(15))
        assert window.allows_now(_dt(25))

    def test_an_empty_window_list_grants_nothing(self):
        """Reconocido como v1.3 pero sin ventanas = revocado.

        Es la regla que NO debe relajarse al ajustar el formato: caer de vuelta
        a "sin límite" convertiría un alcance revocado en acceso ilimitado.
        """
        parsed = parse_scope_value('{"id": "IMEI1", "w": []}')
        assert parsed is not None
        _, window = parsed
        assert not window
        assert not window.allows_now()

    def test_naive_timestamps_are_treated_as_utc(self):
        """Sin zona, comparar reventaría con TypeError a mitad de petición."""
        parsed = parse_scope_value(
            '{"id": "IMEI1", "w": [{"since": "2026-01-01T00:00:00"}]}'
        )
        assert parsed is not None
        _, window = parsed
        assert window.allows_now(_dt(15))

    def test_malformed_json_denies_instead_of_falling_back(self):
        """Un error de codificación no debe convertirse en acceso ilimitado."""
        assert parse_scope_value('{"id": "IMEI1", "w": [') is None

    def test_a_bad_timestamp_denies(self):
        assert parse_scope_value('{"id": "IMEI1", "w": [{"since": "ayer"}]}') is None

    def test_a_missing_id_denies(self):
        assert parse_scope_value('{"w": []}') is None

    def test_an_empty_value_denies(self):
        assert parse_scope_value("") is None


@pytest.mark.unit
class TestEmptyWindowIsNotAMissingWindow:
    """`AccessWindow.none()` es falsy, y eso es una trampa.

    Escribir `window or AccessWindow.always()` para poner un valor por defecto
    convierte un alcance REVOCADO en acceso ILIMITADO, porque la ventana vacía
    es indistinguible de "no me pasaron nada" en un contexto booleano. Estos
    tests fijan la distinción; el código usa `is None` por este motivo.
    """

    def test_an_empty_window_is_falsy(self):
        assert not AccessWindow.none()

    def test_the_or_idiom_silently_grants_everything(self):
        """Demostración del fallo, para que quede claro por qué no se usa."""
        revocada = AccessWindow.none()
        degradada = revocada or AccessWindow.always()
        assert degradada.allows_now()  # ← el alcance revocado concede todo

    def test_the_is_none_idiom_preserves_the_revocation(self):
        revocada = AccessWindow.none()
        correcta = AccessWindow.always() if revocada is None else revocada
        assert not correcta.allows_now()

    def test_an_always_window_is_truthy(self):
        """Para que la distinción no se resuelva haciendo todo truthy."""
        assert AccessWindow.always()
