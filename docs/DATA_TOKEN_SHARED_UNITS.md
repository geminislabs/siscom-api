# Data token: unidades compartidas y revocación inmediata (contrato v1.4)

> Historia: **NEXUS-S2-03**. Contraparte emisora: `siscom-admin-api`.
> Este servicio solo **lee** `dt:scope:*` en Valkey; nunca escribe ni firma.

## Resumen

| Propiedad | Comportamiento en siscom-api |
| --------- | ---------------------------- |
| Unidad compartida por otro equipo | Solo telemetría (posiciones, histórico, última posición), y solo dentro de su ventana |
| Eventos / alertas de una unidad compartida | `403 Not available for shared units` (HTTP); no salen por WebSocket |
| Campos de alerta en la telemetría | `alert_type` sale como `null` (HTTP) o se elimina de la trama (WS) |
| Comandos (apagado de motor) | No existen en este servicio |
| Pausa / revocación / vencimiento del permiso | Rechazo en la **siguiente petición** HTTP; WS abierto cerrado en ≤ `SCOPE_REVALIDATE_SECS` |

Todo esto solo cambia respuestas con `DATA_TOKEN_ENFORCED=true`. En modo
observación (`false`) se verifica y registra, pero ninguna respuesta cambia.

## Contrato con admin-api

### 1. Nivel de acceso en el valor del hash

El valor de cada campo de `dt:scope:<scope_ref>:<kind>` admite desde v1.4 el
campo `access`:

```json
{"id": "<id interno>", "windows": [{"from": "2026-11-01T00:00:00Z", "to": "2026-11-30T00:00:00Z"}], "access": "shared"}
```

| `access` | Significado |
| -------- | ----------- |
| ausente o `"full"` | Unidad propia del alcance; acceso completo (igual que v1.3) |
| `"shared"` | Unidad compartida por otro equipo; solo telemetría. `windows` debe ser la vigencia del permiso |
| otro valor | **Denegado**. Un nivel desconocido nunca degrada a "completo" |

Los valores sin migrar (identificador a secas, o JSON sin `access`) siguen
funcionando como antes.

### 2. Marca de revocación

Ante una **pausa, revocación o vencimiento** de un permiso compartido,
admin-api debe:

1. Escribir `dt:scope:<scope_ref>:revoked` (cualquier valor) con
   **TTL ≥ vida máxima del data token**.
2. Borrar o reescribir los hashes del alcance.

siscom-api consulta la marca con `EXISTS`, **sin caché**:

- en cada petición HTTP con data token;
- en el handshake de cada WebSocket (`/stream` y compartir ubicación);
- periódicamente en cada WebSocket abierto (`SCOPE_REVALIDATE_SECS`, 10 s por
  defecto; en compartir ubicación, en cada ciclo del keep-alive).

Si la marca existe, también se purga la caché de pertenencia del alcance.
El cliente debe pedir un token nuevo, que saldrá con el alcance recalculado.

## Respuestas

| Situación | HTTP | WebSocket |
| --------- | ---- | --------- |
| Token revocado | `401 Data token revoked` | cierre `1008 Data token revoked` |
| Token vencido con la conexión abierta | — | cierre `1008 Data token expired` |
| Enlace de compartir revocado (`/init`) | `403 Invalid token` | evento `revoked` + cierre `1008 Link revoked` |
| Eventos/alertas de unidad compartida | `403 Not available for shared units` | trama descartada |
| `GET /communications/.../latest?class=ALERT` de unidad compartida | `403` | — |
| Fuera de la ventana del permiso | igual que v1.3 (`403`/`404`) | trama descartada |
| Valkey inaccesible | `503 Authorization store unavailable` | cierre `1013` (compartir ubicación: cierre) |

Fail closed en todos los casos: si no se puede comprobar la revocación, no se
sirve.

## Configuración

| Variable | Defecto | Uso |
| -------- | ------- | --- |
| `DATA_TOKEN_ENFORCED` | `false` | Activa la exigencia (y por tanto todo lo anterior) |
| `SCOPE_CACHE_TTL_SECS` | `30` | Caché de pertenencia; **no** afecta a la revocación |
| `SCOPE_REVALIDATE_SECS` | `10` | Techo de latencia de la revocación sobre un WebSocket abierto |

## Coste

Un `EXISTS` adicional a Valkey por petición HTTP autorizada y uno cada
`SCOPE_REVALIDATE_SECS` por WebSocket abierto.

## Tests

- `test/test_shared_units.py` — parseo de `access`, 403 en eventos/alertas,
  redacción de campos, filtrado de tramas, revocación HTTP/WS, fail closed.
- `test/test_data_token_endpoints.py`, `test/test_share_location_tokens.py` —
  revocación en endpoints y enlaces de compartir.
