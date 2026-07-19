.PHONY: help install lint format format-check test test-cov build run run-dev stop clean deploy-test validate scan-secrets audit-deps scan-osv all-checks health logs shell db-shell dev docker-build docker-up docker-down

help:  ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Instala las dependencias
	pip install -r requirements.txt

lint:  ## Ejecuta el linter (Ruff)
	ruff check app/ test/

format:  ## Formatea el código con Black
	black app/ test/

format-check:  ## Verifica el formato sin modificar archivos
	black --check app/ test/

test:  ## Ejecuta los tests
	pytest test/ -v -m "not slow"

test-cov:  ## Ejecuta los tests con coverage (umbrales en pyproject.toml)
	pytest test/ -v -m "not slow" --cov=app --cov-report=term-missing --cov-report=html

build:  ## Construye la imagen Docker
	docker build -t siscom-api:latest .

run:  ## Ejecuta el contenedor localmente
	docker network create siscom-network 2>/dev/null || true
	docker-compose up -d

run-dev:  ## Ejecuta en modo desarrollo con hot-reload
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --http h11

stop:  ## Detiene los contenedores
	docker-compose down

logs:  ## Muestra los logs del contenedor
	docker-compose logs -f siscom-api

clean:  ## Limpia archivos temporales y caché
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	rm -rf htmlcov/
	rm -rf .coverage

deploy-test:  ## Prueba el deployment localmente
	docker network create siscom-network 2>/dev/null || true
	docker-compose up -d

deploy-test-logs:  ## Muestra los logs del deployment de prueba
	docker logs -f siscom-api

deploy-test-stop:  ## Detiene el deployment de prueba
	docker-compose down

health:  ## Verifica el health check
	@echo "Verificando health check..."
	@curl -f http://localhost:8000/health && echo "\n✅ Health check OK" || echo "\n❌ Health check FAILED"

shell:  ## Abre una shell en el contenedor
	docker exec -it siscom-api /bin/bash

db-shell:  ## Abre una shell de PostgreSQL (docker-compose.test.yml)
	docker exec -it siscom-api-test-db psql -U test -d siscom_test

all-checks: format-check lint test  ## Ejecuta todas las verificaciones (formato, lint, tests)
	@echo "✅ Todas las verificaciones pasaron correctamente"

validate: format-check lint test build  ## Pipeline local equivalente a CI quality
	@echo "✅ validate OK"

scan-secrets:  ## Escaneo Gitleaks
	bash scripts/gitleaks-scan.sh

audit-deps:  ## Auditoría pip-audit
	bash scripts/pip-audit-scan.sh

scan-osv:  ## Escaneo OSV-Scanner (requirements.txt)
	bash scripts/osv-scan.sh

verify-github-config:  ## Verifica qué variables y secrets de GitHub faltan configurar
	./scripts/verify_github_config.sh

# Aliases de compatibilidad (preferir los targets canónicos de arriba)
dev: run-dev  ## Alias de run-dev

docker-build: build  ## Alias de build

docker-up: run  ## Alias de run

docker-down: stop  ## Alias de stop

docker-logs: logs  ## Alias de logs
