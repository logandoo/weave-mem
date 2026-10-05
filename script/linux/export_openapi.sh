#!/usr/bin/env bash
# 导出 OpenAPI 规范到 docs/openapi.json（FastAPI 自动生成的固化版本）。
# 用法: bash script/linux/export_openapi.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
mkdir -p docs
PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  for c in ./.venv/bin/python; do
    if [ -x "$c" ]; then PYTHON_BIN="$c"; break; fi
  done
fi
if [ -z "$PYTHON_BIN" ]; then echo "no python found"; exit 1; fi
"$PYTHON_BIN" - << 'PYEOF'
import json, sys
sys.path.insert(0, "backend")
from app.main import app
with open("docs/openapi.json", "w") as f:
    json.dump(app.openapi(), f, ensure_ascii=False, indent=2)
print("docs/openapi.json exported")
PYEOF
