#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.tools:$PATH"
if [ ! -x .venv/bin/python ]; then echo 'First run: bash scripts/install-tools.sh'; exit 1; fi
exec .venv/bin/python -m tools.lab "$@"
