import asyncio
import logging
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.services.kafka_client import kafka_client
from app.utils.metrics import metrics_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Stream"])


class WebSocketManager:
    """Gestor central de suscripciones WebSocket por device_id."""

    def __init__(self):
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.lock = asyncio.Lock()
        self._stats_total_messages = 0
        self._stats_total_subscribers = 0
        self._stats_dropped_messages = 0

    async def subscribe(self, device_ids: list[str]) -> list[asyncio.Queue]:
        """Suscribe una conexión WebSocket a múltiples device_ids."""
        clean_device_ids = [d.strip() for d in device_ids if d and d.strip()]
        # Una cola por socket evita waits innecesarios y simplifica cleanup.
        queue = asyncio.Queue(maxsize=100)

        async with self.lock:
            for dev in clean_device_ids:
                if dev not in self.subscribers:
                    self.subscribers[dev] = set()

                if queue not in self.subscribers[dev]:
                    self.subscribers[dev].add(queue)
                    self._stats_total_subscribers += 1

            logger.info(
                f"WebSocket suscrito a {len(clean_device_ids)} devices. "
                f"Total subscribers activos: {self._stats_total_subscribers}"
            )

        return [queue]

    async def unsubscribe(self, device_ids: list[str], queues: list[asyncio.Queue]):
        """Desuscribe una conexión WebSocket de sus device_ids."""
        if not queues:
            return

        clean_device_ids = [d.strip() for d in device_ids if d and d.strip()]

        async with self.lock:
            for dev in clean_device_ids:
                if dev not in self.subscribers:
                    continue

                for queue in queues:
                    if queue in self.subscribers[dev]:
                        self.subscribers[dev].remove(queue)
                        self._stats_total_subscribers -= 1

                if not self.subscribers[dev]:
                    del self.subscribers[dev]

            logger.info(
                f"WebSocket desuscrito de {len(clean_device_ids)} devices. "
                f"Total subscribers activos: {self._stats_total_subscribers}"
            )

    async def publish(self, message: dict, device_id: str):
        """Publica un mensaje hacia todos los sockets suscritos a un device_id."""
        if not device_id:
            logger.warning(f"Mensaje sin device_id recibido: {message}")
            return

        self._stats_total_messages += 1

        async with self.lock:
            subscribers = self.subscribers.get(device_id)
            if not subscribers:
                return

            dead_queues: list[asyncio.Queue] = []
            for queue in list(subscribers):
                try:
                    if queue.full():
                        self._stats_dropped_messages += 1
                        logger.warning(
                            f"Cola llena para device_id {device_id}. "
                            "Aplicando backpressure (mensaje descartado)"
                        )
                        continue

                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    self._stats_dropped_messages += 1
                    logger.warning(f"Backpressure aplicado para device_id {device_id}")
                except Exception as e:
                    logger.error(f"Error al publicar mensaje: {e}")
                    dead_queues.append(queue)

            # Limpiar colas muertas para evitar referencias colgadas.
            for dead_queue in dead_queues:
                if dead_queue in subscribers:
                    subscribers.remove(dead_queue)
                    self._stats_total_subscribers -= 1

            if not subscribers and device_id in self.subscribers:
                del self.subscribers[device_id]

    def get_stats(self) -> dict:
        """Retorna estadísticas del manager."""
        return {
            "total_messages_processed": self._stats_total_messages,
            "dropped_messages": self._stats_dropped_messages,
            "active_subscribers": self._stats_total_subscribers,
            "devices_being_monitored": len(self.subscribers),
        }


# Compatibilidad con rutas existentes que importan ws_broker.
ws_broker = WebSocketManager()


def _extract_device_id_from_positions(payload: dict) -> str | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return (data.get("device_id") if data is not None else None) or payload.get(
        "device_id"
    )


def _extract_device_id_from_alerts(payload: dict) -> str | None:
    if "device_id" in payload and payload.get("device_id"):
        return payload.get("device_id")

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if data and data.get("device_id"):
        return data.get("device_id")

    inner_payload = payload.get("payload")
    if isinstance(inner_payload, dict):
        return inner_payload.get("device_id")

    return None


def _normalize_alert_message(payload: dict, topic: str | None) -> dict:
    return {
        "message_type": "alert",
        "source_topic": topic,
        "data": payload,
    }


