# AGENTS.md — Guía para agentes de código

Instrucciones para asistentes de IA (Cursor, Copilot, etc.) que trabajen en este repositorio.

## Proyecto

**siscom-api** — API FastAPI de comunicaciones y tracking Geminis Labs: telemetría GPS, eventos, streaming WebSocket/SSE, ubicación pública compartida (PASETO) e integración Kafka.

## Stack

| Capa        | Tecnología                          |
| ----------- | ----------------------------------- |
| Framework   | FastAPI + Uvicorn                   |
| ORM         | SQLAlchemy 2                        |
| DB          | PostgreSQL                          |
| Auth        | JWT + PASETO (public share)         |
| Messaging   | Kafka (aiokafka)                    |
| Lint/format | Ruff + Black                        |
| Tests       | pytest + pytest-cov                 |
| Runtime     | Python 3.12+                        |
| Deploy      | Docker → EC2 (tags `v*.*.*`)      |

## Estructura

```text
app/api/routes/         # REST, WebSocket, SSE, public share
app/services/           # repository, kafka, events
app/models/             # SQLAlchemy models
app/schemas/            # Pydantic DTOs
test/                   # pytest (PostgreSQL + SQLite fixtures)
docs/                   # guías y API docs
```

## Convenciones

- **Python 3.12+** (ver `.python-version`).
- **Formato:** Black (88 cols), Ruff para lint.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, etc.).
- **Alcance:** Cambios mínimos y enfocados. No reformatear código no relacionado.

## Comandos obligatorios antes de terminar

```bash
make validate
```

Equivalente: `make lint`, `make format-check`, `make test`, `docker build`.

Opcional: `make scan-secrets`, `make audit-deps`, `make scan-osv`, `pre-commit run --all-files`.

## Gobernanza

- Branch protection y política de CI: `docs/GOVERNANCE.md`
- CODEOWNERS y Dependabot: `.github/`
- Deploy secrets: `.github/GITHUB_SETUP.md`

## Módulos sensibles

- `app/core/security.py` — JWT
- `app/utils/paseto_validator.py` — tokens de ubicación pública
- `app/api/routes/public.py` — API pública compartida
- `app/services/kafka_client.py` — mensajería Kafka

## Deploy

- CI en PR/push a `develop`/`main` (`.github/workflows/quality.yml`)
- Deploy automático al pushear tag `v*.*.*` (`.github/workflows/deploy.yml`)
