"""Tests de la resolución de scope contra Valkey.

Aquí vive la propiedad de la que depende el aislamiento: **fail closed**. Un
scope revocado, una clave que no existe, un timeout o Valkey caído tienen que
denegar, nunca permitir. Un fallo abierto en este punto expone la flota ajena.
"""

import pytest

from app.core.scope_window import AccessWindow
from app.services.scope_store import ScopeStore, ScopeStoreUnavailable


class FakeValkey:
    """Doble de Valkey que cuenta llamadas y puede simular una caída."""

    def __init__(self, data: dict | None = None, fail: bool = False):
        self.data = data or {}
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def hget(self, key: str, field: str):
        self.calls.append((key, field))
        if self.fail:
            raise ConnectionError("Valkey no disponible")
        return self.data.get(key, {}).get(field)


@pytest.fixture
def store(monkeypatch):
    """ScopeStore con un Valkey falso inyectado."""

    def _build(data=None, fail=False):
        fake = FakeValkey(data, fail)
        instance = ScopeStore()
        monkeypatch.setattr(instance, "_connect", lambda: fake)
        return instance, fake

    return _build


@pytest.mark.unit
class TestResolution:
    async def test_resolves_a_granted_ref_to_its_internal_id(self, store):
        instance, _ = store({"dt:scope:s1:dev": {"ref-a": "867564050638581"}})
        resolved = await instance.resolve("s1", "dev", "ref-a", 30)
        assert resolved == ("867564050638581", AccessWindow.always())

    async def test_uses_hget_not_hgetall(self, store):
        """Una flota grande no se trae entera para resolver un dispositivo."""
        instance, fake = store({"dt:scope:s1:dev": {"ref-a": "imei-1"}})
        await instance.resolve("s1", "dev", "ref-a", 30)
        assert fake.calls == [("dt:scope:s1:dev", "ref-a")]

    async def test_devices_and_units_are_separate_keys(self, store):
        instance, fake = store({"dt:scope:s1:unit": {"u-1": "uuid-1"}})
        resolved = await instance.resolve("s1", "unit", "u-1", 30)
        assert resolved is not None and resolved[0] == "uuid-1"
        assert fake.calls[0][0] == "dt:scope:s1:unit"

    async def test_a_scope_cannot_reach_another_scopes_refs(self, store):
        """El aislamiento entre marcas: mismo ref, distinto scope, denegado."""
        instance, _ = store({"dt:scope:s1:dev": {"ref-a": "imei-1"}})
        assert await instance.resolve("s2", "dev", "ref-a", 30) is None


@pytest.mark.unit
class TestFailClosed:
    async def test_missing_field_is_denied(self, store):
        instance, _ = store({"dt:scope:s1:dev": {"ref-a": "imei-1"}})
        assert await instance.resolve("s1", "dev", "ref-desconocida", 30) is None

    async def test_revoked_scope_is_denied(self, store):
        """La revocación en admin-api es un DEL: la clave deja de existir."""
        instance, _ = store({})
        assert await instance.resolve("s1", "dev", "ref-a", 30) is None

    async def test_valkey_outage_is_reported_apart_from_denial(self, store):
        """Sigue denegando, pero se distingue de "no autorizado".

        Las dos cosas niegan el acceso, pero una es un problema de la
        credencial y la otra de este servicio. Confundirlas hace que el cliente
        reemita el token para arreglar un corte de Valkey.
        """
        instance, _ = store(fail=True)
        with pytest.raises(ScopeStoreUnavailable):
            await instance.resolve("s1", "dev", "ref-a", 30)

    async def test_unconfigured_valkey_is_reported_apart_too(self, monkeypatch):
        monkeypatch.setattr("app.services.scope_store.settings.VALKEY_URL", "")
        with pytest.raises(ScopeStoreUnavailable):
            await ScopeStore().resolve("s1", "dev", "ref-a", 30)


@pytest.mark.unit
class TestCaching:
    async def test_a_hit_is_cached(self, store):
        instance, fake = store({"dt:scope:s1:dev": {"ref-a": "imei-1"}})
        await instance.resolve("s1", "dev", "ref-a", 30)
        await instance.resolve("s1", "dev", "ref-a", 30)
        assert len(fake.calls) == 1

    async def test_a_miss_is_cached_too(self, store):
        """Cachear la denegación evita que sondear sea gratis."""
        instance, fake = store({})
        await instance.resolve("s1", "dev", "ref-a", 30)
        await instance.resolve("s1", "dev", "ref-a", 30)
        assert len(fake.calls) == 1

    async def test_an_outage_is_never_cached(self, store):
        """Un corte no debe congelar 30 s de denegaciones tras recuperarse."""
        instance, fake = store(fail=True)
        for _ in range(2):
            with pytest.raises(ScopeStoreUnavailable):
                await instance.resolve("s1", "dev", "ref-a", 30)
        assert len(fake.calls) == 2

    async def test_a_zero_ceiling_disables_caching(self, store):
        """Con un token a punto de expirar no se cachea nada."""
        instance, fake = store({"dt:scope:s1:dev": {"ref-a": "imei-1"}})
        await instance.resolve("s1", "dev", "ref-a", 0)
        await instance.resolve("s1", "dev", "ref-a", 0)
        assert len(fake.calls) == 2

    async def test_the_ceiling_shortens_but_never_extends_the_ttl(
        self, store, monkeypatch
    ):
        """El `exp` del token acota la caché; nunca la alarga.

        El emisor recorta el `exp` al siguiente límite de ventana horaria, así
        que una entrada cacheada no puede sobrevivir al token que la autorizó.
        """
        monkeypatch.setattr(
            "app.services.scope_store.settings.SCOPE_CACHE_TTL_SECS", 30
        )
        instance, _ = store({"dt:scope:s1:dev": {"ref-a": "imei-1"}})

        import time

        before = time.monotonic()
        await instance.resolve("s1", "dev", "ref-a", 2)
        _, expires_at = instance._cache[("s1", "dev", "ref-a")]

        ttl = expires_at - before
        # Manda el techo del token (2 s), no el global de 30 s. La holgura
        # cubre el tiempo de la propia llamada.
        assert 1.9 <= ttl <= 2.1

    async def test_cache_is_scoped_per_scope_ref(self, store):
        """Cachear por ref sin el scope filtraría entre marcas."""
        instance, fake = store(
            {
                "dt:scope:s1:dev": {"ref-a": "imei-1"},
                "dt:scope:s2:dev": {"ref-a": "imei-2"},
            }
        )
        first = await instance.resolve("s1", "dev", "ref-a", 30)
        second = await instance.resolve("s2", "dev", "ref-a", 30)
        assert first is not None and first[0] == "imei-1"
        assert second is not None and second[0] == "imei-2"
        assert len(fake.calls) == 2
