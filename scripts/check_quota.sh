#!/usr/bin/env bash
# Wrapper so `make quota` uses the project venv when there is one.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/scripts/check_quota.py"
