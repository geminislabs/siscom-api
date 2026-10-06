# Guía de Configuración - Variables de Entorno

Esta guía documenta todas las variables de entorno que puedes configurar en tu archivo `.env`.

## Información de la Aplicación

```env
APP_NAME=siscom-api
APP_VERSION=0.1.0
```

## Base de Datos PostgreSQL

```env
DB_HOST=localhost
DB_PORT=5432
DB_USERNAME=tu_usuario
DB_PASSWORD=tu_password
DB_DATABASE=tu_database
DB_MIN_CONNECTIONS=10
DB_MAX_CONNECTIONS=20
DB_CONNECTION_TIMEOUT_SECS=30
DB_IDLE_TIMEOUT_SECS=300
```

## CORS (Cross-Origin Resource Sharing)

```env
# Permitir todos los orígenes (desarrollo)
ALLOWED_ORIGINS=*

# Permitir orígenes específicos (producción)
ALLOWED_ORIGINS=https://tudominio.com,https://app.tudominio.com
```

## Kafka/Redpanda Configuration

```env
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=tracking/data
KAFKA_GROUP_ID=siscom-api-consumer
KAFKA_AUTO_OFFSET_RESET=latest
KAFKA_USERNAME=tu_usuario_kafka
KAFKA_PASSWORD=tu_password_kafka
KAFKA_SASL_MECHANISM=SCRAM-SHA-256
KAFKA_SECURITY_PROTOCOL=SASL_PLAINTEXT

# Circuit Breaker / Resiliencia
KAFKA_MAX_RETRIES=5
KAFKA_CIRCUIT_BREAKER_COOLDOWN=300
```

### Notas sobre Kafka/Redpanda:

- `KAFKA_BOOTSTRAP_SERVERS`: Lista de servidores bootstrap separados por comas (ej: `host1:9092,host2:9092`)
- `KAFKA_TOPIC`: Topic al que te suscribirás
- `KAFKA_GROUP_ID`: Identificador del consumer group
- `KAFKA_AUTO_OFFSET_RESET`: Posición inicial de lectura (`latest` o `earliest`)
- Las credenciales son **opcionales** si tu cluster no requiere autenticación
- `KAFKA_SASL_MECHANISM`: Mecanismo de autenticación SASL (ej: SCRAM-SHA-256, PLAIN)
- `KAFKA_SECURITY_PROTOCOL`: Protocolo de seguridad (SASL_PLAINTEXT, SASL_SSL, etc.)

### Circuit Breaker:

- `KAFKA_MAX_RETRIES`: Número máximo de reintentos antes de abrir el circuito (default: 5)
- `KAFKA_CIRCUIT_BREAKER_COOLDOWN`: Tiempo en segundos que el circuito permanece abierto antes de reintentar (default: 300)
- El circuit breaker previene loops infinitos de reconexión cuando Kafka no está disponible
- Durante el cooldown, no se intentan reconexiones, reduciendo spam de logs
- El estado del circuito se expone en `/health` y como métrica

## StatsD/Telegraf (Métricas - OPCIONAL)

```env
# Deshabilitar métricas (por defecto, sin errores)
STATSD_ENABLED=false

# Habilitar métricas (solo si tienes Telegraf/StatsD corriendo)
STATSD_ENABLED=true
STATSD_HOST=localhost
STATSD_PORT=8126
STATSD_PREFIX=siscom_api
```

### ¿Cuándo habilitar StatsD?

- **`STATSD_ENABLED=false`** (Recomendado para empezar):
  - No verás errores de conexión
  - La aplicación funcionará sin problemas
  - No tendrás métricas en Grafana

- **`STATSD_ENABLED=true`** (Solo si tienes el servidor):
  - Necesitas Telegraf/StatsD corriendo en el puerto configurado
  - Obtendrás métricas de latencia, requests, conexiones SSE, etc.
  - Puedes visualizarlas en Grafana

### Instalar Telegraf (Docker)

Si quieres habilitar métricas:

```bash
docker run -d \
  --name telegraf \
  --restart always \
  -p 8126:8126/udp \
  -v $PWD/telegraf-siscom-addon.conf:/etc/telegraf/telegraf.conf:ro \
  telegraf
```

Luego cambia `STATSD_ENABLED=true` en tu `.env`.

## Ejemplo Completo

Crea un archivo `.env` en la raíz del proyecto con este contenido:

```env
# Aplicación
APP_NAME=siscom-api
APP_VERSION=0.1.0

# Base de Datos
DB_HOST=localhost
DB_PORT=5432
DB_USERNAME=mi_usuario
DB_PASSWORD=mi_password_seguro
DB_DATABASE=siscom_db
DB_MIN_CONNECTIONS=10
DB_MAX_CONNECTIONS=20
DB_CONNECTION_TIMEOUT_SECS=30
DB_IDLE_TIMEOUT_SECS=300

# CORS
ALLOWED_ORIGINS=*

# StatsD (Deshabilitar por ahora)
STATSD_ENABLED=false
STATSD_HOST=localhost
STATSD_PORT=8126
STATSD_PREFIX=siscom_api

# Kafka/Redpanda
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=tracking/data
KAFKA_GROUP_ID=siscom-api-consumer
KAFKA_AUTO_OFFSET_RESET=latest
KAFKA_USERNAME=tu_usuario_kafka
KAFKA_PASSWORD=tu_password_kafka
```

## Valores por Defecto

Si no especificas alguna variable en `.env`, se usarán estos valores por defecto:

| Variable                      | Valor por Defecto |
| ----------------------------- | ----------------- |
| `STATSD_ENABLED`              | `false`           |
| `STATSD_HOST`                 | `localhost`       |
| `STATSD_PORT`                 | `8126`            |
| `STATSD_PREFIX`               | `siscom_api`      |
| `KAFKA_BOOTSTRAP_SERVERS`     | `localhost:9092`  |
| `KAFKA_TOPIC`                  | `tracking/data`   |
| `KAFKA_GROUP_ID`               | `siscom-api-consumer` |
| `KAFKA_AUTO_OFFSET_RESET`      | `latest`          |
| `ALLOWED_ORIGINS`             | `*`               |

## Seguridad

🔒 **NUNCA subas tu archivo `.env` a Git**

Asegúrate de que `.env` esté en tu `.gitignore`:

```bash
echo ".env" >> .gitignore
```
