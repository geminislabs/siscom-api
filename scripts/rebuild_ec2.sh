#!/bin/bash
# Script para limpiar y rebuilder siscom-api en EC2
# Uso: ./rebuild_ec2.sh

set -e  # Salir si hay errores

echo "🔧 Iniciando rebuild de siscom-api..."
echo ""

# Ir al directorio del proyecto
cd ~/siscom-api

echo "1️⃣ Verificando requirements.txt..."
if grep -q "aio-statsd" requirements.txt; then
    echo "✅ aio-statsd encontrado en requirements.txt"
else
    echo "❌ ERROR: aio-statsd NO está en requirements.txt"
    echo "   Ejecuta 'git pull' primero para actualizar los archivos"
    exit 1
fi
echo ""

echo "2️⃣ Deteniendo contenedor actual..."
docker-compose down
echo ""

echo "3️⃣ Eliminando imagen siscom-api:latest..."
docker rmi siscom-api:latest || echo "   (imagen no encontrada, continuando...)"
echo ""

echo "4️⃣ Limpiando imágenes dangling..."
docker image prune -f
echo ""

echo "5️⃣ Reconstruyendo imagen SIN caché..."
docker-compose build --no-cache
echo ""

echo "6️⃣ Levantando servicio..."
docker-compose up -d
echo ""

echo "7️⃣ Esperando que el contenedor esté saludable..."
sleep 5
echo ""

echo "8️⃣ Verificando logs..."
docker logs siscom-api --tail 30
echo ""

echo "9️⃣ Estado del contenedor:"
docker ps | grep siscom-api
echo ""

echo "✅ Rebuild completado!"
echo ""
echo "Para verificar que aio-statsd está instalado:"
echo "  docker exec siscom-api pip list | grep aio-statsd"

