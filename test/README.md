# Tests - Guía de Uso

## 📋 Fixtures Disponibles

### Tests con PostgreSQL (Existentes)
Los tests **existentes** usan PostgreSQL real y deben mantenerse así:

```python
# Para tests con acceso directo a DB
async def test_existing_feature(db_session: AsyncSession):
    # Usa PostgreSQL real
    pass

# Para tests con cliente HTTP
def test_existing_endpoint(client: TestClient):
    # Usa PostgreSQL real
    pass
```

### Tests con SQLite en Memoria (Nuevos)
Los tests **nuevos** deben usar SQLite en memoria para mayor rapidez:

```python
# Para tests con acceso directo a DB
async def test_new_feature(db_session_sqlite: AsyncSession):
    # Usa SQLite en memoria
    # ✅ Más rápido
    # ✅ No requiere PostgreSQL corriendo
    pass

# Para tests con cliente HTTP
def test_new_endpoint(client_sqlite: TestClient):
    # Usa SQLite en memoria
    response = client_sqlite.get("/api/v1/health")
    assert response.status_code == 200
```

## 🎯 Cuándo Usar Cada Uno

### Usa PostgreSQL (`db_session`, `client`)
- ✅ Tests **existentes** (no cambiar)
- ✅ Tests que requieren características específicas de PostgreSQL
- ✅ Tests de integración con schemas complejos

### Usa SQLite (`db_session_sqlite`, `client_sqlite`)
- ✅ Tests **nuevos** (recomendado por defecto)
- ✅ Tests unitarios de modelos
- ✅ Tests de endpoints simples
- ✅ Tests que no dependen de PostgreSQL específico

## 📝 Ejemplo Completo

```python
# test/test_new_feature.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.my_model import MyModel

class TestNewFeature:
    """Tests para nueva funcionalidad usando SQLite."""
    
    async def test_create_record(self, db_session_sqlite: AsyncSession):
        """Test creación de registro en SQLite."""
        record = MyModel(name="test")
        db_session_sqlite.add(record)
        await db_session_sqlite.commit()
        
        assert record.id is not None
    
    def test_api_endpoint(self, client_sqlite: TestClient):
        """Test endpoint usando SQLite."""
        response = client_sqlite.post(
            "/api/v1/my-endpoint",
            json={"name": "test"}
        )
        assert response.status_code == 201
```

## ⚠️ Limitaciones de SQLite

SQLite no soporta algunos tipos de PostgreSQL:
- ❌ **JSONB** → Usar PostgreSQL para modelos con este tipo
- ❌ **Arrays** → Usar PostgreSQL
- ❌ **Schemas personalizados** → Usar PostgreSQL

**Modelos compatibles con SQLite:**
- ✅ `CommunicationSuntech`, `CommunicationQueclink` (tablas simples)
- ❌ `Event`, `EventType` (usan JSONB)

Para tests con modelos complejos, usa `db_session` (PostgreSQL).

| Característica | PostgreSQL | SQLite en Memoria |
|----------------|------------|-------------------|
| Velocidad | Lento (~30s) | Rápido (~1s) |
| Requisitos | DB externa | Ninguno |
| CI/CD | Requiere service | No requiere nada |
| Desarrollo local | Requiere Docker | Funciona siempre |

## 🔧 Configuración

Las fixtures están en `test/conftest.py`:
- `db_session` → PostgreSQL (existente)
- `db_session_sqlite` → SQLite (nuevo)
- `client` → PostgreSQL (existente)
- `client_sqlite` → SQLite (nuevo)

## 🚀 Correr Tests

```bash
# Todos los tests (incluye los que requieren PostgreSQL)
pytest test/

# Solo tests que no requieren PostgreSQL (marca pendiente)
pytest test/ -m "not requires_postgres"

# Tests específicos
pytest test/test_new_feature.py -v
```
