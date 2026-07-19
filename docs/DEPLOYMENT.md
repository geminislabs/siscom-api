# Guía de Despliegue - SISCOM API

Esta guía cubre el despliegue de SISCOM API v1 (REST) en EC2 usando GitHub Actions.

**Nota:** Esta API usa endpoints REST v1 con versionamiento `/api/v1/`. Ver [API_REST_GUIDE.md](API_REST_GUIDE.md) para documentación de endpoints.

## Requisitos Previos

1. **Servidor EC2** configurado con:
   - Docker y Docker Compose instalados
   - Acceso SSH configurado
   - Red Docker `siscom-network` (se crea automáticamente si no existe)

2. **Secrets de GitHub** configurados en tu repositorio:
   - `DB_PASSWORD`: Contraseña de la base de datos
   - `EC2_HOST`: IP o dominio del servidor EC2
   - `EC2_SSH_KEY`: Clave privada SSH para acceso al EC2
   - `EC2_SSH_PORT`: Puerto SSH del EC2 (usualmente 22)
   - `EC2_USERNAME`: Usuario para SSH (ej: ubuntu, ec2-user)
   - `JWT_SECRET_KEY`: Clave secreta para JWT
   - `PASETO_SECRET_KEY`: Clave secreta para PASETO
   - `KAFKA_USERNAME`: Usuario de Kafka (si usa autenticación)
   - `KAFKA_PASSWORD`: Contraseña de Kafka (si usa autenticación)
   - `DOCKER_USERNAME`: (Opcional) Usuario de Docker Hub
   - `DOCKER_PASSWORD`: (Opcional) Token de Docker Hub

3. **Variables de GitHub** configuradas:
   - `DB_CONNECTION_TIMEOUT_SECS`: Timeout de conexión a DB (ej: 30)
   - `DB_DATABASE`: Nombre de la base de datos
   - `DB_HOST`: Host de la base de datos
   - `DB_IDLE_TIMEOUT_SECS`: Timeout de idle (ej: 300)
   - `DB_MAX_CONNECTIONS`: Conexiones máximas (ej: 20)
   - `DB_MIN_CONNECTIONS`: Conexiones mínimas (ej: 10)
   - `DB_PORT`: Puerto de la base de datos (ej: 5432)
   - `DB_USERNAME`: Usuario de la base de datos
   - `KAFKA_BOOTSTRAP_SERVERS`: Servidores bootstrap de Kafka (ej: localhost:9092)
   - `KAFKA_TOPIC`: Topic de Kafka (ej: siscom-minimal)
   - `KAFKA_ALERTS_TOPIC`: Topic de alertas (ej: tracking/alerts, opcional)
   - `KAFKA_GROUP_ID`: Group ID del consumidor (ej: siscom-api-consumer)
   - `KAFKA_AUTO_OFFSET_RESET`: Offset reset (latest o earliest)
   - `KAFKA_SASL_MECHANISM`: Mecanismo SASL (ej: SCRAM-SHA-256)
   - `KAFKA_SECURITY_PROTOCOL`: Protocolo de seguridad (ej: SASL_PLAINTEXT)
   - `STATSD_HOST`: Host de StatsD/Telegraf (opcional)
   - `STATSD_PORT`: Puerto de StatsD/Telegraf (opcional)
   - `STATSD_PREFIX`: Prefijo de métricas (opcional)

## Configuración de Secrets en GitHub

### Paso 1: Navega a tu repositorio en GitHub

### Paso 2: Ve a Settings > Secrets and variables > Actions

### Paso 3: Agrega los Secrets

```bash
# Secrets
DB_PASSWORD=tu_password_seguro
EC2_HOST=tu-servidor.ejemplo.com
EC2_SSH_KEY=-----BEGIN_[TIPO_DE_CLAVE]_PRIVATE_KEY-----\n<CONTENIDO_REAL_DE_TU_CLAVE_PRIVADA>\n-----END_[TIPO_DE_CLAVE]_PRIVATE_KEY-----
EC2_SSH_PORT=22
EC2_USERNAME=ubuntu
JWT_SECRET_KEY=tu_jwt_secret_muy_seguro_y_largo
```

