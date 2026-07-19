#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python -m pip install --upgrade pip pip-audit >/dev/null
pip install -r requirements.txt >/dev/null

exec python -m pip_audit -r requirements.txt --desc on
