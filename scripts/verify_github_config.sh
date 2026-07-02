#!/bin/bash

# Verifica qué Variables y Secrets de GitHub Actions deben configurarse para siscom-api.
# Ver también: .github/GITHUB_SETUP.md

echo "🔍 Verificación de Configuración de GitHub Actions — siscom-api"
echo "==============================================================="
echo ""
echo "Este script lista variables y secrets requeridos para deploy."
echo "Consulta .github/GITHUB_SETUP.md para valores y ejemplos."
echo ""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Variables (Settings → Actions → Variables) ===${NC}"
echo ""

VARIABLES=(
    "DB_HOST:tu-rds-endpoint.amazonaws.com"
    "DB_PORT:5432"
    "DB_USERNAME:postgres"
    "DB_DATABASE:siscom"
    "DB_MIN_CONNECTIONS:10"
    "DB_MAX_CONNECTIONS:20"
    "DB_CONNECTION_TIMEOUT_SECS:30"
    "DB_IDLE_TIMEOUT_SECS:300"
)

for var_info in "${VARIABLES[@]}"; do
    IFS=':' read -r var_name var_example <<< "$var_info"
    echo -e "  ${YELLOW}✓${NC} $var_name"
    echo -e "    Ejemplo: ${GREEN}$var_example${NC}"
done

echo ""
echo -e "${BLUE}=== Secrets (Settings → Actions → Secrets) ===${NC}"
echo ""

SECRETS=(
    "DB_PASSWORD:Contraseña de PostgreSQL"
    "EC2_HOST:IP o hostname del servidor EC2"
    "EC2_USERNAME:Usuario SSH (ubuntu, ec2-user, etc.)"
    "EC2_SSH_KEY:Clave privada SSH completa"
    "EC2_SSH_PORT:Puerto SSH (generalmente 22)"
    "JWT_SECRET_KEY:Clave secreta JWT (mín. 32 caracteres)"
    "PASETO_SECRET_KEY:Clave PASETO v4.local en base64"
)

for secret_info in "${SECRETS[@]}"; do
    IFS=':' read -r secret_name secret_desc <<< "$secret_info"
    echo -e "  ${YELLOW}✓${NC} $secret_name"
    echo -e "    ${secret_desc}"
done

echo ""
echo -e "${BLUE}=== Environment ===${NC}"
echo "Crear environment 'test' (usado por .github/workflows/deploy.yml)"
echo "Ruta: Settings → Environments → New environment"
echo ""

echo -e "${GREEN}=== Checklist ===${NC}"
echo ""
echo "Variables:"
for var_info in "${VARIABLES[@]}"; do
    IFS=':' read -r var_name _ <<< "$var_info"
    echo "  [ ] $var_name"
done
echo ""
echo "Secrets:"
for secret_info in "${SECRETS[@]}"; do
    IFS=':' read -r secret_name _ <<< "$secret_info"
    echo "  [ ] $secret_name"
done
echo ""
echo "Environment:"
echo "  [ ] test"
echo ""
echo -e "${YELLOW}=================================================${NC}"
echo "Documentación: .github/GITHUB_SETUP.md"