### Paso 4: Agrega las Variables (pestaña "Variables")

```bash
DB_CONNECTION_TIMEOUT_SECS=30
DB_DATABASE=siscom
DB_HOST=tu-db-host.rds.amazonaws.com
DB_IDLE_TIMEOUT_SECS=300
DB_MAX_CONNECTIONS=20
DB_MIN_CONNECTIONS=10
DB_PORT=5432
DB_USERNAME=postgres
```

## Despliegue Manual en EC2

Si necesitas desplegar manualmente:

### 1. Conéctate a tu EC2

```bash
ssh -i tu-clave.pem usuario@tu-ec2-host
```

### 2. Clona el repositorio

```bash
git clone https://github.com/tu-usuario/siscom-api.git
cd siscom-api
```

### 3. Crea el archivo .env

```bash
cat > .env << EOF
DB_HOST=tu-db-host
DB_PORT=5432
DB_USERNAME=postgres
DB_PASSWORD=tu_password
DB_DATABASE=siscom
DB_MIN_CONNECTIONS=10
DB_MAX_CONNECTIONS=20
DB_CONNECTION_TIMEOUT_SECS=30
DB_IDLE_TIMEOUT_SECS=300
JWT_SECRET_KEY=tu_jwt_secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
ALLOWED_ORIGINS=*
EOF
```

### 4. Crea la red Docker

```bash
docker network create siscom-network
```

### 5. Construye y levanta el contenedor

```bash
docker build -t siscom-api:latest .
docker compose up -d
```

### 6. Verifica el estado

```bash
docker ps
docker logs siscom-api
curl http://localhost:8000/health
```

## Despliegue Automático

El despliegue automático se ejecuta cuando:

- Haces push a la rama `main` o `production`
- Ejecutas manualmente el workflow desde GitHub Actions

### Proceso Automático

1. ✅ Build de la imagen Docker
2. ✅ Compresión y transferencia al EC2
3. ✅ Carga de la imagen en el servidor
4. ✅ Creación de la red siscom-network (si no existe)
5. ✅ Detención del contenedor anterior
6. ✅ Inicio del nuevo contenedor
7. ✅ Verificación de salud del contenedor
8. ✅ Limpieza de imágenes antiguas

## Monitoreo

### Ver logs del contenedor

```bash
docker logs -f siscom-api
```

### Ver estado del contenedor

```bash
docker ps
docker inspect siscom-api
```

### Health check

```bash
curl http://localhost:8000/health
```

### Ver recursos

```bash
docker stats siscom-api
```

## Rollback

Si algo sale mal, puedes hacer rollback:

```bash
# Ver imágenes disponibles
docker images | grep siscom-api

# Detener contenedor actual
docker compose down

# Cambiar a versión anterior
docker tag siscom-api:VERSION_ANTERIOR siscom-api:latest

# Levantar con versión anterior
docker compose up -d
```

## Troubleshooting

### El contenedor no inicia

```bash
docker logs siscom-api
```

### Problemas de conexión a la DB

- Verifica que las variables de entorno estén correctas
- Verifica que el host de la DB sea accesible desde el EC2
- Revisa los security groups en AWS

### El health check falla

- Verifica que el puerto 8000 esté expuesto
- Verifica que la aplicación esté respondiendo

### Problemas de red

```bash
# Verificar que la red exista
docker network ls | grep siscom-network

# Recrear la red si es necesario
docker network rm siscom-network
docker network create siscom-network
```

## Seguridad

- ✅ El contenedor corre con un usuario no privilegiado
- ✅ Las credenciales se manejan como secrets de GitHub
- ✅ La imagen usa multi-stage build para reducir tamaño
- ✅ Se implementa health check
- ✅ Pool de conexiones configurado para optimizar recursos
- ⚠️ Cambia JWT_SECRET_KEY en producción
- ⚠️ Configura ALLOWED_ORIGINS apropiadamente en producción
- ⚠️ Considera usar un registry privado para las imágenes