def _resolve_websocket_event_name(payload: dict) -> str:
    """Resuelve el tipo de evento de salida para el cliente WS."""
    if payload.get("message_type") == "alert":
        return "alert"
    return "message"


async def kafka_message_handler(kafka_event: dict):
    """Recibe eventos Kafka y los distribuye por device_id en el manager WS."""
    try:
        topic = kafka_event.get("topic")
        payload = kafka_event.get("payload")

        if not isinstance(payload, dict):
            logger.warning("Evento Kafka ignorado: payload no es dict")
            return

        if topic == settings.KAFKA_TOPIC:
            device_id = _extract_device_id_from_positions(payload)
            ws_message = payload
        elif settings.KAFKA_ALERTS_TOPIC and topic == settings.KAFKA_ALERTS_TOPIC:
            device_id = _extract_device_id_from_alerts(payload)
            ws_message = _normalize_alert_message(payload, topic)
        else:
            logger.debug(f"Topic Kafka no manejado por stream WS: {topic}")
            return

        if not device_id:
            logger.warning(
                f"Mensaje descartado por falta de device_id en topic {topic}: {payload}"
            )
            return

        await ws_broker.publish(ws_message, device_id)
    except Exception as e:
        logger.error(
            f"Error al publicar mensaje al manager WebSocket: {e}", exc_info=True
        )


def start_kafka_broker_bridge():
    """Registra el callback Kafka -> WebSocket."""
    kafka_client.register_message_callback(kafka_message_handler)
    logger.info(
        "✅ Kafka -> WebSocket Manager bridge iniciado "
        f"(topics: {settings.KAFKA_TOPIC}, {settings.KAFKA_ALERTS_TOPIC or 'N/A'})"
    )


async def validate_device_ids(
    websocket: WebSocket, device_ids: str | None
) -> list[str]:
    """Valida y parsea los device_ids del query parameter."""
    raw_list = device_ids.split(",") if device_ids else []
    device_list = [d.strip() for d in raw_list if d and d.strip()]

    if not device_list:
        logger.warning("WebSocket rechazado: no se especificaron device_ids")
        try:
            await websocket.send_json(
                {
                    "event": "error",
                    "data": {
                        "message": "Debe especificar al menos un device_id en los query params",
                        "example": "?device_ids=867564050638581,867564050638582",
                    },
                }
            )
        except Exception as e:
            logger.debug(f"Error al enviar mensaje de error al cliente: {e}")

        await websocket.close(code=1008)
        raise WebSocketDisconnect(code=1008, reason="Missing device_ids")

    return list(dict.fromkeys(device_list))


async def create_keepalive_task(
    websocket: WebSocket, connection_active: asyncio.Event
) -> asyncio.Task:
    """Crea el task de keep-alive para la conexión WebSocket."""

    async def send_keepalive():
        try:
            while connection_active.is_set():
                await asyncio.sleep(settings.WEBSOCKET_KEEPALIVE_SECS)
                if not connection_active.is_set():
                    break
                try:
                    await websocket.send_json(
                        {"event": "ping", "data": {"type": "keep-alive"}}
                    )
                except Exception as e:
                    logger.warning(f"Error al enviar keep-alive: {e}")
                    break
        except asyncio.CancelledError:
            logger.debug("Task de keep-alive cancelado")
            raise
        except Exception as e:
            logger.error(f"Error inesperado en keep-alive: {e}")

    return asyncio.create_task(send_keepalive())


