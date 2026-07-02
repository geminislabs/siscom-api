# GitHub Actions — siscom-api

Workflows de CI/CD para **siscom-api**.

## Workflows

| Archivo        | Trigger                    | Jobs                          |
| -------------- | -------------------------- | ----------------------------- |
| `quality.yml`  | PR/push a `develop`, `main` | `quality`, `security`         |
| `deploy.yml`   | Tag `v*.*.*`               | Build + deploy a EC2          |

## CI (`quality.yml`)

**Job `quality`:** Black, Ruff, pytest + cobertura (65%), Docker build.

**Job `security`:** Gitleaks, Semgrep, pip-audit, OSV-Scanner.

## Deploy (`deploy.yml`)

Se ejecuta al pushear un tag anotado `v*.*.*`. Requiere environment `test` y secrets configurados.

## Configuración

- **Secrets y variables:** ver [.github/GITHUB_SETUP.md](GITHUB_SETUP.md)
- **Checklist local:** `make verify-github-config`
- **Gobernanza:** [docs/GOVERNANCE.md](../docs/GOVERNANCE.md)

## Desarrollo local

```bash
bash scripts/setup.sh
cp .env.example .env
make run-dev      # http://localhost:8000
make validate     # lint + format + test + docker build
```
