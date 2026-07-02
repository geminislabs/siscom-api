#!/usr/bin/env bash

set -euo pipefail

VERSION="v2.3.8"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BIN_DIR="$ROOT/.cache/osv-scanner"
BIN="$BIN_DIR/osv-scanner"

install_osv_scanner() {
	local os arch platform asset

	os="$(uname -s | tr '[:upper:]' '[:lower:]')"
	arch="$(uname -m)"

	case "$arch" in
	x86_64) arch="amd64" ;;
	aarch64 | arm64) arch="arm64" ;;
	*)
		echo "osv-scanner: unsupported architecture: $(uname -m)" >&2
		exit 1
		;;
	esac

	case "$os" in
	darwin) platform="darwin" ;;
	linux) platform="linux" ;;
	*)
		echo "osv-scanner: unsupported OS: $(uname -s)" >&2
		exit 1
		;;
	esac

	mkdir -p "$BIN_DIR"
	asset="osv-scanner_${platform}_${arch}"
	curl -sSfL "https://github.com/google/osv-scanner/releases/download/${VERSION}/${asset}" -o "$BIN"
	chmod +x "$BIN"
}

if [[ ! -x "$BIN" ]]; then
	echo "Installing osv-scanner ${VERSION} into .cache/osv-scanner/ ..."
	install_osv_scanner
fi

# Scan pinned deps only (not .venv / site-packages). Config: osv-scanner.toml
exec "$BIN" scan "$ROOT/requirements.txt"
