"""Ventanas temporales de acceso dentro de un alcance.

El contrato v1.3 añade tiempo a la autorización. Hasta ahora el hash de alcance
resolvía `device_ref → device_id` y eso concedía acceso a todo el histórico;
ahora el valor lleva además **cuándo** ese acceso es válido.

El caso que lo motiva: un equipo se reasigna de una organización a otra. La
anterior debe conservar el histórico del periodo en que fue suyo, pero no ver
lo que el aparato reporta desde que dejó de serlo. Sin ventana, la reasignación
filtra telemetría entre clientes rivales de forma indefinida.

Cuatro propiedades que este módulo implementa a propósito:

1. **Son varios intervalos, no uno.** Un equipo puede estar con un cliente,
   irse a otro y volver. Modelarlo como un intervalo único falla justo en ese
   caso, y falla concediendo de más: el hueco intermedio quedaría dentro.

2. **El extremo abierto significa "sigue asignado".** `until=None` no es "sin
   límite conocido", es "la asignación está viva ahora mismo".

3. **Se recorta, no se rechaza.** Pedir enero–diciembre con ventana enero–marzo
   devuelve enero–marzo. Es deliberadamente distinto del 403 sobre referencias
   ajenas: allí un filtrado silencioso convertiría la API en un oráculo de
   pertenencia, aquí no hay nada que revelar, porque quien pregunta ya conoce
   los límites de su propia ventana.

4. **"Ahora" es el mismo predicado evaluado en el instante actual.** La última
   posición y el stream en vivo no necesitan una regla aparte: exigen que
   alguna ventana contenga el momento presente, que es justo lo que ocurre
   cuando la asignación sigue abierta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Interval:
    """Un periodo de acceso concedido. `until=None` = asignación viva."""

    since: datetime | None
    until: datetime | None

    def contains(self, instant: datetime) -> bool:
        """Semiabierto: `since` incluido, `until` excluido.

        El extremo derecho abierto evita que el instante exacto de una
        reasignación quede concedido a las dos organizaciones a la vez.
        """
        after_start = self.since is None or instant >= self.since
        before_end = self.until is None or instant < self.until
        return after_start and before_end

    @property
    def is_open_ended(self) -> bool:
        return self.until is None

    def overlap(
        self, since: datetime | None, until: datetime | None
    ) -> Interval | None:
        """Intersección con el rango pedido, o None si no se solapan.

        `None` en el rango pedido significa "sin límite por ese lado", así que
        la intersección la fija el otro operando.
        """
        lo = _max_bound(self.since, since)
        hi = _min_bound(self.until, until)

        if lo is not None and hi is not None and lo >= hi:
            return None
        return Interval(lo, hi)


def _max_bound(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _min_bound(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


@dataclass(frozen=True)
class AccessWindow:
    """Los periodos en que un alcance concede acceso a una referencia.

    Una lista vacía no significa "sin restricción": significa **sin acceso**.
    La ausencia de ventanas es la forma que toma un alcance revocado, y tratarla
    como permisiva invertiría el fail closed.
    """

    intervals: tuple[Interval, ...]

    @classmethod
    def always(cls) -> AccessWindow:
        """Acceso sin límite temporal, el comportamiento anterior a v1.3."""
        return cls((Interval(None, None),))

    @classmethod
    def none(cls) -> AccessWindow:
        return cls(())

    def __bool__(self) -> bool:
        return bool(self.intervals)

    def allows_now(self, now: datetime | None = None) -> bool:
        """¿Sigue viva la asignación?

        Es el predicado que exigen `/communications/latest` y el WebSocket. No
        basta con que exista alguna ventana: tiene que contener este instante.
        """
        now = now or datetime.now(UTC)
        return any(interval.contains(now) for interval in self.intervals)

    def clamp(
        self, since: datetime | None, until: datetime | None
    ) -> tuple[Interval, ...]:
        """Recorta el rango pedido a lo que la ventana concede.

        Devuelve los tramos concedidos, en orden y sin solapamientos. Una tupla
        vacía significa que el rango pedido cae entero fuera: la respuesta
        correcta entonces es un resultado vacío, no un error, porque el cliente
        ya sabe cuál es su propia ventana.
        """
        granted = [
            overlap
            for interval in self.intervals
            if (overlap := interval.overlap(since, until)) is not None
        ]
        return tuple(sorted(granted, key=lambda i: (i.since is not None, i.since)))

    def covers_instant(self, instant: datetime) -> bool:
        """¿Cae este momento concreto dentro de alguna ventana?

        Lo usa el filtro por fecha de `/devices/{id}/communications`, donde no
        hay rango sino un día suelto.
        """
        return any(interval.contains(instant) for interval in self.intervals)


# ── Punto de parseo — PROVISIONAL ───────────────────────────────────────────
#
# ⚠️  Esta es la ÚNICA función que depende de cómo admin-api codifica el valor
# del hash de alcance. Cuando confirmen la forma exacta del contrato v1.3, se
# cambia aquí y nada más: el modelo de intervalos, el recorte y los puntos de
# aplicación no dependen de la codificación.
#
# Mientras tanto acepta dos formas, y la primera no es una suposición sino lo
# que admin-api escribe HOY:
#
#   1. Cadena simple  →  "867564050638581"
#      El identificador interno a secas, sin ventana. Se interpreta como acceso
#      sin límite temporal, que es exactamente el comportamiento anterior a
#      v1.3. Así el despliegue de este código no cambia nada por sí solo.
#
#   2. Objeto JSON  →  {"id": "867564050638581",
#                       "w": [{"since": "...", "until": "..."}, ...]}
#      Con `until` ausente o nulo para la asignación viva. Es una forma
#      plausible, NO acordada: si admin-api elige otra, se ajusta aquí.
#
# Lo que NO es provisional y no debe relajarse al ajustar el formato: un valor
# que se reconoce como v1.3 pero cuya lista de ventanas está vacía concede
# CERO acceso. La ausencia de ventanas es un alcance revocado, no un alcance
# sin restricciones.


def _parse_instant(raw) -> datetime | None:
    if raw in (None, ""):
        return None
    parsed = datetime.fromisoformat(raw)
    # Un instante sin zona es ambiguo; se trata como UTC en vez de dejar que
    # la comparación reviente más adelante con un TypeError.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_scope_value(raw: str) -> tuple[str, AccessWindow] | None:
    """Traduce el valor del hash a `(id_interno, ventana)`.

    Returns:
        La pareja, o `None` si el valor no es interpretable — que se trata como
        denegación, igual que un campo ausente.
    """
    if not raw:
        return None

    stripped = raw.strip()

    # Forma 1: identificador a secas.
    if not stripped.startswith("{"):
        return stripped, AccessWindow.always()

    # Forma 2: objeto con ventanas.
    try:
        payload = json.loads(stripped)
        internal_id = payload["id"]
        if not internal_id:
            return None

        intervals = tuple(
            Interval(_parse_instant(w.get("since")), _parse_instant(w.get("until")))
            for w in payload.get("w", [])
        )
    except Exception:
        # Un valor malformado deniega. No se cae de vuelta a "sin ventana":
        # eso convertiría un error de codificación en acceso ilimitado.
        return None

    return internal_id, AccessWindow(intervals)
