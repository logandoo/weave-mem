#!/usr/bin/env bash
# 停止 weave-mem。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${PID_FILE:-$DIR/weave-mem.pid}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "weave-mem 未运行（无 PID 文件）"
  exit 0
fi
PID="$(cat "$PID_FILE")"
if kill -0 "$PID" >/dev/null 2>&1; then
  echo "停止 weave-mem (PID: $PID)..."
  kill "$PID"
  sleep 1
  kill -0 "$PID" >/dev/null 2>&1 && kill -9 "$PID" || true
  echo "已停止"
else
  echo "weave-mem 未运行（PID 文件过期）"
fi
rm -f "$PID_FILE"
