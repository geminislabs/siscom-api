"""Apply test-only environment defaults before app Settings() loads."""

import os

_TEST_ENV_DEFAULTS = {
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_USERNAME": "test",
    "DB_PASSWORD": "test",
    "DB_DATABASE": "siscom_test",
    "PASETO_SECRET_KEY": "dGVzdC1zZWNyZXQta2V5LWZvci1jaS10ZXN0cy0zMmI=",
    "STATSD_ENABLED": "false",
    "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    "DEPLOY_ENV": "test",
    "SERVICE_NAME": "siscom-api",
    "LOG_LEVEL": "INFO",
}


def apply_test_env_defaults() -> None:
    for key, value in _TEST_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)
    # Los tests no exportan al Collector aunque el .env local tenga URL.
    os.environ["OTLP_ENDPOINT"] = ""


def bootstrap_test_runtime() -> None:
    apply_test_env_defaults()
