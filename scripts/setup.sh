#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")/.."

echo "Installing dependencies..."
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pre-commit

if command -v pre-commit >/dev/null 2>&1; then
	echo "Installing pre-commit hooks..."
	pre-commit install
fi

echo "✓ Setup complete"
echo ""
echo "Optional security scans:"
echo "  make scan-secrets"
echo "  make audit-deps"
echo "  make scan-osv"
echo ""
echo "Next steps:"
echo "  cp .env.example .env"
echo "  make run-dev"
echo "  make validate"
