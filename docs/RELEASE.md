# Release Guide — siscom-api

## Version source of truth

- **Git tags:** annotated tags `v*.*.*` (e.g. `v1.0.0`)
- **Changelog:** `CHANGELOG.md` — move `[Unreleased]` entries under the new version header before tagging
- **`VERSION`:** one line at the repository root, bumped in the same commit as the changelog cut. It is what `GET /health` reports (`app/core/config.py`, `_version_del_repo`). Until v1.3.0 the version was a hardcoded `"0.1.0"` that nobody bumped, so `/health` could not tell which release was running

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
   echo "X.Y.Z" > VERSION
   git add CHANGELOG.md VERSION
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
