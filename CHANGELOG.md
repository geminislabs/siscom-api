# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `GET /health` anuncia la versión liberada. Era la constante `APP_VERSION = "0.1.0"`, que nadie
  actualizaba: con `v1.3.0` desplegada seguía diciendo 0.1.0. Ahora sale del fichero `VERSION` de la
  raíz, que escribe el commit de release (`docs/RELEASE.md`), igual que en `siscom-admin-api`; el
  `Dockerfile` lo copia a la imagen. Sin el fichero, `"unknown"`. Se quita `APP_VERSION` de
  `.env.example`: una variable de entorno se impondría al fichero

## [1.3.0] - 2026-10-06

### Fixed

- CI: `greenlet` explícito en `requirements.txt`. El modo asyncio de SQLAlchemy lo exige y dejó de
  llegar como transitiva: desde el 28/09/2026 `test/conftest.py` no cargaba y el CI no corría ni un
  test
- `scripts/pip-audit-scan.sh` funciona con la lista de excepciones vacía también en el bash 3.2 de
  macOS (con `set -u`, un array vacío daba «unbound variable»)

### Removed

- **JWT, que nunca se usó.** `app/core/security.py` (`create_access_token`, `verify_token`,
  `get_current_user`) no estaba conectado a ninguna ruta; la autorización real es el data token
  PASETO v4.public (`require_data_token`) y el PASETO de compartir ubicación. Se quitan el módulo,
  `JWT_SECRET_KEY`/`JWT_ALGORITHM`/`ACCESS_TOKEN_EXPIRE_MINUTES` (config, `deploy.yml`, CI,
  `docker-compose.yml`, `.env.example`, scripts y guías) y sus tests. `JWT_SECRET_KEY` tenía `""`
  por defecto y `docs/API_REST_GUIDE.md` recomendaba conectarlo a las rutas: se elimina antes de que
  alguien lo hiciera
- `python-jose`, `types-python-jose`, `passlib` y `types-passlib` de `requirements.txt` (sin uso).
  Con `python-jose` salen también `ecdsa`, `rsa` y `pyasn1`: se cierran CVE-2026-85394
  (`python-jose`) y PYSEC-2026-1325 (`ecdsa`, cuya excepción había caducado el 01/10/2026 y volvía a
  romper `security`)

## [≤ 1.2.0] - sin cortar

Lo que sigue salió en `v1.1.0` (21/07/2026) y `v1.2.0` (03/09/2026), pero el changelog nunca se
cortó por versión y no se puede atribuir cada entrada a su tag sin reconstruirlo del historial.

### Added

- Engineering foundation (PR-1): blocking CI (`quality` + `security` jobs)
- Soft foundations (PR-2): SQLite in-memory test fixtures (`db_session_sqlite`, `client_sqlite`)
- Quality gates (PR-3): `CODEOWNERS`, `dependabot.yml`, `docs/GOVERNANCE.md`, OSV-Scanner, `osv-scanner.toml`
- Coverage floor (65% on `app/`) via `pyproject.toml` and `pytest.ini`
- `scripts/gitleaks-scan.sh`, `scripts/pip-audit-scan.sh`, `scripts/osv-scan.sh`, `scripts/setup.sh`
- `.pre-commit-config.yaml` (Ruff, Black, hygiene hooks)
- `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/RELEASE.md`
- `.editorconfig`, `.python-version`, `.gitleaks.toml`
- GitHub pull request template and issue templates
- `make validate`, `make run-dev`, `make scan-secrets`, `make audit-deps`, `make scan-osv`

### Changed

- CI: Ruff, Black, pytest (PostgreSQL service), and Docker build are blocking
- Test harness: per-request DB sessions, external service mocks, `-m "not slow"` in CI
- Minimum Python version **3.12** (CI, Docker, tooling)

### Fixed

- Async/event-loop test failures with TestClient + SQLAlchemy
- Gitleaks, pip-audit, and OSV-Scanner scripts for CI
