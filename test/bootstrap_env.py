"""Apply test-only environment defaults before app Settings() loads."""

import os

_TEST_ENV_DEFAULTS = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USERNAME": "test",
    "DB_PASSWORD": "test",
    "DB_DATABASE": "siscom_test",
    "JWT_SECRET_KEY": "test-secret-key-for-ci-minimum-32-chars-long",
    "JWT_ALGORITHM": "HS256",
    "PASETO_SECRET_KEY": "dGVzdC1zZWNyZXQta2V5LWZvci1jaS10ZXN0cy0zMmI=",
    "STATSD_ENABLED": "false",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
}


def apply_test_env_defaults() -> None:
    for key, value in _TEST_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def bootstrap_test_runtime() -> None:
    apply_test_env_defaults()
