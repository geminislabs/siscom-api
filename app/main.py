import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.routes import communications, events, public, stream
from app.api.routes.stream import start_kafka_broker_bridge
from app.core.config import settings
from app.core.database import engine
from app.core.middleware import MetricsMiddleware
from app.observability.init import instrument_app, setup_telemetry
from app.observability.logging import configure_json_logging
from app.observability.metrics import record_http_server_duration
from app.observability.request_id import request_id_middleware
from app.services.kafka_client import kafka_client
from app.services.scope_store import scope_store
from app.utils.metrics import metrics_client

# JSON a stdout aunque OTLP_ENDPOINT esté vacío. El filtro de /health vive
# dentro de configure_json_logging.
configure_json_logging(settings)
setup_telemetry(settings)
logger = logging.getLogger(__name__)

# Asegurar que el middleware tenga logging activado
logging.getLogger("app.core.middleware").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Manejo del ciclo de vida de la aplicación."""
    # Startup: Verificar conexión a la base de datos.
    # El detalle de la excepción no se loguea: el str() de SQLAlchemy trae
    # usuario y host de la conexión.
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            logger.info("db.startup", extra={"outcome": "ok"})
    except Exception:
        logger.error("db.startup", extra={"outcome": "error"})

    # Startup: Conectar cliente de métricas
    await metrics_client.ensure_connected()

    # Startup: Conectar cliente Kafka
    try:
        kafka_client.connect()
        logger.info("Cliente Kafka inicializado")
    except Exception as e:
        logger.error(f"Error al inicializar cliente Kafka: {e}")

    # Startup: Iniciar bridge Kafka → WebSocket Broker (alta performance)
    try:
        start_kafka_broker_bridge()
        logger.info("✅ Bridge Kafka → WebSocket activo")
    except Exception as e:
        logger.error(f"Error al iniciar Kafka bridge: {e}")

    # Tarea periódica para reportar el estado del circuit breaker de Kafka como métrica
    async def report_kafka_circuit_breaker():
        while True:
            await metrics_client.kafka_circuit_breaker_gauge(
                kafka_client.circuit_breaker_status()["open"]
            )
            await asyncio.sleep(10)

    task = None
    if settings.STATSD_ENABLED:
        task = asyncio.create_task(report_kafka_circuit_breaker())

    yield

    if task:
        task.cancel()
        with contextlib.suppress(Exception):
            await task

    # Shutdown: Cerrar cliente Kafka
    try:
        kafka_client.disconnect()
        logger.info("Cliente Kafka desconectado")
    except Exception as e:
        logger.error(f"Error al desconectar cliente Kafka: {e}")

    # Shutdown: Cerrar cliente de métricas
    await metrics_client.close()

    # Shutdown: Cerrar cliente de Valkey
    with contextlib.suppress(Exception):
        await scope_store.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/api/docs" if settings.ENABLE_API_DOCS else None,
    redoc_url="/api/redoc" if settings.ENABLE_API_DOCS else None,
    openapi_url="/api/openapi.json" if settings.ENABLE_API_DOCS else None,
    lifespan=lifespan,
)

# Configurar middleware de métricas
app.add_middleware(MetricsMiddleware)

# Configurar CORS
#
# `allow_credentials=True` junto a un origen comodín es una combinación
# insegura: Starlette responde el origen explícito en el preflight, con lo que
# CUALQUIER sitio pasa la comprobación previa contra todos los endpoints. Por
# eso las credenciales solo se habilitan cuando hay una lista explícita de
# orígenes; con "*" se degradan a peticiones sin credenciales.
_allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
_allow_all_origins = "*" in _allowed_origins

if _allow_all_origins:
    logger.warning(
        "⚠️  ALLOWED_ORIGINS='*': CORS abierto a cualquier origen y credenciales "
        "deshabilitadas. Configura la lista de orígenes en producción."
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=not _allow_all_origins,
    # Toda la superficie de la API es de solo lectura (GET).
    allow_methods=["GET"],
    # traceparent/tracestate/x-request-id para continuar la traza W3C.
    # No se abre ningún otro método ni origen.
    allow_headers=[
        "Authorization",
        "Content-Type",
        "traceparent",
        "tracestate",
        "x-request-id",
    ],
    expose_headers=["X-Request-ID"],
)


def _plantilla_de_ruta(request: Request) -> str:
    """La plantilla, no el path: `/devices/{device_id}/...` y nunca el id."""
    ruta = request.scope.get("route")
    plantilla = getattr(ruta, "path", None)
    if isinstance(plantilla, str) and plantilla:
        return plantilla
    return "unmatched"


def _excluir_de_observabilidad_http(request: Request, route: str) -> bool:
    path = request.url.path.split("?", 1)[0]
    return route == "/health" or path == "/health"


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    return await request_id_middleware(request, call_next)


@app.middleware("http")
async def record_http_duration(request: Request, call_next):
    """Histograma RED al salir. /health no entra ni al histograma ni a las trazas."""
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = getattr(response, "status_code", 500)
        return response
    finally:
        route = _plantilla_de_ruta(request)
        if not _excluir_de_observabilidad_http(request, route):
            duration_ms = (time.perf_counter() - started) * 1000
            record_http_server_duration(route, request.method, status_code, duration_ms)


# Lo ultimo que se registra es lo mas externo. Orden, de fuera hacia dentro:
# traza OTel -> histograma RED -> request_id -> CORS -> StatsD
instrument_app(app)


# Health check endpoint


@app.get("/health", tags=["Health"])
async def health_check():
    kafka_status = kafka_client.circuit_breaker_status()
    return {
        "status": "healthy" if not kafka_status["open"] else "degraded",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.DEPLOY_ENV,
        "kafka_circuit_breaker": kafka_status,
    }


# Incluir routers API v1
app.include_router(communications.router)
app.include_router(events.router)
app.include_router(stream.router)

# Incluir routers públicos
app.include_router(public.router)
