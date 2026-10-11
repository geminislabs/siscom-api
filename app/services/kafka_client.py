import asyncio
import json
import logging
import threading
import time
from contextlib import suppress
from datetime import UTC, datetime

from kafka import KafkaConsumer

from app.core.config import settings
from app.observability.metrics import record_kafka_message, record_kafka_reconnect

logger = logging.getLogger(__name__)


class KafkaClient:
    """Cliente Kafka/Redpanda para recibir mensajes en tiempo real."""

    def __init__(self):
        self.consumer: KafkaConsumer | None = None
        self.connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_attempts = 0
        self._running = False
        self._consumer_thread: threading.Thread | None = None
        self._message_callbacks: list = []

        # Circuit breaker: evita miles de errores cuando Kafka falla
        self.max_retries = settings.KAFKA_MAX_RETRIES
        self.circuit_breaker_cooldown = settings.KAFKA_CIRCUIT_BREAKER_COOLDOWN
        self._circuit_open = False
        self._circuit_opened_at = None
        self._last_circuit_log_at: float | None = (
            None  # log cada 60s cuando circuito abierto
        )
        # Intervalo (segundos) entre logs cuando el circuit breaker está abierto
        self._circuit_log_interval = 60

    def _topics_to_subscribe(self) -> list[str]:
        """Retorna los topics configurados para consumo sin duplicados."""
        topics: list[str] = []

        if settings.KAFKA_TOPIC:
            topics.append(settings.KAFKA_TOPIC)

        if settings.KAFKA_ALERTS_TOPIC and settings.KAFKA_ALERTS_TOPIC not in topics:
            topics.append(settings.KAFKA_ALERTS_TOPIC)

        return topics

    def _create_consumer(self) -> KafkaConsumer:
        """Crear una nueva instancia del consumidor Kafka."""
        # Configuración básica del consumidor
        consumer_config = {
            "bootstrap_servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "group_id": settings.KAFKA_GROUP_ID,
            "auto_offset_reset": settings.KAFKA_AUTO_OFFSET_RESET,
            "enable_auto_commit": True,
            "auto_commit_interval_ms": 1000,
            "session_timeout_ms": 30000,
            "heartbeat_interval_ms": 3000,
            "consumer_timeout_ms": 1000,  # Timeout para poll()
            "value_deserializer": lambda x: json.loads(x.decode("utf-8")),
        }

        # Agregar autenticación SASL si está configurada
        if settings.KAFKA_USERNAME and settings.KAFKA_PASSWORD:
            consumer_config.update(
                {
                    "security_protocol": settings.KAFKA_SECURITY_PROTOCOL,
                    "sasl_mechanism": settings.KAFKA_SASL_MECHANISM,
                    "sasl_plain_username": settings.KAFKA_USERNAME,
                    "sasl_plain_password": settings.KAFKA_PASSWORD,
                }
            )
            logger.info(
                f"Autenticación SASL Kafka configurada: {settings.KAFKA_SASL_MECHANISM} con protocolo {settings.KAFKA_SECURITY_PROTOCOL}"
            )

        topics = self._topics_to_subscribe()
        if not topics:
            raise ValueError("No hay topics Kafka configurados para consumir")

        logger.info(f"Kafka consumer suscrito a topics: {topics}")
        return KafkaConsumer(*topics, **consumer_config)

    def _handle_consumer_unavailable(self):
        """Maneja el caso cuando el consumer no está disponible."""
        logger.warning("Consumer no disponible, esperando...")
        if self._loop:
            asyncio.run_coroutine_threadsafe(asyncio.sleep(1), self._loop)
        else:
            logger.error("Event loop no disponible")

    def _process_message(self, message):
        """Procesa un mensaje individual de Kafka."""
        try:
            payload = message.value
        except (json.JSONDecodeError, UnicodeDecodeError):
            record_kafka_message(getattr(message, "topic", None), "decode_error")
            logger.error("kafka.decode_error")
            return

        record_kafka_message(getattr(message, "topic", None), "ok")
        try:
            kafka_event = {
                "topic": message.topic,
                "payload": payload,
                "timestamp": message.timestamp,
                "partition": message.partition,
                "offset": message.offset,
            }
            logger.debug(f"Mensaje recibido del topic {message.topic}: {payload}")

            # Llamar callbacks registrados (WebSocket Broker)
            if self._loop and self._message_callbacks:
                for callback in self._message_callbacks:
                    asyncio.run_coroutine_threadsafe(callback(kafka_event), self._loop)

        except Exception as e:
            logger.error(f"Error al procesar mensaje Kafka: {e}")

    def _handle_consumer_error(self, error):
        """Maneja errores del consumer Kafka con circuit breaker."""
        self.connected = False
        if self._circuit_open:
            # Circuito abierto, no intentar reconexión
            cooldown_left = self._circuit_breaker_cooldown_remaining()
            if cooldown_left > 0:
                logger.debug(
                    f"Circuit breaker abierto, cooldown restante: {cooldown_left:.1f}s"
                )
                return
            else:
                # Cooldown terminado, cerrar circuito
                self._circuit_open = False
                self._reconnect_attempts = 0
                logger.warning(
                    "Circuit breaker cerrado, reintentando conexión a Kafka..."
                )

        logger.error(f"Error en el loop de consumo Kafka: {error}")
        self._reconnect_attempts += 1

        if self._reconnect_attempts > self.max_retries:
            if not self._circuit_open:
                self._circuit_open = True
                self._circuit_opened_at = datetime.now(UTC)
                self._last_circuit_log_at = time.monotonic()
                # Cerrar consumer para no seguir haciendo poll() y generar miles de errores
                if self.consumer:
                    with suppress(Exception):
                        self.consumer.close()
                    self.consumer = None
                logger.warning("kafka.circuit_open")
                logger.critical(
                    f"Circuit breaker activado tras {self.max_retries} reintentos fallidos. No se intentará reconectar hasta pasar el cooldown de {self.circuit_breaker_cooldown}s."
                )
            return

        if not self._running:
            return

        logger.info(f"Intentando reconexión... (Intento {self._reconnect_attempts})")
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                asyncio.sleep(min(30, 2**self._reconnect_attempts)), self._loop
            )
            self._reconnect()
        else:
            logger.error("Event loop no disponible para reconexión")

    def _circuit_breaker_cooldown_remaining(self):
        if not self._circuit_opened_at:
            return 0
        elapsed = (datetime.now(UTC) - self._circuit_opened_at).total_seconds()
        return max(0, self.circuit_breaker_cooldown - elapsed)

    def _consume_messages(self):
        """Thread worker para consumir mensajes de Kafka."""
        logger.info(
            f"Iniciando consumo de mensajes de Kafka en topics: {self._topics_to_subscribe()}"
        )

        while self._running:
            try:
                # Circuit breaker abierto: no hacer poll(), dormir y loguear solo cada 60s
                if self._circuit_open:
                    cooldown_left = self._circuit_breaker_cooldown_remaining()
                    if cooldown_left > 0:
                        sleep_secs = min(30, max(5, cooldown_left))
                        now = time.monotonic()
                        if (
                            self._last_circuit_log_at is None
                            or (now - self._last_circuit_log_at)
                            >= self._circuit_log_interval
                        ):
                            logger.warning(
                                "Circuit breaker abierto (Kafka no disponible). "
                                "Reintento en %.0fs. Sin nuevos intentos hasta entonces.",
                                cooldown_left,
                            )
                            self._last_circuit_log_at = now
                        time.sleep(sleep_secs)
                        continue
                    else:
                        # Cooldown terminado, permitir reconexión
                        self._circuit_open = False
                        self._circuit_opened_at = None
                        self._reconnect_attempts = 0
                        logger.warning(
                            "Circuit breaker cerrado, reintentando conexión a Kafka..."
                        )
                        self.consumer = None
                        continue

                if not self.consumer:
                    self._handle_consumer_unavailable()
                    continue

                # Poll por mensajes con timeout
                message_batch = self.consumer.poll(timeout_ms=1000)

                if not message_batch:
                    continue

                # Procesar cada mensaje en el batch
                for _topic_partition, messages in message_batch.items():
                    for message in messages:
                        self._process_message(message)

            except (json.JSONDecodeError, UnicodeDecodeError):
                # Un payload ilegible no es una caída del broker: no abre el
                # circuit breaker ni cuenta como reconexión.
                record_kafka_message(None, "decode_error")
                logger.error("kafka.decode_error")
            except Exception as e:
                self._handle_consumer_error(e)

    def _reconnect(self):
        """Intentar reconectar el consumidor Kafka."""
        was_reconnect = self._reconnect_attempts > 0
        if self._circuit_open:
            cooldown_left = self._circuit_breaker_cooldown_remaining()
            if cooldown_left > 0:
                logger.warning(
                    f"Circuit breaker activo, esperando {cooldown_left:.1f}s antes de reintentar conexión a Kafka."
                )
                return
            else:
                self._circuit_open = False
                self._reconnect_attempts = 0
                logger.warning(
                    "Circuit breaker cerrado, reintentando conexión a Kafka..."
                )
        try:
            if self.consumer:
                self.consumer.close()
                self.consumer = None

            self.consumer = self._create_consumer()
            # Intentar hacer poll para verificar conexión
            self.consumer.poll(timeout_ms=1000)

            self.connected = True
            self._reconnect_attempts = 0
            self._circuit_open = False
            self._circuit_opened_at = None
            if was_reconnect:
                record_kafka_reconnect()
                logger.info("kafka.reconnected")
            else:
                logger.info("kafka.connected")
            logger.info(
                f"Conectado exitosamente a Kafka/Redpanda {settings.KAFKA_BOOTSTRAP_SERVERS}"
            )

        except Exception as e:
            logger.error(f"Error al reconectar consumidor Kafka: {e}")
            self.connected = False

    def circuit_breaker_status(self):
        """Devuelve el estado del circuit breaker para healthcheck y métricas."""
        return {
            "open": self._circuit_open,
            "cooldown_remaining": (
                self._circuit_breaker_cooldown_remaining() if self._circuit_open else 0
            ),
            "retries": self._reconnect_attempts,
            "max_retries": self.max_retries,
        }

    def connect(self):
        """Inicializar y conectar el cliente Kafka."""
        try:
            # Obtener el event loop actual
            self._loop = asyncio.get_event_loop()

            # Crear el consumidor
            self.consumer = self._create_consumer()

            # Marcar como corriendo
            self._running = True

            # Iniciar thread de consumo
            self._consumer_thread = threading.Thread(
                target=self._consume_messages, name="kafka-consumer-thread", daemon=True
            )
            self._consumer_thread.start()

            # Intentar primera conexión
            self._reconnect()

            logger.info("Cliente Kafka inicializado correctamente")

        except Exception as e:
            logger.error(f"Error al inicializar cliente Kafka: {e}")
            self.connected = False
            self._running = False

    def disconnect(self):
        """Desconectar el cliente Kafka."""
        if not self._running:
            return

        logger.info("kafka.disconnected")
        logger.info("Desconectando cliente Kafka...")

        # Detener el loop de consumo
        self._running = False

        # Esperar que el thread termine
        if self._consumer_thread and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=5)

        # Cerrar el consumidor
        if self.consumer:
            try:
                self.consumer.close()
                logger.info("Consumidor Kafka cerrado")
            except Exception as e:
                logger.error(f"Error al cerrar consumidor Kafka: {e}")

        self.connected = False

    def is_connected(self) -> bool:
        """Verificar si el cliente está conectado."""
        return self.connected

    def register_message_callback(self, callback):
        """
        Registra un callback para recibir mensajes Kafka en tiempo real.

        El callback debe ser una coroutine async que acepte un dict (mensaje Kafka).

        Args:
            callback: Coroutine async(message: dict) -> None
        """
        if callback not in self._message_callbacks:
            self._message_callbacks.append(callback)
            logger.info(
                f"Callback registrado. Total callbacks: {len(self._message_callbacks)}"
            )

    def unregister_message_callback(self, callback):
        """
        Desregistra un callback de mensajes Kafka.

        Args:
            callback: Coroutine a desregistrar
        """
        if callback in self._message_callbacks:
            self._message_callbacks.remove(callback)
            logger.info(
                f"Callback desregistrado. Total callbacks: {len(self._message_callbacks)}"
            )


# Instancia global del cliente Kafka
kafka_client = KafkaClient()
