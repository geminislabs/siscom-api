#!/usr/bin/env bash

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python -m pip install --upgrade pip pip-audit >/dev/null
pip install -r requirements.txt >/dev/null

# Temporary risk acceptances with automatic expiry.
# Format: "VULN_ID|YYYY-MM-DD|reason"
# After expiry date, the vulnerability is no longer ignored and CI will re-check it.
# Vacía desde que se quitó python-jose (y con él ecdsa, PYSEC-2026-1325).
# Las expansiones `${arr[@]+"${arr[@]}"}` de abajo son a propósito: con `set -u`,
# un array vacío da «unbound variable» en el bash 3.2 de macOS (no en el de CI).
RISK_ACCEPTANCES=()

TODAY_UTC="$(date -u +%F)"

PIP_AUDIT_IGNORE_ARGS=()
for entry in ${RISK_ACCEPTANCES[@]+"${RISK_ACCEPTANCES[@]}"}; do
	IFS='|' read -r vuln_id ignore_until reason <<< "$entry"

	# Ignore only while the acceptance window is active.
	if [[ "${FORCE_FULL_AUDIT:-0}" != "1" && "$TODAY_UTC" < "$ignore_until" ]]; then
		PIP_AUDIT_IGNORE_ARGS+=("--ignore-vuln" "$vuln_id")
		echo "[pip-audit] Ignoring $vuln_id until $ignore_until ($reason)"
	fi
done

exec python -m pip_audit -r requirements.txt --desc on ${PIP_AUDIT_IGNORE_ARGS[@]+"${PIP_AUDIT_IGNORE_ARGS[@]}"}