async def process_websocket_messages(
    websocket: WebSocket, queues: list[asyncio.Queue]
) -> None:
    """Consume la cola del socket y envía eventos al cliente.

    Lee el WebSocket en paralelo (`websocket.receive()`) para detectar la
    desconexión del cliente de inmediato aunque no fluyan mensajes. Sin esto,
    un cliente que se cae solo se detectaba al fallar un `send_json`, que de
    noche con vehículos parados nunca ocurre → la suscripción quedaba huérfana
    (fuga de subscribers zombie).
    """
    # Tarea persistente que espera datos entrantes / el frame de cierre.
    receive_task = asyncio.ensure_future(websocket.receive())
    try:
        while True:
            queue_tasks = [asyncio.create_task(q.get()) for q in queues]

            # Sin timeout: el keep-alive corre en otra task, así que aquí solo
            # despertamos cuando hay un mensaje de cola o el cliente se
            # desconecta (receive_task). Evita churn periódico con muchas conns.
            done, pending = await asyncio.wait(
                [receive_task, *queue_tasks],
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancelar solo las lecturas de cola pendientes; nunca receive_task.
            for task in pending:
                if task is not receive_task:
                    task.cancel()

            # ¿Cliente desconectado o envió un frame? Detectarlo de inmediato.
            # Guardamos la referencia a la task completada ANTES de re-armar,
            # para excluirla del envío de eventos más abajo.
            completed_receive = receive_task if receive_task in done else None
            if completed_receive is not None:
                try:
                    message = completed_receive.result()
                except WebSocketDisconnect:
                    raise
                except Exception as recv_error:
                    # Cualquier fallo de lectura se trata como desconexión, no
                    # como error crítico (el handler lo loguearía como 💥).
                    raise WebSocketDisconnect(
                        code=1006, reason="Receive failed"
                    ) from recv_error

                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect(
                        code=message.get("code", 1000),
                        reason="Client disconnected",
                    )
                # Frame entrante: este endpoint no espera datos, se ignora y
                # se re-arma la lectura para seguir vigilando la conexión.
                receive_task = asyncio.ensure_future(websocket.receive())

            for task in done:
                if task is completed_receive:
                    continue
                try:
                    event = task.result()
                    event_name = (
                        _resolve_websocket_event_name(event)
                        if isinstance(event, dict)
                        else "message"
                    )
                    await websocket.send_json({"event": event_name, "data": event})
                except Exception as send_error:
                    logger.warning(
                        f"Error al enviar mensaje WebSocket (conexión cerrada): {send_error}"
                    )
                    raise WebSocketDisconnect(
                        code=1000, reason="Connection closed"
                    ) from send_error
    finally:
        receive_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await receive_task


async def cleanup_websocket_connection(
    keepalive_task: asyncio.Task,
    device_list: list[str],
    queues: list[asyncio.Queue],
    connection_active: asyncio.Event,
) -> None:
    """Limpia recursos de la conexión WebSocket."""
    connection_active.clear()

    keepalive_task.cancel()
    try:
        await keepalive_task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Error al cancelar keepalive task: {e}")

    await ws_broker.unsubscribe(device_list, queues)
    await metrics_client.decrement_active_connections()


@router.websocket("/stream")
async def websocket_stream(websocket: WebSocket, device_ids: str | None = None):
    """Endpoint WebSocket para recibir eventos de dispositivos en tiempo real."""
    await websocket.accept()
    await metrics_client.increment_active_connections()

    try:
        device_list = await validate_device_ids(websocket, device_ids)
        queues = await ws_broker.subscribe(device_list)

        logger.info(
            "✅ WebSocket conectado exitosamente - "
            f"Device IDs: {device_list} - Cliente: {websocket.client}"
        )

        connection_active = asyncio.Event()
        connection_active.set()

        keepalive_task = await create_keepalive_task(websocket, connection_active)
        await process_websocket_messages(websocket, queues)

    except WebSocketDisconnect as disconnect_error:
        logger.info(
            "📴 WebSocket desconectado normalmente - "
            f"Device IDs: {device_list if 'device_list' in locals() else 'N/A'} - "
            f"Código: {disconnect_error.code} - Cliente: {websocket.client}"
        )
    except Exception as e:
        logger.error(
            "💥 Error crítico en WebSocket - "
            f"Device IDs: {device_list if 'device_list' in locals() else 'N/A'} - "
            f"Cliente: {websocket.client} - Error: {e}",
            exc_info=True,
        )
    finally:
        if "keepalive_task" in locals():
            await cleanup_websocket_connection(
                keepalive_task,
                device_list if "device_list" in locals() else [],
                queues if "queues" in locals() else [],
                (
                    connection_active
                    if "connection_active" in locals()
                    else asyncio.Event()
                ),
            )

        with suppress(Exception):
            await websocket.close()


@router.get("/stream/stats")
async def get_broker_stats():
    """Obtiene estadísticas en tiempo real del WebSocket manager."""
    return ws_broker.get_stats()
