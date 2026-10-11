"""ContextVar de request_id. No loguea body ni headers."""

import re
from contextvars import ContextVar
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import Response

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

# El valor entrante lo elige quien llama, y acaba en los logs y en una cabecera
# de respuesta. Sin acotarlo, un cliente puede escribir lo que quiera en el log
# — saltos de linea incluidos — y engordar cada linea cuanto se le antoje.
MAX_REQUEST_ID = 128
_REQUEST_ID_VALIDO = re.compile(rf"\A[A-Za-z0-9._~:@+-]{{1,{MAX_REQUEST_ID}}}\Z")


def current_request_id() -> str:
    return request_id_ctx.get()


def normalizar_request_id(bruto: str | None) -> str:
    """Devuelve el identificador entrante si es aceptable, o uno nuevo.

    No se recorta ni se limpia el valor del cliente: si no sirve se descarta
    entero. Un identificador a medias se parece demasiado a uno bueno.
    """
    candidato = (bruto or "").strip()
    if candidato and _REQUEST_ID_VALIDO.match(candidato):
        return candidato
    return str(uuid4())


async def request_id_middleware(request: Request, call_next) -> Response:
    incoming = normalizar_request_id(request.headers.get("x-request-id"))
    token = request_id_ctx.set(incoming)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = incoming
        return response
    finally:
        request_id_ctx.reset(token)
