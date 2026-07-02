# Governance — siscom-api

## Branch protection (recommended for `develop`)

Configure in **GitHub → Settings → Branches → Branch protection rules**:

| Setting                     | Value                 |
| --------------------------- | --------------------- |
| Branch                      | `develop`             |
| Require pull request        | Yes                   |
| Required approvals          | 1                     |
| Require status checks       | `quality`, `security` |
| Require branches up to date | Yes                   |
| Include administrators      | Optional (team decision) |

## CODEOWNERS

See [.github/CODEOWNERS](../.github/CODEOWNERS). Replace `@geminislabs/engineering` with your real GitHub team slug before enforcing reviews.

## CI jobs

| Job        | Blocks merge                                      |
| ---------- | ------------------------------------------------- |
| `quality`  | Yes — Ruff, Black, pytest + coverage floor, build |
| `security` | Yes — Gitleaks, Semgrep, pip-audit, OSV-Scanner (`requirements.txt` + `osv-scanner.toml`) |

## Coverage policy

pytest-cov enforces a **minimum 65% line coverage** on `app/` (configured in `pyproject.toml` and `pytest.ini`). The floor prevents regressions without requiring a large test expansion in this PR.

Raise the floor only after adding tests in follow-up PRs.

## Release

Follow [docs/RELEASE.md](RELEASE.md). Deploy only via annotated tags `v*.*.*`.

## Dependabot

Configured in [.github/dependabot.yml](../.github/dependabot.yml).

| Ecosystem      | Schedule | Target branch | Notes                                       |
| -------------- | -------- | ------------- | ------------------------------------------- |
| pip            | Weekly   | `develop`     | Grouped patch/minor; majors as separate PRs |
| github-actions | Weekly   | `develop`     | Single grouped PR per week when possible    |
| docker         | Weekly   | `develop`     | Base images in `Dockerfile`                 |

**Merge policy for Dependabot PRs:**

1. Wait for CI checks `quality` and `security` to pass.
2. Patch/minor grouped PRs: review changelog, merge if green.
3. Major version PRs: run `make validate` locally before merge.
4. Do not auto-merge majors without human approval.

Enable **Dependabot security updates** in GitHub → Settings → Code security and analysis (complements OSV-Scanner and `pip-audit`).
