from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Información de la aplicación
    APP_NAME: str = "siscom-api"
    APP_VERSION: str = "0.1.0"

    # Circuit Breaker/Retry para Kafka
    KAFKA_MAX_RETRIES: int = 5
    KAFKA_CIRCUIT_BREAKER_COOLDOWN: int = 300

    # Configuración de Base de Datos
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_USERNAME: str = ""
    DB_PASSWORD: str = ""
    DB_DATABASE: str = ""
    DB_MIN_CONNECTIONS: int = 10
    DB_MAX_CONNECTIONS: int = 20
    DB_CONNECTION_TIMEOUT_SECS: int = 30
    DB_IDLE_TIMEOUT_SECS: int = 300

    # Seguridad JWT
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Seguridad PASETO — tokens de compartir ubicación (v4.local)
    #
    # SHARE_LOCATION_KEY_B64 es la clave dedicada y la única que este servicio
    # debe conservar a medio plazo.
    #
    # PASETO_SECRET_KEY es la clave HEREDADA, compartida con siscom-admin-api,
    # que además firma sus tokens de servicio `internal-*`. Mientras esté
    # presente aquí, este servicio puede emitir tokens administrativos de
    # admin-api. Se acepta SOLO durante la ventana de transición.
    #
    # ► Paso final de la migración: vaciar/eliminar PASETO_SECRET_KEY del
    #   entorno. El validador pasa a aceptar únicamente la clave dedicada sin
    #   ningún cambio de código.
    SHARE_LOCATION_KEY_B64: str = ""
    PASETO_SECRET_KEY: str = ""

    # CORS
    # Lista separada por comas. El default cubre solo orígenes de desarrollo
    # local: en producción DEBE configurarse explícitamente. Usar "*" deshabilita
    # las credenciales (ver app/main.py).
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # Documentación interactiva (/api/docs, /api/redoc, /api/openapi.json).
    # Deshabilitada por defecto: publica el mapa completo de la API sin
    # autenticación. Habilitar solo en entornos de desarrollo.
    ENABLE_API_DOCS: bool = False

    # Métricas StatsD
    STATSD_ENABLED: bool = False  # Cambiar a True cuando tengas StatsD corriendo
    STATSD_HOST: str = "localhost"
    STATSD_PORT: int = (
        8126  # Puerto dedicado para siscom-api (8125 reservado para otros proyectos)
    )
    STATSD_PREFIX: str = "siscom_api"

    # Kafka/Redpanda Configuration
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_TOPIC: str = "tracking/data"
    KAFKA_ALERTS_TOPIC: str = ""
    KAFKA_GROUP_ID: str = "siscom-api-consumer"
    KAFKA_AUTO_OFFSET_RESET: str = "latest"
    KAFKA_USERNAME: str = ""
    KAFKA_PASSWORD: str = ""
    KAFKA_SASL_MECHANISM: str = "SCRAM-SHA-256"
    KAFKA_SECURITY_PROTOCOL: str = "SASL_PLAINTEXT"

    # Streaming WebSocket
    # Intervalo del ping keep-alive (segundos). Debe ser holgadamente MENOR que
    # el idle timeout del proxy/load-balancer que tenga delante (ALB/nginx
    # default 60s), o el proxy cerrará sockets ociosos (vehículos parados de
    # noche → solo fluye el keep-alive). Recomendado ~20-25s bajo ese timeout.
    WEBSOCKET_KEEPALIVE_SECS: int = 25

    # Para compatibilidad con código existente que use DATABASE_URL
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.DB_USERNAME}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
