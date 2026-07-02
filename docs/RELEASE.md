# Release Guide — siscom-api

## Version source of truth

- **Git tags:** annotated tags `v*.*.*` (e.g. `v1.0.0`)
- **Changelog:** `CHANGELOG.md` — move `[Unreleased]` entries under the new version header before tagging

## Prerequisites

- All changes merged to `develop` via PR with **CI green** (`quality`, `security`)
- `CHANGELOG.md` updated
- GitHub Actions secrets/vars configured for deploy (EC2 SSH, DB, Kafka, etc.)

## Release sequence

1. Sync `develop`:

   ```bash
   git checkout develop
   git pull origin develop
   ```

2. Prepare changelog (and version notes if applicable):

   ```bash
   git add CHANGELOG.md
   git commit -m "chore(release): prepare vX.Y.Z"
   git push origin develop
   ```

3. Create and push an annotated tag:

   ```bash
   git tag -a vX.Y.Z -m "release: vX.Y.Z"
   git push origin vX.Y.Z
   ```

4. **Deploy workflow** (`.github/workflows/deploy.yml`) runs on tag push.

## Rollback

Re-deploy a previous known-good tag:

```bash
git push origin vX.Y.Z-previous
```

Or manually on EC2: load previous image and `docker-compose -f docker-compose.prod.yml up -d`.
