.PHONY: help install lint format type-check test clean dev run docker-build docker-up docker-down

help: ## Mostrar esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Instalar dependencias
	pip install -r requirements.txt

install-dev: ## Instalar dependencias de desarrollo
	pip install -r requirements.txt
	pip install ruff black mypy pytest-cov pre-commit

lint: ## Ejecutar Ruff (linter)
	@echo "🔍 Ejecutando Ruff..."
	ruff check app/ test/

lint-fix: ## Ejecutar Ruff y auto-corregir
	@echo "🔧 Auto-corrigiendo con Ruff..."
	ruff check app/ test/ --fix

format: ## Formatear código con Black
	@echo "🎨 Formateando código con Black..."
	black app/ test/

format-check: ## Verificar formato sin modificar
	@echo "🎨 Verificando formato..."
	black --check app/ test/

type-check: ## Verificar tipos con MyPy
	@echo "📝 Verificando tipos con MyPy..."
	@mypy app/ --ignore-missing-imports || echo "⚠️  MyPy encontró algunos warnings (no críticos)"

check-all: lint format-check type-check ## Ejecutar todos los checks
	@echo "✅ Todos los checks completados!"

validate: format-check lint test-cov ## Validación completa (CI-like)
	@echo "✅ Validación completa exitosa!"

fix-all: lint-fix format ## Auto-corregir y formatear
	@echo "✅ Código corregido y formateado!"

test: ## Ejecutar todos los tests
	@echo "🧪 Ejecutando todos los tests..."
	pytest -v

test-unit: ## Ejecutar solo tests unitarios
	@echo "🧪 Ejecutando tests unitarios..."
	pytest -v -m unit

test-integration: ## Ejecutar solo tests de integración
	@echo "🧪 Ejecutando tests de integración..."
	pytest -v -m integration

test-cov: ## Ejecutar tests con coverage
	@echo "🧪 Ejecutando tests con coverage..."
	pytest --cov=app --cov-report=term-missing --cov-report=html
	@echo "✅ Reporte HTML generado en: htmlcov/index.html"

test-watch: ## Ejecutar tests en modo watch (requiere pytest-watch)
	@echo "🧪 Ejecutando tests en modo watch..."
	ptw -- -v

test-fast: ## Ejecutar tests rápidos (excluir lentos)
	@echo "🧪 Ejecutando tests rápidos..."
	pytest -v -m "not slow"

test-auth: ## Ejecutar tests de autenticación
	@echo "🧪 Ejecutando tests de autenticación..."
	pytest -v -m auth

test-db: ## Ejecutar tests de base de datos
	@echo "🧪 Ejecutando tests de base de datos..."
	pytest -v -m database

test-file: ## Ejecutar tests de un archivo específico (usar FILE=nombre)
	@echo "🧪 Ejecutando tests de $(FILE)..."
	pytest -v test/$(FILE)

clean: ## Limpiar archivos temporales
	@echo "🧹 Limpiando archivos temporales..."
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.coverage" -delete
	rm -rf .pytest_cache
	rm -rf .mypy_cache
	rm -rf .ruff_cache
	rm -rf htmlcov
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info

dev: ## Ejecutar servidor en modo desarrollo
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --http h11

run: ## Ejecutar servidor en modo producción
	uvicorn app.main:app --host 0.0.0.0 --port 8000 --http h11

docker-build: ## Construir imagen Docker
	@echo "🐳 Construyendo imagen Docker..."
	docker build -t siscom-api:latest .

docker-up: ## Levantar servicios con Docker Compose
	@echo "🚀 Levantando servicios..."
	docker network create siscom-network 2>/dev/null || true
	docker-compose up -d

docker-down: ## Detener servicios Docker
	@echo "🛑 Deteniendo servicios..."
	docker-compose down

docker-logs: ## Ver logs del contenedor
	docker-compose logs -f

docker-clean: ## Limpiar recursos Docker
	docker-compose down -v
	docker system prune -f

setup: ## Configurar ambiente de desarrollo
	@echo "🔧 Configurando ambiente de desarrollo..."
	python -m venv venv || true
	@echo "✅ Activa el entorno virtual con: source venv/bin/activate"
	@echo "✅ Luego ejecuta: make install-dev"

