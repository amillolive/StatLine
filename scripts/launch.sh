#!/usr/bin/env bash
set -euo pipefail
statline_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
statline_python="${STATLINE_PYTHON:-}"
if [[ -z "$statline_python" ]]; then
  if [[ -x "$statline_root/.venv/bin/python" ]]; then
    statline_python="$statline_root/.venv/bin/python"
  else
    statline_python=python3
  fi
fi
exec "$statline_python" "$statline_root/scripts/serve.py" --python "$statline_python" "$@"
