#!/usr/bin/env bash
# weave-mem 后端-only 项目构建（无前端产物）：编译自检即构建。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3
"$PY" -m compileall -q "$DIR/backend/app"
echo "weave-mem build OK（backend compileall）"
