"""
Tests para el módulo de seguridad y autenticación JWT.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core.config import settings
from app.core.security import create_access_token, verify_token


@pytest.mark.unit
@pytest.mark.auth
class TestJWTToken:
    """Tests de creación y verificación de JWT tokens."""

    def test_create_access_token(self):
        """
        Test: Crear un token de acceso válido.
        """
        data = {"sub": "test_user", "user_id": 1}
        token = create_access_token(data)

        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_contains_payload_data(self):
        """
        Test: Token contiene los datos del payload.
        """
        data = {"sub": "test_user", "user_id": 1, "role": "admin"}
        token = create_access_token(data)

        # Decodificar token
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )

        assert payload["sub"] == "test_user"
        assert payload["user_id"] == 1
        assert payload["role"] == "admin"

    def test_token_has_expiration(self):
        """
        Test: Token tiene fecha de expiración.
        """
        data = {"sub": "test_user"}
        token = create_access_token(data)

        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )

        assert "exp" in payload

        # Verificar que la expiración es aproximadamente correcta
        exp_datetime = datetime.fromtimestamp(payload["exp"], tz=UTC).replace(
            tzinfo=None
        )
        expected_exp = datetime.now(UTC).replace(tzinfo=None) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

        # Permitir 10 segundos de diferencia
        assert abs((exp_datetime - expected_exp).total_seconds()) < 10

    def test_verify_valid_token(self):
        """
        Test: Verificar un token válido.
        """
        data = {"sub": "test_user", "user_id": 1}
        token = create_access_token(data)

        payload = verify_token(token)

        assert payload["sub"] == "test_user"
        assert payload["user_id"] == 1

    def test_verify_invalid_token_raises_exception(self):
        """
        Test: Token inválido lanza HTTPException.
        """
        invalid_token = "invalid.token.here"

        with pytest.raises(HTTPException) as exc_info:
            verify_token(invalid_token)

        assert exc_info.value.status_code == 401
        assert "Invalid token" in str(exc_info.value.detail)

    def test_verify_expired_token_raises_exception(self, expired_token: str):
        """
        Test: Token expirado lanza HTTPException.
        """
        with pytest.raises(HTTPException) as exc_info:
            verify_token(expired_token)

        assert exc_info.value.status_code == 401

    def test_verify_token_with_wrong_secret(self):
        """
        Test: Token firmado con secret incorrecto falla.
        """
        data = {"sub": "test_user"}
        # Firmar con secret diferente
        wrong_token = jwt.encode(
            data, "wrong_secret_key", algorithm=settings.JWT_ALGORITHM
        )

        with pytest.raises(HTTPException) as exc_info:
            verify_token(wrong_token)

        assert exc_info.value.status_code == 401

    def test_verify_token_with_wrong_algorithm(self):
        """
        Test: Token firmado con algoritmo diferente falla.
        """
        data = {"sub": "test_user"}
        expire = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)
        data.update({"exp": expire})

        # Firmar con algoritmo diferente
        wrong_token = jwt.encode(
            data, settings.JWT_SECRET_KEY, algorithm="HS512"  # Diferente a HS256
        )

        with pytest.raises(HTTPException) as exc_info:
            verify_token(wrong_token)

        assert exc_info.value.status_code == 401


@pytest.mark.unit
@pytest.mark.auth
class TestTokenEdgeCases:
    """Tests de casos extremos para JWT tokens."""

    def test_token_with_empty_payload(self):
        """
        Test: Token con payload vacío.
        """
        token = create_access_token({})
        payload = verify_token(token)

        assert "exp" in payload

    def test_token_with_special_characters(self):
        """
        Test: Token con caracteres especiales en el payload.
        """
        data = {
            "sub": "user@example.com",
            "name": "José María O'Brien",
            "role": "admin/moderator",
        }

        token = create_access_token(data)
        payload = verify_token(token)

        assert payload["sub"] == "user@example.com"
        assert payload["name"] == "José María O'Brien"

    def test_token_with_large_payload(self):
        """
        Test: Token con payload grande.
        """
        data = {
            "sub": "test_user",
            "permissions": ["read", "write", "delete"] * 100,
            "metadata": {"key": "value"},
        }

        token = create_access_token(data)
        payload = verify_token(token)

        assert payload["sub"] == "test_user"
        assert len(payload["permissions"]) == 300
