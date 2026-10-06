"""
Tests para el módulo de configuración.
"""

import pytest

from app.core.config import settings


@pytest.mark.unit
class TestSettings:
    """Tests para la configuración de la aplicación."""

    def test_app_name_is_set(self):
        """
        Test: Nombre de la aplicación está configurado.
        """
        assert settings.APP_NAME == "siscom-api"

    def test_app_version_is_set(self):
        """
        Test: Versión de la aplicación está configurada.
        """
        assert settings.APP_VERSION is not None
        assert isinstance(settings.APP_VERSION, str)

    def test_database_url_format(self):
        """
        Test: DATABASE_URL tiene el formato correcto.
        """
        db_url = settings.DATABASE_URL

        assert db_url.startswith("postgresql+asyncpg://")
        assert settings.DB_USERNAME in db_url
        assert settings.DB_HOST in db_url
        assert str(settings.DB_PORT) in db_url
        assert settings.DB_DATABASE in db_url

    def test_database_pool_settings(self):
        """
        Test: Configuraciones del pool de conexiones son válidas.
        """
        assert settings.DB_MIN_CONNECTIONS >= 1
        assert settings.DB_MAX_CONNECTIONS >= settings.DB_MIN_CONNECTIONS
        assert settings.DB_CONNECTION_TIMEOUT_SECS > 0
        assert settings.DB_IDLE_TIMEOUT_SECS > 0

    def test_cors_settings_exist(self):
        """
        Test: Configuración CORS está presente.
        """
        assert settings.ALLOWED_ORIGINS is not None
        assert isinstance(settings.ALLOWED_ORIGINS, str)
