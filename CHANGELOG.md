# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
