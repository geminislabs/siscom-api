#!/usr/bin/env bash

set -euo pipefail

VERSION="8.24.2"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BIN_DIR="$ROOT/.cache/gitleaks"
BIN="$BIN_DIR/gitleaks"

install_gitleaks() {
	local os arch platform

	os="$(uname -s | tr '[:upper:]' '[:lower:]')"
	arch="$(uname -m)"

	case "$arch" in
	x86_64) arch="x64" ;;
	aarch64 | arm64) arch="arm64" ;;
	*)
		echo "gitleaks: unsupported architecture: $(uname -m)" >&2
		exit 1
		;;
	esac

	case "$os" in
	darwin) platform="darwin" ;;
	linux) platform="linux" ;;
	*)
		echo "gitleaks: unsupported OS: $(uname -s)" >&2
		exit 1
		;;
	esac

	mkdir -p "$BIN_DIR"
	curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/gitleaks_${VERSION}_${platform}_${arch}.tar.gz" \
		| tar -xz -C "$BIN_DIR" gitleaks
	chmod +x "$BIN"
}

if [[ ! -x "$BIN" ]]; then
	echo "Installing gitleaks v${VERSION} into .cache/gitleaks/ ..."
	install_gitleaks
fi

BASELINE_ARGS=()
if [[ -f "$ROOT/.gitleaks-baseline.json" ]]; then
	BASELINE_ARGS=(--baseline-path .gitleaks-baseline.json)
fi

exec "$BIN" detect --source . --config .gitleaks.toml --no-git "${BASELINE_ARGS[@]}" --redact --verbose "$@"
